"""Memória do canal: histórico de vídeos publicados e IDs de playlists.

Fica em data/state.json, que o GitHub Actions commita de volta no repositório
para que o robô não repita temas entre execuções.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR

STATE_FILE = DATA_DIR / "state.json"


class ChannelState:
    def __init__(self, path: Path = STATE_FILE):
        self.path = Path(path)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {}
        self.data.setdefault("history", [])
        self.data.setdefault("playlists", {})

    # ---------- persistência ----------
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    # ---------- histórico ----------
    @property
    def history(self) -> list[dict[str, Any]]:
        return self.data["history"]

    def add_video(self, entry: dict[str, Any]) -> None:
        entry = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
        self.history.append(entry)

    def recent_topics(self, series_id: str | None = None, limit: int = 80) -> list[str]:
        items = [h for h in self.history if series_id is None or h.get("series") == series_id]
        return [h["topic"] for h in items[-limit:] if h.get("topic")]

    def pick_series(self, series: list[dict[str, Any]], rng: random.Random | None = None) -> dict[str, Any]:
        """Sorteia a série pelo peso, evitando repetir a última publicada."""
        rng = rng or random.Random()
        last = self.history[-1]["series"] if self.history else None
        candidates = [s for s in series if s["id"] != last] or list(series)
        weights = [max(float(s.get("weight", 1)), 0.01) for s in candidates]
        return rng.choices(candidates, weights=weights, k=1)[0]

    # ---------- playlists ----------
    def playlist_for(self, series_id: str) -> str | None:
        return self.data["playlists"].get(series_id)

    def set_playlist(self, series_id: str, playlist_id: str) -> None:
        self.data["playlists"][series_id] = playlist_id
