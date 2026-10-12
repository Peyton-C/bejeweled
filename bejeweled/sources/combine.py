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
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, replace

import numpy as np

from .. import ffmpeg as ff
from ..stems import Stem, StemSet
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

def _band_power(audio: np.ndarray, rate: int) -> np.ndarray:
    """Power per band per frame, bands by frames."""
    mono = np.pad(audio.mean(axis=1), FFT_SAMPLES // 2)
    frames = np.lib.stride_tricks.sliding_window_view(mono, FFT_SAMPLES)[::HOP_SAMPLES]
    window = np.hanning(FFT_SAMPLES).astype(np.float32)
    freqs = np.fft.rfftfreq(FFT_SAMPLES, 1 / rate)
    edges = np.geomspace(*BAND_EDGES, BANDS + 1)
    member = np.stack([(freqs >= low) & (freqs < high)
                       for low, high in zip(edges[:-1], edges[1:])]).astype(np.float32)

    out = np.empty((BANDS, len(frames)), dtype=np.float32)
    # A few hundred frames at a time, the whole spectrogram at once is gigabytes
    for start in range(0, len(frames), 256):
        spectrum = np.fft.rfft(frames[start:start + 256] * window, axis=1) / window.sum()
        out[:, start:start + 256] = member @ (np.abs(spectrum) ** 2).T
    return out


def find_windows(clean: np.ndarray, donor: np.ndarray, rate: int
                 ) -> tuple[list[Window], list[Window], float]:
    """Where an aligned donor differs from the clean stem: the windows worth grafting,
    the ones only the clean stem has, and the gain that matches the donor's level."""
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
