"""Living membrane: grains on a vibrating drum, simulated, not drawn.

Each step, a grain is kicked in proportion to the local vibration amplitude |z| and drifts
down the gradient of z² — so grains leave the antinodes and settle where the membrane is
still. When the drive changes mode, the old pattern is kicked apart and a new one forms.
The schedule is a list of (frame, form-or-weights, amplitude); weights interpolate.
"""
import os, functools, hashlib, json
import numpy as np
import cv2
import forms
from core import *
from core import _grid

CACHE = os.path.join(os.path.dirname(__file__), '..', 'out', 'cache')

def _interp(schedule, f):
    for (f0, w0, a0), (f1, w1, a1) in zip(schedule, schedule[1:]):
        if f0 <= f <= f1:
            t = (f - f0) / max(1, f1 - f0); t = t * t * (3 - 2 * t)
            return forms.blend(w0, w1, t), a0 + (a1 - a0) * t
    _, w, a = schedule[-1]; return forms.weights(w), a

_yy, _xx = np.mgrid[-1:1:512j, -1:1:512j]; _DISK = np.hypot(_xx, _yy) < 0.98

def simulate(schedule, nframes, n=160000, steps=4, seed=3, init='uniform'):
    key = hashlib.md5(json.dumps([schedule, nframes, n, steps, seed, init, 8]).encode()).hexdigest()[:12]
    path = os.path.join(CACHE, f'membrane-{key}.npy')
    if os.path.exists(path): return np.load(path, mmap_mode='r')
    Z, GX, GY = forms.basis_grid(512); N = 512
    rs = np.random.RandomState(seed)
    r = np.sqrt(rs.rand(n)) * 0.99; th = rs.rand(n) * 6.2832
    p = np.c_[r * np.cos(th), r * np.sin(th)].astype(np.float32)
    out = np.empty((nframes, n, 2), np.float16)
    for f in range(nframes):
        w, A = _interp(schedule, f)
        z = np.tensordot(w, Z, 1); gx = np.tensordot(w, GX, 1); gy = np.tensordot(w, GY, 1)
        nz = max(1e-3, float(np.abs(z[_DISK]).max())); z = z / nz; gx = gx / nz; gy = gy / nz
        for s in range(steps):
            ix = np.clip(((p[:, 0] + 1) * 0.5 * (N - 1)).astype(np.int32), 0, N - 1)
            iy = np.clip(((p[:, 1] + 1) * 0.5 * (N - 1)).astype(np.int32), 0, N - 1)
            zz = z[iy, ix]
            rr0 = np.hypot(p[:, 0], p[:, 1])
            ax = abs(zz) + A * 0.7 * np.clip((rr0 - 0.86) / 0.12, 0, 1)   # the clamp rings: grains get thrown back in
            g2 = gx[iy, ix] ** 2 + gy[iy, ix] ** 2
            # drift down z² (Newton-ish step toward the nodal line, capped) + amplitude kicks
            k = np.clip(zz / (g2 + 4.0), -0.05, 0.05) * A * 0.16
            p[:, 0] -= k * gx[iy, ix]
            p[:, 1] -= k * gy[iy, ix]
            p += rs.randn(n, 2).astype(np.float32) * (A * 0.016 * np.sqrt(ax) + 0.0011)[:, None]
            p -= p * (A * 0.05 * np.clip((rr0 - 0.84) / 0.14, 0, 1))[:, None]          # and drift inward off the clamp
            rr = np.hypot(p[:, 0], p[:, 1]); o = rr > 0.995
            p[o] *= ((0.985 - 0.06 * rs.rand(int(o.sum()))) / rr[o])[:, None]  # bounce off the clamp
        out[f] = p
    os.makedirs(CACHE, exist_ok=True); tmp = path + f'.{os.getpid()}.npy'
    np.save(tmp, out); os.replace(tmp, path)
    return np.load(path, mmap_mode='r')

def density(pos, cx, cy, scale, rot=0.0):
    sp = forms.to_screen(np.asarray(pos, np.float32), cx, cy, scale, rot)
    d = np.zeros((H, W), np.float32)
    xi = np.round(sp[:, 0] * S).astype(int); yi = np.round(sp[:, 1] * S).astype(int)
    ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
    np.add.at(d, (yi[ok], xi[ok]), 1.0)
    return d, sp

def vib_field(w, cx, cy, scale, rot=0.0):
    """The membrane's mode shape resampled into screen space (for the surface shimmer)."""
    z = forms.field(w, 512)
    yy, xx = _grid()
    c, s = np.cos(-rot), np.sin(-rot)
    u = ((xx / S - cx) * c - (yy / S - cy) * s) / scale; v = ((xx / S - cx) * s + (yy / S - cy) * c) / scale
    mx = ((u + 1) * 0.5 * 511).astype(np.float32); my = ((v + 1) * 0.5 * 511).astype(np.float32)
    zz = cv2.remap(z.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return zz, np.hypot(u, v)

def render(pos, w, A, F, cx=960, cy=560, scale=720, rot=0.0, light=(0.75, -0.55, 0.38),
           grain_col=hexc('b08a62'), skin=hexc('0d0b0a'), glint=1.0, rim=True):
    """Grains as a lit powder heightfield on a dark taut skin."""
    d, sp = density(pos, cx, cy, scale, rot)
    k = (S * scale / 720) ** 2  # keep powder coverage scale-independent
    hgt = blur(d, 1.5) / (2.6 * k + 1e-6)
    hgt = np.clip(hgt, 0, 2.2)
    dif, spc = shade(hgt * 0.9, light, 5.0, spec=24, spec_k=0.9)
    cover = np.clip(hgt * 1.3, 0, 1)
    zz, rr = vib_field(w, cx, cy, scale, rot)
    inside = (rr < 1.0).astype(np.float32)
    # the skin: dark, its displacement visible as a faint aliased shimmer while driven
    ph = math.sin(F * 2.39)
    sk_d, sk_s = shade(zz * ph * A * 0.6 + fbm(81, 4, 6) * 0.04, light, 22, spec=30, spec_k=0.5)
    img = tint(inside * (0.35 + 0.65 * sk_d), skin) + tint(inside * sk_s * 0.35, hexc('ffd6a8'))
    over(img, tint(dif * 1.5 + 0.12, grain_col) + tint(spc, hexc('fff0dc')) * 0.9, cover)
    if glint > 0:  # a few grains catch the light this frame: metallic
        idx = np.arange(0, len(sp), 37)
        g = np.zeros((H, W), np.float32)
        on = np.array([hsh(i * 0.37 + F * 1.7) > 0.93 for i in range(0, len(idx))])
        q = sp[idx[on]]
        xi = np.round(q[:, 0] * S).astype(int); yi = np.round(q[:, 1] * S).astype(int)
        ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H); g[yi[ok], xi[ok]] = 1
        img += tint(blur(g, 1.2) * 9 * glint, hexc('fff2de'))
    if rim:  # the clamped edge, bending under tension
        ring = np.exp(-((rr - 1.0) / (0.006 * 720 / scale)) ** 2) * (1 + 0.4 * ph * A)
        img += tint(ring * 0.18, hexc('7a5a3c'))
        img *= (1 - 0.92 * np.clip((rr - 1.0) * 20, 0, 1))[..., None]
    return img
