"""The instrument: everything between the phenomenon and the frame.

Generators return linear HDR light. This module is the lens, the emulsion and
the apparatus in front of them. It has two jobs:

1. **Witness.** Bloom, halation, starburst diffraction off point sources,
   clipped highlights, chromatic fringing, mount jitter, grain. Light is
   allowed to destroy information — most of this module exists to let it.
2. **Observe.** Observation is not a HUD. It is a *change of imaging
   modality*: a focus pull, a slit closing, an occulting disk, a polariser
   turning, a raster acquisition, a negative, an exposure ramp, a frame edge.
   These are the only ways the film shows a human apparatus touching the
   phenomenon. There is no text anywhere in the picture.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

W, H = 1920, 1080
FPS = 24

# Physical light colours (linear RGB). Warmth always has a cause.
AMBER = np.array([1.00, 0.52, 0.16], np.float32)     # sodium / sunfire
COPPER = np.array([0.85, 0.34, 0.10], np.float32)
FURNACE = np.array([1.00, 0.28, 0.04], np.float32)
WHITEHOT = np.array([1.00, 0.90, 0.78], np.float32)
BONE = np.array([0.93, 0.89, 0.80], np.float32)      # aged off-white
PHOSPHOR = np.array([0.35, 1.00, 0.45], np.float32)
STEEL = np.array([0.55, 0.62, 0.70], np.float32)
UV = np.array([0.55, 0.30, 1.00], np.float32)
HALPHA = np.array([1.00, 0.20, 0.10], np.float32)    # 656 nm, the chromosphere


def tint(I: np.ndarray, col) -> np.ndarray:
    return I[..., None] * np.asarray(col, np.float32)


def up(img: np.ndarray, w=W, h=H) -> np.ndarray:
    if img.shape[1] == w and img.shape[0] == h:
        return img
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_CUBIC)


def blur(img, s):
    if s <= 0:
        return img
    return cv2.GaussianBlur(img, (0, 0), s)


def splat(xy: np.ndarray, wts=None, w=W, h=H) -> np.ndarray:
    """Point light onto the sensor, bilinear so slow drift doesn't stair-step."""
    x = xy[:, 0]
    y = xy[:, 1]
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = (x - x0).astype(np.float32)
    fy = (y - y0).astype(np.float32)
    wv = np.ones(len(x), np.float32) if wts is None else np.asarray(wts, np.float32)
    acc = np.zeros(w * h, np.float64)
    for dx, dy, k in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)),
                      (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        xi, yi = x0 + dx, y0 + dy
        ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        acc += np.bincount(yi[ok] * w + xi[ok], weights=(wv * k)[ok], minlength=w * h)
    return acc.reshape(h, w).astype(np.float32)


def shift(img, dx, dy):
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REFLECT)


def bloom(rgb: np.ndarray, strength=0.35, radius=1.0) -> np.ndarray:
    """Multi-octave glare plus red-shifted halation off the highlights."""
    h, w = rgb.shape[:2]
    small = cv2.resize(rgb, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    acc = np.zeros_like(small)
    for s, k in ((3, 0.5), (9, 0.3), (27, 0.2)):
        acc += k * cv2.GaussianBlur(small, (0, 0), s * radius)
    hi = np.maximum(small - 0.8, 0)
    hal = cv2.GaussianBlur(hi, (0, 0), 14 * radius) * np.array([0.9, 0.25, 0.05], np.float32)
    return rgb + strength * up(acc, w, h) + 0.6 * strength * up(hal, w, h)


def starburst(rgb: np.ndarray, thresh=2.5, blades=6, length=0.11, k=0.05, rot=0.3) -> np.ndarray:
    """Aperture-blade diffraction spikes off anything brighter than ``thresh``.
    A polygonal iris turns point sources into stars — the lens admitting it
    is a lens."""
    h, w = rgb.shape[:2]
    s = 4
    small = cv2.resize(np.maximum(rgb - thresh, 0), (w // s, h // s), interpolation=cv2.INTER_AREA)
    if small.max() <= 0:
        return rgb
    L = int(length * w / s)
    acc = np.zeros_like(small)
    for b in range(blades // 2):
        a = rot + math.pi * b / (blades // 2)
        ker = np.zeros((2 * L + 1, 2 * L + 1), np.float32)
        for t in range(-L, L + 1):
            x = int(round(L + t * math.cos(a)))
            y = int(round(L + t * math.sin(a)))
            ker[y, x] = math.exp(-abs(t) / (0.2 * L))
        ker = cv2.GaussianBlur(ker, (0, 0), 1.2)  # spikes are diffraction, never hairlines
        ker /= ker.sum()
        acc += cv2.filter2D(small, -1, ker)
    return rgb + k * up(acc, w, h) * 40


def god_ray(w, h, src, angle, width, falloff=0.9, t=0.0) -> np.ndarray:
    """A shaft of light crossing the frame from ``src`` at ``angle``."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = xx - src[0], yy - src[1]
    ca, sa = math.cos(angle), math.sin(angle)
    along = dx * ca + dy * sa
    across = -dx * sa + dy * ca
    wid = width * (1 + 0.0007 * np.maximum(along, 0))
    beam = np.exp(-(across / wid) ** 2) * (along > 0) * np.exp(-falloff * along / w)
    flick = 1 + 0.15 * np.sin(across / 23.0 + t * 3.1) * np.sin(across / 61.0 - t * 1.7)
    return (beam * flick).astype(np.float32)


# ── modalities: how an apparatus touches the phenomenon ─────────────────────


def defocus(hdr, depth, focus, aperture=14.0, levels=6):
    """Thin-lens depth of field from a per-pixel depth map. Blur radius grows
    with |depth − focus|; layered so a rack focus is a real pull, not a fade."""
    coc = np.clip(np.abs(depth - focus) * aperture, 0, aperture)
    out = np.zeros_like(hdr)
    wsum = np.zeros(hdr.shape[:2], np.float32)
    radii = np.linspace(0, aperture, levels)
    step = radii[1] - radii[0] if levels > 1 else 1
    for r in radii:
        wgt = np.clip(1 - np.abs(coc - r) / step, 0, 1).astype(np.float32)
        if wgt.max() <= 0:
            continue
        b = blur(hdr, r * 0.6) if r > 0.3 else hdr
        out += b * wgt[..., None]
        wsum += wgt
    return out / np.maximum(wsum, 1e-6)[..., None]


def slit(w, h, cx, cy, angle, half_width, soft=6.0):
    """A slit / knife-edge pair: transmission mask with a diffraction-soft edge."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    across = -(xx - cx) * math.sin(angle) + (yy - cy) * math.cos(angle)
    m = 1 / (1 + np.exp((np.abs(across) - half_width) / soft))
    # Fresnel ringing just inside the jaws.
    ring = 0.12 * np.cos((np.abs(across) - half_width) / 3.0) * np.exp(-np.abs(np.abs(across) - half_width) / 18)
    return np.clip(m + ring * m, 0, 1.2).astype(np.float32)


def occulter(w, h, cx, cy, r, soft=3.0):
    """A coronagraph's occulting disk: blocks the source so its halo can be seen.
    Returns (transmission, diffraction ring light)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.hypot(xx - cx, yy - cy)
    T = 1 / (1 + np.exp(-(d - r) / soft))
    ringlight = np.exp(-((d - r) / 2.5) ** 2) + 0.3 * np.exp(-((d - r * 1.04) / 4.0) ** 2)
    return T.astype(np.float32), ringlight.astype(np.float32)


def photoelastic(stress: np.ndarray, analyser: float, order: float = 7.0) -> np.ndarray:
    """Isochromatic fringes of a stressed transparent sheet between crossed
    polarisers. Retardation ∝ principal-stress difference; each wavelength
    goes dark at its own integer orders, which is where the colour comes from.
    ``analyser`` ∈ [0, 1] is how far the analyser has turned toward crossed."""
    lam = np.array([0.65, 0.55, 0.45], np.float32)  # R, G, B in μm
    delta = stress[..., None] * order * 0.55 / lam
    I = np.sin(np.pi * delta) ** 2
    open_ = np.ones_like(I) * 0.6
    return ((1 - analyser) * open_ + analyser * I).astype(np.float32)


def raster(h_img, row, width=10, ahead_dim=0.15, trail=0.0):
    """Raster acquisition: rows above ``row`` are acquired, the scan line is hot,
    rows below are still dark (only ``ahead_dim`` of them reaches the sensor)."""
    hh = h_img.shape[0]
    y = np.arange(hh, dtype=np.float32)[:, None]
    acquired = (y < row).astype(np.float32)
    line = np.exp(-((y - row) / width) ** 2)
    m = acquired + ahead_dim * (1 - acquired) + 1.8 * line
    if trail:
        m += trail * np.exp(-np.clip(row - y, 0, None) / 40) * acquired
    return m.astype(np.float32)


def film_edge(w, h, x0, pitch=118, frame_no=0, side="left"):
    """Perforations and the dark rebate of a strip of film entering the frame.
    Returns (alpha mask of the strip base, holes mask). No edge print: the
    apparatus is shown, not labelled."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base = (xx > x0) if side == "left" else (xx < x0)
    rebate = ((xx > x0) & (xx < x0 + 150)) if side == "left" else ((xx < x0) & (xx > x0 - 150))
    cx = x0 + 75 if side == "left" else x0 - 75
    py = (yy + frame_no * 7.3) % pitch - pitch / 2
    holes = (np.abs(xx - cx) < 26) & (np.abs(py) < 18)
    holes = cv2.GaussianBlur(holes.astype(np.float32), (0, 0), 1.2)
    return base.astype(np.float32), rebate.astype(np.float32), holes


# ── development: linear light → frame ──────────────────────────────────────

_VIG = None


def _vignette(h, w):
    global _VIG
    if _VIG is None or _VIG.shape != (h, w):
        yy, xx = np.ogrid[0:h, 0:w]
        _VIG = (((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2).astype(np.float32)
    return _VIG


def develop(hdr: np.ndarray, *, exposure=1.0, frame=0, grain=0.06, ca=1.0,
            weave=0.6, bloom_k=0.35, vignette=0.35, negative=False, star=0.0,
            lift=0.006, seed=0, toe=0.0) -> np.ndarray:
    """HDR linear light → 8-bit frame through lens + emulsion."""
    rng = np.random.default_rng(seed * 100003 + frame)
    rgb = hdr
    if star:
        rgb = starburst(rgb, k=star)
    if bloom_k:
        rgb = bloom(rgb, bloom_k)
    rgb = 1.0 - np.exp(-rgb * exposure)  # soft shoulder; whites clip honestly
    if toe:
        rgb = np.clip(rgb - toe, 0, None) / (1 - toe)
    if negative:
        rgb = (1.0 - rgb) * np.array([0.92, 0.95, 1.0], np.float32)
    rgb = rgb * (1 - vignette * 0.5 * _vignette(H, W))[..., None] + lift
    if ca:
        rgb = _chroma(rgb, 0.0016 * ca)
    if weave:
        dx, dy = rng.normal(0, weave, 2)
        rgb = shift(rgb, dx, dy)
    if grain:
        g = up(rng.normal(0, 1, (H // 2, W // 2)).astype(np.float32))
        L = rgb.mean(-1, keepdims=True)
        rgb = rgb + grain * g[..., None] * (0.06 + 1.2 * np.sqrt(np.clip(L, 0, 1)) * (1 - L))
    out = np.clip(rgb, 0, 1) ** (1 / 2.2)
    return (out * 255 + 0.5).astype(np.uint8)


def _chroma(rgb, k):
    out = rgb.copy()
    for ch, s in ((0, 1 + k), (2, 1 - k)):
        M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s)
        out[..., ch] = cv2.warpAffine(rgb[..., ch], M, (W, H), borderMode=cv2.BORDER_REFLECT)
    return out
