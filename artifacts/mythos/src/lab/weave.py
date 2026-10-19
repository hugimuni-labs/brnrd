"""Thread: conductors become fibres, under macro light.

A plain weave as a height field. Warp threads run vertically, weft
horizontally; each thread is a twisted cylinder whose height undulates as it
passes over one crossing and under the next. Whichever thread is higher at a
pixel is the one you see. Fibre striations follow the twist (diagonal along
the thread), which is what makes it read as yarn and not as a grid.

One weft thread is different: it is drawn copper, and it carries the pulse.
Its route is not straight — it inherits the bends of the conductor it came
from, as a gentle vertical offset along its length.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


class Weave:
    def __init__(self, w=1920, h=1080, pitch=64.0, seed=0, angle=0.0):
        self.w, self.h, self.p = w, h, pitch
        rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        ca, sa = math.cos(angle), math.sin(angle)
        self.X = xx * ca + yy * sa
        self.Y = -xx * sa + yy * ca
        self.jit_w = rng.normal(0, 0.06, 4096).astype(np.float32)
        self.jit_f = rng.normal(0, 0.06, 4096).astype(np.float32)
        self.fiber = rng.normal(0, 1, (h // 2, w // 2)).astype(np.float32)

    def field(self, route=None, t=0.0, slack=0.0):
        """Height, which-thread, along-coordinate, thread index.
        ``route``: optional per-column y offset (px) for the weft family,
        a bend inherited from upstream."""
        p = self.p
        X, Y = self.X, self.Y
        Yw = Y + (route if route is not None else 0.0)
        iw = np.floor(X / p).astype(np.int32)
        jf = np.floor(Yw / p).astype(np.int32)
        uw = X / p - iw - 0.5 + self.jit_w[iw % 4096]
        vf = Yw / p - jf - 0.5 + self.jit_f[jf % 4096]
        r = 0.36
        # Cylinder cross-sections.
        cw = np.sqrt(np.clip(1 - (uw / r) ** 2, 0, None))
        cf = np.sqrt(np.clip(1 - (vf / r) ** 2, 0, None))
        # Over/under: warp i is up where (i + j) is even.
        par = ((iw + jf) % 2) * 2 - 1
        und_w = 0.5 * par * np.cos(np.pi * (Yw / p - jf - 0.5))
        und_f = -0.5 * par * np.cos(np.pi * (X / p - iw - 0.5))
        hw = np.where(cw > 0, cw * 0.6 + und_w + 0.6, -9)
        hf = np.where(cf > 0, cf * 0.6 + und_f + 0.6, -9)
        top_warp = hw >= hf
        hgt = np.maximum(hw, hf)
        hgt = np.where(hgt < -1, -0.4, hgt)
        return hgt.astype(np.float32), top_warp, X, Yw, iw, jf

    def render(self, light=(-0.5, -0.7), route=None, t=0.0, live_row=None, pulse_x=None,
               pulse_w=60.0, tint_warp=(0.42, 0.36, 0.30), tint_weft=(0.50, 0.44, 0.36)):
        hgt, top_warp, X, Yw, iw, jf = self.field(route, t)
        # Fibre striations: twist angle ±30° along each thread.
        fib = cv2.resize(self.fiber, (self.w, self.h))
        s_w = np.sin((Yw * 0.9 + X * 0.5) * 1.1) * 0.5 + 0.5
        s_f = np.sin((X * 0.9 - Yw * 0.5) * 1.1) * 0.5 + 0.5
        stri = np.where(top_warp, s_w, s_f) * 0.35 + 0.65 + 0.12 * fib
        gy, gx = np.gradient(cv2.GaussianBlur(hgt, (0, 0), 1.2) * 18)
        nz = 1 / np.sqrt(1 + gx * gx + gy * gy)
        lx, ly = light
        dif = np.clip((-gx * lx - gy * ly + 0.6) * nz, 0, None)
        spec = dif ** 18
        occl = np.clip(hgt + 0.4, 0, 1.6) / 1.6  # gaps between threads go dark
        base = np.where(top_warp[..., None], np.array(tint_warp, np.float32), np.array(tint_weft, np.float32))
        img = base * (dif * stri * occl)[..., None] + 0.25 * spec[..., None] * occl[..., None]
        live = np.zeros(hgt.shape, np.float32)
        if live_row is not None:
            on = (~top_warp) & (jf == live_row) & (hgt > -1)
            copper = np.array([0.85, 0.40, 0.14], np.float32)
            img = np.where(on[..., None], copper * (dif * stri * occl)[..., None] * 1.2 + 0.5 * spec[..., None], img)
            if pulse_x is not None:
                g = np.exp(-((X - pulse_x) / pulse_w) ** 2)
                # The pulse is inside the thread, so it glows through where the
                # weft is under too — dimmer, diffused by the warp fibres.
                row = (jf == live_row).astype(np.float32)
                vis = np.where(on, 1.0, 0.25) * row
                live = (g * vis * np.clip(1 - np.abs(Yw / self.p - jf - 0.5) / 0.4, 0, 1)).astype(np.float32)
        return img.astype(np.float32), live, hgt
