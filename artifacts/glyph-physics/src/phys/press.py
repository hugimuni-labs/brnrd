"""A form pressed into dark matter, then made to conduct.

The die is the skeleton's distance field turned into a channel profile; the
substrate is fbm rock-metal relief. Impact displaces the surface; heat glows
in the crease (furnace light from inside the mark); then a current runs the
channel by arc length — the pressed rune becoming a trace.
"""
from __future__ import annotations

import cv2
import numpy as np

from glyphs import SKELETONS, Placement, place, resample, distance
from phys import fbm, smoothstep


class Relief:
    def __init__(self, g, pl: Placement, w=960, h=540, seed=0, width=9.0, wobble=0.02):
        self.w, self.h = w, h
        polys = place(SKELETONS[g], pl, wobble, seed)
        self.d = distance(polys, w, h)
        self.channel = np.exp(-(self.d / width) ** 2).astype(np.float32)
        self.rim = (np.exp(-((self.d - 1.6 * width) / (0.7 * width)) ** 2)).astype(np.float32)
        self.rock = (0.6 * fbm(w, h, seed + 21, 7, 6) + 0.25 * fbm(w, h, seed + 22, 5, 40)).astype(np.float32)
        # Arc length along the skeleton, propagated outward to the channel.
        pts = resample(polys, 1.5)
        self.arclen = np.zeros((h, w), np.float32)
        if len(pts):
            from scipy.spatial import cKDTree
            yy, xx = np.mgrid[0:h, 0:w]
            near = self.channel > 0.02
            _, idx = cKDTree(pts).query(np.stack([xx[near], yy[near]], 1))
            s = np.arange(len(pts), dtype=np.float32) / max(1, len(pts) - 1)
            self.arclen[near] = s[idx]
        self.near = self.channel > 0.02

    def height(self, depth):
        return self.rock * 0.6 - depth * self.channel + 0.35 * depth * self.rim

    def shade(self, depth, light=(-0.7, -0.5), ambient=0.02):
        hgt = cv2.GaussianBlur(self.height(depth), (0, 0), 1.0)
        gy, gx = np.gradient(hgt * 40)
        nz = 1.0 / np.sqrt(1 + gx * gx + gy * gy)
        lx, ly = light
        lz = 0.45
        L = np.sqrt(lx * lx + ly * ly + lz * lz)
        dif = np.clip((-gx * lx - gy * ly + lz) * nz / L, 0, None)
        spec = dif ** 24
        return (ambient + 0.5 * dif + 1.5 * spec).astype(np.float32)

    def heat(self, h):
        return (h * self.channel ** 1.5 * (0.7 + 0.3 * self.rock)).astype(np.float32)

    def current(self, s, width=0.06, tail=0.35):
        """A pulse at arc length s ∈ [0,1] running the channel, with a decaying tail."""
        x = s - self.arclen
        pulse = np.exp(-(x / width) ** 2) + 0.5 * np.exp(-np.clip(x, 0, None) / tail) * (x > 0)
        return (pulse * self.channel ** 2 * self.near).astype(np.float32)
