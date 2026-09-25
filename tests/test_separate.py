"""The separate source: layouts, grouping and the fold, with a stand-in for demucs."""
import os
import re
import stat
import subprocess
import sys
import textwrap

import pytest

from bejeweled import demucs as dm
from bejeweled import ffmpeg as ff
from bejeweled.sources import separate as sep


# ------------------------------------------------------------------------- layouts

@pytest.mark.parametrize("channels,expected", [
    (1, "mono"), (2, "stereo"), (6, "5.1"), (8, "7.1"), (12, "7.1.4"), (16, "9.1.6"),
])
def test_layout_defaults_by_channel_count(channels, expected):
    assert sep.resolve_layout(channels) == expected


def test_ten_channels_is_ambiguous_and_asks_for_a_layout():
    with pytest.raises(ValueError, match="5.1.4, 7.1.2"):
        sep.resolve_layout(10)


def test_an_explicit_layout_must_match_the_channel_count():
    assert sep.resolve_layout(10, "7.1.2") == "7.1.2"
    with pytest.raises(ValueError, match="has 12 channels, the file has 16"):
        sep.resolve_layout(16, "7.1.4")


@pytest.mark.parametrize("declared,expected", [
    ("5.1(side)", "5.1"), ("5.1", "5.1"), ("stereo", "stereo"),
    # An Atmos capture through a loopback device declares nothing
    ("unknown", "9.1.6"), ("16 channels", "9.1.6"), (None, "9.1.6"),
])
def test_a_declared_layout_is_read_when_there_is_one(declared, expected):
    channels = {"5.1(side)": 6, "5.1": 6, "stereo": 2}.get(declared, 16)
    assert sep.resolve_layout(channels, declared=declared) == expected


def test_a_declared_layout_that_cannot_be_grouped_is_not_guessed_at():
    """A hexagonal file has six channels too, and must not be read as 5.1."""
    with pytest.raises(ValueError, match="hexagonal"):
        sep.resolve_layout(6, declared="hexagonal")
    assert sep.resolve_layout(6, "5.1", declared="hexagonal") == "5.1"


def test_every_role_has_a_group():
    for roles in sep.LAYOUTS.values():
        assert all(role in sep._GROUP_OF for role in roles)


# ------------------------------------------------------------------------ grouping

def test_916_groups_match_the_experiment():
    """The four groups that beat the stereo fold, by 0-based channel index."""
    assert sep.groups("9.1.6") == {
        "bed": [0, 1, 2, 3],
        "surround": [4, 5, 6, 7],
        "wide": [8, 9],
        "height": [10, 11, 12, 13, 14, 15],
    }


def test_51_splits_the_bed_from_the_surrounds():
    assert sep.groups("5.1") == {"bed": [0, 1, 2, 3], "surround": [4, 5]}


def test_stereo_is_a_single_group():
    assert sep.groups("stereo") == {"bed": [0, 1]}


def test_916_gains_match_the_experiment():
    h = 0.7071067811865476
    expected = [(1.0, 0.0), (0.0, 1.0), (h, h), (h, h)] + [(h, 0.0), (0.0, h)] * 6
    assert [sep.gains(role) for role in sep.LAYOUTS["9.1.6"]] == expected


def test_a_one_sided_group_still_folds_to_stereo():
    assert sep.pan_filter("5.1", [4]).endswith("|c1=0*c0")


@pytest.mark.parametrize("layout,marker", [
    ("stereo", "(DE)"), ("mono", "(DE)"), ("5.1", "(DE SR)"), ("7.1", "(DE SR)"),
    ("5.1.4", "(DE AT)"), ("9.1.6", "(DE AT)"),
])
def test_marker_follows_the_layout(layout, marker):
    assert sep.title_marker(layout) == marker


def test_separations_are_marked_by_default():
    """The stems are bejeweled's own, so they must not pass for real stems."""
    assert sep.MARK_TITLES_BY_DEFAULT is True


# ------------------------------------------------------------------------- tags

def test_tags_are_read_across_containers():
    info = {
        "streams": [{"codec_type": "audio", "tags": {"TITLE": "Stream Title"}}],
        "format": {"tags": {"title": "Déjà Vu", "ARTIST": "Beyoncé",
                            "date": "2006-09-01", "TBPM": "105", "initialkey": "Bbm"}},
    }
    assert sep.read_tags(info) == {
        "title": "Déjà Vu", "artist": "Beyoncé", "album": None,
        "year": "2006", "bpm": 105.0, "key": "Bbm",
    }


def test_missing_or_bad_tags_are_left_out():
    tags = sep.read_tags({"format": {"tags": {"bpm": "fast", "date": "someday"}}})
    assert tags["bpm"] is None and tags["year"] is None and tags["title"] is None


# ----------------------------------------------------------------- with FFmpeg

@pytest.fixture(scope="module")
def ffmpeg():
    try:
        return ff.find_ffmpeg()
    except ff.FFmpegError:
        pytest.skip("FFmpeg not available")


@pytest.fixture
def fake_demucs(tmp_path, ffmpeg):
    """Stands in for demucs: writes each input back as four quarter-level stems, so
    the stems of every group sum to exactly that group's submix."""
    log = tmp_path / "argv.txt"
    script = tmp_path / "demucs"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import os, subprocess, sys
        args = sys.argv[1:]
        if args == ["--help"]:
            sys.exit(0)
        open({str(log)!r}, "a").write(" ".join(args) + "\\n")
        out, model = args[args.index("-o") + 1], args[args.index("-n") + 1]
        for path in [a for a in args if a.endswith(".wav")]:
            track = os.path.join(out, model, os.path.splitext(os.path.basename(path))[0])
            os.makedirs(track, exist_ok=True)
            for stem in ("drums", "bass", "other", "vocals"):
                print(f"{{stem}} 100%|", file=sys.stderr)
                subprocess.run([{ffmpeg!r}, "-v", "error", "-y", "-i", path, "-af",
                                "volume=0.25", "-c:a", "pcm_f32le",
                                os.path.join(track, stem + ".wav")], check=True)
    """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script), log


def _render(ffmpeg, path, channels, layout=None):
    """Independent noise on every channel, so any misrouting shows up."""
    inputs = []
    for i in range(channels):
        inputs += ["-f", "lavfi", "-i", f"anoisesrc=seed={i + 1}:amplitude=0.3:duration=1"]
    chain = "".join(f"[{i}:a]" for i in range(channels))
    # join defaults to stereo, amerge leaves the layout undeclared like a loopback rip
    if layout:
        joined = f"{chain}join=inputs={channels}:channel_layout={layout}"
    else:
        joined = f"{chain}amerge=inputs={channels}"
    subprocess.run([ffmpeg, "-v", "error", "-y", *inputs, "-filter_complex", joined,
                    "-c:a", "pcm_f32le", str(path)], check=True)
    return str(path)


def _residual_db(ffmpeg, parts, reference):
    """Peak level of the sum of `parts` minus `reference`.

    astats rather than volumedetect, whose histogram bottoms out at -91 dB and so
    cannot tell float rounding from a 16 bit error.
    """
    cmd = [ffmpeg, "-v", "info", "-nostats"]
    for path in [*parts, reference]:
        cmd += ["-i", path]
    n = len(parts)
    graph = (f"[{n}:a]volume=-1[neg];"
             + "".join(f"[{i}:a]" for i in range(n))
             + f"[neg]amix=inputs={n + 1}:normalize=0,astats=measure_perchannel=none")
    out = subprocess.run(cmd + ["-filter_complex", graph, "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    level = re.findall(r"Peak level dB: (-?[\d.]+|-inf)", out)[-1]
    return float(level)


def test_groups_sum_back_to_the_plain_fold(ffmpeg, tmp_path):
    """The property the method rests on: grouping changes what demucs sees, never
    the mix itself, so any difference in the stems is down to the grouping."""
    render = _render(ffmpeg, tmp_path / "render.wav", 16)
    folds = []
    for name, channels in sep.groups("9.1.6").items():
        out = str(tmp_path / f"{name}.wav")
        ff.run([ffmpeg, "-v", "error", "-y", "-i", render,
                "-af", sep.pan_filter("9.1.6", channels), "-c:a", "pcm_f32le", out])
        folds.append(out)
    full = str(tmp_path / "full.wav")
    ff.run([ffmpeg, "-v", "error", "-y", "-i", render,
            "-af", sep.pan_filter("9.1.6", list(range(16))), "-c:a", "pcm_f32le", full])
    assert _residual_db(ffmpeg, folds, full) < -120


def test_separating_a_render_sums_each_stem_across_groups(ffmpeg, fake_demucs, tmp_path):
    demucs, log = fake_demucs
    render = _render(ffmpeg, tmp_path / "Song.wav", 16)
    stem_set = sep.separate(render, str(tmp_path / "work"), ffmpeg=ffmpeg, demucs=demucs)

    assert stem_set.names() == ["Bass", "Drums", "Other", "Vocals"]
    assert stem_set.extra == {"layout": "9.1.6", "groups": list(sep.GROUPS)}
    assert stem_set.title == "Song"
    assert "9.1.6 render in 4 groups" in stem_set.source
    # Nothing is lost or invented in the summing
    assert _residual_db(ffmpeg, [s.path for s in stem_set.stems], stem_set.master) < -120
    # Scratch audio is gone, only the stems and the mixdown are left
    assert sorted(os.listdir(tmp_path / "work")) == [
        "Bass.wav", "Drums.wav", "Master.wav", "Other.wav", "Vocals.wav"]

    argv = log.read_text().split("\n")[0].split()
    assert len(log.read_text().splitlines()) == 1, "all groups go to one invocation"
    for flag in ("--shifts 0", "--clip-mode none", "--float32"):
        assert flag in " ".join(argv)


def test_a_51_file_is_read_by_its_declared_layout(ffmpeg, fake_demucs, tmp_path):
    demucs, _ = fake_demucs
    render = _render(ffmpeg, tmp_path / "surround.wav", 6, "5.1(side)")
    stem_set = sep.separate(render, str(tmp_path / "work"), ffmpeg=ffmpeg, demucs=demucs)
    assert stem_set.extra == {"layout": "5.1", "groups": ["bed", "surround"]}
    assert _residual_db(ffmpeg, [s.path for s in stem_set.stems], stem_set.master) < -120


def test_stereo_is_its_own_mixdown(ffmpeg, fake_demucs, tmp_path):
    demucs, _ = fake_demucs
    song = _render(ffmpeg, tmp_path / "song.wav", 2, "stereo")
    stem_set = sep.separate(song, str(tmp_path / "work"), ffmpeg=ffmpeg, demucs=demucs)
    assert stem_set.master == song
    assert stem_set.extra["groups"] == ["bed"]
    assert "render" not in stem_set.source


def test_a_missing_file_says_it_is_missing(tmp_path):
    """Not just the path, which reads as an error with no description. A backslash
    typed inside double quotes, as in "i\\'m", is kept and makes exactly this."""
    with pytest.raises(FileNotFoundError, match="no such file: .*i\\\\'m"):
        sep.separate(str(tmp_path / "how i\\'m feeling now.wav"), str(tmp_path / "work"))


def test_a_failed_separation_leaves_nothing_behind(ffmpeg, tmp_path):
    broken = tmp_path / "demucs"
    broken.write_text(f"#!{sys.executable}\nimport sys\n"
                      "sys.exit(0 if sys.argv[1:] == ['--help'] else 3)\n")
    broken.chmod(broken.stat().st_mode | stat.S_IEXEC)
    song = _render(ffmpeg, tmp_path / "song.wav", 2)
    with pytest.raises(dm.DemucsError):
        sep.separate(song, str(tmp_path / "work"), ffmpeg=ffmpeg, demucs=str(broken))
    assert not (tmp_path / "work").exists()


# ------------------------------------------------------------------------- demucs

def test_missing_demucs_says_how_to_install_it(monkeypatch):
    monkeypatch.delenv("BEJEWELED_DEMUCS", raising=False)
    monkeypatch.setattr(dm.shutil, "which", lambda name: None)
    with pytest.raises(dm.DemucsError, match="uv tool install demucs"):
        dm.find_demucs()


def test_a_killed_demucs_says_so(tmp_path):
    """Killed for memory, demucs prints nothing, so its startup chatter is not the error."""
    script = tmp_path / "killed"
    script.write_text(f"#!{sys.executable}\nimport os, signal\n"
                      "print('Separating track bed.wav', flush=True)\n"
                      "os.kill(os.getpid(), signal.SIGKILL)\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(dm.DemucsError, match="SIGKILL.*ran out of memory"):
        dm.run([str(script)])


def test_a_failed_demucs_gives_its_exit_status(tmp_path):
    script = tmp_path / "fails"
    script.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(2)\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(dm.DemucsError, match="demucs exited with status 2"):
        dm.run([str(script)])


def test_progress_spans_every_input(tmp_path):
    """demucs draws one bar per input, restarting at 0% each time."""
    script = tmp_path / "bars"
    script.write_text(f"#!{sys.executable}\nimport sys\n"
                      "for p in (0, 50, 100, 0, 50, 100):\n"
                      "    sys.stderr.write(f'\\r {p}%|###')\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    seen = []
    dm.run([str(script)], lambda done, total: seen.append(done * 100 // total), tracks=2)
    assert seen == [0, 25, 50, 50, 75, 100]
