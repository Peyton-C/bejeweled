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
    """Render a stereo WAV with tone bursts at the given (start, duration, gain) times.

    aevalsrc is evaluated per sample, so a burst starts exactly where it is asked to. A
    gate built out of volume's timeline lands on a frame boundary instead, which is
    coarser than the grid the detector holds clicks to.
    """
    terms = []
    for start, length, *rest in events:
        gain = rest[0] if rest else 0.5
        terms.append(f"{gain}*between(t,{start},{start + length})")
    wave = f"sin(2*PI*{freq}*t)*({'+'.join(terms)})" if terms else "0"
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
         "-i", f"aevalsrc=exprs='{wave}'|'{wave}':s={SR}:d={total}", str(path)],
        check=True,
    )
    return str(path)


def clicks_on(beats, period, start=0.0, length=0.2, gain=0.5):
    """Bursts on the given beats of a grid, as Festival's metronome lays them out."""
    return [(start + n * period, length, gain) for n in beats]


def test_detects_four_beat_countin(ffmpeg, tmp_path):
    bpm, beat = 89, 60 / 89
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on(range(4), beat)),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(4 * beat, 6.0)]),
        "Drums": make_track(ffmpeg, tmp_path / "Drums.wav", []),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.beats == 4
    assert found.downbeat == pytest.approx(4 * beat, abs=0.02)
    assert found.trim_at == pytest.approx(4 * beat, abs=0.05)
    assert found.muted == 0
    assert not found.kept_pickup


def test_detects_eight_beat_countin_with_gapped_pattern(ffmpeg, tmp_path):
    """Clicks on beats 1, 3, 5-8 - the pattern most Festival tracks use."""
    bpm, beat = 120, 0.5
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on((0, 2, 4, 5, 6, 7), beat)),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(8 * beat, 4.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.beats == 8
    assert found.trim_at == pytest.approx(4.0, abs=0.05)
    assert not found.kept_pickup


def test_reads_the_tempo_from_the_clicks_not_the_tag(ffmpeg, tmp_path):
    """vampire is tagged 139 and counts in at 134.3, so the tag only sets the range."""
    bpm, period = 139, 60 / 134.3
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on((0, 2, 4, 5, 6, 7), period)),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(8 * period, 4.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.beats == 8
    assert found.downbeat == pytest.approx(8 * period, abs=0.02)


def test_reads_a_countin_well_off_the_published_tempo(ffmpeg, tmp_path):
    """24K Magic is tagged 107 and counts in at 90."""
    bpm, period = 107, 60 / 90
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on((0, 2, 4, 5, 6, 7), period)),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(8 * period, 4.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.beats == 8
    assert found.downbeat == pytest.approx(8 * period, abs=0.02)


def test_reads_a_countin_that_ticks_every_other_beat(ffmpeg, tmp_path):
    """Side To Side is tagged 159 and counts eight half notes, not sixteen beats."""
    bpm, tick = 159, 2 * 60 / 159
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on((0, 2, 4, 5, 6, 7), tick)),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(8 * tick, 4.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.downbeat == pytest.approx(8 * tick, abs=0.02)


def test_finds_a_countin_that_starts_after_silence(ffmpeg, tmp_path):
    """good 4 u leaves the first beats of its count-in empty before the clicks start."""
    bpm, period = 169, 60 / 169
    downbeat = 11 * period
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on((-7, -5, -3, -2, -1), period, start=downbeat)),
        "Bass": make_track(ffmpeg, tmp_path / "Bass.wav", [(downbeat, 4.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.downbeat == pytest.approx(downbeat, abs=0.02)
    assert found.beats == 7


def test_backs_off_for_a_pickup_before_the_downbeat(ffmpeg, tmp_path):
    """A vocal entering just before the downbeat must not be clipped."""
    bpm, beat = 140, 60 / 140
    downbeat = 8 * beat
    pickup = downbeat - 0.04
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on((0, 2, 4, 5, 6, 7), beat)),
        "Vocals": make_track(ffmpeg, tmp_path / "Vocals.wav", [(pickup, 5.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.beats == 8
    assert found.kept_pickup
    assert found.trim_at < found.downbeat
    assert found.trim_at <= pickup, "trim must start at or before the pickup"
    assert found.muted == 0, "no click is left standing after the cut"


def test_silences_clicks_the_cut_cannot_reach(ffmpeg, tmp_path):
    """Bad Romance clicks on all eight beats and sings from the sixth.

    No cut keeps the vocal and loses the clicks, so the cut keeps the vocal and the
    clicks in front of the downbeat are silenced on their own stem instead.
    """
    bpm, beat = 120, 0.5
    downbeat, pickup = 8 * beat, 5 * beat
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on(range(8), beat) + [(downbeat, 4.0, 0.1)]),
        "Vocals": make_track(ffmpeg, tmp_path / "Vocals.wav", [(pickup, 5.0)]),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(downbeat, 4.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.beats == 8
    assert len(found.clicks) == 8
    assert found.downbeat == pytest.approx(downbeat, abs=0.02)
    assert found.trim_at <= pickup, "the vocal pickup must survive the cut"
    assert found.mute_until == pytest.approx(downbeat, abs=0.02)
    assert found.muted == pytest.approx(found.mute_until - found.trim_at, abs=0.001)


def test_a_downbeat_struck_on_the_grid_is_not_a_click(ffmpeg, tmp_path):
    """Illegal opens on a hit within 3dB of its own count-in, one beat past the clicks.

    Taking it for a seventh click would put the downbeat a beat late and trim a beat of
    music away, so a strike the rest of the band arrives on is read as the downbeat.
    """
    bpm, beat = 120, 0.5
    downbeat = 8 * beat
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on((0, 2, 4, 5, 6, 7), beat)
                            + [(downbeat, 0.2, 0.42)]),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(downbeat, 4.0)]),
        "Drums": make_track(ffmpeg, tmp_path / "Drums.wav", [(downbeat, 4.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.downbeat == pytest.approx(downbeat, abs=0.02)
    assert found.beats == 8
    assert len(found.clicks) == 6


def test_a_downbeat_that_holds_through_the_beat_is_not_a_click(ffmpeg, tmp_path):
    """Born This Way opens on the Other stem alone, on the grid, at its count-in's level.

    No other stem arrives on it, so the only thing separating it from a seventh click
    is that it holds where a click would have dropped away.
    """
    bpm, beat = 120, 0.5
    downbeat = 8 * beat
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on((0, 2, 4, 5, 6, 7), beat) + [(downbeat, 4.0)]),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(12 * beat, 4.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert found.downbeat == pytest.approx(downbeat, abs=0.02)
    assert len(found.clicks) == 6


def test_music_starting_after_the_last_click_is_not_the_downbeat(ffmpeg, tmp_path):
    """The Way I Are comes in a third of a beat after its last click.

    The click drops away first, so it stays a click and the count-in is still read.
    The cut lands on the downbeat, which does clip the front of that entry; telling a
    swell under a click from the click's own ring-down is not something the envelope
    supports, so the trade is made in favour of never moving a good cut early.
    """
    bpm, beat = 115, 60 / 115
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            clicks_on((0, 2, 4, 5), beat) + [(5.5 * beat, 4.0, 0.25)]),
        "Drums": make_track(ffmpeg, tmp_path / "Drums.wav", [(8 * beat, 4.0)]),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert len(found.clicks) == 4, "the last click must not be read as the downbeat"
    assert found.downbeat == pytest.approx(6 * beat, abs=0.02)


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
                            clicks_on((0, 2, 4, 5, 6, 7), beat)),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(8 * beat, 4.0)]),
        "Bass": str(quiet),
    }
    found = countin.detect(stems, bpm)

    assert found is not None
    assert not found.kept_pickup, "noise floor was treated as a pickup"
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
                            [(0.0, 0.2), (0.31, 0.2), (0.77, 0.2), (1.13, 0.2)]),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(4.0, 4.0)]),
    }
    assert countin.detect(stems, 120) is None


def test_returns_none_when_the_click_stem_plays_through_the_countin(ffmpeg, tmp_path):
    """Barbie Girl's synth line starts halfway through its count-in.

    The clicks are still there to be found, but nothing can be done with them: no cut
    takes them without taking the music, and silencing them would silence the music
    too, because it is on the same stem.
    """
    bpm, beat = 130, 60 / 130
    # The clicks over the music are cut so they still peak where the clean ones do,
    # which is what the stem measures: six clicks at one level, four of them ringing
    # down only as far as the music under them.
    other = (clicks_on((0, 2), beat, gain=0.9)
             + clicks_on((4, 5, 6, 7), beat, gain=0.7)
             + [(3.6 * beat, 6.0, 0.2)])
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav", other),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(8 * beat, 4.0)]),
    }
    assert countin.detect(stems, bpm) is None


def test_returns_none_when_something_is_struck_before_the_clicks(ffmpeg, tmp_path):
    """A count-in opens the file, so a run with music in front of it is not one."""
    bpm, beat = 120, 0.5
    stems = {
        "Other": make_track(ffmpeg, tmp_path / "Other.wav",
                            [(0.17, 0.2)] + clicks_on((2, 4, 6, 7, 8, 9), beat)),
        "Lead": make_track(ffmpeg, tmp_path / "Lead.wav", [(5.0, 4.0)]),
    }
    assert countin.detect(stems, bpm) is None
