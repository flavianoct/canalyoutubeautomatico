"""Orquestra tudo: roteiro -> imagens -> narração -> música -> vídeo -> YouTube."""

from __future__ import annotations

import json
import logging
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import OUTPUT_DIR, series_by_id
from .images import ImageGenerator
from .media import probe_duration
from .music import background_music
from .script_writer import Script, ScriptWriter, offline_script
from .state import ChannelState
from .tts import audio_file, make_tts
from .video import SceneMedia, plan_timeline, render_short

log = logging.getLogger(__name__)


@dataclass
class ShortResult:
    folder: Path
    video: Path
    script: Script
    series: dict[str, Any]


def slugify(text: str, limit: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "short"


def next_slots(times: list[str], tz_name: str, count: int, now: datetime | None = None,
               min_lead: timedelta = timedelta(minutes=45)) -> list[datetime]:
    """Próximos `count` horários de publicação (em UTC) a partir de agora."""
    tz = ZoneInfo(tz_name)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    parsed = sorted(tuple(int(x) for x in t.split(":")) for t in times)
    slots: list[datetime] = []
    day = now.date()
    while len(slots) < count:
        for hour, minute in parsed:
            candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
            if candidate - now >= min_lead:
                slots.append(candidate.astimezone(timezone.utc))
                if len(slots) == count:
                    break
        day += timedelta(days=1)
    return slots


def make_short(cfg: dict[str, Any], state: ChannelState, series: dict[str, Any] | None = None,
               topic: str | None = None, offline: bool = False, out_root: Path = OUTPUT_DIR) -> ShortResult:
    series = series or state.pick_series(cfg["series"])
    seed = random.randrange(1, 2**31)

    # 1) roteiro
    if offline:
        script = offline_script(cfg, series, topic)
    else:
        script = ScriptWriter(cfg).write(series, state.recent_topics(series["id"]), topic)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = out_root / f"{stamp}-{series['id']}-{slugify(script.topic)}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "script.json").write_text(json.dumps(script.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Pasta do vídeo: %s", folder)

    # 2) ilustrações
    images = ImageGenerator(cfg, ["local"] if offline else None).generate_scenes(
        script.scenes, folder / "images", seed
    )

    # 3) narração
    tts = make_tts(cfg, "silent" if offline else None)
    audio_dir = folder / "audio"
    audio_dir.mkdir(exist_ok=True)
    scenes: list[SceneMedia] = []
    for i, (scene, image) in enumerate(zip(script.scenes, images)):
        base = audio_dir / f"scene_{i:02d}"
        words = tts.synthesize(scene.narration, base)
        scenes.append(SceneMedia(image=image, audio=audio_file(base), words=words))

    # 4) música do tamanho exato do vídeo
    vcfg = cfg["video"]
    timeline = plan_timeline(
        [probe_duration(s.audio) for s in scenes],
        float(vcfg.get("lead", 0.25)), float(vcfg.get("tail", 0.45)), float(vcfg.get("transition", 0.4)),
    )
    music = background_music(cfg, timeline.total + 1.0, folder, seed)

    # 5) vídeo final
    video = render_short(cfg, scenes, music, folder / "short.mp4", folder, series.get("label"), seed)
    log.info("Short pronto: %s", video)
    return ShortResult(folder=folder, video=video, script=script, series=series)


def load_result(cfg: dict[str, Any], folder: Path) -> ShortResult:
    script = Script.from_dict(json.loads((folder / "script.json").read_text(encoding="utf-8")))
    return ShortResult(folder=folder, video=folder / "short.mp4", script=script,
                       series=series_by_id(cfg, script.series))


def upload_short(cfg: dict[str, Any], state: ChannelState, result: ShortResult, client,
                 publish_at: datetime | None = None) -> str:
    from .youtube import YouTubeError, build_video_body

    body = build_video_body(cfg, result.script, result.series, publish_at)
    video_id = client.upload(result.video, body)
    url = f"https://youtube.com/shorts/{video_id}"
    log.info("Publicado: %s", url)

    if cfg["publishing"].get("playlists_per_series", True):
        try:
            playlist_id = state.playlist_for(result.series["id"])
            if not playlist_id:
                playlist_id = client.create_playlist(
                    f"{result.series['name']} | {cfg['channel']['name']}",
                    result.series.get("brief", ""),
                    cfg["channel"].get("language", "pt-BR"),
                )
                state.set_playlist(result.series["id"], playlist_id)
            client.add_to_playlist(playlist_id, video_id)
        except YouTubeError as exc:  # playlist é opcional; não perde o upload por isso
            log.warning("Não foi possível adicionar à playlist: %s", exc)

    state.add_video({
        "series": result.series["id"],
        "topic": result.script.topic,
        "title": body["snippet"]["title"],
        "video_id": video_id,
        "url": url,
        "publish_at": publish_at.isoformat() if publish_at else None,
    })
    state.save()
    (result.folder / "upload.json").write_text(
        json.dumps({"video_id": video_id, "url": url, "body": body}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return video_id


def run(cfg: dict[str, Any], count: int | None = None, dry_run: bool = False, offline: bool = False,
        series_id: str | None = None, topic: str | None = None) -> list[ShortResult]:
    """Gera e publica `count` Shorts. Com horários agendados, o padrão é um por horário."""
    state = ChannelState()
    pub = cfg["publishing"]
    times = pub.get("schedule_times") or []
    slots: list[datetime | None]
    if times and not dry_run:
        slots = list(next_slots(times, pub.get("timezone", "America/Sao_Paulo"), count or len(times)))
    else:
        slots = [None] * max(1, count or 1)

    client = None
    if not dry_run:
        from .youtube import YouTubeClient

        client = YouTubeClient()  # falha cedo se as credenciais estiverem erradas

    results, failures = [], []
    for slot in slots:
        try:
            series = series_by_id(cfg, series_id) if series_id else None
            result = make_short(cfg, state, series=series, topic=topic, offline=offline)
            if client:
                upload_short(cfg, state, result, client, slot)
            results.append(result)
        except Exception as exc:  # segue para o próximo vídeo e reporta no fim
            log.exception("Falha ao produzir/publicar um Short: %s", exc)
            failures.append(exc)
    if failures:
        raise RuntimeError(f"{len(failures)} de {len(slots)} Shorts falharam; veja o log acima.") from failures[0]
    return results
