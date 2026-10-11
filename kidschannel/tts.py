"""Narração (texto -> voz) com o tempo de cada palavra, para as legendas animadas."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .media import probe_duration

log = logging.getLogger(__name__)


class TTSError(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    start: float  # segundos, relativo ao início do áudio da cena
    end: float


def _norm(token: str) -> str:
    return re.sub(r"[^\w]", "", token.lower())


def align_display_words(narration: str, timed: list[Word]) -> list[Word]:
    """Troca o texto vindo do TTS pelas palavras originais (com pontuação), se baterem."""
    display = narration.split()
    timed = [w for w in timed if _norm(w.text)]
    if len(display) == len(timed):
        return [Word(d, w.start, w.end) for d, w in zip(display, timed)]
    return timed


def even_word_timings(text: str, duration: float, start: float = 0.0) -> list[Word]:
    words = text.split()
    if not words:
        return []
    step = (duration - start) / len(words)
    return [Word(w, start + i * step, start + (i + 1) * step) for i, w in enumerate(words)]


# ---------------------------------------------------------------- Edge TTS
class EdgeTTS:
    """Vozes neurais da Microsoft via edge-tts (gratuito, sem chave)."""

    def __init__(self, cfg: dict[str, Any]):
        opts = cfg["voice"].get("edge", {})
        self.voice = opts.get("voice", "pt-BR-FranciscaNeural")
        self.rate = opts.get("rate", "+0%")
        self.pitch = opts.get("pitch", "+0Hz")

    async def _run(self, text: str, out_path: Path) -> list[Word]:
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            self.voice,
            rate=self.rate,
            pitch=self.pitch,
            boundary="WordBoundary",
            proxy=os.environ.get("HTTPS_PROXY") or None,
        )
        words: list[Word] = []
        with open(out_path, "wb") as fh:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append(Word(chunk["text"], start, start + chunk["duration"] / 1e7))
        return words

    def synthesize(self, text: str, out_path: Path) -> list[Word]:
        last: Exception | None = None
        for attempt in range(3):
            try:
                words = asyncio.run(self._run(text, out_path.with_suffix(".mp3")))
                return align_display_words(text, words) or even_word_timings(
                    text, probe_duration(out_path.with_suffix(".mp3"))
                )
            except Exception as exc:  # rede instável: tenta de novo
                last = exc
                log.warning("edge-tts falhou (tentativa %d): %s", attempt + 1, exc)
        raise TTSError(f"edge-tts falhou: {last}")


# ---------------------------------------------------------------- ElevenLabs
class ElevenLabsTTS:
    """Vozes da ElevenLabs (pago, mais expressivo). Precisa de ELEVENLABS_API_KEY."""

    def __init__(self, cfg: dict[str, Any]):
        opts = cfg["voice"].get("elevenlabs", {})
        self.key = os.environ.get("ELEVENLABS_API_KEY")
        self.voice_id = os.environ.get("ELEVENLABS_VOICE_ID") or opts.get("voice_id")
        self.model_id = opts.get("model_id", "eleven_multilingual_v2")
        self.settings = {
            "stability": opts.get("stability", 0.45),
            "similarity_boost": opts.get("similarity_boost", 0.8),
            "style": opts.get("style", 0.3),
        }
        if not self.key or not self.voice_id:
            raise TTSError("Configure ELEVENLABS_API_KEY e voice.elevenlabs.voice_id para usar a ElevenLabs.")

    def synthesize(self, text: str, out_path: Path) -> list[Word]:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/with-timestamps",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": self.key, "Content-Type": "application/json"},
            json={"text": text, "model_id": self.model_id, "voice_settings": self.settings},
            timeout=180,
        )
        if resp.status_code != 200:
            raise TTSError(f"ElevenLabs {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        out_path.with_suffix(".mp3").write_bytes(base64.b64decode(data["audio_base64"]))
        align = data.get("alignment") or data.get("normalized_alignment")
        if not align:
            return even_word_timings(text, probe_duration(out_path.with_suffix(".mp3")))
        return _words_from_chars(
            align["characters"], align["character_start_times_seconds"], align["character_end_times_seconds"]
        )


def _words_from_chars(chars: list[str], starts: list[float], ends: list[float]) -> list[Word]:
    words: list[Word] = []
    buf, w_start, w_end = "", 0.0, 0.0
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if buf:
                words.append(Word(buf, w_start, w_end))
            buf = ""
            continue
        if not buf:
            w_start = s
        buf += ch
        w_end = e
    if buf:
        words.append(Word(buf, w_start, w_end))
    return words


# ---------------------------------------------------------------- Silencioso (testes)
class SilentTTS:
    """Gera silêncio com duração proporcional ao texto — para testes offline."""

    seconds_per_word = 0.38

    def __init__(self, cfg: dict[str, Any] | None = None):
        pass

    def synthesize(self, text: str, out_path: Path) -> list[Word]:
        duration = 0.3 + len(text.split()) * self.seconds_per_word
        rate = 24000
        with wave.open(str(out_path.with_suffix(".wav")), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(b"\x00\x00" * int(duration * rate))
        return even_word_timings(text, duration, start=0.15)


def make_tts(cfg: dict[str, Any], provider: str | None = None):
    provider = provider or cfg["voice"].get("provider", "edge")
    if provider == "edge":
        return EdgeTTS(cfg)
    if provider == "elevenlabs":
        return ElevenLabsTTS(cfg)
    if provider == "silent":
        return SilentTTS(cfg)
    raise TTSError(f"Provedor de voz desconhecido: {provider}")


def audio_file(base: Path) -> Path:
    """O TTS decide a extensão (.mp3/.wav); devolve o arquivo que existir."""
    for ext in (".mp3", ".wav"):
        if base.with_suffix(ext).exists():
            return base.with_suffix(ext)
    raise TTSError(f"Áudio não encontrado para {base}")
