"""Existing stem files on disk as a source.

Covers anything that already produced separate stems - stemgen output, a DAW export,
a separation tool - so the writers can be used without a ripper involved.
"""
from __future__ import annotations

import os

from .. import ffmpeg as ff
from ..stems import NI_SLOTS, Stem, StemSet

AUDIO_EXTENSIONS = (".wav", ".wv", ".flac", ".aif", ".aiff", ".mp3", ".m4a", ".opus", ".ogg")

# The user supplied this audio and knows what it is, so nothing is marked unless they
# ask for it. They may still want the origin recorded, so a marker is available.
SOURCE_NAME = "local"
TITLE_MARKER = ""
MARK_TITLES_BY_DEFAULT = False

# Names these files commonly use, mapped onto the slot they belong in
ALIASES = {
    "drums": "Drums", "drum": "Drums", "percussion": "Drums",
    "bass": "Bass",
    "other": "Other", "melody": "Other", "instruments": "Other",
    "synths": "Other", "harmonic": "Other", "lead": "Lead",
    "vocals": "Vocals", "vocal": "Vocals", "vox": "Vocals", "voice": "Vocals",
}

MASTER_NAMES = {"master", "mixdown", "mix", "full", "original"}


def from_folder(folder: str, title: str | None = None, artist: str | None = None) -> StemSet:
    """Build a StemSet from a folder of separate stem files.

    File names are matched case-insensitively against the common stem names, so both
    `Drums.wav` and `04_vocals.flac` land in the right slot. Anything unrecognised is
    kept under its own name rather than dropped, and folding happens at write time.
    """
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"no such folder: {folder}")

    stems, master = [], None
    for entry in sorted(os.listdir(folder)):
        path = os.path.join(folder, entry)
        if not os.path.isfile(path) or os.path.splitext(entry)[1].lower() not in AUDIO_EXTENSIONS:
            continue

        stem_name = os.path.splitext(entry)[0].lower()
        if stem_name in MASTER_NAMES:
            master = path
            continue

        matched = next(
            (slot for alias, slot in ALIASES.items() if alias in stem_name),
            os.path.splitext(entry)[0],
        )
        stems.append(Stem(name=matched, path=path))

    if not stems:
        raise ValueError(f"no stem audio found in {folder}")

    return StemSet(
        title=title or os.path.basename(os.path.normpath(folder)),
        artist=artist,
        stems=stems,
        master=master,
    )


def is_stem_file(path: str) -> bool:
    return path.lower().endswith(".stem.mp4") and os.path.isfile(path)


def from_stem_file(path: str, work_dir: str, ffmpeg: str | None = None) -> StemSet:
    """Build a StemSet from an NI stem file, unpacking its four stems into `work_dir`.

    The stems are named by position, not by what the file calls them: the format fixes
    which slot each track is, and a file from another tool may label its Other
    "Synths".
    """
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    info = ff.probe(ffmpeg, path)
    audio = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    if len(audio) != 1 + len(NI_SLOTS):
        raise ValueError(f"{path}: not a stem file, it has {len(audio)} audio tracks "
                         f"where a stem file has {1 + len(NI_SLOTS)}")

    from .separate import read_tags

    os.makedirs(work_dir, exist_ok=True)
    cmd, stems = [ffmpeg, "-y", "-i", path], []
    for number, slot in enumerate(NI_SLOTS, 1):
        out = os.path.join(work_dir, f"{slot}.wav")
        # Float, so a lossy stem that decodes a little over full scale is not clipped
        # on its way to being encoded again
        cmd += ["-map", f"0:a:{number}", "-c:a", "pcm_f32le", out]
        stems.append(out)
    ff.run(cmd)

    tags = read_tags(info)
    comment = (info.get("format", {}).get("tags") or {}).get("comment")
    return StemSet(
        title=tags["title"] or os.path.basename(path)[:-len(".stem.mp4")],
        stems=[Stem(name=slot, path=out) for slot, out in zip(NI_SLOTS, stems)],
        master=path, artist=tags["artist"], album=tags["album"], year=tags["year"],
        bpm=tags["bpm"], key=tags["key"], genre=tags["genre"], comment=comment,
    )
