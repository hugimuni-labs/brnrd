"""Charged dust in a potential: the shared substrate of the whole film.

Langevin dynamics — damping, a force down the gradient of a potential, and
thermal kicks of temperature T. The potential is the glyph's distance field
(V = ½d²), so every particle feels pulled toward the nearest stroke. Mix two
targets and the dust is caught between readings; raise T and it boils off;
drop T and the near-form condenses. Shape-matching transitions are just a
change of potential with the dust left to find its own way across.

A curl-noise wind keeps the dust drifting in the god ray before any form
exists — and never quite stops after.
"""
from __future__ import annotations

import cv2
import numpy as np

from glyphs import Placement, glyph_distance
from phys import fbm, sample

FW, FH = 480, 270


def potential(g, pl_full: Placement, W, H, wobble=0.02, seed=0, cap=120.0):
    """Force field (fx, fy) at field resolution for a glyph placed in full-res px."""
    k = FW / W
    pl = Placement(pl_full.cx * k, pl_full.cy * k, pl_full.size * k, pl_full.rot, pl_full.shear, pl_full.flip_x)
    d = glyph_distance(g, FW, FH, pl, wobble, seed)
    d = np.minimum(d, cap * k)
    gy, gx = np.gradient(cv2.GaussianBlur(d, (0, 0), 1.0))
    # F = −∇(½d²) = −d∇d, in full-res px units.
    return (-d * gx / k).astype(np.float32), (-d * gy / k).astype(np.float32)


class Dust:
    def __init__(self, n, W, H, seed=0):
        self.W, self.H = W, H
        self.rng = np.random.default_rng(seed)
        self.p = self.rng.uniform([0, 0], [W, H], (n, 2)).astype(np.float32)
        self.v = np.zeros_like(self.p)
        self.prev = self.p.copy()
        self.lum = self.rng.lognormal(0, 0.8, n).astype(np.float32)
        cx = fbm(FW, FH, seed + 11, 4, 3)
        cy = fbm(FW, FH, seed + 12, 4, 3)
        # Curl of a scalar noise field → divergence-free drift.
        psi = cv2.GaussianBlur(cx + 0.5 * cy, (0, 0), 4)
        gy, gx = np.gradient(psi)
        self.wind = (gy.astype(np.float32), -gx.astype(np.float32))

    def step(self, forces, wts, T=1.0, gamma=0.25, k=0.03, wind=0.0, dt=1.0, sub=2):
        self.prev = self.p.copy()
        s = FW / self.W
        for _ in range(sub):
            fx = np.zeros(len(self.p), np.float32)
            fy = np.zeros_like(fx)
            x, y = self.p[:, 0] * s, self.p[:, 1] * s
            for (Fx, Fy), w in zip(forces, wts):
                if w:
                    fx += w * sample(Fx, x, y)
                    fy += w * sample(Fy, x, y)
            if wind:
                fx += wind * 60 * sample(self.wind[0], x, y)
                fy += wind * 60 * sample(self.wind[1], x, y)
            noise = self.rng.normal(0, 1, self.p.shape).astype(np.float32) * np.sqrt(2 * gamma * T)
            self.v += dt / sub * (-gamma * self.v + k * np.stack([fx, fy], 1)) + noise / np.sqrt(sub)
            self.p += dt / sub * self.v
        np.clip(self.p[:, 0], -50, self.W + 50, out=self.p[:, 0])
        np.clip(self.p[:, 1], -50, self.H + 50, out=self.p[:, 1])
