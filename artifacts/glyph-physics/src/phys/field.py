"""Iron filings between magnetic poles.

In the plane, the field lines of a +/− pole pair are circles through both
poles. The upper arcs read as ``n``; the lower arcs, the same field, read as
``u``. Add a third pole of the first sign and the field reads ``m`` above and
``ω`` below. The glyph pairs are not designed to rhyme — they are one field
seen from either side. What the viewer reads is decided by where the light
falls: the field doesn't choose, the observation does.

Filings are rendered by line-integral convolution of a sparse dot texture
along the field direction. Each dot smears into a dash aligned with the
field, so the texture *is* filings. Tapping the plate (re-seeding a fraction
of the dots per frame) is the vibration.
"""
from __future__ import annotations

import cv2
import numpy as np

from glyphs import Placement, place

# Pole sets in em coords: (x, y, charge). The pole feet sit on the baseline
# where the glyph's legs touch the ground.
POLES_EM = {
    "n": [(0.33, 0.70, 1), (0.67, 0.70, -1)],
    "u": [(0.33, 0.48, 1), (0.67, 0.48, -1)],
    "m": [(0.24, 0.70, 1), (0.50, 0.70, -1), (0.76, 0.70, 1)],
    "ω": [(0.24, 0.48, 1), (0.50, 0.48, -1), (0.76, 0.48, 1)],
    "ᚢ": [(0.34, 0.82, 1), (0.66, 0.60, -1)],
}


def poles_px(g, pl: Placement):
    P = POLES_EM[g]
    xy = place([[(x, y) for x, y, _ in P]], pl)[0]
    return [(float(x), float(y), q) for (x, y), (_, _, q) in zip(xy, P)]


class Filings:
    def __init__(self, w=640, h=360, density=0.10, seed=0):
        self.w, self.h = w, h
        self.rng = np.random.default_rng(seed)
        self.density = density
        self.dots = self._dots(np.ones((h, w), bool))
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        self.xx, self.yy = xx, yy

    def _dots(self, where):
        d = (self.rng.random((self.h, self.w)) < self.density) * self.rng.uniform(0.3, 1, (self.h, self.w))
        return np.where(where, d, 0).astype(np.float32)

    def tap(self, frac=0.15):
        """A knock on the plate: a fraction of the filings jump."""
        sel = self.rng.random((self.h, self.w)) < frac
        self.dots = np.where(sel, self._dots(np.ones_like(sel)), self.dots).astype(np.float32)

    def field(self, poles, soft=3.0):
        bx = np.zeros((self.h, self.w), np.float32)
        by = np.zeros_like(bx)
        for x, y, q in poles:
            dx, dy = self.xx - x, self.yy - y
            r2 = dx * dx + dy * dy + soft * soft
            bx += q * dx / r2
            by += q * dy / r2
        mag = np.sqrt(bx * bx + by * by) + 1e-9
        return bx / mag, by / mag, mag

    def render(self, poles, steps=18, h=0.9, scale=1.0):
        ux, uy, mag = self.field([(x * scale, y * scale, q) for x, y, q in poles])
        acc = self.dots.copy()
        wsum = np.ones_like(acc)
        for sgn in (1.0, -1.0):
            px, py = self.xx.copy(), self.yy.copy()
            for k in range(1, steps + 1):
                vx = cv2.remap(ux, px, py, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                vy = cv2.remap(uy, px, py, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                px += sgn * h * vx
                py += sgn * h * vy
                wk = 0.5 + 0.5 * np.cos(np.pi * k / (steps + 1))
                acc += wk * cv2.remap(self.dots, px, py, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
                wsum += wk
        lic = acc / wsum
        # Filings crowd where the field is strong.
        dens = np.clip(mag * scale * 40.0, 0, 1) ** 0.6
        return (lic * (0.25 + 0.75 * dens)).astype(np.float32), mag
