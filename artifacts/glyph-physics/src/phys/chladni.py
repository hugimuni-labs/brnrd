"""Grains on a driven membrane.

The membrane's displacement is a sum of plate modes cos(nπx)cos(mπy) — the
DCT basis, so fitting a target is one transform. The glyph enters only as an
amplitude *envelope*: near zero on the skeleton, one elsewhere. Truncating the
fit to modes with n² + m² < K² is the drive frequency: low K gives the
blurred near-forms and stray nodal rings of a low tone, high K sharpens
toward the skeleton. Legibility is literally a function of frequency.

Grains are not told where to go. Each hops with a step proportional to the
local amplitude, so they random-walk out of the antinodes and pile up where
the plate is still — the way sand does on a real plate.

Parity is exact: flipping x multiplies mode n by (−1)ⁿ. Interpolating the
coefficients of ``b`` toward their parity-flipped copy passes through a state
with only even-n modes — the symmetric two-lobed form — on its way to ``d``.
"""
from __future__ import annotations

import cv2
import numpy as np

from glyphs import Placement, glyph_distance

GW, GH = 480, 270


class Membrane:
    def __init__(self, n_grains=180_000, seed=0, w=GW, h=GH):
        self.w, self.h = w, h
        self.rng = np.random.default_rng(seed)
        self.p = self.rng.uniform([0, 0], [w, h], (n_grains, 2)).astype(np.float32)
        self.prev = self.p.copy()
        n = np.arange(w)[None, :]
        m = np.arange(h)[:, None]
        self.radius = np.sqrt((n * h / w) ** 2 + m ** 2).astype(np.float32)  # isotropic-ish order
        self.parity_x = np.where(n % 2 == 0, 1.0, -1.0).astype(np.float32) * np.ones((h, 1), np.float32)
        self.parity_y = np.where(m % 2 == 0, 1.0, -1.0).astype(np.float32) * np.ones((1, w), np.float32)
        self.U = np.ones((h, w), np.float32)
        self.gx = np.zeros((h, w), np.float32)
        self.gy = np.zeros((h, w), np.float32)

    def coeffs(self, g, pl: Placement, width=5.0, wobble=0.02, seed=0):
        """Mode coefficients whose envelope is still on the glyph's skeleton."""
        d = glyph_distance(g, self.w, self.h, pl, wobble, seed)
        A = 1.0 - np.exp(-(d / width) ** 2)
        return cv2.dct(A.astype(np.float32))

    def plate(self, a, b):
        """A pure square-plate Chladni mode (the generic pattern before any glyph)."""
        C = np.zeros((self.h, self.w), np.float32)
        a, b = int(a), int(b)
        C[b, a] = 1.0
        C[a, b] = -1.0 if a != b else 0.0
        C[0, 0] = 0.0
        return C * (self.w * self.h) ** 0.5 * 0.5

    def set_field(self, C, K):
        keep = self.radius < K
        U = cv2.idct((C * keep).astype(np.float32))
        U = np.abs(U)
        self.U = U / (np.percentile(U, 98) + 1e-6)
        gy, gx = np.gradient(cv2.GaussianBlur(self.U, (0, 0), 1.2))
        self.gx, self.gy = gx.astype(np.float32), gy.astype(np.float32)

    def shake(self, amp=2.2, substeps=4, p=1.6, floor=0.02, kick=0.0, drift=6.0):
        """One frame of drive: grains hop in proportion to local amplitude."""
        self.prev = self.p.copy()
        for _ in range(substeps):
            x = np.clip(self.p[:, 0].astype(np.int32), 0, self.w - 1)
            y = np.clip(self.p[:, 1].astype(np.int32), 0, self.h - 1)
            s = amp * (self.U[y, x] ** p + floor) + kick
            self.p += self.rng.normal(0, 1, self.p.shape).astype(np.float32) * s[:, None]
            # Bounced grains also walk downhill in amplitude: the plate empties.
            self.p[:, 0] -= drift * self.gx[y, x]
            self.p[:, 1] -= drift * self.gy[y, x]
            # The plate's rim reflects grains back.
            self.p[:, 0] = np.abs(self.p[:, 0])
            self.p[:, 1] = np.abs(self.p[:, 1])
            self.p[:, 0] = self.w - 1 - np.abs(self.w - 1 - self.p[:, 0])
            self.p[:, 1] = self.h - 1 - np.abs(self.h - 1 - self.p[:, 1])

    def grains_full(self, W, H):
        k = W / self.w
        return self.p * k, self.prev * k


def mix(coeffs: list[np.ndarray], wts) -> np.ndarray:
    out = np.zeros_like(coeffs[0])
    for c, w in zip(coeffs, wts):
        if w:
            out += w * c
    return out
