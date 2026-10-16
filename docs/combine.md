# Combining
bejeweled builds one set of stems from more than one version of the same song.

```sh
bejeweled combine graft "Work Work (FN).stem.mp4" "Work Bitch.flac" --title "Work Bitch"
bejeweled combine graft ./clean-stems/ "Explicit.flac"
bejeweled combine graft "Clean.stem.mp4" "Explicit.flac" --separator hybrid
bejeweled combine extend "HOT TO GO! (FN).stem.mp4" "HOT TO GO! (Demo Version).flac"
bejeweled combine assemble --mix "Song.flac" --instrumental "Song (Instrumental).flac"
bejeweled combine assemble --instrumental "Instrumental.flac" --acapella "Acapella.flac" --title "Song"
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

## Extending
Real stems often exist for one edit of a song when the one you want is another: an extended mix, a demo with a section the album cut, a radio edit. bejeweled lays the stems along the other version, at whatever offset each stretch needs, and separates only what the other version has and the stems do not. A version that is only shorter needs no separating at all.

The first argument is the stems, as a `.stem.mp4` or a folder of stems, and the second is the other version as an ordinary mixed file. Neither is written to. The two must be the same recording at the same speed, and bejeweled refuses a pair that drifts apart.

| Stretch of the other version | What bejeweled does |
| --- | --- |
| The stems have it | The real stems |
| Only the other version has it | Separates it, with a few seconds either side to set each stem's level against the real ones |
| Silence | Silence |
| Only the stems have it | Leaves it out, and lists it |

Seams go on a beat, found from the stems' BPM tag where they have one. Without a tag bejeweled finds the beat itself, sometimes at half the tempo, which still puts each seam on a beat but not always on the downbeat. After extending bejeweled prints where every stretch came from. Listen across the seams before trusting the file.

The mixdown is the stems' own, with the other version's mix in the separated stretches. Those stretches are only as good as the file they came from, so use the best copy of the other version you have.

## Assembling
A song with no real stems anywhere often still has an official instrumental or acapella. bejeweled separates the song with those beside it, so the separator has less to guess. Give it any two of `--mix`, `--instrumental` and `--acapella`, or all three. None of them is written to.

| Given | Vocals | Drums, bass and other |
| --- | --- | --- |
| Mix and instrumental | The mix less the instrumental | Separated from the instrumental |
| Mix and acapella | The acapella | Separated from the mix less the acapella |
| Instrumental and acapella | The acapella | Separated from the instrumental |
| All three | The acapella | Separated from the instrumental |

An acapella is the vocal stem as it is. An instrumental has no voice to bleed into the other stems, which is where a separation of the full mix loses most. Backing vocals that an instrumental keeps stay with it, and land in Other.

The mix less the instrumental is the vocal untouched only when the two null cleanly. Most instrumentals are mastered apart from their mix and leave some of themselves behind, so bejeweled measures the null and, when it falls short, separates what is left as well. That is still closer to the vocal than a separation of the mix, and bejeweled prints which it did.

An acapella comes out of its mix only when the two are the same master in step. One from another master, or running a little fast or slow, would leave most of the vocal behind, so bejeweled measures how much comes out and, when it falls short, separates the whole mix for the other stems and keeps the acapella as the vocal. Voices the acapella lacks are then in no stem, and bejeweled prints which it did.

With a mix, bejeweled lines each part up to it and matches its level itself, and the mix is the mixdown in the stem file. The parts must be the same recording at the same speed, and bejeweled refuses one that drifts. Without a mix the two parts share nothing to line up by, so they must start together, and their sum is the mixdown.

Every part must be stereo. A part that is not the same edit as the mix, a radio edit's instrumental against the album version, is refused or leaves the difference in the stems.

## Options
`--separator`, `--model` and `--device` apply to every action.

| Option | Config | Default |
| --- | --- | --- |
| `--title` | | graft: the explicit file's title tag, or the stems' title. extend: the other version's title tag, or its file name. assemble: the title tag of the mix, or of the instrumental |
| `--separator` | `separator` under `[separate]` | `roformer` |
| `--model` | `model` under `[separate]` | as for `separate` |
| `--device` | `device` under `[separate]` | as for `separate` |

Pass `--title` to a graft when the two versions are named differently, as a clean edit often is. Artist, BPM, key and cover art come from the clean stems. An assembly takes them from the mix, or from the instrumental when there is no mix, and drops a trailing `(Instrumental)` or `(Acapella)` from the title. The separators and what they need are covered in [Separation](separate.md).

## Title marker
Part of the vocal is a separation, so bejeweled marks the title to keep the file from passing for real stems of the explicit release. The separator's letters follow a plus, and join a marker the stems already carried.

| Stems | roformer | hybrid | demucs |
| --- | --- | --- | --- |
| Unmarked | `(+RF)` | `(+HY)` | `(+DE)` |
| Marked `(FN)` | `(FN +RF)` | `(FN +HY)` | `(FN +DE)` |

An extension that separated anything is marked the same way, `(+RF)` or `(FN +RF)`. One that separated nothing is the real stems cut to another edit, and keeps only the marker the stems had.

An assembly is still a separation, so it is marked as one, with letters for the parts that helped: `AC` for an acapella and `IN` for an instrumental.

| Given | roformer | hybrid | demucs |
| --- | --- | --- | --- |
| Mix and instrumental | `(RF IN)` | `(HY IN)` | `(DE IN)` |
| Mix and acapella | `(RF AC)` | `(HY AC)` | `(DE AC)` |
| Instrumental and acapella, with or without the mix | `(RF AC IN)` | `(HY AC IN)` | `(DE AC IN)` |

Pass `--suffix` to change it, or `--no-suffix` to drop it, and set `mark_titles = false` under `[combine]` to turn it off entirely.
