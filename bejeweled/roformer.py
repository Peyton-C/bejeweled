"""Locating and driving audio-separator, which runs the RoFormer models.

Shelled out to like demucs, for the same reason: it brings PyTorch with it, and only
the separate source needs it.
"""
from __future__ import annotations

import os
import re
import shutil

from . import demucs as dm
from . import ffmpeg as ff

# Six stems: drums, bass, other, vocals, guitar and piano. Against Festival's real
# stems on ten Atmos renders it beat htdemucs by +0.80 dB on average and on nine of
# ten tracks, most of all on drums (+1.19).
STEMS_MODEL = "BS-Roformer-SW.ckpt"

# Vocals and instrumental only, the first half of the hybrid
VOCALS_MODEL = "vocals_mel_band_roformer.ckpt"

SAMPLE_RATE = 44100

# Not required but the version the workarounds and measurements were tested against,
# so its the recomended version
AUDIO_SEPARATOR_VERSION = "0.47.0"

# Where the fold enters audio-separator, see separate()
_HEADROOM_PEAK = 0.5


class AudioSeparatorError(RuntimeError):
    """An audio-separator invocation failed, carrying the tail of its output."""


def find_audio_separator(explicit: str | None = None) -> str:
    """Resolve an audio-separator to use, preferring an explicit path over PATH."""
    for candidate in (explicit, os.environ.get("BEJEWELED_AUDIO_SEPARATOR"),
                      shutil.which("audio-separator")):
        if candidate and dm._runs(candidate):
            return candidate
    raise AudioSeparatorError(
        "audio-separator not found. Install it:\n"
        f'  uv tool install --python 3.13 "audio-separator[cpu]=={AUDIO_SEPARATOR_VERSION}" '
        "--with audioread\n"
        "or set BEJEWELED_AUDIO_SEPARATOR to its path, or pass --separator demucs."
    )


def model_dir() -> str:
    """Where models are kept. audio-separator's own default is under /tmp, which macOS
    clears, and the models run to 700 MB and 900 MB, so they are cached properly."""
    explicit = os.environ.get("BEJEWELED_MODEL_DIR")
    if explicit:
        return os.path.expanduser(explicit)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif os.uname().sysname == "Darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "bejeweled", "audio-separator")


def separate(exe: str, inputs: list[str], out_dir: str, model: str = STEMS_MODEL,
             ffmpeg: str | None = None, progress=None) -> dict[str, dict[str, str]]:
    """Separate each input, returning {input path: {stem name: path}}, 44.1 kHz float.

    All inputs go to one invocation so the model is loaded once.

    audio-separator scales any input or output peaking over its normalisation
    threshold down to it, and refuses a threshold above 1, while a group fold can peak
    well over 0 dBFS. Each input therefore goes in at a -6 dBFS peak and each output
    comes back up by the same gain, so the threshold is never reached and the stems of
    every group still sum to its fold. Its default writer passes through int16 whatever
    the input, so soundfile writes instead, which keeps float.

    Autocast and compile are both on. On an RX 9070 XT they took a 202 s Atmos render
    from 270 s to 50 s, and on an M-series MacBook from 334 s to 201 s. Autocast's
    stems sat -62 to -66 dB from full precision and scored the same against real
    stems, and both were bit-identical between runs. Compile falls back to ordinary
    inference by itself where it cannot run.
    """
    ffmpeg = ff.find_ffmpeg(ffmpeg)
    models = model_dir()
    os.makedirs(models, exist_ok=True)
    if not os.path.exists(os.path.join(models, model)):
        download = (lambda done, total: progress(done, total, "downloading model")) if progress else None
        dm.run([exe, "--download_model_only", "-m", model, "--model_file_dir", models],
               download, env=dm.environment(), name="audio-separator", error=AudioSeparatorError)

    scaled_dir = os.path.join(out_dir, "in")
    os.makedirs(scaled_dir, exist_ok=True)
    gains, scaled = {}, []
    for path in inputs:
        peak = _peak(ffmpeg, path)
        gain = _HEADROOM_PEAK / peak if peak else 1.0
        dst = os.path.join(scaled_dir, os.path.basename(path))
        ff.run([ffmpeg, "-v", "error", "-y", "-i", path, "-af", f"volume={gain!r}",
                "-c:a", "pcm_f32le", dst])
        gains[path] = gain
        scaled.append(dst)

    raw_dir = os.path.join(out_dir, "raw")
    cmd = [exe, *scaled, "-m", model, "--model_file_dir", models,
           "--output_dir", raw_dir, "--output_format", "WAV", "--use_soundfile",
           "--normalization", "1.0", "--sample_rate", str(SAMPLE_RATE),
           "--use_autocast", "--use_torch_compile", "--log_level", "warning"]
    dm.run(cmd, progress, len(inputs), env=dm.environment(),
           name="audio-separator", error=AudioSeparatorError)

    found = {}
    for path in inputs:
        base = os.path.splitext(os.path.basename(path))[0]
        # Named <input>_(<Stem>)_<model>.wav, the stem's case varying by writer
        pattern = re.compile(re.escape(base) + r"_\((\w+)\)_.*\.wav$")
        stems = {}
        for name in sorted(os.listdir(raw_dir)) if os.path.isdir(raw_dir) else []:
            match = pattern.match(name)
            if not match:
                continue
            stem = match.group(1).lower()
            out = os.path.join(out_dir, f"{base}.{stem}.wav")
            ff.run([ffmpeg, "-v", "error", "-y", "-i", os.path.join(raw_dir, name),
                    "-af", f"volume={1 / gains[path]!r}", "-c:a", "pcm_f32le", out])
            stems[stem] = out
        if not stems:
            raise AudioSeparatorError(f"audio-separator wrote no stems for {path}")
        found[path] = stems
    return found


def _peak(ffmpeg: str, path: str) -> float:
    """The file's sample peak, linear, or 0 for silence."""
    out = ff.run([ffmpeg, "-v", "info", "-nostats", "-i", path,
                  "-af", "astats=measure_perchannel=none", "-f", "null", "-"])
    levels = re.findall(r"Peak level dB: (-?[\d.]+|-inf)", out.stdout.decode("utf-8", "replace"))
    if not levels or levels[-1] == "-inf":
        return 0.0
    return 10 ** (float(levels[-1]) / 20)
