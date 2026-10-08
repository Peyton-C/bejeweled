"""An Engine DJ library on disk: its database, and where its stems go.

inMusic documents third-party writes to the database, with two rules that shape this
module: Engine must not be running, and the schema must not change. So bejeweled only
ever inserts a Track row, shaped like the one Engine writes when it imports a file with
analysis switched off, and leaves the rest to Engine's own triggers and analysis.

https://support.enginedj.com/support/solutions/articles/69000834165
"""
from __future__ import annotations

import math
import os
import re
import shutil
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass

from . import config
from . import ffmpeg as ff
from .stems import StemSet

# The only schema bejeweled has seen, 3.0.2. A new major version may have changed what
# a Track row means, and a wrong row is worse than a refusal.
SCHEMA_MAJOR = 3

# Engine numbers keys round the circle of fifths, each major followed by its relative
# minor. Five of these were read back from tracks Engine imported (C, G, D, A and F#m)
# and the rest follow the pattern, which is also the order libdjinterop documents.
KEYS = ("C", "Am", "G", "Em", "D", "Bm", "A", "F#m", "E", "Dbm", "B", "Abm",
        "F#", "Ebm", "Db", "Bbm", "Ab", "Fm", "Eb", "Cm", "Bb", "Gm", "F", "Dm")

_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


class EngineError(RuntimeError):
    """The library cannot be written to, and why."""


def key_number(key: str | None) -> int | None:
    """Engine's number for a key written the way bejeweled tags it, Ab or F#m."""
    match = re.fullmatch(r"([A-G])([#b]?)(m?)", (key or "").strip())
    if not match:
        return None
    letter, accidental, minor = match.groups()
    pitch = (_SEMITONES[letter] + {"#": 1, "b": -1, "": 0}[accidental]) % 12
    # A minor key sits beside the major three semitones up, and seven semitones is
    # one step round the circle
    fifths = ((pitch + 3 if minor else pitch) * 7) % 12
    return 2 * fifths + (1 if minor else 0)


def find_key(explicit: str | None = None, cfg: dict | None = None) -> bytes:
    """The key Engine encrypts stems with, which bejeweled does not ship."""
    cfg = cfg if cfg is not None else config.load()
    for candidate in (explicit, os.environ.get("BEJEWELED_ENGINE_KEY"),
                      cfg.get("engine", {}).get("key")):
        if not candidate:
            continue
        try:
            key = bytes.fromhex(candidate.strip())
        except ValueError:
            key = b""
        if len(key) != 16:
            raise EngineError("the Engine DJ key must be 32 hex characters")
        return key
    raise EngineError(
        "no Engine DJ key. Set BEJEWELED_ENGINE_KEY, or key under [engine] in the "
        "config. It is not distributed with this project."
    )


def find_library(explicit: str | None = None, cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else config.load()
    path = explicit or os.environ.get("BEJEWELED_ENGINE_LIBRARY") or cfg.get("engine", {}).get("library")
    if not path:
        raise EngineError(
            "no Engine DJ library. Pass --engine-library, or set library under "
            "[engine] in the config, to the folder named Engine Library."
        )
    return os.path.abspath(os.path.expanduser(path))


def is_running() -> bool:
    """Whether Engine DJ is open on this machine."""
    try:
        if os.name == "nt":
            listed = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Engine DJ.exe", "/NH"],
                                    capture_output=True, text=True, timeout=20).stdout
            return "Engine DJ.exe" in listed
        return subprocess.run(["pgrep", "-x", "Engine DJ"], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def backup_dir() -> str:
    # Outside the library, so Engine never meets a file it did not write
    return os.path.join(config.config_dir(), "engine-backups")


@dataclass
class Track:
    """A track as Engine identifies it, which is by where it was first added.

    A library copied to a drive keeps the ids of the library it came from, and it is
    those that name the stems file, not the row's own id.
    """

    origin_id: int
    origin_uuid: str
    added: bool

    @property
    def stems_name(self) -> str:
        return f"{self.origin_id} {self.origin_uuid}.stems"


class Library:
    def __init__(self, root: str):
        self.root = root
        self.database = os.path.join(root, "Database2", "m.db")
        if not os.path.isfile(self.database):
            raise EngineError(f"{root} is not an Engine DJ library, it has no Database2/m.db")

        db = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        try:
            row = db.execute("SELECT uuid, schemaVersionMajor, schemaVersionMinor, "
                             "schemaVersionPatch FROM Information").fetchone()
        except sqlite3.Error as e:
            raise EngineError(f"{self.database}: {e}") from e
        finally:
            db.close()
        if row is None:
            raise EngineError(f"{self.database} has no library information")
        self.uuid, *version = row
        if version[0] != SCHEMA_MAJOR:
            raise EngineError(
                f"this library is schema {'.'.join(map(str, version))} and bejeweled "
                f"only knows version {SCHEMA_MAJOR}, so it has been left alone"
            )

    def stems_path(self, track: Track) -> str:
        return os.path.join(self.root, "Stems", track.stems_name)

    def check_closed(self) -> None:
        if is_running():
            raise EngineError(
                "Engine DJ is open. Quit it first, writing to a library it has open "
                "can corrupt the database."
            )

    def backup(self) -> str:
        """Keep the database as it was before bejeweled first wrote to this library."""
        kept = os.path.join(backup_dir(), f"{self.uuid}.m.db")
        if not os.path.exists(kept):
            os.makedirs(os.path.dirname(kept), exist_ok=True)
            shutil.copy2(self.database, kept)
        return kept

    @contextmanager
    def writing(self):
        """One transaction on the database, undone whole if anything inside it fails."""
        self.check_closed()
        self.backup()
        db = sqlite3.connect(self.database, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
        except sqlite3.OperationalError as e:
            raise EngineError(f"{self.database}: {e}") from e
        finally:
            db.close()

    def register(self, db: sqlite3.Connection, path: str, stem_set: StemSet,
                 ffmpeg: str | None = None) -> Track:
        """Find the track for this audio file, adding it to the library if it is new.

        A track already there is left exactly as it is, since its row and its
        analysis are Engine's.
        """
        path = os.path.abspath(path)
        relative = os.path.relpath(path, self.root).replace(os.sep, "/")
        found = db.execute("SELECT originTrackId, originDatabaseUuid FROM Track "
                           "WHERE path = ?", (relative,)).fetchone()
        if found:
            return Track(found[0], found[1], added=False)

        info = ff.probe(ff.find_ffmpeg(ffmpeg), path)
        audio = next(s for s in info["streams"] if s.get("codec_type") == "audio")
        stat = os.stat(path)
        year = re.match(r"\d{4}", stem_set.year or "")
        # Engine files a track under the day it was added, at local midnight
        today = time.localtime()
        row = {
            "length": int(float(info["format"]["duration"])),
            "bpm": round(stem_set.bpm) if stem_set.bpm else None,
            "bpmAnalyzed": float(stem_set.bpm) if stem_set.bpm else None,
            "year": int(year.group()) if year else None,
            "path": relative,
            "filename": os.path.basename(path),
            # Rounded up, as Engine does: it files 259060 and 259411 both as 260
            "bitrate": math.ceil(int(audio.get("bit_rate") or 0) / 1000) or None,
            "fileBytes": stat.st_size,
            "title": stem_set.title,
            "artist": stem_set.artist,
            "album": stem_set.album,
            "genre": stem_set.genre,
            "comment": stem_set.comment,
            "key": key_number(stem_set.key),
            "rating": 0,
            "isPlayed": 0,
            "fileType": os.path.splitext(path)[1].lstrip(".").lower(),
            # Left for Engine: the waveform, beat grid and cover art all come from
            # its analysis, and a track marked unanalysed is one it will pick up
            "isAnalyzed": 0,
            "dateCreated": int(getattr(stat, "st_birthtime", stat.st_mtime)),
            "dateAdded": int(time.mktime((today.tm_year, today.tm_mon, today.tm_mday,
                                          0, 0, 0, 0, 0, -1))),
            "isAvailable": 1,
            "isMetadataOfPackedTrackChanged": 0,
            "isPerfomanceDataOfPackedTrackChanged": 0,
            "isMetadataImported": 1,
            "pdbImportKey": 0,
            "isBeatGridLocked": 0,
            "streamingFlags": 0,
            "explicitLyrics": 0,
        }
        # The origin columns are left out on purpose: Engine's insert trigger fills
        # them in, and creates the PerformanceData row beside them
        cursor = db.execute(
            f"INSERT INTO Track ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()))
        origin = db.execute("SELECT originTrackId, originDatabaseUuid FROM Track "
                            "WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if not origin or not origin[0] or not origin[1]:
            raise EngineError("the library did not give the new track an origin id")
        return Track(origin[0], origin[1], added=True)
