"""Engine DJ's .stems sidecar: four stereo stems as one encrypted eight-channel track.

The file carries no title, tags or mixdown. Engine finds it by name beside the library,
`<originTrackId> <originDatabaseUuid>.stems`, so this module only makes the audio and
`engine.py` decides what it is called.
"""
from __future__ import annotations

import os
import struct
import tempfile

from .. import ffmpeg as ff
from ..stems import StemSet
from . import ni_stem

# The order Engine reads the channel pairs in. Checked against Engine's own render of a
# track rather than taken from zplane, whose separator hands them over differently.
ENGINE_SLOTS = ("Vocals", "Bass", "Drums", "Other")

# Engine's writer hardcodes layout 0x737 for eight channels, and its reader expects the
# stream that produces
LAYOUT = "FL+FR+FC+BL+BR+BC+SL+SR"

# What Engine's own renders come out at, 630 to 632k across the files looked at
BITRATE = "630k"
SAMPLE_RATE = 44100

BLOCK = 16


def write(stem_set: StemSet, out_path: str, key: bytes, merge: dict[str, str] | None = None,
          ffmpeg: str | None = None) -> str:
    """Render a StemSet to an Engine DJ .stems file.

    The set is folded to the same four slots NI uses, since Engine has the same four,
    and only the order differs.
    """
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    _cipher(key)  # a bad key should fail before the encode, not after it
    groups = stem_set.to_ni_slots(merge).extra["ni_groups"]

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bejeweled-") as work:
        slots = ni_stem.slot_files(ffmpeg, groups, work)
        plain = os.path.join(work, "plain.mp4")
        _encode(ffmpeg, [slots[name] for name in ENGINE_SLOTS], plain)
        # Beside the target, so a failed write never leaves half a file where Engine
        # would look for it
        partial = f"{out_path}.partial"
        try:
            encrypt_file(plain, partial, key)
            os.replace(partial, out_path)
        finally:
            if os.path.exists(partial):
                os.remove(partial)
    return out_path


def _encode(ffmpeg: str, stems: list[str], out_path: str) -> None:
    cmd = [ffmpeg, "-y"]
    for path in stems:
        cmd += ["-i", path]
    prep = ";".join(f"[{i}:a]aformat=channel_layouts=stereo[s{i}]" for i in range(len(stems)))
    chain = "".join(f"[s{i}]" for i in range(len(stems)))
    count = 2 * len(stems)
    # amerge keeps the inputs in the order given when their layouts overlap, as four
    # stereo pairs do, and pan then only names the layout without moving anything
    routing = "|".join(f"c{i}=c{i}" for i in range(count))
    cmd += [
        "-filter_complex",
        f"{prep};{chain}amerge=inputs={len(stems)},pan={LAYOUT}|{routing}[out]",
        "-map", "[out]", "-map_metadata", "-1",
        "-c:a", "aac", "-b:a", BITRATE, "-ar", str(SAMPLE_RATE),
        # Engine's files are mdat first, and the sample tables are patched in place
        # below, which needs moov after the audio
        "-movflags", "-faststart", "-f", "mp4", out_path,
    ]
    ff.run(cmd)


# ------------------------------------------------------------------------ encryption

def encrypt_file(src: str, dst: str, key: bytes) -> int:
    """Encrypt every packet of a plain MP4 the way Engine does. Returns the packet count.

    Engine encrypts the packets, not the file: each AAC packet is padded to a 16-byte
    boundary and AES-128-ECB encrypted, and the container is left readable.
    """
    cipher = _cipher(key)

    def seal(packet: bytes) -> bytes:
        pad = BLOCK - len(packet) % BLOCK
        return cipher.encrypt(packet + bytes([pad]) * pad)

    return _rewrite(src, dst, seal)


def decrypt_file(src: str, dst: str, key: bytes) -> int:
    """Undo `encrypt_file`, leaving an MP4 with plain AAC packets."""
    cipher = _cipher(key)

    def unseal(packet: bytes) -> bytes:
        if not packet or len(packet) % BLOCK:
            raise ValueError(f"{src}: not an Engine DJ stems file")
        plain = cipher.decrypt(packet)
        pad = plain[-1]
        if not 1 <= pad <= BLOCK or plain[-pad:] != bytes([pad]) * pad:
            raise ValueError(f"{src}: wrong key, or not an Engine DJ stems file")
        return plain[:-pad]

    return _rewrite(src, dst, unseal)


def _cipher(key: bytes):
    from Crypto.Cipher import AES

    if len(key) != BLOCK:
        raise ValueError(f"the Engine DJ key is {BLOCK} bytes, got {len(key)}")
    return AES.new(key, AES.MODE_ECB)


def _rewrite(src: str, dst: str, transform) -> int:
    """Pass every packet through `transform`, which may change its size.

    The packets are rewritten end to end in the order they are stored, so only the
    sizes in stsz and the chunk offsets in stco change, and both are patched where they
    sit. Nothing in moov moves or grows.
    """
    with open(src, "rb") as f:
        data = f.read()

    mdat = _find(data, 0, len(data), b"mdat")
    moov = _find(data, 0, len(data), b"moov")
    if mdat is None or moov is None:
        raise ValueError(f"{src}: not an MP4 file")
    if moov[0] < mdat[1]:
        raise ValueError(f"{src}: moov precedes mdat, so the audio cannot grow in place")

    stbl = _find(data, *moov, b"trak", b"mdia", b"minf", b"stbl")
    stsz, stsc, stco = (stbl and _find(data, *stbl, kind) for kind in (b"stsz", b"stsc", b"stco"))
    if not (stsz and stsc and stco):
        raise ValueError(f"{src}: no sample tables")

    uniform, count = struct.unpack_from(">II", data, stsz[0] + 4)
    if uniform:
        raise ValueError(f"{src}: every sample is one size, which AAC never is")
    sizes = struct.unpack_from(f">{count}I", data, stsz[0] + 12)
    chunks = struct.unpack_from(f">{_count(data, stco)}I", data, stco[0] + 8)
    runs = [struct.unpack_from(">III", data, stsc[0] + 8 + 12 * i)
            for i in range(_count(data, stsc))]

    # The first sample of each chunk, so its new offset can be found afterwards
    firsts, sample = [], 0
    for i, (first, per_chunk, _) in enumerate(runs):
        last = runs[i + 1][0] - 1 if i + 1 < len(runs) else len(chunks)
        for chunk in range(first, last + 1):
            firsts.append(sample)
            sample += per_chunk

    # One track written by FFmpeg is stored back to back from the start of mdat, and
    # the rewrite depends on it
    offsets = _running(mdat[0], sizes)
    if (sample < count or offsets[-1] > mdat[1]
            or any(index > count or chunk != offsets[index]
                   for index, chunk in zip(firsts, chunks))):
        raise ValueError(f"{src}: packets are not stored back to back")

    packets = [transform(data[at:at + size]) for at, size in zip(offsets, sizes)]
    starts = _running(mdat[0], [len(p) for p in packets])

    payload = b"".join(packets)
    tail = bytearray(data[mdat[1]:])
    struct.pack_into(f">{count}I", tail, stsz[0] + 12 - mdat[1], *(len(p) for p in packets))
    struct.pack_into(f">{len(chunks)}I", tail, stco[0] + 8 - mdat[1],
                     *(starts[index] for index in firsts))

    with open(dst, "wb") as f:
        f.write(data[:mdat[0] - 8])
        f.write(struct.pack(">I4s", 8 + len(payload), b"mdat"))
        f.write(payload)
        f.write(tail)
    return count


def _running(start: int, sizes) -> list[int]:
    """Where each of a run of back-to-back packets begins, and where the last ends."""
    offsets = [start]
    for size in sizes:
        offsets.append(offsets[-1] + size)
    return offsets


def _count(data: bytes, box: tuple[int, int]) -> int:
    # Past the version and flags every full box opens with
    return struct.unpack_from(">I", data, box[0] + 4)[0]


def _find(data: bytes, start: int, end: int, *kinds: bytes) -> tuple[int, int] | None:
    """Walk down through nested boxes, returning where the last one's payload sits."""
    for kind in kinds:
        pos, found = start, None
        while pos + 8 <= end:
            size, name = struct.unpack_from(">I4s", data, pos)
            if size < 8:
                break
            if name == kind:
                found = (pos + 8, pos + size)
                break
            pos += size
        if found is None:
            return None
        start, end = found
    return start, end
