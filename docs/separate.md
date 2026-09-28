# Separation
bejeweled splits any mixed song into stems. It takes an ordinary stereo file, a 5.1 album, or a multichannel Dolby Atmos render.

```sh
bejeweled separate "Song.flac"
bejeweled separate "Song.flac" --format files
bejeweled separate "Song (Atmos).wav" --layout 7.1.4
bejeweled separate "Song.flac" --separator demucs
bejeweled separate ~/Music/Atmos/                   # every song in a folder
bejeweled separate ~/Music/Atmos/*.wav
```

A folder is read one level deep. In a batch a file that fails is reported and skipped, and the failures are listed again at the end.

## Separators
| Separator | What runs | Needs |
| --- | --- | --- |
| `roformer` (default) | BS-Roformer-SW | audio-separator |
| `hybrid` | a RoFormer vocal model for vocals, demucs `htdemucs_ft` on what is left | audio-separator and demucs |
| `demucs` | demucs `htdemucs` | demucs |

Against real stems of ten Atmos songs, roformer was the cleanest on average and on nine of the ten, most of all on drums. hybrid was a little less clean, but the only one that never did worse than demucs. demucs is several times faster than either.

| 202 s Atmos render | roformer | demucs |
| --- | --- | --- |
| Apple Silicon (MPS) | about 3.5 min | about 30 s |
| RX 9070 XT (ROCm) | about 1 min | about 45 s |

Choose with `--separator`, or `separator` under `[separate]` in the config.

## Installing
Neither separator is installed with bejeweled, because each brings several gigabytes of PyTorch with it. Install what your separator needs, and point `BEJEWELED_AUDIO_SEPARATOR` or `BEJEWELED_DEMUCS` at it if it is not on your `PATH`.

```sh
uv tool install --python 3.13 "audio-separator[cpu]==0.47.0" --with audioread
uv tool install demucs --with soundfile
```

audio-separator is pinned to the version bejeweled was tested with, since bejeweled works around several of its defaults and a newer one could quietly change the stems. Keep to it rather than upgrading. It needs Python 3.13, its RoFormer models fail to load on 3.14. `[cpu]` is right on Apple Silicon too, it still uses the GPU. The `soundfile` extra for demucs is required, the published package leaves out a dependency it needs to run.

bejeweled downloads the models the first time they are needed, 700 MB for roformer and 900 MB more for hybrid, and keeps them in your user cache folder. Point `BEJEWELED_MODEL_DIR` elsewhere to keep them on another drive.

On Linux with an AMD GPU, install both against PyTorch's ROCm build instead, using the index URL the PyTorch install selector gives for your ROCm version. ROCm presents itself as `cuda`, so both pick the GPU without being told. bejeweled runs them with `MIOPEN_FIND_MODE=FAST` and `GLIBC_TUNABLES=glibc.malloc.hugetlb=1`, which make ROCm start and separate noticeably faster, unless you have set either yourself.

```sh
uv tool install --python 3.13 "audio-separator[cpu]==0.47.0" --with audioread --index https://download.pytorch.org/whl/rocm7.1
uv tool install demucs --with soundfile --index https://download.pytorch.org/whl/rocm7.1
```

bejeweled runs the RoFormer at reduced precision and compiles it on first use, which made it about five times faster on ROCm and about 1.7 times on Apple Silicon with no measurable loss. The first separation after installing is a few seconds slower while it compiles.

## Stems
roformer gives six stems, and guitar and piano are folded into Other in a stem file. hybrid and demucs give four, which map one to one onto the stem format's slots. Title, artist, album, year, BPM, key and cover art are read from the file's own tags, and the title falls back to the file name.

| Option | Config | Default |
| --- | --- | --- |
| `--separator` | `separator` | `roformer` |
| `--model` | `model` | `htdemucs` for demucs, `htdemucs_ft` for hybrid |
| `--device` | `device` | `mps` on Apple Silicon, otherwise whatever demucs picks |

`--model` and `--device` apply to demucs, and so to the second half of hybrid. audio-separator picks its own device. `htdemucs_6s` also splits out guitar and piano.

Every separator outputs at 44.1 kHz, whatever the input. Leave `sample_rate` at 44100, setting 48000 gives you upsampled stems rather than better ones.

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

A file that declares its channel layout, as most 5.1 album rips do, is read by it. An Atmos render captured through a loopback device declares nothing, so bejeweled goes by the channel count instead. Pass `--layout` when that guess is wrong. A `layout=9.1.6` entry in the file's comment, which OutOfTheWoods writes, is used ahead of both.

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
The song is yours but the stems are a separation, so bejeweled marks the title to keep them from passing for real stems of the same song. Two separators give audibly different stems of the same song, and stems cut from a render differ from stems cut from the stereo master, so the marker says both.

| Input | roformer | hybrid | demucs |
| --- | --- | --- | --- |
| Stereo or mono | `(RF)` | `(HY)` | `(DE)` |
| 5.1 or 7.1 | `(RF SR)` | `(HY SR)` | `(DE SR)` |
| Atmos, with height channels | `(RF AT)` | `(HY AT)` | `(DE AT)` |

The marker names the input, the stems themselves are always stereo. Pass `--suffix` to change it, or `--no-suffix` to drop it, and set `mark_titles = false` under `[separate]` to turn it off entirely.
