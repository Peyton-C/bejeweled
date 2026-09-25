"""Existing stem files on disk as a source.

Covers anything that already produced separate stems - stemgen output, a DAW export,
a separation tool - so the writers can be used without a ripper involved.
"""
from __future__ import annotations

import os

from ..stems import Stem, StemSet

AUDIO_EXTENSIONS = (".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a", ".opus", ".ogg")

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
