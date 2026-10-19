"""A captured, simplified form pressed into dark matter — then made to conduct.

The die is not a letter. It is the polyline set ``observe.simplify`` kept
from the photogram: the skeleton of whatever the membrane did around the
lightning's scar, reduced by Douglas–Peucker until only what a hand would
keep remains. Its distance field becomes a channel profile; the substrate is
fbm rock-metal relief. Impact displaces the surface; heat glows in the crease
(furnace light from inside the mark); then a current runs the channel by arc
length from the root — the pressed trace becoming a conductor.
"""
from __future__ import annotations

import cv2
import numpy as np

from lab import arclength_field, fbm, polyline_distance


class Relief:
    def __init__(self, polys, w=960, h=540, seed=0, width=9.0):
        self.w, self.h = w, h
        self.polys = polys
        self.d = polyline_distance(polys, w, h)
        self.channel = np.exp(-(self.d / width) ** 2).astype(np.float32)
        self.rim = np.exp(-((self.d - 1.6 * width) / (0.7 * width)) ** 2).astype(np.float32)
        self.rock = (0.6 * fbm(w, h, seed + 21, 7, 6) + 0.25 * fbm(w, h, seed + 22, 5, 40)).astype(np.float32)
        self.near = self.channel > 0.02
        self.arclen = arclength_field(polys, w, h, self.near)

    def height(self, depth):
        return self.rock * 0.6 - depth * self.channel + 0.35 * depth * self.rim

    def shade(self, depth, light=(-0.7, -0.5), ambient=0.02, gloss=24):
        hgt = cv2.GaussianBlur(self.height(depth), (0, 0), 1.0)
        gy, gx = np.gradient(hgt * 40)
        nz = 1.0 / np.sqrt(1 + gx * gx + gy * gy)
        lx, ly = light
        lz = 0.45
        L = np.sqrt(lx * lx + ly * ly + lz * lz)
        dif = np.clip((-gx * lx - gy * ly + lz) * nz / L, 0, None)
        return (ambient + 0.5 * dif + 1.5 * dif ** gloss).astype(np.float32)

    def heat(self, h):
        return (h * self.channel ** 1.5 * (0.7 + 0.3 * self.rock)).astype(np.float32)

    def current(self, s, width=0.06, tail=0.35):
        """A pulse at arc length s ∈ [0,1] running the channel, with a decaying tail."""
        x = s - self.arclen
        pulse = np.exp(-(x / width) ** 2) + 0.5 * np.exp(-np.clip(x, 0, None) / tail) * (x > 0)
        return (pulse * self.channel ** 2 * self.near).astype(np.float32)
