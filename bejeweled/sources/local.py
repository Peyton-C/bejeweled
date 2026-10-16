"""Existing stem files on disk as a source.

Covers anything that already produced separate stems - stemgen output, a DAW export,
a separation tool - so the writers can be used without a ripper involved.
"""
from __future__ import annotations

import fnmatch
import os
import re

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

MASTER_NAMES = {"master", "mixdown", "mix", "full", "original", "full mix", "main mix"}

# The words a multitrack's file names use, by the slot each goes in. Checked in this
# order, so Vocal FX is a vocal, Bass Drum a drum and Synth Bass a bass. Anything with
# none of these words goes in Other, where most of what a name leaves unsaid belongs.
# Drawn from 4,097 files across 181 multitracks: Fills left Drums when Jazz Bass Fills
# turned out to be a bass part.
SLOT_WORDS = (
    ("Vocals", {"vocal", "vocals", "voc", "vocs", "vc", "vox", "voice", "voices", "bgv", "bgvs",
                "bv", "bvs", "bvox", "bg", "bgs", "ld", "dbl", "dbls", "dobls",
                "double", "doubles", "adlib", "adlibs", "ad", "ads", "harm", "harms",
                "harmony", "harmonies", "choir", "chrs", "gang", "uni", "unison", "ooh",
                "oohs", "oooh", "aah", "aahs", "aaah", "yeah", "yeahs", "hey", "woo",
                "wooo", "whisper", "rap", "chant", "chants", "vocoder", "talkbox", "vfx",
                "tag"}),
    ("Drums", {"drum", "drums", "drm", "kick", "kicks", "kik", "snare", "snares", "snr", "sn",
               "clap", "claps", "hat", "hats", "hh", "hihat", "hihats", "cymbal", "cymbals",
               "cymb", "crash", "ride", "tom", "toms", "perc", "percs", "percussion",
               "percusion", "percy", "shaker", "shakers", "tamb", "tambourine", "triangle",
               "timpani", "rim", "rimshot", "clack", "snap", "snaps", "cowbell", "conga",
               "congas", "bongo", "bongos", "overheads", "beat", "beats", "break",
               "breaks"}),
    ("Bass", {"bass", "basses", "bassline", "subbass", "sub", "subs", "808", "808s"}),
    # Backing on its own is a backing track, as Rock Band names it; backing vocals say so
    ("Other", {"backing", "synth", "synths", "synt", "keys", "key", "keytar", "piano", "rhodes", "organ",
               "moog", "prophet", "solina", "bells", "bell", "marimba", "guitar", "guitars",
               "guit", "gtr", "horn", "horns", "brass", "sax", "trumpet", "strings",
               "string", "violin", "viola", "cello", "pad", "pads", "arp", "arps", "lead",
               "leads", "melody", "riff", "riffs", "solo", "chords", "pluck", "plucks",
               "flute", "sample", "samples", "fx", "sfx", "efx", "riser", "rizer", "swell",
               "swells", "laser", "alarm", "glitch", "stab", "stabs"}),
)

# Parts of a song, which in a multitrack name a vocal: Hook, Verse 2, Bridge Dbl. Only
# when no word above says otherwise, since Techno Synt Chorus is a synth.
SECTION_WORDS = {"hook", "hooks", "verse", "verses", "bridge", "chorus", "prechorus", "pre",
                 "intro", "outro"}

# A multitrack sometimes ships with the acapella or instrumental bounced from it, and
# summing either with the stems would play that part twice
BOUNCE_WORDS = {"acapella", "acappella", "accapella", "cappella", "instrumental",
                "instrumentals"}

# What marks the explicit one of a clean and explicit pair of the same stem
EXPLICIT_WORDS = {"dirty", "explicit"}

SKIP = "skip"


def from_folder(folder: str, title: str | None = None, artist: str | None = None,
                names=None, assign=None) -> StemSet:
    """Build a StemSet from a folder of separate stem files.

    File names are matched case-insensitively against the common stem names, so both
    `Drums.wav` and `04_vocals.flac` land in the right slot.

    With `names`, the stems the caller expects, a file is matched by its whole name
    against those alone. The common names would get in the way: they match anywhere in
    a name, so a slot called Bass Synths would have its file read as Bass.

    A folder that is not one file per slot is a multitrack, read as `from_multitrack`
    reads it. `assign` always makes it one, since it places files by hand.
    """
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"no such folder: {folder}")

    files, master = [], None
    for entry in sorted(os.listdir(folder)):
        path = os.path.join(folder, entry)
        if not os.path.isfile(path) or os.path.splitext(entry)[1].lower() not in AUDIO_EXTENSIONS:
            continue
        if os.path.splitext(entry)[0].lower() in MASTER_NAMES:
            master = path
        else:
            files.append((os.path.splitext(entry)[0], path))

    if not files:
        raise ValueError(f"no stem audio found in {folder}")

    title = title or folder_title(folder)
    if names and not assign:
        stems = [Stem(name=next((n for n in names if n.lower() == name.lower()), name),
                      path=path) for name, path in files]
    else:
        stems = None if assign else _one_per_slot(files)
    if stems is None:
        return from_multitrack(files, title, artist=artist, master=master, names=names,
                               assign=assign)
    return StemSet(title=title, artist=artist, stems=stems, master=master)


def _one_per_slot(files) -> list[Stem] | None:
    """The stems of a folder holding one file per slot, or None for anything else."""
    stems = [Stem(name=next((slot for alias, slot in ALIASES.items()
                             if alias in name.lower()), name), path=path)
             for name, path in files]
    found = [stem.name for stem in stems]
    if set(found) <= {*NI_SLOTS, "Lead"} and len(set(found)) == len(found):
        return stems
    return None


def from_multitrack(files, title: str, artist: str | None = None, master: str | None = None,
                    names=None, assign=None) -> StemSet:
    """Build a StemSet from a multitrack's many stems, each placed in a slot.

    Every file stays a stem of its own under its own name, and `extra["slots"]` says
    which slot it sums into when the set is written. Names decide the slot, by the
    words in `SLOT_WORDS`, and `assign` overrides them: (pattern, slot) pairs, where
    the pattern is a file's name or a glob of it and the slot may be `skip`.

    With `names`, the slots are the caller's own and the words cannot say which of
    them is which, so every file must be assigned.
    """
    targets = list(names or NI_SLOTS)
    by_name = {target.lower(): target for target in targets}
    rules = []
    for pattern, target in assign or ():
        if target.lower() != SKIP and target.lower() not in by_name:
            raise ValueError(f"{pattern}={target}: the slots are {', '.join(targets)} "
                             f"or {SKIP}")
        if not any(fnmatch.fnmatch(name.lower(), pattern.lower()) for name, _ in files):
            raise ValueError(f"{pattern} matches none of {', '.join(n for n, _ in files)}")
        rules.append((pattern.lower(), target.lower()))

    words = {name: _words(name) for name, _ in files}
    stems, slots, unsorted, skipped, unplaced = [], {}, [], {}, []
    for name, path in files:
        chosen = next((target for pattern, target in reversed(rules)
                       if fnmatch.fnmatch(name.lower(), pattern)), None)
        if chosen == SKIP:
            skipped[name] = "assigned to skip"
            continue
        if chosen is None:
            reason = _left_out(name, words, len(files))
            if reason:
                skipped[name] = reason
                continue
        if chosen is not None:
            # A named slot is written in the place of the NI slot at its position
            slot = NI_SLOTS[targets.index(by_name[chosen])]
        elif names:
            unplaced.append(name)
            continue
        else:
            slot = _slot_by_words(words[name])
            if slot is None:
                slot = "Other"
                unsorted.append(name)
        stems.append(Stem(name=name, path=path))
        slots[name] = slot

    if unplaced:
        raise ValueError(f"with --slots, every file of a multitrack is placed with "
                         f"--assign, and {', '.join(unplaced)} "
                         f"{'is' if len(unplaced) == 1 else 'are'} not")
    if not stems:
        raise ValueError("every file was left out")
    return StemSet(title=title, artist=artist, stems=stems, master=master,
                   extra={"slots": slots, "unsorted": unsorted, "skipped": skipped,
                          "slot_names": dict(zip(NI_SLOTS, targets))})


def _words(name: str) -> list[str]:
    # A capital after a lower case letter starts a word, as in mainBASS and IndPerc
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return re.findall(r"[a-z]+|\d+", name.lower())


def _slot_by_words(words: list[str]) -> str | None:
    for slot, vocabulary in SLOT_WORDS:
        if vocabulary.intersection(words):
            return slot
    if SECTION_WORDS.intersection(words):
        return "Vocals"
    return None


def _left_out(name: str, words: dict[str, list[str]], count: int) -> str | None:
    """Why a multitrack's file should not be summed with the rest, if it should not."""
    own = words[name]
    if "dry" in own:
        wet = [w for w in own if w != "dry"]
        twin = next((other for other, theirs in words.items() if theirs == wet), None)
        if twin:
            # 7 rings ships Ld Voc beside Ld Voc Dry, the same take before its effects
            return f"a dry copy of {twin}"
    explicit = next((other for other, theirs in words.items()
                     if theirs != own and sorted(w for w in theirs
                                                 if w not in EXPLICIT_WORDS) ==
                     sorted(w for w in own if w != "clean")), None)
    if explicit and not EXPLICIT_WORDS.intersection(own):
        # good 4 u from Rock Band has Vocals beside Vocals (Dirty), and both would sing
        return f"the clean twin of {explicit}"
    if count > 1 and BOUNCE_WORDS.intersection(own):
        return "a bounce of the stems beside it"
    if name.strip().lower() == "crowd":
        # Rock Band's audience, singing along. Elsewhere a crowd is in the song, as Dua
        # Lipa's 38 CROWD and Zedd's Crowd - 5B are, and is named more than this
        return "Rock Band's audience, not the song"
    return None


def folder_title(folder: str) -> str:
    """A folder's name without the word stems, which titles a download, not the song.

    `AG_Greedy [Stems]` and `Get Into It (Yuh) Stems + Acapella` are how multitracks
    arrive.
    """
    name = os.path.basename(os.path.normpath(folder))
    name = re.sub(r"\s*[\[(][^\])]*\b(stems?|multi ?tracks?)\b[^\])]*[\])]", "", name,
                  flags=re.IGNORECASE)
    name = re.sub(r"\s+(stems?|multi ?tracks?)\b.*$", "", name, flags=re.IGNORECASE)
    return name.strip() or os.path.basename(os.path.normpath(folder))


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
