"""Montagem do Short vertical com FFmpeg.

Cada cena vira um clipe com movimento suave (efeito Ken Burns); os clipes são
ligados com transições, a narração entra no tempo certo de cada cena, a música
fica baixinha e abaixa sozinha quando há fala, e as legendas são gravadas no vídeo.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .captions import TimedWord, write_ass
from .media import font_family_available, probe_duration, require_ffmpeg, run_ffmpeg
from .tts import Word

log = logging.getLogger(__name__)

TRANSITIONS = ["fade", "slideleft", "circleopen", "smoothleft", "slideup", "radial", "wiperight"]
MOTIONS = ["zoom_in", "zoom_out", "pan_right", "pan_left", "pan_up"]


@dataclass
class SceneMedia:
    image: Path
    audio: Path
    words: list[Word]
    audio_duration: float = 0.0


@dataclass
class Timeline:
    starts: list[float] = field(default_factory=list)  # início de cada cena no vídeo
    clip_lengths: list[float] = field(default_factory=list)  # duração de cada clipe (com sobreposição)
    total: float = 0.0


def plan_timeline(durations: list[float], lead: float, tail: float, transition: float, end_hold: float = 0.6) -> Timeline:
    tl = Timeline()
    t = 0.0
    n = len(durations)
    for i, dur in enumerate(durations):
        visible = lead + dur + tail + (end_hold if i == n - 1 else 0.0)
        tl.starts.append(t)
        tl.clip_lengths.append(visible + (transition if i < n - 1 else 0.0))
        t += visible
    tl.total = t
    return tl


def _motion_expr(motion: str, frames: int, zoom: float = 0.14) -> tuple[str, str, str]:
    n = max(1, frames - 1)
    p = f"(on/{n})"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_in":
        return f"1+{zoom}*{p}", cx, cy
    if motion == "zoom_out":
        return f"{1 + zoom}-{zoom}*{p}", cx, cy
    if motion == "pan_right":
        return f"{1 + zoom * 0.8}", f"(iw-iw/zoom)*{p}", cy
    if motion == "pan_left":
        return f"{1 + zoom * 0.8}", f"(iw-iw/zoom)*(1-{p})", cy
    if motion == "pan_up":
        return f"{1 + zoom * 0.8}", cx, f"(ih-ih/zoom)*(1-{p})"
    raise ValueError(motion)


def render_short(
    cfg: dict[str, Any],
    scenes: list[SceneMedia],
    music: Path | None,
    out_path: Path,
    work_dir: Path,
    label: str | None,
    seed: int,
) -> Path:
    require_ffmpeg()
    vcfg = cfg["video"]
    width, height, fps = vcfg["width"], vcfg["height"], vcfg.get("fps", 30)
    transition = float(vcfg.get("transition", 0.4))
    lead, tail = float(vcfg.get("lead", 0.25)), float(vcfg.get("tail", 0.45))
    rng = random.Random(seed)

    for scene in scenes:
        scene.audio_duration = probe_duration(scene.audio)
    tl = plan_timeline([s.audio_duration for s in scenes], lead, tail, transition)
    log.info("Duração do Short: %.1fs em %d cenas", tl.total, len(scenes))

    # ---------- legendas ----------
    timed: list[TimedWord] = []
    for scene, start in zip(scenes, tl.starts):
        offset = start + lead
        timed += [TimedWord(w.text, offset + w.start, offset + w.end) for w in scene.words]
    font = vcfg.get("caption_font", "DejaVu Sans")
    if not font_family_available(font):
        fallback = vcfg.get("caption_font_fallback", "DejaVu Sans")
        log.warning("Fonte '%s' não instalada; usando '%s'.", font, fallback)
        font = fallback
    write_ass(
        work_dir / "captions.ass",
        timed,
        width,
        height,
        font,
        int(vcfg.get("caption_size", 88)),
        int(vcfg.get("caption_words", 3)),
        label if vcfg.get("show_series_label", True) else None,
        tl.total,
    )

    # ---------- entradas ----------
    args: list[str] = []
    for scene in scenes:
        args += ["-i", str(scene.image.resolve())]
    for scene in scenes:
        args += ["-i", str(scene.audio.resolve())]
    if music:
        args += ["-stream_loop", "-1", "-i", str(music.resolve())]

    # ---------- vídeo ----------
    n = len(scenes)
    graph: list[str] = []
    for i in range(n):
        frames = max(2, int(round(tl.clip_lengths[i] * fps)))
        z, x, y = _motion_expr(rng.choice(MOTIONS), frames)
        graph.append(
            f"[{i}:v]scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},setsar=1,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},"
            f"format=yuv420p,setsar=1[v{i}]"
        )
    last = "v0"
    for i in range(1, n):
        kind = rng.choice(TRANSITIONS)
        graph.append(
            f"[{last}][v{i}]xfade=transition={kind}:duration={transition}:offset={tl.starts[i]:.3f}[x{i}]"
        )
        last = f"x{i}"
    graph.append(f"[{last}]ass=captions.ass[vout]")

    # ---------- áudio ----------
    fmt = "aformat=sample_rates=44100:channel_layouts=stereo"
    for i in range(n):
        delay = int(round((tl.starts[i] + lead) * 1000))
        graph.append(f"[{n + i}:a]{fmt},adelay=delays={delay}:all=1[a{i}]")
    graph.append(
        "".join(f"[a{i}]" for i in range(n))
        + f"amix=inputs={n}:normalize=0:dropout_transition=0,apad,atrim=0:{tl.total:.3f}[narr]"
    )
    if music:
        volume = float(cfg.get("music", {}).get("volume", 0.14))
        fade = min(1.5, tl.total / 4)
        graph.append(
            f"[{2 * n}:a]{fmt},atrim=0:{tl.total:.3f},volume={volume},"
            f"afade=t=in:d=0.4,afade=t=out:st={tl.total - fade:.3f}:d={fade:.3f}[mus]"
        )
        graph.append("[narr]asplit=2[narr1][narr2]")
        graph.append("[mus][narr1]sidechaincompress=threshold=0.03:ratio=5:attack=15:release=350[duck]")
        graph.append("[narr2][duck]amix=inputs=2:normalize=0:duration=first[mix]")
    else:
        graph.append("[narr]anull[mix]")
    graph.append("[mix]loudnorm=I=-15:TP=-1.5:LRA=11,aresample=44100[aout]")

    out_path = out_path.resolve()
    args += [
        "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(vcfg.get("crf", 20)),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        "-t", f"{tl.total:.3f}",
        str(out_path),
    ]
    run_ffmpeg(args, cwd=work_dir)
    return out_path
