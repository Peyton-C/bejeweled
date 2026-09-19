"""Festival metadata handling. Pure functions only - no network."""
import json

import pytest

from bejeweled.sources import festival


@pytest.mark.parametrize("root,mode,expected", [
    ("Ab", "Major", "Ab"),
    ("Ab", "Minor", "Abm"),
    ("C", "major", "C"),
    ("F#", "minor", "F#m"),
    # Mode missing is treated as major, which is the safer default to display
    ("D", None, "D"),
    (None, "Major", None),
])
def test_key_formatting_matches_dj_software_convention(root, mode, expected):
    assert festival._format_key(root, mode) == expected


def test_part_order_read_from_manifest():
    qi = {"tracks": [{"part": p} for p in ("ds", "bs", "gs", "vs", "fs")]}
    assert festival._part_order(qi) == ["Drums", "Bass", "Lead", "Vocals", "Other"]


def test_part_order_honours_a_reordered_manifest():
    """The order is read per track rather than assumed, so a change is picked up."""
    qi = {"tracks": [{"part": p} for p in ("vs", "ds", "bs", "fs", "gs")]}
    assert festival._part_order(qi) == ["Vocals", "Drums", "Bass", "Other", "Lead"]


def test_part_order_falls_back_when_unrecognised():
    assert festival._part_order({"tracks": [{"part": "xx"}]}) is None
    assert festival._part_order({}) is None


def test_envelope_parsing_roundtrip():
    import base64
    nonce = b"testnonce123"
    key = bytes(range(16))
    raw = bytes([1, 0, len(nonce), 0, 0]) + nonce + key
    parsed_nonce, parsed_key = festival._parse_envelope(
        base64.b64encode(raw).decode()
    )
    assert parsed_nonce == nonce.decode()
    assert parsed_key == key


def test_envelope_rejects_unknown_version():
    import base64
    raw = bytes([9, 0, 4, 0, 0]) + b"abcd" + bytes(16)
    with pytest.raises(festival.FestivalError, match="envelope"):
        festival._parse_envelope(base64.b64encode(raw).decode())


def test_sanitize_mpd_strips_empty_prefix_declarations():
    """Epic emits xmlns:xsi="", which XML forbids and strict parsers reject."""
    import xml.etree.ElementTree as ET

    xml = (
        '<?xml version="1.0"?>'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:xsi="" '
        'xsi:schemaLocation="" type="static"></MPD>'
    )
    with pytest.raises(ET.ParseError):
        ET.fromstring(xml)

    cleaned = festival._sanitize_mpd(xml)
    root = ET.fromstring(cleaned)
    assert root.get("type") == "static"


ON_DEMAND_MPD = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:xsi="" xsi:schemaLocation="">
  <BaseURL>https://example.com/abc/</BaseURL>
  <Period>
    <AdaptationSet contentType="" lang="und">
      <Representation id="4" bandwidth="1165552" mimeType="audio/mp4" codecs="Opus">
        <AudioChannelConfiguration value="10"></AudioChannelConfiguration>
        <BaseURL>main_0_dashinit.mp4</BaseURL>
        <SegmentBase indexRange="1096-2087"></SegmentBase>
      </Representation>
      <Representation id="1" bandwidth="294808" mimeType="audio/mp4" codecs="Opus">
        <AudioChannelConfiguration value="10"></AudioChannelConfiguration>
        <BaseURL>main_3_dashinit.mp4</BaseURL>
        <SegmentBase indexRange="1096-2087"></SegmentBase>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>"""


def test_parse_mpd_picks_highest_bandwidth_ten_channel_rendition():
    plan = festival._parse_mpd(ON_DEMAND_MPD, "https://example.com/abc/main.mpd")
    assert plan["mode"] == "single"
    assert plan["channels"] == 10
    assert plan["bandwidth"] == 1165552
    assert plan["url"] == "https://example.com/abc/main_0_dashinit.mp4"


def test_parse_mpd_resolves_relative_to_declared_base_url():
    """The manifest's own <BaseURL> wins over the URL it was fetched from."""
    xml = ON_DEMAND_MPD.replace(
        "<BaseURL>https://example.com/abc/</BaseURL>",
        "<BaseURL>https://cdn.example.net/xyz/</BaseURL>",
    )
    plan = festival._parse_mpd(xml, "https://example.com/abc/main.mpd")
    assert plan["url"] == "https://cdn.example.net/xyz/main_0_dashinit.mp4"


def test_parse_mpd_rejects_a_manifest_with_no_audio():
    xml = ON_DEMAND_MPD.replace('mimeType="audio/mp4"', 'mimeType="video/mp4"')
    with pytest.raises(festival.FestivalError, match="no audio"):
        festival._parse_mpd(xml, "https://example.com/abc/main.mpd")
