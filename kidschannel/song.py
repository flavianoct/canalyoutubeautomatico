"""Músicas cantadas: compõe com a ElevenLabs Music e sincroniza a letra com o áudio.

O plano de composição define a duração exata de cada seção (refrão, verso...) e a
letra de cada uma. A API devolve o tempo de cada palavra cantada; com isso as
legendas karaokê e os cortes de imagem caem exatamente no ritmo da música.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .media import probe_duration
from .music import compose, write_wav
from .script_writer import Script
from .tts import Word

log = logging.getLogger(__name__)


class SongError(RuntimeError):
    pass


@dataclass
class SongAudio:
    path: Path
    duration: float
    lines: list[list[Word]]  # palavras de cada linha da letra, no tempo em que são cantadas

    @property
    def line_starts(self) -> list[float]:
        return [line[0].start if line else 0.0 for line in self.lines]


# ---------------------------------------------------------------- alinhamento da letra
def _norm(token: str) -> str:
    token = unicodedata.normalize("NFD", token.lower())
    token = "".join(c for c in token if unicodedata.category(c) != "Mn")
    token = re.sub(r"[^\w]", "", token)
    return re.sub(r"(.)\1+", r"\1", token)  # "muuu" e "mu" contam como a mesma palavra


def section_bounds(script: Script) -> list[tuple[float, float]]:
    bounds, t = [], 0.0
    for section in script.sections:
        bounds.append((t, t + section.duration))
        t += section.duration
    return bounds


def even_timings(script: Script) -> list[list[Word]]:
    """Tempo estimado: as linhas dividem igualmente o tempo de cada seção."""
    lines: list[list[Word]] = []
    for section, (start, end) in zip(script.sections, section_bounds(script)):
        usable_start = start + min(0.3, section.duration * 0.05)
        usable_end = end - min(0.4, section.duration * 0.08)
        span = (usable_end - usable_start) / max(1, len(section.lines))
        for i, line in enumerate(section.lines):
            tokens = line.narration.split()
            l_start = usable_start + i * span
            step = span * 0.92 / max(1, len(tokens))
            lines.append([Word(tok, l_start + k * step, l_start + (k + 1) * step) for k, tok in enumerate(tokens)])
    return lines


def align_lyrics(script: Script, sung: list[Word], min_match: float = 0.4) -> list[list[Word]]:
    """Casa as palavras da letra com as palavras cantadas (que podem repetir ou faltar).

    Palavras casadas usam o tempo real; as outras são interpoladas entre vizinhas.
    Se quase nada casar, usa a estimativa por seção.
    """
    estimate = even_timings(script)
    flat = [w for line in estimate for w in line]
    if not sung or not flat:
        return estimate
    lyric_norm = [_norm(w.text) for w in flat]
    sung = [w for w in sung if _norm(w.text)]
    sung_norm = [_norm(w.text) for w in sung]
    matcher = SequenceMatcher(None, lyric_norm, sung_norm, autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(flat)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            s = sung[block.b + k]
            times[block.a + k] = (s.start, max(s.end, s.start + 0.05))
    matched = sum(t is not None for t in times)
    if matched / len(flat) < min_match:
        log.warning("Letra pouco reconhecida no áudio (%d/%d palavras); usando tempo estimado.", matched, len(flat))
        return estimate

    # interpola os buracos entre âncoras reais
    i = 0
    while i < len(flat):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(flat) and times[j] is None:
            j += 1
        left = times[i - 1][1] if i > 0 else flat[i].start
        right = times[j][0] if j < len(flat) else max(left, flat[j - 1].end)
        if right < left:
            right = left
        step = (right - left) / (j - i)
        for k in range(i, j):
            times[k] = (left + (k - i) * step, left + (k - i + 1) * step)
        i = j

    result, idx = [], 0
    for line in estimate:
        words = []
        for w in line:
            start, end = times[idx]
            words.append(Word(w.text, start, end))
            idx += 1
        result.append(words)
    return result


def _finish(path: Path, lines: list[list[Word]]) -> SongAudio:
    return SongAudio(path=path, duration=probe_duration(path), lines=lines)


# ---------------------------------------------------------------- ElevenLabs Music
class ElevenLabsSong:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg.get("song", {})
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise SongError("As músicas cantadas precisam de ELEVENLABS_API_KEY (ElevenLabs Music).")
        from elevenlabs.client import ElevenLabs

        self.client = ElevenLabs(api_key=key, timeout=600)
        self.model = self.cfg.get("model_id", "music_v2_5")

    def plan(self, script: Script):
        styles = list(self.cfg.get("styles", []))
        if script.music_style:
            styles.insert(0, script.music_style)
        negative = list(self.cfg.get("negative_styles", []))
        section_hints = self.cfg.get("section_styles", {})

        if self.model.startswith("music_v1"):
            from elevenlabs.types import MusicPrompt, SongSection

            return MusicPrompt(
                positive_global_styles=styles,
                negative_global_styles=negative,
                sections=[
                    SongSection(
                        section_name=s.name,
                        positive_local_styles=list(section_hints.get(s.name.lower(), [])),
                        negative_local_styles=[],
                        duration_ms=int(round(s.duration * 1000)),
                        lines=[line.narration for line in s.lines],
                    )
                    for s in script.sections
                ],
            )

        from elevenlabs.types import CompositionPlan, GenerationChunkInput

        chunks = []
        for i, s in enumerate(script.sections):
            # O primeiro trecho define o gênero da música inteira; os seguintes dão nuances.
            chunk_styles = styles if i == 0 else styles[:3] + list(section_hints.get(s.name.lower(), []))
            chunks.append(
                GenerationChunkInput(
                    text=f"[{s.name}]\n" + "\n".join(line.narration for line in s.lines),
                    duration_ms=int(round(s.duration * 1000)),
                    positive_styles=chunk_styles,
                    negative_styles=negative or None,
                )
            )
        return CompositionPlan(chunks=chunks)

    def create(self, script: Script, out_dir: Path) -> SongAudio:
        log.info("Compondo a música (%s, %.0fs)...", self.model, script.song_duration)
        try:
            response = self.client.music.compose_detailed(
                composition_plan=self.plan(script),
                model_id=self.model,
                with_timestamps=True,
            )
        except Exception as exc:
            raise SongError(f"ElevenLabs Music falhou: {exc}") from exc
        path = out_dir / "song.mp3"
        path.write_bytes(response.audio)
        sung = _words_from_metadata(response.json)
        if not sung and self.cfg.get("transcribe_fallback", True):
            sung = self.transcribe(path)
        lines = align_lyrics(script, sung)
        return _finish(path, lines)

    def transcribe(self, path: Path) -> list[Word]:
        """Plano B para os tempos: transcreve a música cantada (Scribe)."""
        try:
            with open(path, "rb") as fh:
                result = self.client.speech_to_text.convert(
                    model_id=self.cfg.get("transcribe_model", "scribe_v1"),
                    file=fh,
                    language_code="por",
                    timestamps_granularity="word",
                    tag_audio_events=False,
                )
        except Exception as exc:
            log.warning("Transcrição da música falhou (%s); usando tempo estimado.", exc)
            return []
        return [
            Word(w.text, float(w.start), float(w.end))
            for w in (result.words or [])
            if getattr(w, "type", "word") == "word" and w.start is not None and w.end is not None
        ]


def _words_from_metadata(meta: dict[str, Any] | None) -> list[Word]:
    if not meta:
        return []
    items = meta.get("words_timestamps") or meta.get("wordsTimestamps") or []
    words = []
    for item in items:
        text = item.get("word") or item.get("text") or ""
        start = item.get("start_ms", item.get("startMs"))
        end = item.get("end_ms", item.get("endMs"))
        if text.strip() and start is not None and end is not None:
            words.append(Word(text.strip(), start / 1000.0, end / 1000.0))
    return words


# ---------------------------------------------------------------- offline
def offline_song(script: Script, out_dir: Path, seed: int) -> SongAudio:
    """Sem API: base instrumental composta localmente e tempo estimado da letra."""
    path = write_wav(out_dir / "song.wav", compose(script.song_duration, seed))
    return _finish(path, even_timings(script))


def make_song(cfg: dict[str, Any], script: Script, out_dir: Path, seed: int, offline: bool = False) -> SongAudio:
    if offline:
        return offline_song(script, out_dir, seed)
    provider = cfg.get("song", {}).get("provider", "elevenlabs")
    if provider != "elevenlabs":
        raise SongError(f"Provedor de música desconhecido: {provider}")
    return ElevenLabsSong(cfg).create(script, out_dir)


def song_available(cfg: dict[str, Any]) -> bool:
    return cfg.get("song", {}).get("provider", "elevenlabs") == "elevenlabs" and bool(
        os.environ.get("ELEVENLABS_API_KEY")
    )
