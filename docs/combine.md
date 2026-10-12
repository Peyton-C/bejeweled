# Combining
bejeweled builds one set of stems from more than one version of the same song.

```sh
bejeweled combine graft "Work Work (FN).stem.mp4" "Work Bitch.flac" --title "Work Bitch"
bejeweled combine graft ./clean-stems/ "Explicit.flac"
bejeweled combine graft "Clean.stem.mp4" "Explicit.flac" --separator hybrid
```

## Grafting
Real stems sometimes exist only for the clean edit of a song. bejeweled takes those stems and the explicit release, separates the vocal from the release, and replaces the vocal stem only where the two differ. Everything else stays the real stems, including the whole of the drums, bass and other.

The first argument is the clean stems, as a `.stem.mp4` or a folder of stems, and the second is the explicit song as an ordinary mixed file. Neither is written to. Use the best copy of the explicit release you have, since the grafted words are only as good as it is.

bejeweled finds the offset between the two itself, so they need not start together. They do need to be the same recording at the same speed and arrangement, and bejeweled refuses a pair that drifts apart rather than grafting out of step.

After a graft bejeweled prints where each one was made. Listen to those places with the vocal soloed before trusting the file, the seams are where a graft shows.

| Difference | What bejeweled does |
| --- | --- |
| A word swapped, reversed or bleeped in the clean edit | Grafts the explicit vocal over it |
| A word muted in the clean edit | Grafts the explicit vocal into the gap |
| Something only the clean stems have | Leaves it, and lists it apart |
| Drums, bass or other changed in the clean edit | Nothing, only the vocal is grafted |

A song whose hook is the censored word has a graft every bar, and with the vocal soloed you can hear it change character each time. In a full mix this is much harder to hear.

The mixdown in the stem file gets the same grafts as the vocal stem and is otherwise left as it was.

## Options
| Option | Config | Default |
| --- | --- | --- |
| `--title` | | the explicit file's title tag, or the stems' title |
| `--separator` | `separator` under `[separate]` | `roformer` |
| `--model` | `model` under `[separate]` | as for `separate` |
| `--device` | `device` under `[separate]` | as for `separate` |

Pass `--title` when the two versions are named differently, as a clean edit often is. Artist, BPM, key and cover art come from the clean stems. The separators and what they need are covered in [Separation](separate.md).

## Title marker
Part of the vocal is a separation, so bejeweled marks the title to keep the file from passing for real stems of the explicit release. The separator's letters follow a plus, and join a marker the stems already carried.

| Stems | roformer | hybrid | demucs |
| --- | --- | --- | --- |
| Unmarked | `(+RF)` | `(+HY)` | `(+DE)` |
| Marked `(FN)` | `(FN +RF)` | `(FN +HY)` | `(FN +DE)` |

Pass `--suffix` to change it, or `--no-suffix` to drop it, and set `mark_titles = false` under `[combine]` to turn it off entirely.
