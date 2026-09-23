"""Detect and remove Fortnite Festival's count-in.

Every Festival track opens with a metronome count-in on the Other stem before the music
starts. Its length is authored per track - four beats on some, eight on others - and the
song begins on the downbeat after it.

The clicks are found rather than assumed. bejeweled measures the level of the Other stem
frame by frame, takes the transients out of that envelope, and looks for the grid the
count-in sits on:

  - a count-in has its own tempo, near the published one but sometimes well off it
  - it is the first thing in the file, so nothing as loud may precede it
  - every click is struck at the same level, which is what tells a count-in apart from
    an intro that happens to land on the beat
  - it is heard against silence, ringing down between clicks
  - the clicks form one block, skipping at most a beat or two, and then stop

The downbeat is one beat past the last click. Trimming stops short of it when a stem has
a pickup before it, and the clicks left standing in front of the downbeat are silenced
on the Other stem rather than cut away: Bad Romance clicks on all eight beats and brings
the vocal in on the sixth, so no single cut keeps the vocal and loses the clicks.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from . import ffmpeg as ff

# The stem Festival puts the count-in on
CLICK_STEM = "Other"

# Envelope resolution. 5ms is short enough to place a click within a frame or two, and
# long enough that one frame is a level rather than a single sample.
FRAME_RATE = 44100
FRAME_SAMPLES = 220
HOP = FRAME_SAMPLES / FRAME_RATE

# How far the count-in's tempo may sit from the one Epic publishes. The published tempo
# is a starting point and no more: vampire is tagged 139 and counts in at 134.3, 24K
# Magic is tagged 107 and counts in at 90.2. The clicks themselves set the grid, so this
# only has to be wide enough to reach them, and nothing in the sample needs it wider.
TEMPO_TOLERANCE = 0.25

# A count-in is not always counted in the song's own beat. Side To Side is tagged 159
# and ticks every other beat, counting eight half notes rather than sixteen beats.
COUNTED_IN_BEATS = (1, 2)

# How far off its grid line a click may sit. A metronome is far tighter than this, no
# count-in in the sample fits its own grid worse than 2.5ms, and holding to it stops a
# slightly mistuned grid from assembling a run out of transients that are not a
# count-in. Anything from 10 to 30ms works; the middle of that is the safest place.
GRID_TOLERANCE = 0.015

# A count-in runs at most four bars, and the search has to reach past any silence in
# front of it: good 4 u leaves its first four beats empty before the clicks start.
MAX_COUNTIN_BEATS = 16
WINDOW_BEATS = MAX_COUNTIN_BEATS + 10

# Clicks in one count-in are struck at one level: Illegal's six sit within 3dB of each
# other, Apple's within 1.3dB. A run that spreads wider than this is music.
CLICK_LEVEL_SPREAD = 6.0

# Beats a count-in may skip and still read as one block. Festival's usual pattern clicks
# on beats 1, 3, 5, 6, 7, 8, so two is the widest gap that occurs, and allowing three
# lets The Giver's count-in run on into the guitar that follows it.
MAX_SKIPPED_BEATS = 2

# Fewest clicks worth trusting. The Giver has five, most tracks six, Bad Romance eight.
MIN_CLICKS = 4

# A click is struck and left to ring, so it drops away again before the next beat while
# music carries on. Born This Way's opening chord, which lands on the grid at the same
# level as its count-in, drops 1dB. Clicks drop far further, but not limitlessly: Rain
# On Me's last click has the song coming in under it and drops 16.5dB, which is what
# puts the ceiling on this rather than the clicks themselves.
CLICK_FALL_DB = 14.0

# Measured from here into the beat, so the click's own attack is not part of it
FALL_FROM = 0.15

# A transient is this far above the preceding 20ms. The softest click in the sample
# rises 16dB, so there is room under it, and Born This Way's opening chord swells by
# about 10dB over the same span and is left where it belongs, out of the count-in.
ONSET_RISE_DB = 12.0
ONSET_LOOKBACK = 4

# Below this a frame is the encoder's noise floor rather than anything struck
ONSET_FLOOR_DB = -60.0

# Nothing else is taken within 60ms of a transient, so one strike counts once
ONSET_HOLD = 0.06

# Level at which a stem counts as carrying real audio rather than dither. Empty stems
# are common - Call Me Maybe's bass idles at -62dB mean, peaking near -48dB, for the
# whole track - while a real pickup is far louder: HOT TO GO!'s vocal enters at -9.9dB.
# The gap between those is wide, so the threshold sits well clear of the noise.
CONTENT_DB = -40.0

# A stem has to hold above that level this long to count. good 4 u's vocal stem carries
# a 3ms tick a beat before the downbeat that is not a pickup and must not be read as one.
CONTENT_SECONDS = 0.01

# Left in front of a pickup so its attack is not shaved off
GUARD_SECONDS = 0.02

SILENT_DB = -140.0

_LEVEL_RE = re.compile(r"RMS_level=(-?[\d.]+|-inf)")


@dataclass
class CountIn:
    """A detected count-in, and where it is safe to cut."""

    beats: int         # counted from the first click, so a beat short where it is silent
    downbeat: float    # where the music is written to start
    trim_at: float     # where to actually cut, never later than the downbeat
    mute_until: float  # how far past the cut the Other stem is still count-in
    clicks: list[float]
    kept_pickup: bool  # true when the cut was held back for a stem that starts early

    @property
    def muted(self) -> float:
        """Seconds of count-in left on the Other stem once the cut is made."""
        return max(0.0, self.mute_until - self.trim_at)


def search_window(bpm: float) -> float:
    """How much of the opening has to be decoded to find the count-in."""
    return WINDOW_BEATS * 60.0 / bpm


def detect(stem_paths: dict[str, str], bpm: float, ffmpeg: str | None = None) -> CountIn | None:
    """Find the count-in, given the split stems and the track's tempo.

    Returns None when nothing convincing is found, which is the safe outcome - a track
    is left untouched rather than guessed at.
    """
    if not bpm or bpm <= 0:
        return None
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    beat = 60.0 / bpm

    click_path = stem_paths.get(CLICK_STEM)
    if not click_path or not os.path.exists(click_path):
        return None

    window = search_window(bpm)
    env = _envelope(ffmpeg, click_path, window)
    run, period = _count_in_run(_onsets(env), beat)
    if run is None:
        return None

    # A count-in is heard against silence. Where the click stem plays on underneath it
    # the clicks can still be picked out, but nothing useful can be done with them: no
    # cut takes the clicks without taking the music, and they cannot be silenced on
    # their own stem either, because the music is on it too. Leave the track alone.
    if any(_fall(env, strike, period) < CLICK_FALL_DB for strike in run[:-1]):
        return None

    clicks = [frame * HOP for frame, _ in run]

    entries = []
    for name, path in stem_paths.items():
        if name == CLICK_STEM or not os.path.exists(path):
            continue
        entry = _first_content(ffmpeg, path, clicks[-1] + 3 * period)
        if entry is not None:
            entries.append(entry)

    downbeat = clicks[-1] + period
    if _is_the_downbeat(env, run, period, entries):
        downbeat, clicks = clicks[-1], clicks[:-1]
        if len(clicks) < MIN_CLICKS:
            return None
    if downbeat > window:
        return None

    # A stem arriving within a frame or two of the grid line places the downbeat more
    # exactly than extrapolating from the last click does, and it is what the cut has
    # to respect anyway
    arrival = [entry for entry in entries if abs(entry - downbeat) <= GRID_TOLERANCE]
    if arrival:
        downbeat = min(arrival)

    earliest = min((entry for entry in entries if entry < downbeat), default=None)
    if earliest is not None and earliest < clicks[0]:
        # Music in front of the first click: this is not the top of the song
        return None

    trim_at, pickup = downbeat, False
    if earliest is not None:
        trim_at, pickup = max(0.0, earliest - GUARD_SECONDS), True

    # Only a click that survives the cut has to be silenced. Holding back a few
    # milliseconds for a stem that enters on the downbeat leaves nothing to hide. Where
    # there is something to hide, the click stem is checked for music of its own so the
    # silence stops short of it. That check does not also hold the cut back, the way a
    # pickup on another stem does: a click ringing down and a pad swelling under it look
    # too alike in the envelope to tell apart, and every rule tried for it moved cuts
    # that were already right earlier than they belonged.
    mute_until = trim_at
    if clicks[-1] >= trim_at:
        resumes = _resumes(env, clicks[-1], downbeat)
        mute_until = downbeat if resumes is None else min(downbeat, resumes)

    return CountIn(
        beats=round((downbeat - clicks[0]) / period),
        downbeat=downbeat,
        trim_at=trim_at,
        mute_until=mute_until,
        clicks=clicks,
        kept_pickup=pickup,
    )


def _envelope(ffmpeg: str, path: str, duration: float) -> list[float]:
    """The level of each 5ms frame, in dB.

    astats does the arithmetic, so a whole opening is measured in one FFmpeg pass. The
    signal is left at full bandwidth deliberately: Apple's clicks are mostly above 4kHz
    and read 30dB quieter through a downmix to 8kHz, which is enough to lose them.
    """
    chain = (
        f"aresample={FRAME_RATE},asetnsamples=n={FRAME_SAMPLES}:p=0,"
        "astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
    )
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-t", f"{duration:.4f}", "-i", path,
         "-af", chain, "-f", "null", "-"],
        capture_output=True,
    )
    return [SILENT_DB if value == "-inf" else float(value)
            for value in _LEVEL_RE.findall(result.stdout.decode("utf-8", "replace"))]


def _onsets(env: list[float]) -> list[tuple[int, float]]:
    """Every strike in the envelope: the frame it begins on, and how hard it hits.

    The frame is the start of the attack rather than its peak. A click takes two or
    three frames to reach full level, so peaking it would put it that far late on the
    grid and measure its rise from halfway up its own attack - Good Luck, Babe!'s clicks
    jump 58dB out of silence but only 12dB from there.
    """
    reach = max(1, round(ONSET_HOLD / HOP))
    strikes: list[tuple[int, float]] = []
    i = 0
    while i < len(env):
        before = env[max(0, i - ONSET_LOOKBACK):i]
        rise = env[i] - (min(before) if before else SILENT_DB)
        if env[i] < ONSET_FLOOR_DB or rise < ONSET_RISE_DB:
            i += 1
            continue
        strikes.append((i, max(env[i:i + reach])))
        i += reach
    return strikes


def _is_the_downbeat(env: list[float], run: list[tuple[int, float]], period: float,
                     entries: list[float]) -> bool:
    """Whether a run's last strike is really the music starting, not a last click.

    The music's first hit lands on the grid as readily as a click does, and can be
    struck as hard: Illegal and Pink Pony Club both open within 3dB of their own
    count-in, and taking either for a last click would put the downbeat a beat late and
    trim a beat of music away. The rest of the band arriving on it is what gives those
    two away.

    The second test is for a downbeat the Other stem carries alone, where there is no
    band to look at: the music holds through the beat where a click would have dropped
    away. Nothing in the sample needs it, since Born This Way, which opens that way,
    swells too slowly to read as a strike at all, so it is a guard rather than a
    correction.
    """
    last = run[-1][0] * HOP
    if any(abs(entry - last) <= GRID_TOLERANCE for entry in entries):
        return True
    return _fall(env, run[-1], period) < CLICK_FALL_DB


def _fall(env: list[float], strike: tuple[int, float], period: float) -> float:
    """How far a strike drops away again before the next beat."""
    frame, level = strike
    tail = env[frame + int(FALL_FROM * period / HOP):frame + int(period / HOP) + 1]
    return level - min(tail) if tail else 0.0


def _count_in_run(strikes: list[tuple[int, float]],
                  beat: float) -> tuple[list[tuple[int, float]] | None, float]:
    """The longest block of equally struck transients that shares one grid.

    Every pair of strikes proposes a tempo, so the grid comes out of the audio rather
    than out of the published tempo, which can be several percent off.
    """
    best, best_period, best_level = None, 0.0, SILENT_DB
    counted = [beat * n for n in COUNTED_IN_BEATS]
    low = min(counted) * (1 - TEMPO_TOLERANCE)

    for a, (anchor, _) in enumerate(strikes):
        for b in range(a + 1, len(strikes)):
            span = (strikes[b][0] - anchor) * HOP
            for beats in range(1, MAX_COUNTIN_BEATS + 1):
                period = span / beats
                if period < low:
                    break
                if not any(abs(period - c) <= c * TEMPO_TOLERANCE for c in counted):
                    continue
                run = _run_on_grid(strikes, a, period)
                if len(run) < MIN_CLICKS:
                    continue
                softest = min(hit for _, hit in run)
                if any(hit > softest - CLICK_LEVEL_SPREAD
                       for frame, hit in strikes if frame < run[0][0]):
                    # Something was struck before the count-in, so this is not one
                    continue
                if (len(run), softest) > (len(best or []), best_level):
                    best, best_period, best_level = run, period, softest
    return best, best_period


def _run_on_grid(strikes: list[tuple[int, float]], a: int,
                 period: float) -> list[tuple[int, float]]:
    """Strikes on the anchor's grid, hit as hard as it, in one unbroken block."""
    anchor, anchor_level = strikes[a]
    on_grid, start = [], 0
    for k, strike in enumerate(strikes):
        if abs(strike[1] - anchor_level) > CLICK_LEVEL_SPREAD:
            continue
        offset = (strike[0] - anchor) * HOP
        if abs(offset - round(offset / period) * period) > GRID_TOLERANCE:
            continue
        if k == a:
            start = len(on_grid)
        on_grid.append(strike)

    # Cut the block either side of the anchor at the first gap too wide for a count-in,
    # so a transient later in the intro that lands on the grid cannot extend it
    reach = period * MAX_SKIPPED_BEATS + GRID_TOLERANCE
    first = last = start
    while first > 0 and (on_grid[first][0] - on_grid[first - 1][0]) * HOP <= reach:
        first -= 1
    while last + 1 < len(on_grid) and (on_grid[last + 1][0] - on_grid[last][0]) * HOP <= reach:
        last += 1
    return on_grid[first:last + 1]


def _first_content(ffmpeg: str, path: str, duration: float) -> float | None:
    """When a stem first carries real audio, ignoring its noise floor."""
    return _content_after(_envelope(ffmpeg, path, duration), 0.0)


def _content_after(env: list[float], after: float) -> float | None:
    frames = max(1, round(CONTENT_SECONDS / HOP))
    for i in range(int(after / HOP), len(env) - frames + 1):
        if all(db > CONTENT_DB for db in env[i:i + frames]):
            return i * HOP
    return None


def _resumes(env: list[float], last_click: float, downbeat: float) -> float | None:
    """When the click stem picks up again, once the last click has rung down.

    None where it stays quiet to the downbeat, which is the usual case. The stem is
    checked rather than assumed, because anything it plays in front of the downbeat is
    music that the cut has to keep and the silencing has to stop short of.
    """
    for i in range(int(last_click / HOP), min(len(env), int(downbeat / HOP) + 1)):
        if env[i] <= CONTENT_DB:
            return _content_after(env, i * HOP)
    return None
