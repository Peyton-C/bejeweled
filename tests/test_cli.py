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
        parser.parse_args(["festival", "rip", "x", "--format", "engine"])


def test_formats_are_the_two_intended_outputs():
    assert set(FORMATS) == {"ni-stem", "files"}


def test_separate_takes_a_file_and_the_shared_output_options(parser):
    args = parser.parse_args(["separate", "song.flac", "--format", "files", "--layout", "5.1"])
    assert (args.command, args.file, args.format, args.layout) == (
        "separate", "song.flac", "files", "5.1")
    assert parser.parse_args(["separate", "song.flac"]).layout is None

    with pytest.raises(SystemExit):
        parser.parse_args(["separate", "song.flac", "--layout", "hexagonal"])


def test_stem_file_verbs_stay_top_level(parser):
    """These act on stem files whatever produced them, so they are not source-scoped."""
    assert parser.parse_args(["info", "a.stem.mp4"]).files == ["a.stem.mp4"]
    assert parser.parse_args(["recolor", "a.stem.mp4", "--palette", "vivid-dark"]).files
    assert parser.parse_args(["palette"]).name is None
    assert parser.parse_args(["convert", "./stems"]).folder == "./stems"


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
