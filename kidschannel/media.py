"""Utilitários de mídia: ffmpeg/ffprobe e fontes."""

from __future__ import annotations

import functools
import json
import logging
import shutil
import subprocess
from pathlib import Path

from PIL import ImageFont

log = logging.getLogger(__name__)


class MediaError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise MediaError(f"'{tool}' não encontrado. Instale o FFmpeg (ex.: sudo apt-get install ffmpeg).")


def run_ffmpeg(args: list[str], cwd: str | Path | None = None) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg falhou ({proc.returncode}):\n{proc.stderr[-3000:]}")


def probe_duration(path: str | Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffprobe falhou para {path}: {proc.stderr}")
    return float(json.loads(proc.stdout)["format"]["duration"])


@functools.lru_cache(maxsize=None)
def font_path(pattern: str) -> str | None:
    """Caminho do arquivo de fonte que o fontconfig escolhe para o padrão dado."""
    if not shutil.which("fc-match"):
        return None
    proc = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True)
    path = proc.stdout.strip()
    return path if proc.returncode == 0 and path and Path(path).exists() else None


def font_family_available(family: str) -> bool:
    """True se o fontconfig tem exatamente essa família instalada."""
    if not shutil.which("fc-list"):
        return False
    proc = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True)
    families = {f.strip().lower() for line in proc.stdout.splitlines() for f in line.split(",")}
    return family.lower() in families


def find_font(pattern: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = font_path(pattern) or font_path("DejaVu Sans:bold")
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)
