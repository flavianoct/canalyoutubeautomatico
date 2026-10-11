"""Geração das ilustrações de cada cena, com cadeia de provedores de reserva."""

from __future__ import annotations

import base64
import io
import logging
import os
import random
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .config import ASSETS_DIR
from .media import find_font

log = logging.getLogger(__name__)

MASCOT_REFERENCE = ASSETS_DIR / "mascot.png"


class ImageError(RuntimeError):
    pass


def build_prompt(cfg: dict[str, Any], scene_prompt: str, show_mascot: bool) -> str:
    style = cfg.get("style", {})
    parts = [style.get("image", "").strip(), scene_prompt.strip().rstrip(".")]
    if show_mascot:
        parts.append(cfg["channel"]["mascot"]["visual"].strip())
    if style.get("avoid"):
        parts.append(style["avoid"].strip())
    return ". ".join(p for p in parts if p)


def fit_cover(img: Image.Image, width: int, height: int) -> Image.Image:
    """Redimensiona e recorta para preencher exatamente width x height."""
    return ImageOps.fit(img.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)


# ---------------------------------------------------------------- provedores
class OpenAIProvider:
    name = "openai"

    def __init__(self, cfg: dict[str, Any]):
        self.key = os.environ.get("OPENAI_API_KEY")
        opts = cfg["images"].get("openai", {})
        self.model = opts.get("model", "gpt-image-1")
        self.size = opts.get("size", "1024x1536")
        self.quality = opts.get("quality", "medium")
        self.use_reference = opts.get("use_mascot_reference", True)

    def available(self) -> bool:
        return bool(self.key)

    def generate(self, prompt: str, seed: int, show_mascot: bool) -> Image.Image:
        headers = {"Authorization": f"Bearer {self.key}"}
        resp = None
        if show_mascot and self.use_reference and MASCOT_REFERENCE.exists():
            # Usa a imagem do mascote como referência para manter o personagem igual.
            with open(MASCOT_REFERENCE, "rb") as fh:
                resp = requests.post(
                    "https://api.openai.com/v1/images/edits",
                    headers=headers,
                    data={
                        "model": self.model,
                        "prompt": "Use the character from the reference image. " + prompt,
                        "size": self.size,
                        "quality": self.quality,
                    },
                    files=[("image[]", ("mascot.png", fh, "image/png"))],
                    timeout=300,
                )
            if resp.status_code != 200:
                log.warning("OpenAI com referência falhou (%s); gerando sem referência.", resp.status_code)
                resp = None
        if resp is None:
            resp = requests.post(
                "https://api.openai.com/v1/images/generations",
                headers=headers,
                json={"model": self.model, "prompt": prompt, "size": self.size, "quality": self.quality, "n": 1},
                timeout=300,
            )
        if resp.status_code != 200:
            raise ImageError(f"OpenAI {resp.status_code}: {resp.text[:300]}")
        data = resp.json()["data"][0]
        if data.get("b64_json"):
            raw = base64.b64decode(data["b64_json"])
        else:
            raw = requests.get(data["url"], timeout=120).content
        return Image.open(io.BytesIO(raw))


class PollinationsProvider:
    name = "pollinations"

    def __init__(self, cfg: dict[str, Any]):
        opts = cfg["images"].get("pollinations", {})
        self.base_url = opts.get("base_url", "https://image.pollinations.ai/prompt/")
        self.model = opts.get("model", "flux")
        self.token = os.environ.get("POLLINATIONS_TOKEN")
        self.width = cfg["video"]["width"]
        self.height = cfg["video"]["height"]

    def available(self) -> bool:
        return True

    def generate(self, prompt: str, seed: int, show_mascot: bool) -> Image.Image:
        url = self.base_url + urllib.parse.quote(prompt[:1800], safe="")
        params = {
            "width": self.width,
            "height": self.height,
            "seed": seed,
            "model": self.model,
            "nologo": "true",
            "safe": "true",
            "private": "true",
        }
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        last_error = ""
        for attempt in range(4):
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=180)
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                    return Image.open(io.BytesIO(resp.content))
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except requests.RequestException as exc:
                last_error = str(exc)
            time.sleep(8 * (attempt + 1))
        raise ImageError(f"Pollinations falhou: {last_error}")


class LocalProvider:
    """Ilustração simples desenhada localmente — só para testes offline."""

    name = "local"

    def __init__(self, cfg: dict[str, Any]):
        self.width = cfg["video"]["width"]
        self.height = cfg["video"]["height"]

    def available(self) -> bool:
        return True

    def generate(self, prompt: str, seed: int, show_mascot: bool) -> Image.Image:
        rng = random.Random(seed)
        w, h = self.width, self.height
        top = tuple(rng.randint(120, 255) for _ in range(3))
        bottom = tuple(rng.randint(60, 200) for _ in range(3))
        img = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(img)
        for y in range(h):
            t = y / h
            draw.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
        for _ in range(14):
            r = rng.randint(60, 260)
            x, y = rng.randint(0, w), rng.randint(0, h)
            color = tuple(rng.randint(150, 255) for _ in range(3))
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color)
        img = img.filter(ImageFilter.GaussianBlur(6))
        draw = ImageDraw.Draw(img)
        if show_mascot:
            cx, cy = w // 2, int(h * 0.42)
            draw.ellipse([cx - 220, cy - 220, cx + 220, cy + 220], fill=(250, 214, 90))
            for dx, dy in [(-90, -60), (80, 40), (-30, 110), (120, -120)]:
                draw.ellipse([cx + dx - 40, cy + dy - 40, cx + dx + 40, cy + dy + 40], fill=(240, 150, 60))
            for dx in (-80, 80):
                draw.ellipse([cx + dx - 32, cy - 70, cx + dx + 32, cy - 6], fill="white")
                draw.ellipse([cx + dx - 16, cy - 50, cx + dx + 16, cy - 18], fill=(70, 40, 20))
        font = find_font("DejaVu Sans:bold", 44)
        draw.text((60, h - 260), prompt[:60], font=font, fill="white", stroke_width=3, stroke_fill="black")
        return img


PROVIDERS = {"openai": OpenAIProvider, "pollinations": PollinationsProvider, "local": LocalProvider}


class ImageGenerator:
    def __init__(self, cfg: dict[str, Any], providers: list[str] | None = None):
        self.cfg = cfg
        names = providers or cfg["images"].get("providers", ["pollinations"])
        self.providers = []
        for name in names:
            if name not in PROVIDERS:
                raise ImageError(f"Provedor de imagem desconhecido: {name}")
            provider = PROVIDERS[name](cfg)
            if provider.available():
                self.providers.append(provider)
            else:
                log.info("Provedor de imagem '%s' sem chave configurada; pulando.", name)
        if not self.providers:
            raise ImageError("Nenhum provedor de imagem disponível.")

    def generate_one(self, prompt: str, out_path: Path, seed: int, show_mascot: bool) -> Path:
        w, h = self.cfg["video"]["width"], self.cfg["video"]["height"]
        errors = []
        for provider in self.providers:
            try:
                img = provider.generate(prompt, seed, show_mascot)
                fit_cover(img, w, h).save(out_path, quality=95)
                log.info("Imagem gerada com %s: %s", provider.name, out_path.name)
                return out_path
            except Exception as exc:  # tenta o próximo provedor
                log.warning("Provedor %s falhou: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
        raise ImageError("Todos os provedores de imagem falharam: " + " | ".join(errors))

    def generate_scenes(self, scenes: list[Any], out_dir: Path, base_seed: int) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        jobs = []
        for i, scene in enumerate(scenes):
            prompt = build_prompt(self.cfg, scene.image_prompt, scene.show_mascot)
            jobs.append((prompt, out_dir / f"scene_{i:02d}.jpg", base_seed + i, scene.show_mascot))
        workers = max(1, int(self.cfg["images"].get("max_workers", 2)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda job: self.generate_one(*job), jobs))
