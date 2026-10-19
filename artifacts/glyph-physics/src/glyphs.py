"""Glyph skeletons and the lexicon that ties each one to the physics that finds it.

A glyph here is never typeset. It is a *skeleton*: a handful of strokes in a
unit em box (x right, y down; ascender 0.08, x-height 0.40, baseline 0.78,
descender 0.98). Every generator in ``phys/`` receives the skeleton only as a
force — an attractor set, a potential, an aperture, a pole placement — and the
form that comes out is whatever that physics makes of it. Legibility is an
outcome, never an input.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

Stroke = list  # list[(x, y)]


def _line(*pts):
    return [tuple(p) for p in pts]


def _arc(cx, cy, rx, ry, a0, a1, n=48):
    a = np.radians(np.linspace(a0, a1, n))
    return [(cx + rx * math.cos(t), cy + ry * math.sin(t)) for t in a]


def _mirror_x(strokes):
    return [[(1.0 - x, y) for x, y in s] for s in strokes]


def _mirror_y(strokes, axis):
    return [[(x, 2 * axis - y) for x, y in s] for s in strokes]


_b = [_line((0.35, 0.08), (0.35, 0.78)), _arc(0.52, 0.59, 0.17, 0.19, 0, 360)]
_p = [_line((0.35, 0.40), (0.35, 0.98)), _arc(0.52, 0.59, 0.17, 0.19, 0, 360)]
_n = [
    _line((0.33, 0.40), (0.33, 0.78)),
    _arc(0.50, 0.56, 0.17, 0.15, 180, 360),
    _line((0.67, 0.56), (0.67, 0.78)),
]
_m = [
    _line((0.24, 0.40), (0.24, 0.78)),
    _arc(0.37, 0.56, 0.13, 0.15, 180, 360),
    _line((0.50, 0.56), (0.50, 0.78)),
    _arc(0.63, 0.56, 0.13, 0.15, 180, 360),
    _line((0.76, 0.56), (0.76, 0.78)),
]

SKELETONS: dict[str, list[Stroke]] = {
    # ── fork family: stems, hooks, splits ────────────────────────────────
    "l": [_line((0.5, 0.08), (0.5, 0.78))],
    "r": [_line((0.40, 0.40), (0.40, 0.78)), _arc(0.57, 0.58, 0.17, 0.16, 180, 290)],
    "Y": [_line((0.28, 0.12), (0.5, 0.46), (0.72, 0.12)), _line((0.5, 0.46), (0.5, 0.86))],
    "V": [_line((0.28, 0.18), (0.5, 0.80), (0.72, 0.18))],
    "^": [_line((0.28, 0.66), (0.5, 0.30), (0.72, 0.66))],
    "A": [_line((0.27, 0.80), (0.5, 0.12), (0.73, 0.80)), _line((0.36, 0.54), (0.64, 0.54))],
    "ᚠ": [
        _line((0.38, 0.10), (0.38, 0.90)),
        _line((0.38, 0.34), (0.64, 0.14)),
        _line((0.38, 0.54), (0.66, 0.32)),
    ],
    "ᚱ": [_line((0.36, 0.10), (0.36, 0.90)), _line((0.36, 0.10), (0.62, 0.30), (0.36, 0.50), (0.66, 0.90))],
    "ᛉ": [_line((0.5, 0.10), (0.5, 0.90)), _line((0.28, 0.16), (0.5, 0.48), (0.72, 0.16))],
    "⤙": [_line((0.20, 0.50), (0.80, 0.50)), _line((0.82, 0.34), (0.66, 0.50), (0.82, 0.66))],
    # ── bowl family: cavities, counters, mirrored lobes ──────────────────
    "b": _b,
    "d": _mirror_x(_b),
    "p": _p,
    "q": _mirror_x(_p),
    "o": [_arc(0.5, 0.59, 0.19, 0.19, 0, 360)],
    "c": [_arc(0.52, 0.59, 0.19, 0.19, 40, 320)],
    "a": [_arc(0.47, 0.61, 0.15, 0.17, 0, 360), _line((0.62, 0.42), (0.62, 0.78))],
    "J": [_line((0.60, 0.08), (0.60, 0.68)), _arc(0.47, 0.68, 0.13, 0.12, 0, 180)],
    # ── channel family: arches, troughs, prongs ──────────────────────────
    "n": _n,
    "u": _mirror_y(_n, 0.59),
    "m": _m,
    "ω": _mirror_y(_m, 0.59),
    "ᚢ": [_line((0.34, 0.90), (0.34, 0.14), (0.66, 0.34), (0.66, 0.90))],
}

FAMILIES = {
    "fork": ["l", "r", "Y", "V", "^", "A", "ᚠ", "ᚱ", "ᛉ", "⤙"],
    "bowl": ["b", "d", "p", "q", "o", "c", "a", "J"],
    "channel": ["n", "u", "m", "ω", "ᚢ"],
}
FAMILY_OF = {g: f for f, gs in FAMILIES.items() for g in gs}


@dataclass(frozen=True)
class Etymology:
    """Why a physical system produces a glyph — the relation, not the picture."""

    physics: str  # generator key in phys/
    reading: str  # the one-line relation, used verbatim in overlays and LEXICON.md
    twin: str | None = None  # the glyph the same physics yields under a symmetry


# The native physics of each glyph. Every generator can render every glyph
# (that is the barrage), but only these pairings are *honest*: the physics
# produces the shape from its own law, not from being told the answer.
LEXICON: dict[str, Etymology] = {
    "l": Etymology("breakdown", "one channel: the first path a discharge commits to"),
    "r": Etymology("breakdown", "a channel that hesitates and hooks toward the nearer charge"),
    "Y": Etymology("mach", "a supersonic wake: the cone, and the trail that made it", None),
    "V": Etymology("mach", "the shock cone of anything faster than its medium", "^"),
    "^": Etymology("mach", "the same cone, travelling the other way", "V"),
    "A": Etymology("breakdown", "a fork whose arms are crossed by their own branch"),
    "ᚠ": Etymology("breakdown", "a stem that branches twice toward the same side"),
    "ᚱ": Etymology("breakdown", "a stem whose branch folds back on itself"),
    "ᛉ": Etymology("breakdown", "a fork that keeps its trunk"),
    "⤙": Etymology("breakdown", "a route that splits at its end: fan-out"),
    "b": Etymology("chladni", "a cavity on a stem: grains fleeing the antinode", "d"),
    "d": Etymology("chladni", "the same mode with odd parity flipped", "b"),
    "p": Etymology("chladni", "b under the vertical reflection of the plate", "q"),
    "q": Etymology("chladni", "both parities flipped: b rotated half a turn", "p"),
    "o": Etymology("rd", "a cavity that closes on itself: reaction outruns diffusion"),
    "c": Etymology("rd", "a cavity that failed to close"),
    "a": Etymology("rd", "a cavity grown against a wall"),
    "J": Etymology("holo", "a stem whose foot is still out of focus"),
    "n": Etymology("field", "the line from N to S, read from above", "u"),
    "u": Etymology("field", "the same line, read from below", "n"),
    "m": Etymology("field", "three poles, read from above", "ω"),
    "ω": Etymology("field", "three poles, read from below", "m"),
    "ᚢ": Etymology("field", "an arch with one leg pulled long"),
}

# The relation between glyphs: which physical operation turns one into another.
SYMMETRIES = [
    ("b", "d", "parity in x  (odd modes change sign)"),
    ("b", "p", "parity in y"),
    ("b", "q", "both parities: a half turn"),
    ("n", "u", "one field, read from either side of the poles"),
    ("m", "ω", "one field, read from either side of three poles"),
    ("V", "^", "one wake, velocity reversed"),
    ("Y", "V", "the wake with and without its trail"),
    ("l", "r", "a channel before and after its first branch"),
    ("o", "c", "a cavity, closed and open"),
]


# ── rasterisation ───────────────────────────────────────────────────────────


@dataclass
class Placement:
    cx: float  # pixel centre of the em box
    cy: float
    size: float  # em box edge in pixels
    rot: float = 0.0  # radians
    shear: float = 0.0
    flip_x: bool = False


def place(strokes, pl: Placement, wobble: float = 0.0, seed: int = 0):
    """Strokes → pixel-space polylines. ``wobble`` bends the skeleton by a
    smooth low-frequency displacement field, so no two findings share a contour."""
    rng = np.random.default_rng(seed)
    k = rng.normal(size=(4, 3))
    c, s = math.cos(pl.rot), math.sin(pl.rot)
    out = []
    for st in strokes:
        pts = np.asarray(st, np.float64)
        if pl.flip_x:
            pts[:, 0] = 1.0 - pts[:, 0]
        x, y = pts[:, 0] - 0.5, pts[:, 1] - 0.53
        if wobble:
            x = x + wobble * (k[0, 0] * np.sin(3.1 * y + k[0, 1]) + 0.5 * k[1, 0] * np.sin(7.3 * y + k[1, 1]))
            y = y + wobble * (k[2, 0] * np.sin(2.7 * x + k[2, 1]) + 0.5 * k[3, 0] * np.sin(6.1 * x + k[3, 1]))
        x = x + pl.shear * y
        px = pl.cx + pl.size * (c * x - s * y)
        py = pl.cy + pl.size * (s * x + c * y)
        out.append(np.stack([px, py], 1))
    return out


def resample(polys, step: float):
    """Even samples along the polylines, ``step`` pixels apart."""
    pts = []
    for p in polys:
        seg = np.diff(p, axis=0)
        L = np.hypot(seg[:, 0], seg[:, 1])
        cum = np.concatenate([[0], np.cumsum(L)])
        if cum[-1] <= 0:
            continue
        t = np.arange(0, cum[-1], step)
        pts.append(np.stack([np.interp(t, cum, p[:, 0]), np.interp(t, cum, p[:, 1])], 1))
    return np.concatenate(pts) if pts else np.zeros((0, 2))


def mask(polys, w: int, h: int, thick: float) -> np.ndarray:
    m = np.zeros((h, w), np.uint8)
    sh = 4
    for p in polys:
        cv2.polylines(m, [np.round(p * (1 << sh)).astype(np.int32)], False, 255,
                      max(1, int(round(thick))), cv2.LINE_AA, shift=sh)
    return m


def distance(polys, w: int, h: int) -> np.ndarray:
    """Unsigned distance (pixels) to the skeleton."""
    m = mask(polys, w, h, 1)
    return cv2.distanceTransform(255 - (m > 0).astype(np.uint8) * 255, cv2.DIST_L2, 5)


def glyph_distance(g: str, w: int, h: int, pl: Placement, wobble=0.0, seed=0):
    return distance(place(SKELETONS[g], pl, wobble, seed), w, h)
