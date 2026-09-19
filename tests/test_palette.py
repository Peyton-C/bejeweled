"""Colour maths, checked against published reference values."""
import pytest

from bejeweled import palette as pal


def test_relative_luminance_endpoints():
    assert pal.relative_luminance("#000000") == pytest.approx(0.0)
    assert pal.relative_luminance("#FFFFFF") == pytest.approx(1.0)


def test_contrast_ratio_extremes():
    # WCAG's range runs from 1:1 to 21:1
    assert pal.contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)
    assert pal.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)


def test_lab_conversion_reference_points():
    # D65 white and mid grey, from the sRGB/Lab reference
    l, a, b = pal.to_lab("#FFFFFF")
    assert (l, a, b) == pytest.approx((100.0, 0.0, 0.0), abs=0.01)
    l, a, b = pal.to_lab("#000000")
    assert (l, a, b) == pytest.approx((0.0, 0.0, 0.0), abs=0.01)
    l, _, _ = pal.to_lab("#808080")
    assert l == pytest.approx(53.59, abs=0.1)


def test_delta_e_endpoints():
    assert pal.delta_e("#FF0000", "#FF0000") == pytest.approx(0.0, abs=0.01)
    assert pal.delta_e("#FFFFFF", "#000000") == pytest.approx(100.0, abs=0.5)


# Sharma, Wu & Dalal (2005), "The CIEDE2000 Color-Difference Formula", Table 1.
# These are the cases that break naive implementations: the hue-rotation term, the
# discontinuity at the 0/360 boundary, and near-neutral colours.
SHARMA_VECTORS = [
    ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
    ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
    ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
    ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -1.1848, -84.8006), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -0.9009, -85.5211), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
    ((50.0000, -1.0000, 2.0000), (50.0000, 0.0000, 0.0000), 2.3669),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0011), 7.2195),
    ((50.0000, -0.0010, 2.4900), (50.0000, 0.0009, -2.4900), 4.8045),
    ((50.0000, 2.5000, 0.0000), (50.0000, 0.0000, -2.5000), 4.3065),
    ((50.0000, 2.5000, 0.0000), (73.0000, 25.0000, -18.0000), 27.1492),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.1736, 0.5854), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2972, 0.0000), 1.0000),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
]


@pytest.mark.parametrize("lab1,lab2,expected", SHARMA_VECTORS)
def test_delta_e_matches_sharma_reference(lab1, lab2, expected):
    assert pal.delta_e_lab(lab1, lab2) == pytest.approx(expected, abs=0.0001)


def test_delta_e_is_symmetric():
    for a, b in [("#009E73", "#D55E00"), ("#56B4E9", "#CC79A7")]:
        assert pal.delta_e(a, b) == pytest.approx(pal.delta_e(b, a), abs=1e-9)


def test_delta_e_separates_equal_luminance_hues():
    """The reason CIEDE2000 replaced WCAG ratio for stem separation.

    Two bright colours of different hue are near-identical in luminance, so the WCAG
    ratio calls them indistinguishable while they are obviously different on screen.
    """
    green, cyan = "#00E676", "#00E5FF"
    assert pal.contrast_ratio(green, cyan) < 1.3
    assert pal.delta_e(green, cyan) > 20


def test_builtin_palettes_are_internally_distinct():
    for name, palette in pal.BUILTIN.items():
        measured = pal.report(palette, "#1E1E1E")
        assert measured["worst_pair"] >= pal.MIN_DELTA_E, (
            f"{name} has two stems that are hard to tell apart: "
            f"{measured['between_stems']}"
        )


def test_resolve_accepts_name_list_and_mapping():
    assert pal.resolve("okabe-ito") is pal.BUILTIN["okabe-ito"]
    assert pal.resolve(None) is pal.DEFAULT

    colors = ["#111111", "#222222", "#333333", "#444444"]
    assert pal.resolve(colors).colors == tuple(colors)

    mapping = dict(zip(("Drums", "Bass", "Other", "Vocals"), colors))
    assert pal.resolve(mapping).colors == tuple(colors)


def test_resolve_rejects_bad_input():
    with pytest.raises(ValueError):
        pal.resolve("no-such-palette")
    with pytest.raises(ValueError):
        pal.resolve({"Drums": "#000000"})
    with pytest.raises(ValueError):
        pal.resolve(["#000000", "#111111"])
    with pytest.raises(ValueError):
        pal.resolve(["nonsense", "#111111", "#222222", "#333333"])


def test_best_for_background_prefers_visible_palette():
    # deep-light is too dark to see on a dark background and must never win there
    assert pal.best_for_background("#1E1E1E").name != "deep-light"
    assert pal.best_for_background("#FFFFFF").name != "max-separation"
