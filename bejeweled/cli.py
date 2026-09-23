"""Command line entry point."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__, config, palette as pal
from .ffmpeg import FFmpegError
from .stems import NI_SLOTS
from .writers import ni_stem


# What a rip is written as. Sources are scoped to their own subcommand because they
# take different arguments; outputs are a flag because every writer takes a StemSet.
FORMATS = ("ni-stem", "files")


def _add_output_options(parser, with_format=True):
    """Options shared by anything that produces stems."""
    parser.add_argument("-o", "--out", default=".", help="output directory")
    parser.add_argument("--codec", help="aac, alac, flac, opus or wav")
    parser.add_argument("--palette",
                        help="palette name, or 4 comma-separated hex colours")
    if with_format:
        parser.add_argument("--format", choices=FORMATS, default=None,
                            help="ni-stem (a .stem.mp4) or files (separate stems)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bejeweled",
        description="A stem multi-tool for creating Native Instruments stem files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- sources: each gets its own group, since they take different arguments ---
    p_fest = sub.add_parser("festival", help="Fortnite Festival")
    fest = p_fest.add_subparsers(dest="action", required=True)

    f_list = fest.add_parser("list", help="list Festival tracks")
    f_list.add_argument("query", nargs="?", help="filter by title or artist")

    f_rip = fest.add_parser("rip", help="rip a track")
    f_rip.add_argument("query", help="track title or artist to match")
    _add_output_options(f_rip)
    f_rip.add_argument("--keys", help="path to Epic's keys.bin")
    f_rip.add_argument("--suffix", help="appended to the title, e.g. '(FN)'")
    f_rip.add_argument("--no-suffix", action="store_true", help="never append a suffix")
    f_rip.add_argument("--no-cover", action="store_true", help="skip cover art")
    f_rip.add_argument("--keep-countin", action="store_true",
                       help="keep Festival's metronome count-in")

    # --- verbs that act on stem files, whatever produced them ---
    p_conv = sub.add_parser("convert", help="build a stem file from a folder of stems")
    p_conv.add_argument("folder")
    p_conv.add_argument("-o", "--out", help="output .stem.mp4 path")
    p_conv.add_argument("--codec", help="aac, alac, flac, opus or wav")
    p_conv.add_argument("--palette", help="palette name, or 4 comma-separated hex colours")
    p_conv.add_argument("--suffix", help="appended to the title, e.g. '(ENGINE)'")
    p_conv.add_argument("--no-suffix", action="store_true", help="never append a suffix")

    p_color = sub.add_parser("recolor", help="restyle existing stem files in place")
    p_color.add_argument("files", nargs="+")
    p_color.add_argument("--palette", required=True,
                         help="palette name, or 4 comma-separated hex colours")

    p_pal = sub.add_parser("palette", help="inspect palettes and their contrast")
    p_pal.add_argument("name", nargs="?", help="palette to measure (default: all)")
    p_pal.add_argument("--background", help="background colour to measure against")

    p_info = sub.add_parser("info", help="show a stem file's metadata")
    p_info.add_argument("files", nargs="+")

    p_cfg = sub.add_parser("config", help="show or create the config file")
    p_cfg.add_argument("--init", action="store_true", help="write a starting config")
    p_cfg.add_argument("--force", action="store_true", help="overwrite an existing one")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "festival":
        handler = {"rip": _rip, "list": _list}[args.action]
    else:
        handler = {
            "convert": _convert, "recolor": _recolor,
            "palette": _palette, "info": _info, "config": _config,
        }[args.command]

    try:
        return handler(args)
    except (FFmpegError, ValueError, RuntimeError, FileNotFoundError) as e:
        # Lead with a newline so the message is not swallowed by a \r progress line
        print(f"\nerror: {e}", file=sys.stderr)
        return 1


def _colors(args, cfg):
    """Command line palette wins over the configured one."""
    spec = getattr(args, "palette", None)
    if spec:
        return [c.strip() for c in spec.split(",")] if "," in spec else spec
    return config.palette_spec(cfg)


def _source_suffix(source, cfg):
    """The title marker a source asks for, as the config may have overridden it."""
    return config.title_suffix(
        source.SOURCE_NAME, source.TITLE_MARKER, source.MARK_TITLES_BY_DEFAULT, cfg
    )


def _rip(args) -> int:
    from .sources import festival

    cfg = config.load()
    tracks = festival.catalog()
    track = festival.find(args.query, tracks)
    print(f"{track['artist']} - {track['title']}")

    out_dir = os.path.abspath(args.out)
    work = os.path.join(out_dir, f"{_safe(track['title'])} - stems")

    last = [-1]

    def progress(done, total):
        if not total:
            return
        percent = done * 100 // total
        if percent != last[0]:
            last[0] = percent
            print(f"\r  downloading {percent}%", end="", flush=True)

    meta_cfg = cfg.get("metadata", {})
    suffix = None if args.no_suffix else (args.suffix or _source_suffix(festival, cfg))

    keys = args.keys or cfg.get("festival", {}).get("keys")
    stem_set = festival.rip(
        track, work, keys_path=keys, progress=progress,
        title_suffix=suffix,
        cover=not args.no_cover and cfg.get("festival", {}).get("cover_art", True),
        trim_countin=not args.keep_countin
        and cfg.get("festival", {}).get("trim_countin", True),
    )
    print()

    detected = stem_set.extra.get("countin")
    if detected:
        notes = []
        if detected.kept_pickup:
            notes.append("backed off to keep a pickup")
        if detected.muted:
            notes.append(f"silenced {detected.muted:.3f}s of clicks")
        note = f" ({', '.join(notes)})" if notes else ""
        print(f"  trimmed {detected.beats}-beat count-in "
              f"at {detected.trim_at:.3f}s{note}")
    elif not args.keep_countin:
        print("  no count-in detected, nothing trimmed")

    if meta_cfg.get("write_comment", True):
        stem_set.comment = stem_set.describe_source(__version__)

    fmt = args.format or cfg.get("output", {}).get("format", "ni-stem")
    if fmt == "files":
        print(f"wrote {len(stem_set.stems)} stems to {work}")
        return 0

    out_path = os.path.join(out_dir, f"{_safe(stem_set.title)}.stem.mp4")
    ni_stem.write(
        stem_set, out_path,
        codec=args.codec or cfg.get("output", {}).get("codec", "aac"),
        sample_rate=cfg.get("output", {}).get("sample_rate", 44100),
        merge=festival.NI_MERGE,
        colors=_colors(args, cfg),
    )
    for stem in stem_set.stems:
        os.remove(stem.path)
    if stem_set.cover and os.path.exists(stem_set.cover):
        os.remove(stem_set.cover)
    os.rmdir(work)
    print(f"wrote {out_path}")
    return 0


def _list(args) -> int:
    from .sources import festival

    for track in festival.catalog():
        line = f"{track['artist']} - {track['title']}"
        if not args.query or args.query.lower() in line.lower():
            print(line)
    return 0


def _convert(args) -> int:
    from .sources import local

    cfg = config.load()
    stem_set = local.from_folder(args.folder)

    suffix = None if args.no_suffix else (args.suffix or _source_suffix(local, cfg))
    if suffix:
        stem_set.title = f"{stem_set.title} {suffix}"
    if cfg.get("metadata", {}).get("write_comment", True):
        stem_set.comment = stem_set.describe_source(__version__)

    out_path = args.out or os.path.join(
        os.path.dirname(os.path.normpath(args.folder)) or ".",
        f"{_safe(stem_set.title)}.stem.mp4",
    )
    merge = {"Lead": "Other"} if stem_set.get("Lead") else None
    ni_stem.write(
        stem_set, out_path,
        codec=args.codec or cfg.get("output", {}).get("codec", "aac"),
        sample_rate=cfg.get("output", {}).get("sample_rate", 44100),
        merge=merge,
        colors=_colors(args, cfg),
    )
    print(f"wrote {out_path}")
    return 0


def _recolor(args) -> int:
    cfg = config.load()
    colors = _colors(args, cfg)
    for path in args.files:
        ni_stem.recolor(path, colors)
        print(f"recoloured {path}")
    return 0


def _palette(args) -> int:
    cfg = config.load()
    if args.background:
        backgrounds = [b.strip() for b in args.background.split(",")]
    else:
        backgrounds = config.backgrounds_list(cfg)
    names = [args.name] if args.name else list(pal.BUILTIN)

    print(f"backgrounds: {', '.join(backgrounds)}")
    print(f"thresholds: {pal.MIN_CONTRAST:.0f}:1 vs background, "
          f"deltaE {pal.MIN_DELTA_E:.0f} between stems\n")

    for name in names:
        palette = pal.resolve(name)
        measured = pal.report_multi(palette, backgrounds)
        mark = "ok" if measured["passes"] else "LOW"
        print(f"{palette.name}  [{mark}]")
        print(f"  {palette.description}")
        for slot, color in zip(NI_SLOTS, palette.colors):
            ratio = measured["against_background"][slot]
            flag = " " if ratio >= pal.MIN_CONTRAST else "!"
            detail = "  ".join(
                f"{measured['per_background'][bg]['against_background'][slot]:5.2f}"
                for bg in backgrounds
            )
            print(f"  {flag} {slot:<7} {color}  worst {ratio:5.2f}:1   ({detail})")
        worst = min(measured["between_stems"].items(), key=lambda kv: kv[1])
        flag = " " if worst[1] >= pal.MIN_DELTA_E else "!"
        print(f"  {flag} closest pair: {worst[0][0]}/{worst[0][1]} "
              f"at deltaE {worst[1]:.1f}\n")

    if not args.name:
        best = pal.best_for_backgrounds(backgrounds)
        print(f"most legible across these backgrounds: {best.name}")
    return 0


def _info(args) -> int:
    for path in args.files:
        metadata = ni_stem.read_metadata(path)
        print(path)
        if metadata is None:
            print("  no stem atom - not a stem file")
            continue
        print(f"  version {metadata.get('version')}")
        for entry in metadata.get("stems", []):
            print(f"  {entry.get('name'):<7} {entry.get('color')}")
    return 0


def _config(args) -> int:
    if args.init:
        print(f"wrote {config.init(force=args.force)}")
        return 0
    path = config.config_path()
    if not os.path.exists(path):
        print(f"no config at {path} (create one with: bejeweled config --init)")
        return 0
    print(path)
    with open(path) as f:
        print(f.read())
    return 0


def _safe(name: str) -> str:
    import re
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


if __name__ == "__main__":
    raise SystemExit(main())
