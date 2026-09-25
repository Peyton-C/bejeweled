"""Locating and driving demucs.

demucs is shelled out to rather than imported, the same way FFmpeg is, because it
brings two to three gigabytes of torch with it and only the separate source needs it.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import signal
import subprocess

DEFAULT_MODEL = "htdemucs"

# Every demucs model resamples its output to this rate, whatever the input was
SAMPLE_RATE = 44100


class DemucsError(RuntimeError):
    """A demucs invocation failed, carrying the tail of its output."""


def find_demucs(explicit: str | None = None) -> str:
    """Resolve a demucs to use, preferring an explicit path over PATH."""
    for candidate in (explicit, os.environ.get("BEJEWELED_DEMUCS"), shutil.which("demucs")):
        if candidate and _runs(candidate):
            return candidate
    raise DemucsError(
        "demucs not found. Install it (uv tool install demucs --with soundfile) "
        "or set BEJEWELED_DEMUCS to its path."
    )


def _runs(path: str) -> bool:
    """Whether this is an executable file. Unlike FFmpeg it is not run to check, since
    that imports torch, which took 11 seconds on ROCm, once per file in a batch. A
    broken install still fails on the real run, with demucs's own error."""
    resolved = shutil.which(path)
    return bool(resolved) and os.path.isfile(resolved) and os.access(resolved, os.X_OK)


def default_device() -> str | None:
    """The device to ask for, or None to leave the choice to demucs.

    demucs picks CUDA when it can and otherwise falls back to the CPU, and never picks
    Apple's GPU by itself. On Apple Silicon MPS separated about 2.3 times faster than
    the CPU and used 18 percent of one core against 900 percent.
    """
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mps"
    return None


def separate(demucs: str, inputs: list[str], out_dir: str, model: str = DEFAULT_MODEL,
             device: str | None = None, progress=None) -> dict[str, dict[str, str]]:
    """Separate each input, returning {input path: {stem name: path}}.

    All inputs go to one invocation so the model is loaded once. Output is float with
    no clipping protection, because the default rescales each file by its own peak,
    and stems cut from different submixes would then no longer sum to the mix.
    `--shifts 0` because the default applies a random time shift, so two runs of the
    same input differed by as much as the gain from grouping channels (-12 dB on Other).
    """
    cmd = [demucs, "-n", model, "--shifts", "0", "--float32", "--clip-mode", "none",
           "-o", out_dir]
    if device:
        cmd += ["-d", device]
    run(cmd + list(inputs), progress, len(inputs), env=environment())

    model_dir = os.path.join(out_dir, model)
    found = {}
    for path in inputs:
        track_dir = os.path.join(model_dir, os.path.splitext(os.path.basename(path))[0])
        stems = {
            os.path.splitext(name)[0]: os.path.join(track_dir, name)
            for name in sorted(os.listdir(track_dir)) if name.endswith(".wav")
        } if os.path.isdir(track_dir) else {}
        if not stems:
            raise DemucsError(f"demucs wrote no stems for {path}")
        found[path] = stems
    return found


def environment() -> dict[str, str]:
    """The environment demucs runs in, tuned for ROCm without overriding the user.

    Importing PyTorch's ROCm build reads every GPU library it ships into fresh memory,
    12.5 GB on torch 2.13 + ROCm 7.1, which took 10.3 s on an RX 9070 XT almost
    entirely in 2.9 million page faults. Backing malloc with huge pages cut that to
    5.8 s and 68 thousand faults. glibc elsewhere ignores the tunable, as does macOS.

    MIOpen's fast kernel selection took a warm separation of a 296 s track from 10.6 s
    to 9.4 s. Its output differed from the default search by at most 1.9e-5 against a
    peak of 5.4, about -109 dB, and was bit-identical between runs, which --shifts 0
    exists to guarantee. Only ROCm reads it.
    """
    env = dict(os.environ)
    env.setdefault("GLIBC_TUNABLES", "glibc.malloc.hugetlb=1")
    env.setdefault("MIOPEN_FIND_MODE", "FAST")
    return env


_PERCENT = re.compile(r"(\d+)%\|")


def run(cmd: list[str], progress=None, tracks: int = 1, env: dict | None = None) -> None:
    """Run demucs, reporting its progress bars and surfacing its output on failure.

    `progress(done, total)` is called with whole-job percentages. demucs draws one bar
    per input and carriage-returns within it, so completed bars are counted to place
    the current one within the whole job.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    tail, buf, finished, last = [], b"", 0, 0
    while True:
        chunk = proc.stdout.read1(4096)
        *lines, buf = re.split(rb"[\r\n]", buf + chunk)
        if not chunk:
            # Whatever trails the last newline is a line too
            lines.append(buf)
        for raw in lines:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            match = _PERCENT.search(line)
            if match:
                percent = int(match.group(1))
                if percent < last:
                    finished += 1
                last = percent
                if progress:
                    progress(min(finished, tracks - 1) * 100 + percent, tracks * 100)
            else:
                tail = (tail + [line])[-12:]
        if not chunk:
            break
    code = proc.wait()
    if code < 0:
        # A process the OS kills prints nothing, so the tail would only be demucs's
        # ordinary chatter and read as an error with no description
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = f"signal {-code}"
        hint = (", which usually means the system ran out of memory. Run one "
                "separation at a time" if -code == signal.SIGKILL else "")
        raise DemucsError(f"demucs was stopped by {name}{hint}")
    if code != 0:
        raise DemucsError("\n".join(tail + [f"demucs exited with status {code}"]))
