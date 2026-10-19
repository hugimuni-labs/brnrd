"""Debris and belts: a ring system shaped by orbital resonance.

Particles orbit a planet on Keplerian circles (Ω ∝ r^(−3/2)). An outer moon
at r_m clears **gaps** wherever a ring particle's period is a simple fraction
of the moon's — the p:q mean-motion resonances at r = r_m (q/p)^(2/3). That is
the Cassini division and the Kirkwood gaps: integer ratios cutting circles
out of a disk. Ringlets and spokes are fbm in r and φ, sheared by the same
rotation.

The camera here is a real perspective camera in 3-D, because this shot's
job is a **scale revelation**: it starts *inside* the ring plane, where the
nearest particles are out-of-focus discs and the frame reads as dust in a
beam — the opening's own material — and pulls back until the dust is a ring
around a world, with the planet's shadow lying across it.

Operators: **loop**, **gap** (the `o`/`c` family), **arc**, repetition.
"""
from __future__ import annotations

import math

import numpy as np

from lab import fbm


class Rings:
    def __init__(self, n=900_000, seed=0, r0=1.35, r1=2.45, moon=3.3):
        rng = np.random.default_rng(seed)
        r = rng.uniform(r0, r1, n * 3)
        dens = 1 + 0.45 * np.sin(r * 61.0) * np.sin(r * 7.3) + 0.25 * np.sin(r * 173.0)
        self.gaps = []
        for p, q, w in ((2, 1, 0.045), (3, 2, 0.02), (3, 1, 0.025), (5, 3, 0.012), (4, 3, 0.01)):
            rg = moon * (q / p) ** (2 / 3)
            if r0 < rg < r1:
                self.gaps.append(rg)
                dens *= 1 - 0.97 * np.exp(-((r - rg) / w) ** 6)
        keep = rng.random(len(r)) < np.clip(dens, 0, None) / dens.max()
        r = r[keep][:n]
        self.r = r.astype(np.float32)
        self.phi0 = rng.uniform(0, 2 * np.pi, len(r)).astype(np.float32)
        self.z = rng.normal(0, 0.004, len(r)).astype(np.float32)
        self.albedo = rng.lognormal(0, 0.6, len(r)).astype(np.float32)
        self.size = rng.lognormal(0, 0.5, len(r)).astype(np.float32)
        self.sun = np.array([-0.35, 0.92, 0.22])  # from behind: a crescent, rings lit through
        self.sun /= np.linalg.norm(self.sun)

    def positions(self, t):
        phi = self.phi0 + self.r ** -1.5 * t
        return np.stack([self.r * np.cos(phi), self.r * np.sin(phi), self.z], 1)

    def lit(self, P):
        """Sunlight on each particle: zero inside the planet's shadow cylinder."""
        s = self.sun
        along = P @ s
        perp = P - along[:, None] * s[None, :]
        shadow = (along < 0) & (np.einsum("ij,ij->i", perp, perp) < 1.0)
        return np.where(shadow, 0.02, 1.0).astype(np.float32)


def look(cam, target, up=(0, 0, 1)):
    f = np.asarray(target, float) - np.asarray(cam, float)
    f /= np.linalg.norm(f)
    r = np.cross(f, up)
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return np.stack([r, u, f])


def project(P, cam, R, focal, w, h):
    X = (P - np.asarray(cam)) @ R.T
    d = X[:, 2]
    ok = d > 1e-3
    sx = w / 2 + focal * X[:, 0] / np.where(ok, d, 1)
    sy = h / 2 - focal * X[:, 1] / np.where(ok, d, 1)
    return np.stack([sx, sy], 1).astype(np.float32), d.astype(np.float32), ok


def planet(cam, R, focal, w, h, sun, ring_obj=None, seed=0):
    """Per-pixel ray–sphere: shading, depth and coverage of a banded planet,
    with the rings' shadow lying across it."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dirs = np.stack([(xx - w / 2) / focal, -(yy - h / 2) / focal, np.ones_like(xx)], -1)
    dirs /= np.linalg.norm(dirs, axis=-1, keepdims=True)
    D = dirs @ R  # camera→world
    C = np.asarray(cam, float)
    b = D @ C
    c = C @ C - 1.0
    disc = b * b - c
    hit = disc > 0
    t = -b - np.sqrt(np.clip(disc, 0, None))
    hit &= t > 0
    Pw = C + D * t[..., None]
    n = Pw
    lam = np.clip(n @ sun, 0, None)
    lat = np.arcsin(np.clip(n[..., 2], -1, 1))
    bands = 0.75 + 0.25 * np.sin(lat * 14 + 0.6 * np.sin(lat * 3)) \
        + 0.08 * np.asarray(fbm(256, 128, seed, 4, 4))[np.clip(((lat / np.pi + 0.5) * 127).astype(int), 0, 127),
                                                      np.clip(((np.arctan2(n[..., 1], n[..., 0]) / (2 * np.pi) + 0.5) * 255).astype(int), 0, 255)]
    # Ring shadow: follow the sun ray from the surface point to the ring plane.
    shade = np.ones_like(lam)
    if ring_obj is not None:
        tz = -Pw[..., 2] / (sun[2] + 1e-6)
        Q = Pw + sun * tz[..., None]
        rq = np.hypot(Q[..., 0], Q[..., 1])
        inring = (tz > 0) & (rq > 1.35) & (rq < 2.45)
        for g in ring_obj.gaps:
            inring &= np.abs(rq - g) > 0.03
        shade = np.where(inring, 0.25, 1.0)
    I = np.where(hit, lam * bands * shade, 0.0).astype(np.float32)
    depth = np.where(hit, t, np.inf).astype(np.float32)
    limb = np.where(hit, (1 - np.abs(D * n).sum(-1)) ** 3, 0).astype(np.float32)
    return I, depth, hit, limb
