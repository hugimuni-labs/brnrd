"""Capture: a pattern exposed onto translucent emulsion, then developed.

Photographic development is autocatalytic: a latent speck of silver makes the
crystal around it reduce faster, so density grows from what is already
there. Here, per developing step,

    dD = dt · (k₀ E + k₁ E D) · (1 − D) · g(x)

where E is the exposure the polymer received, D the silver density, and g a
fixed grain field (the emulsion's own crystals — the image can only exist at
the resolution the material allows). Developer exhausts near dense areas, so
a thin bright fringe survives along every boundary — the *adjacency effect*,
which is why old scientific plates look inked.

The exposure is a photogram: the captured form sat on the film and blocked
light. So the form stays clear while the ground goes black — the trace is
literally the light that was *not* stopped. Backlit, it glows through.

The film is a physical object: wrinkled polymer, uneven thickness, a
perforated rebate. A human mark (one grease-pencil loop) is the only sign
that someone looked.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from lab import fbm


class Emulsion:
    def __init__(self, w, h, seed=0, grain_px=1.3):
        rng = np.random.default_rng(seed)
        g = rng.random((h, w)).astype(np.float32)
        g = cv2.GaussianBlur(g, (0, 0), grain_px * 0.5)
        g = (g - g.mean()) / (g.std() + 1e-6)
        self.grain = np.clip(0.75 + 0.35 * g, 0.2, 1.6).astype(np.float32)
        self.D = np.zeros((h, w), np.float32)
        self.E = np.zeros((h, w), np.float32)
        self.w, self.h = w, h
        wr = fbm(w // 2, h // 2, seed + 50, 6, 3)
        gy, gx = np.gradient(cv2.GaussianBlur(wr, (0, 0), 3) * 30)
        self.wrinkle = cv2.resize(np.clip(0.92 + 0.12 * gx - 0.05 * gy, 0.7, 1.15).astype(np.float32), (w, h))
        self.thick = cv2.resize((0.5 + 0.5 * fbm(w // 4, h // 4, seed + 51, 5, 8)).astype(np.float32), (w, h))

    def expose(self, mask, amount=1.0, scatter=1.5):
        """``mask`` 0..1: where the object sat. Light is everything else,
        scattered a little inside the polymer (halation)."""
        light = 1.0 - np.clip(mask, 0, 1)
        light = cv2.GaussianBlur(light, (0, 0), scatter)
        self.E += amount * light

    def develop(self, dt=0.12, k0=0.25, k1=3.0, steps=1):
        for _ in range(steps):
            E = np.clip(self.E, 0, 2)
            dD = dt * (k0 * E + k1 * E * self.D) * (1 - self.D) * self.grain
            self.D = np.clip(self.D + dD, 0, 1)

    def adjacency(self, k=0.35, r=6.0):
        """Edge effect: bright rim inside clear areas next to dense ones."""
        near = cv2.GaussianBlur(self.D, (0, 0), r)
        return np.clip(self.D - k * (near - self.D), 0, 1)

    def transmit(self):
        """Fraction of backlight the film passes."""
        Dm = self.adjacency()
        return np.exp(-3.2 * Dm * (0.8 + 0.4 * self.thick)) * self.wrinkle


def grease_loop(w, h, cx, cy, rx, ry, seed=0, progress=1.0, width=7):
    """One hand-drawn loop around a found form: overshoot, uneven pressure,
    a wax skip or two. Returns a 0..1 coverage map. ``progress`` draws it."""
    rng = np.random.default_rng(seed)
    n = 220
    a0 = rng.uniform(0, 2 * math.pi)
    t = np.linspace(0, 2 * math.pi * 1.12, n)  # overshoot past closure
    wob = 1 + 0.05 * np.sin(t * 2 + rng.uniform(0, 6)) + 0.03 * np.sin(t * 5 + rng.uniform(0, 6))
    drift = np.linspace(0, 1, n) * 0.08
    x = cx + rx * wob * (1 + drift) * np.cos(a0 + t)
    y = cy + ry * wob * (1 - drift * 0.5) * np.sin(a0 + t) + rng.normal(0, 0.6, n).cumsum() * 0.3
    m = int(max(2, progress * n))
    img = np.zeros((h, w), np.float32)
    pres = 0.7 + 0.3 * np.sin(np.linspace(0, 7, n) + rng.uniform(0, 6))
    for i in range(m - 1):
        if rng.random() < 0.015:
            continue  # wax skip
        cv2.line(img, (int(x[i] * 16), int(y[i] * 16)), (int(x[i + 1] * 16), int(y[i + 1] * 16)),
                 float(pres[i]), width, cv2.LINE_AA, shift=4)
    tex = (np.random.default_rng(seed + 1).random((h, w)) > 0.35).astype(np.float32)
    return np.clip(img * (0.55 + 0.45 * cv2.GaussianBlur(tex, (0, 0), 0.7)), 0, 1)
