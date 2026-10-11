"""Orquestra tudo: roteiro/letra -> imagens -> voz ou música cantada -> vídeo -> YouTube."""

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

from .captions import TimedWord
from .config import OUTPUT_DIR, series_by_id
from .images import ImageGenerator
from .media import probe_duration
from .music import background_music
from .script_writer import NARRATION, SONG, Script, ScriptWriter, offline_script
from .song import make_song, song_available
from .state import ChannelState
from .tts import audio_file, make_tts
from .video import plan_timeline, render, song_timeline

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


def available_series(cfg: dict[str, Any], offline: bool = False) -> list[dict[str, Any]]:
    """Séries que dá para produzir agora (as de música precisam da chave da ElevenLabs)."""
    if offline or song_available(cfg):
        return cfg["series"]
    usable = [s for s in cfg["series"] if s.get("format", NARRATION) != SONG]
    if len(usable) < len(cfg["series"]):
        log.warning("Séries de música puladas: configure ELEVENLABS_API_KEY para os vídeos cantados.")
    return usable or cfg["series"]


def make_short(cfg: dict[str, Any], state: ChannelState, series: dict[str, Any] | None = None,
               topic: str | None = None, offline: bool = False, out_root: Path = OUTPUT_DIR,
               tts_provider: str | None = None) -> ShortResult:
    series = series or state.pick_series(available_series(cfg, offline))
    seed = random.randrange(1, 2**31)

    # 1) roteiro ou letra
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

    # 3) áudio + linha do tempo
    if script.format == SONG:
        video = _render_song(cfg, script, series, images, folder, seed, offline)
    else:
        provider = tts_provider or ("edge_or_silent" if offline else None)
        video = _render_narration(cfg, script, series, images, folder, seed, provider)
    log.info("Short pronto: %s", video)
    return ShortResult(folder=folder, video=video, script=script, series=series)


def _render_narration(cfg, script: Script, series, images, folder: Path, seed: int, tts_provider) -> Path:
    tts = make_tts(cfg, tts_provider)
    audio_dir = folder / "audio"
    audio_dir.mkdir(exist_ok=True)
    audios, scene_words = [], []
    for i, scene in enumerate(script.scenes):
        base = audio_dir / f"scene_{i:02d}"
        scene_words.append(tts.synthesize(scene.narration, base))
        audios.append(audio_file(base))

    vcfg = cfg["video"]
    lead = float(vcfg.get("lead", 0.25))
    timeline = plan_timeline(
        [probe_duration(a) for a in audios], lead, float(vcfg.get("tail", 0.45)), float(vcfg.get("transition", 0.4))
    )
    words = [
        TimedWord(w.text, start + lead + w.start, start + lead + w.end)
        for start, ws in zip(timeline.starts, scene_words)
        for w in ws
    ]
    tracks = [(a, start + lead) for a, start in zip(audios, timeline.starts)]
    music = background_music(cfg, timeline.total + 1.0, folder, seed)
    return render(cfg, images, timeline, tracks, words, folder / "short.mp4", folder, seed,
                  music=music, label=series.get("label"))


def _render_song(cfg, script: Script, series, images, folder: Path, seed: int, offline: bool) -> Path:
    song = make_song(cfg, script, folder, seed, offline=offline)
    timeline, kept = song_timeline(song.line_starts, song.duration, float(cfg["video"].get("transition", 0.4)))
    words = [
        TimedWord(w.text, w.start, w.end, line_break=(k == len(line) - 1))
        for line in song.lines
        for k, w in enumerate(line)
    ]
    return render(cfg, [images[i] for i in kept], timeline, [(song.path, 0.0)], words,
                  folder / "short.mp4", folder, seed, music=None, label=series.get("label"),
                  caption_words=int(cfg.get("song", {}).get("caption_words", 4)))


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
