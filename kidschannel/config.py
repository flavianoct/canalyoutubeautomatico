"""Leitura do config.yaml e caminhos do projeto."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"
OUTPUT_DIR = ROOT / "output"
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"


class ConfigError(RuntimeError):
    pass


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    path = Path(path or os.environ.get("KIDSCHANNEL_CONFIG") or DEFAULT_CONFIG)
    if not path.exists():
        raise ConfigError(f"Arquivo de configuração não encontrado: {path}")
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    for key in ("channel", "series", "llm", "images", "voice", "video", "publishing"):
        if key not in cfg:
            raise ConfigError(f"Seção obrigatória ausente no config: '{key}'")
    if not cfg["series"]:
        raise ConfigError("Defina ao menos uma série em 'series'.")
    ids = [s["id"] for s in cfg["series"]]
    if len(ids) != len(set(ids)):
        raise ConfigError("Há séries com 'id' repetido.")
    cfg.setdefault("music", {"enabled": False})
    cfg.setdefault("style", {})
    return cfg


def series_by_id(cfg: dict[str, Any], series_id: str) -> dict[str, Any]:
    for s in cfg["series"]:
        if s["id"] == series_id:
            return s
    raise ConfigError(f"Série desconhecida: {series_id}. Opções: {[s['id'] for s in cfg['series']]}")


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Carrega chaves de um arquivo .env local (sem sobrescrever variáveis já definidas)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)
