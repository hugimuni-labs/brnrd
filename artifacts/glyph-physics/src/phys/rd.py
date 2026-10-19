"""Gray–Scott reaction–diffusion: cavities that close (o), fail to close (c),
grow against a wall (a).

Two chemicals, U fed in and V autocatalytic (U + 2V → 3V), V decaying. The
glyph is a landscape of feed and kill rates: the reaction grows labyrinths
everywhere on the plate *except* along the skeleton, where the kill rate is
too high for V to live. The form is never drawn; it is the one channel the
growth cannot enter — a void inside mass, which is what a counter is.
"""
from __future__ import annotations

import cv2
import numpy as np

from glyphs import Placement, glyph_distance
from phys import fbm

LAP = np.array([[0.05, 0.2, 0.05], [0.2, -1.0, 0.2], [0.05, 0.2, 0.05]], np.float32)


class Reaction:
    def __init__(self, g, pl: Placement, w=480, h=270, seed=0, width=7.0,
                 inside=(0.02, 0.075), outside=(0.029, 0.057), wobble=0.03, region=0.55):
        self.w, self.h = w, h
        rng = np.random.default_rng(seed)
        d = glyph_distance(g, w, h, pl, wobble, seed)
        t = np.exp(-(d / width) ** 2).astype(np.float32)
        # The growth lives on a soft disc around the form, not the whole plate.
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        rr = np.hypot((xx - pl.cx) / w, (yy - pl.cy) / w) * (1 + 0.25 * fbm(w, h, seed + 5, 4, 3))
        self.disc = np.clip(1 - (rr / region) ** 4, 0, 1).astype(np.float32)
        t = np.maximum(t, 1 - self.disc)
        self.t = t
        self.F = (outside[0] + (inside[0] - outside[0]) * t).astype(np.float32)
        self.k = (outside[1] + (inside[1] - outside[1]) * t).astype(np.float32)
        self.U = np.ones((h, w), np.float32)
        self.V = np.zeros((h, w), np.float32)
        # Seed patches (single cells die): U=½, V=¼ in small blobs.
        seeds = cv2.dilate(((rng.random((h, w)) < 0.004) & (t < 0.5)).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        self.V[seeds] = 0.25 + 0.05 * rng.random(seeds.sum())
        self.U[seeds] = 0.5
        self.rng = rng

    def retarget(self, g, pl, width=7.0, wobble=0.03, seed=0, inside=(0.02, 0.075), outside=(0.029, 0.057)):
        d = glyph_distance(g, self.w, self.h, pl, wobble, seed)
        t = np.maximum(np.exp(-(d / width) ** 2), 1 - self.disc).astype(np.float32)
        self.t = t
        self.F = (outside[0] + (inside[0] - outside[0]) * t).astype(np.float32)
        self.k = (outside[1] + (inside[1] - outside[1]) * t).astype(np.float32)
        seeds = (self.rng.random((self.h, self.w)) < 0.01) & (t < 0.5)
        self.V[seeds] = np.maximum(self.V[seeds], 0.8)

    def step(self, n=24, Du=1.0, Dv=0.5, dt=1.0, agitate=0.0):
        U, V = self.U, self.V
        for _ in range(n):
            lu = cv2.filter2D(U, -1, LAP, borderType=cv2.BORDER_REFLECT)
            lv = cv2.filter2D(V, -1, LAP, borderType=cv2.BORDER_REFLECT)
            uvv = U * V * V
            U += dt * (Du * lu - uvv + self.F * (1 - U))
            V += dt * (Dv * lv + uvv - (self.F + self.k) * V)
        if agitate:
            V += agitate * self.rng.normal(0, 1, V.shape).astype(np.float32) * (V > 0.05)
            np.clip(V, 0, 1, out=V)
        self.U, self.V = U, V
        return V
