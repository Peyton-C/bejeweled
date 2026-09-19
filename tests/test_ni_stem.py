"""Stem file reading and writing, against files built on the fly."""
import json
import os
import struct
import subprocess

import pytest

from bejeweled import ffmpeg as ff
from bejeweled import palette as pal
from bejeweled.stems import NI_SLOTS, Stem, StemSet
from bejeweled.writers import ni_stem


@pytest.fixture(scope="module")
def ffmpeg():
    try:
        return ff.find_ffmpeg()
    except ff.FFmpegError:
        pytest.skip("FFmpeg not available")


@pytest.fixture(scope="module")
def stem_set(ffmpeg, tmp_path_factory):
    """Four short tones at distinct frequencies, one per slot."""
    folder = tmp_path_factory.mktemp("stems")
    stems = []
    for slot, freq in zip(NI_SLOTS, (110, 220, 440, 880)):
        path = folder / f"{slot}.wav"
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
             "-i", f"sine=frequency={freq}:duration=1:sample_rate=44100",
             "-ac", "2", str(path)],
            check=True,
        )
        stems.append(Stem(name=slot, path=str(path)))
    return StemSet(title="Test Track", artist="Tester", stems=stems)


def test_write_produces_five_default_flagged_correctly(stem_set, tmp_path, ffmpeg):
    out = str(tmp_path / "out.stem.mp4")
    ni_stem.write(stem_set, out)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type:stream_disposition=default",
         "-of", "json", out],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    audio = [s for s in streams if s["codec_type"] == "audio"]

    # One mixdown plus four stems, and only the mixdown is default
    assert len(audio) == 5
    assert audio[0]["disposition"]["default"] == 1
    assert all(s["disposition"]["default"] == 0 for s in audio[1:])


def test_roundtrip_metadata(stem_set, tmp_path):
    out = str(tmp_path / "rt.stem.mp4")
    ni_stem.write(stem_set, out)

    metadata = ni_stem.read_metadata(out)
    assert metadata["version"] == 1
    assert [s["name"] for s in metadata["stems"]] == list(NI_SLOTS)
    assert [s["color"] for s in metadata["stems"]] == list(pal.DEFAULT.colors)
    assert metadata["mastering_dsp"]["limiter"]["enabled"] is False


def test_custom_palette_is_written(stem_set, tmp_path):
    out = str(tmp_path / "colored.stem.mp4")
    ni_stem.write(stem_set, out, colors="vivid-dark")

    metadata = ni_stem.read_metadata(out)
    assert [s["color"] for s in metadata["stems"]] == list(pal.BUILTIN["vivid-dark"].colors)


def test_recolor_rewrites_only_metadata(stem_set, tmp_path):
    out = str(tmp_path / "recolor.stem.mp4")
    ni_stem.write(stem_set, out)

    before_audio = _audio_bytes(out)
    ni_stem.recolor(out, "max-separation")

    assert _audio_bytes(out) == before_audio
    metadata = ni_stem.read_metadata(out)
    assert [s["color"] for s in metadata["stems"]] == list(pal.BUILTIN["max-separation"].colors)


def test_injection_is_idempotent(stem_set, tmp_path):
    """Rewriting colours repeatedly must not stack duplicate atoms."""
    out = str(tmp_path / "idem.stem.mp4")
    ni_stem.write(stem_set, out)

    sizes = []
    for name in ("vivid-dark", "okabe-ito", "vivid-dark"):
        ni_stem.recolor(out, name)
        sizes.append(os.path.getsize(out))

    assert sizes[0] == sizes[2], "file grows on every recolour - atom is not replaced"
    assert _count_atoms(out, b"stem") == 1


def test_read_metadata_on_plain_mp4_returns_none(stem_set, tmp_path, ffmpeg):
    plain = str(tmp_path / "plain.m4a")
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", stem_set.stems[0].path, "-c:a", "aac", plain],
        check=True,
    )
    assert ni_stem.read_metadata(plain) is None


def test_write_rejects_wrong_stem_count(tmp_path, stem_set):
    partial = StemSet(title="x", stems=stem_set.stems[:3])
    with pytest.raises(ValueError, match="no stems for NI slot"):
        ni_stem.write(partial, str(tmp_path / "bad.stem.mp4"))


def test_itunes_tags_survive_where_ffmpeg_drops_them(tmp_path, stem_set):
    """FFmpeg silently discards BPM and key for MP4; these are written as atoms."""
    out = str(tmp_path / "tagged.stem.mp4")
    tagged = StemSet(
        title="Kill Bill (FN)", artist="SZA", year="2022", bpm=89, key="Ab",
        comment="bejeweled test | source: Fortnite Festival",
        stems=stem_set.stems,
    )
    ni_stem.write(tagged, out)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", out],
        capture_output=True, text=True, check=True,
    )
    tags = json.loads(probe.stdout)["format"]["tags"]

    assert tags["title"] == "Kill Bill (FN)"
    assert tags["artist"] == "SZA"
    assert tags["comment"].startswith("bejeweled test")
    assert tags["initialkey"] == "Ab"
    assert tags["BPM"] == "89"


def test_stem_atom_still_readable_after_itunes_tags(tmp_path, stem_set):
    """Both injections touch moov, so they must not corrupt each other."""
    out = str(tmp_path / "both.stem.mp4")
    tagged = StemSet(title="T", bpm=128, key="Am", stems=stem_set.stems)
    ni_stem.write(tagged, out, colors="vivid-dark")

    metadata = ni_stem.read_metadata(out)
    assert [s["name"] for s in metadata["stems"]] == list(NI_SLOTS)
    assert metadata["stems"][0]["color"] == pal.BUILTIN["vivid-dark"].colors[0]

    # and the container is still structurally valid
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=index", "-of", "json", out],
        capture_output=True, text=True, check=True,
    )
    assert len(json.loads(probe.stdout)["streams"]) == 5


def test_source_description_records_tool_and_origin():
    s = StemSet(title="T", stems=[], source="Fortnite Festival")
    assert s.describe_source("0.1.0") == "bejeweled 0.1.0 | source: Fortnite Festival"

    bare = StemSet(title="T", stems=[])
    assert bare.describe_source("0.1.0") == "bejeweled 0.1.0"


def _audio_bytes(path):
    """The mdat payload, which recolouring must never touch."""
    with open(path, "rb") as f:
        data = f.read()
    pos = 0
    while pos + 8 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, pos)
        if size == 1:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
        elif size == 0:
            size = len(data) - pos
        if kind == b"mdat":
            return data[pos:pos + size]
        pos += size
    raise AssertionError("no mdat")


def _count_atoms(path, wanted):
    with open(path, "rb") as f:
        data = f.read()
    return data.count(wanted + b'{"stems"') or data.count(wanted)
