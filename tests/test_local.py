"""Folders of stems, from four files to a whole multitrack."""
import pytest

from bejeweled.sources import local
from bejeweled.stems import NI_SLOTS


@pytest.fixture
def folder(tmp_path):
    """A folder of named empty files, since Stem only checks that the path exists."""
    def make(*names, name="Song"):
        path = tmp_path / name
        path.mkdir()
        for stem in names:
            (path / stem).write_bytes(b"")
        return str(path)
    return make


def test_a_folder_of_four_is_read_as_before(folder):
    s = local.from_folder(folder("drums.wav", "bass.wav", "other.wav", "04_vocals.flac"))
    assert sorted(s.names()) == sorted(NI_SLOTS)
    assert "slots" not in s.extra


def test_a_multitrack_is_placed_by_the_words_in_its_names(folder):
    s = local.from_folder(folder(
        "Kick Stem.wav", "Snare 1 Stem.flac", "Percusion Stem.flac", "Drop Bass 1 Stem.flac",
        "Synth Bass Stem.wav", "808 Stem.wav", "Vocal FX 1 Stem.flac", "Ld Voc Stem.wav",
        "BGV 3 Stem.wav", "Producer Tag.wav", "Guitar Arp Stem.flac", "Toy Piano.wav",
        "Master.wav",
    ))
    slots = s.extra["slots"]
    assert {n for n, slot in slots.items() if slot == "Drums"} == {
        "Kick Stem", "Snare 1 Stem", "Percusion Stem"}
    assert {n for n, slot in slots.items() if slot == "Bass"} == {
        "Drop Bass 1 Stem", "Synth Bass Stem", "808 Stem"}
    assert {n for n, slot in slots.items() if slot == "Vocals"} == {
        "Vocal FX 1 Stem", "Ld Voc Stem", "BGV 3 Stem", "Producer Tag"}
    assert {n for n, slot in slots.items() if slot == "Other"} == {
        "Guitar Arp Stem", "Toy Piano"}
    assert s.master.endswith("Master.wav")
    assert s.extra["unsorted"] == []
    # Each stem keeps its own name, and is summed into its slot only when written
    groups = s.to_ni_slots(slots).extra["ni_groups"]
    assert [len(groups[slot]) for slot in NI_SLOTS] == [3, 3, 2, 4]


@pytest.mark.parametrize("name, slot", [
    # A part of the song names a vocal, unless an instrument is named beside it
    ("Hook Dbl 2", "Vocals"),
    ("Bridge Whisper", "Vocals"),
    ("techno synt chorus +3", "Other"),
    ("RAK Chorus Viola", "Other"),
    ("PreChorus DRM Loop", "Drums"),
    # Words run together are split at a capital
    ("bb-mainBASS", "Bass"),
    ("IndPerc -GP1- Stem", "Drums"),
    ("43 BASS Jazz Bass Fills", "Bass"),
    ("Beat Voc Stem", "Vocals"),
])
def test_names_from_real_multitracks_land_in_their_slot(name, slot):
    assert local._slot_by_words(local._words(name)) == slot


def test_a_name_with_no_known_word_goes_in_other_and_is_reported(folder):
    s = local.from_folder(folder("Kick.wav", "Bass.wav", "Vocals.wav", "Loops.wav",
                                 "Bird.wav"))
    assert s.extra["slots"]["Loops"] == "Other"
    assert s.extra["unsorted"] == ["Bird", "Loops"]


def test_dry_copies_and_bounces_are_left_out(folder):
    s = local.from_folder(folder("Kick.wav", "Bass.wav", "Keys.wav", "Ld Voc Stem.wav",
                                 "Ld Voc Dry Stem.wav", "ACAPELLA.mp3"))
    assert set(s.extra["skipped"]) == {"Ld Voc Dry Stem", "ACAPELLA"}
    assert "Ld Voc Dry Stem" not in s.names()


def test_rock_band_tracks_are_placed_and_its_extras_left_out(folder):
    s = local.from_folder(folder("Backing.flac", "Bass.flac", "drums_kick.flac",
                                 "drums_snare.flac", "drums_kit.flac", "Guitar.flac",
                                 "keys.flac", "Vocals.flac", "Vocals (Dirty).flac",
                                 "crowd.flac"))
    slots = s.extra["slots"]
    assert (slots["Backing"], slots["drums_kit"], slots["keys"]) == ("Other", "Drums", "Other")
    assert slots["Vocals (Dirty)"] == "Vocals"
    assert set(s.extra["skipped"]) == {"Vocals", "crowd"}
    assert s.extra["unsorted"] == []
    # A crowd that is part of the song is named more than that, and stays
    s = local.from_folder(folder("Kick.wav", "Bass.wav", "Vocals.wav", "38 CROWD_1.wav",
                                 name="Physical"))
    assert "38 CROWD_1" in s.extra["slots"]


def test_assign_overrides_the_names_and_can_skip(folder):
    s = local.from_folder(folder("Kick.wav", "Bass.wav", "Keys.wav", "Vocals.wav",
                                 "Loops.wav", "AA.wav", "BB.wav"),
                          assign=[("Loops", "drums"), ("??", "Vocals"), ("BB", "skip")])
    slots = s.extra["slots"]
    assert (slots["Loops"], slots["AA"]) == ("Drums", "Vocals")
    assert "BB" not in slots and s.extra["skipped"]["BB"] == "assigned to skip"
    # Even a folder of one file per slot is a multitrack once placed by hand
    s = local.from_folder(folder("drums.wav", "bass.wav", "other.wav", "vocals.wav",
                                 name="Four"), assign=[("other", "Vocals")])
    assert s.extra["slots"]["other"] == "Vocals"


def test_assign_refuses_what_it_cannot_place(folder):
    path = folder("Kick.wav", "Bass.wav", "Keys.wav", "Vocals.wav", "Loops.wav")
    with pytest.raises(ValueError, match="slots are Drums, Bass, Other, Vocals or skip"):
        local.from_folder(path, assign=[("Loops", "Percussion")])
    with pytest.raises(ValueError, match="Horns matches none"):
        local.from_folder(path, assign=[("Horns", "Other")])


def test_named_slots_need_every_file_assigned(folder):
    path = folder("Kick.wav", "Snare.wav", "Bass.wav", "Sax.wav", "Vocals.wav")
    names = ["Drums", "Bass", "Sax", "Vocals"]
    with pytest.raises(ValueError, match="Bass, Sax, Snare, Vocals are not"):
        local.from_folder(path, names=names, assign=[("Kick", "Drums")])
    with pytest.raises(ValueError, match="Snare is not"):
        local.from_folder(path, names=names, assign=[
            ("Kick", "Drums"), ("Bass", "Bass"), ("Sax", "Sax"), ("Vocals", "Vocals")])
    s = local.from_folder(path, names=names, assign=[
        ("Kick", "Drums"), ("Snare", "Drums"), ("Bass", "Bass"), ("Sax", "Sax"),
        ("Vocals", "Vocals")])
    # A named slot is written in the place of the NI slot at its position
    assert s.extra["slots"]["Sax"] == "Other"
    assert s.extra["slot_names"]["Other"] == "Sax"


@pytest.mark.parametrize("name, title", [
    ("AG_Greedy [Stems]", "AG_Greedy"),
    ("Get Into It (Yuh) Stems + Acapella", "Get Into It (Yuh)"),
    ("Streets Stems", "Streets"),
    ("10 7 rings", "10 7 rings"),
    ("Stems", "Stems"),
])
def test_the_folder_title_drops_the_word_stems(name, title):
    assert local.folder_title(f"/x/{name}") == title
