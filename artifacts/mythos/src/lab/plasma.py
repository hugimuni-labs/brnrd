"""Solar plasma topology: loops under magnetic tension, and reconnection.

The photosphere is the plane z = 0. Below it sit magnetic sources — point
charges of flux (monopoles) at depth, in opposite-signed pairs. Above it the
field is the potential field of those sources:

    B(x) = Σ qᵢ (x − xᵢ) / |x − xᵢ|³

Hot plasma is frozen to the field, so it lights up *field lines*: start a line
at a positive footpoint, integrate along B until it returns to the surface,
and you have a coronal loop — an **arch**. Bend the sources and the arches
lean into **hooks**.

Flux emergence drives the drama. A new bipole rises under an existing
arcade; field lines that used to connect A→B now find C nearer, and the
connectivity of the corona changes. Where two lines swap partners there is an
X-point — **reconnection**, the universe's first **fork** in a field — and
the swapped lines flash, because that is where the stored energy is released.
The flash is not authored: the generator compares each line's endpoint
between frames and lights the ones whose topology changed.
"""
from __future__ import annotations

import numpy as np


class Corona:
    def __init__(self, seed=0, n_lines=420, depth=0.22, region=(-2.4, 2.4, -1.0, 1.0)):
        rng = np.random.default_rng(seed)
        self.rng = rng
        self.depth = depth
        x0, x1, y0, y1 = region
        # An active region: two main polarities, plus a scatter of small flux.
        src = [(-0.75, 0.05, 1.0), (0.80, -0.08, -1.0), (-1.45, 0.35, 0.45), (1.6, -0.3, -0.5)]
        for _ in range(10):
            src.append((rng.uniform(x0, x1), rng.uniform(y0, y1), rng.choice([-1, 1]) * rng.uniform(0.06, 0.2)))
        self.base = np.array(src, np.float64)
        # Footpoints: sampled near positive flux, weighted by flux.
        pos = self.base[self.base[:, 2] > 0]
        w = pos[:, 2] / pos[:, 2].sum()
        pick = rng.choice(len(pos), n_lines, p=w)
        r = rng.gamma(2.0, 0.09, n_lines) * np.sqrt(pos[pick, 2])
        a = rng.uniform(0, 2 * np.pi, n_lines)
        self.feet = np.stack([pos[pick, 0] + r * np.cos(a), pos[pick, 1] + r * np.sin(a)], 1)
        self.heat = rng.lognormal(0, 0.7, n_lines)          # each loop's own heating
        self.phase = rng.uniform(0, 2 * np.pi, n_lines)
        self.last_end = None
        self.flash = np.zeros(n_lines)

    def sources(self, t, emerge=None, shear=0.0):
        """Sources at time t. ``emerge``: (x, y, flux, separation, progress∈[0,1])
        — a new bipole rising and separating. ``shear``: footpoint drift."""
        S = self.base.copy()
        S[:, 0] += shear * np.sign(S[:, 1] - 0.0) * 0.15 * t
        S[:, 0] += 0.02 * np.sin(0.4 * t + np.arange(len(S)))
        S[:, 1] += 0.02 * np.cos(0.33 * t + np.arange(len(S)) * 1.7)
        if emerge is not None:
            ex, ey, q, sep, prog = emerge
            prog = float(np.clip(prog, 0, 1))
            if prog > 0:
                d = sep * prog
                S = np.vstack([S, [ex - d / 2, ey + 0.05, q * prog], [ex + d / 2, ey - 0.05, -q * prog]])
        return S

    def _B(self, P, S):
        B = np.zeros_like(P)
        for x, y, q in S:
            d = P - np.array([x, y, -self.depth])
            r3 = (np.einsum("ij,ij->i", d, d) + 1e-4) ** 1.5
            B += q * d / r3[:, None]
        return B

    def trace(self, S, step=0.012, n=520, z0=0.004):
        """Trace every footpoint's line through the field (vectorised RK2).
        Returns positions (n_lines, n, 3) and a mask of steps above the surface."""
        L = len(self.feet)
        P = np.column_stack([self.feet, np.full(L, z0)])
        out = np.zeros((L, n, 3))
        alive = np.ones(L, bool)
        mask = np.zeros((L, n), bool)
        for i in range(n):
            out[:, i] = P
            mask[:, i] = alive
            B = self._B(P, S)
            u = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-12)
            Pm = P + 0.5 * step * u
            B2 = self._B(Pm, S)
            u2 = B2 / (np.linalg.norm(B2, axis=1, keepdims=True) + 1e-12)
            P = np.where(alive[:, None], P + step * u2, P)
            alive &= (P[:, 2] > 0) & (np.abs(P[:, 0]) < 6) & (np.abs(P[:, 1]) < 4) & (P[:, 2] < 3.5)
            if not alive.any():
                out[:, i + 1:] = P[:, None, :]
                break
        return out, mask

    def step_topology(self, lines, mask):
        """Where each line lands; lines whose landing site jumped have reconnected."""
        idx = mask.sum(1) - 1
        end = lines[np.arange(len(lines)), np.clip(idx, 0, lines.shape[1] - 1), :2]
        closed = lines[np.arange(len(lines)), np.clip(idx, 0, lines.shape[1] - 1), 2] < 0.05
        if self.last_end is not None:
            jump = np.linalg.norm(end - self.last_end, axis=1)
            self.flash = np.maximum(self.flash * 0.72, np.clip((jump - 0.25) * 3, 0, 4))
        self.last_end = end
        return closed

    @staticmethod
    def xpoints(lines, mask, flash, thresh=0.5):
        """Apex of each flashing line — where the swap happened, near enough."""
        pts = []
        for i in np.flatnonzero(flash > thresh):
            seg = lines[i][mask[i]]
            if len(seg) > 4:
                pts.append(seg[np.argmax(seg[:, 2])])
        return np.array(pts) if pts else np.zeros((0, 3))


def camera(P, *, yaw=0.0, pitch=0.35, scale=420.0, center=(960, 860), dist=7.0):
    """Perspective projection of corona coords (x right, y back, z up).
    ``pitch`` tilts the camera down onto the surface; small pitch = limb view."""
    x, y, z = P[..., 0], P[..., 1], P[..., 2]
    cy_, sy_ = np.cos(yaw), np.sin(yaw)
    x, y = x * cy_ - y * sy_, x * sy_ + y * cy_
    cp, sp = np.cos(pitch), np.sin(pitch)
    yc = y * cp + z * sp           # depth
    zc = -y * sp + z * cp          # up on screen
    f = dist / (dist + yc)
    sx = center[0] + scale * x * f
    sy = center[1] - scale * zc * f
    return np.stack([sx, sy], -1), yc
