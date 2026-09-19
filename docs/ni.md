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

Where bejeweled obtained the audio rather than the user, the title also gets a marker, `(FN)` for Fortnite Festival. Mixes of the same song can differ between platforms, so the marker keeps them apart in a library instead of letting one quietly stand in for the other.

Audio you supplied yourself is not marked, you already know what it is, but you can ask for one with `--suffix` if you want the origin recorded anyway.

## Inside the file
The format is thinly documented, so these are findings from reading real stem files.

The stem names and colours live at `moov/udta/stem` as raw JSON, with no version or flags header ahead of it. `moov` sits after `mdat`, which means that JSON can be replaced without moving any audio or rewriting a single sample offset, and that is why `recolor` is instant.

Key and BPM are not written by FFmpeg, which silently drops both when muxing MP4, so bejeweled writes them itself. BPM goes in the standard `tmpo` atom and the key in an iTunes freeform atom named `initialkey`, which is where TagLib looks, and therefore where Mixxx and Traktor find it.
