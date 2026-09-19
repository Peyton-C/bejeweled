"""Count-in detection, against synthesised audio that mimics the real patterns."""
import subprocess

import pytest

from bejeweled import countin
from bejeweled import ffmpeg as ff

SR = 44100


@pytest.fixture(scope="module")
def ffmpeg():
    try:
        return ff.find_ffmpeg()
    except ff.FFmpegError:
        pytest.skip("FFmpeg not available")


def make_track(ffmpeg, path, events, total=12.0, freq=440):
    """Render a mono WAV with tone bursts at the given (start, duration) times."""
    if not events:
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
             "-i", f"anullsrc=r={SR}:cl=stereo", "-t", str(total), str(path)],
            check=True,
        )
        return str(path)

    enable = "+".join(f"between(t,{s},{s + d})" for s, d in events)
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={total}:sample_rate={SR}",
         "-af", f"volume=0:enable='not({enable})',aformat=channel_layouts=stereo",
         str(path)],
        check=True,
    )
    return str(path)


def click_times(bpm, beats):
    """Festival's two observed patterns: every beat, or 1,3,5..N."""
    beat = 60 / bpm
    if beats == 4:
        return [i * beat for i in range(4)]
    return [i * beat for i in (0, 2, 4, 5, 6, 7)]


def test_detects_four_beat_countin(ffmpeg, tmp_path):
    bpm, beat = 89, 60 / 89
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            [(t, 0.25) for t in click_times(bpm, 4)]),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(4 * beat, 6.0)]),
        "Drums": make_track(ffmpeg, tmp_path / "Drums.wav", []),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.beats == 4
    assert found.downbeat == pytest.approx(4 * beat, abs=0.01)
    assert found.trim_at == pytest.approx(4 * beat, abs=0.05)
    assert not found.clipped_pickup


def test_detects_eight_beat_countin_with_gapped_pattern(ffmpeg, tmp_path):
    """Clicks on beats 1, 3, 5-8 - the pattern most Festival tracks use."""
    bpm, beat = 120, 0.5
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            [(t, 0.23) for t in click_times(bpm, 8)]),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(8 * beat, 4.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.beats == 8
    assert found.trim_at == pytest.approx(4.0, abs=0.05)
    assert not found.clipped_pickup


def test_backs_off_for_a_pickup_before_the_downbeat(ffmpeg, tmp_path):
    """A vocal entering just before the downbeat must not be clipped."""
    bpm, beat = 140, 60 / 140
    downbeat = 8 * beat
    pickup = downbeat - 0.04
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            [(t, 0.28) for t in click_times(bpm, 8)]),
        "Vocals": make_track(ffmpeg, tmp_path / "Vocals.wav", [(pickup, 5.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.beats == 8
    assert found.clipped_pickup
    assert found.trim_at < found.downbeat
    assert found.trim_at <= pickup, "trim must start at or before the pickup"


def test_ignores_a_stem_that_is_only_noise_floor(ffmpeg, tmp_path):
    """An effectively empty stem must not be mistaken for an early pickup.

    Call Me Maybe ships a bass stem that never rises above about -48dB.
    """
    bpm, beat = 120, 0.5
    quiet = tmp_path / "Bass.wav"
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
         "-i", f"anoisesrc=d=12:c=pink:r={SR}:a=0.002",
         "-af", "aformat=channel_layouts=stereo", str(quiet)],
        check=True,
    )
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            [(t, 0.23) for t in click_times(bpm, 8)]),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(8 * beat, 4.0)]),
        "Bass": str(quiet),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert not found.clipped_pickup, "noise floor was treated as a pickup"
    assert found.trim_at == pytest.approx(4.0, abs=0.05)


def test_returns_none_when_there_is_no_countin(ffmpeg, tmp_path):
    """Music from the first sample must be left alone, not guessed at."""
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav", [(0.0, 10.0)]),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(0.0, 10.0)]),
    }
    assert countin.detect(stems, 120) is None


def test_returns_none_without_a_tempo(ffmpeg, tmp_path):
    stems = {"Other": make_track(ffmpeg, tmp_path / "Other.wav", [(0, 0.25)])}
    assert countin.detect(stems, 0) is None
    assert countin.detect(stems, None) is None


def test_returns_none_when_clicks_are_off_grid(ffmpeg, tmp_path):
    """Off-grid sounds are music, not a count-in, so nothing is trimmed."""
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            [(0.0, 0.25), (0.31, 0.25), (0.77, 0.25), (1.13, 0.25)]),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(4.0, 4.0)]),
    }
    assert countin.detect(stems, 120) is None
