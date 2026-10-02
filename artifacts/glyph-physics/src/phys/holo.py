"""Coherent light through a glyph-shaped aperture; focus is the reading.

The aperture is a thin rough slot in the shape of the skeleton, illuminated
by sodium light with a slowly drifting random phase. The angular-spectrum
method propagates it a distance z. At z = 0 the glyph is there; anywhere else
it is fringes and speckle. Racking focus is the act of observation that makes
the form exist — and the speckle keeps boiling even when it does.
"""
from __future__ import annotations

import cv2
import numpy as np

from glyphs import Placement, glyph_distance
from phys import fbm


class Aperture:
    def __init__(self, g, pl: Placement, w=640, h=360, seed=0, slot=2.2,
                 wavelength=0.55, wobble=0.02):
        self.w, self.h = w, h
        d = glyph_distance(g, w, h, pl, wobble, seed)
        rough = 1 + 0.35 * fbm(w, h, seed + 7, 5, 16)
        self.amp = (np.exp(-(d / slot) ** 2) * np.clip(rough, 0, None)).astype(np.float32)
        self.phase_a = fbm(w, h, seed + 1, 4, 24) * 2.5
        self.phase_b = fbm(w, h, seed + 2, 4, 24) * 2.5
        fx = np.fft.fftfreq(w)[None, :]
        fy = np.fft.fftfreq(h)[:, None]
        arg = 1.0 - (wavelength * fx) ** 2 - (wavelength * fy) ** 2
        self.kz = (2 * np.pi / wavelength) * np.sqrt(np.clip(arg, 0, None))

    def intensity(self, z, t=0.0):
        ph = np.cos(t) * self.phase_a + np.sin(t) * self.phase_b
        E = self.amp * np.exp(1j * ph)
        if abs(z) > 1e-3:
            E = np.fft.ifft2(np.fft.fft2(E) * np.exp(1j * self.kz * z))
        I = np.abs(E) ** 2
        return (I / (np.percentile(I, 99.7) + 1e-9)).astype(np.float32)
