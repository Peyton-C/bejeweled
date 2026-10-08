"""Any mixed song as a stem source, separated by a RoFormer, demucs, or both.

Stereo is separated as it is. A multichannel render (5.1, 7.1.4, a 9.1.6 Atmos render)
is folded to stereo in groups by where the mixer placed things, each group is
separated on its own, and each stem is summed back across groups. Stereo is the same
path with a single group.

Grouping works because a mixer who sends pads, synths and backing vocals to the
surrounds and heights has already pulled them apart from what stays in the bed.
Against Festival's real multitracks, six Atmos renders, four groups beat the plain
stereo fold by +1.05 dB SDR on average, and every track improved (+0.48 to +1.45).
Almost all of it is less bleed rather than fewer artifacts: SIR +1.99 dB, SAR +0.73.
Drums and bass gain little, since nobody puts the kick in the heights. Splitting
further, one group per speaker pair, scored +1.00 against +0.99 for twice the
compute, so four groups is fixed rather than a setting.
"""
from __future__ import annotations

import os
import re
import shutil

from .. import demucs as dm
from .. import ffmpeg as ff
from .. import roformer as rf
from ..stems import Stem, StemSet

# roformer runs BS-Roformer-SW alone. hybrid takes the vocals with a RoFormer vocal
# model and splits what is left with demucs: +0.52 dB over htdemucs on average against
# roformer's +0.80, but it was the only one to improve every one of ten tracks.
SEPARATORS = ("roformer", "hybrid", "demucs")
DEFAULT_SEPARATOR = "roformer"

# The demucs model each separator uses unless told otherwise. The hybrid was measured
# with htdemucs_ft, whose four times the cost is small beside the RoFormer's.
DEFAULT_MODELS = {"demucs": dm.DEFAULT_MODEL, "hybrid": "htdemucs_ft"}

# The input is the user's own audio, but the stems are bejeweled's, and a separation
# should never pass for real stems of the same song, so titles are marked by default.
# Two separators' stems of one song differ as much as two platforms' mixes do, so each
# has its own letters, and a set cut from a render is marked apart from one cut from
# the stereo master. The markers name the input, never the output, which is stereo
# either way: (ATMOS) alone would suggest the stems are still Atmos, and (AI) would
# suggest generated audio.
SOURCE_NAME = "separate"
SEPARATOR_CODES = {"roformer": "RF", "hybrid": "HY", "demucs": "DE"}
TITLE_MARKER = f"({SEPARATOR_CODES[DEFAULT_SEPARATOR]})"
MARK_TITLES_BY_DEFAULT = True

# BS-Roformer-SW and htdemucs_6s split two more instruments out of Other; NI has no
# slot for either
NI_MERGE = {"Guitar": "Other", "Piano": "Other"}

# Channel roles in the order each layout carries them. Files ripped through a
# loopback device carry no channel mask (every Atmos capture here has
# channelmask=0x00000000), so the layout cannot be read from the file and is declared
# by channel count instead, overridable with --layout.
LAYOUTS = {
    "mono": ("M",),
    "stereo": ("L", "R"),
    "5.1": ("L", "R", "C", "LFE", "Ls", "Rs"),
    "7.1": ("L", "R", "C", "LFE", "Lss", "Rss", "Lrs", "Rrs"),
    "5.1.2": ("L", "R", "C", "LFE", "Ls", "Rs", "Ltm", "Rtm"),
    "5.1.4": ("L", "R", "C", "LFE", "Ls", "Rs", "Ltf", "Rtf", "Ltr", "Rtr"),
    "7.1.2": ("L", "R", "C", "LFE", "Lss", "Rss", "Lrs", "Rrs", "Ltm", "Rtm"),
    "7.1.4": ("L", "R", "C", "LFE", "Lss", "Rss", "Lrs", "Rrs", "Ltf", "Rtf", "Ltr", "Rtr"),
    "9.1.6": ("L", "R", "C", "LFE", "Lss", "Rss", "Lrs", "Rrs",
              "Lw", "Rw", "Ltf", "Rtf", "Ltm", "Rtm", "Ltr", "Rtr"),
}

# 10 channels is left out on purpose: 5.1.4 and 7.1.2 are both common
DEFAULT_LAYOUTS = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1", 12: "7.1.4", 16: "9.1.6"}

# A file that does declare its layout, as a 5.1 album rip usually does, is read by it.
# FFmpeg's orders differ from the renderer's inside the surround and height channels
# (7.1 puts the rears before the sides), which is harmless: those channels share a
# group and each still folds to its own side.
FFMPEG_LAYOUTS = {
    "mono": "mono", "stereo": "stereo", "5.1": "5.1", "5.1(side)": "5.1",
    "7.1": "7.1", "5.1.2": "5.1.2", "5.1.4": "5.1.4", "7.1.2": "7.1.2", "7.1.4": "7.1.4",
}

GROUPS = ("bed", "surround", "wide", "height")

_GROUP_OF = {
    "M": "bed", "L": "bed", "R": "bed", "C": "bed", "LFE": "bed",
    "Ls": "surround", "Rs": "surround", "Lss": "surround", "Rss": "surround",
    "Lrs": "surround", "Rrs": "surround",
    "Lw": "wide", "Rw": "wide",
    "Ltf": "height", "Rtf": "height", "Ltm": "height", "Rtm": "height",
    "Ltr": "height", "Rtr": "height",
}

_H = 0.7071067811865476


def gains(role: str) -> tuple[float, float]:
    """How much of one channel goes to the left and right of the stereo fold.

    L and R stay put, C and LFE go to both at -3 dB, and everything else goes to its
    own side at -3 dB. Every group uses these same gains restricted to its own
    channels, so the groups sum back to the plain fold exactly (-145 dB residual,
    float32 rounding) and any difference in the result is down to the grouping.
    """
    if role == "M":
        return 1.0, 1.0
    if role == "L":
        return 1.0, 0.0
    if role == "R":
        return 0.0, 1.0
    if role in ("C", "LFE"):
        return _H, _H
    return (_H, 0.0) if role.startswith("L") else (0.0, _H)


def resolve_layout(channels: int, layout: str | None = None, declared: str | None = None) -> str:
    """The layout to read a file as: the one asked for, the one the file declares, or
    the default for its width. A declared layout this table cannot map is an error
    rather than a guess, since a hexagonal file would otherwise be read as 5.1."""
    if layout:
        if layout not in LAYOUTS:
            raise ValueError(f"unknown layout {layout!r}, expected one of {', '.join(LAYOUTS)}")
        if len(LAYOUTS[layout]) != channels:
            raise ValueError(
                f"layout {layout} has {len(LAYOUTS[layout])} channels, the file has {channels}"
            )
        return layout
    # ffprobe reports an undeclared layout as "unknown", or as "16 channels"
    if declared and declared != "unknown" and not declared.endswith(" channels"):
        if declared in FFMPEG_LAYOUTS:
            return FFMPEG_LAYOUTS[declared]
        raise ValueError(f"the file declares a {declared} layout, which bejeweled cannot "
                         f"group, pass --layout to read it as one it can")
    if channels in DEFAULT_LAYOUTS:
        return DEFAULT_LAYOUTS[channels]
    fits = [name for name, roles in LAYOUTS.items() if len(roles) == channels]
    hint = f" (one of {', '.join(fits)})" if fits else ""
    raise ValueError(f"cannot tell the layout of a {channels} channel file, pass --layout{hint}")


def groups(layout: str) -> dict[str, list[int]]:
    """Channel indices per group, in GROUPS order, leaving out groups with none."""
    found: dict[str, list[int]] = {name: [] for name in GROUPS}
    for index, role in enumerate(LAYOUTS[layout]):
        found[_GROUP_OF[role]].append(index)
    return {name: indices for name, indices in found.items() if indices}


def pan_filter(layout: str, channels: list[int]) -> str:
    """An FFmpeg pan expression folding the given channels to stereo."""
    roles = LAYOUTS[layout]
    sides: tuple[list[str], list[str]] = ([], [])
    for index in channels:
        for side, gain in zip(sides, gains(roles[index])):
            if gain:
                side.append(f"{gain!r}*c{index}")
    # A group with nothing on one side still needs that side, or pan emits mono
    left, right = ("+".join(side) or "0*c0" for side in sides)
    return f"pan=stereo|c0={left}|c1={right}"


def title_marker(layout: str, separator: str = DEFAULT_SEPARATOR) -> str:
    """The marker for a set cut by this separator from this layout: whether it came
    from a stereo master, a surround mix, or a render with height channels."""
    code = SEPARATOR_CODES[separator]
    roles = LAYOUTS[layout]
    if len(roles) <= 2:
        return f"({code})"
    if any(_GROUP_OF[role] == "height" for role in roles):
        return f"({code} AT)"
    return f"({code} SR)"


# ------------------------------------------------------------------------- metadata

def read_tags(info: dict) -> dict:
    """Tags worth carrying over, from ffprobe output. Keys vary by container."""
    tags = {}
    for stream in info.get("streams", []):
        tags.update({k.lower(): v for k, v in (stream.get("tags") or {}).items()})
    tags.update({k.lower(): v for k, v in (info.get("format", {}).get("tags") or {}).items()})

    def first(*keys):
        return next((tags[k].strip() for k in keys if tags.get(k, "").strip()), None)

    year = first("date", "year", "originaldate")
    match = re.search(r"\d{4}", year or "")
    bpm = first("bpm", "tbpm", "tmpo")
    try:
        bpm = float(bpm) if bpm else None
    except ValueError:
        bpm = None
    return {
        "title": first("title"),
        "artist": first("artist", "album_artist"),
        "album": first("album"),
        "year": match.group(0) if match else None,
        "bpm": bpm or None,
        "key": first("initialkey", "tkey", "key"),
        "layout": _tagged_layout(first("comment")),
    }


def _tagged_layout(comment: str | None) -> str | None:
    """A `layout=` entry in the comment, which OutOfTheWoods writes because neither a
    WAV channel mask nor FFmpeg's layout names can describe Music's channel order."""
    match = re.search(r"\blayout=(\S+)", comment or "")
    return match.group(1) if match else None


def file_layout(info: dict, layout: str | None = None) -> str:
    """The layout to read a probed file as: the one asked for, a `layout=` tag, the
    one the file declares, or the default for its channel count."""
    stream = _audio_stream(info)
    # FFmpeg's WavPack decoder makes up its default layout when the file declares
    # none, reporting an undeclared 16 channel file as its own 9.1.6, whose order is
    # not Music's, so for WavPack only the channel count is trusted
    declared = None if stream.get("codec_name") == "wavpack" else stream.get("channel_layout")
    return resolve_layout(int(stream.get("channels") or 0),
                          layout or read_tags(info)["layout"], declared)


def _audio_stream(info: dict) -> dict:
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            return stream
    raise ValueError("no audio stream in the file")


def _extract_cover(ffmpeg: str, path: str, info: dict, out_dir: str) -> str | None:
    """Pull embedded cover art out, if the file has any."""
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("disposition", {}).get("attached_pic"):
            ext = "png" if stream.get("codec_name") == "png" else "jpg"
            out = os.path.join(out_dir, f"cover.{ext}")
            ff.run([ffmpeg, "-v", "error", "-y", "-i", path, "-map", f"0:{stream['index']}",
                    "-c", "copy", "-frames:v", "1", out])
            return out
    return None


# ------------------------------------------------------------------------ separate

def separate(path: str, work_dir: str, layout: str | None = None,
             separator: str = DEFAULT_SEPARATOR, model: str | None = None,
             device: str | None = None, progress=None, ffmpeg: str | None = None,
             demucs: str | None = None, audio_separator: str | None = None) -> StemSet:
    """Separate one mixed file into a StemSet, with the stems written to `work_dir`.

    `model` is the demucs model, used by demucs and by the second half of the hybrid,
    and `device` is passed to demucs only; audio-separator picks its own.

    Scratch audio goes in a hidden folder under `work_dir` rather than the system
    temp directory, since a 16 channel render runs to a few gigabytes of it.
    """
    if separator not in SEPARATORS:
        raise ValueError(f"unknown separator {separator!r}, expected one of {', '.join(SEPARATORS)}")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    path = os.path.abspath(path)
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    # Both found before any audio is folded, so a missing tool fails at once
    if separator != "roformer":
        demucs = dm.find_demucs(demucs)
        model = model or DEFAULT_MODELS[separator]
    if separator != "demucs":
        audio_separator = rf.find_audio_separator(audio_separator)

    info = ff.probe(ffmpeg, path)
    stream = _audio_stream(info)
    tags = read_tags(info)
    layout = file_layout(info, layout)
    plan = groups(layout)

    os.makedirs(work_dir, exist_ok=True)
    scratch = os.path.join(work_dir, ".separate")
    mix_dir = os.path.join(scratch, "mix")
    os.makedirs(mix_dir, exist_ok=True)
    try:
        # Folded to float so a sum over 0 dBFS reaches demucs intact rather than
        # clipped, and so every group is cut from the same decode
        submixes = {}
        for name, channels in plan.items():
            out = os.path.join(mix_dir, f"{name}.wav")
            ff.run([ffmpeg, "-v", "error", "-y", "-i", path, "-map", f"0:{stream['index']}",
                    "-af", pan_filter(layout, channels),
                    "-c:a", "pcm_f32le", out])
            submixes[name] = out

        separated = _run_separator(separator, list(submixes.values()),
                                   os.path.join(scratch, "sep"), model, device, progress,
                                   ffmpeg, demucs, audio_separator)

        stems = []
        for stem_name in separated[submixes[next(iter(plan))]]:
            parts = [part for sub in submixes.values() for part in separated[sub][stem_name]]
            name = stem_name.capitalize()
            out = os.path.join(work_dir, f"{name}.wav")
            if len(parts) == 1:
                os.replace(parts[0], out)
            else:
                ff.mix(ffmpeg, parts, out, ["-c:a", "pcm_f32le"])
            stems.append(Stem(name=name, path=out))

        # The stereo file is its own mixdown. A render's mixdown is the plain fold of
        # every channel, which the stems approximate and the groups sum to exactly.
        master = path
        if len(plan) > 1:
            master = os.path.join(work_dir, "Master.wav")
            ff.run([ffmpeg, "-v", "error", "-y", "-i", path, "-map", f"0:{stream['index']}",
                    "-af", pan_filter(layout, list(range(len(LAYOUTS[layout])))),
                    "-c:a", "pcm_f32le", master])
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        if not os.listdir(work_dir):
            os.rmdir(work_dir)
        raise
    shutil.rmtree(scratch, ignore_errors=True)

    if separator == "roformer":
        source = _model_name(rf.STEMS_MODEL)
    elif separator == "hybrid":
        source = f"{_model_name(rf.VOCALS_MODEL)} vocals, demucs {model}"
    else:
        source = f"demucs {model}"
    if len(LAYOUTS[layout]) > 2:
        source += f", {layout} render in {len(plan)} groups"
    return StemSet(
        title=tags["title"] or os.path.splitext(os.path.basename(path))[0],
        artist=tags["artist"],
        album=tags["album"],
        year=tags["year"],
        bpm=tags["bpm"],
        key=tags["key"],
        cover=_extract_cover(ffmpeg, path, info, work_dir),
        stems=stems,
        master=master,
        source=source,
        extra={"layout": layout, "groups": list(plan), "separator": separator},
    )


def _run_separator(separator, inputs, out_dir, model, device, progress,
                   ffmpeg, demucs, audio_separator) -> dict[str, dict[str, list[str]]]:
    """{input: {stem name: [paths that sum to that stem]}} from one separator."""
    if separator == "roformer":
        found = rf.separate(audio_separator, inputs, out_dir, rf.STEMS_MODEL, ffmpeg, progress)
        return {p: {k: [v] for k, v in stems.items()} for p, stems in found.items()}
    if separator == "demucs":
        found = dm.separate(demucs, inputs, out_dir, model, device, progress)
        return {p: {k: [v] for k, v in stems.items()} for p, stems in found.items()}

    # The hybrid is two passes over every group, each reported as half the job
    def half(offset):
        if not progress:
            return None
        return lambda done, total, *stage: (
            progress(done, total, *stage) if stage else progress(offset * total + done, 2 * total))

    voc = rf.separate(audio_separator, inputs, os.path.join(out_dir, "vocals"),
                      rf.VOCALS_MODEL, ffmpeg, half(0))
    rest_of = {p: next(v for k, v in stems.items() if k != "vocals") for p, stems in voc.items()}
    rest = dm.separate(demucs, list(rest_of.values()), os.path.join(out_dir, "rest"),
                       model, device, half(1))
    found = {}
    for p in inputs:
        stems = {k: [v] for k, v in rest[rest_of[p]].items()}
        # Whatever demucs still hears as voice in the instrumental is vocal content
        # the RoFormer missed, so it goes back with the vocals rather than being lost
        stems["vocals"].insert(0, voc[p]["vocals"])
        found[p] = stems
    return found


def _model_name(filename: str) -> str:
    return os.path.splitext(filename)[0]
