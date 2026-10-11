"""The MCP server's protocol and tools. No network, no ripping, no separating."""
import io
import json

import pytest

from bejeweled import jobs, mcp
from bejeweled.sources import festival
from bejeweled.stems import StemSet

TRACKS = [
    {"sid": "a1", "title": "Kill Bill", "artist": "SZA", "year": 2022, "bpm": 89,
     "key": "Abm", "duration": 153, "cover_url": "", "parts": None},
    {"sid": "b2", "title": "HOT TO GO!", "artist": "Chappell Roan", "year": 2023, "bpm": 115,
     "key": "G", "duration": 184, "cover_url": "", "parts": None},
]


@pytest.fixture
def talk(tmp_path, monkeypatch):
    """Send requests to a fresh server and get back everything it wrote."""
    monkeypatch.setenv("BEJEWELED_CONFIG", str(tmp_path / "none.toml"))
    monkeypatch.setattr(festival, "catalog", lambda *a, **k: TRACKS)

    def talk(*requests):
        lines = "".join(json.dumps({"jsonrpc": "2.0", **r}) + "\n" for r in requests)
        writer = io.BytesIO()
        mcp.Server(str(tmp_path), io.BytesIO(lines.encode()), writer).run()
        return [json.loads(line) for line in writer.getvalue().decode().splitlines()]
    return talk


def call(name, arguments=None, id_=1, **meta):
    params = {"name": name, "arguments": arguments or {}}
    if meta:
        params["_meta"] = meta
    return {"id": id_, "method": "tools/call", "params": params}


def result(messages, id_=1):
    """A tool's JSON result, and whether it was an error."""
    reply = next(m for m in messages if m.get("id") == id_)["result"]
    text = reply["content"][0]["text"]
    return (text if reply["isError"] else json.loads(text)), reply["isError"]


def test_initialize_answers_in_the_version_asked_for(talk):
    reply, = talk({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["serverInfo"]["name"] == "bejeweled"
    assert "tools" in reply["result"]["capabilities"]


def test_an_unknown_version_gets_the_newest_one_known(talk):
    reply, = talk({"id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert reply["result"]["protocolVersion"] == mcp.PROTOCOL_VERSIONS[0]


def test_notifications_get_no_reply_and_unknown_methods_an_error(talk):
    replies = talk({"method": "notifications/initialized"}, {"id": 7, "method": "ping"},
                   {"id": 8, "method": "resources/list"})
    assert replies[0] == {"jsonrpc": "2.0", "id": 7, "result": {}}
    assert replies[1]["error"]["code"] == -32601
    assert len(replies) == 2


def test_tools_are_listed_with_schemas(talk):
    reply, = talk({"id": 1, "method": "tools/list"})
    tools = {t["name"]: t for t in reply["result"]["tools"]}
    assert set(tools) == {"search_festival", "download_festival", "separate_song"}
    assert tools["download_festival"]["inputSchema"]["required"] == ["id"]


def test_search_matches_every_word_in_title_or_artist(talk):
    found, failed = result(talk(call("search_festival", {"query": "roan hot"})))
    assert not failed
    assert found["total"] == 1
    assert found["tracks"][0] == {"id": "b2", "title": "HOT TO GO!", "artist": "Chappell Roan",
                                  "year": 2023, "bpm": 115, "key": "G", "seconds": 184}


def test_search_filters_by_bpm_and_key_and_pages(talk):
    assert result(talk(call("search_festival", {"bpm_min": 100})))[0]["total"] == 1
    assert result(talk(call("search_festival", {"key": "abm"})))[0]["tracks"][0]["id"] == "a1"
    page, _ = result(talk(call("search_festival", {"limit": 1, "offset": 1})))
    assert page["total"] == 2
    assert [t["id"] for t in page["tracks"]] == ["b2"]


def test_a_downloaded_track_is_found_and_not_fetched_again(talk, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "rip_festival", lambda *a, **k: pytest.fail("ripped again"))
    stem_file = tmp_path / "Kill Bill (FN).stem.mp4"
    stem_file.write_bytes(b"")
    monkeypatch.setattr(mcp.Server, "_artist_of", lambda self, path: "SZA")

    found, _ = result(talk(call("search_festival", {"query": "kill"})))
    assert found["tracks"][0]["path"] == str(stem_file)

    got, failed = result(talk(call("download_festival", {"id": "a1"})))
    assert not failed
    assert got["path"] == str(stem_file)
    assert got["already_downloaded"] is True


def test_download_writes_into_the_servers_folder_and_reports_progress(talk, tmp_path, monkeypatch):
    def rip(track, out_dir, cfg, fmt, progress):
        assert (out_dir, fmt) == (str(tmp_path), "ni-stem")
        for done in (10, 10, 50, 100):
            progress(done, 100)
        return jobs.Written(str(tmp_path / "x.stem.mp4"),
                            StemSet(title="Kill Bill (FN)", stems=[], extra={"countin": None}))
    monkeypatch.setattr(jobs, "rip_festival", rip)

    messages = talk(call("download_festival", {"id": "a1"}, progressToken="tok"))
    got, failed = result(messages)
    assert not failed
    assert got["path"] == str(tmp_path / "x.stem.mp4")
    assert got["count_in"] == "left in"
    progress = [m["params"] for m in messages if m.get("method") == "notifications/progress"]
    assert [p["progress"] for p in progress] == [10, 50, 100]
    assert progress[0] == {"progressToken": "tok", "progress": 10, "message": "downloading 10%"}


def test_progress_keeps_rising_across_stages(talk, tmp_path, monkeypatch):
    def separate(path, out_dir, cfg, progress, **options):
        progress(100, 100, "downloading model")
        progress(40, 100)
        return jobs.Written("x", StemSet(title="t", stems=[],
                                         extra={"separator": "roformer", "layout": "stereo"}))
    monkeypatch.setattr(jobs, "separate_file", separate)
    monkeypatch.setattr(jobs, "separated_output", lambda *a, **k: str(tmp_path / "t.stem.mp4"))

    messages = talk(call("separate_song", {"path": "/music/t.flac"}, progressToken=3))
    progress = [m["params"] for m in messages if m.get("method") == "notifications/progress"]
    assert [(p["progress"], p["message"]) for p in progress] == [
        (100, "downloading model 100%"), (140, "separating 40%")]
    assert result(messages)[0]["read_as"] == "stereo"


def test_failures_come_back_as_tool_errors_the_agent_can_read(talk):
    text, failed = result(talk(call("download_festival", {"id": "nope"})))
    assert failed and "search_festival" in text

    text, failed = result(talk(call("separate_song", {"path": "/music/a (FN).stem.mp4"})))
    assert failed and "already a stem file" in text

    text, failed = result(talk(call("separate_song", {"path": "/nowhere/song.flac"})))
    assert failed and "no such file" in text

    reply, = talk(call("rip_everything"))
    assert reply["error"]["code"] == -32602


def test_a_cancelled_call_sends_no_result(talk, monkeypatch):
    # Cancelled before it is called: the server reads the notification first
    replies = talk({"method": "notifications/cancelled", "params": {"requestId": 5}},
                   call("search_festival", id_=5), {"id": 6, "method": "ping"})
    assert [r["id"] for r in replies] == [6]


def test_a_same_titled_song_by_someone_else_is_not_mistaken_for_this_one(talk, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "rip_festival", lambda *a, **k: pytest.fail("replaced it"))
    monkeypatch.setattr(mcp.Server, "_artist_of", lambda self, path: "Nancy Sinatra")
    (tmp_path / "Kill Bill (FN).stem.mp4").write_bytes(b"")

    found, _ = result(talk(call("search_festival", {"query": "kill"})))
    assert "path" not in found["tracks"][0]
    text, failed = result(talk(call("download_festival", {"id": "a1"})))
    assert failed and "Nancy Sinatra" in text
