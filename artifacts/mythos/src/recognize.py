"""Recognition: the moment a human sees a letter in what matter did.

Nothing here feeds back into physics. A recognition event is triggered by a
*finding* — an operator ``observe`` located in a frame, or a topology a
generator recorded (the lightning's fork, the arch's apex) — and it renders a
letter as a **physical object made by people**, aligned onto that finding:

    type     a cast metal sort, raking light, ink in the counters. A sort is
             mirror-reversed, so the type for ``b`` reads ``d`` — parity is
             built into printing itself
    stone    a rune or letter cut as a V-groove into rock
    ink      printed on translucent polymer, misregistered
    grease   written by hand, in wax pencil, on film
    scratch  scratched into an emulsion

Letters are never text on the screen. They appear for 1–4 frames, as a hard
cut to the carrier (``replace``) or as a faint double exposure over the
phenomenon (``ghost``).

Recognition matures over the film. Early events are **uncertain**: two or
three candidate forms from the operator's family flicker as ghosts and none
wins. Later ones are **confident**: one form, one carrier, hard cut. That arc
— nature makes near-forms; an observer guesses; the guesses converge — is the
pre-history of symbol-based intelligence the later film is about.

Families (operator → candidate readings):

    fork / hooked fork   r ᚠ ᚱ Y ᛉ ᛦ
    arch                 n ᚢ ∩ ω m
    bowl                 u U ᚢ
    cavity / near        o c O
    bowl+stem            b d p q
"""
from __future__ import annotations

import math
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from lab import fbm
from optics import AMBER, BONE, COPPER, FURNACE, STEEL, WHITEHOT, H, W, blur, tint, up

FAMILIES = {
    "fork": ["r", "ᚠ", "Y", "ᛉ"],
    "hooked fork": ["r", "ᚱ", "ᚠ"],
    "arch": ["n", "ᚢ", "∩", "ω"],
    "bowl": ["u", "U", "ᚢ"],
    "cavity": ["o", "c", "O"],
    "near": ["c", "o"],
    "bowl+stem": ["b", "d", "q", "p"],
}

# Runes (and the bare arch) as cut strokes: no installed font is assumed to
# carry the Runic block, and runes were cut as straight strokes anyway.
# Unit box, y down; the stem bottom is at y=1.
RUNE_STROKES = {
    "ᚠ": [[(0.30, 0.0), (0.30, 1.0)], [(0.30, 0.30), (0.75, 0.05)], [(0.30, 0.55), (0.78, 0.30)]],
    "ᚢ": [[(0.25, 1.0), (0.25, 0.08), (0.75, 0.32), (0.75, 1.0)]],
    "ᚱ": [[(0.28, 0.0), (0.28, 1.0)], [(0.28, 0.02), (0.72, 0.25), (0.28, 0.48), (0.75, 1.0)]],
    "ᛉ": [[(0.5, 0.0), (0.5, 1.0)], [(0.12, 0.05), (0.5, 0.45), (0.88, 0.05)]],
    "ᛦ": [[(0.5, 0.0), (0.5, 1.0)], [(0.12, 0.95), (0.5, 0.55), (0.88, 0.95)]],
    "∩": [[(0.2, 1.0), (0.2, 0.45), (0.3, 0.18), (0.5, 0.08), (0.7, 0.18), (0.8, 0.45), (0.8, 1.0)]],
}

FONTS = ["/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf", "FreeSerifBold.ttf", "DejaVuSerif-Bold.ttf",
         "Times New Roman Bold.ttf", "Georgia Bold.ttf", "timesbd.ttf"]


@lru_cache(maxsize=4)
def _font(size):
    for p in FONTS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


@lru_cache(maxsize=64)
def glyph_mask(g: str, px: int = 512, weight: float = 1.0) -> np.ndarray:
    """A tight 0..1 mask of a glyph, ``px`` tall, as an object to be made.
    Runes are drawn as strokes; letters come from a serif face."""
    if g in RUNE_STROKES:
        w = int(px * 0.8)
        im = Image.new("L", (w, px), 0)
        d = ImageDraw.Draw(im)
        lw = int(px * 0.11 * weight)
        for s in RUNE_STROKES[g]:
            d.line([(x * w, 0.04 * px + y * 0.92 * px) for x, y in s], fill=255, width=lw, joint="curve")
            for x, y in (s[0], s[-1]):
                r = lw / 2
                d.ellipse([x * w - r, 0.04 * px + y * 0.92 * px - r, x * w + r, 0.04 * px + y * 0.92 * px + r], fill=255)
        m = np.asarray(im, np.float32) / 255
    else:
        f = _font(px)
        im = Image.new("L", (px * 2, px * 2), 0)
        ImageDraw.Draw(im).text((px // 2, px // 4), g, font=f, fill=255)
        m = np.asarray(im, np.float32) / 255
        if weight != 1.0:
            k = max(1, int(abs(weight - 1) * px * 0.03))
            m = (cv2.dilate if weight > 1 else cv2.erode)(m, np.ones((k, k), np.uint8))
    ys, xs = np.nonzero(m > 0.3)
    if not len(xs):
        return np.zeros((px, px), np.float32)
    m = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return cv2.resize(m, (max(2, int(m.shape[1] * px / m.shape[0])), px), interpolation=cv2.INTER_AREA)


def place(g, anchor, *, mirror=False, weight=1.0, flipv=False):
    """Glyph mask on the full frame. ``anchor`` = (x, y, height, rotation°):
    the glyph's bounding box is centred on (x, y), ``height`` px tall."""
    x, y, hgt, rot = anchor
    m = glyph_mask(g, 512, weight)
    if mirror:
        m = m[:, ::-1]
    if flipv:
        m = m[::-1]
    s = hgt / m.shape[0]
    M = cv2.getRotationMatrix2D((m.shape[1] / 2, m.shape[0] / 2), rot, s)
    M[0, 2] += x - m.shape[1] / 2
    M[1, 2] += y - m.shape[0] / 2
    return cv2.warpAffine(m, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=0.0)


# ── carriers ──────────────────────────────────────────────────────────────


def _shade(height, light=(-0.7, -0.5), k=30.0, gloss=20):
    gy, gx = np.gradient(height * k)
    nz = 1 / np.sqrt(1 + gx * gx + gy * gy)
    lx, ly = light
    dif = np.clip((-gx * lx - gy * ly + 0.5) * nz, 0, None)
    return dif.astype(np.float32), (dif ** gloss).astype(np.float32)


def carrier(kind, g, anchor, *, seed=0, mirror=False):
    """HDR frame of a letter made physical, aligned on ``anchor``."""
    rng = np.random.default_rng(seed)
    if kind == "type":
        # The sort reads mirror-reversed: the printed letter is its parity twin.
        m = place(g, anchor, mirror=not mirror)
        x, y, hgt, rot = anchor
        body = np.zeros((H, W), np.float32)
        bw, bh = hgt * 0.95, hgt * 1.35
        box = cv2.boxPoints(((x, y + hgt * 0.05), (bw, bh), -rot))
        cv2.fillConvexPoly(body, box.astype(np.int32), 1.0)
        body = blur(body, 2.0)
        face = blur(m, 1.2)
        height = 0.25 * body + 0.6 * face
        # Shoulder bevel around the face; the nick groove on the sort's body.
        dif, spec = _shade(blur(height, 1.0), light=(-0.75, -0.55))
        ink = np.clip(blur(m, 3) - m, 0, 1) * 0.8          # ink pooled at the face edges
        rake = np.clip(1.3 - np.linspace(0, 1, W, dtype=np.float32)[None, :] * 1.2, 0.1, 1.3)
        metal = (0.012 + 0.10 * dif * rake + 1.2 * spec * rake) * (body > 0.05)
        grit = up(fbm(W // 3, H // 3, seed + 3, 4, 30)) * 0.012
        # The face carries old ink: near-black, with a wet highlight on its edges.
        inkface = 1 - 0.85 * np.clip(face * 1.4, 0, 1)
        hdr = tint(metal * inkface * (1 - 0.7 * ink) + grit * body, STEEL * 0.7 + BONE * 0.3)
        hdr += tint(np.clip(blur(face, 1) - blur(face, 4), 0, 1) * spec * 3.0, WHITEHOT)
        return hdr
    if kind == "stone":
        m = place(g, anchor, weight=1.15)
        inside = cv2.distanceTransform((m > 0.5).astype(np.uint8), cv2.DIST_L2, 5)
        depth = np.clip(inside / (anchor[2] * 0.05 + 1e-6), 0, 1)       # V-groove profile
        rock = up(0.6 * fbm(W // 3, H // 3, seed + 21, 6, 5) + 0.3 * fbm(W // 3, H // 3, seed + 22, 4, 30))
        height = rock * 0.25 - 0.9 * depth
        dif, spec = _shade(blur(height, 1.2), light=(0.8, -0.45), k=40)
        dust = (rng.random((H, W)) > 0.995).astype(np.float32) * (depth > 0.1)
        rake = np.clip(np.linspace(0.05, 1.4, W, dtype=np.float32)[None, :] ** 1.5, 0.02, 1.6)
        hdr = tint((0.006 + 0.09 * dif * rake + 0.5 * spec * rake), STEEL * 0.4 + COPPER * 0.6) \
            + tint(blur(dust, 0.7) * 0.4 * rake, BONE)
        # Furnace light deep in the cut, as if the stone were still warm.
        hdr += tint(depth ** 4 * 0.06, FURNACE)
        return hdr
    if kind == "ink":
        m = place(g, anchor)
        m2 = place(g, (anchor[0] + 5, anchor[1] + 3, anchor[2], anchor[3]))
        film = 0.55 + 0.15 * up(fbm(W // 4, H // 4, seed + 50, 5, 3))
        bleed = blur(m, 1.6)
        hdr = tint(film * (1 - 0.92 * bleed), BONE * np.array([1.0, 0.93, 0.8], np.float32))
        hdr *= (1 - 0.35 * blur(m2, 2.5)[..., None] * np.array([0.2, 0.5, 0.8], np.float32))
        return hdr
    if kind == "grease":
        m = place(g, anchor, weight=0.85)
        tex = (rng.random((H, W)) > 0.3).astype(np.float32)
        stroke = np.clip(blur(m, 1.0) * (0.6 + 0.4 * blur(tex, 0.6)), 0, 1)
        return stroke  # an alpha, composited by the caller onto film
    if kind == "scratch":
        m = place(g, anchor, weight=0.55)
        edge = np.clip(m - cv2.erode(m, np.ones((3, 3), np.uint8)), 0, 1)
        return tint(blur(edge, 0.6) * 2.5, WHITEHOT)
    raise KeyError(kind)


def to8(hdr, frame=0, seed=0, exposure=1.4):
    from optics import develop
    return develop(hdr, exposure=exposure, frame=frame, seed=seed, grain=0.07, bloom_k=0.4, vignette=0.55)


# ── composition at assembly time ──────────────────────────────────────────


def ghost(base_bgr, g, anchor, alpha=0.35, mirror=False, jitter=(0, 0), tint_col=(0.85, 0.92, 1.0)):
    """A recognition that does not yet trust itself: the letter as a faint,
    misregistered double exposure over the phenomenon."""
    x, y, hgt, rot = anchor
    m = place(g, (x + jitter[0], y + jitter[1], hgt, rot), mirror=mirror, weight=0.8)
    m = blur(m, 2.0)
    edge = np.clip(m - blur(m, 6), 0, 1) * 2
    out = base_bgr.astype(np.float32) / 255.0
    lift = (alpha * (0.35 * m + 0.9 * edge))[..., None] * np.array(tint_col[::-1], np.float32)
    return np.clip((out + lift) * 255, 0, 255).astype(np.uint8)
