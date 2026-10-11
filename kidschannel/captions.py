"""Legendas animadas no formato ASS (palavra falada destacada em amarelo)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

HIGHLIGHT = "&H00E5FF&"  # amarelo (formato &HBBGGRR&)
NORMAL = "&HFFFFFF&"


@dataclass
class TimedWord:
    text: str
    start: float  # segundos no vídeo final
    end: float


def ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[TimedWord], max_words: int) -> list[list[TimedWord]]:
    """Agrupa palavras em blocos curtos, quebrando também no fim das frases."""
    chunks: list[list[TimedWord]] = []
    current: list[TimedWord] = []
    for i, word in enumerate(words):
        if current and word.start - current[-1].end > 0.6:  # pausa longa / troca de cena
            chunks.append(current)
            current = []
        current.append(word)
        if len(current) >= max_words or word.text.endswith((".", "!", "?", ",", ":", ";")):
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def build_ass(
    words: list[TimedWord],
    width: int,
    height: int,
    font: str,
    font_size: int,
    max_words: int = 3,
    label: str | None = None,
    duration: float | None = None,
) -> str:
    caption_margin = int(height * 0.32)  # acima da área de botões dos Shorts
    label_margin = int(height * 0.09)  # abaixo da barra superior dos Shorts
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{font},{font_size},&H00FFFFFF,&H000000FF,&H00301A0B,&H78000000,"
        f"-1,0,0,0,100,100,1,0,1,{max(4, font_size // 12)},4,2,70,70,{caption_margin},1",
        f"Style: Label,{font},{int(font_size * 0.62)},&H00FFFFFF,&H000000FF,&H00B4581E,&H00000000,"
        f"-1,0,0,0,100,100,3,0,3,16,0,8,60,60,{label_margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if label and duration:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(duration)},Label,,0,0,0,,{escape(label)}")

    chunks = chunk_words(words, max_words)
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0].start if ci + 1 < len(chunks) else None
        chunk_end = chunk[-1].end + 0.3
        if next_start is not None:
            chunk_end = min(chunk_end, next_start)
        for k, word in enumerate(chunk):
            start = chunk[0].start if k == 0 else word.start
            end = chunk[k + 1].start if k + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            parts = []
            for j, w in enumerate(chunk):
                if j == k:
                    parts.append(f"{{\\c{HIGHLIGHT}\\fscx112\\fscy112}}{escape(w.text)}{{\\c{NORMAL}\\fscx100\\fscy100}}")
                else:
                    parts.append(escape(w.text))
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{' '.join(parts)}")
    return "\n".join(lines) + "\n"


def write_ass(path: Path, *args, **kwargs) -> Path:
    path.write_text(build_ass(*args, **kwargs), encoding="utf-8")
    return path
