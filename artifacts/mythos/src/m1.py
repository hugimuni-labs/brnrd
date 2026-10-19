"""Act 1 — matter. Before the symbol: radiation, fracture, orbit, vibration, scale collapse.

Nothing here is drawn as a letter. The fork the crack takes, the angle the orbit sheds
matter at, and the patterns the grains settle into are the same nodal sets (forms.py),
arrived at by different physics.
"""
import functools, math
import numpy as np
import cv2
from scipy.spatial import cKDTree
import forms, membrane
from core import *
from core import _grid
from optics import *
from stage import *

# ------------------------------------------------------------------ the crack's geometry
CR = dict(cx=900, cy=560, scale=620)

@functools.lru_cache(None)
def crack_paths():
    """The fork form rotated so its stem runs in from the left and its hook climbs right."""
    cs = forms.contours('fork')
    main = max(cs, key=lambda c: np.ptp(c[:, 1]))
    if main[0, 1] < main[-1, 1]: main = main[::-1]          # start at the stem's foot
    other = [c for c in cs if c is not main]
    def place(c, seed):
        p = forms.to_screen(c, CR['cx'], CR['cy'], CR['scale'], math.pi / 2)
        p[:, 1] = 2 * CR['cy'] - p[:, 1]
        return jag(p, 7, seed)
    return place(main, 3), [place(c, 10 + i) for i, c in enumerate(other)]

def jag(p, amt, seed):
    rs = np.random.RandomState(seed); n = len(p); u = np.arange(n); off = np.zeros(n, np.float32)
    for o in range(6):
        st = max(2, n // (3 * 2 ** o)); pts = rs.normal(0, amt / (1.6 ** o), n // st + 2)
        off += np.interp(u, np.arange(len(pts)) * st, pts)
    tg = np.gradient(p, axis=0); tg /= np.linalg.norm(tg, axis=1, keepdims=True) + 1e-6
    return p + np.c_[-tg[:, 1], tg[:, 0]] * off[:, None]

@functools.lru_cache(None)
def junction():
    """Where the stem turns into the hook: max curvature along the main path."""
    p, _ = crack_paths(); tg = np.gradient(cv2.GaussianBlur(p.reshape(-1, 1, 2), (1, 31), 8).reshape(-1, 2), axis=0)
    ang = np.unwrap(np.arctan2(tg[:, 1], tg[:, 0])); curv = np.abs(np.gradient(ang))
    n = len(p); i = int(np.argmax(curv[n // 5: 4 * n // 5])) + n // 5
    hook = p[min(n - 1, i + 60)] - p[i]
    return i / n, tuple(p[i]), math.atan2(hook[1], hook[0])

def crack_front(f):
    """Arc-length fraction reached at local frame f: runs, hesitates at the junction, branches."""
    j, _, _ = junction()
    if f < 34: return eout(f / 34) * j
    if f < 56: return j + 0.004 * math.sin(f * 1.7)          # hesitation: the front flickers in place
    return j + (1 - j) * eout((f - 56) / 30)

def crack(f, n, F, push=0.0):
    main, others = crack_paths(); s = crack_front(f); j, (jx, jy), _ = junction()
    side = clamp((f - 50) / 26)                               # the secondary branch, after the hesitation
    drawn = stroke([main], 9, [s]); drawn = np.maximum(drawn, stroke(others, 6, [side] * len(others)))
    fr = stroke([main[int(len(main) * max(0, s - 0.06)):max(2, int(len(main) * s))]], 11)
    rough = fbm(34, 5, 30)
    groove = np.clip(blur(drawn, 4) * (0.6 + rough), 0, 1)
    hgt = rock_height(21) * 0.8 - groove * 0.5
    dif, spc = shade(hgt, (0.5, -0.7, 0.35), 12, spec=24, spec_k=0.6)
    # broken crust: dark plates float on the melt; the seam glows only between them
    crust = (fbm(35, 5, 26) > 0.52).astype(np.float32)
    heat = blur(drawn, 2) * (0.25 + 0.75 * (1 - blur(crust, 1.5))) + fr * 1.8
    illum = blur(heat, 60) * 2.6 + blur(heat, 18) * 1.3
    img = tint(dif ** 1.3 * 0.55, hexc('6a5e58'))                       # what little ambient reaches the rock
    img += tint(dif * illum * 1.2, hexc('a8805e')) + tint(spc * illum, EMBER) * 0.9
    img += tint(heat * 1.3, FURNACE) + tint(blur(fr, 1.5) * 2.0, hexc('fff1d8')) + tint(blur(heat, 9) * 0.5, COPPER)
    # sparks thrown off the front
    if s < 0.999:
        rs = np.random.RandomState(F); i = max(1, int(len(main) * s) - 1); px, py = main[i]
        k = 26; ang = rs.uniform(-math.pi, 0, k); spd = rs.uniform(20, 160, k); age = rs.rand(k)
        sp = np.zeros((H, W), np.float32)
        for q in range(k):
            x0 = px + math.cos(ang[q]) * spd[q] * age[q]; y0 = py + math.sin(ang[q]) * spd[q] * age[q] + 90 * age[q] ** 2
            sp = np.maximum(sp, lines_mask([[(x0, y0), (x0 - math.cos(ang[q]) * 14, y0 - math.sin(ang[q]) * 14)]], 1.5) * (1 - age[q]))
        img += tint(sp * 1.6, hexc('ffd9a0'))
    if 30 < f < 70:   # during the hesitation the rock around the front heats: a halo, not lines
        yy, xx = _grid(); near = np.exp(-(((xx / S - jx) ** 2 + (yy / S - jy) ** 2) / 120 ** 2))
        img += tint(near * dif * 0.5 * (0.6 + 0.4 * math.sin(f * 2.2)), FURNACE)
    vol, rays = dust_volume(F, light=(jx + 300, -120), k=0.5, ray=0.3)
    img = img + vol + tint(dif * rays * 1.4, hexc('8a6a50'))
    img = tilt_dof(img, jy, depth=340, max_sigma=6)
    dx, dy = jitter(F, 2.0)
    return move(img, dx, dy, 1.0 + push * f / n, cx=jx, cy=jy)

# ------------------------------------------------------------------ liquid: rings and a glitter path
def ripple(f, n, F):
    """A drop lands where the crack forked. Rings on dark liquid (the bowl), the low sun's
    glitter path down the surface (the stem), seen obliquely, so the rings are ellipses."""
    _, (jx, jy), ang = junction(); t = f / FPS
    yy, xx = _grid(); X = xx / S; Y = yy / S
    # surface coordinates: oblique, so depth compresses vertically
    u = X - jx; v = (Y - jy) / 0.36; r = np.sqrt(u * u + v * v)
    h = np.zeros((H, W), np.float32)
    for k, (t0, a0) in enumerate(((0.0, 1.0), (0.22, 0.6), (0.5, 0.35))):
        if t < t0: continue
        front = 40 + 420 * (t - t0); env = np.exp(-((r - front) / (60 + 80 * (t - t0))) ** 2) * a0 / (1 + (t - t0) * 2)
        h += np.sin((r - front) * 0.09) * env
    h += (fbm(71, 5, 18) - 0.5) * 0.25 + np.sin(X * 0.011 + Y * 0.05 + t * 2.0) * 0.08     # swell
    dif, spc = shade(h * 0.8, (0.08, -0.995, 0.10), 18, spec=90, spec_k=3.0)
    sun_x = jx + 40
    path = np.exp(-((X - sun_x) / (60 + (Y / 1080) * 220)) ** 2) * np.clip((Y - 80) / 400, 0, 1)
    img = tint(dif * 0.05, hexc('2a2420')) + tint(spc * (0.25 + path * 4.0), hexc('ffd9a8'))
    img += tint(spc * path * 0.6, FURNACE)
    horizon = np.exp(-((Y - 70) / 50) ** 2) * np.exp(-((X - sun_x) / 500) ** 2)
    img += tint(horizon * 1.2, AMBER) + tint(np.exp(-((Y - 60) / 18) ** 2 - ((X - sun_x) / 90) ** 2) * 3, hexc('fff2dc'))
    vol, _ = dust_volume(F, light=(sun_x, 40), k=0.3, ray=0.4); img += vol * 0.7
    img = tilt_dof(img, jy, depth=300, max_sigma=5)
    dx, dy = jitter(F, 1.2, 3)
    return move(img, dx, dy)

# ------------------------------------------------------------------ the membrane
MEM_SCHED = [(0, 'noise', 0.0), (14, 'fork', 1.0), (70, 'fork', 1.0), (80, 'bowl', 1.0), (150, 'bowl', 1.0),
             (158, 'mirror', 1.0), (182, 'mirror', 1.0), (190, 'channels', 1.0), (226, 'channels', 0.9)]
MEM_N = 226

@functools.lru_cache(None)
def mem():
    return membrane.simulate(MEM_SCHED, MEM_N)

@functools.lru_cache(None)
def bowl_centroid():
    cs = [c for c in forms.contours('bowl') if forms.closed(c)]
    return min(cs, key=len).mean(0) if cs else np.zeros(2, np.float32)

def membrane_frame(f, F, scale=780, cx=1000, cy=430, focus=None, exposure=1.25, obl=0.22):
    w, A = membrane._interp(MEM_SCHED, f)
    img = membrane.render(mem()[min(f, MEM_N - 1)], w, A, F, cx=cx, cy=cy, scale=scale)
    vol, _ = dust_volume(F, light=(1900, 150), k=0.25, ray=0.25)
    img = img + vol * 0.6
    if obl > 0: img = oblique(img, obl)
    if focus is not None: img = tilt_dof(img, focus, depth=420, max_sigma=6)
    dx, dy = jitter(F, 0.6 + A * 1.4, 5)
    return move(img, dx, dy)

def oblique(img, k):
    """Look at the plane obliquely: the far edge narrows (a camera, not a scanner)."""
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[W * k, -H * 0.1], [W * (1 - k), -H * 0.1], [W * (1 + k * 0.4), H * 1.04], [-W * k * 0.4, H * 1.04]])
    return cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (W, H), flags=cv2.INTER_LINEAR)

def bessel_plate(F):
    """2-frame print insert: J₁ and J₂ against r, with the zeros the grains will find."""
    from scipy.special import jv
    img = canvas(PAPER); xs = np.linspace(140, 1780, 500); rr = (xs - 140) / 1640 * 12
    for gx in range(140, 1781, 82): over(img, hexc('9fb7c0'), lines_mask([[(gx, 160), (gx, 920)]], 1) * 0.5)
    over(img, INK, lines_mask([[(140, 540), (1780, 540)]], 2))
    for nn, col in ((1, INK), (2, RED)):
        ys = 540 - jv(nn, rr) * 620
        over(img, col, lines_mask([list(zip(xs, ys))], 3))
    from scipy.special import jn_zeros
    for z in jn_zeros(1, 3):
        x = 140 + z / 12 * 1640; over(img, RED, circle_mask(x, 540, 9, 2)); text(img, f'{z:.3f}', 'mono', 18, x - 30, 570, RED)
    text(img, 'J₁(kr) cos θ', 'serifi', 64, 160, 180, INK)
    text(img, 'NODES: WHERE THE SKIN IS STILL', 'mono', 18, 160, 960, INK, wght=600)
    return img

# ------------------------------------------------------------------ scale collapse
def sem(h, F, label, k=6.0):
    """Scanning-electron rendering of a heightfield: edges glow, flats are grey, the scan is noisy."""
    gy, gx = np.gradient(h * S ** 0 * 1.0)
    g = np.sqrt(gx * gx + gy * gy) * k * 100
    v = 0.18 + 0.45 * h + np.clip(g, 0, 1.2) * 0.55
    rs = np.random.RandomState(F % 97)
    v = v + rs.randn(H, 1).astype(np.float32) * 0.02 + rs.randn(H, W).astype(np.float32) * 0.05   # line jitter + shot noise
    img = tint(np.clip(v, 0, 1.2), hexc('e8e4dc'))
    over(img, BLACK, rect_mask(0, 1010, 1920, 1080))
    text(img, label, 'mono', 20, 30, 1032, hexc('e8e4dc'), wght=500)
    over(img, hexc('e8e4dc'), rect_mask(1600, 1036, 1860, 1044))
    return img

@functools.lru_cache(None)
def _sem_grains(seed=2, n=420):
    rs = np.random.RandomState(seed)
    c = np.c_[rs.uniform(-60, 1980, n), rs.uniform(-60, 1140, n)]; r = rs.uniform(26, 70, n)
    yy, xx = _grid(); tree = cKDTree(c * S)
    d, idx = tree.query(np.c_[xx.ravel(), yy.ravel()], k=3)
    h = np.zeros(H * W, np.float32)
    for j in range(3):
        rr = r[idx[:, j]] * S; h = np.maximum(h, np.sqrt(np.clip(rr ** 2 - d[:, j] ** 2, 0, None)) / (70 * S))
    h = h.reshape(H, W) + fbm(57, 5, 60) * 0.08
    return h.astype(np.float32)

@functools.lru_cache(None)
def _sem_crystal(seed=6, n=110):
    rs = np.random.RandomState(seed); pts = np.c_[rs.uniform(-200, 2120, n), rs.uniform(-200, 1280, n)]
    yy, xx = _grid(); tree = cKDTree(pts * S)
    _, lab = tree.query(np.c_[xx.ravel(), yy.ravel()]); lab = lab.reshape(H, W)
    edge = (cv2.Laplacian(lab.astype(np.float32), cv2.CV_32F) != 0).astype(np.uint8)
    dist = cv2.distanceTransform(1 - edge, cv2.DIST_L2, 3) / S
    tilt = rs.normal(0, 0.0012, (n, 2))
    h = np.clip(dist / 40, 0, 1) ** 0.5 * 0.6 + (tilt[lab, 0] * xx / S + tilt[lab, 1] * yy / S) % 0.3
    fork = poly_screen('fork', 960, 540, 900, math.pi / 2, jag=10, seed=4)
    h = h - blur(stroke(fork, 6), 2) * 0.6
    return h.astype(np.float32)

def sem_grains(f, F, k=1.0):
    h = _sem_grains()
    if k != 1.0: h = zoom(h[..., None].repeat(3, -1), k)[..., 0]
    return sem(h, F, f'SE  20kV  ×{int(240 * k)}  WD 9.8   SPECIMEN 0049')

def sem_crystal(f, F):
    return sem(_sem_crystal(), F, 'SE  20kV  ×6,000  WD 9.8   SPECIMEN 0049')

@functools.lru_cache(None)
def _lichtenberg(seed=7):
    """An electrical tree grown into a resin block: segments (x0,y0,x1,y1,depth,order)."""
    rs = np.random.RandomState(seed); segs = []
    def grow(x, y, a, L, d, o):
        if L < 14 or o > 8: return
        n = max(2, int(L / 12)); px, py = x, y
        for i in range(n):
            a += rs.normal(0, 0.10); nx, ny = px + math.cos(a) * L / n, py + math.sin(a) * L / n
            segs.append((px, py, nx, ny, d, o)); px, py = nx, ny
        spread = 0.553  # half of 63.4°: the bifurcation angle the crystal measured
        grow(px, py, a - spread + rs.normal(0, 0.1), L * rs.uniform(0.62, 0.78), d + rs.normal(0, 0.25), o + 1)
        grow(px, py, a + spread + rs.normal(0, 0.1), L * rs.uniform(0.62, 0.78), d + rs.normal(0, 0.25), o + 1)
    grow(960, 1180, -math.pi / 2, 420, 0.0, 0)
    return segs

def lichtenberg(f, n, F, k=1.0):
    segs = _lichtenberg(); maxo = clamp(f / (n * 0.45)) * 10
    layers = [np.zeros((H, W), np.float32) for _ in range(3)]
    for (x0, y0, x1, y1, d, o) in segs:
        if o > maxo: continue
        b = min(2, int(abs(d) * 1.6)); w = max(1, int((4.5 - o * 0.4) * S))
        p0 = ((np.array([x0, y0]) - [960, 540]) * k + [960, 540]) * S * 16; p1 = ((np.array([x1, y1]) - [960, 540]) * k + [960, 540]) * S * 16
        cv2.line(layers[b], tuple(p0.astype(int)), tuple(p1.astype(int)), 1.0, w, cv2.LINE_AA, 4)
    fresh = clamp(1.5 - f / n)
    m = layers[0] + blur(layers[1], 3) + blur(layers[2], 8)
    img = canvas(hexc('060507')) + tint(blur(m, 14) * 1.5, FURNACE) + tint(m * (0.6 + fresh), hexc('ffe6c4')) + tint(blur(m, 3), AMBER)
    resin = fbm(91, 4, 3) * 0.06; img += tint(resin, hexc('3a2a20'))
    return img

def blueprint(F, kind='angle'):
    img = canvas(hexc('0d2a55')); ink = hexc('d8e8ff')
    for g in range(0, 1920, 60): over(img, ink, lines_mask([[(g, 0), (g, 1080)]], 1) * 0.12)
    for g in range(0, 1080, 60): over(img, ink, lines_mask([[(0, g), (1920, g)]], 1) * 0.12)
    if kind == 'angle':
        o = (960, 760); a = 0.553
        over(img, ink, lines_mask([[o, (960, 980)], [o, (960 - 520 * math.sin(a), 760 - 520 * math.cos(a))], [o, (960 + 520 * math.sin(a), 760 - 520 * math.cos(a))]], 3))
        e = np.zeros((H, W), np.uint8)
        cv2.ellipse(e, P(*o), (int(220 * S), int(220 * S)), -90, -31.7, 31.7, 255, max(1, int(2 * S)), cv2.LINE_AA); over(img, ink, e / 255)
        text(img, '63.4°', 'mono', 40, 1010, 470, ink, wght=600)
    else:  # logistic map: forks all the way down
        xs = []; ys = []
        for rr in np.linspace(2.6, 4.0, 1400):
            x = 0.5
            for _ in range(200): x = rr * x * (1 - x)
            for _ in range(60): x = rr * x * (1 - x); xs.append(100 + (rr - 2.6) / 1.4 * 1720); ys.append(980 - x * 880)
        m = np.zeros((H, W), np.float32); splat(m, np.array(xs), np.array(ys), np.ones(len(xs)) * 0.7)
        over(img, ink, np.clip(blur(m, 0.6) * 3, 0, 1))
        text(img, 'xₙ₊₁ = r xₙ (1 − xₙ)', 'mono', 26, 110, 90, ink, wght=500)
    return img

# ------------------------------------------------------------------ the edit list
def build():
    E = []; A = E.append
    jx, jy = junction()[1]
    # 1 · before the symbol: near-black, a ray through particulate, hidden structure flashes
    def void(f, n, F):
        vol, _ = dust_volume(F, light=(1460, -80), k=ss(0, 40, f), ray=ss(4, 50, f) * 0.9)
        img = vol * 0.8
        if f in (27, 28, 47, 48):  # a discharge somewhere lights hidden terrain for a frame
            hgt = rock_height(41) - blur(stroke(poly_screen('channels', 900, 560, 700, 0.3, jag=12), 30), 12) * 0.6
            dif, _ = shade(hgt, (0.7, -0.6, 0.3), 12)
            img += tint(dif ** 2 * (1.0 if f in (27, 47) else 0.4), hexc('c49a70'))
        return develop(img, F, exposure=1.1)
    A((64, tag(void, 'silence')))
    # 2 · first glowing bend
    A((90, tag(lambda f, n, F: develop(crack(f, n, F, push=0.05), F, exposure=1.7 + (1.2 if 34 <= f < 37 else 0)), 'crack')))
    A((1, lambda f, n, F: develop(crack(n, n, F) * 6, F, exposure=2.5)))            # light destroys the frame
    # 3 · liquid: a drop where the crack forked; rings for a bowl, the sun's glitter path for a stem
    A((56, tag(lambda f, n, F: develop(ripple(f, n, F), F, exposure=1.2), 'drop')))
    # 4 · the membrane
    def mem_shot(f, n, F):
        if 118 <= f < 146:  # optical magnification jump onto the bowl, square-on
            bc = bowl_centroid(); sc = 1050
            img = membrane_frame(f, F, scale=sc, cx=960 - bc[0] * sc, cy=540 - bc[1] * sc, focus=540, obl=0)
        else:
            img = membrane_frame(f, F, focus=lerp(260, 780, ss(0, 70, f)) if f < 118 else 600)
        img = develop(img, F, exposure=2.2)
        if f == 172: img = invert(img)
        return img
    def mem_cut(a, b, snd=None):
        fn = lambda f, n, F: mem_shot(f + a, n, F)
        A((b - a, tag(fn, snd) if snd else fn))
    mem_cut(0, 86, 'tone:82.4')
    A((2, lambda f, n, F: bessel_plate(F)))
    mem_cut(86, 154, 'tone:110')
    mem_cut(154, 186, 'tone:98')
    A((1, lambda f, n, F: canvas(BLACK)))
    mem_cut(186, 226, 'tone:73.4')
    # 5 · violent scale collapse: grain → crystal → bifurcation, with sub-second math
    A((8, lambda f, n, F: sem_grains(f, F, 1.0 + step(f / 8, 4) * 0.8)))
    A((2, lambda f, n, F: blueprint(F, 'angle')))
    A((8, lambda f, n, F: sem_crystal(f, F)))
    A((1, lambda f, n, F: canvas(BLACK)))
    A((18, tag(lambda f, n, F: develop(lichtenberg(f, n, F), F, exposure=1.3), 'impact')))
    A((2, lambda f, n, F: blueprint(F, 'logistic')))
    A((10, lambda f, n, F: develop(lichtenberg(n, n, F, k=0.62), F, exposure=1.1)))
    A((1, lambda f, n, F: invert(develop(lichtenberg(n, n, F, k=0.62), F))))
    return E

def prepare():
    mem()
