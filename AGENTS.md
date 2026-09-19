# Working in this repo

## Naming

The project is always written `bejeweled`, fully lowercase, everywhere: prose, headings,
code comments, commit messages, and the start of a sentence. Never `Bejeweled`, never
`BEJEWELED` outside environment variable names such as `BEJEWELED_KEYS`.

## Source markers

Where bejeweled obtained the audio itself rather than the user supplying it, the title
gets a short marker: `(FN)` for Fortnite Festival, and the equivalent for every source
added later. Mixes of the same song differ between platforms, sometimes substantially,
so the marker stops one quietly standing in for another in a library.

The marker belongs to the source, not to the output format. Each source declares its own
marker and whether it is on by default, as `TITLE_MARKER` and `MARK_TITLES_BY_DEFAULT`.

A source that fetched the audio itself marks titles by default. A source reading audio
the user supplied does not, because they know what it is, but the marker is still there
to switch on: someone converting their Engine DJ stems may well want that recorded.

## What goes where

`README.md` is what a user needs to get in and pointed the right way, nothing more. It
keeps the runnable example commands, including the Fortnite Festival ones, because
trying the tool once is part of getting started. It does not carry the depth.

`docs/` holds one file per source or format, and those files own their subject. Someone
may install bejeweled only to turn Engine DJ stems into NI stems and never touch
Fortnite Festival, so a doc should stand on its own rather than assume the reader came
through another one.

Technical detail is welcome in `docs/`, more so than in the README. Format internals,
atom layouts and how a thing works belong there.

That is mechanism, not evidence, and the distinction matters. How the count-in is found
is mechanism and can be documented. The click durations and decibel thresholds that
prove the method works are evidence, and stay out under the rule below.

## Documentation voice

The house style is set by `docs/festival.md`. Match it. That file was produced by editing
longer, more discursive drafts down, so the clearest way to describe the style is by what
the editing removed.

**Be short.** Roughly half the length of a first draft. State what the tool does and, if
it is not obvious, why. Then stop.

> bejeweled writes the BPM and key itself because FFmpeg's MP4 muxer silently drops them.

That sentence replaced a paragraph naming the atoms, the tagging library, and the DJ
software that reads them. The reason survived because it is load-bearing; the detail did
not, because a reader acting on this does not need it.

**Cut the evidence, keep the consequence.** The test is whether it changes what the
reader does. Measurements, worked examples and the reasoning that justifies a decision
come out. A caveat that would change someone's choice stays in, even when it is
unflattering: a user who writes a codec Traktor cannot open blames bejeweled, not
Traktor, so that warning earns its place.

The count-in section documents that the count-in varies per track and is removed by
default. It does not reproduce the click durations or the decibel thresholds that
establish it, even though those are what make the feature trustworthy.

**Name the tool as the subject.** "bejeweled uses...", "bejeweled writes...". Prefer that
to passive voice or an abstract subject.

**Address the reader directly** for anything they do: "you must source it yourself",
"Point `BEJEWELED_KEYS` at your copy", "Pass `--keep-countin`". This applies where a doc
asks something of the reader. A format reference is mostly descriptive and will barely
use it, which is fine, do not manufacture instructions to satisfy the rule.

**Use a table for any fixed set a reader might look up**, not just field mappings. Track
order, supported codecs and metadata fields are all tables. Prose gets trimmed; tables do
not. The metadata table survived editing untouched while the prose around it was halved.

### Mechanics

- No hard wrapping. One long line per paragraph, however wide it runs.
- No blank line between a heading and the text under it.
- Commas where a dash would be tempting. The existing docs contain no em-dashes.
- No bold or italics in prose. Emphasis comes from sentence structure.
- Headings are short noun phrases: "Metadata", "Count-in", "Fortnite Festival".

### Common corrections

Drafts written without this file in mind tend to need the same fixes: they run about
twice the necessary length, wrap at 88 columns, lean on em-dashes for asides, and explain
the reasoning behind a decision where stating the decision would do.

`docs/` is being written now, so this describes a small and still-growing sample. Where a
later document contradicts this file, the document is right and this file should be
updated.

## Code

Sources produce a `StemSet`; writers render one. Neither knows about the other, so a new
source slots in without touching the rest.

```
sources/           StemSet            writers/
  festival.py  ->  title, stems,  ->    ni_stem.py   .stem.mp4
  local.py         master, tags         (or no writer: --format files)
```

The two axes are deliberately asymmetric. Sources keep multiplying, and each arrives
with its own arguments, so each gets its own subcommand. Outputs do not multiply: NI
stems, or the raw stems with no container. That is the whole list, which is why a flag
suffices. Do not add an output format without asking.

A set may hold more stems than an output format accepts, so folding happens at write
time and the source stays faithful to what it actually received.

Tests run with `uv run pytest`. FFmpeg is required for the audio tests; they skip without
it.

Code comments are the place for the reasoning the docs leave out. Explain why something
is done a particular way, especially where the obvious approach fails, and cite the
measurement or the specific track that proves it.

## Commits

Commit messages are held to the same measure as the docs: short, plain, and specific
about what changed. A subject line in the imperative, then a sentence or two on why,
only when the why is not obvious from the diff.

No essays, and no restating the diff as a list of touched files. Reasoning that needs
more room than that belongs in a code comment beside the thing it explains, where it
stays attached to the code instead of being buried in history.
