"""The instrument: how the camera witnesses the phenomenon.

Exposure that clips, halation around highlights, a tilted macro focal plane, mount
jitter, out-of-focus dust between lens and subject. CG light clarifies form; this
light is allowed to destroy information.
"""
import functools, math
import numpy as np
import cv2
from core import *
from core import _grid

def jitter(F, amp=1.0, seed=0):
    """Smooth hashed mount vibration in 1920 px."""
    x = sum(math.sin(F * f + hsh(seed + k) * 6.28) / (k + 1) for k, f in enumerate((0.31, 0.77, 1.9, 4.3)))
    y = sum(math.sin(F * f + hsh(seed + k + 9) * 6.28) / (k + 1) for k, f in enumerate((0.27, 0.83, 2.1, 3.7)))
    return x * amp * 0.5, y * amp * 0.5

def filmic(img, exposure=1.0, halation=0.35, knee=0.8):
    """Exposure, red-orange halation around clipped light, a soft shoulder that still clips."""
    x = img * exposure
    hi = np.clip(lum(x) - knee, 0, None)
    hal = blur(hi, 10) * 0.7 + blur(hi, 36) * 0.5
    x = x + hal[..., None] * np.array([1.0, 0.42, 0.16], np.float32) * halation
    return 1 - np.exp(-x * 1.15)

def tilt_dof(img, focus_y, depth=360, max_sigma=14, levels=(0, 3.5, 8, 14)):
    """Macro on an oblique surface: sharp along one band, falling off above and below."""
    yy, _ = _grid()
    d = np.clip(np.abs(yy / S - focus_y) / depth, 0, 1) * max_sigma
    out = img.copy(); prev = img
    for a, b in zip(levels[:-1], levels[1:]):
        nxt = blur(img, b)
        w = np.clip((d - a) / (b - a), 0, 1)[..., None]
        sel = (d > a)[..., None]
        out = np.where(sel, prev * (1 - w) + nxt * w, out); prev = nxt
    return out

def defocus(img, sigma):
    return blur(img, sigma) if sigma > 0.3 else img

@functools.lru_cache(None)
def _motes(seed, n):
    rs = np.random.RandomState(seed)
    return rs.uniform(-100, 2020, n), rs.uniform(-100, 1180, n), rs.uniform(3, 20, n), rs.uniform(0.2, 1, n), rs.normal(0, 1, (n, 2))

def foreground_dust(F, n=60, light=(1500, -100), k=1.0, seed=5, drift=(6, -2)):
    """Bokeh motes between lens and subject: soft discs, lit by the source they drift through."""
    x, y, r, b, v = _motes(seed, n)
    t = F / FPS
    px = (x + (drift[0] + v[:, 0] * 8) * t) % 2120 - 100
    py = (y + (drift[1] + v[:, 1] * 5) * t) % 1280 - 100
    m = np.zeros((H, W), np.float32)
    for i in range(n):
        lit = math.exp(-((px[i] - light[0]) ** 2 + (py[i] - light[1]) ** 2) / (900 ** 2))
        cv2.circle(m, P(px[i], py[i]), max(1, int(r[i] * S)), float(b[i] * (0.15 + lit)), -1, cv2.LINE_AA)
    return blur(m, 3) * k

def chroma(img, k=1.0):
    """Lateral chromatic aberration toward the edges (optical, not decorative)."""
    if k <= 0: return img
    out = img.copy()
    for c, s in ((0, 1 + 0.0016 * k), (2, 1 - 0.0016 * k)):
        M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s)
        out[..., c] = cv2.warpAffine(img[..., c], M, (W, H), borderMode=cv2.BORDER_REFLECT)
    return out

def move(img, dx, dy, k=1.0, rot=0.0, cx=W0 / 2, cy=H0 / 2):
    M = cv2.getRotationMatrix2D((cx * S, cy * S), rot, k); M[0, 2] += dx * S; M[1, 2] += dy * S
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
