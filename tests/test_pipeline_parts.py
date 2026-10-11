import random
from datetime import datetime, timezone

import numpy as np
import pytest

from kidschannel.llm import LLMError
from kidschannel.music import SR, compose
from kidschannel.pipeline import next_slots, slugify
from kidschannel.script_writer import ScriptWriter, local_checks, offline_script
from kidschannel.state import ChannelState
from kidschannel.tts import Word, _words_from_chars, align_display_words
from kidschannel.video import plan_timeline


# ---------------------------------------------------------------- agendamento
def test_next_slots_skips_past_and_too_close_times():
    now = datetime(2030, 5, 10, 13, 50, tzinfo=timezone.utc)  # 10:50 em São Paulo
    slots = next_slots(["11:00", "17:30"], "America/Sao_Paulo", 3, now=now)
    local = [s.astimezone().isoformat() for s in slots]
    assert [s.isoformat() for s in slots] == [
        "2030-05-10T20:30:00+00:00",  # 17:30 hoje (11:00 está a menos de 45 min)
        "2030-05-11T14:00:00+00:00",  # 11:00 amanhã
        "2030-05-11T20:30:00+00:00",
    ], local


def test_slugify():
    assert slugify("Por que o céu é azul?") == "por-que-o-ceu-e-azul"


# ---------------------------------------------------------------- linha do tempo
def test_plan_timeline_accounts_for_transitions():
    tl = plan_timeline([2.0, 3.0], lead=0.25, tail=0.45, transition=0.4, end_hold=0.6)
    assert tl.starts == [0.0, pytest.approx(2.7)]
    assert tl.clip_lengths == [pytest.approx(3.1), pytest.approx(4.3)]
    assert tl.total == pytest.approx(2.7 + 4.3)


# ---------------------------------------------------------------- música
def test_compose_music_shape_and_level():
    audio = compose(5.0, seed=3)
    assert audio.shape == (int(5.0 * SR), 2)
    assert 0.5 < np.max(np.abs(audio)) <= 0.7001
    assert np.std(audio) > 0.01


# ---------------------------------------------------------------- narração
def test_align_display_words_keeps_punctuation():
    timed = [Word("Oi", 0, 0.2), Word("amiguinhos", 0.2, 0.8)]
    out = align_display_words("Oi, amiguinhos!", timed)
    assert [w.text for w in out] == ["Oi,", "amiguinhos!"]
    assert out[1].start == 0.2


def test_words_from_elevenlabs_characters():
    chars = list("oi tu")
    starts = [0.0, 0.1, 0.2, 0.3, 0.4]
    ends = [0.1, 0.2, 0.3, 0.4, 0.5]
    out = _words_from_chars(chars, starts, ends)
    assert [(w.text, w.start, w.end) for w in out] == [("oi", 0.0, 0.2), ("tu", 0.3, 0.5)]


# ---------------------------------------------------------------- roteiro
def test_local_checks(cfg):
    series = cfg["series"][0]
    script = offline_script(cfg, series)
    assert local_checks(script, 20, 200) == []
    script.scenes[0].narration = "Ele pegou uma faca."
    assert any("faca" in issue for issue in local_checks(script, 20, 200))
    script.scenes[0].narration = "O burro é um animal muito forte."  # é o animal, não ofensa
    assert local_checks(script, 20, 200) == []


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, system, prompt, schema, max_tokens=16000):
        self.prompts.append(prompt)
        return self.responses.pop(0)


def _raw_script(narration_words=12):
    scene = {"narration": " ".join(["palavra"] * narration_words), "image_prompt": "a cat", "show_mascot": False}
    return {"topic": "gatos", "title": "Gatos", "description": "Sobre gatos.", "tags": ["gato"], "scenes": [scene] * 6}


def test_writer_retries_with_review_feedback(cfg):
    llm = FakeLLM([
        _raw_script(), {"approved": False, "issues": ["Fato incorreto na cena 2."]},
        _raw_script(), {"approved": True, "issues": []},
    ])
    script = ScriptWriter(cfg, llm=llm).write(cfg["series"][0], recent_topics=["polvo"])
    assert script.topic == "gatos" and script.series == cfg["series"][0]["id"]
    assert "Fato incorreto na cena 2." in llm.prompts[2]
    assert "polvo" in llm.prompts[0]


def test_writer_gives_up_after_max_attempts(cfg):
    cfg["llm"]["max_attempts"] = 2
    too_short = _raw_script(narration_words=1)
    llm = FakeLLM([too_short, too_short])
    with pytest.raises(LLMError):
        ScriptWriter(cfg, llm=llm).write(cfg["series"][0], recent_topics=[])


# ---------------------------------------------------------------- estado
def test_state_avoids_repeating_last_series(tmp_path, cfg):
    state = ChannelState(tmp_path / "state.json")
    state.add_video({"series": "curiosidades", "topic": "polvo"})
    rng = random.Random(0)
    picks = {state.pick_series(cfg["series"], rng)["id"] for _ in range(50)}
    assert "curiosidades" not in picks
    state.save()
    again = ChannelState(tmp_path / "state.json")
    assert again.recent_topics("curiosidades") == ["polvo"]
