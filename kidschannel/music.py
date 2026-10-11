"""Música de fundo.

Usa um arquivo da pasta configurada (ex.: faixas da Biblioteca de Áudio do YouTube)
ou compõe uma melodia alegre e original na hora, sem risco de direitos autorais.
"""

from __future__ import annotations

import random
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .config import ROOT

SR = 44100
MAJOR = [0, 2, 4, 5, 7, 9, 11]
PENTATONIC = [0, 2, 4, 7, 9]
# Progressões alegres, em graus da escala maior (0 = I, 4 = V, 5 = vi, 3 = IV).
PROGRESSIONS = [[0, 4, 5, 3], [0, 3, 4, 3], [0, 5, 3, 4], [3, 4, 0, 0]]


def pick_music_file(cfg: dict[str, Any], rng: random.Random) -> Path | None:
    folder = ROOT / cfg.get("music", {}).get("folder", "assets/music")
    if not folder.is_dir():
        return None
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".mp3", ".wav", ".ogg", ".m4a"})
    return rng.choice(files) if files else None


def _freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _tone(freq: float, dur: float, kind: str) -> np.ndarray:
    n = max(1, int(dur * SR))
    t = np.arange(n) / SR
    if kind == "bell":  # xilofone/marimba: harmônicos com decaimento rápido
        wave_ = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * freq * 4 * t) * np.exp(-t * 18)
        env = np.exp(-t * 6.5)
    elif kind == "pluck":
        wave_ = np.sin(2 * np.pi * freq * t) + 0.2 * np.sin(2 * np.pi * freq * 2 * t)
        env = np.exp(-t * 9)
    else:  # baixo
        wave_ = np.sin(2 * np.pi * freq * t) + 0.15 * np.sin(2 * np.pi * freq * 2 * t)
        env = np.exp(-t * 3.5)
    attack = min(n, int(0.006 * SR))
    env[:attack] *= np.linspace(0, 1, attack)
    return wave_ * env


def _kick(dur: float = 0.18) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    freq = 110 * np.exp(-t * 25) + 45
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 22)


def _shaker(rng: np.random.Generator, dur: float = 0.05) -> np.ndarray:
    n = int(dur * SR)
    noise = rng.standard_normal(n)
    noise = np.diff(noise, prepend=0)  # tira os graves
    return noise * np.exp(-np.arange(n) / SR * 70)


def _add(buf: np.ndarray, sig: np.ndarray, start: float, gain: float) -> None:
    i = int(start * SR)
    if i >= len(buf):
        return
    end = min(len(buf), i + len(sig))
    buf[i:end] += sig[: end - i] * gain


def compose(duration: float, seed: int) -> np.ndarray:
    """Devolve áudio estéreo float32 (n, 2) com uma musiquinha alegre."""
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    bpm = rng.uniform(100, 122)
    beat = 60.0 / bpm
    key = 60 + rng.choice([0, 2, 5, 7])  # Dó, Ré, Fá ou Sol
    prog = rng.choice(PROGRESSIONS)
    total = duration + 0.5
    left = np.zeros(int(total * SR) + SR, dtype=np.float64)
    right = np.zeros_like(left)

    # Motivo de 2 compassos (16 colcheias) repetido na forma A A B A.
    def motif() -> list[int | None]:
        notes, deg = [], rng.randrange(len(PENTATONIC))
        for _ in range(16):
            if rng.random() < 0.3:
                notes.append(None)
                continue
            deg = max(0, min(len(PENTATONIC) * 2 - 1, deg + rng.choice([-2, -1, -1, 0, 1, 1, 2])))
            notes.append(deg)
        return notes

    a, b = motif(), motif()
    form = [a, a, b, a]

    bar = 4 * beat
    n_bars = int(np.ceil(total / bar))
    for bar_i in range(n_bars):
        t0 = bar_i * bar
        degree = prog[bar_i % len(prog)]
        chord = [key + MAJOR[d % 7] + 12 * (d // 7) for d in (degree, degree + 2, degree + 4)]
        root = chord[0]

        # baixo nos tempos 1 e 3, bumbo leve junto
        for beat_i in (0, 2):
            _add(left, _tone(_freq(root - 24), beat * 1.8, "bass"), t0 + beat_i * beat, 0.32)
            _add(right, _tone(_freq(root - 24), beat * 1.8, "bass"), t0 + beat_i * beat, 0.32)
            _add(left, _kick(), t0 + beat_i * beat, 0.22)
            _add(right, _kick(), t0 + beat_i * beat, 0.22)

        # arpejo de acordes em colcheias (mais para a direita)
        for step in range(8):
            note = chord[[0, 1, 2, 1][step % 4]]
            _add(right, _tone(_freq(note), beat * 0.9, "pluck"), t0 + step * beat / 2, 0.10)
            _add(left, _tone(_freq(note), beat * 0.9, "pluck"), t0 + step * beat / 2, 0.05)
            # chocalho nos contratempos
            if step % 2 == 1:
                shake = _shaker(nrng)
                _add(left, shake, t0 + step * beat / 2, 0.025)
                _add(right, shake, t0 + step * beat / 2, 0.035)

        # melodia (mais para a esquerda)
        phrase = form[(bar_i // 2) % len(form)]
        half = (bar_i % 2) * 8
        for step in range(8):
            deg = phrase[half + step]
            if deg is None:
                continue
            octave, idx = divmod(deg, len(PENTATONIC))
            midi = key + 12 + PENTATONIC[idx] + 12 * octave
            note = _tone(_freq(midi), beat * 1.2, "bell")
            _add(left, note, t0 + step * beat / 2, 0.16)
            _add(right, note, t0 + step * beat / 2, 0.10)

    n = int(duration * SR)
    audio = np.stack([left[:n], right[:n]], axis=1)
    fade_in, fade_out = int(0.4 * SR), int(min(1.5, duration / 4) * SR)
    audio[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    audio[n - fade_out :] *= np.linspace(1, 0, fade_out)[:, None]
    peak = np.max(np.abs(audio)) or 1.0
    return (audio / peak * 0.7).astype(np.float32)


def write_wav(path: Path, audio: np.ndarray) -> Path:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(pcm.shape[1])
        wav.setsampwidth(2)
        wav.setframerate(SR)
        wav.writeframes(pcm.tobytes())
    return path


def background_music(cfg: dict[str, Any], duration: float, out_dir: Path, seed: int) -> Path | None:
    if not cfg.get("music", {}).get("enabled", True):
        return None
    rng = random.Random(seed)
    existing = pick_music_file(cfg, rng)
    if existing:
        return existing
    return write_wav(out_dir / "music.wav", compose(duration, seed))
