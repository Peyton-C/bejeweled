"""User configuration.

Colours are an accessibility setting here, so they belong somewhere durable rather
than on every command line. The file is read-only as far as this module is concerned;
`bejeweled config --init` writes a commented starting point and the user edits it.
"""
from __future__ import annotations

import os
import tomllib

APP_NAME = "bejeweled"

TEMPLATE = '''\
# bejeweled configuration

[output]
# What a rip is written as: "ni-stem" for a .stem.mp4, or "files" for separate
# stem files with no container.
format = "ni-stem"

# aac, alac, flac, opus or wav. NI's own spec allows only aac and alac, but Mixxx
# accepts any codec as long as every stem in the file uses the same one.
codec = "aac"
sample_rate = 44100

[colors]
# Which colours Mixxx shows for each stem. Either name a built-in palette:
#
#   okabe-ito       colourblind-safe default, what stemgen writes
#   vivid-dark      brighter, for dark skins
#   deep-light      darker, for light skins
#   max-separation  maximum contrast, not colourblind-safe
#
palette = "okabe-ito"

# ...or set each slot explicitly, which overrides `palette`:
#
# [colors.custom]
# Drums  = "#00E676"
# Bass   = "#FF9100"
# Other  = "#FF4FD8"
# Vocals = "#40C4FF"

# The background(s) your Mixxx skin draws stems on. `bejeweled palette` measures
# contrast against all of them, since a palette has to work on every deck, and some
# skins give each deck its own background - Deere's are ["#333941", "#413C33"].
backgrounds = ["#1E1E1E"]

[metadata]
# Record which tool and which source produced the file, in the comment field.
write_comment = true

[separate]
# The demucs model. htdemucs_ft is slightly cleaner and four times slower.
model = "htdemucs"

# cpu, cuda or mps. Left unset, Apple Silicon uses mps and anything else lets
# demucs choose.
# device = "mps"

# Mark separated titles so they are not mistaken for real stems: (DE) from a
# stereo file, (DE SR) from a surround mix, (DE AT) from an Atmos render. Setting
# title_suffix here replaces all three.
mark_titles = true

[festival]
# Epic's key database. Not distributed with this project.
# keys = "~/.config/bejeweled/keys.bin"

# Appended to the title, to tell a Festival cut apart from another mix of the same
# song. Each source has its own marker, and decides whether it is on by default.
title_suffix = "(FN)"
mark_titles = true

# Festival publishes key, BPM and cover art for every track; keep them.
cover_art = true

# Remove the metronome count-in that opens every Festival track. Detection is
# conservative: a track it cannot read confidently is left untouched.
trim_countin = true
'''


def config_dir() -> str:
    """Where configuration lives, following the platform convention."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif os.uname().sysname == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


def config_path() -> str:
    return os.environ.get("BEJEWELED_CONFIG") or os.path.join(config_dir(), "config.toml")


def load() -> dict:
    """Read the config file, returning an empty mapping when there is none."""
    path = config_path()
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def init(force: bool = False) -> str:
    """Write the commented template, leaving an existing file alone unless forced."""
    path = config_path()
    if os.path.exists(path) and not force:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(TEMPLATE)
    return path


def title_suffix(source: str, default_marker: str, default_enabled: bool,
                 cfg: dict | None = None) -> str | None:
    """The marker appended to titles from one source, or None when switched off.

    The marker belongs to the source, not to the output format, so each source carries
    its own and decides whether it is on by default. bejeweled fetched a Festival track
    itself and that mix can differ from the commercial release, so it is marked. Audio
    the user supplied is left alone unless they turn the marker on, since they know what
    it is, though they may still want it recorded.
    """
    cfg = cfg if cfg is not None else load()
    section = cfg.get(source, {})

    if not section.get("mark_titles", default_enabled):
        return None

    marker = section.get("title_suffix")
    if marker is None and source == "festival":
        # Markers used to be one global setting, and Festival is the only source that
        # could have been written under it
        marker = cfg.get("metadata", {}).get("title_suffix")
    return (marker if marker is not None else default_marker) or None


def palette_spec(cfg: dict | None = None):
    """Resolve the configured colours into something `palette.resolve` understands."""
    colors = (cfg if cfg is not None else load()).get("colors", {})
    custom = colors.get("custom")
    return custom if custom else colors.get("palette")


def background(cfg: dict | None = None) -> str:
    colors = (cfg if cfg is not None else load()).get("colors", {})
    backgrounds = backgrounds_list(cfg if cfg is not None else None)
    return backgrounds[0] if backgrounds else colors.get("background", "#1E1E1E")


def backgrounds_list(cfg: dict | None = None) -> list[str]:
    """Every background the palette has to work on.

    A skin can draw decks on different backgrounds - Deere gives each deck its own -
    so a palette has to stay legible on all of them, not just one.
    """
    colors = (cfg if cfg is not None else load()).get("colors", {})
    if colors.get("backgrounds"):
        return list(colors["backgrounds"])
    return [colors["background"]] if colors.get("background") else ["#1E1E1E"]
