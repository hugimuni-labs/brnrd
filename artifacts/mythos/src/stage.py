"""Shared stage pieces for the mythos acts: the dust volume, the rock, the finishing pass,
and the edit-list helpers."""
import functools, math
import numpy as np
import cv2
import forms
from core import *
from core import _grid, _grain_bank
from optics import *

def tag(fn, snd):
    fn.snd = snd; return fn

# ---------- the particulate volume ----------
@functools.lru_cache(None)
def _dust(seed=4, n=16000):
    rs = np.random.RandomState(seed)
    return dict(x=rs.uniform(-300, 2220, n), y=rs.uniform(-200, 1280, n), z=rs.uniform(0.4, 3.2, n) ** 1.4,
                vx=rs.normal(7, 5, n), vy=rs.normal(-2, 4, n), b=rs.uniform(0.2, 1, n) ** 2, ph=rs.rand(n) * 6.28)

def splat(buf, xs, ys, val):
    xi = np.round(xs * S).astype(int); yi = np.round(ys * S).astype(int)
    ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
    np.add.at(buf, (yi[ok], xi[ok]), val[ok])

def dust_volume(F, light=(1460, -80), k=1.0, ray=1.0, cloud_seed=11, zoomk=1.0, cx=W0 / 2, cy=H0 / 2):
    """Metallic ash drifting at several depths, raked by a hot source above frame."""
    d = _dust(); t = F / FPS
    x = (d['x'] + d['vx'] * t / d['z'] - cx) * zoomk + cx; y = (d['y'] + d['vy'] * t / d['z'] - cy) * zoomk + cy
    flick = 0.7 + 0.3 * np.sin(d['ph'] + t * 5.0)
    far = np.zeros((H, W), np.float32); near = np.zeros((H, W), np.float32); fm = d['z'] > 1.3
    splat(far, x[fm], y[fm], d['b'][fm] * flick[fm]); splat(near, x[~fm], y[~fm], d['b'][~fm] * flick[~fm] * 1.6)
    dust = blur(far, 1.4) * 6.0 + blur(near, 0.8) * 3.0
    yy, xx = _grid()
    r = np.sqrt((xx - light[0] * S) ** 2 + (yy - light[1] * S) ** 2) / (W * 0.33)
    cloud = np.roll(fbm(cloud_seed, 5, 4), int(t * 16 * S), 1)
    src = np.exp(-r * r * 1.1) * (0.12 + 1.3 * np.clip(cloud * 2.2 - 0.8, 0, 1)) + dust * 0.1
    rays = godrays(src.astype(np.float32), *light, n=28, length=0.97) * ray
    img = tint(rays * 1.5, AMBER * 0.85)
    img += tint(np.clip(dust, 0, 2), EMBER * 0.8) * (0.25 + rays[..., None] * 2.4) * k
    return img, rays

# ---------- dark matter ----------
@functools.lru_cache(None)
def rock_height(seed=21):
    return fbm(seed, 7, 4) * 0.55 + fbm(seed + 1, 4, 2) * 0.3 + np.abs(fbm(seed + 2, 6, 10) - 0.5) * 0.3

def poly_screen(name, cx, cy, scale, rot=0.0, jag=0.0, seed=1):
    """Nodal contours of a form, placed on screen; optional fractal jaggedness (fracture)."""
    out = []
    for i, c in enumerate(forms.contours(name)):
        p = forms.to_screen(c, cx, cy, scale, rot)
        if jag > 0:
            rs = np.random.RandomState(seed + i)
            n = len(p); u = np.arange(n)
            off = np.zeros(n, np.float32)
            for o in range(5):
                step_ = max(2, n // (4 * 2 ** o)); pts = rs.normal(0, jag / (1.7 ** o), n // step_ + 2)
                off += np.interp(u, np.arange(len(pts)) * step_, pts)
            tang = np.gradient(p, axis=0); tang /= np.linalg.norm(tang, axis=1, keepdims=True) + 1e-6
            p = p + np.c_[-tang[:, 1], tang[:, 0]] * off[:, None]
        out.append(p)
    return out

def stroke(polys, width, upto=None):
    """Polylines -> mask; `upto` = list of fractions drawn per polyline."""
    segs = []
    for i, p in enumerate(polys):
        f = 1.0 if upto is None else upto[i]
        if f <= 0: continue
        segs.append(p[:max(2, int(len(p) * min(1, f)))])
    return lines_mask(segs, width) if segs else np.zeros((H, W), np.float32)

# ---------- finish ----------
def develop(img, F, exposure=1.0, halation=0.35, ca=0.6, fg=None, fg_col=EMBER, grain_k=0.035, vig=0.45):
    """The finishing pass every photographic shot goes through. Returns linear-ish 0..1."""
    if fg is not None: img = img + tint(fg, fg_col)
    img = filmic(img, exposure, halation)
    img = chroma(img, ca)
    return img

def flat_finish(img):
    """Mark a frame as already finished (print inserts skip the photographic develop)."""
    return img
