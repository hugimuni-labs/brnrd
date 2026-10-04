"""The laboratory: physical generators that know nothing about letters.

Every module here is parameterised by forces, frequencies, fields, boundaries,
temperatures, densities and seeds. None of them accepts a glyph, a skeleton or
a target shape. Whatever recognisable form comes out is found afterwards by
``observe.py`` — glyph-likeness is a discovery criterion, never an input.

| module     | phenomenon                                   | operators it tends to discover |
|------------|----------------------------------------------|--------------------------------|
| web        | Zel'dovich collapse of a random potential    | line, filament, node           |
| disk       | Keplerian belt, density waves, lensed far side| loop, cavity, arc, jet (line) |
| plasma     | coronal loops traced through sub-surface flux| arch, bend, reconnection fork  |
| breakdown  | channel growth up a potential (space colon.) | branch, fork, hook, stem       |
| membrane   | driven FDTD membrane + hopping grains        | bowl, mirrored lobes, channel  |
| develop    | photographic development as autocatalysis    | (inherits whatever was exposed)|
| relief     | a captured trace pressed into dark matter    | (inherits the die)             |
| weave      | warp and weft under macro light              | line, crossing, over/under     |

Two generators (``develop``, ``relief``) do take a shape — but only one that an
earlier generator made and ``observe`` captured. That is the causal chain of
the film, not a glyph handed in from outside.
"""
from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np


@lru_cache(maxsize=32)
def fbm(w: int, h: int, seed: int = 0, octaves: int = 6, base: int = 4) -> np.ndarray:
    """Fractal noise, zero mean, unit std. Cached: treat the result as read-only."""
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        n = base * 2 ** o
        g = rng.normal(size=(max(2, n * h // w), n)).astype(np.float32)
        acc += amp * cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)
        tot += amp
        amp *= 0.55
    acc /= tot
    return (acc - acc.mean()) / (acc.std() + 1e-6)


def sample(field: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = field.shape[:2]
    xi = np.clip(x.astype(np.int32), 0, w - 1)
    yi = np.clip(y.astype(np.int32), 0, h - 1)
    return field[yi, xi]


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def bump(k, a, b, ramp=4.0):
    """0 → 1 → 0 window over [a, b] with soft edges."""
    return float(smoothstep(a - ramp, a, k) * (1 - smoothstep(b, b + ramp, k)))


def curl_wind(w: int, h: int, seed: int, scale: float = 4.0):
    """Divergence-free drift from the curl of a smooth scalar."""
    psi = cv2.GaussianBlur(fbm(w, h, seed, 4, 3), (0, 0), scale)
    gy, gx = np.gradient(psi)
    return gy.astype(np.float32), (-gx).astype(np.float32)


def polyline_distance(polys, w: int, h: int, scale: int = 1) -> np.ndarray:
    """Distance (px) to a set of polylines, at w×h. Rasterised then EDT."""
    m = np.full((h * scale, w * scale), 255, np.uint8)
    for p in polys:
        if len(p) < 2:
            continue
        pts = (np.asarray(p, np.float64) * scale * 16).astype(np.int32)
        cv2.polylines(m, [pts], False, 0, 1, cv2.LINE_8, shift=4)
    d = cv2.distanceTransform(m, cv2.DIST_L2, 5) / scale
    if scale != 1:
        d = cv2.resize(d, (w, h), interpolation=cv2.INTER_AREA)
    return d.astype(np.float32)


def arclength_field(polys, w: int, h: int, near: np.ndarray) -> np.ndarray:
    """For pixels in ``near``, the normalised arc length of the nearest point on
    the polylines, measured from the root (the first point of the first poly).
    Each poly's arc starts where its first point sits on an earlier poly."""
    from scipy.spatial import cKDTree
    pts, s = [], []
    offset = {}
    for i, p in enumerate(polys):
        p = np.asarray(p, np.float32)
        if len(p) < 2:
            continue
        seg = np.r_[0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
        start = 0.0
        if pts:
            P = np.concatenate(pts)
            S = np.concatenate(s)
            dd, j = cKDTree(P).query(p[0])
            start = float(S[j]) if dd < 6 else 0.0
        pts.append(p)
        s.append(seg + start)
        offset[i] = start
    if not pts:
        return np.zeros((h, w), np.float32)
    P = np.concatenate(pts)
    S = np.concatenate(s)
    S = S / (S.max() + 1e-6)
    out = np.zeros((h, w), np.float32)
    yy, xx = np.nonzero(near)
    _, idx = cKDTree(P).query(np.stack([xx, yy], 1))
    out[yy, xx] = S[idx]
    return out
