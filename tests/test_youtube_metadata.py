from datetime import datetime, timezone

from kidschannel.script_writer import Scene, Script
from kidschannel.youtube import build_video_body, clean_description, clean_tags, clean_title


def make_script(**kw):
    data = dict(series="curiosidades", topic="polvo", title="O polvo tem <três> corações?",
                description="Aprenda sobre o polvo.", tags=["polvo", "animais marinhos", "Polvo"],
                scenes=[Scene("Oi!", "octopus", False)])
    data.update(kw)
    return Script(**data)


def test_clean_title_removes_angle_brackets_and_limits_length():
    assert clean_title("O <polvo>") == "O polvo"
    assert len(clean_title("a" * 300)) == 100


def test_clean_description_limits_bytes_without_breaking_utf8():
    text = "ç" * 4000  # 2 bytes cada
    out = clean_description(text)
    assert len(out.encode("utf-8")) <= 4900
    out.encode("utf-8")  # continua UTF-8 válido


def test_clean_tags_dedup_hash_and_limit():
    tags = clean_tags(["#polvo", "Polvo", "animais marinhos", "a,b"] + [f"tag{i:03d}" for i in range(200)])
    assert tags[:3] == ["polvo", "animais marinhos", "a b"]
    cost = sum(len(t) + (2 if " " in t else 0) for t in tags) + len(tags) - 1
    assert cost <= 480


def test_video_body_is_made_for_kids(cfg):
    series = cfg["series"][0]
    body = build_video_body(cfg, make_script(), series)
    assert body["status"]["selfDeclaredMadeForKids"] is True
    assert body["status"]["privacyStatus"] == cfg["publishing"]["privacy_status"]
    assert body["snippet"]["title"] == "O polvo tem três corações?"
    assert "#shorts" in body["snippet"]["description"]
    assert body["snippet"]["categoryId"] == "27"
    assert body["snippet"]["defaultAudioLanguage"] == "pt-BR"


def test_scheduled_video_is_private_with_publish_at(cfg):
    when = datetime(2030, 1, 2, 14, 0, tzinfo=timezone.utc)
    body = build_video_body(cfg, make_script(), cfg["series"][0], publish_at=when)
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2030-01-02T14:00:00+00:00"
