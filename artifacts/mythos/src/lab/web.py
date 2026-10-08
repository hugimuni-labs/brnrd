"""Energy without names: a random potential collapsing into filaments.

The Zel'dovich approximation. Start from a near-uniform sea of particles on a
lattice q and a Gaussian random potential φ with a red power spectrum. Each
particle moves along the potential's gradient, scaled by a growth factor D:

    x(q, D) = q − D ∇φ(q)

Nothing is told to form a line. Where neighbouring trajectories cross (shell
crossing) the density diverges into caustics: first sheets, then filaments,
then nodes where filaments meet. It is the first operator the universe finds —
**line** — and the second — **node**, where lines agree.

The same lattice at three depths (seeds, scales) gives the volume: a near slab
blurred to bokeh dust, a sharp mid slab, a faint far one.
"""
from __future__ import annotations

import numpy as np


class Web:
    def __init__(self, nx=640, ny=360, extent=(2400.0, 1350.0), seed=0, slope=2.6,
                 smooth=70.0, amp=1.0):
        rng = np.random.default_rng(seed)
        self.nx, self.ny = nx, ny
        self.ex, self.ey = extent
        kx = np.fft.fftfreq(nx)[None, :] * nx / self.ex
        ky = np.fft.fftfreq(ny)[:, None] * ny / self.ey
        k = np.sqrt(kx * kx + ky * ky)
        k[0, 0] = 1.0
        k0 = 1.0 / max(self.ex, self.ey)
        P = (k / k0) ** (-slope) * np.exp(-(2 * np.pi * k * smooth) ** 2)  # smooth: px
        P[0, 0] = 0.0
        noise = rng.normal(size=(ny, nx)) + 1j * rng.normal(size=(ny, nx))
        phik = noise * np.sqrt(P)
        # Displacement Ψ = −∇φ, in Fourier space −i k φ.
        psx = np.real(np.fft.ifft2(-1j * 2 * np.pi * kx * phik))
        psy = np.real(np.fft.ifft2(-1j * 2 * np.pi * ky * phik))
        rms = np.sqrt((psx ** 2 + psy ** 2).mean()) + 1e-12
        spacing = self.ex / nx
        # Normalise so D=1 moves a particle ~6 lattice spacings: well past
        # shell crossing, into filaments.
        self.psi = np.stack([psx.ravel(), psy.ravel()], 1).astype(np.float32) / rms * spacing * 6 * amp
        qy, qx = np.mgrid[0:ny, 0:nx].astype(np.float32)
        jitter = rng.uniform(-0.5, 0.5, (ny * nx, 2)).astype(np.float32)
        self.q = np.stack([(qx.ravel() + 0.5), (qy.ravel() + 0.5)], 1) + jitter
        self.q *= np.array([self.ex / nx, self.ey / ny], np.float32)
        self.lum = rng.lognormal(0, 0.9, len(self.q)).astype(np.float32)

    def positions(self, D, offset=(0.0, 0.0), wrap=True):
        x = self.q + D * self.psi
        x = x + np.asarray(offset, np.float32)
        if wrap:
            x[:, 0] %= self.ex
            x[:, 1] %= self.ey
        return x

    def density(self, D, w=240, h=135):
        """Smoothed density at low resolution — where the nodes are."""
        import cv2
        x = self.positions(D)
        xi = np.clip((x[:, 0] / self.ex * w).astype(int), 0, w - 1)
        yi = np.clip((x[:, 1] / self.ey * h).astype(int), 0, h - 1)
        rho = np.bincount(yi * w + xi, minlength=w * h).reshape(h, w).astype(np.float32)
        return cv2.GaussianBlur(rho, (0, 0), 1.5)
