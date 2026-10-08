"""A living membrane: a driven, damped wave field with grains on it.

The membrane is simulated, not composed from modes. The displacement u obeys

    ∂²u/∂t² = c² ∇²u − γ ∂u/∂t − κ(x) u + F sin(2π f t) δ(x − x_drive)

on a rectangle with three clamped edges and one free edge (the edge that
crosses the frame: it is allowed to whip). Sweep the drive frequency f and
the plate passes through its resonances; between them it holds compromise
patterns that no textbook plate shows. The nodal set — where the RMS
amplitude is near zero — is where the grains end up, because grains hop in
proportion to the local amplitude and walk down its gradient.

``κ(x)`` is the coupling to the past: anywhere the membrane carries a scar (a
burnt breakdown channel, passed in as a 0..1 map), it is stiffened and
damped. The scar pins a node. Grains therefore collect *along what the
lightning left*, braided into the cavities and lobes the free plate makes
around it. Nobody composes the combination; the boundary condition does.

Observation is a force here too: ``probe`` kicks grains under a scan line,
and ``stress`` is what a polariser would see.
"""
from __future__ import annotations

import numpy as np
import cv2


class Membrane:
    def __init__(self, nx=360, ny=200, seed=0, n_grains=200_000, damping=0.004,
                 drive=(0.62, 0.38), scar=None, pin=0.35, c=1.0):
        self.nx, self.ny = nx, ny
        self.rng = np.random.default_rng(seed)
        self.u = np.zeros((ny, nx), np.float32)
        self.v = np.zeros_like(self.u)
        self.rms2 = np.zeros_like(self.u)      # EMA of u²
        self.strain = np.zeros_like(self.u)    # EMA of |∇u|²
        self.t = 0.0
        self.dt = 0.5
        self.c2 = c * c
        self.gamma = damping
        self.drive = (int(drive[0] * nx), int(drive[1] * ny))
        self.kappa = np.zeros_like(self.u)
        if scar is not None:
            self.set_scar(scar, pin)
        self.p = self.rng.uniform([1, 1], [nx - 1, ny - 1], (n_grains, 2)).astype(np.float32)
        self.prev = self.p.copy()
        self.hop = np.zeros(n_grains, np.float32)  # current airborne height
        self.A = np.zeros_like(self.u)
        self.gx = np.zeros_like(self.u)
        self.gy = np.zeros_like(self.u)

    def set_scar(self, scar, pin=0.35):
        s = cv2.resize(scar.astype(np.float32), (self.nx, self.ny), interpolation=cv2.INTER_AREA)
        s = np.clip(s / (s.max() + 1e-6), 0, 1)
        self.scar = s
        self.kappa = (pin * s).astype(np.float32)

    def run(self, freq, steps=200, amp=1.0, ema=0.01):
        u, v = self.u, self.v
        dt = self.dt
        dx, dy = self.drive
        for _ in range(steps):
            lap = np.zeros_like(u)
            lap[1:-1, 1:-1] = (u[1:-1, 2:] + u[1:-1, :-2] + u[2:, 1:-1] + u[:-2, 1:-1] - 4 * u[1:-1, 1:-1])
            # Free top edge: mirror condition (∂u/∂y = 0).
            lap[0, 1:-1] = (u[0, 2:] + u[0, :-2] + 2 * u[1, 1:-1] - 4 * u[0, 1:-1])
            a = self.c2 * lap - self.gamma * v - self.kappa * u
            a[dy - 1:dy + 2, dx - 1:dx + 2] += amp * np.sin(2 * np.pi * freq * self.t)
            v += dt * a
            u += dt * v
            # Clamped edges.
            u[-1, :] = 0
            u[:, 0] = 0
            u[:, -1] = 0
            # Scar damping: energy that reaches a burnt channel is absorbed.
            v *= 1 - 0.25 * self.kappa
            self.rms2 += ema * (u * u - self.rms2)
            self.t += dt
        gy, gx = np.gradient(u)
        self.strain += 0.2 * (gx * gx + gy * gy - self.strain)
        self.u, self.v = u, v
        A = np.sqrt(self.rms2)
        self.A = A / (np.percentile(A, 98) + 1e-6)
        gy, gx = np.gradient(cv2.GaussianBlur(self.A, (0, 0), 1.0))
        self.gx, self.gy = gx.astype(np.float32), gy.astype(np.float32)

    def shake(self, amp=1.6, substeps=3, p=1.5, floor=0.015, drift=5.0, kick=None):
        """One frame of grain motion: hops ∝ amplitude, walk down its gradient.
        ``kick``: optional per-grain extra hop (the probe)."""
        self.prev = self.p.copy()
        A = self.A
        for _ in range(substeps):
            x = np.clip(self.p[:, 0].astype(np.int32), 0, self.nx - 1)
            y = np.clip(self.p[:, 1].astype(np.int32), 0, self.ny - 1)
            s = amp * (np.clip(A[y, x], 0, 3) ** p + floor)
            if kick is not None:
                s = s + kick
            self.p += self.rng.normal(0, 1, self.p.shape).astype(np.float32) * s[:, None]
            self.p[:, 0] -= drift * self.gx[y, x]
            self.p[:, 1] -= drift * self.gy[y, x]
            np.clip(self.p[:, 0], 0.5, self.nx - 1.5, out=self.p[:, 0])
            np.clip(self.p[:, 1], 0.5, self.ny - 1.5, out=self.p[:, 1])
        x = np.clip(self.p[:, 0].astype(np.int32), 0, self.nx - 1)
        y = np.clip(self.p[:, 1].astype(np.int32), 0, self.ny - 1)
        self.hop = (amp * np.clip(A[y, x], 0, 3) ** p * np.abs(self.rng.normal(0, 1, len(x)))).astype(np.float32)

    def probe(self, row, width=3.0, strength=2.0):
        """A scan line at plate row ``row``: grains under it get kicked."""
        d = np.abs(self.p[:, 1] - row)
        return (strength * np.exp(-(d / width) ** 2)).astype(np.float32)

    def stress(self):
        """Principal-stress proxy for the photoelastic view: RMS strain, normalised."""
        s = np.sqrt(self.strain)
        return s / (np.percentile(s, 99) + 1e-6)

    def quiet(self, thresh=0.12):
        """Where the plate is still: the nodal set as a 0..1 map."""
        return np.clip(1 - self.A / thresh, 0, 1)


def oblique(nx, ny, *, corners):
    """Homography from plate coords (nx×ny) to screen given the four screen
    corners [top-left, top-right, bottom-right, bottom-left] of the plate."""
    src = np.float32([[0, 0], [nx, 0], [nx, ny], [0, ny]])
    return cv2.getPerspectiveTransform(src, np.float32(corners))


def to_screen(M, pts):
    p = np.concatenate([pts, np.ones((len(pts), 1), np.float32)], 1) @ M.T
    return (p[:, :2] / p[:, 2:3]).astype(np.float32)
