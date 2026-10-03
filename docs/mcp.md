# MCP server
bejeweled runs as a Model Context Protocol server with `bejeweled mcp`, so an agent can search Fortnite Festival, download tracks and separate songs. It runs the same code as the command line and reads the same config file.

## Setup
Point an MCP client at `bejeweled mcp`. The server speaks MCP over stdin and stdout, and needs nothing installed beyond bejeweled.

```sh
claude mcp add bejeweled -- uv run --directory /path/to/bejeweled bejeweled mcp
```

Add `--out <folder>` to choose where stem files go. Without it they go to `~/Music/bejeweled`.

Downloading needs `keys.bin`, see [Fortnite Festival](festival.md). Separating needs audio-separator or demucs, see [Separation](separate.md).

## Files
MCP has no way to send a file to a server, so bejeweled exchanges paths. The server runs on your machine: `separate_song` takes the path of an audio file, and every tool that writes a stem file returns its path, which an agent can hand to another local tool.

## Tools
| Tool | What |
| --- | --- |
| `search_festival` | Festival tracks whose title or artist has every word of `query`, narrowed by `bpm_min`, `bpm_max` and `key`, a page at a time. Each has an id, title, artist, year, BPM, key and length, its album and genre where Festival gives them, and its `path` if it is already downloaded |
| `download_festival` | Downloads the track with an `id` from `search_festival` and writes a stem file. `count_in` says whether the count-in was removed or left in |
| `separate_song` | Separates the audio file at `path` and writes a stem file. Takes `separator` and `layout`, which default as the command line's do |

The server always writes a `.stem.mp4`, whatever `format` the config sets. Everything else, the codec, the palette, the title markers and the count-in, follows the config.

## Repeated calls
A song already in the output folder is returned as it is, not fetched or separated again. Pass `overwrite: true` to redo it. A separation is found by its title marker, so the same song separated by demucs and by roformer are two files.

bejeweled names a stem file by its title, and Festival has a few titles twice by different artists. The server reads the artist from the file before calling it downloaded, and refuses to replace one song with the other unless `overwrite` is set.

## Long calls
A separation takes minutes. The server reports progress when the client asks for it, and keeps answering other requests while it works. If a client gives up waiting, the work carries on, and calling again waits for the same file.

One separation runs at a time, since two at once can exhaust memory. Downloads run alongside each other.
