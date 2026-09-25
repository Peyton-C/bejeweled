"""The StemSet model and its folding onto NI's four slots."""
import pytest

from bejeweled.stems import NI_SLOTS, Stem, StemSet


@pytest.fixture
def files(tmp_path):
    """Named empty files, since Stem only checks that the path exists."""
    def make(*names):
        paths = {}
        for name in names:
            path = tmp_path / f"{name}.wav"
            path.write_bytes(b"")
            paths[name] = str(path)
        return paths
    return make


def test_stem_requires_the_file_to_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        Stem(name="Drums", path=str(tmp_path / "absent.wav"))


def test_lookup_is_case_insensitive(files):
    paths = files("Drums")
    s = StemSet(title="t", stems=[Stem("Drums", paths["Drums"])])
    assert s.get("drums") is not None
    assert s.get("DRUMS") is not None
    assert s.get("Bass") is None
    with pytest.raises(KeyError):
        s.require("Bass")


def test_four_slot_set_folds_unchanged(files):
    paths = files(*NI_SLOTS)
    s = StemSet(title="t", stems=[Stem(n, paths[n]) for n in NI_SLOTS])
    groups = s.to_ni_slots().extra["ni_groups"]
    assert [len(groups[slot]) for slot in NI_SLOTS] == [1, 1, 1, 1]


def test_festival_five_stems_fold_lead_into_other(files):
    """Festival splits melodic content into Lead and Other; NI has one slot."""
    names = ["Drums", "Bass", "Lead", "Vocals", "Other"]
    paths = files(*names)
    s = StemSet(title="t", stems=[Stem(n, paths[n]) for n in names])

    groups = s.to_ni_slots({"Lead": "Other"}).extra["ni_groups"]
    assert len(groups["Other"]) == 2
    assert {stem.name for stem in groups["Other"]} == {"Lead", "Other"}
    assert [len(groups[s]) for s in ("Drums", "Bass", "Vocals")] == [1, 1, 1]


def test_unmapped_stem_is_rejected_with_a_useful_message(files):
    names = ["Drums", "Bass", "Lead", "Vocals", "Other"]
    paths = files(*names)
    s = StemSet(title="t", stems=[Stem(n, paths[n]) for n in names])

    with pytest.raises(ValueError, match="Lead"):
        s.to_ni_slots()


def test_missing_slot_is_rejected(files):
    paths = files("Drums", "Bass")
    s = StemSet(title="t", stems=[Stem(n, paths[n]) for n in ("Drums", "Bass")])
    with pytest.raises(ValueError, match="no stems for NI slot"):
        s.to_ni_slots()


def test_folding_does_not_mutate_the_original(files):
    names = ["Drums", "Bass", "Lead", "Vocals", "Other"]
    paths = files(*names)
    s = StemSet(title="t", stems=[Stem(n, paths[n]) for n in names])

    s.to_ni_slots({"Lead": "Other"})
    assert len(s.stems) == 5
    assert "ni_groups" not in s.extra


def test_tags_omit_empty_values(files):
    paths = files("Drums")
    s = StemSet(title="Song", artist="Artist", stems=[Stem("Drums", paths["Drums"])])
    tags = s.tags()
    assert tags == {"title": "Song", "artist": "Artist"}

    s2 = StemSet(title="Song", artist="A", bpm=128.0, year="2024",
                 stems=[Stem("Drums", paths["Drums"])])
    assert s2.tags()["BPM"] == "128"
    assert s2.tags()["date"] == "2024"


def test_version_comes_from_the_project_metadata():
    """Hardcoding it once left every stem file claiming 0.1.0 after 0.1.1 shipped."""
    import tomllib
    from pathlib import Path

    import bejeweled

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject, "rb") as f:
        assert bejeweled.__version__ == tomllib.load(f)["project"]["version"]
