"""Read and write Native Instruments stem files (.stem.mp4).

A stem file is an MP4 carrying five stereo audio tracks - track 0 is the mixdown that
ordinary players fall back to, tracks 1-4 are the stems - plus a JSON atom at
moov/udta/stem naming and colouring them. Only track 0 is flagged default, which is
what stops normal players from playing all five at once.

Mixxx 2.6 reads this format but relaxes NI's codec rules: any codec is accepted as
long as every track shares it, the sample rate matches, and each track is stereo.
"""
from __future__ import annotations

import json
import os
import struct
import tempfile

from .. import ffmpeg as ff
from .. import palette as pal
from ..stems import NI_SLOTS, StemSet

# Required by the spec, but every value can stay inert
NEUTRAL_DSP = {
    "compressor": {
        "enabled": False, "ratio": 10, "output_gain": 0, "release": 1.0,
        "attack": 0.0001, "input_gain": 0, "threshold": 0, "hp_cutoff": 20,
        "dry_wet": 100,
    },
    "limiter": {"enabled": False, "release": 1.0, "threshold": 0, "ceiling": 0},
}

_CONTAINERS = {b"moov", b"udta"}

CODEC_ARGS = {
    "aac": ["-c:a", "aac", "-b:a", "256k"],
    "alac": ["-c:a", "alac"],
    "flac": ["-c:a", "flac"],
    "opus": ["-c:a", "libopus", "-b:a", "256k"],
    "wav": ["-c:a", "pcm_s16le"],
}


# ---------------------------------------------------------------------------- read

def read_metadata(path: str) -> dict | None:
    """Return the parsed moov/udta/stem JSON, or None if there is no stem atom."""
    raw = _find_atom(path, b"stem")
    return json.loads(raw.decode("utf-8")) if raw else None


def _find_atom(path: str, wanted: bytes) -> bytes | None:
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(0)
        return _scan(f, size, wanted)


def _scan(f, end: int, wanted: bytes) -> bytes | None:
    while f.tell() < end:
        start = f.tell()
        header = f.read(8)
        if len(header) < 8:
            return None
        box_size, kind = struct.unpack(">I4s", header)
        if box_size == 1:
            box_size = struct.unpack(">Q", f.read(8))[0]
        elif box_size == 0:
            box_size = end - start
        if box_size < 8:
            return None
        if kind == wanted:
            f.seek(start + 8)
            return f.read(box_size - 8)
        if kind in _CONTAINERS:
            f.seek(start + 8)
            found = _scan(f, start + box_size, wanted)
            if found is not None:
                return found
        f.seek(start + box_size)
    return None


# --------------------------------------------------------------------------- write

def write(stem_set: StemSet, out_path: str, codec: str = "aac", sample_rate: int = 44100,
          merge: dict[str, str] | None = None, colors=None, ffmpeg: str | None = None) -> str:
    """Render a StemSet to a .stem.mp4.

    The set is folded to NI's four slots first; anything that maps onto the same slot
    is summed. A mixdown is synthesised from the stems when the set has none.

    `colors` accepts a palette name, four hex colours, or a slot->colour mapping. Mixxx
    reads these out of the file, so they are what actually appears on screen.
    """
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    palette = pal.resolve(colors)
    if codec not in CODEC_ARGS:
        raise ValueError(f"unknown codec {codec!r}, expected one of {', '.join(CODEC_ARGS)}")

    folded = stem_set.to_ni_slots(merge)
    groups = folded.extra["ni_groups"]

    with tempfile.TemporaryDirectory(prefix="bejeweled-") as work:
        slot_paths = []
        for slot in NI_SLOTS:
            members = groups[slot]
            if len(members) == 1:
                slot_paths.append(members[0].path)
            else:
                merged = os.path.join(work, f"{slot.lower()}.wav")
                ff.mix(ffmpeg, [m.path for m in members], merged)
                slot_paths.append(merged)

        master = stem_set.master
        if not master:
            master = ff.mix(ffmpeg, slot_paths, os.path.join(work, "master.wav"))

        tags = stem_set.tags()
        # FFmpeg drops these two for MP4, so they are written as atoms afterwards
        bpm = tags.pop("BPM", None)
        key = tags.pop("initial_key", None)
        _mux(ffmpeg, master, slot_paths, out_path, codec, sample_rate,
             cover=stem_set.cover, tags=tags)

    if bpm or key:
        inject_itunes_tags(
            out_path,
            freeform={"initialkey": key, "BPM": bpm},
            bpm=int(float(bpm)) if bpm else None,
        )

    inject_metadata(out_path, {
        "stems": [{"color": c, "name": n} for n, c in zip(NI_SLOTS, palette.colors)],
        "mastering_dsp": NEUTRAL_DSP,
        "version": 1,
    })
    return out_path


def recolor(path: str, colors) -> str:
    """Change the colours of an existing stem file without touching its audio.

    Only the metadata atom is rewritten, so this is instant regardless of file size
    and lossless by construction - useful for retuning a whole library to a skin.
    """
    palette = pal.resolve(colors)
    metadata = read_metadata(path)
    if metadata is None:
        raise RuntimeError(f"{path}: not a stem file (no stem atom)")

    for entry, color in zip(metadata.get("stems", []), palette.colors):
        entry["color"] = color
    inject_metadata(path, metadata)
    return path


def _mux(ffmpeg, master, stems, out_path, codec, sample_rate, cover=None, tags=None):
    inputs = [master, *stems]
    cmd = [ffmpeg, "-y"]
    for path in inputs:
        cmd += ["-i", path]
    if cover:
        cmd += ["-i", cover]

    for i in range(len(inputs)):
        cmd += ["-map", f"{i}:a:0"]
    if cover:
        cmd += ["-map", f"{len(inputs)}:v:0", "-c:v", "mjpeg",
                "-disposition:v:0", "attached_pic"]

    cmd += [*CODEC_ARGS[codec], "-ar", str(sample_rate), "-ac", "2"]

    # Only the mixdown is default, so ordinary players do not stack all five tracks
    cmd += ["-disposition:a:0", "default"]
    for i in range(1, len(inputs)):
        cmd += [f"-disposition:a:{i}", "0"]

    for key, value in (tags or {}).items():
        cmd += ["-metadata", f"{key}={value}"]

    # moov must trail mdat so the stem atom can be injected without moving samples
    cmd += ["-movflags", "-faststart", out_path]
    ff.run(cmd)


def inject_metadata(path: str, metadata: dict) -> None:
    """Insert the stem JSON into moov/udta, growing only the trailing moov box.

    FFmpeg writes moov after mdat, so the inserted bytes land past all the media data
    and every sample offset in stco stays valid - no offset rewriting needed.
    """
    payload = json.dumps(metadata).encode("utf-8")
    stem_box = struct.pack(">I4s", 8 + len(payload), b"stem") + payload

    with open(path, "rb") as f:
        data = f.read()

    moov_start, moov_size = _top_level(data, b"moov")
    if moov_start is None:
        raise RuntimeError(f"{path}: no moov box")
    mdat_start, _ = _top_level(data, b"mdat")
    if mdat_start is not None and mdat_start > moov_start:
        raise RuntimeError(
            f"{path}: moov precedes mdat, so injecting would invalidate sample offsets "
            "- remux without +faststart"
        )

    udta = _child(data, moov_start + 8, moov_start + moov_size, b"udta")
    if udta is None:
        udta_box = struct.pack(">I4s", 8 + len(stem_box), b"udta") + stem_box
        at = moov_start + moov_size
        data = data[:at] + udta_box + data[at:]
        data = _set_size(data, moov_start, moov_size + len(udta_box))
    else:
        udta_start, udta_size = udta
        # Drop any stem atom already present, so rewriting a file stays idempotent
        existing = _child(data, udta_start + 8, udta_start + udta_size, b"stem")
        if existing:
            ex_start, ex_size = existing
            data = data[:ex_start] + data[ex_start + ex_size:]
            udta_size -= ex_size
            moov_size -= ex_size
        at = udta_start + 8
        data = data[:at] + stem_box + data[at:]
        data = _set_size(data, udta_start, udta_size + len(stem_box))
        data = _set_size(data, moov_start, moov_size + len(stem_box))

    with open(path, "wb") as f:
        f.write(data)


# ------------------------------------------------------------------- iTunes metadata

# `data` box payload types, from the QuickTime metadata spec
_TYPE_UTF8 = 1
_TYPE_INT = 21

_ITUNES_MEAN = b"com.apple.iTunes"


def inject_itunes_tags(path: str, freeform: dict[str, str] | None = None,
                       bpm: int | None = None) -> None:
    """Write tags into moov/udta/meta/ilst that FFmpeg will not write itself.

    FFmpeg's MP4 muxer silently drops BPM and musical key, which are the two tags DJ
    software most wants. Both go in here directly: BPM as the standard `tmpo` atom, and
    anything else as an iTunes freeform atom, which is where TagLib - and so Mixxx and
    Traktor - looks for `initialkey`.
    """
    boxes = b""
    if bpm:
        boxes += _box(b"tmpo", _data_box(struct.pack(">H", int(bpm)), _TYPE_INT))
    for name, value in (freeform or {}).items():
        if value:
            boxes += _freeform_box(name, str(value))
    if not boxes:
        return

    with open(path, "rb") as f:
        data = f.read()

    moov_start, moov_size = _top_level(data, b"moov")
    if moov_start is None:
        raise RuntimeError(f"{path}: no moov box")

    udta = _child(data, moov_start + 8, moov_start + moov_size, b"udta")
    if udta is None:
        raise RuntimeError(f"{path}: no udta box to hold metadata")
    udta_start, udta_size = udta

    meta = _child(data, udta_start + 8, udta_start + udta_size, b"meta")
    if meta is None:
        raise RuntimeError(f"{path}: no meta box - write standard tags first")
    meta_start, meta_size = meta

    # meta carries a 4-byte version/flags field ahead of its children
    ilst = _child(data, meta_start + 12, meta_start + meta_size, b"ilst")
    if ilst is None:
        raise RuntimeError(f"{path}: no ilst box")
    ilst_start, ilst_size = ilst

    at = ilst_start + ilst_size
    data = data[:at] + boxes + data[at:]
    for start, size in ((ilst_start, ilst_size), (meta_start, meta_size),
                        (udta_start, udta_size), (moov_start, moov_size)):
        data = _set_size(data, start, size + len(boxes))

    with open(path, "wb") as f:
        f.write(data)


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _data_box(payload: bytes, type_code: int) -> bytes:
    # version/flags carry the payload type, then a 4-byte locale field
    return _box(b"data", struct.pack(">II", type_code, 0) + payload)


def _freeform_box(name: str, value: str) -> bytes:
    return _box(b"----",
                _box(b"mean", struct.pack(">I", 0) + _ITUNES_MEAN)
                + _box(b"name", struct.pack(">I", 0) + name.encode("utf-8"))
                + _data_box(value.encode("utf-8"), _TYPE_UTF8))


def _top_level(data: bytes, wanted: bytes):
    pos = 0
    while pos + 8 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, pos)
        if size == 1:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
        elif size == 0:
            size = len(data) - pos
        if size < 8:
            return None, None
        if kind == wanted:
            return pos, size
        pos += size
    return None, None


def _child(data: bytes, start: int, end: int, wanted: bytes):
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        if size < 8:
            return None
        if kind == wanted:
            return pos, size
        pos += size
    return None


def _set_size(data: bytes, offset: int, size: int) -> bytes:
    return data[:offset] + struct.pack(">I", size) + data[offset + 4:]
