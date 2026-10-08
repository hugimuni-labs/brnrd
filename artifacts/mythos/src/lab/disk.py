"""Rotation and collapse: a belt of matter around a dark centre.

A Keplerian disk — angular velocity Ω ∝ r^(−3/2), so the belt shears and
never rotates as a rigid object. Its surface density carries three kinds of
structure, all physical:

* **density waves** — an m=2 spiral pattern moving at its own pattern speed;
* **a gap** — cleared by a small embedded body orbiting inside it. The body's
  gravity raises *edge waves* on the gap's rims, trailing behind it in
  azimuth (Daphnis in Saturn's Keeler gap does exactly this);
* **turbulent emissivity** — fbm in (r, φ) sheared by the same rotation.

The far half of the disk is seen through the centre's gravity. Each particle
behind the lens plane is moved by the point-lens equation

    θ± = ½ (β ± √(β² + 4θ_E²))

so the far disk rises over the shadow as an arc and a thin inverted image
hugs the Einstein ring. Beaming brightens the approaching side. It is a cheat
(thin lens, no ray tracing) but every term in it is real.

Operators this phenomenon gives away for free: **loop** (the ring), **cavity**
(the shadow, the gap), **arc** (any crop of the ring), **line** (the jet).
"""
from __future__ import annotations

import math

import numpy as np


class Disk:
    def __init__(self, n=260_000, seed=0, r_in=1.0, r_out=4.2, gap=(2.55, 0.09),
                 thickness=0.012, jet_n=12_000):
        rng = np.random.default_rng(seed)
        self.rng = rng
        # Surface density ∝ r^(−0.6) with ring structure; sample by rejection.
        r = rng.uniform(r_in, r_out, n * 3)
        rings = 1 + 0.55 * np.sin(r * 9.3 + 1.1) * np.sin(r * 2.1) + 0.35 * np.sin(r * 31.0)
        dens = r ** -0.6 * np.clip(rings, 0.05, None)
        g0, gw = gap
        dens *= 1 - 0.985 * np.exp(-((r - g0) / gw) ** 8)
        keep = rng.random(len(r)) < dens / dens.max()
        r = r[keep][:n]
        self.r = r.astype(np.float32)
        self.phi0 = rng.uniform(0, 2 * np.pi, len(r)).astype(np.float32)
        self.z = rng.normal(0, thickness, len(r)).astype(np.float32) * r
        self.lum = rng.lognormal(0, 0.7, len(r)).astype(np.float32)
        self.inverted = rng.random(len(r)) < 0.35  # which also show the inner image
        self.gap = gap
        self.r_in, self.r_out = r_in, r_out
        # Jet: particles leaving along the axis, both poles.
        self.jet_s = rng.uniform(0, 1, jet_n).astype(np.float32)
        self.jet_sign = np.where(rng.random(jet_n) < 0.5, -1.0, 1.0).astype(np.float32)
        self.jet_off = rng.normal(0, 1, (jet_n, 2)).astype(np.float32)
        self.jet_lum = rng.lognormal(-0.5, 0.8, jet_n).astype(np.float32)

    def omega(self, r):
        return r ** -1.5

    def state(self, t):
        """Disk-frame positions at time t (orbital units: Ω(1) = 1)."""
        r = self.r
        phi = self.phi0 + self.omega(r) * t
        # Edge waves: the gap body sits at azimuth Ω(g0)·t. Particles just
        # outside the gap lag it (they orbit slower), particles inside lead it;
        # both get a radial wiggle that decays with azimuthal distance.
        g0, gw = self.gap
        phib = self.omega(g0) * t
        dphi = np.mod(phi - phib + np.pi, 2 * np.pi) - np.pi
        edge = np.exp(-((r - g0) / (3.5 * gw)) ** 2)
        side = np.sign(r - g0)
        trail = np.where(side * dphi < 0, np.exp(-np.abs(dphi) / 1.6), 0.0)
        r_eff = r + 0.06 * edge * trail * np.sin(np.abs(dphi) * 14.0) * side
        # m=2 spiral density wave in brightness.
        spiral = 1 + 0.45 * np.cos(2 * (phi - 0.35 * t) + 7.0 * np.log(r))
        return r_eff, phi, spiral.astype(np.float32), phib

    def project(self, t, *, incl=1.32, roll=-0.22, scale=330.0, center=(960, 540),
                theta_e=0.62, doppler=0.55, lens=True):
        """Screen positions, brightness and depth of every particle.

        ``incl`` is the tilt from face-on (π/2 = edge-on). ``theta_e`` is the
        Einstein radius in disk units."""
        r, phi, spiral, phib = self.state(t)
        x = r * np.cos(phi)
        y = r * np.sin(phi)
        z = self.z
        ci, si = math.cos(incl), math.sin(incl)
        Y = y * ci - z * si
        depth = y * si + z * ci  # >0: behind the centre
        X = x
        lum = self.lum * spiral * (r ** -0.8)
        # Temperature: inner disk hotter.
        temp = np.clip((self.r_out - r) / (self.r_out - self.r_in), 0, 1)
        # Relativistic-ish beaming: line-of-sight velocity ∝ −Ω r sin φ cos... keep sign real.
        vlos = -self.omega(r) * r * np.cos(phi) * si
        lum = lum * (1 + doppler * vlos) ** 3
        bx, by = X.copy(), Y.copy()
        mag = np.ones_like(lum)
        behind_all = depth > 0
        if lens:
            behind = depth > 0
            b = np.hypot(X, Y) + 1e-6
            u = b / theta_e
            root = np.sqrt(b * b + 4 * theta_e ** 2)
            tp = 0.5 * (b + root)
            k = np.where(behind, tp / b, 1.0)
            bx, by = X * k, Y * k
            mp = (u * u + 2) / (2 * u * np.sqrt(u * u + 4)) + 0.5
            mag = np.where(behind, np.clip(mp, 1, 6), 1.0).astype(np.float32)
            # The inverted image inside the ring, for the behind half only.
            tm = 0.5 * (b - root)
            km = tm / b
            sel = behind & self.inverted  # fixed per particle: frames render in any order
            ix, iy = X[sel] * km[sel], Y[sel] * km[sel]
            mm = np.clip((u[sel] ** 2 + 2) / (2 * u[sel] * np.sqrt(u[sel] ** 2 + 4)) - 0.5, 0, 3)
            bx = np.concatenate([bx, ix])
            by = np.concatenate([by, iy])
            lum = np.concatenate([lum * mag, lum[sel] * mm * 0.6])
            temp = np.concatenate([temp, temp[sel]])
            depth = np.concatenate([depth, depth[sel]])
            behind_all = np.concatenate([behind_all, np.ones(int(sel.sum()), bool)])
        cr, sr = math.cos(roll), math.sin(roll)
        sx = center[0] + scale * (bx * cr - by * sr)
        sy = center[1] + scale * (bx * sr + by * cr)
        # The shadow: anything behind that lands inside it is swallowed.
        rr = np.hypot(bx, by)
        alive = ~((depth > 0) & (rr < 0.92 * theta_e) & (rr > 0.0))
        # The front disk occludes the shadow, the shadow occludes nothing in front.
        return (np.stack([sx, sy], 1)[alive].astype(np.float32), lum[alive].astype(np.float32),
                temp[alive].astype(np.float32), behind_all[alive])

    def jet(self, t, *, incl=1.32, roll=-0.22, scale=330.0, center=(960, 540), speed=0.9, length=9.0):
        s = (self.jet_s + speed * t / length) % 1.0
        along = s * length * self.jet_sign
        spread = 0.03 + 0.06 * s
        off = self.jet_off * spread[:, None]
        # Axis is the disk normal; project: normal (0,0,1) → screen (0, −sin i) after tilt.
        si = math.sin(incl)
        ci = math.cos(incl)
        X = off[:, 0]
        Y = -along * si + off[:, 1] * ci
        cr, sr = math.cos(roll), math.sin(roll)
        sx = center[0] + scale * (X * cr - Y * sr)
        sy = center[1] + scale * (X * sr + Y * cr)
        lum = self.jet_lum * np.exp(-s * 2.5) * (0.5 + 0.5 * np.sin(s * 40 - t * 3) ** 2)
        return np.stack([sx, sy], 1).astype(np.float32), lum.astype(np.float32)
