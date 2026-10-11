import json

import pytest

from kidschannel.captions import TimedWord, chunk_words
from kidschannel.script_writer import SONG, Script, ScriptWriter, offline_song, song_checks
from kidschannel.song import ElevenLabsSong, _words_from_metadata, align_lyrics, even_timings
from kidschannel.tts import Word
from kidschannel.video import song_timeline

from .test_pipeline_parts import FakeLLM

# Trecho real da transcrição de uma música gerada (o "muu" virou "mu" e o cantor repetiu).
SUNG = [
    ("Quá,", 0.38, 0.38), ("quá,", 0.38, 0.52), ("quá.", 0.64, 0.98), ("O", 1.18, 1.22),
    ("patinho", 1.28, 1.58), ("quer", 1.64, 1.86), ("nadar.", 1.9, 2.38),
    ("Quá,", 4.14, 4.26), ("quá,", 4.38, 4.52), ("quá.", 4.64, 4.88), ("Vem", 5.16, 5.3),
    ("comigo", 5.38, 5.8), ("cantar.", 5.88, 6.48),
    ("Mu,", 7.42, 7.52), ("mu,", 7.66, 7.8), ("mu,", 7.92, 8.04), ("mu.", 8.18, 8.46),
    ("A", 8.9, 8.96), ("vaquinha", 9.0, 9.34), ("diz", 9.38, 9.5), ("bom", 9.52, 9.64), ("dia.", 9.7, 10.14),
]


@pytest.fixture
def song():
    return offline_song({"id": "musica"})


def test_song_script_roundtrip(song):
    data = json.loads(json.dumps(song.to_dict()))
    again = Script.from_dict(data)
    assert again.format == SONG
    assert [s.narration for s in again.scenes] == [s.narration for s in song.scenes]
    assert again.song_duration == pytest.approx(26.0)


def test_song_checks_pass_and_catch_problems(song):
    assert song_checks(song, 20, 40) == []
    song.sections[1].duration = 2.0
    issues = song_checks(song, 20, 40)
    assert any("3 a 30 segundos" in i for i in issues)
    assert any("palavras demais" in i for i in issues)


def test_even_timings_stay_inside_sections(song):
    lines = even_timings(song)
    assert len(lines) == len(song.scenes)
    assert lines[0][0].start >= 0 and lines[1][-1].end <= 7.0  # refrão inicial (0-7s)
    assert lines[2][0].start >= 7.0  # verso começa depois


def test_align_lyrics_uses_real_times_and_fuzzy_repeats(song):
    lines = align_lyrics(song, [Word(*w) for w in SUNG], min_match=0.3)
    assert lines[0][0].text == "Quá" and lines[0][0].start == pytest.approx(0.38)
    assert lines[0][-1].text == "nadar" and lines[0][-1].end == pytest.approx(2.38)
    assert lines[2][0].text == "Muu" and lines[2][0].start == pytest.approx(7.42)  # "muu" ~ "mu"
    starts = [line[0].start for line in lines]
    assert starts == sorted(starts)


def test_align_lyrics_falls_back_when_nothing_matches(song):
    junk = [Word("blá", i * 0.5, i * 0.5 + 0.3) for i in range(30)]
    assert align_lyrics(song, junk) == even_timings(song)


def test_song_timeline_merges_lines_too_close():
    tl, kept = song_timeline([0.4, 2.0, 2.5, 6.0, 9.5], total=11.0, transition=0.4, min_scene=1.2)
    assert kept == [0, 1, 3, 4]
    assert tl.starts[0] == 0.0
    assert tl.clip_lengths[-1] == pytest.approx(11.0 - tl.starts[-1])
    assert all(length > 0.4 for length in tl.clip_lengths)


def test_caption_line_breaks_split_verses():
    words = [TimedWord("um", 0, 0.2), TimedWord("dois", 0.2, 0.4, line_break=True), TimedWord("três", 0.4, 0.6)]
    assert [[w.text for w in c] for c in chunk_words(words, 4)] == [["um", "dois"], ["três"]]


def test_words_from_metadata_accepts_both_spellings():
    snake = {"words_timestamps": [{"word": "Oi", "start_ms": 100, "end_ms": 400}]}
    camel = {"wordsTimestamps": [{"word": "Oi", "startMs": 100, "endMs": 400}]}
    assert _words_from_metadata(snake) == _words_from_metadata(camel) == [Word("Oi", 0.1, 0.4)]
    assert _words_from_metadata({}) == []


def test_elevenlabs_plan_formats(cfg, song, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test")
    v2 = ElevenLabsSong(cfg).plan(song)
    assert len(v2.chunks) == 4
    assert v2.chunks[0].text.startswith("[Chorus]\nQuá quá quá")
    assert v2.chunks[0].duration_ms == 7000
    assert song.music_style in v2.chunks[0].positive_styles

    cfg["song"]["model_id"] = "music_v1"
    v1 = ElevenLabsSong(cfg).plan(song)
    assert [s.section_name for s in v1.sections] == ["Chorus", "Verse", "Chorus", "Outro"]
    assert v1.sections[3].lines[-1] == "E de novo: quá quá quá!"


def test_song_requires_key(cfg, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    from kidschannel.song import SongError

    with pytest.raises(SongError):
        ElevenLabsSong(cfg)


def test_writer_song_flow(cfg):
    raw = {
        "topic": "cores", "title": "A música das cores", "description": "Aprenda as cores cantando.",
        "tags": ["cores"], "music_style": "bouncy kids pop, ukulele, 115 BPM",
        "sections": [
            {"name": "Chorus", "duration_seconds": 8, "lines": [
                {"text": "Vermelho, azul e amarelo", "image_prompt": "rainbow", "show_mascot": True}]},
            {"name": "Verse", "duration_seconds": 8, "lines": [
                {"text": "O céu é azulzinho", "image_prompt": "blue sky", "show_mascot": False}]},
            {"name": "Chorus", "duration_seconds": 8, "lines": [
                {"text": "Vermelho, azul e amarelo", "image_prompt": "rainbow", "show_mascot": True}]},
        ],
    }
    llm = FakeLLM([raw, {"approved": True, "issues": []}])
    series = next(s for s in cfg["series"] if s.get("format") == "song")
    script = ScriptWriter(cfg, llm=llm).write(series, recent_topics=[])
    assert script.format == SONG and script.song_duration == 24
    assert len(script.scenes) == 3


def test_song_series_skipped_without_key(cfg, monkeypatch):
    from kidschannel.pipeline import available_series

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    ids = {s["id"] for s in available_series(cfg)}
    assert "musica" not in ids and "curiosidades" in ids
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test")
    assert "musica" in {s["id"] for s in available_series(cfg)}
