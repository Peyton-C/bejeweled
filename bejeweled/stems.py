"""The canonical stem model every source produces and every writer consumes."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

# NI's four slots, in the order they appear in a .stem.mp4
NI_SLOTS = ("Drums", "Bass", "Other", "Vocals")

# Okabe-Ito colourblind-safe palette, matching what stemgen writes
NI_COLORS = ("#009E73", "#D55E00", "#CC79A7", "#56B4E9")


@dataclass
class Stem:
    """One component of a song, as a stereo audio file on disk."""

    name: str
    path: str
    color: str | None = None

    def __post_init__(self):
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"stem {self.name!r}: {self.path}")


@dataclass
class StemSet:
    """A song split into parts, plus the mixdown they sum to.

    Sources build this; writers render it. A set may hold more stems than any one
    output format accepts - Fortnite Festival gives five, NI takes four - so folding
    happens at write time via `to_ni_slots`, never in the source.
    """

    title: str
    stems: list[Stem]
    master: str | None = None
    artist: str | None = None
    album: str | None = None
    year: str | None = None
    bpm: float | None = None
    key: str | None = None
    cover: str | None = None
    comment: str | None = None
    source: str | None = None
    extra: dict = field(default_factory=dict)

    def get(self, name: str) -> Stem | None:
        """Find a stem by name, ignoring case."""
        for stem in self.stems:
            if stem.name.lower() == name.lower():
                return stem
        return None

    def require(self, name: str) -> Stem:
        stem = self.get(name)
        if stem is None:
            raise KeyError(f"{self.title!r} has no {name!r} stem (has: {self.names()})")
        return stem

    def names(self) -> list[str]:
        return [s.name for s in self.stems]

    def tags(self) -> dict:
        """Metadata worth writing into an output container."""
        pairs = {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "date": self.year,
            "BPM": str(int(self.bpm)) if self.bpm else None,
            "initial_key": self.key,
            "comment": self.comment,
        }
        return {k: v for k, v in pairs.items() if v}

    def describe_source(self, version: str) -> str:
        """The provenance line written into the comment field.

        Worth recording because a stem file gives no other clue where its parts came
        from, and a Festival cut can differ from the commercial release of the same
        song.
        """
        parts = [f"bejeweled {version}"]
        if self.source:
            parts.append(f"source: {self.source}")
        return " | ".join(parts)

    def to_ni_slots(self, merge: dict[str, str] | None = None) -> "StemSet":
        """Fold this set down to NI's four slots, in canonical order.

        `merge` maps a stem this set has onto the slot it should be summed into, which
        is how Festival's separate Lead and Other both land in NI's single Other slot.
        Merging is left to the writer, which owns the mixing; here we only decide the
        grouping.
        """
        merge = {k.lower(): v for k, v in (merge or {}).items()}
        groups: dict[str, list[Stem]] = {slot: [] for slot in NI_SLOTS}

        for stem in self.stems:
            slot = merge.get(stem.name.lower(), stem.name)
            for candidate in NI_SLOTS:
                if slot.lower() == candidate.lower():
                    groups[candidate].append(stem)
                    break
            else:
                raise ValueError(
                    f"stem {stem.name!r} maps to {slot!r}, which is not an NI slot "
                    f"({', '.join(NI_SLOTS)}) - pass a merge rule for it"
                )

        empty = [slot for slot, members in groups.items() if not members]
        if empty:
            raise ValueError(f"no stems for NI slot(s): {', '.join(empty)}")

        return replace(self, extra={**self.extra, "ni_groups": groups})
