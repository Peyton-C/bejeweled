"""Fortnite Festival as a stem source.

Epic serves each track as a BLURL (a zlib-wrapped JSON pointer to a DASH manifest),
with the audio encrypted under a key that has to be recovered from Epic's own key
database. The audio itself is a single 10-channel Opus stream: five stereo pairs, in
the order Drums, Bass, Lead, Vocals, Other.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
import zlib
from urllib.parse import urljoin

import requests
from Crypto.Cipher import AES

from .. import countin
from .. import ffmpeg as ff
from ..stems import Stem, StemSet

CATALOG_URL = (
    "https://fortnitecontent-website-prod07.ol.epicgames.com"
    "/content/api/pages/fortnite-game/spark-tracks"
)
TRACK_API = "https://cdn.qstv.on.epicgames.com/{sid}"

# The 10 channels arrive as 5.1.4, which is just a container for five stereo pairs.
# Each track's `qi.tracks` states the running order itself; this is the fallback, and
# matches every track in the catalogue at the time of writing.
CHANNEL_PAIRS = (
    ("Drums", "FL", "FR"),
    ("Bass", "FC", "LFE"),
    ("Lead", "BL", "BR"),
    ("Vocals", "SL", "SR"),
    ("Other", "TFL", "TFR"),
)

# Epic's part codes, in the order they occupy the 10 channels
PART_NAMES = {"ds": "Drums", "bs": "Bass", "gs": "Lead", "vs": "Vocals", "fs": "Other"}

# The 5.1.4 channel names, paired up in running order
LAYOUT_PAIRS = (("FL", "FR"), ("FC", "LFE"), ("BL", "BR"), ("SL", "SR"), ("TFL", "TFR"))

# Festival splits melodic content two ways; NI has one slot for it
NI_MERGE = {"Lead": "Other", "Other": "Other"}

# This source fetches the audio itself, and a Festival cut is often a different mix from
# the commercial release, so its titles are marked unless the user says otherwise
SOURCE_NAME = "festival"
TITLE_MARKER = "(FN)"
MARK_TITLES_BY_DEFAULT = True


class FestivalError(RuntimeError):
    pass


# -------------------------------------------------------------------------- catalog

def catalog(session: requests.Session | None = None) -> list[dict]:
    """Every track currently in Festival, newest last."""
    get = (session or requests).get
    data = get(CATALOG_URL, timeout=30).json()

    tracks = []
    for value in data.values():
        if not isinstance(value, dict) or "track" not in value:
            continue
        track = value["track"]
        qi = track.get("qi", "{}")
        try:
            qi = json.loads(qi) if isinstance(qi, str) else qi
        except json.JSONDecodeError:
            qi = {}
        if not qi.get("sid"):
            continue
        tracks.append({
            "sid": qi["sid"],
            "title": track.get("tt", "Unknown Title"),
            "artist": track.get("an", "Unknown Artist"),
            "year": track.get("ry"),
            "bpm": track.get("mt"),
            "duration": track.get("dn"),
            "cover_url": track.get("au", ""),
            "key": _format_key(track.get("mk"), track.get("mm")),
            "added": track.get("nu"),
            "parts": _part_order(qi),
        })
    return tracks


def _format_key(root: str | None, mode: str | None) -> str | None:
    """Render Epic's key and mode the way DJ software expects to read it.

    Mixxx and Traktor both parse "Ab" as A-flat major and "Abm" as A-flat minor, so
    minor gets the suffix and major is left bare.
    """
    if not root:
        return None
    return f"{root}m" if (mode or "").lower().startswith("min") else root


def _part_order(qi: dict) -> list[str] | None:
    """Read the stem running order out of the track's own quality info.

    Every track in the catalogue currently lists ds, bs, gs, vs, fs in that order, but
    the manifest states it per track, so trusting it beats hardcoding the order.
    """
    entries = qi.get("tracks")
    if not entries:
        return None
    names = [PART_NAMES.get(e.get("part")) for e in entries]
    return names if all(names) else None


def find(query: str, tracks: list[dict] | None = None) -> dict:
    """First track whose title or artist contains `query`, ignoring case."""
    needle = query.lower()
    for track in tracks if tracks is not None else catalog():
        if needle in track["title"].lower() or needle in track["artist"].lower():
            return track
    raise FestivalError(f"no Festival track matching {query!r}")


# ------------------------------------------------------------------------ decryption

def find_keys_file(explicit: str | None = None) -> str:
    """Locate Epic's key database, which this project does not redistribute."""
    candidates = [explicit, os.environ.get("BEJEWELED_KEYS")]
    candidates += [
        os.path.join(os.getcwd(), "keys.bin"),
        os.path.expanduser("~/.config/bejeweled/keys.bin"),
        os.path.expanduser("~/Library/Application Support/bejeweled/keys.bin"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    raise FestivalError(
        "keys.bin not found. Point BEJEWELED_KEYS at it, or drop it in "
        "~/.config/bejeweled/keys.bin"
    )


def _parse_envelope(ev_b64: str) -> tuple[str, bytes]:
    raw = base64.b64decode(ev_b64)
    if raw[0] != 1:
        raise FestivalError("unsupported BLURL envelope version")
    length = raw[2]
    nonce = raw[5:5 + length].decode()
    key = raw[5 + length:5 + length + 16]
    return nonce, key


def _lookup_key(keys_path: str, nonce: str, encrypted_key: bytes) -> bytes:
    """Scan Epic's key database for the record matching this track's nonce.

    Records are 0x34 bytes: a 4-byte tag, one checksum byte that must equal the first
    byte of md5(tag + nonce), then the AES key that unwraps the track key.
    """
    with open(keys_path, "rb") as f:
        offset = 0
        while True:
            f.seek(offset)
            head = f.read(5)
            if len(head) < 5:
                break
            digest = hashlib.md5(head[:4] + nonce.encode()).digest()
            if digest[0] == head[4]:
                f.seek(15, 1)
                wrapper = f.read(32)
                if len(wrapper) < 32:
                    break
                if len(encrypted_key) % 16:
                    raise FestivalError("track key is not a whole number of AES blocks")
                return AES.new(wrapper, AES.MODE_ECB).decrypt(encrypted_key)
            offset += 0x34
    raise FestivalError("no matching key in keys.bin - the database may be out of date")


# -------------------------------------------------------------------------- manifest

def _sanitize_mpd(xml: str) -> str:
    """Drop namespace prefixes Epic declares as empty.

    Their manifests carry xmlns:xsi="", which XML forbids - a prefix cannot be
    undeclared - so a strict parser rejects the whole document.
    """
    for prefix in set(re.findall(r'\sxmlns:([A-Za-z0-9_.-]+)=""', xml)):
        xml = re.sub(r'\sxmlns:%s=""' % re.escape(prefix), "", xml)
        xml = re.sub(r'\s%s:[A-Za-z0-9_.-]+="[^"]*"' % re.escape(prefix), "", xml)
    return xml


def _parse_mpd(xml: str, manifest_url: str) -> dict:
    """Pick the best audio rendition and say how to fetch it.

    Epic serves on-demand manifests: each Representation is one complete file named by
    its own <BaseURL>. The older numbered <SegmentTemplate> layout is still handled for
    anything that has not been re-encoded.
    """
    root = ET.fromstring(_sanitize_mpd(xml))
    ns = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}

    manifest_base = manifest_url.rsplit("/", 1)[0] + "/"
    declared = (root.findtext("mpd:BaseURL", default="", namespaces=ns) or "").strip()
    base = urljoin(manifest_base, declared) if declared else manifest_base

    candidates = []
    for adaptation in root.iterfind(".//mpd:AdaptationSet", ns):
        content_type = adaptation.get("contentType") or ""
        set_mime = adaptation.get("mimeType") or ""
        for rep in adaptation.iterfind("mpd:Representation", ns):
            mime = rep.get("mimeType") or set_mime
            if content_type != "audio" and not mime.startswith("audio"):
                continue

            config = rep.find("mpd:AudioChannelConfiguration", ns)
            try:
                channels = int(config.get("value")) if config is not None else 0
            except (TypeError, ValueError):
                channels = 0
            try:
                bandwidth = int(rep.get("bandwidth", "0"))
            except ValueError:
                bandwidth = 0

            rep_base = (rep.findtext("mpd:BaseURL", default="", namespaces=ns) or "").strip()
            if rep_base:
                candidates.append({
                    "mode": "single", "url": urljoin(base, rep_base),
                    "channels": channels, "bandwidth": bandwidth,
                })
                continue

            template = rep.find("mpd:SegmentTemplate", ns)
            if template is not None:
                rep_id = rep.get("id") or ""
                candidates.append({
                    "mode": "segments", "base_url": base,
                    "init": (template.get("initialization") or "").replace("$RepresentationID$", rep_id),
                    "media": (template.get("media") or "").replace("$RepresentationID$", rep_id),
                    "start_number": int(template.get("startNumber", "1")),
                    "channels": channels, "bandwidth": bandwidth,
                })

    if not candidates:
        raise FestivalError("no audio rendition found in manifest")

    # All stems live in the 10-channel rendition; among those take the best bitrate
    return max(candidates, key=lambda c: (c["channels"], c["bandwidth"]))


# -------------------------------------------------------------------------- download

def _download(url: str, out_path: str, progress=None, session=None) -> str:
    get = (session or requests).get
    with get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    return out_path


def _download_segments(plan: dict, out_path: str, progress=None, session=None) -> str:
    """Concatenate a numbered segment list, stopping at the first gap."""
    get = (session or requests).get
    base, media = plan["base_url"], plan["media"]
    with open(out_path, "wb") as out:
        if plan.get("init"):
            out.write(get(base + plan["init"], timeout=30).content)
        number = plan["start_number"]
        while True:
            r = get(base + media.replace("$Number$", str(number)), timeout=30)
            if r.status_code != 200:
                break
            out.write(r.content)
            number += 1
            if progress:
                progress(number - plan["start_number"], 0)
    return out_path


def fetch_master(track: dict, work_dir: str, keys_path: str | None = None,
                 progress=None, ffmpeg: str | None = None, session=None) -> str:
    """Download and decrypt one track, returning the 10-channel audio file."""
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    get = (session or requests).get
    os.makedirs(work_dir, exist_ok=True)

    data = get(TRACK_API.format(sid=track["sid"]), timeout=30).json()
    blurl_url = None
    for value in data.values():
        if isinstance(value, dict) and value.get("baseUrls"):
            parts = value["baseUrls"][0].rstrip("/").split("/")
            if len(parts) >= 4:
                blurl_url = f"{parts[0]}//{parts[2]}/{parts[3]}/master.blurl"
                break
    if not blurl_url:
        raise FestivalError(f"no playable URL for {track['title']!r}")

    raw = get(blurl_url, timeout=60).content
    blurl = json.loads(zlib.decompress(raw[8:]))

    key_hex = None
    if blurl.get("ev"):
        nonce, wrapped = _parse_envelope(blurl["ev"])
        key_hex = _lookup_key(find_keys_file(keys_path), nonce, wrapped).hex()

    manifest_url = blurl["playlists"][0]["url"]
    plan = _parse_mpd(get(manifest_url, timeout=30).text, manifest_url)

    encrypted = os.path.join(work_dir, "track.mp4")
    if plan["mode"] == "single":
        _download(plan["url"], encrypted, progress, session)
    else:
        _download_segments(plan, encrypted, progress, session)

    master = os.path.join(work_dir, "master_audio.mp4")
    if key_hex:
        ff.run([ffmpeg, "-y", "-decryption_key", key_hex, "-i", encrypted, "-c", "copy", master])
        os.remove(encrypted)
    else:
        os.replace(encrypted, master)
    return master


def split_stems(master_audio: str, out_dir: str, fmt: str = "wav",
                parts: list[str] | None = None, trim_start: float = 0.0,
                max_duration: float | None = None, ffmpeg: str | None = None,
                mute: dict[str, float] | None = None) -> list[Stem]:
    """Split the 10-channel master into five stereo stems.

    `parts` names the stems in channel order, as the track's manifest declares them;
    without it the catalogue-wide default order is assumed. `mute` silences the opening
    of a named stem, measured from the trimmed start, which is how count-in clicks that
    outlast the cut are removed.
    """
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    os.makedirs(out_dir, exist_ok=True)

    if parts and len(parts) == len(LAYOUT_PAIRS):
        pairs = [(name, left, right) for name, (left, right) in zip(parts, LAYOUT_PAIRS)]
    else:
        pairs = list(CHANNEL_PAIRS)

    # Trimming ahead of the split keeps every stem cut at the same sample, so they
    # stay in sync, and atrim is sample-accurate where a seek would not be
    prefix = "[0:a]"
    if trim_start > 0:
        prefix = f"[0:a]atrim=start={trim_start:.6f},asetpts=PTS-STARTPTS,"

    split = prefix + "channelsplit=channel_layout=5.1.4" + "".join(
        f"[{left}][{right}]" for left, right in LAYOUT_PAIRS
    )
    # join builds a real stereo layout from the two channels. amerge would instead
    # label its output with the source channel names - "SL+SR" for the Vocals pair -
    # and the resampler rejects that as an input layout, so nothing downstream can
    # read the stems back in. Relabelling after the fact does not help; the layout has
    # to be correct at the point the pair is assembled.
    chains = []
    for name, left, right in pairs:
        chain = f"[{left}][{right}]join=inputs=2:channel_layout=stereo"
        silence = (mute or {}).get(name, 0.0)
        if silence > 0:
            chain += f",volume=0:enable='lt(t,{silence:.6f})'"
        chains.append(f"{chain}[{name.lower()}]")
    merges = ";".join(chains)

    cmd = [ffmpeg, "-y"]
    if max_duration:
        # Only the opening is needed to find the count-in, so decode just that
        cmd += ["-t", f"{max_duration:.4f}"]
    cmd += ["-i", master_audio, "-filter_complex", f"{split};{merges}"]
    planned = []
    for name, _, _ in pairs:
        path = os.path.join(out_dir, f"{name}.{fmt}")
        extra = ["-b:a", "320k"] if fmt == "mp3" else []
        cmd += ["-map", f"[{name.lower()}]", *extra, path]
        planned.append((name, path))
    ff.run(cmd)

    # Stem validates that the file exists, so build them only once FFmpeg has written
    return [Stem(name=name, path=path) for name, path in planned]


def _detect_countin(master_audio: str, track: dict, ffmpeg: str | None):
    """Split just the opening bars to a scratch directory and look for the count-in."""
    import tempfile

    window = countin.search_window(track["bpm"])
    with tempfile.TemporaryDirectory(prefix="bejeweled-countin-") as scratch:
        preview = split_stems(master_audio, scratch, "wav", track.get("parts"),
                              0.0, window, ffmpeg)
        return countin.detect({s.name: s.path for s in preview}, track["bpm"], ffmpeg)


def fetch_cover(track: dict, out_dir: str, session=None) -> str | None:
    """Download the track's cover art, if it has any."""
    url = track.get("cover_url")
    if not url:
        return None
    try:
        r = (session or requests).get(url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"cover art unavailable: {e}")
        return None
    path = os.path.join(out_dir, "cover.jpg")
    with open(path, "wb") as f:
        f.write(r.content)
    return path


def rip(track: dict, out_dir: str, keys_path: str | None = None, fmt: str = "wav",
        progress=None, ffmpeg: str | None = None, session=None,
        title_suffix: str | None = None, cover: bool = True,
        trim_countin: bool = True) -> StemSet:
    """Download a Festival track and split it into a StemSet.

    `title_suffix` is appended to the title, which is how a Festival cut is told apart
    from another mix of the same song in a library.
    """
    os.makedirs(out_dir, exist_ok=True)
    master_audio = fetch_master(track, out_dir, keys_path, progress, ffmpeg, session)

    detected = None
    if trim_countin and track.get("bpm"):
        detected = _detect_countin(master_audio, track, ffmpeg)

    mute = {countin.CLICK_STEM: detected.muted} if detected else None
    stems = split_stems(master_audio, out_dir, fmt, track.get("parts"),
                        detected.trim_at if detected else 0.0, None, ffmpeg, mute)
    os.remove(master_audio)

    title = track["title"]
    if title_suffix:
        title = f"{title} {title_suffix}"

    return StemSet(
        title=title,
        artist=track["artist"],
        year=str(track.get("year") or "") or None,
        bpm=track.get("bpm"),
        key=track.get("key"),
        cover=fetch_cover(track, out_dir, session) if cover else None,
        stems=stems,
        source="Fortnite Festival",
        extra={
            "festival_sid": track["sid"],
            "festival_title": track["title"],
            "countin": detected,
        },
    )
