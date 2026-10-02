"""Act I — the universe discovers wiring · rune-hunting · industrial incantation (0–18 s).

The one subject is the route ⌁: energy finding a path. It is the first structure matter
makes, the groove the rune-hunters find, the channel the press stamps, and (much later)
the prefix of the resident's face ⌁[b·_·d].
"""
import functools, math
import numpy as np
import cv2
from core import *
from core import _grain_bank, _grid

# The route: a horizontal lightning-arrow, the ⌁ drawn as a path through matter.
ROUTE = [(150, 600), (830, 566), (1000, 410), (968, 664), (1130, 530), (1770, 488)]

def route_pts(n=600):
    p = np.asarray(ROUTE, np.float32); seg = np.linalg.norm(np.diff(p, axis=0), axis=1); cum = np.r_[0, np.cumsum(seg)]
    u = np.linspace(0, cum[-1], n); out = np.empty((n, 2), np.float32)
    for k in range(2): out[:, k] = np.interp(u, cum, p[:, k])
    return out, u / cum[-1]

def partial(pts, frac):
    n = max(2, int(len(pts) * clamp(frac))); return pts[:n]

# ---------------------------------------------------------------- particles
@functools.lru_cache(None)
def _dust():
    rs = np.random.RandomState(4); N = 14000
    x = rs.uniform(-200, 2120, N); y = rs.uniform(-150, 1230, N); z = rs.uniform(0.35, 3.2, N) ** 1.3
    vx = rs.normal(9, 6, N); vy = rs.normal(-3, 4, N); b = rs.uniform(0.25, 1, N) ** 2
    rp, ru = route_pts(2400); k = rs.randint(0, len(rp), 3200)
    home = rp[k] + rs.normal(0, 1, (len(k), 2)) * np.c_[rs.uniform(2, 26, len(k))]
    start = np.c_[rs.uniform(-100, 2020, len(k)), rs.uniform(-80, 1160, len(k))]
    delay = ru[k] * 0.5 + rs.uniform(0, 0.35, len(k))
    return dict(x=x, y=y, z=z, vx=vx, vy=vy, b=b, home=home, start=start, delay=delay, sb=rs.uniform(0.4, 1, len(k)))

def splat(buf, xs, ys, val):
    xi = np.round(xs * S).astype(int); yi = np.round(ys * S).astype(int)
    ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
    np.add.at(buf, (yi[ok], xi[ok]), val[ok])

LIGHT = (1380, -60)

def matter(t, gather, frame, seam=0.0, zoomk=1.0):
    """Rest state A: charged dust, warm god rays, matter accreting into the route."""
    d = _dust(); img = canvas(hexc('070605'))
    far = np.zeros((H, W), np.float32); near = np.zeros((H, W), np.float32)
    x = (d['x'] + d['vx'] * t / d['z']) ; y = (d['y'] + d['vy'] * t / d['z'])
    x = (x - W0 / 2) * zoomk + W0 / 2; y = (y - H0 / 2) * zoomk + H0 / 2
    flick = 0.75 + 0.25 * np.sin(d['x'] * 0.37 + t * 6.0 + d['y'])
    fm = d['z'] > 1.2
    splat(far, x[fm], y[fm], d['b'][fm] * flick[fm] * 0.9)
    splat(near, x[~fm], y[~fm], d['b'][~fm] * flick[~fm] * 1.6)
    # seam particles: drawn to the route
    a = np.clip((gather - d['delay']) / 0.45, 0, 1); a = a * a * (3 - 2 * a)
    p = d['start'] * (1 - a[:, None]) + d['home'] * a[:, None]
    p[:, 0] += np.sin(t * 2 + d['delay'] * 30) * 3 * (1 - a)
    p = (p - [W0 / 2, H0 / 2]) * zoomk + [W0 / 2, H0 / 2]
    splat(near, p[:, 0], p[:, 1], d['sb'] * (0.6 + 1.4 * a))
    far = blur(far, 1.6) * 7.0; near = blur(near, 0.7) * 3.4
    dust = far + near
    # light: a hot source above frame, broken by cloud, raked through the dust
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    lx, ly = LIGHT[0] * S, LIGHT[1] * S
    r = np.sqrt((xx - lx) ** 2 + (yy - ly) ** 2) / (W * 0.33)
    cloud = fbm(11, 5, 4); cloud = np.roll(cloud, int(t * 18 * S), 1)
    src = np.exp(-r * r * 1.1) * (0.15 + 1.4 * np.clip(cloud * 2.2 - 0.8, 0, 1)) + dust * 0.12
    rays = godrays(src.astype(np.float32), *LIGHT, n=32, length=0.97)
    img += tint(rays * 1.6, AMBER * 0.9)
    img += tint(np.clip(dust, 0, 2), EMBER * 0.85) * (0.45 + rays[..., None] * 2.2)
    if seam > 0:
        rp, _ = route_pts(900)
        rp2 = (rp - [W0 / 2, H0 / 2]) * zoomk + [W0 / 2, H0 / 2]
        m = lines_mask([partial(rp2, seam)], 2.2)
        img += tint(blur(m, 9) * 1.6 + blur(m, 2.5) * 1.2 + m * 0.8, COPPER) + tint(m, EMBER) * 0.6
    return img

# ---------------------------------------------------------------- inserts
def orbit_diagram(frame, inv=False):
    """2-frame mathematical insert: orbits, tangents, a stem — the b hidden in geometry."""
    img = canvas(PAPER)
    cx, cy = 820, 560
    m = np.zeros((H, W), np.float32)
    for r in (90, 160, 250, 380):
        m = np.maximum(m, circle_mask(cx, cy, r, 1.2))
    e = np.zeros((H, W), np.uint8)
    cv2.ellipse(e, P(cx, cy), (int(470 * S), int(150 * S)), -18, 0, 360, 255, max(1, int(1.4 * S)), cv2.LINE_AA)
    m = np.maximum(m, e / 255)
    over(img, INK, m * 0.85)
    red = lines_mask([[(cx - 160, 140), (cx - 160, cy + 2)], [(cx - 600, cy + 300), (cx + 700, cy - 260)], [(cx, cy), (cx + 250 * 0.707, cy - 250 * 0.707)]], 2)
    red = np.maximum(red, circle_mask(cx, cy, 160, 3))
    over(img, RED, red)
    over(img, INK, circle_mask(cx, cy, 6))
    text(img, 'fig. 1 — a bowl and a stem', 'mono', 18, 1260, 880, INK, wght=500)
    text(img, 'r = 160', 'mono', 16, cx + 120, cy - 150, RED)
    text(img, '0.0049', 'mono', 16, 120, 980, INK)
    return invert(img) if inv else img

def lattice(frame, k=1.0):
    img = canvas(hexc('07040f')); m = np.zeros((H, W), np.float32)
    a = 46 * k; pts = []
    for j in range(-2, int(H0 / (a * 0.866)) + 3):
        for i in range(-2, int(W0 / a) + 3):
            pts.append((i * a + (j % 2) * a / 2 + 13, j * a * 0.866 + 7))
    segs = []
    for (x, y) in pts:
        segs.append([(x, y), (x + a, y)]); segs.append([(x, y), (x + a / 2, y + a * 0.866)])
    m = lines_mask(segs, 1.0)
    img += tint(m, UV) * 0.7
    dots = np.zeros((H, W), np.float32); splat(dots, np.array([p[0] for p in pts]), np.array([p[1] for p in pts]), np.ones(len(pts)))
    img += tint(blur(dots, 2.5) * 30, CYAN * 0.6) + tint(dots, WHITE)
    text(img, '×10⁹', 'mono', 22, 80, 80, OFFWHITE)
    return img

def interference(frame, t=0):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32) / S
    r1 = np.hypot(xx - 700, yy - 540); r2 = np.hypot(xx - 1220, yy - 540)
    v = np.sin(r1 * 0.11 - t * 9) + np.sin(r2 * 0.11 - t * 9)
    m = (v > 0.6).astype(np.float32)
    img = canvas(BLACK); over(img, OFFWHITE, m); return img

def crack_close(frame, k=1.0, t=0.0):
    """Scale inversion: the route seen from inside — a molten crack in dark rock."""
    hgt = fbm(21, 7, 5) * 0.6 + fbm(22, 4, 2) * 0.4
    pts, _ = route_pts(300); c = np.array([1000, 410])
    pts = (pts - c) * (6.0 * k) + [W0 / 2, H0 / 2]
    jag = pts + np.random.RandomState(5).normal(0, 4, pts.shape) * k
    m = lines_mask([jag], 10 * k)
    hgt = hgt - blur(m, 14) * 0.9
    dif, spc = shade(hgt, (-0.3, -0.75, 0.55), 9)
    img = tint(dif * 0.18, hexc('6b5a4a')) + tint(spc, AMBER) * 0.4
    core = blur(m, 3)
    img += tint(blur(m, 30) * 1.5, FURNACE) + tint(core * 1.4, EMBER) + tint(blur(m, 8), AMBER)
    return img

@functools.lru_cache(None)
def _web():
    rs = np.random.RandomState(12); n = 260
    p = np.c_[rs.uniform(-400, 2320, n), rs.uniform(-300, 1380, n)]
    p[0] = (1000, 410)
    d = np.linalg.norm(p[:, None] - p[None], axis=2); np.fill_diagonal(d, 1e9)
    edges = set()
    for i in range(n):
        for j in np.argsort(d[i])[:3]: edges.add((min(i, j), max(i, j)))
    return p, sorted(edges)

def web(frame, k, ann=True, t=0.0):
    """Zoom-out: the route is one filament of a cosmic web of routes."""
    p, edges = _web(); img = canvas(hexc('060504'))
    q = (p - p[0]) * k + [W0 / 2 + 60, H0 / 2 - 40]
    rs = np.random.RandomState(3)
    segs = []
    for (i, j) in edges:
        a, b = q[i], q[j]; mid = (a + b) / 2 + rs.normal(0, 18, 2) * k
        segs.append([tuple(a), tuple(mid), tuple(b)])
    m = lines_mask(segs, 1.3)
    img += tint(blur(m, 6) * 2.2, COPPER) + tint(m, EMBER) * 0.9
    nodes = np.zeros((H, W), np.float32); splat(nodes, q[:, 0], q[:, 1], np.ones(len(q)) * 4)
    img += tint(blur(nodes, 3) * 10, AMBER) + tint(blur(nodes, 1), WHITE)
    if ann:
        for i in (0, 17, 44, 91, 130):
            x, y = q[i]
            if 40 < x < 1800 and 40 < y < 1040:
                text(img, f'ROUTE {i:04d}', 'mono', 14, x + 12, y - 18, OFFWHITE, a=0.8)
                over(img, RED, lines_mask([[(x - 14, y - 14), (x - 14, y + 14)], [(x + 14, y - 14), (x + 14, y + 14)]], 1.5))
    return img

def glyph_card(ch, fnt, size, bg=PAPER, fg=INK, sub=None, wght=None):
    img = canvas(bg); text(img, ch, fnt, size, W0 / 2, H0 / 2, fg, anchor='mm', wght=wght)
    if sub: text(img, sub, 'mono', 18, 80, 1000, fg, wght=500)
    return img

# ---------------------------------------------------------------- rune-hunting
@functools.lru_cache(None)
def _warp_field(seed):
    a = (fbm(seed, 4, 3) - 0.5) * 2; b = (fbm(seed + 1, 4, 3) - 0.5) * 2; return a, b

def warped(mask, amt, seed=31):
    if amt <= 0.01: return mask
    a, b = _warp_field(seed); yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    return cv2.remap(mask, xx + a * amt * S, yy + b * amt * S, cv2.INTER_LINEAR)

def glyph_mask(ch, fnt='runic', size=620, x=W0 / 2, y=H0 / 2, wght=None, flip=False):
    m = text_mask(ch, fnt, size, x, y, anchor='mm', wght=wght)
    return m[:, ::-1].copy() if flip else m

def stone(groove, light=(-0.8, -0.35, 0.45), seed=41, depth=1.0, glow=0.0, warmth=1.0):
    """Rock lit raking; the groove is cut into it. Glow fills the cut like molten copper."""
    h = fbm(seed, 7, 4) * 0.55 + fbm(seed + 3, 3, 1) * 0.45
    g = blur(groove, 5)
    hgt = h - g * 0.55 * depth
    dif, spc = shade(hgt, light, 10)
    img = tint(dif ** 1.2 * 0.8, hexc('8a7866') * warmth + hexc('5a5a64') * (1 - warmth)) + tint(spc, EMBER) * 0.18
    img *= (0.35 + 0.65 * fbm(seed + 9, 3, 2))[..., None]
    if glow > 0:
        img += tint(blur(groove, 18) * glow * 0.9, FURNACE) + tint(groove * glow * 0.9, EMBER)
    return img

SPECIMENS = [  # natural form, the letter it rhymes with, its medium on tape
    ('ᚠ', 'runic', 'branching crack  ~  ᚠ  ~  r', 'stone'),
    ('ᚱ', 'runic', 'split channel  ~  r', 'stencil'),
    ('ᚢ', 'runic', 'riverbed prongs  ~  ᚢ  ~  n', 'thermal'),
    ('n', 'serif', 'vessel  ~  n  ~  u', 'negative'),
    ('p', 'serif', 'bowl below  ~  p', 'halftone'),
    ('b', 'serif', 'orbit + stem  ~  b', 'stone'),
]

def specimen(i, frame, tl):
    ch, fnt, label, med = SPECIMENS[i]
    m = glyph_mask(ch, fnt, 700 if fnt == 'runic' else 860)
    m = warped(m, 30 * (1 - tl) + 4, seed=31 + i)
    if med == 'stone':
        img = stone(m, seed=41 + i, glow=0.35 * tl)
    elif med == 'stencil':
        img = canvas(VIOLET); over(img, OFFWHITE, (blur(m, 1.2) > 0.5).astype(np.float32))
    elif med == 'thermal':
        base = stone(m, seed=44); img = dither(base * 2.2, ink=hexc('1a1a1a'), paper=hexc('e4e0d6'))
    elif med == 'negative':
        img = invert(stone(m, seed=45, glow=0.6))
    else:
        img = halftone(stone(m, seed=46, glow=0.4) * 2.0, 11)
    dark = med in ('stone', 'stencil', 'negative')
    col = OFFWHITE if dark else INK
    text(img, f'FOUND {i + 1:02d}', 'mono', 18, 80, 70, col, wght=600)
    text(img, label, 'mono', 18, 80, 1000, col)
    over(img, RED, lines_mask([[(660, 200), (620, 200), (620, 880), (660, 880)], [(1260, 200), (1300, 200), (1300, 880), (1260, 880)]], 3))
    return img

def mirror(t, frame):
    """Rest: a b-shaped cavity in the rock and, across a fold, its d. The face, not yet."""
    bm = glyph_mask('b', 'serif', 820, x=W0 / 2 - 250)
    dm = bm[:, ::-1].copy()
    app = clamp(prog(t, 0.6, 1.6))  # threshold apparition of the reflection
    flick = 1.0 if app >= 1 else (1.0 if hsh(frame * 3.1) < app else 0.0)
    g = np.maximum(warped(bm, 6), warped(dm, 6, 33) * flick)
    lx = lerp(-0.85, -0.55, t / 2.0)
    img = stone(g, light=(lx, -0.4, 0.42), seed=52, glow=0.3 + 0.12 * math.sin(t * 3))
    axis = lines_mask([[(W0 / 2, 60), (W0 / 2, 1020)]], 1.2)
    over(img, RED, axis * ss(0.2, 0.5, t))
    if t > 0.45:
        text(img, 'b', 'mono', 18, W0 / 2 - 40, 1010, OFFWHITE, anchor='ra')
        text(img, 'MIRROR', 'mono', 14, W0 / 2 + 10, 74, RED, wght=600)
    if app >= 1:
        text(img, 'd', 'mono', 18, W0 / 2 + 40, 1010, OFFWHITE)
    return img

def catalog(frame, slip=0):
    img = canvas(PAPER)
    cells = [('ᚠ', 'runic'), ('r', 'serif'), ('ᚢ', 'runic'), ('n', 'serif'), ('u', 'serif'), ('b', 'serif'), ('d', 'serif'),
             ('p', 'serif'), ('ᛒ', 'runic'), ('⌁', 'sym'), ('ᚱ', 'runic'), ('∩', 'math'), ('◡', 'sym'), ('ᛞ', 'runic'), ('·', 'serif')]
    for k, (ch, f) in enumerate(cells):
        cx = 240 + (k % 5) * 360; cy = 230 + (k // 5) * 300
        over(img, INK, lines_mask([[(cx - 150, cy - 125), (cx + 150, cy - 125), (cx + 150, cy + 125), (cx - 150, cy + 125)]], 1, closed=True) * 0.5)
        text(img, ch, f, 190, cx + (slip if k % 2 else -slip), cy - 10, RED if ch in 'bd⌁' else INK, anchor='mm')
        text(img, f'{k + 1:03d}', 'mono', 13, cx - 140, cy + 100, INK)
    text(img, 'PLATE II — FORMS THE WORLD KEEPS MAKING', 'mono', 20, 90, 40, INK, wght=600)
    return halftone(img, 4, ink=INK, paper=PAPER) * 0.5 + img * 0.5

def bell(frame):
    img = canvas(hexc('f0ebe0')); xs = np.linspace(80, 1840, 400)
    for gx in range(80, 1841, 80): over(img, CYAN, lines_mask([[(gx, 80), (gx, 1000)]], 1) * 0.5)
    for gy in range(80, 1001, 80): over(img, CYAN, lines_mask([[(80, gy), (1840, gy)]], 1) * 0.5)
    ys = 900 - 700 * np.exp(-((xs - 960) / 260) ** 2)
    over(img, INK, lines_mask([list(zip(xs, ys))], 4))
    ys2 = 540 + 160 * np.sin((xs - 80) / 90)
    over(img, RED, lines_mask([list(zip(xs, ys2))], 2))
    text(img, 'σ', 'math', 60, 1260, 300, INK)
    return img

# ---------------------------------------------------------------- industrial incantation
def rune_transistor():
    """The b-rune pressed as a channel, crossed by a gate: rune, transistor, building, symbol."""
    b = glyph_mask('b', 'sans', 760, x=W0 / 2, y=H0 / 2 + 10, wght=300)
    gate = rect_mask(W0 / 2 - 330, H0 / 2 - 6, W0 / 2 + 330, H0 / 2 + 14)
    pads = np.maximum(rect_mask(W0 / 2 - 360, H0 / 2 - 40, W0 / 2 - 320, H0 / 2 + 48), rect_mask(W0 / 2 + 320, H0 / 2 - 40, W0 / 2 + 360, H0 / 2 + 48))
    return np.clip(b + gate + pads, 0, 1)

@functools.lru_cache(None)
def _brushed(seed):
    rs = np.random.RandomState(seed)
    n = rs.rand(H // 2 + 1, 24).astype(np.float32)
    n = cv2.resize(n, (W, H), interpolation=cv2.INTER_CUBIC)
    return (n - n.min()) / (n.max() - n.min() + 1e-6) * 0.5 + fbm(seed, 5, 6) * 0.5

def plate(t, frame, fill=0.0, light=(-0.5, -0.6, 0.6), emb=1.0, warm=0.0, mark=None):
    m = rune_transistor() if mark is None else mark
    edge = blur(m, 2.5)
    hgt = _brushed(61) * 0.12 + edge * 0.6 * emb - blur(m, 9) * 0.25 * emb
    dif, spc = shade(hgt, light, 7, spec=60, spec_k=1.0)
    img = tint(dif * 0.16 + 0.02, hexc('8c96a8')) + tint(spc, hexc('ffe2c0')) * 0.55
    if fill > 0:
        yy, xx = _grid_cache()
        front = (xx / W) < fill + (fbm(62, 3, 6) - 0.5) * 0.08
        fm = m * front
        img += tint(blur(fm, 16) * 1.2, FURNACE) + tint(fm * 0.9, COPPER) + tint(blur(fm, 3) * 0.6, EMBER)
    if warm > 0:
        img += tint(np.clip(dif - 0.6, 0, 1) * warm, AMBER)
    return img

@functools.lru_cache(None)
def _grid_cache():
    return np.mgrid[0:H, 0:W].astype(np.float32)

def press(t, frame):
    """The die descends in three stepped poses; impact."""
    img = plate(t, frame, emb=0.0)
    y = lerp(-720, -40, step(t, 3))
    slab = rect_mask(260, -800, 1660, y + 700)
    dif = canvas(hexc('1c1c20')); dif *= (0.6 + 0.8 * fbm(63, 4, 5))[..., None]; text(dif, 'DIE 0049', 'mono', 22, 300, y + 640, hexc('6a6a6a'), wght=600)
    over(img, dif, slab)
    over(img, AMBER, rect_mask(260, y + 694, 1660, y + 702)); img += tint(blur(rect_mask(260, y + 694, 1660, y + 702), 20) * 2, FURNACE)
    return img

def rhinestones(frame):
    m = rune_transistor(); img = canvas(hexc('050507'))
    rs = np.random.RandomState(7); pts = []
    for y in range(80, 1000, 22):
        for x in range(200, 1720, 22):
            if m[int(y * S), int(x * S)] > 0.5: pts.append((x + (y // 22 % 2) * 11, y))
    pts = np.array(pts, np.float32); tw = rs.rand(len(pts))
    dots = np.zeros((H, W), np.float32); splat(dots, pts[:, 0], pts[:, 1], 0.4 + tw * (0.6 + 0.6 * math.sin(frame)))
    star = blur(dots, 1.2) * 6
    k = int(9 * S) | 1
    cross = cv2.filter2D(dots, -1, np.eye(k, dtype=np.float32) * 0 + np.pad(np.ones((1, k), np.float32), ((k // 2, k // 2), (0, 0))) / 2) + \
        cv2.filter2D(dots, -1, np.pad(np.ones((k, 1), np.float32), ((0, 0), (k // 2, k // 2))) / 2)
    img += tint(star + cross * 1.5, hexc('fff4e6')) + tint(blur(dots, 6) * 8, AMBER)
    text(img, 'RHINESTONES AND INCANTATIONS', 'mono', 16, 80, 1010, OFFWHITE, wght=500)
    return img

def schematic(frame):
    img = canvas(RED); m = rune_transistor()
    e = np.clip(blur(m, 1.5) - blur(m, 0.5) * 0.0, 0, 1)
    out = (cv2.Canny((m * 255).astype(np.uint8), 60, 120) / 255).astype(np.float32)
    over(img, OFFWHITE, blur(out, 0.6) * 1.5)
    for (s, x, y) in (('G', 560, 520), ('S', 760, 1010), ('D', 760, 70)):
        text(img, s, 'anton', 64, x, y, OFFWHITE, anchor='mm')
    text(img, 'GATE ACROSS THE STEM', 'mono', 18, 1240, 1000, OFFWHITE, wght=600)
    return img

def thermal_strip(frame, lines, t=0.0, bg=hexc('0a0a0a')):
    """Heat-sealed milky polymer with crude thermal print."""
    img = canvas(bg)
    x0, x1, y0, y1 = 380, 1540, 240, 840
    m = rect_mask(x0, y0, x1, y1)
    yy, xx = _grid_cache()
    sheen = np.exp(-((yy / S - (y0 + 120 + 80 * math.sin(t * 2))) / 70) ** 2) * 0.25 + fbm(71, 4, 6) * 0.08
    film = hexc('dcd8cf') * 0.86
    over(img, film, m * 0.92)
    img += tint(sheen * m, WHITE)
    crimp = np.zeros((H, W), np.float32)
    for x in range(x0, x1, 14):
        crimp = np.maximum(crimp, lines_mask([[(x, y0 + 4), (x + 7, y0 + 18), (x + 14, y0 + 4)], [(x, y1 - 4), (x + 7, y1 - 18), (x + 14, y1 - 4)]], 1.4))
    over(img, hexc('b9b4a8'), crimp)
    pm = np.zeros((H, W), np.float32)
    for k, (s, sz) in enumerate(lines):
        pm = np.maximum(pm, text_mask(s, 'mono', sz, x0 + 60, y0 + 70 + sum(l[1] * 1.25 for l in lines[:k]), wght=600))
    pm = pm * (0.75 + 0.25 * fbm(72, 3, 8)) * (_grain_bank()[frame % 6] * 0.2 + 0.9 > 0.55)
    over(img, hexc('15130f'), np.clip(pm, 0, 1))
    return img

def planet_plate(t, frame):
    """Rest B: a machined plate at planetary scale, one embossed form, furnace light crossing."""
    lx = lerp(-0.95, 0.75, t / 2.4)
    tex = plate(t, frame, fill=1.0, light=(lx, -0.25, 0.32), emb=1.0, warm=1.2)
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[W * 0.30, H * 0.22], [W * 0.70, H * 0.22], [W * 1.55, H * 1.02], [-W * 0.55, H * 1.02]])
    M = cv2.getPerspectiveTransform(src, dst)
    img = cv2.warpPerspective(tex, M, (W, H), flags=cv2.INTER_LINEAR)
    # horizon haze + furnace glow behind the rim
    yy, xx = _grid_cache()
    hz = np.exp(-((yy / H - 0.22) / 0.05) ** 2) * np.exp(-((xx / W - (0.5 + lx * 0.3)) / 0.35) ** 2)
    img += tint(hz * 0.9, FURNACE) + tint(hz ** 3 * 0.8, EMBER)
    text(img, 'PLATE 0049', 'mono', 16, 80, 1010, hexc('8a8a8a'), wght=500)
    text(img, f'{1977 + int(t * 10) % 49:04d}', 'mono', 16, 1840, 1010, hexc('8a8a8a'), anchor='ra')
    return img

# ---------------------------------------------------------------- the edit list (frames @30)
def tag(fn, snd):
    fn.snd = snd; return fn

def build():
    S_ = []
    A = S_.append
    # --- I. wiring ---
    A((10, tag(lambda f, n, F: canvas(BLACK), 'silence')))
    def ember(f, n, F):
        img = canvas(BLACK); m = circle_mask(1000, 410, 2 + f * 0.15); return img + tint(blur(m, 4) * 3, EMBER)
    A((8, ember))
    def rest_a(f, n, F):
        t = (F) / FPS; return matter(t, 0.12 + 0.55 * f / n, F, seam=0.0)
    A((26, rest_a))
    A((2, lambda f, n, F: orbit_diagram(F)))
    def rest_a2(f, n, F):
        t = F / FPS; return matter(t, 0.45 + 0.4 * f / n, F, seam=ss(0.3, 1.0, f / n) * 0.7)
    A((30, rest_a2))
    A((1, lambda f, n, F: lattice(F)))
    def rest_a3(f, n, F):
        t = F / FPS; return matter(t, 0.85 + 0.15 * f / n, F, seam=0.7 + 0.3 * f / n)
    A((22, rest_a3))
    # burst: violent zoom into the seam
    A((2, lambda f, n, F: zoom(matter(F / FPS, 1.0, F, seam=1.0), 3.0, 1000, 410)))
    A((2, lambda f, n, F: crack_close(F, 0.5)))
    A((3, lambda f, n, F: crack_close(F, 1.2)))
    A((2, lambda f, n, F: interference(F, F / FPS)))
    A((2, lambda f, n, F: invert(crack_close(F, 2.4))))
    A((1, lambda f, n, F: canvas(BLACK)))
    # zoom-out: one route among thousands (stepped, posed on 2s)
    def web_out(f, n, F):
        k = math.exp(lerp(math.log(5.0), math.log(0.55), step(f / n, n // 2)))
        img = web(F, k, ann=f > 6)
        if 26 <= f < 29: img = misregister(img, 10)
        return img
    A((52, tag(web_out, 'hiss')))
    A((1, lambda f, n, F: glyph_card('⌁', 'sym', 760)))
    A((2, lambda f, n, F: invert(web(F, 0.55, ann=False))))
    A((2, lambda f, n, F: canvas(BLACK)))
    # --- II. rune-hunting ---
    def hunt(f, n, F):
        tl = f / n
        m = glyph_mask('ᚠ', 'runic', 720); m = warped(m, lerp(60, 6, eout(tl)))
        img = stone(m, light=(-0.85, -0.3 + 0.2 * tl, 0.42), seed=41, glow=0.25 * tl)
        if tl > 0.35:
            over(img, RED, lines_mask([[(700, 190), (660, 190), (660, 890), (700, 890)], [(1220, 190), (1260, 190), (1260, 890), (1220, 890)]], 3) * (1 if step(tl, 6) >= 0.5 else 0.0))
            text(img, 'ᚠ ~ r', 'mono', 18, 1290, 880, OFFWHITE, a=float(tl > 0.55))
        if 34 <= f < 37:
            over(img, OFFWHITE, glyph_mask('r', 'serif', 900, x=W0 / 2 + 40) * 0.85)
        return img
    A((54, hunt))
    for i in range(len(SPECIMENS)):
        A((6, (lambda i: lambda f, n, F: specimen(i, F, f / n))(i)))
    A((2, lambda f, n, F: bell(F)))
    A((54, tag(lambda f, n, F: mirror(f / FPS, F), 'tone:55')))
    A((10, lambda f, n, F: catalog(F)))
    A((3, lambda f, n, F: misregister(catalog(F, slip=14), 12)))
    A((4, lambda f, n, F: catalog(F)))
    A((1, lambda f, n, F: canvas(BLACK)))
    # --- III. industrial incantation ---
    A((6, lambda f, n, F: press(f / n * 1.0, F)))
    A((1, tag(lambda f, n, F: canvas(WHITE), 'impact')))
    def struck(f, n, F):
        img = plate(F / FPS, F, fill=prog(f, 6, n) * 1.05, emb=1.0)
        dx = (8 * (1 - f / 6) * (1 if f % 2 else -1)) if f < 6 else 0
        img = shake(img, dx, -dx * 0.6)
        if 16 <= f < 19: img = canvas(VIOLET); text(img, 'INCANTATION', 'anton', 300, W0 / 2, H0 / 2, OFFWHITE, anchor='mm')
        if 30 <= f < 32: img = rhinestones(F)
        if 42 <= f < 45: img = schematic(F)
        return img
    A((56, struck))
    A((4, lambda f, n, F: thermal_strip(F, [('PATTERN 0049', 72), ('STATE: CAST', 72), ('HEAT 214°', 40)], f / FPS)))
    def diecut(f, n, F):
        img = canvas(RED); over(img, BLACK, rune_transistor()); text(img, 'DIE-CUT', 'mono', 18, 80, 1000, BLACK, wght=600); return img
    A((3, diecut))
    def mass(f, n, F):
        img = canvas(hexc('060606'))
        k = step(f / n, 3)
        for r in range(3):
            for c in range(6):
                small = (r * 6 + c) <= int(k * 18) + 6
                x = 160 + c * 290; y = 160 + r * 330
                if small:
                    over(img, hexc('17171b'), rect_mask(x, y, x + 250, y + 250))
                    sm = cv2.resize(rune_transistor(), (int(250 * S), int(250 * S)), interpolation=cv2.INTER_AREA)
                    sub = img[int(y * S):int(y * S) + sm.shape[0], int(x * S):int(x * S) + sm.shape[1]]
                    sub += sm[:sub.shape[0], :sub.shape[1], None] * COPPER * 0.9
                    text(img, f'{r * 6 + c + 1:04d}', 'mono', 13, x, y + 268, hexc('8a8a8a'))
        return img
    A((9, mass))
    A((1, lambda f, n, F: canvas(BLACK)))
    A((72, tag(lambda f, n, F: planet_plate(f / FPS, F), 'tone:36.7')))
    return S_
