"""A source outrunning its own waves, photographed by schlieren.

Each instant the source emits a circular front that expands at the medium's
speed c. When the source moves at v > c the fronts pile up on an envelope:
a cone of half-angle asin(c/v). Moving down the frame, the cone is ``V``;
moving up, the same wake is ``^``. Leave the hot trail visible behind the
source and the cone grows a stem: ``Y``. A wall that reflects the front adds
a crossbar: ``A``. The letters are the same event with different velocity.

Schlieren photography sees the *gradient* of refractive index, so the image
is the derivative of the pressure field along the knife edge — it gives the
classic split light/dark fronts.
"""
from __future__ import annotations

import numpy as np

from phys import fbm


class Wake:
    def __init__(self, w=640, h=360, c=3.0, seed=0):
        self.w, self.h, self.c = w, h, c
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        self.xx, self.yy = xx, yy
        self.turb = fbm(w, h, seed + 3, 5, 6)

    def field(self, p0, vel, t, *, emit_dt=0.6, history=90.0, width=1.6,
              trail=0.0, wall=None):
        """Pressure-ish field at time t for a source at p0 + vel·t."""
        P = np.zeros((self.h, self.w), np.float32)
        ts = np.arange(max(0.0, t - history), t, emit_dt)
        for te in ts:
            sx, sy = p0[0] + vel[0] * te, p0[1] + vel[1] * te
            r = self.c * (t - te)
            d = np.sqrt((self.xx - sx) ** 2 + (self.yy - sy) ** 2) - r
            amp = 1.0 / np.sqrt(1.0 + 0.05 * r)
            P += amp * np.exp(-(d / width) ** 2) * np.sign(-d + 0.3)
            if wall is not None:  # mirror source across a horizontal wall at y=wall
                my = 2 * wall - sy
                d2 = np.sqrt((self.xx - sx) ** 2 + (self.yy - my) ** 2) - r
                P += 0.7 * amp * np.exp(-(d2 / width) ** 2) * np.sign(-d2 + 0.3) * (self.yy < wall)
        if trail:
            sx, sy = p0[0] + vel[0] * t, p0[1] + vel[1] * t
            vx, vy = vel
            L = np.hypot(vx, vy) + 1e-9
            ux, uy = vx / L, vy / L
            dx, dy = self.xx - sx, self.yy - sy
            along = -(dx * ux + dy * uy)
            across = dx * -uy + dy * ux
            wob = 3.0 * self.turb
            tr = np.exp(-((across + wob) / (2.0 + 0.02 * along)) ** 2) * (along > 0) * np.exp(-along / (L * history * 0.8))
            P += trail * tr * (1 + 0.6 * self.turb)
        return P

    @staticmethod
    def schlieren(P, knife=(1.0, 0.35)):
        gy, gx = np.gradient(P)
        return (knife[0] * gx + knife[1] * gy).astype(np.float32)
