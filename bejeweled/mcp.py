"""bejeweled as a Model Context Protocol server, so an agent can fetch and make stems.

The protocol is spoken by hand, newline-delimited JSON-RPC over stdin and stdout,
rather than through the MCP SDK: three tools need a few dozen lines of it, and the SDK
would be bejeweled's largest dependency by far.

MCP has no way to send a file to a server. A tool call carries JSON, so audio would
have to travel as base64 inside it, which no client does for a song. A stdio server
runs on the agent's own machine, so files go in and come out as paths instead.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

from . import __version__, config, jobs
from .sources.separate import LAYOUTS, SEPARATORS

# Newest first. Tools and progress read the same in all of them, so the client gets
# whichever of these it asked for.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

DEFAULT_OUT = "~/Music/bejeweled"

# Festival adds tracks weekly at most, and an agent searches many times a session
CATALOG_SECONDS = 600

INSTRUCTIONS = (
    "bejeweled gets songs as Native Instruments stem files (.stem.mp4: drums, bass, "
    "other, vocals and the mixdown). Files are exchanged as paths on this machine. "
    "search_festival finds songs in Fortnite Festival's catalogue, whose stems are the "
    "real multitrack, and download_festival fetches one. separate_song splits a local "
    "audio file into stems with a separation model, which takes minutes and is less "
    "clean than real stems. Titles end in a marker for where the stems came from: (FN) "
    "Festival, (RF), (HY) or (DE) a separation."
)

TOOLS = [
    {
        "name": "search_festival",
        "description": "Search Fortnite Festival's catalogue. Returns the total that match "
                       "and up to `limit` of them: id, title, artist, year, BPM, key (Ab is "
                       "major, Abm minor) and length in seconds. Epic gives a genre for "
                       "few tracks, so most have none, here and in the stem file. A track "
                       "already downloaded also has the `path` of its stem file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Words that must all appear in the title or artist. "
                                         "Leave out to list everything"},
                "bpm_min": {"type": "number"},
                "bpm_max": {"type": "number"},
                "key": {"type": "string", "description": "Exact key, such as Ab or F#m"},
                "limit": {"type": "integer", "description": "Default 25"},
                "offset": {"type": "integer", "description": "Skip this many, for the next page"},
            },
        },
    },
    {
        "name": "download_festival",
        "description": "Download a Festival track and write it as a stem file. Returns its "
                       "path, title, artist, BPM and key. Takes several seconds. A track "
                       "already downloaded is returned as it is, unless `overwrite` is set. "
                       "The metronome count-in Festival puts on every track is removed "
                       "where it can be found; `count_in` says whether it was.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "A track id from search_festival"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "separate_song",
        "description": "Split a local audio file into stems with a separation model and "
                       "write a stem file. Returns its path. Takes from half a minute to "
                       "several minutes, and one runs at a time. A song already separated "
                       "the same way is returned as it is, unless `overwrite` is set. "
                       "Stereo, 5.1 and multichannel Dolby Atmos renders are accepted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The audio file, as a full path"},
                "separator": {"type": "string", "enum": list(SEPARATORS),
                              "description": "roformer is the cleanest, demucs several times "
                                             "faster. Default: the configured one, or roformer"},
                "layout": {"type": "string", "enum": list(LAYOUTS),
                           "description": "Channel layout of a multichannel file, when the "
                                          "error says it cannot be told"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["path"],
        },
    },
]


class Server:
    def __init__(self, out_dir: str | None, reader, writer):
        self.out_dir = os.path.abspath(os.path.expanduser(out_dir or DEFAULT_OUT))
        self.reader, self.writer = reader, writer
        self._writing = threading.Lock()
        self._catalog_lock = threading.Lock()
        self._tracks: list[dict] = []
        self._fetched = 0.0
        # A client that gives up waiting calls again, and the second call must wait for
        # the first one's file rather than write the same file beside it
        self._busy: dict[str, threading.Lock] = {}
        self._busy_lock = threading.Lock()
        self._artists: dict[tuple, str | None] = {}
        # Two separations at once is how the machine runs out of memory
        self._separating = threading.Lock()
        self._cancelled: set = set()
        self._calls: list[threading.Thread] = []

    # ------------------------------------------------------------------ protocol

    def run(self) -> int:
        for line in self.reader:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self._send({"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700, "message": "parse error"}})
                continue
            for one in message if isinstance(message, list) else [message]:
                if isinstance(one, dict):
                    self._handle(one)
        # The client has gone, but a file half written is worth finishing
        for call in self._calls:
            call.join()
        return 0

    def _send(self, message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._writing:
            self.writer.write(data)
            self.writer.flush()

    def _handle(self, message: dict) -> None:
        method, id_ = message.get("method"), message.get("id")
        params = message.get("params") or {}

        if method == "notifications/cancelled":
            self._cancelled.add(params.get("requestId"))
        if id_ is None or method is None:
            return  # a notification, or a response to something never asked

        if method == "initialize":
            asked = params.get("protocolVersion")
            result = {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bejeweled", "version": __version__},
                "instructions": f"{INSTRUCTIONS} Stem files are written to {self.out_dir}.",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            # On its own thread, so a ping is still answered during a separation
            call = threading.Thread(target=self._call, args=(id_, params))
            self._calls = [c for c in self._calls if c.is_alive()] + [call]
            call.start()
            return
        else:
            self._send({"jsonrpc": "2.0", "id": id_,
                        "error": {"code": -32601, "message": f"unknown method {method}"}})
            return
        self._send({"jsonrpc": "2.0", "id": id_, "result": result})

    def _call(self, id_, params: dict) -> None:
        tool = {"search_festival": self._search, "download_festival": self._download,
                "separate_song": self._separate}.get(params.get("name"))
        if tool is None:
            self._send({"jsonrpc": "2.0", "id": id_,
                        "error": {"code": -32602, "message": f"unknown tool {params.get('name')}"}})
            return
        token = (params.get("_meta") or {}).get("progressToken")
        try:
            result = tool(params.get("arguments") or {}, token)
            text, failed = json.dumps(result, ensure_ascii=False), False
        except Exception as e:  # the agent can act on any of them, so none is fatal
            text, failed = str(e) or type(e).__name__, True
        if id_ in self._cancelled:
            self._cancelled.discard(id_)
            return
        self._send({"jsonrpc": "2.0", "id": id_,
                    "result": {"content": [{"type": "text", "text": text}], "isError": failed}})

    def _progress(self, token, default: str):
        """A progress callback for the sources, reporting each new percent to the client.
        `default` names the stage a source reports without naming it.

        A job has several stages that each count to 100, a model download then the
        separation, while MCP wants one number that only rises, so each stage starts
        where the last one stopped and the stage is named in the message.
        """
        if token is None:
            return None
        state = {"stage": None, "base": 0, "percent": -1}

        def progress(done, total, stage=default):
            if not total:
                return
            percent = min(100, done * 100 // total)
            if stage != state["stage"]:
                if state["stage"] is not None:
                    state["base"] += 100
                state["stage"], state["percent"] = stage, -1
            if percent <= state["percent"]:
                return
            state["percent"] = percent
            self._send({"jsonrpc": "2.0", "method": "notifications/progress",
                        "params": {"progressToken": token, "progress": state["base"] + percent,
                                   "message": f"{stage} {percent}%"}})
        return progress

    # --------------------------------------------------------------------- tools

    def _catalog(self) -> list[dict]:
        from .sources import festival

        with self._catalog_lock:
            if not self._tracks or time.monotonic() - self._fetched > CATALOG_SECONDS:
                self._tracks = festival.catalog()
                self._fetched = time.monotonic()
            return self._tracks

    def _search(self, args: dict, token) -> dict:
        cfg = config.load()
        words = str(args.get("query") or "").lower().split()
        key = str(args.get("key") or "").lower()
        low, high = args.get("bpm_min"), args.get("bpm_max")

        found = []
        for track in self._catalog():
            text = f"{track['artist']} {track['title']}".lower()
            if not all(word in text for word in words):
                continue
            if key and (track.get("key") or "").lower() != key:
                continue
            if low is not None or high is not None:
                bpm = track.get("bpm")
                if not bpm or (low is not None and bpm < low) or (high is not None and bpm > high):
                    continue
            found.append(track)

        offset = max(0, int(args.get("offset") or 0))
        limit = max(1, int(args.get("limit") or 25))
        return {"total": len(found),
                "tracks": [self._describe(t, cfg) for t in found[offset:offset + limit]]}

    def _describe(self, track: dict, cfg: dict) -> dict:
        entry = {"id": track["sid"], "title": track["title"], "artist": track["artist"],
                 "year": track.get("year"), "bpm": track.get("bpm"), "key": track.get("key"),
                 "genre": track.get("genre"), "seconds": track.get("duration")}
        path = jobs.festival_output(track, self.out_dir, cfg)
        if self._artist_of(path) == track["artist"]:
            entry["path"] = path
        return {k: v for k, v in entry.items() if v is not None}

    def _lock(self, path: str) -> threading.Lock:
        with self._busy_lock:
            return self._busy.setdefault(path, threading.Lock())

    def _artist_of(self, path: str) -> str | None:
        """The artist tagged in a stem file, or None when it is missing or unreadable.

        bejeweled names a stem file by its title alone and Festival has several titles
        twice over, Closer by The Chainsmokers and by Nine Inch Nails among them, so a
        file with the right name is not yet the right song.
        """
        from . import ffmpeg as ff
        from .sources import separate

        try:
            stamp = (path, os.path.getmtime(path))
        except OSError:
            return None
        if stamp not in self._artists:
            try:
                tags = separate.read_tags(ff.probe(ff.find_ffmpeg(), path))
            except Exception:
                tags = {}
            self._artists[stamp] = tags.get("artist")
        return self._artists[stamp]

    def _download(self, args: dict, token) -> dict:
        track = next((t for t in self._catalog() if t["sid"] == args.get("id")), None)
        if track is None:
            raise ValueError(f"no Festival track with id {args.get('id')!r}, "
                             f"find one with search_festival")
        cfg = config.load()
        path = jobs.festival_output(track, self.out_dir, cfg)
        result = self._describe(track, cfg)
        with self._lock(path):
            other = self._artist_of(path)
            if other == track["artist"] and not args.get("overwrite"):
                return {**result, "path": path, "already_downloaded": True}
            if other and other != track["artist"] and not args.get("overwrite"):
                raise ValueError(
                    f"{path} is {track['title']} by {other}, and bejeweled names a stem "
                    f"file by its title, so this one would replace it. Move that file, "
                    f"or pass overwrite to replace it")
            written = jobs.rip_festival(track, self.out_dir, cfg, fmt="ni-stem",
                                        progress=self._progress(token, "downloading"))
        result.update(path=written.path,
                      count_in="removed" if written.stem_set.extra.get("countin") else "left in")
        return result

    def _separate(self, args: dict, token) -> dict:
        source = os.path.abspath(os.path.expanduser(str(args.get("path") or "")))
        if source.lower().endswith(".stem.mp4"):
            raise ValueError(f"{os.path.basename(source)} is already a stem file")
        cfg = config.load()
        separator, layout = args.get("separator"), args.get("layout")
        path = jobs.separated_output(source, self.out_dir, cfg, separator, layout)
        with self._separating:
            if os.path.exists(path) and not args.get("overwrite"):
                return {"path": path, "already_separated": True}
            written = jobs.separate_file(source, self.out_dir, cfg, separator=separator,
                                         layout=layout, fmt="ni-stem",
                                         progress=self._progress(token, "separating"))
        stem_set = written.stem_set
        result = {"path": written.path, "title": stem_set.title, "artist": stem_set.artist,
                  "bpm": stem_set.bpm, "key": stem_set.key, "genre": stem_set.genre,
                  "separator": stem_set.extra["separator"], "read_as": stem_set.extra["layout"]}
        return {k: v for k, v in result.items() if v is not None}


def serve(out_dir: str | None = None) -> int:
    """Serve on stdin and stdout until the client closes stdin."""
    writer = sys.stdout.buffer
    # stdout carries the protocol, so anything a source prints goes to stderr instead
    sys.stdout = sys.stderr
    try:
        return Server(out_dir, sys.stdin.buffer, writer).run()
    finally:
        sys.stdout = sys.__stdout__
