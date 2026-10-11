"""Kit de identidade do canal: mascote de referência, foto de perfil e banner."""

from __future__ import annotations

import logging
import random
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .config import ASSETS_DIR, OUTPUT_DIR
from .images import MASCOT_REFERENCE, ImageGenerator
from .media import find_font

log = logging.getLogger(__name__)

BANNER_SIZE = (2560, 1440)
SAFE_AREA = (1546, 423)  # área visível em todos os dispositivos


def _mascot_prompt(cfg: dict[str, Any]) -> str:
    style = cfg.get("style", {})
    return (
        f"{style.get('image', '')}. Character design sheet: {cfg['channel']['mascot']['visual']}, "
        "full body, standing, front view, happy smile, waving, centered, plain soft pastel background. "
        f"{style.get('avoid', '')}"
    )


def _avatar_prompt(cfg: dict[str, Any]) -> str:
    style = cfg.get("style", {})
    return (
        f"{style.get('image', '')}. Close-up portrait of {cfg['channel']['mascot']['visual']}, "
        "big friendly smile, head and shoulders centered in the middle of the frame, "
        f"bright solid sky blue background. {style.get('avoid', '')}"
    )


def make_banner(cfg: dict[str, Any], avatar: Image.Image, out_path: Path) -> Path:
    w, h = BANNER_SIZE
    banner = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(banner)
    top, bottom = (120, 200, 255), (255, 214, 120)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    # confetes suaves ao fundo
    rng = random.Random(7)
    for _ in range(90):
        r = rng.randint(10, 40)
        x, y = rng.randint(0, w), rng.randint(0, h)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=tuple(rng.randint(170, 255) for _ in range(3)))
    banner = banner.filter(ImageFilter.GaussianBlur(2))
    draw = ImageDraw.Draw(banner)

    safe_w, safe_h = SAFE_AREA
    sx, sy = (w - safe_w) // 2, (h - safe_h) // 2
    size = safe_h - 20
    circle = ImageOps.fit(avatar.convert("RGB"), (size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
    draw.ellipse([sx - 8, sy + 2, sx + size + 8, sy + size + 18], fill="white")
    banner.paste(circle, (sx, sy + 10), mask)

    name = cfg["channel"]["name"]
    text_x = sx + size + 50
    max_w = sx + safe_w - text_x
    font_size = 150
    font = find_font("Comic Neue:bold", font_size)
    while draw.textlength(name, font=font) > max_w and font_size > 50:
        font_size -= 6
        font = find_font("Comic Neue:bold", font_size)
    draw.text((text_x, h // 2 - 40), name, font=font, fill="white", anchor="lm",
              stroke_width=10, stroke_fill=(40, 70, 160))
    sub = find_font("Comic Neue:bold", 56)
    tagline = cfg["channel"].get("tagline", "")
    if tagline:
        draw.text((text_x, h // 2 + 85), tagline, font=sub, fill=(40, 70, 160), anchor="lm")
    banner.save(out_path, quality=92)
    return out_path


def build_kit(cfg: dict[str, Any], offline: bool = False, refresh_mascot: bool = False) -> Path:
    out = OUTPUT_DIR / "channel_kit"
    out.mkdir(parents=True, exist_ok=True)
    gen = ImageGenerator(cfg, ["local"] if offline else None)

    if refresh_mascot or not MASCOT_REFERENCE.exists():
        gen.generate_one(_mascot_prompt(cfg), out / "mascot.jpg", seed=11, show_mascot=False)
        if not offline:  # a arte de teste nunca vira referência do personagem
            ASSETS_DIR.mkdir(exist_ok=True)
            Image.open(out / "mascot.jpg").save(MASCOT_REFERENCE)
            log.info("Mascote de referência salvo em %s (revise antes de usar!)", MASCOT_REFERENCE)
    else:
        shutil.copy(MASCOT_REFERENCE, out / "mascot.png")

    portrait = gen.generate_one(_avatar_prompt(cfg), out / "avatar_raw.jpg", seed=21, show_mascot=False)
    img = Image.open(portrait)
    side = min(img.size)
    avatar = ImageOps.fit(img, (side, side), centering=(0.5, 0.42)).resize((800, 800), Image.Resampling.LANCZOS)
    avatar.save(out / "avatar.png")

    make_banner(cfg, avatar, out / "banner.jpg")

    ch = cfg["channel"]
    (out / "sobre_o_canal.txt").write_text(
        f"Nome: {ch['name']}\n\nDescrição:\n{ch['description']}\n\nPalavras-chave:\n"
        + ", ".join(ch.get("keywords", [])) + "\n",
        encoding="utf-8",
    )
    log.info("Kit do canal gerado em %s", out)
    return out
