"""Grafting: lining two versions of a stem up, finding where they differ, splicing."""
import os

import numpy as np
import pytest

from bejeweled import ffmpeg as ff
from bejeweled import jobs
from bejeweled.sources import combine
from bejeweled.stems import Stem, StemSet

RATE = 44100
SECONDS = 40
OFFSET = 5000
SWAPPED = (20.0, 20.4)
DONOR_GAIN = 0.8


def _word(rng, pitch, length):
    """A burst with a few harmonics and a little noise, shaped like a sung word."""
    t = np.arange(length) / RATE
    tone = sum(np.sin(2 * np.pi * pitch * n * t) / n for n in range(1, 6))
    return (0.2 * tone + 0.01 * rng.standard_normal(length)) * np.hanning(length)


def _vocal(swap: bool):
    """Forty seconds of words, one every half second, optionally with one swapped."""
    rng = np.random.default_rng(7)
    out = np.zeros(SECONDS * RATE)
    for index in range(SECONDS * 2):
        start, length = int(index * 0.5 * RATE), int(0.4 * RATE)
        pitch = 180 + 40 * (index * 7 % 11)
        word = _word(rng, pitch, length)
        if swap and SWAPPED[0] <= index * 0.5 < SWAPPED[1]:
            word = _word(rng, 1900, length)
        out[start:start + length] = word
    return np.stack([out, out], axis=1).astype(np.float32)


@pytest.fixture(scope="module")
def clean():
    return _vocal(swap=False)


@pytest.fixture(scope="module")
def donor():
    """The other version: one word different, quieter, and starting later."""
    late = np.zeros((OFFSET, 2), dtype=np.float32)
    return np.concatenate([late, _vocal(swap=True) * DONOR_GAIN])


@pytest.fixture(scope="module")
def ffmpeg():
    try:
        return ff.find_ffmpeg()
    except ff.FFmpegError:
        pytest.skip("FFmpeg not available")


# ------------------------------------------------------------------------ alignment

def test_the_offset_is_found_to_the_sample(clean, donor):
    assert combine.find_offset(clean, donor, RATE) == OFFSET


def test_a_donor_that_starts_early_has_a_negative_offset(clean, donor):
    assert combine.find_offset(donor, clean, RATE) == -OFFSET


def test_a_different_recording_is_refused(clean):
    other = np.random.default_rng(1).standard_normal(clean.shape).astype(np.float32) * 0.1
    with pytest.raises(ValueError, match="do not line up"):
        combine.find_offset(clean, other, RATE)


def test_a_version_at_another_speed_is_refused(clean):
    """One offset cannot hold, so nothing is grafted out of step."""
    stretched = np.interp(np.arange(0, len(clean), 1.0005), np.arange(len(clean)), clean[:, 0])
    stretched = np.stack([stretched, stretched], axis=1).astype(np.float32)
    with pytest.raises(ValueError, match="drift apart|do not line up"):
        combine.find_offset(clean, stretched, RATE)


# ------------------------------------------------------------------------- windows

def _aligned(clean, donor):
    return combine._shifted(donor, OFFSET, len(clean))


def test_only_the_swapped_word_is_a_window(clean, donor):
    windows, only_clean, gain = combine.find_windows(clean, _aligned(clean, donor), RATE)
    assert only_clean == []
    assert len(windows) == 1
    assert windows[0].start == pytest.approx(SWAPPED[0], abs=0.15)
    assert windows[0].end == pytest.approx(SWAPPED[1], abs=0.15)
    assert gain == pytest.approx(1 / DONOR_GAIN, rel=0.02)


def test_identical_versions_have_no_windows(clean):
    assert combine.find_windows(clean, clean, RATE)[:2] == ([], [])


def test_a_word_only_the_clean_stem_has_is_left_alone(clean, donor):
    """Grafting silence over it would delete something real."""
    aligned = _aligned(clean, donor).copy()
    aligned[int(30.0 * RATE):int(30.4 * RATE)] = 0
    windows, only_clean, _ = combine.find_windows(clean, aligned, RATE)
    assert len(windows) == 1
    assert len(only_clean) == 1
    assert only_clean[0].start == pytest.approx(30.0, abs=0.15)


def test_a_word_muted_in_the_clean_stem_is_grafted(clean, donor):
    muted = clean.copy()
    muted[int(10.0 * RATE):int(10.4 * RATE)] = 0
    windows, only_clean, _ = combine.find_windows(muted, _aligned(clean, donor), RATE)
    assert [round(w.start) for w in windows] == [10, 20]
    assert only_clean == []


# -------------------------------------------------------------------------- splice

def test_splice_leaves_everything_outside_the_window_untouched(clean, donor):
    aligned = _aligned(clean, donor)
    out = combine.splice(clean, aligned, [combine.Window(*SWAPPED)], RATE)
    reach = combine.PRE_SECONDS + combine.FADE_SECONDS + 0.001
    before = int((SWAPPED[0] - reach) * RATE)
    after = int((SWAPPED[1] + combine.POST_SECONDS + combine.FADE_SECONDS + 0.001) * RATE)
    assert np.array_equal(out[:before], clean[:before])
    assert np.array_equal(out[after:], clean[after:])
    middle = slice(int(SWAPPED[0] * RATE), int(SWAPPED[1] * RATE))
    assert np.array_equal(out[middle], aligned[middle])


def test_a_window_at_the_very_start_or_end_does_not_overrun(clean):
    out = combine.splice(clean, clean * 0, [combine.Window(0.0, 0.1),
                                            combine.Window(SECONDS - 0.1, SECONDS)], RATE)
    assert out.shape == clean.shape
    assert not out[:100].any() and not out[-100:].any()


# --------------------------------------------------------------------------- graft

@pytest.fixture
def stem_files(clean, donor, ffmpeg, tmp_path):
    folder = tmp_path / "Clean Song"
    folder.mkdir()
    paths = {}
    for name in ("Drums", "Bass", "Other"):
        paths[name] = combine._encode(ffmpeg, clean * 0.1, RATE, str(folder / f"{name}.wav"))
    paths["Vocals"] = combine._encode(ffmpeg, clean, RATE, str(folder / "Vocals.wav"))
    paths["master"] = combine._encode(ffmpeg, clean * 1.3, RATE, str(folder / "Master.wav"))
    paths["donor"] = combine._encode(ffmpeg, donor, RATE, str(tmp_path / "donor.wav"))
    paths["folder"] = str(folder)
    return paths


def _decode(ffmpeg, path):
    return combine._decode(ffmpeg, path, RATE)


def test_graft_rewrites_one_stem_and_the_mixdown(stem_files, clean, ffmpeg, tmp_path):
    stem_set = StemSet(
        title="Clean Song", master=stem_files["master"],
        stems=[Stem(n, stem_files[n]) for n in ("Drums", "Bass", "Other", "Vocals")])
    out = combine.graft(stem_set, stem_files["donor"], str(tmp_path / "work"))

    report = out.extra["graft"]
    assert report.stem == "Vocals"
    assert report.offset == pytest.approx(OFFSET / RATE)
    assert len(report.windows) == 1

    # The other stems are the same files, and the originals are never written to
    assert [s.path for s in out.stems[:3]] == [stem_files[n] for n in ("Drums", "Bass", "Other")]
    assert out.require("Vocals").path != stem_files["Vocals"]
    assert np.array_equal(_decode(ffmpeg, stem_files["Vocals"]), clean)

    vocal = _decode(ffmpeg, out.require("Vocals").path)
    changed = np.abs(vocal - clean).max(axis=1) > 1e-6
    assert changed[int(20.1 * RATE)]
    assert not changed[:int(19.5 * RATE)].any() and not changed[int(21 * RATE):].any()

    # The mixdown changes by exactly what the stem did, keeping its own level
    master = _decode(ffmpeg, out.master)
    assert np.allclose(master - clean * 1.3, vocal - clean, atol=1e-6)


def test_graft_needs_the_stem_it_grafts(stem_files, tmp_path):
    stem_set = StemSet(title="x", stems=[Stem("Drums", stem_files["Drums"])])
    with pytest.raises(KeyError, match="Vocals"):
        combine.graft(stem_set, stem_files["donor"], str(tmp_path / "work"))


# -------------------------------------------------------------------------- titles

@pytest.mark.parametrize("title,marker,carry,expected", [
    ("Work Work (FN)", "(+RF)", None, "Work Work (FN +RF)"),
    ("Work Bitch", "(+RF)", "Work Work (FN)", "Work Bitch (FN +RF)"),
    ("Song", "(+HY)", None, "Song (+HY)"),
    ("Song", "(+RF)", "Song", "Song (+RF)"),
    # Not a marker, so it is part of the title
    ("Song (Remix)", "(+RF)", None, "Song (Remix) (+RF)"),
    ("Work Work (FN)", None, None, "Work Work (FN)"),
    ("Work Bitch", None, "Work Work (FN)", "Work Bitch (FN)"),
    ("Work Work (FN)", "explicit", None, "Work Work (FN) explicit"),
])
def test_the_marker_joins_one_the_stems_already_had(title, marker, carry, expected):
    assert combine.mark_title(title, marker, carry_from=carry) == expected


def test_each_separator_has_its_own_marker():
    assert [combine.title_marker(s) for s in ("roformer", "hybrid", "demucs")] == [
        "(+RF)", "(+HY)", "(+DE)"]


# ----------------------------------------------------------------------------- job

@pytest.fixture
def fake_separator(stem_files, monkeypatch):
    """Stands in for a separation of the mix, handing back the prepared donor."""
    from bejeweled.sources import separate

    def fake(path, work_dir, **kwargs):
        os.makedirs(work_dir, exist_ok=True)
        vocals = os.path.join(work_dir, "Vocals.wav")
        with open(stem_files["donor"], "rb") as src, open(vocals, "wb") as dst:
            dst.write(src.read())
        return StemSet(title="mix", stems=[Stem("Vocals", vocals)], source="a separator")

    monkeypatch.setattr(separate, "separate", fake)


def test_the_job_writes_a_marked_stem_file_and_cleans_up(stem_files, fake_separator, tmp_path):
    out = tmp_path / "out"
    written = jobs.graft_file(stem_files["folder"], stem_files["donor"], str(out), {},
                              title="Explicit Song")
    assert os.path.basename(written.path) == "Explicit Song (+RF).stem.mp4"
    assert written.stem_set.comment.endswith("vocals grafted in 1 places from a separator")
    assert os.listdir(out) == ["Explicit Song (+RF).stem.mp4"]
    # The folder of stems it read is as it was
    assert sorted(os.listdir(stem_files["folder"])) == [
        "Bass.wav", "Drums.wav", "Master.wav", "Other.wav", "Vocals.wav"]


def test_files_format_gives_a_whole_folder(stem_files, fake_separator, tmp_path):
    written = jobs.graft_file(stem_files["folder"], stem_files["donor"], str(tmp_path / "out"),
                              {}, fmt="files")
    assert written.stem_set.title == "Clean Song (+RF)"
    assert sorted(os.listdir(written.path)) == [
        "Bass.wav", "Drums.wav", "Master.wav", "Other.wav", "Vocals.wav"]


def test_nothing_is_written_when_nothing_differs(stem_files, monkeypatch, tmp_path):
    from bejeweled.sources import separate

    same = StemSet(title="mix", stems=[Stem("Vocals", stem_files["Vocals"])], source="x")
    monkeypatch.setattr(separate, "separate", lambda *a, **k: same)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="nothing to graft"):
        jobs.graft_file(stem_files["folder"], stem_files["donor"], str(out), {})
    assert not out.exists() or os.listdir(out) == []
