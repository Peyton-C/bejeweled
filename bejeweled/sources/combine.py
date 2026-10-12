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
from dataclasses import dataclass, replace

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
