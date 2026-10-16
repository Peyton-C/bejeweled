"""Stem sets built from more than one version of the same song.

Grafting takes real stems of a clean edit and the same stem separated from the explicit
release, and replaces only the stretches where the two differ. A clean edit touches a
few words of one stem, so nearly all of the result is still the real stems: Work Bitch
against Festival's Work Work came to 19 windows and 16.4 s of a 247 s vocal, with the
other three stems untouched.

The two are compared rather than nulled against each other. Festival's vocal and a
separation of the retail master correlate at only 0.39 as waveforms, since they are
different mixes of the same performance, but their spectra agree within a few dB
wherever the words are the same.

Extending lays real stems along another edit of the song, an extended mix or a demo
with a section the album cut, and separates only the stretches the stems lack. The
other version is searched for every offset it lines up with the stems at, each half
second is given the offset that matches, and the ends of every stretch are found by
ear and put on a beat. HOT TO GO's demo against Festival's stems came to two offsets
6.857 s apart, exactly four bars at 140 BPM, with 19.7 s of a 191.7 s song separated
and Festival's own 12.9 s bridge left out.

Assembling is for a song with no real stems at all, only some of its mix, its
instrumental and its acapella. Each part given is one thing less the separator has to
pull out: an acapella is the vocal stem as it stands, an instrumental is separated with
no voice in it to bleed, and a part taken out of the mix leaves the other part behind.
The parts are treated the way separate treats the groups of a surround mix, each
separated on its own and each stem summed back across them.

Say So, with Festival's mixdown as the mix and its stems summed into the parts, scored
13.5, 19.3, 6.9 and 10.7 dB SDR on drums, bass, other and vocals separated by demucs
alone, and 16.9, 21.5, 13.4 and 18.5 with the instrumental beside it. Those parts fit
their mix better than separately mastered releases will.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field, replace

import numpy as np

from .. import demucs as dm
from .. import ffmpeg as ff
from ..stems import Stem, StemSet
from . import separate as sep
from .separate import DEFAULT_SEPARATOR, SEPARATOR_CODES

# The stems are mostly real, but the grafted stretches are a separation and the whole
# must not pass for real stems of the explicit release, so titles are marked by
# default. The plus says separated audio was added to stems that came from elsewhere,
# and keeps a graft apart from a full separation of the same song, which is (RF).
SOURCE_NAME = "combine"
MARK_TITLES_BY_DEFAULT = True

# What a clean edit most often changes, and the only stem grafted unless asked
GRAFT_STEM = "Vocals"

# The coarse search runs on audio averaged down by this much, which finds the offset
# to within a few samples in a fraction of a second, and the chunks below finish it
DECIMATE = 8
CHUNK_SECONDS = 10.0
REFINE_SAMPLES = 64

# A chunk only votes on the offset when the two stems correlate this well in it. On
# Work Bitch the chunks over 0.3 all landed within 5 samples of each other, while those
# under it, mostly hooks where the words differ, strayed by up to 470.
CHUNK_CONFIDENCE = 0.3
MIN_CHUNKS = 3

# Chunks further than this from the common offset mean the two versions are different
# edits or run at different speeds, and one offset cannot line them up
STEP_TOLERANCE = 0.002
STEP_AGREEMENT = 0.8

# Spectra are compared in bands across the range a voice occupies
FFT_SAMPLES = 4096
HOP_SAMPLES = 1024
BAND_EDGES = (100.0, 12000.0)
BANDS = 24

# A frame is compared only where either stem is within 30 dB of the clean stem's
# loudest, so two noise floors are not measured against each other
ACTIVE_RATIO = 1e-3

# The distance is smoothed over about 0.2 s, a little under the shortest word
SMOOTH_FRAMES = 9

# A window is every frame over EDGE_DB around a peak over PEAK_DB. On Work Bitch half
# the frames sit under 3.3 dB and the swapped words peak between 33 and 61, so the peak
# threshold has room either side. The edge is lower so a window takes in the whole
# word rather than its loudest part.
EDGE_DB = 9.0
PEAK_DB = 20.0

# Windows closer than this are one edit
MERGE_SECONDS = 0.15

# A stretch where the donor is this far under the clean stem is something only the
# clean edit has, and grafting there would delete it
ONLY_CLEAN_DB = 20.0

# Taken either side of a window, since a word's attack starts before its spectrum
# diverges and its tail rings on after
PRE_SECONDS = 0.06
POST_SECONDS = 0.12

# Short, because the two stems are not phase-aligned and a long crossfade between them
# combs
FADE_SECONDS = 0.015

# An extension finds every offset the two versions line up at from chunks of the
# other version this long, each searched for across the whole of the stems. One
# offset needs this many chunks behind it, since a chorus heard again elsewhere in
# the song wins the odd chunk on its own.
EXTEND_CHUNK_SECONDS = 5.0
OFFSET_SUPPORT = 2
# Chunks that land within this of each other are one offset, found to the sample
# after. HOT TO GO's chunks strayed by up to 30 ms either way of the true offset,
# from beats that line up a little either side of it too.
CLUSTER_SECONDS = 0.05

# Then every half second of the other version is scored against every offset, and
# the path through them chosen that matches best with fewest changes. A stretch that
# matches at no offset scores what no offset beats there, and a change costs as much
# as this many blocks matching at that score would earn.
BLOCK_SECONDS = 0.5
NO_MATCH_SCORE = 0.2
CHANGE_COST = 1.5

# The path only finds each stretch to within a block or two, so its ends are found
# again by ear: where the stems stop sounding like the other version for longer than
# MATCH_HOLD_SECONDS, over a median that keeps one odd frame from breaking a match
SEARCH_SECONDS = 2.0
MATCH_DB = 6.0
MATCH_MEDIAN_SECONDS = 0.5
MATCH_HOLD_SECONDS = 1.0

# Seams go on a beat of the stems' grid, the nearest one that keeps the real stems
# off audio they do not match, give or take this
SNAP_SECONDS = 0.1
# A grid whose beats hold this much more onset energy than the average spot in the
# bar is a grid; music that drifts or has no beat to speak of leaves seams unsnapped
GRID_STRENGTH = 4.0
GRID_BPM = (70.0, 180.0)

# Two offsets closer than this are one version drifting against the other, since an
# edit moves a song by beats, not by a few milliseconds
EDIT_SECONDS = 0.1

# What is separated around a stretch, so the separator hears it in context and the
# stems either side give each separated stem its level
CONTEXT_SECONDS = 5.0
# How far one separated stem's level may sit from the level the whole mix needed. On
# HOT TO GO the four stems needed between 0.6 dB under and 4.7 over it, so the range
# holds back only a stem matched to bleed, one nearly silent beside the stretch.
STEM_GAIN_RANGE_DB = 6.0

# Separated stems a set has no slot for go here, the guitar into Festival's Lead
SEPARATED_INTO = {"Guitar": "Lead"}

# What an assembly can be given, the mix first since everything is lined up to it
PARTS = ("mix", "instrumental", "acapella")

# An assembly is a separation with fewer unknowns, so it is marked as one, with letters
# for the parts that stood in for the separator's guesses. The mix has none, being what
# a plain separation starts from. Never A and I, which side by side read as AI.
PART_CODES = {"acapella": "AC", "instrumental": "IN"}

# The mix less the instrumental is used as the vocal untouched only when the
# instrumental nulled by this much, and is otherwise separated like any other mix. On
# Say So the sum of Festival's instrument stems nulled 28 dB out of its mixdown, and
# what was left scored 16.3 dB SDR as the vocal untouched against 18.5 separated by
# demucs, so the bar sits over that. Of the synthetic pairs in the tests an exact one
# measures 37 dB, held there by the vocal itself, one whose level dips 1 dB on every
# kick 20 dB, and one a sample out of line 7 dB. No pair has yet been measured that
# nulls cleanly and is not synthetic.
NULL_CLEAN_DB = 30.0

# An acapella is taken out of the mix only when what comes out is within this of all of
# it, and the mix is otherwise separated whole with the acapella as its vocal. A dB
# short leaves a fifth of the vocal behind, and the separator's vocal from a remainder
# goes to Other, so a mix the acapella barely touched put the whole vocal there. 4
# Minutes' acapella runs a sample a second slow against its mix and came out 31 dB
# short, 3 dB short even with the drift followed, and Hot N Cold's 19 dB. The exact
# acapella in the tests comes out whole to within 0.1 dB.
ACAPELLA_FITS_DB = 1.0

# Nothing sung reaches under 70 Hz, so in that band the whole mix is instrumental and
# the null can be measured through the entire song, choruses included, which is where
# two masters of one song part ways
NULL_LOW_BAND = (20.0, 70.0)
NULL_TOP = 16000.0

# The quietest frames of the remainder against the mix, which are the ones with nobody
# singing. A song sung through more than this much of its length has no such frames,
# measures badly and is separated, which is the safe way to be wrong.
NULL_QUIET_PERCENT = 5

# Where voice a separator hears outside the vocal goes. On Say So, whose Festival
# vocal stem holds every voice in the song, demucs still put enough of the instrumental
# in its vocal stem that adding it to the real acapella brought the vocal from exact
# down to 15.4 dB SDR.
VOICE_ELSEWHERE = "Other"

# A part named in a title, as an instrumental's own tag usually has it
_PART_RE = re.compile(
    r"\s*(?:[(\[]\s*(?:instrumental|a\s?c{1,2}app?ella)(?:\s+version)?\s*[)\]]"
    r"|-\s*(?:instrumental|a\s?c{1,2}app?ella)(?:\s+version)?)\s*$", re.IGNORECASE)

# A marker already on the end of a title, such as (FN) or (RF AT)
_MARKER_RE = re.compile(r"\s*\(([A-Z][A-Z +]*)\)$")


@dataclass
class Window:
    """A stretch of a stem, in seconds, where the two versions differ."""

    start: float
    end: float


@dataclass
class Graft:
    """What was grafted into a stem, and what was left alone."""

    stem: str
    offset: float            # seconds the donor runs ahead of the stems
    gain_db: float           # applied to the donor to match the stem's level
    windows: list[Window]
    only_clean: list[Window]  # differences left as they were

    @property
    def seconds(self) -> float:
        """How much of the stem was replaced, with what is taken either side."""
        return sum(w.end - w.start + PRE_SECONDS + POST_SECONDS for w in self.windows)


def title_marker(separator: str = DEFAULT_SEPARATOR) -> str:
    return f"(+{SEPARATOR_CODES[separator]})"


TITLE_MARKER = title_marker()


def mark_title(title: str, marker: str | None, carry_from: str | None = None) -> str:
    """Add a graft's marker to a title, joining the marker the stems already had.

    Festival's Work Work (FN) becomes Work Work (FN +RF) rather than gaining a second
    pair of brackets. `carry_from` is the stems' own title when `title` was given
    apart from it, so the stems' marker still follows the new name.
    """
    carried = _MARKER_RE.search(carry_from if carry_from is not None else title)
    if carry_from is None and carried:
        title = title[:carried.start()]
    inner = [carried.group(1)] if carried else []
    if marker:
        wrapped = re.fullmatch(r"\((.*)\)", marker)
        if not wrapped:
            # A suffix of the user's own is appended as it is
            kept = f" ({inner[0]})" if inner else ""
            return f"{title}{kept} {marker}"
        inner.append(wrapped.group(1))
    return f"{title} ({' '.join(inner)})" if inner else title


# ---------------------------------------------------------------------------- audio

def _decode(ffmpeg: str, path: str, rate: int, stream: str = "0:a:0") -> np.ndarray:
    """One audio stream as stereo float32, resampled to `rate`."""
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-i", path, "-map", stream, "-ac", "2", "-ar", str(rate),
         "-f", "f32le", "-"], capture_output=True)
    if result.returncode != 0:
        lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ff.FFmpegError("\n".join(lines[-12:]) or f"exit {result.returncode}")
    return np.frombuffer(result.stdout, dtype="<f4").reshape(-1, 2)


def _encode(ffmpeg: str, audio: np.ndarray, rate: int, out_path: str) -> str:
    # Float, as the stems it replaces are, so nothing clips where the graft is louder
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", "-",
         "-c:a", "pcm_f32le", out_path],
        input=np.ascontiguousarray(audio, dtype="<f4").tobytes(), capture_output=True)
    if result.returncode != 0:
        lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ff.FFmpegError("\n".join(lines[-12:]) or f"exit {result.returncode}")
    return out_path


def _sample_rate(ffmpeg: str, path: str) -> int:
    for stream in ff.probe(ffmpeg, path).get("streams", []):
        if stream.get("codec_type") == "audio":
            return int(stream["sample_rate"])
    raise ValueError(f"no audio stream in {path}")


# ------------------------------------------------------------------------ alignment

def find_offset(clean: np.ndarray, donor: np.ndarray, rate: int) -> int:
    """How many samples the donor runs ahead: clean[t] lines up with donor[t + offset].

    One offset has to hold for the whole song. Chunks that disagree mean a different
    edit or a different speed, which is refused rather than grafted out of step.
    """
    a, b = clean.mean(axis=1), donor.mean(axis=1)

    def averaged(x):
        usable = len(x) - len(x) % DECIMATE
        return x[:usable].reshape(-1, DECIMATE).mean(axis=1)

    low_a, low_b = averaged(a), averaged(b)
    size = 1 << (len(low_a) + len(low_b)).bit_length()
    found = np.fft.irfft(np.conj(np.fft.rfft(low_a, size)) * np.fft.rfft(low_b, size), size)
    lag = int(np.argmax(found))
    coarse = (lag if lag < size // 2 else lag - size) * DECIMATE

    chunk, reach = int(CHUNK_SECONDS * rate), REFINE_SAMPLES
    # Padded so the first and last chunks of two versions that start together still
    # have room to be searched either side
    b = np.pad(b, reach)
    lags = []
    for start in range(0, len(a) - chunk + 1, chunk):
        first = start + coarse
        if first < 0 or first + chunk + 2 * reach > len(b):
            continue
        x, y = a[start:start + chunk], b[first:first + chunk + 2 * reach]
        scores = np.correlate(y, x, mode="valid")
        best = int(np.argmax(scores))
        scale = np.linalg.norm(x) * np.linalg.norm(y[best:best + chunk])
        if scale and scores[best] / scale >= CHUNK_CONFIDENCE:
            lags.append(coarse - reach + best)

    if len(lags) < MIN_CHUNKS:
        raise ValueError("the two versions do not line up, they may not be the same "
                         "recording")
    offset = int(np.median(lags))
    in_step = sum(abs(lag - offset) <= STEP_TOLERANCE * rate for lag in lags)
    if in_step < STEP_AGREEMENT * len(lags):
        raise ValueError("the two versions drift apart, they are different edits or run "
                         "at different speeds")
    return offset


def _shifted(donor: np.ndarray, offset: int, length: int) -> np.ndarray:
    """The donor on the stems' timeline, silent wherever it has nothing."""
    out = np.zeros((length, 2), dtype=np.float32)
    source, target = max(offset, 0), max(-offset, 0)
    count = min(length - target, len(donor) - source)
    if count > 0:
        out[target:target + count] = donor[source:source + count]
    return out


# ------------------------------------------------------------------------ comparing

def _band_power(audio: np.ndarray, rate: int, edges=None) -> np.ndarray:
    """Power per band per frame, bands by frames. The bands are the ones spectra are
    compared in unless `edges` gives others."""
    mono = np.pad(audio.mean(axis=1), FFT_SAMPLES // 2)
    frames = np.lib.stride_tricks.sliding_window_view(mono, FFT_SAMPLES)[::HOP_SAMPLES]
    window = np.hanning(FFT_SAMPLES).astype(np.float32)
    freqs = np.fft.rfftfreq(FFT_SAMPLES, 1 / rate)
    if edges is None:
        edges = np.geomspace(*BAND_EDGES, BANDS + 1)
    member = np.stack([(freqs >= low) & (freqs < high)
                       for low, high in zip(edges[:-1], edges[1:])]).astype(np.float32)

    out = np.empty((len(member), len(frames)), dtype=np.float32)
    # A few hundred frames at a time, the whole spectrogram at once is gigabytes
    for start in range(0, len(frames), 256):
        spectrum = np.fft.rfft(frames[start:start + 256] * window, axis=1) / window.sum()
        out[:, start:start + 256] = member @ (np.abs(spectrum) ** 2).T
    return out


def _compare(clean: np.ndarray, donor: np.ndarray, rate: int
             ) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """How far apart an aligned donor sounds from the clean audio, frame by frame, in
    dB, with each one's level per frame and the gain that matches the donor's level."""
    a, b = _band_power(clean, rate), _band_power(donor, rate)
    level_a, level_b = a.sum(axis=0), b.sum(axis=0)
    floor = level_a.max() * ACTIVE_RATIO
    both = (level_a > floor) & (level_b > floor)
    if not both.any():
        raise ValueError("the two versions are never playing at the same time")

    # The median ignores the frames that differ, which is what is being looked for
    ratio = np.median(a[:, both] / (b[:, both] + 1e-12), axis=1)
    gain = float(np.sqrt(np.median(ratio)))
    b = b * ratio[:, None]
    level_b = b.sum(axis=0)

    # Weighted by energy, so a band neither stem has much in cannot dominate
    weight = np.maximum(a, b)
    weight = weight / (weight.sum(axis=0) + 1e-12)
    apart = (np.abs(10 * np.log10(a + 1e-9) - 10 * np.log10(b + 1e-9)) * weight).sum(axis=0)
    active = (level_a > floor) | (level_b > floor)
    apart = np.convolve(np.where(active, apart, 0.0),
                        np.ones(SMOOTH_FRAMES) / SMOOTH_FRAMES, mode="same")
    return apart, level_a, level_b, gain


def find_windows(clean: np.ndarray, donor: np.ndarray, rate: int
                 ) -> tuple[list[Window], list[Window], float]:
    """Where an aligned donor differs from the clean stem: the windows worth grafting,
    the ones only the clean stem has, and the gain that matches the donor's level."""
    apart, level_a, level_b, gain = _compare(clean, donor, rate)

    hop = HOP_SAMPLES / rate
    runs: list[list[int]] = []  # first frame, one past the last
    over = apart > EDGE_DB
    frame = 0
    while frame < len(over):
        if not over[frame]:
            frame += 1
            continue
        end = frame
        while end < len(over) and over[end]:
            end += 1
        if runs and (frame - runs[-1][1]) * hop < MERGE_SECONDS:
            runs[-1][1] = end
        else:
            runs.append([frame, end])
        frame = end

    windows, only_clean = [], []
    for first, end in runs:
        if apart[first:end].max() < PEAK_DB:
            continue
        window = Window(first * hop, (end - 1) * hop)
        # The median, since a window's edges catch the words either side of it
        quieter = np.median(10 * np.log10((level_a[first:end] + 1e-12)
                                          / (level_b[first:end] + 1e-12)))
        (only_clean if quieter > ONLY_CLEAN_DB else windows).append(window)
    return windows, only_clean, gain


def splice(clean: np.ndarray, donor: np.ndarray, windows: list[Window], rate: int
           ) -> np.ndarray:
    """The clean stem with the aligned donor crossfaded in over each window."""
    fade = max(1, int(FADE_SECONDS * rate))
    ramp = np.zeros(len(clean), dtype=np.float32)
    for window in windows:
        start = max(0, int((window.start - PRE_SECONDS) * rate))
        end = min(len(clean), int((window.end + POST_SECONDS) * rate))
        ramp[start:end] = 1.0
        rise = ramp[max(0, start - fade):start]
        np.maximum(rise, np.linspace(0, 1, fade, dtype=np.float32)[fade - len(rise):], out=rise)
        fall = ramp[end:end + fade]
        np.maximum(fall, np.linspace(1, 0, fade, dtype=np.float32)[:len(fall)], out=fall)
    ramp = ramp[:, None]
    return clean * (1 - ramp) + donor * ramp


# ---------------------------------------------------------------------------- graft

def graft(stem_set: StemSet, donor_path: str, work_dir: str, stem: str = GRAFT_STEM,
          ffmpeg: str | None = None) -> StemSet:
    """Graft the parts of `donor_path` that differ into one stem of a set.

    `donor_path` is the same stem from the other version of the song, in any timing:
    the offset between the two is found here. The grafted stem, and the mixdown where
    the set has one, are written to `work_dir` and every other stem is left as it is.
    """
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    target = stem_set.require(stem)
    rate = _sample_rate(ffmpeg, target.path)
    clean = _decode(ffmpeg, target.path, rate)
    donor = _decode(ffmpeg, donor_path, rate)

    offset = find_offset(clean, donor, rate)
    donor = _shifted(donor, offset, len(clean))
    windows, only_clean, gain = find_windows(clean, donor, rate)
    grafted = splice(clean, donor * gain, windows, rate)

    os.makedirs(work_dir, exist_ok=True)
    out = _encode(ffmpeg, grafted, rate, os.path.join(work_dir, f"{target.name}.wav"))

    master = stem_set.master
    if master and windows:
        # The mixdown gets the same change the stem did and nothing else. Summing the
        # stems instead would throw away a mastered mixdown, which is not their sum.
        mixdown = _decode(ffmpeg, master, rate).copy()
        count = min(len(mixdown), len(clean))
        mixdown[:count] += grafted[:count] - clean[:count]
        master = _encode(ffmpeg, mixdown, rate, os.path.join(work_dir, "Master.wav"))

    stems = [Stem(s.name, out, s.color) if s is target else s for s in stem_set.stems]
    report = Graft(target.name, offset / rate, 20 * float(np.log10(gain)), windows, only_clean)
    return replace(stem_set, stems=stems, master=master,
                   extra={**stem_set.extra, "graft": report})


# --------------------------------------------------------------------------- extend

@dataclass
class Piece:
    """A stretch of the other version, in seconds on its timeline, and what fills it."""

    start: float
    end: float
    kind: str                # "stems", "separated" or "silence"
    offset: float = 0.0      # for "stems": seconds the other version runs ahead of them


@dataclass
class Extension:
    """How a set was laid out along another version of the song."""

    pieces: list[Piece]
    dropped: list[Window]    # stretches of the stems the other version does not have
    bpm: float | None        # the grid the seams are on, None where there was none
    gains_db: dict[str, float] = field(default_factory=dict)  # on the separated stems

    @property
    def separated(self) -> float:
        return sum(p.end - p.start for p in self.pieces if p.kind == "separated")


def _mono_shifted(audio: np.ndarray, offset: int, length: int) -> np.ndarray:
    out = np.zeros(length, dtype=np.float32)
    source, target = max(offset, 0), max(-offset, 0)
    count = min(length - target, len(audio) - source)
    if count > 0:
        out[target:target + count] = audio[source:source + count]
    return out


def candidate_offsets(stems: np.ndarray, other: np.ndarray, rate: int) -> list[int]:
    """Every offset the other version lines up with the stems at, to the sample:
    other[t] is stems[t - offset] wherever that offset holds."""
    def averaged(x):
        usable = len(x) - len(x) % DECIMATE
        return x[:usable].reshape(-1, DECIMATE).mean(axis=1)

    a, b = stems.mean(axis=1), other.mean(axis=1)
    low_a, low_b = averaged(a), averaged(b)
    chunk = int(EXTEND_CHUNK_SECONDS * rate / DECIMATE)
    if len(low_a) < chunk or len(low_b) < chunk:
        return []
    size = 1 << (len(low_a) + chunk).bit_length()
    spectrum = np.fft.rfft(low_a, size)
    # The stems' level under every chunk-long window, to score each lag on its own
    energy = np.concatenate([[0.0], np.cumsum(low_a.astype(np.float64) ** 2)])
    window_norm = np.sqrt(np.maximum(energy[chunk:] - energy[:-chunk], 0))

    found = []  # (offset, donor chunk start), at the full rate
    for start in range(0, len(low_b) - chunk + 1, chunk // 2):
        x = low_b[start:start + chunk]
        norm = np.linalg.norm(x)
        if not norm:
            continue
        scores = np.fft.irfft(np.conj(np.fft.rfft(x, size)) * spectrum, size)
        scores = scores[:len(window_norm)] / (window_norm * norm + 1e-12)
        best = int(np.argmax(scores))
        if scores[best] >= CHUNK_CONFIDENCE:
            found.append(((start - best) * DECIMATE, start * DECIMATE))

    found.sort()
    clusters, tolerance = [], CLUSTER_SECONDS * rate
    for offset, start in found:
        if clusters and offset - clusters[-1][-1][0] <= tolerance:
            clusters[-1].append((offset, start))
        else:
            clusters.append([(offset, start)])

    offsets, reach, full = [], int(tolerance) + DECIMATE, chunk * DECIMATE
    for cluster in clusters:
        if len(cluster) < OFFSET_SUPPORT:
            continue
        middle = int(np.median([offset for offset, _ in cluster]))
        total = np.zeros(2 * reach + 1)
        for _, start in cluster:
            x = b[start:start + full]
            y = _mono_shifted(a, start - middle - reach, len(x) + 2 * reach)
            n = len(x) + len(y)
            total += np.fft.irfft(np.conj(np.fft.rfft(x, n)) * np.fft.rfft(y, n), n)[:2 * reach + 1]
        offset = middle + reach - int(np.argmax(total))
        if not any(abs(offset - o) <= DECIMATE for o in offsets):
            offsets.append(offset)
    return offsets


def _block_scores(stems: np.ndarray, other: np.ndarray, offsets: list[int], rate: int
                  ) -> tuple[np.ndarray, np.ndarray]:
    """How well each block of the other version matches the stems at each offset, as
    a correlation, and which blocks have anything in them to match."""
    block = int(BLOCK_SECONDS * rate)
    count = len(other) // block + (len(other) % block > 0)
    b = np.zeros(count * block, dtype=np.float32)
    b[:len(other)] = other.mean(axis=1)
    b = b.reshape(count, block)
    power = (b.astype(np.float64) ** 2).sum(axis=1)
    audible = power > power.max() * ACTIVE_RATIO

    a = stems.mean(axis=1)
    scores = np.zeros((len(offsets), count))
    for row, offset in enumerate(offsets):
        x = _mono_shifted(a, -offset, count * block).reshape(count, block)
        dot = (x * b).sum(axis=1, dtype=np.float64)
        norm = np.sqrt((x.astype(np.float64) ** 2).sum(axis=1) * power)
        scores[row] = np.where(norm > 0, dot / (norm + 1e-12), 0.0)
    return scores, audible


def _best_path(scores: np.ndarray, audible: np.ndarray) -> np.ndarray:
    """The offset each block is filled from, -1 for none, chosen to match best with
    fewest changes."""
    states = len(scores) + 1
    reward = np.vstack([scores, np.full(scores.shape[1], NO_MATCH_SCORE)])
    # Silence matches anything, so it never decides where a change goes
    reward[:, ~audible] = NO_MATCH_SCORE
    total = reward[:, 0].copy()
    came = np.zeros(reward.shape, dtype=np.int64)
    for column in range(1, reward.shape[1]):
        stay, leave = total, total.max() - CHANGE_COST
        best = int(np.argmax(total))
        came[:, column] = np.where(stay >= leave, np.arange(states), best)
        total = np.maximum(stay, leave) + reward[:, column]
    path = np.empty(reward.shape[1], dtype=np.int64)
    path[-1] = int(np.argmax(total))
    for column in range(reward.shape[1] - 1, 0, -1):
        path[column - 1] = came[path[column], column]
    return np.where(path == states - 1, -1, path)


def _held(mask: np.ndarray, frames: int) -> np.ndarray:
    """Frames from which `mask` holds for at least `frames` frames running."""
    run = np.zeros(len(mask) + 1, dtype=np.int64)
    for index in range(len(mask) - 1, -1, -1):
        run[index] = run[index + 1] + 1 if mask[index] else 0
    return run[:-1] >= frames


def _matching(stems: np.ndarray, other: np.ndarray, offset: int, low: int, high: int,
              start: int, end: int, rate: int) -> tuple[int, int] | None:
    """Where the stems at `offset` sound like the other version, found from the rough
    stretch [start, end) by walking each end out while they still match, or in until
    they first match for good, never beyond [low, high).

    Only the ends move, since a word that differs inside is a graft's business rather
    than a reason to separate everything. HOT TO GO's opening bars scored too low to
    start the path, and walking out from it found them matching from the first beat.
    """
    if high - low < FFT_SAMPLES:
        return None
    clean = _shifted(stems, low - offset, high - low)
    try:
        apart = _compare(clean, other[low:high], rate)[0]
    except ValueError:
        return None
    width = int(MATCH_MEDIAN_SECONDS * rate / HOP_SAMPLES) | 1
    padded = np.pad(apart, width // 2, mode="edge")
    apart = np.median(np.lib.stride_tricks.sliding_window_view(padded, width), axis=1)
    same = apart < MATCH_DB
    hold = max(1, int(MATCH_HOLD_SECONDS * rate / HOP_SAMPLES))
    held_on, held_back = _held(same, hold), _held(same[::-1], hold)[::-1]

    first = min(len(same) - 1, max(0, (start - low) // HOP_SAMPLES))
    last = min(len(same) - 1, max(0, (end - low) // HOP_SAMPLES - 1))
    if held_on[first]:
        while first > 0 and same[first - 1]:
            first -= 1
    else:
        ahead = np.flatnonzero(held_on[first:last + 1])
        if not len(ahead):
            return None
        first += ahead[0]
    if held_back[last]:
        while last < len(same) - 1 and same[last + 1]:
            last += 1
    else:
        behind = np.flatnonzero(held_back[first:last + 1])
        if not len(behind):
            return None
        last = first + behind[-1]
    if last <= first:
        return None
    return low + first * HOP_SAMPLES, min(high, low + (last + 1) * HOP_SAMPLES)


def beat_grid(audio: np.ndarray, rate: int, bpm: float | None = None
              ) -> tuple[float, float] | None:
    """The beat as (seconds per beat, seconds to the first), fitted to the onsets of
    `audio`, or None where they keep no steady beat. A tagged BPM narrows the search.

    Onsets are rises in level rather than in log level, which let HOT TO GO's hi-hats
    between the beats outweigh its kicks and put the grid on the off-beat.
    """
    hop = 128
    mono = audio.mean(axis=1).astype(np.float64)
    usable = len(mono) - len(mono) % hop
    if usable < 8 * rate:
        return None
    level = np.sqrt((mono[:usable] ** 2).reshape(-1, hop).mean(axis=1))
    onsets = np.maximum(np.diff(level), 0)
    frames = rate / hop
    index = np.arange(len(onsets))
    bins = 200

    # The period is the one whose first eight multiples the onsets repeat at most. A
    # single lag put HOT TO GO at 93.3 against its 140, and the multiples both pin
    # the period to a fraction of a frame and favour the beat over half or twice it.
    centred = onsets - onsets.mean()
    spectrum = np.fft.rfft(centred, 2 * len(centred))
    lagged = np.fft.irfft(np.abs(spectrum) ** 2)[:len(centred)]
    low, high = GRID_BPM
    if bpm and low <= bpm <= high:
        low, high = bpm * 0.97, bpm * 1.03
    periods = np.arange(60 * frames / high, 60 * frames / low, 0.01)
    multiples = np.arange(1, 9)[:, None] * periods
    comb = np.interp(multiples, np.arange(len(lagged)), lagged, right=0).sum(axis=0)
    guess = 60 * frames / periods[int(np.argmax(comb))]

    def fold(tempo):
        period = 60 * frames / tempo
        folded, _ = np.histogram(index % period, bins=bins, range=(0, period), weights=onsets)
        return folded.max() / (folded.mean() + 1e-12), folded, period

    # Then the fold that stacks the beats tightest, which finds where they fall
    tempo = max(np.geomspace(guess * 0.998, guess * 1.002, 81), key=lambda t: fold(t)[0])
    strength, folded, period = fold(tempo)
    if strength < GRID_STRENGTH:
        return None
    # A rise in level shows in the frame after the one it starts in
    phase = ((int(np.argmax(folded)) + 0.5) / bins * period + 1) / frames
    return 60 / tempo, phase


def _snap(seconds: float, grid, later: bool) -> float:
    """The beat a seam at `seconds` of the stems goes on: the first one not much
    before it for a stretch of stems starting, the last one not much after it for one
    ending."""
    if grid is None:
        return seconds
    period, phase = grid
    if later:
        return phase + np.ceil((seconds - SNAP_SECONDS - phase) / period) * period
    return phase + np.floor((seconds + SNAP_SECONDS - phase) / period) * period


def lay_out(stems: np.ndarray, other: np.ndarray, rate: int, grid=None
            ) -> list[tuple[int, int, int | None]]:
    """The other version as stretches of (start, end, offset) in its samples, the
    offset None where the stems have nothing that sounds like it.

    `stems` is the mix of the stems, `grid` the beat their seams go on.
    """
    offsets = candidate_offsets(stems, other, rate)
    if not offsets:
        raise ValueError("the two versions do not line up, they may not be the same "
                         "recording")
    scores, audible = _block_scores(stems, other, offsets, rate)
    path = _best_path(scores, audible)
    block = int(BLOCK_SECONDS * rate)

    runs = []  # rough (start, end, offset) for each stretch at one offset
    for index, state in enumerate(path):
        if state < 0:
            continue
        if runs and runs[-1][2] == offsets[state] and runs[-1][1] == index * block:
            runs[-1][1] = (index + 1) * block
        else:
            runs.append([index * block, (index + 1) * block, offsets[state]])

    search, length = int(SEARCH_SECONDS * rate), len(other)
    found = []
    for index, (start, end, offset) in enumerate(runs):
        # Out as far as the neighbouring stretches, and only where the stems have
        # audio at this offset
        before = runs[index - 1][1] if index else 0
        after = runs[index + 1][0] if index + 1 < len(runs) else length
        low = max(0, offset, min(before, start - search))
        high = min(length, len(stems) + offset, max(after, end + search))
        where = _matching(stems, other, offset, low, high, start, end, rate)
        if where:
            found.append([*where, offset])

    # Ends found by ear may overlap where both offsets sound right, as a repeated
    # bar does, and the line is drawn halfway
    for left, right in zip(found, found[1:]):
        if left[1] > right[0]:
            left[1] = right[0] = (left[1] + right[0]) // 2

    floor = float(np.max(np.abs(other), initial=0)) * np.sqrt(ACTIVE_RATIO)

    def silent(start, end):
        """A gap with nothing audible, beside which a seam stays where it was found,
        or the last beat's ring would be cut short for a beat it does not need."""
        return end > start and np.max(np.abs(other[start:end]), initial=0) < floor

    snapped = []
    for index, (start, end, offset) in enumerate(found):
        before = found[index - 1][1] if index else 0
        after = found[index + 1][0] if index + 1 < len(found) else length
        first, last = start, end
        if start > 0 and not silent(before, start):
            first = round(_snap((start - offset) / rate, grid, later=True) * rate) + offset
        if end < length and not silent(end, after):
            last = round(_snap((end - offset) / rate, grid, later=False) * rate) + offset
        first, last = max(first, 0, offset), min(last, length, len(stems) + offset)
        # Two stretches seamed on one beat can miss each other by a few samples, the
        # grid not being exact to the sample across two offsets, and nothing that short
        # is worth separating
        if snapped and first - snapped[-1][1] < SNAP_SECONDS * rate:
            first = snapped[-1][1]
        if last > first:
            snapped.append((first, last, offset))
    if not snapped:
        raise ValueError("the two versions do not line up, they may not be the same "
                         "recording, or one may run at another speed")

    offsets_used = sorted({offset for _, _, offset in snapped})
    for a, b in zip(offsets_used, offsets_used[1:]):
        if b - a < EDIT_SECONDS * rate:
            raise ValueError("the two versions drift apart, they run at different "
                             "speeds")

    layout, at = [], 0
    for start, end, offset in snapped:
        if start > at:
            layout.append((at, start, None))
        layout.append((start, end, offset))
        at = end
    if at < length:
        layout.append((at, length, None))
    return layout


def _weights(bounds: list[int], length: int, fade: int) -> list[np.ndarray]:
    """One weight per stretch, summing to one everywhere, each crossfade ending on the
    seam so the beat there comes whole from the stretch it starts."""
    edges = [0] + bounds + [length]
    ramps = []
    for seam in edges:
        ramp = np.clip((np.arange(length) - (seam - fade)) / fade, 0, 1).astype(np.float32)
        ramps.append(ramp)
    ramps[0][:] = 1
    ramps[-1][:] = 0
    return [(ramps[i] - ramps[i + 1])[:, None] for i in range(len(edges) - 1)]


def extend(stem_set: StemSet, other_path: str, work_dir: str,
           separator: str = DEFAULT_SEPARATOR, model: str | None = None,
           device: str | None = None, progress=None, ffmpeg: str | None = None) -> StemSet:
    """Lay real stems along another version of the song, with what only that version
    has separated from it.

    The result follows the other version from start to end: the stems wherever they
    sound like it, at whatever offset that takes, and a separation of the other
    version wherever nothing in the stems does. Each stretch is separated with a few
    seconds either side, which set each separated stem's level against the real one.
    The mixdown is the stems' own, with the other version's mix in the separated
    stretches.
    """
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    rate = _sample_rate(ffmpeg, stem_set.stems[0].path)
    real = {s.name: _decode(ffmpeg, s.path, rate) for s in stem_set.stems}
    stem_length = max(len(audio) for audio in real.values())
    real = {name: _shifted(audio, 0, stem_length) for name, audio in real.items()}
    if stem_set.master:
        mixdown = _shifted(_decode(ffmpeg, stem_set.master, rate), 0, stem_length)
    else:
        mixdown = sum(real.values())
    other = _decode(ffmpeg, other_path, rate)
    length = len(other)

    drums = stem_set.get("Drums")
    grid = beat_grid(real[drums.name] if drums else mixdown, rate, stem_set.bpm)
    layout = lay_out(mixdown, other, rate, grid)

    def from_stems(audio):
        out = np.zeros((length, 2), dtype=np.float32)
        for start, end, offset in layout:
            if offset is not None:
                out[start:end] = _shifted(audio, start - offset, end - start)
        return out

    stems_mask = np.zeros(length, dtype=bool)
    for start, end, offset in layout:
        stems_mask[start:end] = offset is not None
    if not stems_mask.any():
        raise ValueError("the two versions do not line up, they may not be the same "
                         "recording")
    placed_mix = from_stems(mixdown)
    mix_gain = float(np.sqrt(np.sum(placed_mix[stems_mask].astype(np.float64) ** 2)
                             / (np.sum(other[stems_mask].astype(np.float64) ** 2) + 1e-20)))

    # A stretch of the other version with nothing audible in it needs no separating
    floor = float(np.max(np.abs(other))) * np.sqrt(ACTIVE_RATIO)
    kinds = []
    for start, end, offset in layout:
        if offset is not None:
            kinds.append("stems")
        elif np.max(np.abs(other[start:end]), initial=0) < floor:
            kinds.append("silence")
        else:
            kinds.append("separated")

    stretches = [(start, end) for (start, end, _), kind in zip(layout, kinds)
                 if kind == "separated"]

    def staged(index):
        if not progress:
            return None
        return lambda done, total, *stage: (
            progress(done, total, *stage) if stage
            else progress(index * total + done, len(stretches) * total))

    placed = {name: from_stems(audio) for name, audio in real.items()}
    sources = {name: np.zeros((length, 2), dtype=np.float32) for name in real}
    gains_db: dict[str, float] = {}
    models = None
    os.makedirs(work_dir, exist_ok=True)
    scratch = os.path.join(work_dir, ".extend")
    os.makedirs(scratch, exist_ok=True)
    context = int(CONTEXT_SECONDS * rate)
    names = {name.lower(): name for name in real}
    try:
        for index, (start, end) in enumerate(stretches):
            low, high = max(0, start - context), min(length, end + context)
            clip = _encode(ffmpeg, other[low:high], rate,
                           os.path.join(scratch, f"stretch {index}.wav"))
            separated = sep.separate(clip, os.path.join(scratch, f"stretch {index}"),
                                     separator=separator, model=model, device=device,
                                     progress=staged(index), ffmpeg=ffmpeg)
            models = separated.source
            heard: dict[str, np.ndarray] = {}
            for stem in separated.stems:
                into = names.get(SEPARATED_INTO.get(stem.name, stem.name).lower()) \
                    or names.get(stem.name.lower()) or names.get(VOICE_ELSEWHERE.lower())
                if into is None:
                    continue
                audio = _shifted(_decode(ffmpeg, stem.path, rate), 0, high - low)
                heard[into] = heard[into] + audio if into in heard else audio

            # The stems either side, inside the context, set each separated stem's level
            beside = stems_mask[low:high].copy()
            beside[start - low:end - low] = False
            mix_power = float(np.sum(placed_mix[low:high][beside].astype(np.float64) ** 2))
            low_gain, high_gain = (mix_gain * 10 ** (sign * STEM_GAIN_RANGE_DB / 20)
                                   for sign in (-1, 1))
            for name, audio in heard.items():
                want = float(np.sum(placed[name][low:high][beside].astype(np.float64) ** 2))
                have = float(np.sum(audio[beside].astype(np.float64) ** 2))
                gain = mix_gain
                # A stem all but silent beside the stretch has only bleed to match
                if want > mix_power * ACTIVE_RATIO and have > 0:
                    gain = float(np.clip(np.sqrt(want / have), low_gain, high_gain))
                gains_db[name] = 20 * float(np.log10(gain))
                sources[name][low:high] = audio * np.float32(gain)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        if os.path.isdir(work_dir) and not os.listdir(work_dir):
            os.rmdir(work_dir)
        raise
    shutil.rmtree(scratch, ignore_errors=True)

    fade = max(1, int(FADE_SECONDS * rate))
    weights = _weights([end for _, end, _ in layout[:-1]], length, fade)

    def joined(stems_audio, separated_audio):
        out = np.zeros((length, 2), dtype=np.float32)
        for (start, end, offset), kind, weight in zip(layout, kinds, weights):
            if kind == "stems":
                # Taken past its own ends by a fade, so the crossfade has it to fade
                lead = max(0, start - fade)
                piece = np.zeros((length, 2), dtype=np.float32)
                piece[lead:end] = _shifted(stems_audio, lead - offset, end - lead)
                out += piece * weight
            elif kind == "separated":
                out += separated_audio * weight
        return out

    stems = [Stem(s.name, _encode(ffmpeg, joined(real[s.name], sources[s.name]), rate,
                                  os.path.join(work_dir, f"{s.name}.wav")), s.color)
             for s in stem_set.stems]
    master = _encode(ffmpeg, joined(mixdown, other * np.float32(mix_gain)), rate,
                     os.path.join(work_dir, "Master.wav"))

    pieces = [Piece(start / rate, end / rate, kind, (offset or 0) / rate)
              for (start, end, offset), kind in zip(layout, kinds)]
    used = sorted((p.start - p.offset, p.end - p.offset) for p in pieces if p.kind == "stems")
    dropped, at = [], 0.0
    for start, end in used + [(stem_length / rate, stem_length / rate)]:
        if start - at >= BLOCK_SECONDS:
            dropped.append(Window(at, start))
        at = max(at, end)
    report = Extension(pieces, dropped, 60 / grid[0] if grid else None, gains_db)
    source = f"stems laid along {os.path.basename(other_path)}"
    if models:
        source += f", {report.separated:.1f}s separated by {models}"
    return replace(stem_set, stems=stems, master=master, source=source,
                   extra={**stem_set.extra, "extension": report, "separator": separator})


# ------------------------------------------------------------------------- assemble

@dataclass
class Assembly:
    """What a set was assembled from, and how each part was used."""

    parts: list[str]                # which of the three were given
    vocals: str                     # "acapella", "null" or "separated"
    separated: list[str]            # what went through the separator
    offsets: dict[str, float]       # seconds each part runs ahead of the mix
    gains_db: dict[str, float]      # applied to each part to match the mix
    null_db: float | None = None    # how far the instrumental nulled, where it was tried


def assembly_marker(parts, separator: str = DEFAULT_SEPARATOR) -> str:
    """The marker for a set this separator cut with these parts to help it."""
    letters = [SEPARATOR_CODES[separator]]
    letters += [code for part, code in PART_CODES.items() if part in parts]
    return f"({' '.join(letters)})"


def given_parts(mix: str | None = None, instrumental: str | None = None,
                acapella: str | None = None) -> dict[str, str]:
    """The parts that were given, by name in PARTS order. One alone is a job for
    separate, or no job at all."""
    given = {name: os.path.abspath(path)
             for name, path in zip(PARTS, (mix, instrumental, acapella)) if path}
    if len(given) < 2:
        raise ValueError("assembling takes at least two of a mix, an instrumental and "
                         "an acapella")
    for path in given.values():
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no such file: {path}")
    return given


def plain_title(title: str) -> str:
    """A title without the part it names: Song (Instrumental) is a part of Song."""
    return _PART_RE.sub("", title) or title


def fit_gain(mix: np.ndarray, part: np.ndarray) -> float:
    """The gain at which an aligned part comes out of the mix most completely.

    Parts are often released louder than they sit in the mix, an instrumental having
    more room under its limiter. Whatever else is in the mix does not correlate with
    the part over a whole song, so it does not pull the fit. Neither does whatever of
    the part itself fails to line up, so this is the gain to subtract at, and reads
    under the level the part sits at wherever the two are not one waveform.
    """
    power = float(np.vdot(part, part))
    return float(np.vdot(mix, part)) / power if power else 1.0


def fit_levels(mix: np.ndarray, parts: dict[str, np.ndarray], rate: int) -> dict[str, float]:
    """The gain that brings each aligned part to the level it sits at in the mix.

    The fit is on power in bands and not on the waveform, since a part from another
    master, or one that creeps a few samples against the mix over the song, stops
    correlating long before it stops being the same sound at the same level. Hot N
    Cold's acapella wanders 30 samples either way of its mix and correlates with it at
    0.05, which fit_gain read as 21.5 dB down where this reads 2.7, and the vocal came
    out 19 dB under two separations of the same mix. Its instrumental, 7 samples out
    at either end, read 3.3 dB down and 2.0 here, which is also what the intro measures
    with nobody singing over it.

    Both parts account for the whole mix between them. One alone leaves the rest of
    the song, which is taken as a level of its own in each band, or all of it would
    count towards the part. What of the rest still rises and falls with the part reads
    as the part, 0.2 dB of Hot N Cold's acapella and 0.3 of its instrumental.
    """
    def power(audio):
        # A channel at a time, a part out of phase between the sides is still there
        return sum(_band_power(audio[:, [side]], rate) for side in (0, 1)).astype(np.float64)

    m = power(mix)
    p = [power(part) for part in parts.values()]
    # Every band counts alike, or the fit is of the bass alone
    weight = 1 / (m.mean(axis=1, keepdims=True) + 1e-20)
    if len(p) == 1:
        m, p = m - m.mean(axis=1, keepdims=True), [p[0] - p[0].mean(axis=1, keepdims=True)]
    squared = np.linalg.lstsq(np.stack([(x * weight).ravel() for x in p], axis=1),
                              (m * weight).ravel(), rcond=None)[0]
    # A part whose power the mix does not follow at all has no level to find this way
    return {name: float(np.sqrt(g)) if g > 0 else abs(fit_gain(mix, part))
            for (name, part), g in zip(parts.items(), squared)}


def null_depth(mix: np.ndarray, remainder: np.ndarray, rate: int) -> float:
    """How many dB the instrumental nulled out of the mix by, leaving `remainder`.

    The worse of two measures, since each misses what the other catches. Under 70 Hz
    catches a master whose level moves with the kick, which nulls well in a quiet intro
    and badly in every chorus. The quietest frames across the whole spectrum catch a
    pair that is a fraction of a sample apart or differently equalised, which nulls
    well in the bass and nowhere else.
    """
    edges = (*NULL_LOW_BAND, NULL_TOP)

    def power(audio):
        # A channel at a time, a difference between the sides vanishes from their mean
        return sum(_band_power(audio[:, [side]], rate, edges) for side in (0, 1))

    m, r = power(mix), power(remainder)
    level_m, level_r = m.sum(axis=0), r.sum(axis=0)
    active = level_m > level_m.max() * ACTIVE_RATIO
    if not active.any():
        return 0.0
    quiet = np.percentile(level_r[active] / level_m[active], NULL_QUIET_PERCENT)
    depth = -10 * float(np.log10(quiet + 1e-12))
    # A song with no bass to speak of has nothing there to measure, or to pump
    if m[0].sum() > level_m.sum() * 1e-4:
        depth = min(depth, 10 * float(np.log10(m[0].sum() / (r[0].sum() + 1e-12))))
    return depth


def _stereo_info(ffmpeg: str, name: str, path: str) -> dict:
    info = ff.probe(ffmpeg, path)
    if int(sep._audio_stream(info).get("channels") or 0) > 2:
        # Lining up and nulling are done on two channels, and a fold of a surround mix
        # is not the stereo mix its instrumental or acapella was made from
        raise ValueError(f"the {name} is not stereo, and only stereo parts can be assembled")
    return info


def assemble(work_dir: str, mix: str | None = None, instrumental: str | None = None,
             acapella: str | None = None, separator: str = DEFAULT_SEPARATOR,
             model: str | None = None, device: str | None = None, progress=None,
             ffmpeg: str | None = None) -> StemSet:
    """Build a StemSet from any two or all of a song's mix, instrumental and acapella.

    With a mix, each part is lined up to it and brought to its level, and the mix is
    the mixdown. Without one the two parts cannot be lined up, having nothing in
    common, so they are taken as starting together and their sum is the mixdown.

    An instrumental is separated for every stem. An acapella is the vocal stem. A mix
    missing one of them has the other taken out of it, and what remains is separated
    too, unless it is the mix less an instrumental that nulled cleanly, which is the
    vocal as it stands. An acapella that does not come out of its mix is left in, and
    the mix is separated whole for everything but the vocal. Each stem is the sum of what every separation heard as that
    stem, so the stems sum to the mix as nearly as the separator's own do.
    """
    given = given_parts(mix, instrumental, acapella)
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    # Every separator writes at this rate, whatever it was given
    rate = dm.SAMPLE_RATE
    info = {name: _stereo_info(ffmpeg, name, path) for name, path in given.items()}
    audio = {name: _decode(ffmpeg, path, rate) for name, path in given.items()}

    offsets, gains, whole_mix = {}, {}, False
    if "mix" in audio:
        length = len(audio["mix"])
        for name in PARTS[1:]:
            if name not in audio:
                continue
            try:
                offset = find_offset(audio["mix"], audio[name], rate)
            except ValueError as e:
                raise ValueError(f"the {name} against the mix: {e}") from None
            audio[name] = _shifted(audio[name], offset, length)
            offsets[name] = offset / rate
        parts = {name: audio[name] for name in offsets}
        fitted = {name: fit_gain(audio["mix"], part) for name, part in parts.items()}
        levels = fit_levels(audio["mix"], parts, rate)
        if len(parts) == 2:
            # Nothing is taken out of the mix when both parts are given, so they only
            # have to sit at the right level, not null
            fitted = levels
        elif "acapella" in parts and (
                abs(fitted["acapella"]) * 10 ** (ACAPELLA_FITS_DB / 20) < levels["acapella"]):
            fitted, whole_mix = levels, True
        for name, gain in fitted.items():
            audio[name] = parts[name] * np.float32(gain)
            gains[name] = 20 * float(np.log10(abs(gain) + 1e-12))
    else:
        length = max(len(part) for part in audio.values())
        audio = {name: _shifted(part, 0, length) for name, part in audio.items()}

    vocal, vocals_from, null_db = audio.get("acapella"), "acapella", None
    queue = {}
    if "instrumental" in audio:
        queue["instrumental"] = audio["instrumental"]
        if vocal is None:
            remainder = audio["mix"] - audio["instrumental"]
            null_db = null_depth(audio["mix"], remainder, rate)
            if null_db >= NULL_CLEAN_DB:
                vocal, vocals_from = remainder, "null"
            else:
                queue["remainder"], vocals_from = remainder, "separated"
    elif whole_mix:
        queue["mix"] = audio["mix"]
    else:
        queue["remainder"] = audio["mix"] - vocal

    def staged(index):
        if not progress:
            return None
        return lambda done, total, *stage: (
            progress(done, total, *stage) if stage
            else progress(index * total + done, len(queue) * total))

    os.makedirs(work_dir, exist_ok=True)
    scratch = os.path.join(work_dir, ".assemble")
    os.makedirs(scratch, exist_ok=True)
    try:
        sums: dict[str, np.ndarray] = {}
        models = None
        for index, (name, part) in enumerate(queue.items()):
            path = _encode(ffmpeg, part, rate, os.path.join(scratch, f"{name}.wav"))
            separated = sep.separate(path, os.path.join(scratch, name), separator=separator,
                                     model=model, device=device, progress=staged(index),
                                     ffmpeg=ffmpeg)
            models = separated.source
            names = separated.names()
            for stem in separated.stems:
                heard = _shifted(_decode(ffmpeg, stem.path, rate), 0, length)
                into = stem.name
                # An acapella that would not come out of the mix is still the vocal,
                # and what the separator heard of it in the mix is the same vocal again
                if into == GRAFT_STEM and name == "mix":
                    continue
                # The parts already say where the vocal is, so voice heard anywhere
                # else is the separator's mistake and stays with the instruments
                if into == GRAFT_STEM and VOICE_ELSEWHERE in names and not (
                        name == "remainder" and vocal is None):
                    into = VOICE_ELSEWHERE
                sums[into] = sums[into] + heard if into in sums else heard
        if vocal is not None:
            sums[GRAFT_STEM] = sums[GRAFT_STEM] + vocal if GRAFT_STEM in sums else vocal

        stems = [Stem(name, _encode(ffmpeg, data, rate, os.path.join(work_dir, f"{name}.wav")))
                 for name, data in sums.items()]
        master = given.get("mix")
        if master is None:
            master = _encode(ffmpeg, audio["instrumental"] + audio["acapella"], rate,
                             os.path.join(work_dir, "Master.wav"))
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        if os.path.isdir(work_dir) and not os.listdir(work_dir):
            os.rmdir(work_dir)
        raise
    shutil.rmtree(scratch, ignore_errors=True)

    # The mix knows the song best, and a part's own tags name the part
    first = next(iter(given))
    tags = sep.read_tags(info[first])
    title = tags["title"] or os.path.splitext(os.path.basename(given[first]))[0]
    report = Assembly(list(given), vocals_from, list(queue), offsets, gains, null_db)
    return StemSet(
        title=title if first == "mix" else plain_title(title),
        artist=tags["artist"],
        album=tags["album"],
        year=tags["year"],
        bpm=tags["bpm"],
        key=tags["key"],
        genre=tags["genre"],
        cover=sep._extract_cover(ffmpeg, given[first], info[first], work_dir),
        stems=stems,
        master=master,
        source=f"{models}, assembled from {' and '.join(given)}",
        extra={"assembly": report, "separator": separator},
    )
