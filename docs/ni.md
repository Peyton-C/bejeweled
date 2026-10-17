# Native Instruments Stem Format
Every `.stem.mp4` contains 5 stereo tracks, a mixdown along with 4 stems. Only the mixdown is flagged as the default track, so players that do not understand the format should play it alone and a stem file works anywhere an ordinary audio file does.

The 4 stems are always in the same order, and Other is the slot for melody, instruments and synths.

| Track | Contents |
| --- | --- |
| 0 | Mixdown |
| 1 | Drums |
| 2 | Bass |
| 3 | Other |
| 4 | Vocals |

A source that provides more parts than this is folded down when the file is written, so nothing is lost, it is only summed.

## Multitracks
`bejeweled convert` builds a stem file from a folder of a song's many stems, a kick, three snares and nine backing vocals, as readily as from a folder of four.

```sh
bejeweled convert "./Greedy [Stems]/" --artist "Ariana Grande"
bejeweled convert "./Greedy [Stems]/" --assign Loops=Drums --assign "BGV*=Vocals"
```

bejeweled places each file by the words in its name, and prints where every one went. A name with none of the words it knows goes in Other and is marked with a `?` in that list. Check those, and move any that are wrong with `--assign`, which takes a file's name or a glob of it and a slot, or `skip` to leave the file out.

| Slot | Words, for example |
| --- | --- |
| Vocals | vocal, voc, vox, Ld, BGV, BVox, Dbl, adlib, harm, choir, gang, talkbox, VFX, tag |
| Drums | drum, kick, snare, clap, hat, HH, cymbal, crash, ride, tom, perc, beat |
| Bass | bass, sub, 808 |
| Other | synth, keys, piano, guitar, horns, strings, pad, riff, FX, sample, anything else |

A name with words for two slots goes to the first slot in this table, so Vocal FX is a vocal and Synth Bass is bass. A part of the song, hook, verse, bridge, chorus, intro or outro, is a vocal when nothing else in the name says what it is, so Hook Dbl is a vocal and Techno Synth Chorus a synth. Words run together are split at a capital, so mainBASS is bass.

A dry copy of a stem beside the stem itself, `Ld Voc Dry` beside `Ld Voc`, is left out, and so is an acapella or instrumental bounced from the stems, since either would play that part twice. Of a clean and an explicit take of the same stem, `Vocals` beside `Vocals (Dirty)`, bejeweled keeps the explicit one, assign the other to its slot and skip this one to swap them. Rock Band's audience track, named `crowd`, is left out too. A file named Master or Mix is the mixdown.

The title is the folder's name without the word Stems. Pass `--title` and `--artist` when the folder does not say, as without an artist no cover art is found. `--artist` may be given for several folders at once, such as an album's. Given several folders, `-o` names the folder their stem files go in.

## Slot names
The four slots suit most songs, but not one whose hook is an instrument that would otherwise be buried in Other. `bejeweled convert` can name the slots itself when it builds a stem file from a folder.

```sh
bejeweled convert ./my-stems/ --slots Drums,Music,Sax,Vocals
```

bejeweled matches each name to the file of that name in the folder, and writes them in the order given. The folder must hold exactly those four stems, beside the mixdown if there is one. A multitrack can have named slots too, but its words cannot say which named slot a file belongs in, so `--assign` must place every file.

The names are labels. A player still treats the slots as drums, bass, other and vocals wherever it goes by position, and Engine DJ, which has no names of its own to change, always does.

## Codecs
Native Instruments allows AAC and ALAC. Mixxx is more permissive and accepts any codec as long as every track in the file uses the same one, the sample rate matches, and each track is stereo.

| Codec | Native Instruments | Mixxx |
| --- | --- | --- |
| AAC | yes | yes |
| ALAC | yes | yes |
| FLAC | no | yes |
| Opus | no | yes |
| WAV | no | yes |

Set it with `--codec`, or under `[output]` in the config. Writing anything other than AAC or ALAC produces a file Mixxx reads and Traktor may not.

## Colours
Each stem carries its own colour, stored in the file rather than chosen by the player, so the palette bejeweled writes is what you see on screen. This makes it an accessibility setting, see [Colours](../README.md#colours) for how to configure it.

bejeweled can also restyle existing stem files, including ones it did not create. `bejeweled recolor` replaces the colours in place and never touches the audio, so it is instant whatever the file size and lossless by construction.

## Metadata
bejeweled writes the title, artist, year, key, BPM, cover art and a provenance comment into the file. Tags a source does not provide are left out rather than guessed at.

A song that arrives without cover art, a YouTube mix or a folder of stems, gets the cover of its release from iTunes, or from Deezer when iTunes has none. bejeweled takes a cover only when both the artist and the title match, so a song with no artist tag gets none. Set `find_cover = false` under `[metadata]` to turn this off, or pass `--no-cover` to `convert`. Pass `--cover` with an image to `convert` to use your own instead, which also covers a song neither service has. One cover may be given for several folders, such as an album's.

Each song is looked up on its own, so the songs of one album can come back with different covers when some also appear on a deluxe edition or a remix album. Pass `--album` with `--artist` to look up the album's cover once and give it to every folder. The album's name must match whole, so a deluxe edition is never taken for it. When the album is not found, bejeweled looks up each song instead. `--album` also sets the album tag.

Where bejeweled obtained the audio rather than the user, the title also gets a marker, `(FN)` for Fortnite Festival. Mixes of the same song can differ between platforms, so the marker keeps them apart in a library instead of letting one quietly stand in for the other.

Stems bejeweled separated itself are marked too, `(RF)`, `(DE)` and their variants, so a separation never passes for real stems. See [Separation](separate.md).

Stems you supplied yourself are not marked, you already know what they are, but you can ask for one with `--suffix` if you want the origin recorded anyway.

## Separate stems
`bejeweled convert` unpacks a `.stem.mp4` back into its four stems, whichever tool made it.

```sh
bejeweled convert track.stem.mp4 --format files
```

bejeweled writes them as 32-bit float WAV into a folder beside the stem file, or the one `-o` names. Given several stem files, `-o` names the folder that holds a folder for each. The mixdown is not unpacked.

Stems from an AAC or Opus stem file have already been compressed, and writing them as WAV does not undo that.

## Inside the file
The format is thinly documented, so these are findings from reading real stem files.

The stem names and colours live at `moov/udta/stem` as raw JSON, with no version or flags header ahead of it. `moov` sits after `mdat`, which means that JSON can be replaced without moving any audio or rewriting a single sample offset, and that is why `recolor` is instant.

Key and BPM are not written by FFmpeg, which silently drops both when muxing MP4, so bejeweled writes them itself. BPM goes in the standard `tmpo` atom, which only holds whole numbers, and exactly in an iTunes freeform atom named `BPM`. The key goes in a freeform atom named `initialkey`. Freeform atoms are where TagLib looks, and therefore where Mixxx and Traktor find them.
