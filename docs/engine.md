# Engine DJ
bejeweled can add a song to an Engine DJ library together with its stems, so Engine plays those instead of separating the track itself. Engine encrypts its stems and the key is not included, you must source it yourself. Set `BEJEWELED_ENGINE_KEY` to it as 32 hex characters, or `key` under `[engine]` in the config.

```sh
bejeweled festival rip "Kill Bill" --format engine --engine-library "~/Music/Engine Library" -o ~/Music/Stems
bejeweled separate "Song.flac" --format engine -o ~/Music/Stems
bejeweled convert ~/Music/Stems/*.stem.mp4          # stem files you already have
```

`--engine-library` is the folder named Engine Library. Set `library` under `[engine]` in the config to leave it off.

Quit Engine DJ first. bejeweled refuses to write while it is open, because two programs writing to the library can corrupt it.

## What is written
| Written | Where |
| --- | --- |
| The `.stem.mp4` | The `-o` folder |
| A track for it | The library's database |
| A `.stems` file | `Stems` inside the library |

The `.stem.mp4` is the track Engine plays, and Engine remembers where it is. Write it to the folder it will live in, moving it afterwards loses the track.

bejeweled adds the track unanalysed, the way Engine adds one with automatic analysis switched off, so analyse it in Engine as you would any import. bejeweled does not add cover art to the library.

A file already in the library is not added again. bejeweled only replaces its stems, and leaves the track and its analysis as they are.

Before its first write to a library bejeweled copies the database to `engine-backups` in its config folder.

## Existing stem files
`bejeweled convert` gives a `.stem.mp4` its Engine stems without writing it again, whichever tool made it. The file stays where it is and becomes the track, so one already in your library keeps its analysis, cues and playlists and only gains stems.

These stems are decoded from the stem file and encoded again, so from an AAC stem file they are compressed twice. Use `--format engine` when the stems are first made to avoid that.

`convert` on a folder of stems takes `--format engine` too.

## Stems
Engine has the same four stems as the Native Instruments format in a different order, so a source with more parts is folded the same way.

| Channels | Stem |
| --- | --- |
| 1-2 | Vocals |
| 3-4 | Bass |
| 5-6 | Drums |
| 7-8 | Other |

From a rip or a separation, bejeweled encodes them from the source's own stems, not from the `.stem.mp4`, so they are compressed once. They are always AAC at 44.1 kHz, and `--codec` only changes the `.stem.mp4`.

## Inside the file
A `.stems` file is an MP4 holding one 8 channel AAC track and nothing else, no tags and no mixdown. Engine encrypts the audio packets and leaves the container readable: each packet is padded to a 16 byte boundary and encrypted with AES-128 in ECB mode. Engine shows a plain file as having stems and then fails to play them.

Nothing in the database says a track has stems. Engine looks for a file named after the track, `<origin track id> <origin database uuid>.stems`, and the origin is the library the track was first added to. A library copied to a drive keeps those ids, which is why bejeweled reads them back from the database instead of using the track's own id.

## Inside the library
inMusic [documents](https://support.enginedj.com/support/solutions/articles/69000834165) third-party writes to the database and asks that Engine be closed and the schema left unchanged. bejeweled inserts one track and nothing else, and Engine's own triggers give it its origin id and its empty analysis.

bejeweled writes to schema version 3 and leaves a library on any other version alone.
