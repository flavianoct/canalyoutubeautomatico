"""Renderiza um Short completo sem nenhuma API (imagens de teste e áudio mudo)."""

from kidschannel.media import probe_duration
from kidschannel.pipeline import make_short
from kidschannel.state import ChannelState

from .conftest import requires_ffmpeg


@requires_ffmpeg
def test_offline_short_renders(tmp_path, cfg):
    cfg["video"].update(width=360, height=640, fps=15)  # menor para o teste ser rápido
    state = ChannelState(tmp_path / "state.json")
    series = next(s for s in cfg["series"] if s.get("format", "narration") == "narration")
    result = make_short(cfg, state, series=series, offline=True, out_root=tmp_path, tts_provider="silent")

    assert result.video.exists()
    assert (result.folder / "script.json").exists()
    assert (result.folder / "captions.ass").read_text(encoding="utf-8").count("Dialogue: 0") > 10
    duration = probe_duration(result.video)
    assert 15 < duration < 60


@requires_ffmpeg
def test_offline_song_renders_with_karaoke(tmp_path, cfg):
    cfg["video"].update(width=360, height=640, fps=15)
    state = ChannelState(tmp_path / "state.json")
    series = next(s for s in cfg["series"] if s.get("format") == "song")
    result = make_short(cfg, state, series=series, offline=True, out_root=tmp_path)

    assert result.script.format == "song"
    ass = (result.folder / "captions.ass").read_text(encoding="utf-8")
    assert "Quá" in ass and ass.count("Dialogue: 0") > 20
    assert abs(probe_duration(result.video) - result.script.song_duration) < 0.5
    # o refrão repetido reaproveita as mesmas imagens
    assert len(list((result.folder / "images").glob("*.jpg"))) == 5
