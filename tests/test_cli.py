"""Command structure. Parsing only - no ripping, no network."""
import pytest

from bejeweled.cli import FORMATS, build_parser


@pytest.fixture
def parser():
    return build_parser()


def test_sources_are_scoped_to_their_own_subcommand(parser):
    """A source is a subcommand, not a --backend flag, so each can take its own args."""
    args = parser.parse_args(["festival", "rip", "Kill Bill"])
    assert args.command == "festival"
    assert args.action == "rip"
    assert args.query == "Kill Bill"


def test_festival_list_takes_an_optional_filter(parser):
    assert parser.parse_args(["festival", "list"]).query is None
    assert parser.parse_args(["festival", "list", "sza"]).query == "sza"


def test_a_source_without_an_action_is_rejected(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["festival"])


def test_bare_rip_is_no_longer_a_command(parser):
    """The old Festival-only spelling must not silently keep working."""
    with pytest.raises(SystemExit):
        parser.parse_args(["rip", "Kill Bill"])
    with pytest.raises(SystemExit):
        parser.parse_args(["list"])


def test_output_format_is_a_flag_with_a_closed_set(parser):
    assert parser.parse_args(["festival", "rip", "x"]).format is None  # config decides
    assert parser.parse_args(["festival", "rip", "x", "--format", "files"]).format == "files"
    assert parser.parse_args(
        ["festival", "rip", "x", "--format", "ni-stem"]
    ).format == "ni-stem"

    with pytest.raises(SystemExit):
        parser.parse_args(["festival", "rip", "x", "--format", "serato"])


def test_formats_are_the_three_intended_outputs():
    assert set(FORMATS) == {"ni-stem", "engine", "files"}


def test_engine_takes_its_library_and_key_from_either_source(parser):
    args = parser.parse_args(["festival", "rip", "x", "--format", "engine",
                              "--engine-library", "/music/Engine Library"])
    assert (args.format, args.engine_library, args.engine_key) == (
        "engine", "/music/Engine Library", None)
    assert parser.parse_args(
        ["separate", "a.wav", "--format", "engine", "--engine-key", "00" * 16]
    ).engine_key == "00" * 16


def test_separate_takes_a_file_and_the_shared_output_options(parser):
    args = parser.parse_args(["separate", "song.flac", "--format", "files", "--layout", "5.1"])
    assert (args.command, args.files, args.format, args.layout) == (
        "separate", ["song.flac"], "files", "5.1")
    assert parser.parse_args(["separate", "song.flac"]).layout is None
    # What a shell wildcard expands to
    assert parser.parse_args(["separate", "a.wav", "b.wav", "albums/"]).files == [
        "a.wav", "b.wav", "albums/"]

    with pytest.raises(SystemExit):
        parser.parse_args(["separate"])

    with pytest.raises(SystemExit):
        parser.parse_args(["separate", "song.flac", "--layout", "hexagonal"])


def test_separate_takes_a_separator_and_leaves_the_default_to_the_config(parser):
    assert parser.parse_args(["separate", "a.wav", "--separator", "hybrid"]).separator == "hybrid"
    # Unset on the command line, so a configured separator is not overridden
    assert parser.parse_args(["separate", "a.wav"]).separator is None
    with pytest.raises(SystemExit):
        parser.parse_args(["separate", "a.wav", "--separator", "spleeter"])


def test_stem_file_verbs_stay_top_level(parser):
    """These act on stem files whatever produced them, so they are not source-scoped."""
    assert parser.parse_args(["info", "a.stem.mp4"]).files == ["a.stem.mp4"]
    assert parser.parse_args(["recolor", "a.stem.mp4", "--palette", "vivid-dark"]).files
    assert parser.parse_args(["palette"]).name is None
    assert parser.parse_args(["convert", "./stems"]).inputs == ["./stems"]


def test_recolor_requires_a_palette(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["recolor", "a.stem.mp4"])


def test_festival_rip_options(parser):
    args = parser.parse_args([
        "festival", "rip", "Kill Bill", "-o", "/tmp/x",
        "--keys", "/k/keys.bin", "--palette", "vivid-dark",
        "--suffix", "(FN)", "--keep-countin", "--no-cover",
    ])
    assert args.out == "/tmp/x"
    assert args.keys == "/k/keys.bin"
    assert args.palette == "vivid-dark"
    assert args.suffix == "(FN)"
    assert args.keep_countin
    assert args.no_cover


def test_convert_takes_stem_files_and_an_engine_library(parser):
    args = parser.parse_args(["convert", "a.stem.mp4", "b.stem.mp4", "--format", "engine",
                              "--engine-library", "/music/Engine Library"])
    assert (args.inputs, args.format, args.engine_library) == (
        ["a.stem.mp4", "b.stem.mp4"], "engine", "/music/Engine Library")
    # Unset, so a stem file becomes Engine stems and a folder a stem file
    assert parser.parse_args(["convert", "a.stem.mp4"]).format is None
    assert parser.parse_args(["convert", "a.stem.mp4", "--format", "files"]).format == "files"


def test_convert_refuses_what_the_input_already_is(tmp_path, capsys):
    from bejeweled.cli import main

    assert main(["convert", str(tmp_path), "--format", "files"]) == 1
    assert "already a folder of stems" in capsys.readouterr().err


def test_convert_names_the_slots_of_a_folder(tmp_path, monkeypatch, capsys):
    """Stems are matched to --slots by name and keep that order in the file."""
    import subprocess

    from bejeweled import ffmpeg as ff
    from bejeweled.cli import main
    from bejeweled.writers import ni_stem

    try:
        ffmpeg = ff.find_ffmpeg()
    except ff.FFmpegError:
        pytest.skip("FFmpeg not available")
    # An empty config, so the user's own cannot send this to their Engine library
    monkeypatch.setenv("BEJEWELED_CONFIG", str(tmp_path / "config.toml"))

    folder = tmp_path / "Song"
    folder.mkdir()
    for name, freq in (("drums", 110), ("Music", 220), ("sax", 440), ("vocals", 880)):
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={freq}:duration=1:sample_rate=44100",
                        "-ac", "2", str(folder / f"{name}.wav")], check=True)

    out = str(tmp_path / "Song.stem.mp4")
    assert main(["convert", str(folder), "--slots", "Drums,Music,Sax,Vocals", "-o", out]) == 0
    names = [s["name"] for s in ni_stem.read_metadata(out)["stems"]]
    assert names == ["Drums", "Music", "Sax", "Vocals"]

    capsys.readouterr()
    assert main(["convert", str(folder), "--slots", "Drums,Bass,Sax,Vocals", "-o", out]) == 1
    assert "--slots names Drums, Bass, Sax, Vocals" in capsys.readouterr().err
    assert main(["convert", str(folder), "--slots", "Drums,Music,Vocals", "-o", out]) == 1
    assert "--slots takes 4 names" in capsys.readouterr().err
    assert main(["convert", out, "--slots", "Drums,Music,Sax,Vocals"]) == 1
    assert "is a stem file" in capsys.readouterr().err


def test_convert_embeds_the_cover_it_is_given(tmp_path, monkeypatch, capsys):
    import subprocess

    from bejeweled import artwork
    from bejeweled import ffmpeg as ff
    from bejeweled.cli import main

    try:
        ffmpeg = ff.find_ffmpeg()
    except ff.FFmpegError:
        pytest.skip("FFmpeg not available")
    monkeypatch.setenv("BEJEWELED_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(artwork, "find", lambda *a, **k: pytest.fail("looked up a cover"))

    folder = tmp_path / "Song"
    folder.mkdir()
    for name in ("drums", "bass", "other", "vocals"):
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                        "sine=duration=1:sample_rate=44100", "-ac", "2",
                        str(folder / f"{name}.wav")], check=True)
    cover = str(tmp_path / "cover.png")
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=red:size=64x64", "-frames:v", "1", cover], check=True)

    out = str(tmp_path / "Song.stem.mp4")
    assert main(["convert", str(folder), "--artist", "100 gecs", "--cover", cover,
                 "-o", out]) == 0
    assert any(s.get("disposition", {}).get("attached_pic")
               for s in ff.probe(ffmpeg, out)["streams"])

    assert main(["convert", str(folder), "--cover", str(tmp_path / "missing.jpg")]) == 1
    assert "no cover image" in capsys.readouterr().err
    assert main(["convert", out, "--cover", cover]) == 1
    assert "is a stem file" in capsys.readouterr().err


def test_combine_scopes_its_actions_like_a_source(parser):
    args = parser.parse_args(["combine", "graft", "clean.stem.mp4", "explicit.flac",
                              "--title", "Song", "--separator", "hybrid"])
    assert (args.command, args.action) == ("combine", "graft")
    assert (args.stems, args.mix, args.title, args.separator) == (
        "clean.stem.mp4", "explicit.flac", "Song", "hybrid")
    with pytest.raises(SystemExit):
        parser.parse_args(["combine"])
    with pytest.raises(SystemExit):
        parser.parse_args(["combine", "graft", "clean.stem.mp4"])


def test_extend_takes_the_stems_and_the_other_version(parser):
    args = parser.parse_args(["combine", "extend", "song.stem.mp4", "extended.flac",
                              "--title", "Song (Extended)"])
    assert (args.command, args.action) == ("combine", "extend")
    assert (args.stems, args.mix, args.title) == ("song.stem.mp4", "extended.flac",
                                                  "Song (Extended)")


def test_assemble_takes_its_parts_as_options(parser):
    args = parser.parse_args(["combine", "assemble", "--mix", "song.flac",
                              "--instrumental", "inst.flac", "--separator", "demucs"])
    assert (args.command, args.action) == ("combine", "assemble")
    assert (args.mix, args.instrumental, args.acapella) == ("song.flac", "inst.flac", None)
