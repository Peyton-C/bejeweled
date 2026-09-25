# Separation
bejeweled splits any mixed song into stems with demucs, the model stemgen is built on. It takes an ordinary stereo file, a 5.1 album, or a multichannel Dolby Atmos render.

```sh
bejeweled separate "Song.flac"
bejeweled separate "Song.flac" --format files
bejeweled separate "Song (Atmos).wav" --layout 7.1.4
bejeweled separate ~/Music/Atmos/                   # every song in a folder
bejeweled separate ~/Music/Atmos/*.wav
```

A folder is read one level deep. In a batch a file that fails is reported and skipped, and the failures are listed again at the end.

demucs is not installed with bejeweled, because it brings several gigabytes of PyTorch with it. Install it yourself, and point `BEJEWELED_DEMUCS` at it if it is not on your `PATH`.

```sh
uv tool install demucs --with soundfile
```

The `soundfile` extra is required, the published package leaves out a dependency it needs to run.

On Linux with an AMD GPU, install demucs against PyTorch's ROCm build instead, using the index URL the PyTorch install selector gives for your ROCm version. ROCm presents itself as `cuda`, so demucs picks the GPU without being told.

```sh
uv tool install demucs --with soundfile --index https://download.pytorch.org/whl/rocm7.1
```

## Stems
demucs gives four stems, which map one to one onto the stem format's slots. Title, artist, album, year, BPM, key and cover art are read from the file's own tags, and the title falls back to the file name.

| Option | Config | Default |
| --- | --- | --- |
| `--model` | `model` | `htdemucs` |
| `--device` | `device` | `mps` on Apple Silicon, otherwise whatever demucs picks |

`htdemucs_ft` is slightly cleaner and four times slower. `htdemucs_6s` also splits out guitar and piano, and both are folded into Other in a stem file.

demucs always outputs at 44.1 kHz, whatever the input. Leave `sample_rate` at 44100, setting 48000 gives you upsampled stems rather than better ones.

Stems are written as 32-bit float with `--format files`.

## Surround and Atmos
A multichannel file is not simply folded to stereo and separated. bejeweled folds it in groups by where the mixer placed things, separates each group, and sums each stem back together.

| Group | Channels |
| --- | --- |
| Bed | L, R, C, LFE |
| Surround | side and rear surrounds |
| Wide | Lw, Rw |
| Height | every top channel |

This works because a mixer who puts pads, synths and backing vocals in the surrounds and heights has already pulled them apart from what stays in the bed. The result has noticeably less bleed on Other and Vocals than separating the stereo fold. Drums and bass barely change, since they sit in the bed in almost every mix.

The groups always sum to exactly the stereo fold, so the mixdown in the stem file is the same whichever way it was separated. The fold keeps L and R as they are, sends C and LFE to both sides at -3 dB, and every other channel to its own side at -3 dB.

A file that declares its channel layout, as most 5.1 album rips do, is read by it. An Atmos render captured through a loopback device declares nothing, so bejeweled goes by the channel count instead. Pass `--layout` when that guess is wrong.

| Channels | Read as | Channel order |
| --- | --- | --- |
| 6 | 5.1 | L R C LFE Ls Rs |
| 8 | 7.1 | L R C LFE Lss Rss Lrs Rrs |
| 10 | pass `--layout 5.1.4` or `7.1.2` | |
| 12 | 7.1.4 | L R C LFE Lss Rss Lrs Rrs Ltf Rtf Ltr Rtr |
| 16 | 9.1.6 | L R C LFE Lss Rss Lrs Rrs Lw Rw Ltf Rtf Ltm Rtm Ltr Rtr |

`5.1.2` is also accepted with `--layout`. A file declaring a layout bejeweled cannot group, such as hexagonal, is refused rather than guessed at.

Expect a 16 channel render to take four times as long as its stereo master, and a few gigabytes of scratch space beside the output while it runs.

## Title marker
The song is yours but the stems are a separation, so bejeweled marks the title to keep them from passing for real stems of the same song. Stems cut from a render are a different separation from stems cut from the stereo master, so the marker also says which it came from.

| Input | Marker |
| --- | --- |
| Stereo or mono | `(DE)` |
| 5.1 or 7.1 | `(DE SR)` |
| Atmos, with height channels | `(DE AT)` |

The marker names the input, the stems themselves are always stereo. Pass `--suffix` to change it, or `--no-suffix` to drop it, and set `mark_titles = false` under `[separate]` to turn it off entirely.
