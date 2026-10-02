"""Core of the mythos renderer: a float RGB frame buffer and the material toolkit.

Every shot is a pure function of (local time, global frame) -> HxWx3 float32 in 0..1.
Coordinates are authored in 1920x1080 units and scaled by S, so previews can render small.
"""
import os, math, functools
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

W0, H0 = 1920, 1080
S = float(os.environ.get('MYTH_SCALE', '1'))
W, H = int(W0 * S), int(H0 * S)
FPS = 30
FONT_DIR = os.environ.get('MYTH_FONTS', os.path.join(os.path.dirname(__file__), '..', 'fonts'))

# ---------- palette (display space) ----------
def hexc(h):
    h = h.lstrip('#'); return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)], np.float32)
BLACK = hexc('050505'); GRAPHITE = hexc('16161a'); PAPER = hexc('e9e4d8'); PAPER2 = hexc('d8d0bf')
AMBER = hexc('ff9a3c'); COPPER = hexc('c8673a'); FURNACE = hexc('ff5a1a'); EMBER = hexc('ffcf8a')
RED = hexc('e8242c'); VIOLET = hexc('6a3cff'); UV = hexc('8b5cff'); CYAN = hexc('4fe3ff'); WHITE = hexc('ffffff')
PHOSPHOR = hexc('ffb347'); OFFWHITE = hexc('f2eee6'); INK = hexc('111111')

# ---------- small math ----------
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def prog(t, a, b): return clamp((t - a) / (b - a)) if b != a else float(t >= b)
def ss(a, b, x): t = prog(x, a, b); return t * t * (3 - 2 * t)
def eout(t): t = clamp(t); return 1 - (1 - t) ** 3
def ein(t): t = clamp(t); return t ** 3
def lerp(a, b, t): return a + (b - a) * t
def step(t, n):
    """Pose-on-n quantisation of a 0..1 progress (stepped motion)."""
    return math.floor(clamp(t) * n) / n
def hsh(n):
    x = math.sin(n * 127.1 + 311.7) * 43758.5453; return x - math.floor(x)
def P(x, y): return int(round(x * S)), int(round(y * S))
def sc(v): return v * S

# ---------- buffers ----------
def canvas(col=BLACK):
    return np.zeros((H, W, 3), np.float32) + np.asarray(col, np.float32)
def fill(img, col): img[:] = col; return img
def over(img, layer, alpha):
    """alpha: HxW (0..1) or scalar; layer: HxWx3 or colour."""
    a = alpha[..., None] if isinstance(alpha, np.ndarray) and alpha.ndim == 2 else alpha
    img *= (1 - a); img += np.asarray(layer, np.float32) * a; return img
def add(img, layer, k=1.0): img += layer * k; return img
def screen(img, layer): img[:] = 1 - (1 - img) * (1 - np.clip(layer, 0, 1)); return img
def mul(img, layer): img *= layer; return img
def tint(mask, col): return mask[..., None] * np.asarray(col, np.float32)
def lum(img): return img @ np.array([0.299, 0.587, 0.114], np.float32)

def blur(a, sigma):
    if sigma <= 0.3: return a
    s = sigma * S
    if s > 6:  # blur at reduced resolution for speed
        f = max(1, int(s / 3)); h, w = a.shape[:2]
        small = cv2.resize(a, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), s / f)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(a, (0, 0), s)

def bloom(img, thresh=0.6, k=0.8, sigmas=(4, 16, 48)):
    hi = np.clip(img - thresh, 0, None)
    acc = np.zeros_like(img)
    for s in sigmas: acc += blur(hi, s)
    img += acc * (k / len(sigmas)); return img

def godrays(src, cx, cy, n=28, decay=0.93, length=0.55):
    """Radial blur of an occluder/emitter map toward (cx, cy) in 1920 units — volumetric light."""
    h, w = src.shape[:2]; f = 4
    small = cv2.resize(src, (w // f, h // f), interpolation=cv2.INTER_AREA)
    acc = np.zeros_like(small); wsum = 0; c = (cx * S / f, cy * S / f)
    for i in range(n):
        s = 1 - length * i / n
        M = np.float32([[s, 0, c[0] * (1 - s)], [0, s, c[1] * (1 - s)]])
        wgt = decay ** i
        acc += cv2.warpAffine(small, M, (w // f, h // f), flags=cv2.INTER_LINEAR) * wgt; wsum += wgt
    return cv2.resize(acc / wsum, (w, h), interpolation=cv2.INTER_LINEAR)

def vignette(img, k=0.55, p=2.2):
    yy, xx = _grid()
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / 1.414
    img *= (1 - k * r ** p)[..., None]; return img

@functools.lru_cache(None)
def _grid():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32); return yy, xx

@functools.lru_cache(None)
def _grain_bank():
    rs = np.random.RandomState(9)
    return [rs.randn(H, W).astype(np.float32) for _ in range(6)]
def grain(img, frame, k=0.035, mono=True):
    g = _grain_bank()[frame % 6]
    if mono: img += (g * k)[..., None]
    else: img += np.stack([g, np.roll(g, 7, 0), np.roll(g, 13, 1)], -1) * k
    return img

# ---------- noise ----------
@functools.lru_cache(None)
def fbm(seed, octaves=6, base=8, w=None, h=None, persist=0.55):
    """Seeded fractal value noise, HxW float32 roughly 0..1."""
    w = w or W; h = h or H
    rs = np.random.RandomState(seed); acc = np.zeros((h, w), np.float32); amp = 1; tot = 0
    for o in range(octaves):
        cells = base * 2 ** o
        gx, gy = cells + 1, max(2, int(cells * h / w) + 1)
        g = rs.rand(gy, gx).astype(np.float32)
        acc += cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC) * amp; tot += amp; amp *= persist
    acc /= tot; acc -= acc.min(); acc /= max(1e-6, acc.max()); return acc

def shade(height, light=(-0.6, -0.5, 0.62), strength=6.0, spec=40, spec_k=0.6):
    """Heightfield -> (diffuse, specular) under a directional light. The machined-matter workhorse."""
    gy, gx = np.gradient(height * strength * S)
    nz = 1 / np.sqrt(gx * gx + gy * gy + 1)
    nx, ny = -gx * nz, -gy * nz
    L = np.array(light, np.float32); L /= np.linalg.norm(L)
    d = np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0, 1)
    # Blinn half-vector with the viewer straight on
    Hv = L + np.array([0, 0, 1], np.float32); Hv /= np.linalg.norm(Hv)
    s = np.clip(nx * Hv[0] + ny * Hv[1] + nz * Hv[2], 0, 1) ** spec * spec_k
    return d.astype(np.float32), s.astype(np.float32)

# ---------- type ----------
FONTS = {
    'mono': 'GeistMono.ttf', 'anton': 'Anton-Regular.ttf', 'serif': 'InstrumentSerif-Regular.ttf',
    'serifi': 'InstrumentSerif-Italic.ttf', 'pix': 'Silkscreen-Regular.ttf', 'doto': 'Doto.ttf',
    'bodoni': 'BodoniModa.ttf', 'sans': 'InterTight.ttf', 'vt': 'VT323-Regular.ttf',
    'bar': 'LibreBarcode128-Regular.ttf', 'runic': 'NotoSansRunic-Regular.ttf',
    'frak': 'UnifrakturMaguntia-Book.ttf', 'sym': 'NotoSansSymbols.ttf', 'math': 'NotoSansMath-Regular.ttf',
}
@functools.lru_cache(None)
def font(name, size, wght=None):
    f = ImageFont.truetype(os.path.join(FONT_DIR, FONTS[name]), max(1, int(size * S)))
    if wght is not None:
        try: f.set_variation_by_axes([wght] if name != 'doto' else [0, wght])
        except Exception: pass
    return f

def text_mask(s, name, size, x, y, anchor='la', wght=None, spacing=0, rot=0, stroke=0):
    """Text as an HxW coverage mask, placed at (x, y) in 1920 units."""
    f = font(name, size, wght)
    if rot == 0 and spacing == 0:
        im = Image.new('L', (W, H), 0); d = ImageDraw.Draw(im)
        d.text(P(x, y), s, font=f, fill=255, anchor=anchor, stroke_width=int(stroke * S), stroke_fill=255)
        return np.asarray(im, np.float32) / 255
    # tracked and/or rotated: draw on a tile, then place
    pad = int(size * S * 2)
    widths = [f.getlength(ch) for ch in s]; tw = int(sum(widths) + spacing * S * max(0, len(s) - 1)) + pad
    tile = Image.new('L', (tw + pad, int(size * S * 1.6) + pad), 0); d = ImageDraw.Draw(tile); cx = pad // 2
    for ch, wd in zip(s, widths):
        d.text((cx, pad // 2), ch, font=f, fill=255, anchor='lt'); cx += wd + spacing * S
    tile = tile.crop(tile.getbbox() or (0, 0, 1, 1))
    if rot: tile = tile.rotate(rot, expand=True, resample=Image.BICUBIC)
    im = Image.new('L', (W, H), 0)
    tx, ty = P(x, y)
    if anchor[0] == 'm': tx -= tile.width // 2
    elif anchor[0] == 'r': tx -= tile.width
    if anchor[1] == 'm': ty -= tile.height // 2
    elif anchor[1] in 'bd': ty -= tile.height
    im.paste(tile, (tx, ty)); return np.asarray(im, np.float32) / 255

def text(img, s, name, size, x, y, col=WHITE, a=1.0, **kw):
    m = text_mask(s, name, size, x, y, **kw); return over(img, col, m * a)

def lines_mask(segs, width=2, closed=False, aa=True):
    """Polyline(s) in 1920 units -> mask. segs: list of point lists."""
    m = np.zeros((H, W), np.uint8)
    for pts in segs:
        p = (np.asarray(pts, np.float32) * S * 16).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(m, [p], closed, 255, max(1, int(round(width * S))), cv2.LINE_AA if aa else cv2.LINE_8, shift=4)
    return m.astype(np.float32) / 255

def circle_mask(cx, cy, r, width=-1):
    m = np.zeros((H, W), np.uint8)
    cv2.circle(m, (int(cx * S * 16), int(cy * S * 16)), int(r * S * 16), 255, -1 if width < 0 else max(1, int(width * S)), cv2.LINE_AA, shift=4)
    return m.astype(np.float32) / 255

def poly_mask(pts):
    m = np.zeros((H, W), np.uint8)
    p = (np.asarray(pts, np.float32) * S * 16).astype(np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(m, [p], 255, cv2.LINE_AA, shift=4); return m.astype(np.float32) / 255

def rect_mask(x0, y0, x1, y1):
    m = np.zeros((H, W), np.float32); a, b = P(x0, y0); c, d = P(x1, y1)
    m[max(0, b):max(0, d), max(0, a):max(0, c)] = 1; return m

# ---------- print / raster media switches ----------
def halftone(img, cell=10, ang=0.4, ink=INK, paper=PAPER):
    """Luminance -> AM halftone dots (the print medium)."""
    L = lum(img); c = max(3, int(cell * S)); yy, xx = _grid()
    u = (xx * math.cos(ang) + yy * math.sin(ang)) / c; v = (-xx * math.sin(ang) + yy * math.cos(ang)) / c
    d = np.sqrt((u - np.round(u)) ** 2 + (v - np.round(v)) ** 2)
    Ls = blur(L, cell * 0.35)
    dots = (d < (1 - Ls) * 0.62).astype(np.float32)
    out = canvas(paper); return over(out, ink, dots)

def threshold(img, t=0.5, ink=INK, paper=PAPER, noise=0.0, frame=0):
    L = lum(img)
    if noise: L = L + _grain_bank()[frame % 6] * noise
    m = (L > t).astype(np.float32); out = canvas(paper); return over(out, ink, m)

def crush(img, f=12):
    """Pixel crush: the raw low-fi raster frame."""
    s = max(1, int(f * S)); small = cv2.resize(img, (W // s, H // s), interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (W, H), interpolation=cv2.INTER_NEAREST)

def dither(img, ink=INK, paper=PAPER, cell=3):
    L = cv2.resize(lum(img), (W // max(1, int(cell * S)), H // max(1, int(cell * S))), interpolation=cv2.INTER_AREA)
    b = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32) / 16
    hh, ww = L.shape; th = np.tile(b, (hh // 4 + 1, ww // 4 + 1))[:hh, :ww]
    m = cv2.resize((L > th).astype(np.float32), (W, H), interpolation=cv2.INTER_NEAREST)
    out = canvas(ink); return over(out, paper, m)

def misregister(img, dx=8, dy=0, cols=(RED, VIOLET)):
    """Split a frame's luminance into two offset ink plates (registration slip)."""
    L = lum(img); a = np.roll(np.roll(L, int(dx * S), 1), int(dy * S), 0); b = np.roll(np.roll(L, -int(dx * S), 1), -int(dy * S), 0)
    out = np.zeros_like(img); out += a[..., None] * cols[0]; out += b[..., None] * cols[1]
    return np.clip(out + (L * 0.5)[..., None], 0, 1)

def invert(img): return 1 - np.clip(img, 0, 1)

def shake(img, dx, dy):
    M = np.float32([[1, 0, dx * S], [0, 1, dy * S]])
    return cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REFLECT)

def zoom(img, k, cx=W0 / 2, cy=H0 / 2, rot=0.0):
    M = cv2.getRotationMatrix2D((cx * S, cy * S), rot, k)
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)

def finish(img, frame, g=0.03, vig=0.4):
    vignette(img, vig); grain(img, frame, g); return np.clip(img, 0, 1)

def to8(img): return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
