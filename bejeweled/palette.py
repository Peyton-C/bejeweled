"""Stem colours, and the contrast maths for choosing them.

Mixxx reads the per-stem colour out of the stem file itself, so the palette written
here is what ends up on screen. That makes it an accessibility setting, not decoration:
the default colourblind-safe palette is tuned for distinguishability between the four
colours, not for contrast against whatever background a skin happens to use.

Two different things matter, and a palette can pass one while failing the other:

  - contrast against the background, so a stem is visible at all
  - contrast between the stems, so they can be told apart from each other

`report` measures both. Thresholds follow WCAG 2.1 non-text contrast (1.4.11), which
asks for 3:1 on interface components - waveforms and meters are exactly that.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .stems import NI_SLOTS

# WCAG 1.4.11 for user interface components and graphical objects
MIN_CONTRAST = 3.0
# WCAG 1.4.3 for text; worth meeting when a colour carries a label
TEXT_CONTRAST = 4.5
# CIEDE2000 difference at which two colours read as clearly different at a glance
MIN_DELTA_E = 20.0


@dataclass(frozen=True)
class Palette:
    """Four colours, in NI slot order: Drums, Bass, Other, Vocals."""

    name: str
    colors: tuple[str, str, str, str]
    description: str = ""

    def __post_init__(self):
        if len(self.colors) != 4:
            raise ValueError(f"palette {self.name!r} needs 4 colours, got {len(self.colors)}")
        for color in self.colors:
            parse_hex(color)

    def as_dict(self) -> dict[str, str]:
        return dict(zip(NI_SLOTS, self.colors))


# ------------------------------------------------------------------------- contrast

def parse_hex(color: str) -> tuple[int, int, int]:
    """Parse '#RGB' or '#RRGGBB' into 8-bit channels."""
    value = color.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        raise ValueError(f"not a hex colour: {color!r}")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ValueError(f"not a hex colour: {color!r}") from None


def relative_luminance(color: str) -> float:
    """WCAG relative luminance, 0 for black through 1 for white."""
    channels = []
    for raw in parse_hex(color):
        c = raw / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours, from 1.0 to 21.0.

    Purely luminance-based, which makes it the right measure for "is this visible
    against the background" and the wrong one for "can these two be told apart" - two
    equally bright colours of opposite hue score near 1:1 here. Use `delta_e` for that.
    """
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def to_lab(color: str) -> tuple[float, float, float]:
    """Convert sRGB to CIE L*a*b* under a D65 white point."""
    linear = []
    for raw in parse_hex(color):
        c = raw / 255
        linear.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = linear

    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.00000
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: str, b: str) -> float:
    """CIEDE2000 perceptual difference between two hex colours.

    Roughly: under 2 is invisible, 10 is noticeable at a glance, and over 20 reads as
    two clearly different colours. This is what decides whether two stems can be told
    apart, since it accounts for hue and chroma rather than brightness alone.
    """
    return delta_e_lab(to_lab(a), to_lab(b))


def delta_e_lab(lab1: tuple[float, float, float],
                lab2: tuple[float, float, float]) -> float:
    """CIEDE2000 between two L*a*b* triples, per Sharma et al. (2005)."""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2

    avg_l = (l1 + l2) / 2
    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    avg_c = (c1 + c2) / 2

    g = 0.5 * (1 - math.sqrt(avg_c ** 7 / (avg_c ** 7 + 25 ** 7))) if avg_c else 0
    a1p, a2p = a1 * (1 + g), a2 * (1 + g)
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    avg_cp = (c1p + c2p) / 2

    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360

    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    else:
        dhp = h2p - h1p - 360 if h2p > h1p else h2p - h1p + 360

    dlp = l2 - l1
    dcp = c2p - c1p
    dhp_term = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp) / 2)

    if c1p * c2p == 0:
        avg_hp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        avg_hp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        avg_hp = (h1p + h2p + 360) / 2
    else:
        avg_hp = (h1p + h2p - 360) / 2

    t = (1
         - 0.17 * math.cos(math.radians(avg_hp - 30))
         + 0.24 * math.cos(math.radians(2 * avg_hp))
         + 0.32 * math.cos(math.radians(3 * avg_hp + 6))
         - 0.20 * math.cos(math.radians(4 * avg_hp - 63)))

    sl = 1 + (0.015 * (avg_l - 50) ** 2) / math.sqrt(20 + (avg_l - 50) ** 2)
    sc = 1 + 0.045 * avg_cp
    sh = 1 + 0.015 * avg_cp * t
    rt = (-2 * math.sqrt(avg_cp ** 7 / (avg_cp ** 7 + 25 ** 7))
          * math.sin(math.radians(60 * math.exp(-(((avg_hp - 275) / 25) ** 2)))))

    return math.sqrt(
        (dlp / sl) ** 2 + (dcp / sc) ** 2 + (dhp_term / sh) ** 2
        + rt * (dcp / sc) * (dhp_term / sh)
    )


# ------------------------------------------------------------------------- palettes

BUILTIN: dict[str, Palette] = {
    "okabe-ito": Palette(
        "okabe-ito",
        ("#009E73", "#D55E00", "#CC79A7", "#56B4E9"),
        "Colourblind-safe default, as written by stemgen and NI tooling.",
    ),
    "vivid-dark": Palette(
        "vivid-dark",
        ("#00E676", "#FF9100", "#FF4FD8", "#40C4FF"),
        "High-luminance version of the default hues, for dark skins.",
    ),
    "deep-light": Palette(
        "deep-light",
        ("#00695C", "#BF360C", "#AD1457", "#01579B"),
        "Darkened hues for light skins, where bright colours wash out.",
    ),
    "max-separation": Palette(
        "max-separation",
        ("#FFFFFF", "#FFD400", "#FF2D95", "#00E5FF"),
        "Maximum separation from each other and from dark backgrounds; "
        "abandons colourblind-safety in favour of raw contrast.",
    ),
}

DEFAULT = BUILTIN["okabe-ito"]


def report(palette: Palette, background: str) -> dict:
    """Measure a palette two ways: visibility on the background, separation from itself.

    These need different metrics. Visibility is a luminance question, so it uses the
    WCAG ratio. Separation is a perceptual-colour question, so it uses CIEDE2000 -
    scoring it by luminance would fail every palette built from equally bright hues.
    """
    against_bg = {
        slot: contrast_ratio(color, background)
        for slot, color in zip(NI_SLOTS, palette.colors)
    }
    pairs = {}
    entries = list(zip(NI_SLOTS, palette.colors))
    for i, (slot_a, color_a) in enumerate(entries):
        for slot_b, color_b in entries[i + 1:]:
            pairs[(slot_a, slot_b)] = delta_e(color_a, color_b)

    worst_bg = min(against_bg.values())
    worst_pair = min(pairs.values())
    return {
        "palette": palette.name,
        "background": background,
        "against_background": against_bg,
        "between_stems": pairs,
        "worst_background": worst_bg,
        "worst_pair": worst_pair,
        "passes": worst_bg >= MIN_CONTRAST and worst_pair >= MIN_DELTA_E,
    }


def report_multi(palette: Palette, backgrounds: list[str]) -> dict:
    """Measure a palette across every background it has to work on.

    A palette is only as good as its worst deck, so the per-stem figure kept here is
    the minimum across backgrounds rather than an average.
    """
    per_bg = {bg: report(palette, bg) for bg in backgrounds}
    worst_per_slot = {
        slot: min(r["against_background"][slot] for r in per_bg.values())
        for slot in NI_SLOTS
    }
    any_report = next(iter(per_bg.values()))
    worst_bg = min(worst_per_slot.values())
    worst_pair = any_report["worst_pair"]  # independent of background

    return {
        "palette": palette.name,
        "backgrounds": backgrounds,
        "per_background": per_bg,
        "against_background": worst_per_slot,
        "between_stems": any_report["between_stems"],
        "worst_background": worst_bg,
        "worst_pair": worst_pair,
        "passes": worst_bg >= MIN_CONTRAST and worst_pair >= MIN_DELTA_E,
    }


def best_for_backgrounds(backgrounds: list[str],
                         candidates: dict[str, Palette] | None = None) -> Palette:
    """Pick the built-in palette that stays legible on all the given backgrounds."""
    def score(palette: Palette) -> tuple[float, float]:
        measured = report_multi(palette, backgrounds)
        return (
            min(measured["worst_background"] / MIN_CONTRAST,
                measured["worst_pair"] / MIN_DELTA_E),
            measured["worst_background"],
        )

    return max((candidates or BUILTIN).values(), key=score)


def best_for_background(background: str, candidates: dict[str, Palette] | None = None) -> Palette:
    """Pick the built-in palette that stays most legible on a given background.

    Ranked by the weakest link - the worst of the background and pairwise contrasts -
    since one invisible stem ruins a palette regardless of how good the others are.
    """
    def score(palette: Palette) -> tuple[float, float]:
        measured = report(palette, background)
        # Normalise both metrics against their thresholds so they are comparable,
        # then rank on whichever is doing worse
        return (
            min(measured["worst_background"] / MIN_CONTRAST,
                measured["worst_pair"] / MIN_DELTA_E),
            measured["worst_background"],
        )

    return max((candidates or BUILTIN).values(), key=score)


def resolve(spec) -> Palette:
    """Turn a palette name, a list of four colours, or a slot->colour mapping into one.

    Accepting all three means a config file, a CLI flag and a library call can share the
    same notion of "which colours", without callers converting between them.
    """
    if spec is None:
        return DEFAULT
    if isinstance(spec, Palette):
        return spec
    if isinstance(spec, str):
        if spec in BUILTIN:
            return BUILTIN[spec]
        raise ValueError(f"unknown palette {spec!r}, expected one of {', '.join(BUILTIN)}")
    if isinstance(spec, dict):
        missing = [slot for slot in NI_SLOTS if slot not in spec]
        if missing:
            raise ValueError(f"palette mapping is missing {', '.join(missing)}")
        return Palette("custom", tuple(spec[slot] for slot in NI_SLOTS), "From configuration.")
    colors = tuple(spec)
    return Palette("custom", colors, "From configuration.")
