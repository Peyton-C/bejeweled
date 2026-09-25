"""Locating and driving FFmpeg."""
from __future__ import annotations

import os
import shutil
import subprocess


class FFmpegError(RuntimeError):
    """An FFmpeg invocation failed, carrying the tail of its output."""


def find_ffmpeg(explicit: str | None = None) -> str:
    """Resolve an FFmpeg to use, preferring whatever is already on PATH.

    A binary on PATH is the right architecture by definition, which a bundled one is
    not - shipping an x86_64 build to an Apple Silicon machine is how this breaks.
    """
    for candidate in (explicit, os.environ.get("BEJEWELED_FFMPEG"), shutil.which("ffmpeg")):
        if candidate and _runs(candidate):
            return candidate
    raise FFmpegError(
        "FFmpeg not found. Install it (brew install ffmpeg / apt install ffmpeg) "
        "or set BEJEWELED_FFMPEG to its path."
    )


def _runs(path: str) -> bool:
    try:
        result = subprocess.run(
            [path, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20
        )
        return result.returncode == 0
    except Exception:
        return False


def find_ffprobe(ffmpeg: str) -> str:
    """The ffprobe that shipped alongside this FFmpeg, falling back to PATH."""
    sibling = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
    for candidate in (sibling, shutil.which("ffprobe")):
        if candidate and _runs(candidate):
            return candidate
    raise FFmpegError("ffprobe not found. It ships with FFmpeg, so reinstalling FFmpeg should fix it.")


def probe(ffmpeg: str, path: str) -> dict:
    """ffprobe's view of a file: its streams, and its format-level tags."""
    import json

    result = run([find_ffprobe(ffmpeg), "-v", "error", "-show_streams", "-show_format",
                  "-of", "json", path])
    return json.loads(result.stdout)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run FFmpeg, surfacing its output on failure instead of discarding it."""
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        lines = result.stdout.decode("utf-8", "replace").strip().splitlines()
        raise FFmpegError("\n".join(lines[-12:]) or f"exit {result.returncode}")
    return result


def mix(ffmpeg: str, inputs: list[str], out_path: str, codec_args: list[str] | None = None) -> str:
    """Sum several audio files into one, without the level scaling amix defaults to."""
    if len(inputs) == 1:
        cmd = [ffmpeg, "-y", "-i", inputs[0], *(codec_args or []), out_path]
    else:
        cmd = [ffmpeg, "-y"]
        for path in inputs:
            cmd += ["-i", path]
        # Normalise every input to plain stereo first; sources carrying an exotic
        # channel layout are otherwise rejected by the resampler amix feeds
        prep = ";".join(
            f"[{i}:a]aformat=channel_layouts=stereo[m{i}]" for i in range(len(inputs))
        )
        chain = "".join(f"[m{i}]" for i in range(len(inputs)))
        cmd += [
            "-filter_complex",
            f"{prep};{chain}amix=inputs={len(inputs)}:normalize=0[out]",
            "-map", "[out]", *(codec_args or []), out_path,
        ]
    run(cmd)
    return out_path
