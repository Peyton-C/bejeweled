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
    # A stem file can only become Engine stems, so the format is left to the input
    assert parser.parse_args(["convert", "a.stem.mp4"]).format is None
    with pytest.raises(SystemExit):
        parser.parse_args(["convert", "./stems", "--format", "files"])
