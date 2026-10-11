import json

from googleapiclient.http import HttpMockSequence

from kidschannel.pipeline import ShortResult, upload_short
from kidschannel.script_writer import offline_script
from kidschannel.state import ChannelState
from kidschannel.youtube import YouTubeClient


def test_upload_creates_playlist_and_records_history(tmp_path, cfg):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00" * 2048)
    series = cfg["series"][0]
    result = ShortResult(folder=tmp_path, video=video, script=offline_script(cfg, series), series=series)

    http = HttpMockSequence([
        ({"status": "200", "location": "https://upload.example/session"}, ""),
        ({"status": "200"}, json.dumps({"id": "VIDEO123"})),
        ({"status": "200"}, json.dumps({"id": "PLAYLIST1"})),
        ({"status": "200"}, json.dumps({"id": "ITEM1"})),
    ])
    state = ChannelState(tmp_path / "state.json")
    video_id = upload_short(cfg, state, result, YouTubeClient(http=http))

    assert video_id == "VIDEO123"
    assert state.playlist_for(series["id"]) == "PLAYLIST1"
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["history"][-1]["url"] == "https://youtube.com/shorts/VIDEO123"
    upload = json.loads((tmp_path / "upload.json").read_text())
    assert upload["body"]["status"]["selfDeclaredMadeForKids"] is True


def test_upload_reuses_existing_playlist(tmp_path, cfg):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00" * 1024)
    series = cfg["series"][1]
    result = ShortResult(folder=tmp_path, video=video, script=offline_script(cfg, series), series=series)
    state = ChannelState(tmp_path / "state.json")
    state.set_playlist(series["id"], "EXISTING")

    http = HttpMockSequence([
        ({"status": "200", "location": "https://upload.example/session"}, ""),
        ({"status": "200"}, json.dumps({"id": "V2"})),
        ({"status": "200"}, json.dumps({"id": "ITEM"})),
    ])
    assert upload_short(cfg, state, result, YouTubeClient(http=http)) == "V2"
    assert state.playlist_for(series["id"]) == "EXISTING"
