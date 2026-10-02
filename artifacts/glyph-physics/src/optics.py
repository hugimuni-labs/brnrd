"""The instrument: everything between the phenomenon and the frame.

Physics generators return linear HDR light. This module is the lens, the
emulsion and the glass reticle in front of it: tint, bloom, halation, clipped
highlights, chromatic fringing, mount jitter, grain. Light is allowed to
destroy information here — that is the point of most of it.
"""
from __future__ import annotations

import math
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FPS = 24

# Physical light colours (linear RGB). Warmth always has a cause.
AMBER = np.array([1.00, 0.52, 0.16], np.float32)  # sodium / sunfire
COPPER = np.array([0.85, 0.34, 0.10], np.float32)
FURNACE = np.array([1.00, 0.28, 0.04], np.float32)
WHITEHOT = np.array([1.00, 0.90, 0.78], np.float32)
BONE = np.array([0.93, 0.89, 0.80], np.float32)  # aged off-white
PHOSPHOR = np.array([0.35, 1.00, 0.45], np.float32)
STEEL = np.array([0.55, 0.62, 0.70], np.float32)
UV = np.array([0.55, 0.30, 1.00], np.float32)

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@lru_cache(maxsize=32)
def font(size: int, path: str = MONO):
    """The named font, else the same file found by name (Pillow searches the
    system font dirs), else a common macOS/Windows face, else Pillow's own.
    Labels only — no finding ever uses a font."""
    import os
    for cand in (path, os.path.basename(path), "Menlo.ttc", "Arial Unicode.ttf",
                 "DejaVuSansMono.ttf", "consola.ttf"):
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def tint(I: np.ndarray, col) -> np.ndarray:
    return I[..., None] * np.asarray(col, np.float32)


def up(img: np.ndarray, w=W, h=H) -> np.ndarray:
    if img.shape[1] == w and img.shape[0] == h:
        return img
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_CUBIC)


def splat(xy: np.ndarray, wts=None, w=W, h=H) -> np.ndarray:
    """Point light onto the sensor (nearest pixel; the lens blurs it later)."""
    x = xy[:, 0].astype(np.int64)
    y = xy[:, 1].astype(np.int64)
    ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
    idx = y[ok] * w + x[ok]
    acc = np.bincount(idx, weights=None if wts is None else wts[ok], minlength=w * h)
    return acc.reshape(h, w).astype(np.float32)


def bloom(rgb: np.ndarray, strength=0.35, radius=1.0) -> np.ndarray:
    """Multi-octave glare plus red-shifted halation off the highlights."""
    small = cv2.resize(rgb, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    acc = np.zeros_like(small)
    for s, k in ((3, 0.5), (9, 0.3), (27, 0.2)):
        acc += k * cv2.GaussianBlur(small, (0, 0), s * radius)
    hi = np.maximum(small - 0.8, 0)
    hal = cv2.GaussianBlur(hi, (0, 0), 14 * radius) * np.array([0.9, 0.25, 0.05], np.float32)
    return rgb + strength * up(acc) + 0.6 * strength * up(hal)


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


def develop(hdr: np.ndarray, *, exposure=1.0, frame=0, grain=0.06, ca=1.0,
            weave=0.6, bloom_k=0.35, vignette=0.35, negative=False,
            overlay: np.ndarray | None = None, lift=0.006, seed=0) -> np.ndarray:
    """HDR linear light → 8-bit frame through lens + emulsion."""
    rng = np.random.default_rng(seed * 100003 + frame)
    rgb = bloom(hdr, bloom_k) if bloom_k else hdr
    rgb = 1.0 - np.exp(-rgb * exposure)  # soft shoulder; whites clip honestly
    if overlay is not None:  # etched glass sits in the light path
        a = overlay[..., 3:4]
        rgb = rgb * (1 - 0.55 * a) + overlay[..., :3] * a
    if negative:
        rgb = (1.0 - rgb) * np.array([0.92, 0.95, 1.0], np.float32)
    # Vignette and black lift.
    yy, xx = np.ogrid[0:H, 0:W]
    r2 = ((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2
    rgb = rgb * (1 - vignette * 0.5 * r2)[..., None].astype(np.float32) + lift
    # Lateral chromatic aberration: R spreads, B pulls in.
    if ca:
        rgb = _chroma(rgb, 0.0016 * ca)
    # Mount jitter.
    if weave:
        dx, dy = rng.normal(0, weave, 2)
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        rgb = cv2.warpAffine(rgb, M, (W, H), borderMode=cv2.BORDER_REFLECT)
    # Emulsion grain, strongest in the mids.
    if grain:
        g = rng.normal(0, 1, (H // 2, W // 2)).astype(np.float32)
        g = up(g)
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


# ── the glass: reticles, registration, specimen labels ──────────────────────


class Glass:
    """An RGBA layer of measurement marks. Everything drawn is slightly
    misregistered — the instrument is never quite square to the world."""

    def __init__(self, seed=0, col=(236, 226, 205)):
        self.im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.im)
        self.rng = np.random.default_rng(seed)
        self.col = col

    def _j(self, v, s=1.2):
        return v + self.rng.normal(0, s)

    def text(self, x, y, s, size=18, a=200, col=None, path=MONO, anchor="la"):
        c = col or self.col
        self.d.text((self._j(x, 0.6), self._j(y, 0.6)), s, font=font(int(size * 1.25), path),
                    fill=(*c, a), anchor=anchor)

    def line(self, pts, a=170, w=1, col=None):
        c = col or self.col
        self.d.line([(self._j(x, .4), self._j(y, .4)) for x, y in pts], fill=(*c, a), width=w)

    def reticle(self, cx, cy, r, a=170, ticks=12, gap=0.25):
        cx, cy = self._j(cx), self._j(cy)
        self.d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*self.col, a), width=1)
        for k in range(ticks):
            t = 2 * math.pi * k / ticks
            r0 = r * (0.9 if k % 3 else 0.8)
            self.line([(cx + r0 * math.cos(t), cy + r0 * math.sin(t)),
                       (cx + r * math.cos(t), cy + r * math.sin(t))], a)
        g = r * gap
        for sx, sy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            self.line([(cx + sx * g, cy + sy * g), (cx + sx * r * 1.25, cy + sy * r * 1.25)], a)

    def brackets(self, x0, y0, x1, y1, L=26, a=200, w=2):
        for (x, y, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            self.line([(x + sx * L, y), (x, y), (x, y + sy * L)], a, w)

    def ruler(self, x0, y0, x1, y1, n=40, a=140, major=5):
        self.line([(x0, y0), (x1, y1)], a)
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy)
        nx, ny = -dy / L, dx / L
        for k in range(n + 1):
            t = k / n
            px, py = x0 + dx * t, y0 + dy * t
            m = 12 if k % major == 0 else 6
            self.line([(px, py), (px + nx * m, py + ny * m)], a)

    def regmark(self, x, y, r=14, a=200):
        self.d.ellipse([x - r, y - r, x + r, y + r], outline=(*self.col, a), width=1)
        self.line([(x - r * 1.6, y), (x + r * 1.6, y)], a)
        self.line([(x, y - r * 1.6), (x, y + r * 1.6)], a)

    def frame_count(self, f, a=150):
        self.text(W - 210, H - 46, f"F{f:05d}  {f / FPS:6.2f}s", 16, a)

    def array(self) -> np.ndarray:
        return np.asarray(self.im, np.float32) / 255.0
