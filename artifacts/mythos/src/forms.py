"""The shape grammar, derived from physics rather than typed.

Every recurring form in the mythos is a nodal set of a vibrating circular membrane:
a superposition of drum modes J_n(k r) cos(n(θ - φ)). Where the membrane does not move,
grains settle — and the same contours are reused as the crack's path, the erosion bias,
the die, the transistor channels. One cause, many media.

Coordinates: membrane units, the unit disk; +y is down (screen convention).
"""
import functools, math
import numpy as np
import cv2
from scipy.special import jv, jn_zeros

# basis: (n, k-th zero, phase φ)
BASIS = [(1, 1, 0.0), (1, 2, 0.0), (2, 1, 0.0), (2, 1, 0.7), (0, 3, 0.0), (3, 1, 0.0), (0, 2, 0.0), (2, 1, math.pi / 4)]
# the families (weights over BASIS). Names are what a human later reads into them.
FORMS = {
    'noise':    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    'ring':     [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
    'fork':     [1.0, 0.0, 0.0, 0.6, 0.0, 0.0, 0.0, 0.0],   # stem + hook: r · ᚠ · Y
    'bowl':     [0.0, 1.0, 0.0, 0.6, 0.0, 0.0, 0.0, 0.0],   # stem into a closed bowl: b · d · p · q
    'mirror':   [0.0, 0.0, 0.0, 0.0, 0.6, 0.0, 0.0, -1.0],  # facing lobes across an axis: b|d
    'channels': [0.0, 0.0, 1.0, 0.0, 0.3, 0.0, 0.0, 0.0],   # n over u: n · u · ω · ᚢ
    'cross':    [1.0, 0.0, 0.0, 0.0, 0.0, 0.6, 0.0, 0.0],
}

def mode(n, k, ph, x, y):
    r = np.hypot(x, y); th = np.arctan2(y, x)
    return jv(n, jn_zeros(n, k)[-1] * r) * np.cos(n * (th - ph))

def freq(n, k):
    """Relative eigenfrequency of a mode (∝ the Bessel zero) — what the tone sounds."""
    return float(jn_zeros(n, k)[-1])

@functools.lru_cache(None)
def basis_grid(N=512):
    """Each basis mode and its gradient sampled on an NxN grid over [-1, 1]²."""
    y, x = np.mgrid[-1:1:N * 1j, -1:1:N * 1j]
    Z = []
    for (n, k, ph) in BASIS:
        z = mode(n, k, ph, x, y); z /= np.abs(z[np.hypot(x, y) < 1]).max(); Z.append(z.astype(np.float32))
    Z = np.stack(Z)
    gy, gx = np.gradient(Z, 2 / (N - 1), axis=(1, 2))
    return Z, gx.astype(np.float32), gy.astype(np.float32)

def weights(name_or_w):
    return np.asarray(FORMS[name_or_w] if isinstance(name_or_w, str) else name_or_w, np.float32)

def field(w, N=512):
    Z, _, _ = basis_grid(N); return np.tensordot(weights(w), Z, 1)

def blend(a, b, t):
    return weights(a) * (1 - t) + weights(b) * t

@functools.lru_cache(None)
def contours(name, N=768, rmax=0.97, minlen=0.25):
    """Nodal lines of a named form as open/closed polylines in membrane units (marching squares)."""
    from skimage import measure
    z = field(name, N); out = []
    for c in measure.find_contours(z, 0.0):
        p = (c[:, ::-1] / (N - 1) * 2 - 1).astype(np.float32)
        inside = np.hypot(p[:, 0], p[:, 1]) < rmax
        # split where the curve leaves the disk; keep the runs inside
        idx = np.flatnonzero(np.diff(np.r_[0, inside.astype(np.int8), 0]))
        for a, b in zip(idx[::2], idx[1::2]):
            q = p[a:b]
            if len(q) > 2 and np.linalg.norm(np.diff(q, axis=0), axis=1).sum() >= minlen: out.append(q)
    return out

def closed(c):
    return np.linalg.norm(c[0] - c[-1]) < 0.02

def to_screen(pts, cx, cy, scale, rot=0.0):
    c, s = math.cos(rot), math.sin(rot)
    p = np.asarray(pts, np.float32)
    return np.c_[cx + scale * (p[:, 0] * c - p[:, 1] * s), cy + scale * (p[:, 0] * s + p[:, 1] * c)]
