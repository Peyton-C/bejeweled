"""Whole jobs, from a source to a written file.

The command line and the MCP server both run these, so a setting resolves the same way
whichever asked: what was passed, then the config, then the default.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import __version__, config
from .stems import StemSet
from .writers import ni_stem


@dataclass
class Written:
    """What a job left behind: the stem file, or the folder of stems for `files`."""

    path: str
    stem_set: StemSet


def safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def colors(spec: str | None, cfg: dict):
    """A palette asked for by name or as hex colours wins over the configured one."""
    if spec:
        return [c.strip() for c in spec.split(",")] if "," in spec else spec
    return config.palette_spec(cfg)


def _write_ni(stem_set: StemSet, out_dir: str, cfg: dict, merge, codec, palette) -> str:
    out_path = os.path.join(out_dir, f"{safe(stem_set.title)}.stem.mp4")
    ni_stem.write(
        stem_set, out_path,
        codec=codec or cfg.get("output", {}).get("codec", "aac"),
        sample_rate=cfg.get("output", {}).get("sample_rate", 44100),
        merge=merge,
        colors=colors(palette, cfg),
    )
    return out_path


def _format(fmt: str | None, cfg: dict) -> str:
    return fmt or cfg.get("output", {}).get("format", "ni-stem")


# -------------------------------------------------------------------------- festival

def _festival_suffix(cfg: dict, suffix: str | None, no_suffix: bool) -> str | None:
    from .sources import festival

    if no_suffix:
        return None
    return suffix or config.title_suffix(
        festival.SOURCE_NAME, festival.TITLE_MARKER, festival.MARK_TITLES_BY_DEFAULT, cfg)


def festival_output(track: dict, out_dir: str, cfg: dict, suffix: str | None = None,
                    no_suffix: bool = False) -> str:
    """The stem file `rip_festival` writes for this track, known before it is ripped."""
    marker = _festival_suffix(cfg, suffix, no_suffix)
    title = f"{track['title']} {marker}" if marker else track["title"]
    return os.path.join(os.path.abspath(out_dir), f"{safe(title)}.stem.mp4")


def rip_festival(track: dict, out_dir: str, cfg: dict, *, keys: str | None = None,
                 suffix: str | None = None, no_suffix: bool = False, cover: bool = True,
                 trim_countin: bool = True, fmt: str | None = None, codec: str | None = None,
                 palette: str | None = None, progress=None) -> Written:
    from .sources import festival

    out_dir = os.path.abspath(out_dir)
    work = os.path.join(out_dir, f"{safe(track['title'])} - stems")
    fest_cfg = cfg.get("festival", {})

    stem_set = festival.rip(
        track, work, keys_path=keys or fest_cfg.get("keys"), progress=progress,
        title_suffix=_festival_suffix(cfg, suffix, no_suffix),
        cover=cover and fest_cfg.get("cover_art", True),
        trim_countin=trim_countin and fest_cfg.get("trim_countin", True),
    )
    if cfg.get("metadata", {}).get("write_comment", True):
        stem_set.comment = stem_set.describe_source(__version__)

    if _format(fmt, cfg) == "files":
        return Written(work, stem_set)

    out_path = _write_ni(stem_set, out_dir, cfg, festival.NI_MERGE, codec, palette)
    for stem in stem_set.stems:
        os.remove(stem.path)
    if stem_set.cover and os.path.exists(stem_set.cover):
        os.remove(stem_set.cover)
    os.rmdir(work)
    return Written(out_path, stem_set)


# -------------------------------------------------------------------------- separate

def _separator(separator: str | None, cfg: dict) -> str:
    from .sources import separate

    separator = separator or cfg.get("separate", {}).get("separator", separate.DEFAULT_SEPARATOR)
    if separator not in separate.SEPARATORS:
        raise ValueError(f"unknown separator {separator!r}, expected one of "
                         f"{', '.join(separate.SEPARATORS)}")
    return separator


def _separate_suffix(cfg: dict, layout: str, separator: str, suffix: str | None,
                     no_suffix: bool) -> str | None:
    from .sources import separate

    if no_suffix:
        return None
    return suffix or config.title_suffix(
        separate.SOURCE_NAME, separate.title_marker(layout, separator),
        separate.MARK_TITLES_BY_DEFAULT, cfg)


def separated_output(path: str, out_dir: str, cfg: dict, separator: str | None = None,
                     layout: str | None = None, suffix: str | None = None,
                     no_suffix: bool = False) -> str:
    """The stem file `separate_file` writes for this song, known before it is separated.

    The title and the layout are read the way the separation reads them, so this costs
    one probe and saves a caller minutes when the file is already there.
    """
    from . import ffmpeg as ff
    from .sources import separate

    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    info = ff.probe(ff.find_ffmpeg(), path)
    title = separate.read_tags(info)["title"] or os.path.splitext(os.path.basename(path))[0]
    marker = _separate_suffix(cfg, separate.file_layout(info, layout),
                              _separator(separator, cfg), suffix, no_suffix)
    if marker:
        title = f"{title} {marker}"
    return os.path.join(os.path.abspath(out_dir), f"{safe(title)}.stem.mp4")


def separate_file(path: str, out_dir: str, cfg: dict, *, separator: str | None = None,
                  layout: str | None = None, model: str | None = None,
                  device: str | None = None, suffix: str | None = None,
                  no_suffix: bool = False, fmt: str | None = None, codec: str | None = None,
                  palette: str | None = None, progress=None) -> Written:
    from . import demucs as dm
    from .sources import separate

    sep_cfg = cfg.get("separate", {})
    out_dir = os.path.abspath(out_dir)
    base = os.path.splitext(os.path.basename(path))[0]
    work = os.path.join(out_dir, f"{safe(base)} - stems")

    separator = _separator(separator, cfg)
    stem_set = separate.separate(
        path, work, layout=layout, separator=separator,
        model=model or sep_cfg.get("model"),
        device=device or sep_cfg.get("device") or dm.default_device(),
        progress=progress,
    )

    marker = _separate_suffix(cfg, stem_set.extra["layout"], separator, suffix, no_suffix)
    if marker:
        stem_set.title = f"{stem_set.title} {marker}"
    if cfg.get("metadata", {}).get("write_comment", True):
        stem_set.comment = stem_set.describe_source(__version__)

    if _format(fmt, cfg) == "files":
        return Written(work, stem_set)

    out_path = _write_ni(stem_set, out_dir, cfg, separate.NI_MERGE, codec, palette)
    written = [s.path for s in stem_set.stems] + [stem_set.cover]
    if stem_set.master != os.path.abspath(path):
        written.append(stem_set.master)
    for leftover in written:
        if leftover and os.path.exists(leftover):
            os.remove(leftover)
    # An earlier --format files run into the same folder may have left stems this one
    # did not write, such as a RoFormer's Guitar and Piano, which are not ours to delete
    if not os.listdir(work):
        os.rmdir(work)
    return Written(out_path, stem_set)
