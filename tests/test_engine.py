"""Engine DJ output, against a stand-in library and stems built on the fly."""
import os
import sqlite3
import subprocess

import pytest

from bejeweled import engine, jobs
from bejeweled import ffmpeg as ff
from bejeweled.stems import NI_SLOTS, Stem, StemSet
from bejeweled.writers import engine_stem, ni_stem

# Any sixteen bytes do for a round trip. The real key is not in this repository.
KEY = bytes(range(16))

# The parts of Engine's 3.0.2 schema a track insert touches, triggers included, since
# it is the triggers that hand out the origin ids
SCHEMA = """
CREATE TABLE Information (id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT,
    schemaVersionMajor INTEGER, schemaVersionMinor INTEGER, schemaVersionPatch INTEGER,
    currentPlayedIndiciator INTEGER, lastRekordBoxLibraryImportReadCounter INTEGER);
CREATE TABLE AlbumArt (id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT, albumArt BLOB);
CREATE TABLE Track (id INTEGER PRIMARY KEY AUTOINCREMENT, playOrder INTEGER,
    length INTEGER, bpm INTEGER, year INTEGER, path TEXT, filename TEXT, bitrate INTEGER,
    bpmAnalyzed REAL, albumArtId INTEGER, fileBytes INTEGER, title TEXT, artist TEXT,
    album TEXT, genre TEXT, comment TEXT, label TEXT, composer TEXT, remixer TEXT,
    key INTEGER, rating INTEGER, albumArt TEXT, timeLastPlayed DATETIME, isPlayed BOOLEAN,
    fileType TEXT, isAnalyzed BOOLEAN, dateCreated DATETIME, dateAdded DATETIME,
    isAvailable BOOLEAN, isMetadataOfPackedTrackChanged BOOLEAN,
    isPerfomanceDataOfPackedTrackChanged BOOLEAN, playedIndicator INTEGER,
    isMetadataImported BOOLEAN, pdbImportKey INTEGER, streamingSource TEXT, uri TEXT,
    isBeatGridLocked BOOLEAN, originDatabaseUuid TEXT, originTrackId INTEGER,
    streamingFlags INTEGER, explicitLyrics BOOLEAN, lastEditTime DATETIME,
    albumArtSourceHash CHAR(40),
    CONSTRAINT C_originDatabaseUuid_originTrackId UNIQUE (originDatabaseUuid, originTrackId),
    CONSTRAINT C_path UNIQUE (path),
    FOREIGN KEY (albumArtId) REFERENCES AlbumArt (id) ON DELETE RESTRICT);
CREATE TABLE PerformanceData (trackId INTEGER PRIMARY KEY, trackData BLOB,
    overviewWaveFormData BLOB, beatData BLOB, quickCues BLOB, loops BLOB,
    thirdPartySourceId INTEGER, activeOnLoadLoops INTEGER,
    FOREIGN KEY(trackId) REFERENCES Track(id) ON DELETE CASCADE ON UPDATE CASCADE);
CREATE TRIGGER trigger_after_insert_Track_fix_origin AFTER INSERT ON Track
    WHEN IFNULL(NEW.originTrackId, 0) = 0 OR IFNULL(NEW.originDatabaseUuid, '') = ''
BEGIN
    UPDATE Track SET originTrackId = NEW.id,
        originDatabaseUuid = (SELECT uuid FROM Information) WHERE track.id = NEW.id;
END;
CREATE TRIGGER trigger_after_insert_Track_insert_performance_data AFTER INSERT ON Track
BEGIN
    INSERT INTO PerformanceData(trackId) VALUES(NEW.id);
END;
"""

UUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(scope="module")
def ffmpeg():
    try:
        return ff.find_ffmpeg()
    except ff.FFmpegError:
        pytest.skip("FFmpeg not available")


@pytest.fixture(scope="module")
def stem_set(ffmpeg, tmp_path_factory):
    """Four tones an octave apart, so a channel pair says which stem it holds."""
    folder = tmp_path_factory.mktemp("stems")
    stems = []
    for slot, freq in zip(NI_SLOTS, (110, 220, 440, 880)):
        path = folder / f"{slot}.wav"
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
             "-i", f"sine=frequency={freq}:duration=1:sample_rate=44100",
             "-ac", "2", str(path)],
            check=True,
        )
        stems.append(Stem(name=slot, path=str(path)))
    return StemSet(title="Test Track", artist="Tester", album="Tests", year="2020-05-01",
                   bpm=143.297, key="F#m", stems=stems)


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "Engine Library"
    (root / "Database2").mkdir(parents=True)
    db = sqlite3.connect(root / "Database2" / "m.db")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO Information (uuid, schemaVersionMajor, schemaVersionMinor, "
               "schemaVersionPatch) VALUES (?, 3, 0, 2)", (UUID,))
    db.commit()
    db.close()
    monkeypatch.setattr(engine, "is_running", lambda: False)
    monkeypatch.setattr(engine, "backup_dir", lambda: str(tmp_path / "backups"))
    return engine.Library(str(root))


def _rows(library, sql):
    db = sqlite3.connect(library.database)
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute(sql)]
    finally:
        db.close()


# ------------------------------------------------------------------------- the file

def test_keys_follow_the_circle_of_fifths():
    # The five read back from a real library
    assert [engine.key_number(k) for k in ("C", "G", "D", "A", "F#m")] == [0, 2, 4, 6, 7]
    assert [engine.key_number(k) for k in engine.KEYS] == list(range(24))
    # Either spelling of the same note
    assert engine.key_number("Gb") == engine.key_number("F#")
    assert engine.key_number("C#m") == engine.key_number("Dbm")
    assert engine.key_number(None) is None
    assert engine.key_number("H") is None


def test_stems_are_one_eight_channel_track_in_engines_order(stem_set, tmp_path, ffmpeg):
    out = str(tmp_path / "1 x.stems")
    engine_stem.write(stem_set, out, KEY)

    plain = str(tmp_path / "plain.mp4")
    engine_stem.decrypt_file(out, plain, KEY)
    stream, = ff.probe(ffmpeg, plain)["streams"]
    assert (stream["codec_name"], stream["channels"], stream["sample_rate"]) == ("aac", 8, "44100")

    # Each pair's pitch, from how often it crosses zero in the middle half second
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", plain, "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    samples = memoryview(raw).cast("h")
    frames = len(samples) // 8
    found = []
    for pair in range(4):
        channel = samples[pair * 2::8][frames // 4:frames // 4 + 22050]
        crossings = sum(1 for a, b in zip(channel, channel[1:]) if (a < 0) != (b < 0))
        found.append(crossings)
    tones = dict(zip(NI_SLOTS, (110, 220, 440, 880)))
    for crossings, slot in zip(found, engine_stem.ENGINE_SLOTS):
        assert crossings == pytest.approx(tones[slot], rel=0.05)


def test_every_packet_is_padded_and_encrypted(stem_set, tmp_path):
    out = str(tmp_path / "enc.stems")
    engine_stem.write(stem_set, out, KEY)

    plain, again = str(tmp_path / "plain.mp4"), str(tmp_path / "again.stems")
    count = engine_stem.decrypt_file(out, plain, KEY)
    assert count > 40  # a second of audio at 1024 samples a packet
    assert os.path.getsize(out) > os.path.getsize(plain)
    # Encrypting is the exact inverse, which is what lets Engine's own files be read
    assert engine_stem.encrypt_file(plain, again, KEY) == count
    with open(out, "rb") as a, open(again, "rb") as b:
        assert a.read() == b.read()


def test_the_wrong_key_is_noticed(stem_set, tmp_path):
    out = str(tmp_path / "enc.stems")
    engine_stem.write(stem_set, out, KEY)
    with pytest.raises(ValueError, match="wrong key"):
        engine_stem.decrypt_file(out, str(tmp_path / "plain.mp4"), bytes(16))
    with pytest.raises(ValueError, match="16 bytes"):
        engine_stem.write(stem_set, out, b"short")


# ---------------------------------------------------------------------- the library

def test_key_and_library_come_from_the_flag_then_the_environment_then_the_config(monkeypatch):
    monkeypatch.delenv("BEJEWELED_ENGINE_KEY", raising=False)
    monkeypatch.delenv("BEJEWELED_ENGINE_LIBRARY", raising=False)
    cfg = {"engine": {"key": "00" * 16, "library": "/music/Engine Library"}}
    assert engine.find_key(None, cfg) == bytes(16)
    assert engine.find_key("ff" * 16, cfg) == b"\xff" * 16
    assert engine.find_library(None, cfg) == "/music/Engine Library"
    monkeypatch.setenv("BEJEWELED_ENGINE_KEY", "11" * 16)
    assert engine.find_key(None, cfg) == b"\x11" * 16

    with pytest.raises(engine.EngineError, match="32 hex"):
        engine.find_key("abc", {})
    monkeypatch.delenv("BEJEWELED_ENGINE_KEY")
    with pytest.raises(engine.EngineError, match="not distributed"):
        engine.find_key(None, {})
    with pytest.raises(engine.EngineError, match="--engine-library"):
        engine.find_library(None, {})


def test_a_folder_that_is_not_a_library_is_refused(tmp_path):
    with pytest.raises(engine.EngineError, match="not an Engine DJ library"):
        engine.Library(str(tmp_path))


def test_an_unknown_schema_is_left_alone(library):
    db = sqlite3.connect(library.database)
    db.execute("UPDATE Information SET schemaVersionMajor = 4")
    db.commit()
    db.close()
    with pytest.raises(engine.EngineError, match="schema 4.0.2"):
        engine.Library(library.root)


def test_nothing_is_written_while_engine_is_open(library, monkeypatch):
    monkeypatch.setattr(engine, "is_running", lambda: True)
    with pytest.raises(engine.EngineError, match="Quit it first"):
        with library.writing():
            pass
    assert not os.path.exists(engine.backup_dir())


def test_a_track_is_added_the_way_engine_imports_one(library, stem_set, tmp_path):
    track_file = str(tmp_path / "Music" / "Test Track.stem.mp4")
    os.makedirs(os.path.dirname(track_file))
    ni_stem.write(stem_set, track_file)

    with library.writing() as db:
        track = library.register(db, track_file, stem_set)

    assert (track.origin_id, track.origin_uuid, track.added) == (1, UUID, True)
    assert track.stems_name == f"1 {UUID}.stems"
    row, = _rows(library, "SELECT * FROM Track")
    assert row["path"] == "../Music/Test Track.stem.mp4"
    assert row["filename"] == "Test Track.stem.mp4"
    assert (row["title"], row["artist"], row["album"], row["year"]) == (
        "Test Track", "Tester", "Tests", 2020)
    assert (row["bpm"], row["bpmAnalyzed"], row["key"]) == (143, 143.297, 7)
    assert (row["fileType"], row["length"], row["isAnalyzed"]) == ("mp4", 1, 0)
    assert row["fileBytes"] == os.path.getsize(track_file)
    # What a source did not give stays empty, as it does in a row Engine wrote
    assert row["genre"] is None and row["albumArtId"] is None and row["lastEditTime"] is None
    # Engine's trigger made the analysis row, and bejeweled left it empty
    perf, = _rows(library, "SELECT * FROM PerformanceData")
    assert perf["trackId"] == 1 and perf["beatData"] is None

    # The database from before the first write is kept
    kept = os.path.join(engine.backup_dir(), f"{UUID}.m.db")
    assert sqlite3.connect(kept).execute("SELECT count(*) FROM Track").fetchone() == (0,)


def test_a_track_already_in_the_library_is_found_not_added(library, stem_set, tmp_path):
    track_file = str(tmp_path / "Test Track.stem.mp4")
    ni_stem.write(stem_set, track_file)
    with library.writing() as db:
        first = library.register(db, track_file, stem_set)
    with library.writing() as db:
        second = library.register(db, track_file, stem_set)
    assert (second.origin_id, second.added) == (first.origin_id, False)
    assert len(_rows(library, "SELECT id FROM Track")) == 1


def test_stems_are_named_by_origin_not_by_row(library, stem_set, tmp_path):
    """A library synced from another keeps that library's ids, and Engine looks for the
    stems under those."""
    track_file = str(tmp_path / "Test Track.stem.mp4")
    ni_stem.write(stem_set, track_file)
    db = sqlite3.connect(library.database)
    db.execute("INSERT INTO Track (path, originTrackId, originDatabaseUuid) VALUES (?, 76, 'other')",
               (os.path.relpath(track_file, library.root).replace(os.sep, "/"),))
    db.commit()
    db.close()

    with library.writing() as db:
        track = library.register(db, track_file, stem_set)
    assert library.stems_path(track) == os.path.join(library.root, "Stems", "76 other.stems")


def test_a_failed_write_leaves_the_library_as_it_was(library, stem_set, tmp_path, monkeypatch):
    track_file = str(tmp_path / "Test Track.stem.mp4")
    ni_stem.write(stem_set, track_file)

    def fail(*args, **kwargs):
        raise ff.FFmpegError("encoder fell over")

    monkeypatch.setattr(engine_stem, "_encode", fail)
    with pytest.raises(ff.FFmpegError):
        jobs.write_engine(stem_set, track_file, (library, KEY), None)

    assert _rows(library, "SELECT id FROM Track") == []
    assert _rows(library, "SELECT trackId FROM PerformanceData") == []
    assert not os.path.exists(os.path.join(library.root, "Stems", f"1 {UUID}.stems"))


def test_the_job_adds_the_track_and_writes_its_stems(library, stem_set, tmp_path):
    track_file = str(tmp_path / "Test Track.stem.mp4")
    ni_stem.write(stem_set, track_file)

    stems = jobs.write_engine(stem_set, track_file, (library, KEY), None)

    assert stems == os.path.join(library.root, "Stems", f"1 {UUID}.stems")
    assert engine_stem.decrypt_file(stems, str(tmp_path / "plain.mp4"), KEY) > 40
    assert len(_rows(library, "SELECT id FROM Track")) == 1


def test_an_engine_job_fails_before_any_work_without_a_key(library, monkeypatch):
    monkeypatch.delenv("BEJEWELED_ENGINE_KEY", raising=False)
    with pytest.raises(engine.EngineError, match="no Engine DJ key"):
        jobs.engine_target("engine", {}, library.root, None)
    assert jobs.engine_target("ni-stem", {}, None, None) is None
    with pytest.raises(ValueError, match="unknown format"):
        jobs._format("serato", {})


# ------------------------------------------------------------------- from a stem file

def test_a_stem_file_is_read_back_as_its_four_stems(stem_set, tmp_path):
    from bejeweled.sources import local

    track_file = str(tmp_path / "Test Track.stem.mp4")
    ni_stem.write(stem_set, track_file)

    read = local.from_stem_file(track_file, str(tmp_path / "work"))
    assert read.names() == list(NI_SLOTS)
    assert (read.title, read.artist, read.album, read.year) == (
        "Test Track", "Tester", "Tests", "2020")
    assert (read.bpm, read.key, read.master) == (143.297, "F#m", track_file)

    with pytest.raises(ValueError, match="not a stem file"):
        local.from_stem_file(stem_set.stems[0].path, str(tmp_path / "work"))


def test_a_stem_file_gains_engine_stems_where_it_stands(library, stem_set, tmp_path, ffmpeg,
                                                        monkeypatch):
    monkeypatch.setenv("BEJEWELED_ENGINE_KEY", KEY.hex())
    track_file = str(tmp_path / "Test Track.stem.mp4")
    ni_stem.write(stem_set, track_file)
    before = os.path.getmtime(track_file)

    written = jobs.stem_file_to_engine(track_file, {}, engine_library=library.root)

    assert written.path == track_file and os.path.getmtime(track_file) == before
    assert written.engine_stems == os.path.join(library.root, "Stems", f"1 {UUID}.stems")
    row, = _rows(library, "SELECT title, path, key FROM Track")
    assert row == {"title": "Test Track", "path": "../Test Track.stem.mp4", "key": 7}

    # Vocals are the stem file's last track and Engine's first pair
    plain = str(tmp_path / "plain.mp4")
    engine_stem.decrypt_file(written.engine_stems, plain, KEY)
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", plain, "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    samples = memoryview(raw).cast("h")
    channel = samples[0::8][len(samples) // 32:len(samples) // 32 + 22050]
    crossings = sum(1 for a, b in zip(channel, channel[1:]) if (a < 0) != (b < 0))
    assert crossings == pytest.approx(880, rel=0.05)

    # Converting it again finds the track, and only replaces the stems
    again = jobs.stem_file_to_engine(track_file, {}, engine_library=library.root)
    assert again.engine_stems == written.engine_stems
    assert len(_rows(library, "SELECT id FROM Track")) == 1
