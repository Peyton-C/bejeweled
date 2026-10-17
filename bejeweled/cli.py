"""Command line entry point."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__, config, jobs, palette as pal
from .demucs import DemucsError
from .ffmpeg import FFmpegError
from .sources.separate import DEFAULT_SEPARATOR, LAYOUTS, SEPARATORS
from .stems import NI_SLOTS
from .writers import ni_stem


# What a rip is written as. Sources are scoped to their own subcommand because they
# take different arguments; outputs are a flag because every writer takes a StemSet.
FORMATS = jobs.FORMATS

# `files` is what a folder already is, so only a stem file can be converted to it
CONVERT_FORMATS = FORMATS


def _add_output_options(parser, with_format=True):
    """Options shared by anything that produces stems."""
    parser.add_argument("-o", "--out", default=".", help="output directory")
    parser.add_argument("--codec", help="aac, alac, flac, opus or wav")
    parser.add_argument("--palette",
                        help="palette name, or 4 comma-separated hex colours")
    if with_format:
        parser.add_argument("--format", choices=FORMATS, default=None,
                            help="ni-stem (a .stem.mp4), engine (a .stem.mp4 added to an "
                                 "Engine DJ library with its stems) or files (separate stems)")
        parser.add_argument("--engine-library",
                            help="the Engine Library folder, for --format engine")
        parser.add_argument("--engine-key", help="Engine DJ's stems key, as hex")


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

    p_sep = sub.add_parser("separate", help="split a mixed song into stems")
    p_sep.add_argument("files", nargs="+",
                       help="songs or multichannel renders, or folders of them")
    _add_output_options(p_sep)
    p_sep.add_argument("--layout", choices=list(LAYOUTS),
                       help="channel layout of a multichannel file (default: by channel count)")
    p_sep.add_argument("--separator", choices=list(SEPARATORS),
                       help=f"what separates (default: {DEFAULT_SEPARATOR})")
    p_sep.add_argument("--model", help="demucs model, for demucs and the hybrid "
                                       "(default: htdemucs, htdemucs_ft for the hybrid)")
    p_sep.add_argument("--device",
                       help="demucs's device: cpu, cuda or mps, ROCm counts as cuda (default: "
                            "mps on Apple Silicon, otherwise whatever demucs picks)")
    p_sep.add_argument("--suffix", help="appended to the title instead of a marker such as '(RF)'")
    p_sep.add_argument("--no-suffix", action="store_true", help="never append a suffix")

    p_comb = sub.add_parser("combine", help="build stems from more than one version of a song")
    comb = p_comb.add_subparsers(dest="action", required=True)

    c_graft = comb.add_parser("graft", help="put the explicit vocal into real stems of "
                                            "the clean edit")
    c_graft.add_argument("stems", help="a .stem.mp4 or a folder of stems, of the clean edit")
    c_graft.add_argument("mix", help="the explicit release, as an ordinary mixed song")
    _add_output_options(c_graft)
    c_graft.add_argument("--title", help="the title to give the result (default: the "
                                         "mix's title tag, or the stems' title)")
    c_graft.add_argument("--separator", choices=list(SEPARATORS),
                         help=f"what separates the mix (default: {DEFAULT_SEPARATOR})")
    c_graft.add_argument("--model", help="demucs model, for demucs and the hybrid")
    c_graft.add_argument("--device", help="demucs's device: cpu, cuda or mps")
    c_graft.add_argument("--suffix", help="appended to the title instead of a marker "
                                          "such as '(+RF)'")
    c_graft.add_argument("--no-suffix", action="store_true", help="never append a suffix")

    c_ext = comb.add_parser("extend", help="lay real stems along another version of the "
                                           "song, such as an extended mix")
    c_ext.add_argument("stems", help="a .stem.mp4 or a folder of stems")
    c_ext.add_argument("mix", help="the other version, as an ordinary mixed song")
    _add_output_options(c_ext)
    c_ext.add_argument("--title", help="the title to give the result (default: the "
                                       "mix's title tag, or its file name)")
    c_ext.add_argument("--separator", choices=list(SEPARATORS),
                       help=f"what separates what only the mix has (default: {DEFAULT_SEPARATOR})")
    c_ext.add_argument("--model", help="demucs model, for demucs and the hybrid")
    c_ext.add_argument("--device", help="demucs's device: cpu, cuda or mps")
    c_ext.add_argument("--suffix", help="appended to the title instead of a marker "
                                        "such as '(+RF)'")
    c_ext.add_argument("--no-suffix", action="store_true", help="never append a suffix")

    c_asm = comb.add_parser("assemble", help="build stems from a song's mix, instrumental "
                                             "and acapella, any two or all three")
    c_asm.add_argument("--mix", help="the full song")
    c_asm.add_argument("--instrumental", help="the song without its vocal")
    c_asm.add_argument("--acapella", help="the vocal alone")
    _add_output_options(c_asm)
    c_asm.add_argument("--title", help="the title to give the result (default: the "
                                       "title tag of the mix, or of the instrumental)")
    c_asm.add_argument("--separator", choices=list(SEPARATORS),
                       help=f"what separates (default: {DEFAULT_SEPARATOR})")
    c_asm.add_argument("--model", help="demucs model, for demucs and the hybrid")
    c_asm.add_argument("--device", help="demucs's device: cpu, cuda or mps")
    c_asm.add_argument("--suffix", help="appended to the title instead of a marker "
                                        "such as '(RF IN)'")
    c_asm.add_argument("--no-suffix", action="store_true", help="never append a suffix")

    # --- verbs that act on stem files, whatever produced them ---
    p_conv = sub.add_parser("convert", help="build a stem file from a folder of stems, or "
                                            "Engine DJ stems or separate stems from a "
                                            "stem file")
    p_conv.add_argument("inputs", nargs="+", metavar="input",
                        help="folders of stems, or .stem.mp4 files")
    p_conv.add_argument("-o", "--out", help="output .stem.mp4 path for one folder, or "
                                            "the stems folder for one .stem.mp4")
    p_conv.add_argument("--format", choices=CONVERT_FORMATS, default=None,
                        help="ni-stem, engine to add it to an Engine DJ library with its "
                             "stems, or files to unpack a .stem.mp4 into separate stems "
                             "(default: engine for a .stem.mp4)")
    p_conv.add_argument("--engine-library",
                        help="the Engine Library folder, for --format engine")
    p_conv.add_argument("--engine-key", help="Engine DJ's stems key, as hex")
    p_conv.add_argument("--codec", help="aac, alac, flac, opus or wav")
    p_conv.add_argument("-j", "--jobs", type=int,
                        help="how many inputs to convert at once (default: one per core)")
    p_conv.add_argument("--palette", help="palette name, or 4 comma-separated hex colours")
    p_conv.add_argument("--slots", help="what a folder's four stems are, in order, when "
                                        "they are not drums, bass, other and vocals, "
                                        "e.g. Drums,Music,Sax,Vocals")
    p_conv.add_argument("--assign", action="append", metavar="NAME=SLOT",
                        help="put a folder's file in a slot, or skip it, overriding "
                             "what its name says; NAME may be a glob, e.g. 'BGV*=Vocals'. "
                             "Repeat for more")
    p_conv.add_argument("--title", help="the title, for one folder (default: the "
                                        "folder's name)")
    p_conv.add_argument("--artist", help="the artist, for every folder given")
    cover = p_conv.add_mutually_exclusive_group()
    cover.add_argument("--cover", metavar="IMAGE",
                       help="cover art for a folder, instead of looking it up")
    cover.add_argument("--no-cover", action="store_true",
                       help="do not look up cover art for a folder")
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

    p_mcp = sub.add_parser("mcp", help="run as an MCP server for an agent")
    p_mcp.add_argument("-o", "--out", help="where stem files go (default: ~/Music/bejeweled)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "festival":
        handler = {"rip": _rip, "list": _list}[args.action]
    elif args.command == "combine":
        handler = {"graft": _graft, "extend": _extend, "assemble": _assemble}[args.action]
    else:
        handler = {
            "separate": _separate, "convert": _convert, "recolor": _recolor,
            "palette": _palette, "info": _info, "config": _config, "mcp": _mcp,
        }[args.command]

    try:
        return handler(args)
    except (FFmpegError, DemucsError, ValueError, RuntimeError, FileNotFoundError) as e:
        # Lead with a newline so the message is not swallowed by a \r progress line
        print(f"\nerror: {e}", file=sys.stderr)
        return 1


def _colors(args, cfg):
    """Command line palette wins over the configured one."""
    return jobs.colors(getattr(args, "palette", None), cfg)


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

    last = [-1]

    def progress(done, total):
        if not total:
            return
        percent = done * 100 // total
        if percent != last[0]:
            last[0] = percent
            print(f"\r  downloading {percent}%", end="", flush=True)

    written = jobs.rip_festival(
        track, args.out, cfg, keys=args.keys, suffix=args.suffix, no_suffix=args.no_suffix,
        cover=not args.no_cover, trim_countin=not args.keep_countin, fmt=args.format,
        codec=args.codec, palette=args.palette, engine_library=args.engine_library,
        engine_key=args.engine_key, progress=progress,
    )
    print()

    detected = written.stem_set.extra.get("countin")
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

    _report(written)
    return 0


def _report(written, wrote_track=True) -> None:
    if os.path.isdir(written.path):
        print(f"wrote {len(written.stem_set.stems)} stems to {written.path}")
    elif wrote_track:
        print(f"wrote {written.path}")
    _report_multitrack(written.stem_set)
    if written.engine_stems:
        print(f"wrote {written.engine_stems}")


def _report_multitrack(stem_set) -> None:
    """Which file went where, so a name read wrongly can be put right with --assign."""
    slots = stem_set.extra.get("slots")
    if not slots:
        return
    labels = stem_set.extra.get("slot_names") or {}
    unsorted = set(stem_set.extra.get("unsorted", ()))
    for slot in NI_SLOTS:
        members = [f"{name}?" if name in unsorted else name
                   for name, into in slots.items() if into == slot]
        print(f"  {labels.get(slot, slot):<8} {', '.join(members) or '-'}")
    for name, why in stem_set.extra.get("skipped", {}).items():
        print(f"  left out {name}, {why}")
    if unsorted:
        print("  ? names nothing bejeweled recognises, so went in Other")


def _separate(args) -> int:
    from .sources import local

    files = []
    for target in args.files:
        if os.path.isdir(target):
            # One level only, so pointing at a library root does not quietly queue
            # every album in it
            files += sorted(
                os.path.join(target, name) for name in os.listdir(target)
                if not name.startswith(".")
                and os.path.splitext(name)[1].lower() in local.AUDIO_EXTENSIONS
            )
        else:
            files.append(target)
    if not files:
        raise ValueError(f"no audio files in {', '.join(args.files)}")

    cfg = config.load()
    if len(files) == 1:
        return _separate_one(files[0], args, cfg)

    # A batch carries on past a bad file rather than losing the rest of the run
    failed = []
    for number, path in enumerate(files, 1):
        print(f"[{number}/{len(files)}] {os.path.basename(path)}")
        try:
            _separate_one(path, args, cfg)
        except (FFmpegError, DemucsError, ValueError, RuntimeError, FileNotFoundError) as e:
            print(f"\n  error: {e}", file=sys.stderr)
            failed.append(path)
    if failed:
        print(f"\n{len(failed)} of {len(files)} failed:", file=sys.stderr)
        for path in failed:
            print(f"  {path}", file=sys.stderr)
        return 1
    return 0


def _stages():
    """A progress callback that prints each stage of a separation on its own line."""
    last = [None]

    def progress(done, total, stage="separating"):
        if not total:
            return
        percent = done * 100 // total
        if (stage, percent) != last[0]:
            if last[0] and last[0][0] != stage:
                print()
            last[0] = (stage, percent)
            print(f"\r  {stage} {percent}%", end="", flush=True)

    return progress


def _separate_one(path, args, cfg) -> int:
    progress = _stages()
    written = jobs.separate_file(
        path, args.out, cfg, separator=args.separator, layout=args.layout,
        model=args.model, device=args.device, suffix=args.suffix, no_suffix=args.no_suffix,
        fmt=args.format, codec=args.codec, palette=args.palette,
        engine_library=args.engine_library, engine_key=args.engine_key, progress=progress,
    )
    print()

    layout = written.stem_set.extra["layout"]
    groups = written.stem_set.extra["groups"]
    if len(groups) > 1:
        print(f"  read as {layout}, separated in {len(groups)} groups: {', '.join(groups)}")

    _report(written)
    return 0


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def _graft(args) -> int:
    cfg = config.load()
    progress = _stages()
    written = jobs.graft_file(
        args.stems, args.mix, args.out, cfg, title=args.title, separator=args.separator,
        model=args.model, device=args.device, suffix=args.suffix, no_suffix=args.no_suffix,
        fmt=args.format, codec=args.codec, palette=args.palette,
        engine_library=args.engine_library, engine_key=args.engine_key, progress=progress,
    )
    print()

    report = written.stem_set.extra["graft"]
    print(f"  grafted {len(report.windows)} places, {report.seconds:.1f}s of "
          f"{report.stem.lower()}: {' '.join(_clock(w.start) for w in report.windows)}")
    if report.only_clean:
        # Printed because nothing was done there, and it may be an edit worth checking
        print(f"  left alone, only in the stems: "
              f"{' '.join(_clock(w.start) for w in report.only_clean)}")

    _report(written)
    return 0


def _extend(args) -> int:
    cfg = config.load()
    written = jobs.extend_file(
        args.stems, args.mix, args.out, cfg, title=args.title, separator=args.separator,
        model=args.model, device=args.device, suffix=args.suffix, no_suffix=args.no_suffix,
        fmt=args.format, codec=args.codec, palette=args.palette,
        engine_library=args.engine_library, engine_key=args.engine_key, progress=_stages(),
    )
    print()

    report = written.stem_set.extra["extension"]
    print("  " + ", ".join(f"{p.kind} {_clock(p.start)}-{_clock(p.end)}"
                           for p in report.pieces))
    if report.dropped:
        # Printed because the stems had it and the file does not
        print(f"  only in the stems, left out: "
              f"{' '.join(f'{_clock(w.start)}-{_clock(w.end)}' for w in report.dropped)}")
    if report.separated:
        print(f"  seams on a {report.bpm:.1f} BPM grid" if report.bpm
              else "  no steady beat found, seams where the versions part")

    _report(written)
    return 0


def _assemble(args) -> int:
    cfg = config.load()
    written = jobs.assemble_file(
        args.out, cfg, mix=args.mix, instrumental=args.instrumental, acapella=args.acapella,
        title=args.title, separator=args.separator, model=args.model, device=args.device,
        suffix=args.suffix, no_suffix=args.no_suffix, fmt=args.format, codec=args.codec,
        palette=args.palette, engine_library=args.engine_library,
        engine_key=args.engine_key, progress=_stages(),
    )
    print()

    report = written.stem_set.extra["assembly"]
    if "mix" not in report.parts:
        # Printed because it is an assumption, and a wrong one is heard at once
        print("  no mix to line the parts up against, taken as starting together")
    for part, offset in report.offsets.items():
        if abs(offset) >= 0.0005:
            print(f"  {part} starts {abs(offset):.3f}s {'later' if offset > 0 else 'earlier'} "
                  f"than the mix, lined up")
    if report.vocals == "acapella":
        print("  vocals: the acapella")
    elif report.vocals == "null":
        print(f"  vocals: the mix less the instrumental, which nulled by {report.null_db:.0f} dB")
    else:
        print(f"  vocals: separated from the mix less the instrumental, which nulled "
              f"by only {report.null_db:.0f} dB")
    others = {"instrumental": "the instrumental", "remainder": "the mix less the acapella",
              "mix": "the mix, which the acapella would not come out of"}
    print(f"  the rest: separated from {others[report.separated[0]]}")

    _report(written)
    return 0


def _list(args) -> int:
    from .sources import festival

    for track in festival.catalog():
        line = f"{track['artist']} - {track['title']}"
        if not args.query or args.query.lower() in line.lower():
            print(line)
    return 0


def _convert(args) -> int:
    if args.out and len(args.inputs) > 1:
        raise ValueError("-o names one output, so it takes one input")
    if args.title and len(args.inputs) > 1:
        raise ValueError("--title names one song, so it takes one input")
    if args.jobs is not None and args.jobs < 1:
        raise ValueError("--jobs is at least 1")
    if args.cover and not os.path.isfile(args.cover):
        raise FileNotFoundError(f"no cover image at {args.cover}")

    cfg = config.load()
    if len(args.inputs) == 1:
        _report(*_convert_one(args.inputs[0], args, cfg))
        return 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    # The work is FFmpeg's and its AAC encoder keeps to one core, so a core each.
    # Eight side by side finished in 14 s where one took 11.
    workers = min(args.jobs or os.cpu_count() or 1, len(args.inputs))
    # As with separate, a batch carries on past a bad one
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        running = {pool.submit(_convert_one, target, args, cfg): target
                   for target in args.inputs}
        try:
            for number, done in enumerate(as_completed(running), 1):
                target = running[done]
                print(f"[{number}/{len(args.inputs)}] {os.path.basename(os.path.normpath(target))}")
                try:
                    _report(*done.result())
                except (FFmpegError, ValueError, RuntimeError, FileNotFoundError) as e:
                    print(f"  error: {e}", file=sys.stderr)
                    failed.append(target)
        except BaseException:
            pool.shutdown(cancel_futures=True)
            raise
    if failed:
        print(f"\n{len(failed)} of {len(args.inputs)} failed:", file=sys.stderr)
        for target in sorted(failed, key=args.inputs.index):
            print(f"  {target}", file=sys.stderr)
        return 1
    return 0


def _slots(spec: str | None):
    """The four slot names --slots gives, or None."""
    if not spec:
        return None
    names = [name.strip() for name in spec.split(",")]
    if len(names) != len(NI_SLOTS) or not all(names):
        raise ValueError(f"--slots takes {len(NI_SLOTS)} names, such as Drums,Music,Sax,Vocals")
    if len({name.lower() for name in names}) != len(names):
        raise ValueError("--slots names each slot once")
    return names


def _assignments(specs):
    """The (name, slot) pairs --assign gives."""
    pairs = []
    for spec in specs or ():
        name, sep, slot = spec.rpartition("=")
        if not sep or not name.strip() or not slot.strip():
            raise ValueError(f"--assign takes NAME=SLOT, such as 'Loops=Drums', not {spec!r}")
        pairs.append((name.strip(), slot.strip()))
    return pairs


def _convert_one(target, args, cfg):
    """Convert one input, returning what was written and whether the track itself was."""
    from .sources import local

    names = _slots(args.slots)
    assign = _assignments(args.assign)
    if local.is_stem_file(target):
        for flag, given in (("--slots", names), ("--assign", assign),
                            ("--title", args.title), ("--artist", args.artist),
                            ("--cover", args.cover)):
            if given:
                raise ValueError(f"{flag} is for a folder of stems, and "
                                 f"{os.path.basename(target)} is a stem file")
        if args.format == "ni-stem":
            raise ValueError(f"{os.path.basename(target)} is already a stem file")
        if args.format == "files":
            return jobs.stem_file_to_files(target, args.out), True
        return jobs.stem_file_to_engine(target, cfg, engine_library=args.engine_library,
                                        engine_key=args.engine_key), False

    if args.format == "files":
        raise ValueError(f"{target} is already a folder of stems")
    fmt = args.format or cfg.get("output", {}).get("format")
    engine = jobs.engine_target(fmt if fmt == "engine" else "ni-stem", cfg,
                                args.engine_library, args.engine_key)
    stem_set = local.from_folder(target, title=args.title, artist=args.artist, names=names,
                                 assign=assign)
    if args.cover:
        stem_set.cover = args.cover
    slots = stem_set.extra.get("slots")
    if names and not slots:
        missing = [name for name in names if not stem_set.get(name)]
        unnamed = [name for name in stem_set.names() if name not in names]
        if missing or unnamed:
            raise ValueError(f"{target} has {', '.join(stem_set.names())}, and --slots "
                             f"names {', '.join(names)}")

    suffix = None if args.no_suffix else (args.suffix or _source_suffix(local, cfg))
    if suffix:
        stem_set.title = f"{stem_set.title} {suffix}"
    if cfg.get("metadata", {}).get("write_comment", True):
        stem_set.comment = stem_set.describe_source(__version__)

    out_path = args.out or os.path.join(
        os.path.dirname(os.path.normpath(target)) or ".",
        f"{jobs.safe(stem_set.title)}.stem.mp4",
    )
    if slots:
        merge = slots
    elif names:
        # Each named stem to the slot in its position, whatever that slot is called
        merge = dict(zip(names, NI_SLOTS))
    else:
        merge = {"Lead": "Other"} if stem_set.get("Lead") else None
    jobs.write_ni(stem_set, out_path, cfg, merge=merge, codec=args.codec,
                  palette=args.palette, names=names, find_cover=not args.no_cover)
    stems = engine and jobs.write_engine(stem_set, out_path, engine, merge)
    return jobs.Written(out_path, stem_set, stems), True


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


def _mcp(args) -> int:
    from . import mcp

    return mcp.serve(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
