# Fortnite Festival
Fortnite Festival is supported for ripping stems, ripping requires Epic's key database, `keys.bin`, this is not included and you must source it yourself. Point `BEJEWELED_KEYS` at your copy, or put it in `~/.config/bejeweled/keys.bin`.

```sh
bejeweled festival list "chappell roan"
bejeweled festival rip "HOT TO GO"
```

`list` matches on title or artist and the filter is optional, `rip` takes the first match, so narrow the query if it picks the wrong track. If a rip fails saying there is no matching key, your `keys.bin` is older than the track.

## Stems
Festival serves a track as one 10 channel file, five stereo pairs, one pair per part. bejeweled splits them out and folds them into the 4 slots the stem format has.

| Festival part | NI slot |
| --- | --- |
| Drums | Drums |
| Bass | Bass |
| Lead | Other |
| Vocals | Vocals |
| Other | Other |

Lead and Other are summed because the stem format has a single slot for melodic content. Pass `--format files` to keep all five as separate stems instead, nothing is folded in that case.

bejeweled reads the running order from each track rather than assuming it, so a change at Epic's end does not silently swap your stems around.

## Metadata
Festival provides a significant amount of metadata about tracks that we can use, bejeweled uses the title, artist, musical key, mode, BPM, release year and cover art, which are all written into the stem file.

| Written | From | Stored as |
| --- | --- | --- |
| Title | `tt`, plus the configured suffix | `©nam` |
| Artist | `an` | `©ART` |
| Year | `ry` | `©day` |
| Key | `mk` + `mm`, as `Ab` / `Abm` | iTunes freeform `initialkey` |
| BPM | `mt` | `tmpo`, and freeform `BPM` |
| Cover art | `au` | `covr` |
| Provenance | this tool and the source | `©cmt` |

bejeweled writes the BPM and key itself because FFmpeg's MP4 muxer silently drops them.

Festival cuts are often a different mix from the commercial release, so the title gets the `(FN)` marker to keep the two apart in a library. Pass `--suffix` to change it, or `--no-suffix` to drop it.

## Count-in
Every Festival track has a metronome count-in on the Other stem before the music starts, this varies depending on the track, so by default it is automatically detected and removed. Pass `--keep-countin`, or set `trim_countin = false` to disable it entirely.

bejeweled finds the count-in by measuring the Other stem and looking for the grid its transients sit on. The tempo Festival publishes only sets the search range, because a count-in does not always run at it. What marks a run out as a metronome is that it opens the file, that every click is struck at the same level, that it is heard against silence, and that the clicks stop at the downbeat. A track whose count-in cannot be read confidently is left alone rather than guessed at, so the worst case is a track that still has its count-in, not one cut in the wrong place.

A few tracks play music on the Other stem underneath their own clicks. Those are left alone on purpose: the clicks and the music share a stem, so there is no cut that takes one without the other, and silencing the clicks would silence the music with them. Where the music starts after the last click instead, the count-in is removed as usual, and the cut still lands on the downbeat, so anything the Other stem plays in the beat before it is lost.

Trimming stops short of the downbeat when a stem starts before it, some songs open with a pickup a fraction of a beat early and cutting on the downbeat would clip it. Where the pickup reaches further back, the count-in is still playing over it and no one cut keeps the pickup and loses the clicks, so the clicks in front of the downbeat are silenced on the Other stem instead.

Removing the count-in also means the file starts on the downbeat, or just before it, which is usually what you want when Mixxx detects the beatgrid.
