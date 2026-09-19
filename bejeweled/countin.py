"""Detect and remove Fortnite Festival's count-in.

Every Festival track opens with a metronome count-in on the Other stem before the music
starts. Its length is authored per track - four beats on some, eight on others - and the
song begins on the downbeat after it.

The clicks are found rather than assumed, using three properties that hold across the
catalogue:

  - they sit on the beat grid, and the tempo is published in the track metadata
  - they are a consistent ~0.25s long, which separates them from music starting on the
    same beat (Kill Bill's first note is 13ms of onset, not a 250ms click)
  - the count-in runs a whole number of bars, so its length snaps to a multiple of four

Trimming stops short of the downbeat when a stem has a pickup before it. HOT TO GO!
starts its vocal 41ms early at -9.9dB, and cutting on the downbeat would clip it.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from . import ffmpeg as ff

# A count-in click, measured across the catalogue, runs roughly a quarter second
CLICK_MIN_SECONDS = 0.10
CLICK_MAX_SECONDS = 0.45

# How far off the grid a click may sit, as a fraction of a beat
GRID_TOLERANCE = 0.12

# Count-ins run whole bars; anything else is treated as a failed detection
PLAUSIBLE_BEATS = (4, 8, 12, 16)

# Level at which a stem counts as carrying real audio rather than dither. Empty stems
# are common - Call Me Maybe's bass idles at -62dB mean, peaking near -48dB, for the
# whole track - while a real pickup is far louder: HOT TO GO!'s vocal enters at -9.9dB.
# The gap between those is wide, so the threshold sits well clear of the noise.
CONTENT_DB = -40.0

# Left in front of a pickup so its attack is not shaved off
GUARD_SECONDS = 0.02

_SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


@dataclass
class CountIn:
    """A detected count-in, and where it is safe to cut."""

    beats: int
    downbeat: float       # where the music is written to start
    trim_at: float        # where to actually cut, never later than the downbeat
    clicks: list[float]
    clipped_pickup: bool  # true when a stem starts before the downbeat

    @property
    def trimmed(self) -> float:
        return self.trim_at


def detect(stem_paths: dict[str, str], bpm: float, ffmpeg: str | None = None) -> CountIn | None:
    """Find the count-in, given the split stems and the track's tempo.

    Returns None when nothing convincing is found, which is the safe outcome - a track
    is left untouched rather than guessed at.
    """
    if not bpm or bpm <= 0:
        return None
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    beat = 60.0 / bpm

    other = stem_paths.get("Other")
    if not other or not os.path.exists(other):
        return None

    window = beat * (max(PLAUSIBLE_BEATS) + 2)
    clicks = _find_clicks(ffmpeg, other, beat, window)
    if len(clicks) < 3 or clicks[0][0] > beat * GRID_TOLERANCE:
        # A count-in always starts at the top of the file
        return None

    beats = round(clicks[-1][0] / beat) + 1
    beats = min((n for n in PLAUSIBLE_BEATS if n >= beats), default=None)
    if beats is None:
        return None

    downbeat = beats * beat
    last_click_end = clicks[-1][1]
    if last_click_end > downbeat + beat * GRID_TOLERANCE:
        return None

    # Do not cut into a pickup: find the earliest real audio in the other stems
    earliest = None
    for name, path in stem_paths.items():
        if name == "Other" or not os.path.exists(path):
            continue
        onset = _first_content(ffmpeg, path, downbeat + beat)
        if onset is not None and (earliest is None or onset < earliest):
            earliest = onset

    trim_at = downbeat
    clipped = False
    if earliest is not None and earliest < downbeat:
        clipped = True
        trim_at = max(last_click_end, earliest - GUARD_SECONDS)

    return CountIn(
        beats=beats,
        downbeat=downbeat,
        trim_at=max(0.0, min(trim_at, downbeat)),
        clicks=[start for start, _ in clicks],
        clipped_pickup=clipped,
    )


def _sound_spans(ffmpeg: str, path: str, duration: float,
                 threshold_db: float) -> list[tuple[float, float]]:
    """Stretches of audible audio, derived from FFmpeg's silence boundaries."""
    result = subprocess.run(
        [ffmpeg, "-v", "info", "-t", f"{duration:.4f}", "-i", path,
         "-af", f"silencedetect=noise={threshold_db}dB:d=0.05", "-f", "null", "-"],
        capture_output=True,
    )
    events = [(kind, float(value))
              for kind, value in _SILENCE_RE.findall(result.stderr.decode("utf-8", "replace"))]

    spans, open_at = [], 0.0
    silent_from_start = bool(events) and events[0][0] == "start" and events[0][1] <= 0.001
    if silent_from_start:
        open_at = None

    for kind, at in events:
        if kind == "start" and open_at is not None:
            if at > open_at:
                spans.append((open_at, at))
            open_at = None
        elif kind == "end":
            open_at = at
    if open_at is not None and open_at < duration:
        spans.append((open_at, duration))
    return spans


def _find_clicks(ffmpeg: str, other_stem: str, beat: float,
                 window: float) -> list[tuple[float, float]]:
    """Click-shaped, on-grid sounds at the start of the Other stem."""
    clicks = []
    for start, end in _sound_spans(ffmpeg, other_stem, window, -45):
        length = end - start
        if not CLICK_MIN_SECONDS <= length <= CLICK_MAX_SECONDS:
            continue
        position = start / beat
        if abs(position - round(position)) > GRID_TOLERANCE:
            continue
        clicks.append((start, end))
    return clicks


def _first_content(ffmpeg: str, path: str, duration: float) -> float | None:
    """When a stem first carries real audio, ignoring its noise floor."""
    spans = _sound_spans(ffmpeg, path, duration, CONTENT_DB)
    return spans[0][0] if spans else None
