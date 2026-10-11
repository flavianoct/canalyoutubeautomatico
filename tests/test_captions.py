from kidschannel.captions import TimedWord, ass_time, build_ass, chunk_words, escape


def words(*items):
    return [TimedWord(t, s, e) for t, s, e in items]


def test_ass_time_format():
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(61.257) == "0:01:01.26"
    assert ass_time(3725.5) == "1:02:05.50"


def test_escape_removes_override_braces():
    assert escape("oi {\\b1} tchau") == "oi (/b1) tchau"


def test_chunks_respect_size_and_sentence_end():
    ws = words(("Oi,", 0, 0.3), ("tudo", 0.3, 0.6), ("bem", 0.6, 0.9), ("com", 0.9, 1.2),
               ("você", 1.2, 1.5), ("hoje?", 1.5, 1.8), ("Vamos", 1.8, 2.1))
    chunks = [[w.text for w in c] for c in chunk_words(ws, 3)]
    assert chunks == [["Oi,"], ["tudo", "bem", "com"], ["você", "hoje?"], ["Vamos"]]


def test_chunks_split_on_long_pause():
    ws = words(("um", 0, 0.3), ("dois", 2.0, 2.3))
    assert len(chunk_words(ws, 3)) == 2


def test_build_ass_highlights_each_word_once():
    ws = words(("Olá", 0.0, 0.4), ("amigos", 0.4, 0.9))
    ass = build_ass(ws, 1080, 1920, "DejaVu Sans", 80, max_words=3, label="TESTE", duration=2.0)
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue: 0")]
    assert len(dialogues) == 2
    assert "{\\c&H00E5FF&" in dialogues[0] and dialogues[0].index("Olá") < dialogues[0].index("amigos")
    assert "Dialogue: 1,0:00:00.00,0:00:02.00,Label,,0,0,0,,TESTE" in ass
    assert "PlayResY: 1920" in ass
