# bejeweled
A Stem multi-tool for creating Native Instruments stem files.

Designed for use with Mixxx 2.6, but it should be compatible with Traktor and other DJ software that supports the Native Instruments format.

## Install

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and FFmpeg:

```sh
brew install uv ffmpeg          # macOS
```

```sh
git clone https://github.com/Peyton-C/bejeweled.git
cd bejeweled
uv run bejeweled --help
```

FFmpeg is taken from your `PATH`. Set `BEJEWELED_FFMPEG` to override it.

## Use

```sh
bejeweled festival list                  # browse Fortnite Festival's catalogue
bejeweled festival list "chappell roan"

bejeweled festival rip "Kill Bill"                  # -> Kill Bill (FN).stem.mp4
bejeweled festival rip "Kill Bill" --format files   # separate stems, no container
bejeweled festival rip "Kill Bill" --no-suffix      # drop the title marker

bejeweled convert ./my-stems/        # build a stem file from a folder of stems
bejeweled info track.stem.mp4        # show a stem file's metadata
```

Each source is its own subcommand, so `bejeweled festival --help` lists what Festival takes. Settings that would otherwise be typed every time live in a config file, written with `bejeweled config --init`.

## Sources and formats

| Doc | Covers |
| --- | --- |
| [Fortnite Festival](docs/festival.md) | Ripping tracks, the metadata Festival provides, and the count-in it puts on every song |
| [Native Instruments stems](docs/ni.md) | What a `.stem.mp4` contains, which codecs work where, and what bejeweled writes inside it |

## Colours

Mixxx reads the colour of each stem out of the file, so the palette written here is
what appears on screen. That makes it an accessibility setting rather than decoration,
and it is configurable throughout.

```sh
bejeweled palette                                   # measure the built-in palettes
bejeweled palette --background "#333941,#413C33"    # against your skin's backgrounds

bejeweled festival rip "Kill Bill" --palette vivid-dark
bejeweled recolor *.stem.mp4 --palette vivid-dark
```

Some skins give each deck its own background — Deere's are `#333941` and `#413C33` —
so several can be configured and a palette is judged on its worst one. This matters:
the default `okabe-ito` palette drops to 2.83:1 on Deere's decks, below the 3:1 floor,
because those backgrounds are lighter than the dark skins it was chosen against.

`recolor` rewrites only the metadata atom, so it is instant on any file size and does
not re-encode the audio. It works on stem files from any tool, not just this one.

Two separate things decide whether a palette works, and they need different measures:

- **Visible against the background** — a luminance question, measured with the WCAG contrast ratio. The bar is 3:1, from WCAG 2.1 non-text contrast.
- **Distinguishable from each other** — a perceptual-colour question, measured with CIEDE2000. Scoring this by luminance would fail every palette built from equally bright hues, since two opposite hues of the same brightness sit near 1:1.

Built-in palettes:

| Name | For |
| --- | --- |
| `okabe-ito` | Colourblind-safe default, matching stemgen and NI tooling |
| `vivid-dark` | Same hues at higher luminance, for dark skins |
| `deep-light` | Darkened hues, for light skins |
| `max-separation` | Maximum contrast, at the cost of colourblind-safety |

Set your own with `bejeweled config --init`, either by name or per slot.

## Development

```sh
uv run pytest
```

The CIEDE2000 implementation is checked against the Sharma et al. (2005) reference vectors, including the hue-rotation cases where implementations typically go wrong.

See [AGENTS.md](AGENTS.md) for how the project is laid out and how its docs are written.

## License

MIT
