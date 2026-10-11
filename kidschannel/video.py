"""Montagem do Short vertical com FFmpeg.

Cada cena vira um clipe com movimento suave (efeito Ken Burns) e os clipes são
ligados com transições. No formato narrado, cada fala entra no tempo da sua cena e a
música de fundo abaixa sozinha quando há voz. No formato música, as imagens trocam
no início de cada linha cantada. As legendas karaokê são gravadas no vídeo.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .captions import TimedWord, write_ass
from .media import font_family_available, require_ffmpeg, run_ffmpeg

log = logging.getLogger(__name__)

TRANSITIONS = ["fade", "slideleft", "circleopen", "smoothleft", "slideup", "radial", "wiperight"]
MOTIONS = ["zoom_in", "zoom_out", "pan_right", "pan_left", "pan_up"]


@dataclass
class Timeline:
    starts: list[float] = field(default_factory=list)  # início de cada cena no vídeo
    clip_lengths: list[float] = field(default_factory=list)  # duração de cada clipe (com sobreposição)
    total: float = 0.0


def plan_timeline(durations: list[float], lead: float, tail: float, transition: float, end_hold: float = 0.6) -> Timeline:
    """Formato narrado: cada cena dura o tempo da sua fala mais um respiro."""
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


def song_timeline(line_starts: list[float], total: float, transition: float,
                  min_scene: float = 1.2, pre_roll: float = 0.12) -> tuple[Timeline, list[int]]:
    """Formato música: corta para a imagem de cada linha um instante antes de ela ser cantada.

    Linhas muito próximas são juntadas (a imagem anterior continua). Devolve a linha
    do tempo e o índice da linha usada em cada cena.
    """
    starts, kept = [0.0], [0]
    for i in range(1, len(line_starts)):
        t = max(0.0, line_starts[i] - pre_roll)
        if t - starts[-1] >= min_scene and total - t >= min_scene:
            starts.append(t)
            kept.append(i)
    tl = Timeline(starts=starts, total=total)
    for k, start in enumerate(starts):
        if k + 1 < len(starts):
            tl.clip_lengths.append(starts[k + 1] - start + transition)
        else:
            tl.clip_lengths.append(total - start)
    return tl, kept


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


def render(
    cfg: dict[str, Any],
    images: list[Path],
    timeline: Timeline,
    audio_tracks: list[tuple[Path, float]],
    words: list[TimedWord],
    out_path: Path,
    work_dir: Path,
    seed: int,
    music: Path | None = None,
    label: str | None = None,
    caption_words: int | None = None,
) -> Path:
    """Monta o vídeo. `audio_tracks` = [(arquivo, segundo em que começa)]."""
    require_ffmpeg()
    vcfg = cfg["video"]
    width, height, fps = vcfg["width"], vcfg["height"], vcfg.get("fps", 30)
    transition = float(vcfg.get("transition", 0.4))
    rng = random.Random(seed)
    n = len(images)
    if n != len(timeline.starts):
        raise ValueError("Número de imagens diferente do número de cenas.")
    log.info("Duração do Short: %.1fs em %d cenas", timeline.total, n)

    # ---------- legendas ----------
    font = vcfg.get("caption_font", "DejaVu Sans")
    if not font_family_available(font):
        fallback = vcfg.get("caption_font_fallback", "DejaVu Sans")
        log.warning("Fonte '%s' não instalada; usando '%s'.", font, fallback)
        font = fallback
    write_ass(
        work_dir / "captions.ass",
        words,
        width,
        height,
        font,
        int(vcfg.get("caption_size", 88)),
        int(caption_words or vcfg.get("caption_words", 3)),
        label if vcfg.get("show_series_label", True) else None,
        timeline.total,
    )

    # ---------- entradas ----------
    args: list[str] = []
    for image in images:
        args += ["-i", str(image.resolve())]
    for path, _ in audio_tracks:
        args += ["-i", str(path.resolve())]
    if music:
        args += ["-stream_loop", "-1", "-i", str(music.resolve())]

    # ---------- vídeo ----------
    graph: list[str] = []
    for i in range(n):
        frames = max(2, int(round(timeline.clip_lengths[i] * fps)))
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
            f"[{last}][v{i}]xfade=transition={kind}:duration={transition}:offset={timeline.starts[i]:.3f}[x{i}]"
        )
        last = f"x{i}"
    graph.append(f"[{last}]ass=captions.ass[vout]")

    # ---------- áudio ----------
    fmt = "aformat=sample_rates=44100:channel_layouts=stereo"
    total = f"{timeline.total:.3f}"
    for k, (_, start) in enumerate(audio_tracks):
        delay = int(round(start * 1000))
        graph.append(f"[{n + k}:a]{fmt},adelay=delays={delay}:all=1[a{k}]")
    graph.append(
        "".join(f"[a{k}]" for k in range(len(audio_tracks)))
        + f"amix=inputs={len(audio_tracks)}:normalize=0:dropout_transition=0,apad,atrim=0:{total}[voice]"
    )
    if music:
        volume = float(cfg.get("music", {}).get("volume", 0.14))
        fade = min(1.5, timeline.total / 4)
        graph.append(
            f"[{n + len(audio_tracks)}:a]{fmt},atrim=0:{total},volume={volume},"
            f"afade=t=in:d=0.4,afade=t=out:st={timeline.total - fade:.3f}:d={fade:.3f}[mus]"
        )
        graph.append("[voice]asplit=2[voice1][voice2]")
        graph.append("[mus][voice1]sidechaincompress=threshold=0.03:ratio=5:attack=15:release=350[duck]")
        graph.append("[voice2][duck]amix=inputs=2:normalize=0:duration=first[mix]")
    else:
        graph.append("[voice]anull[mix]")
    graph.append("[mix]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100[aout]")

    out_path = out_path.resolve()
    args += [
        "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(vcfg.get("crf", 20)),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        "-t", total,
        str(out_path),
    ]
    run_ffmpeg(args, cwd=work_dir)
    return out_path
