"""Configuration resolution, in particular the per-source title markers."""
import pytest

from bejeweled import config
from bejeweled.sources import festival, local


def test_sources_declare_their_own_marker():
    """The marker belongs to the source, so two sources can differ."""
    assert festival.TITLE_MARKER == "(FN)"
    assert festival.MARK_TITLES_BY_DEFAULT is True
    # The user supplied this audio and knows what it is
    assert local.MARK_TITLES_BY_DEFAULT is False


def test_source_default_is_used_when_unconfigured():
    assert config.title_suffix("festival", "(FN)", True, cfg={}) == "(FN)"
    assert config.title_suffix("local", "", False, cfg={}) is None


def test_config_overrides_the_marker_text():
    cfg = {"festival": {"title_suffix": "(FEST)"}}
    assert config.title_suffix("festival", "(FN)", True, cfg) == "(FEST)"


def test_marking_can_be_switched_off_per_source():
    cfg = {"festival": {"mark_titles": False}}
    assert config.title_suffix("festival", "(FN)", True, cfg) is None


def test_marking_can_be_switched_on_for_a_source_that_defaults_off():
    """Someone may want their Engine stems tagged as having come from Engine."""
    cfg = {"local": {"mark_titles": True, "title_suffix": "(ENGINE)"}}
    assert config.title_suffix("local", "", False, cfg) == "(ENGINE)"


def test_two_sources_keep_separate_markers():
    cfg = {
        "festival": {"title_suffix": "(FN)"},
        "local": {"mark_titles": True, "title_suffix": "(ENGINE)"},
    }
    assert config.title_suffix("festival", "(FN)", True, cfg) == "(FN)"
    assert config.title_suffix("local", "", False, cfg) == "(ENGINE)"


def test_empty_marker_disables_marking():
    cfg = {"festival": {"title_suffix": ""}}
    assert config.title_suffix("festival", "(FN)", True, cfg) is None


def test_the_old_global_setting_still_works_for_festival():
    """Markers used to live under [metadata]; existing configs must keep working."""
    cfg = {"metadata": {"title_suffix": "(FN)"}}
    assert config.title_suffix("festival", "(FN)", True, cfg) == "(FN)"

    legacy_custom = {"metadata": {"title_suffix": "(FEST)"}}
    assert config.title_suffix("festival", "(FN)", True, legacy_custom) == "(FEST)"


def test_the_old_global_setting_does_not_leak_to_other_sources():
    """Only Festival could have been written under the global setting."""
    cfg = {"metadata": {"title_suffix": "(FN)"}, "local": {"mark_titles": True}}
    assert config.title_suffix("local", "", False, cfg) is None


def test_per_source_setting_beats_the_old_global_one():
    cfg = {
        "metadata": {"title_suffix": "(OLD)"},
        "festival": {"title_suffix": "(NEW)"},
    }
    assert config.title_suffix("festival", "(FN)", True, cfg) == "(NEW)"


@pytest.mark.parametrize("backgrounds,expected", [
    ({"colors": {"backgrounds": ["#333941", "#413C33"]}}, ["#333941", "#413C33"]),
    ({"colors": {"background": "#101010"}}, ["#101010"]),
    ({}, ["#1E1E1E"]),
])
def test_backgrounds_accept_one_or_many(backgrounds, expected):
    assert config.backgrounds_list(backgrounds) == expected
