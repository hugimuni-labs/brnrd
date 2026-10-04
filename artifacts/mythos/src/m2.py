"""Act 2 — observation. The world is shaken into legibility, then measured, then written down.

A dark surface whose channels were cut by the same nodal sets the membrane found. An instrument
intrudes: reticle glass, registration brackets, grease pencil. Measuring perturbs it. The form is
printed onto polymer as a specimen, and only then does anyone write a letter beside it.
"""
import functools, math
import numpy as np
import cv2
import forms, membrane
from core import *
from core import _grid, _grain_bank
from optics import *
from stage import *
import m1

# the specimen's placement, shared by every shot in this act (preserve position)
SP = dict(cx=860, cy=500, scale=560, rot=0.10)

@functools.lru_cache(None)
def bowl_polys(jagk=5):
    return poly_screen('bowl', SP['cx'], SP['cy'], SP['scale'], SP['rot'], jag=jagk, seed=21)

@functools.lru_cache(None)
def oval_box():
    cs = [c for c in forms.contours('bowl')]
    allp = np.concatenate([forms.to_screen(c, SP['cx'], SP['cy'], SP['scale'], SP['rot']) for c in cs])
    oval = min([c for c in cs if forms.closed(c)], key=len)
    o = forms.to_screen(oval, SP['cx'], SP['cy'], SP['scale'], SP['rot'])
    return o.min(0), o.max(0), o.mean(0), allp.min(0), allp.max(0)

@functools.lru_cache(None)
def terrain():
    """Height of the observed surface: rock, a drainage network, and among its channels the
    nodal sets the membrane found — the bowl here, a fork and a pair of channels elsewhere."""
    brk = (fbm(63, 4, 9) > 0.36).astype(np.float32)                     # erosion breaks the run
    ch = stroke(bowl_polys(), 22) * blur(brk, 6)
    others = stroke(poly_screen('fork', 1560, 330, 300, 2.2, jag=9, seed=31), 14) + \
        stroke(poly_screen('channels', 330, 900, 260, -0.4, jag=9, seed=37), 12) + \
        stroke(poly_screen('mirror', 1620, 960, 420, 0.7, jag=12, seed=41), 16)
    chan = blur(ch, 14) * 0.8 + blur(ch, 4) * 0.35 + blur(np.clip(others, 0, 1), 10) * 0.7
    rid = np.abs(fbm(61, 6, 7) - 0.5); trib = np.clip(1 - rid / 0.06, 0, 1)
    h = rock_height(55) * 0.8 - chan * 0.5 - trib * 0.10 - blur(trib, 3) * 0.05
    return h.astype(np.float32), np.clip(blur(ch, 7) * 1.2, 0, 1)

def surface(F, lx=0.0, heat=0.3, shift=(0, 0), exposure_k=1.0):
    h, chan = terrain()
    L = (math.cos(lx) * 0.85, math.sin(lx) * 0.5 - 0.45, 0.30)
    dif, spc = shade(h, L, 14, spec=50, spec_k=1.2)
    img = tint(dif ** 1.4 * 0.42, hexc('6b625c')) + tint(spc * 1.1, hexc('ffd9b0'))
    glow = chan * (0.6 + 0.4 * fbm(62, 4, 20))
    img += tint(blur(glow, 14) * heat * 0.7, FURNACE) + tint(blur(glow, 3) * heat * 0.25, EMBER)
    if shift != (0, 0): img = move(img, *shift)
    return img * exposure_k

# ------------------------------------------------------------------ 6 · the stare
def stare(f, n, F):
    img = surface(F, lx=lerp(-0.6, 0.15, f / n), heat=0.07 + 0.03 * math.sin(f * 0.15))
    vol, _ = dust_volume(F, light=(1700, -60), k=0.45, ray=0.5); img += vol * 0.8
    img = tilt_dof(img, 520, depth=380, max_sigma=8)
    img = move(img, *jitter(F, 0.5, 11), 1.0 + 0.02 * f / n)
    return develop(img, F, exposure=2.0, fg=foreground_dust(F, 14, light=(1700, -60), k=0.35, seed=12))

# ------------------------------------------------------------------ 7 · measurement intrusion
def reticle(cx, cy, f):
    m = np.zeros((H, W), np.float32)
    m = np.maximum(m, lines_mask([[(cx - 900, cy), (cx - 40, cy)], [(cx + 40, cy), (cx + 900, cy)], [(cx, cy - 600), (cx, cy - 40)], [(cx, cy + 40), (cx, cy + 600)]], 1.4))
    for r in (120, 260): m = np.maximum(m, circle_mask(cx, cy, r, 1.2))
    for k in range(-30, 31):
        L = 18 if k % 5 == 0 else 8
        m = np.maximum(m, lines_mask([[(cx + k * 26, cy - L), (cx + k * 26, cy + L)], [(cx - L, cy + k * 26), (cx + L, cy + k * 26)]], 1.1))
    return m

def brackets(lo, hi, k=1.0):
    (x0, y0), (x1, y1) = lo, hi; L = 40
    segs = [[(x0, y0 + L), (x0, y0), (x0 + L, y0)], [(x1 - L, y0), (x1, y0), (x1, y0 + L)],
            [(x0, y1 - L), (x0, y1), (x0 + L, y1)], [(x1 - L, y1), (x1, y1), (x1, y1 - L)]]
    return lines_mask(segs, 2.5 * k)

def pencil(cx, cy, rx, ry, frac, seed=3):
    """Grease pencil on the glass: waxy, uneven, overshooting its start."""
    a = np.linspace(-0.3, 2 * math.pi * 1.08 - 0.3, 220)[:max(2, int(220 * frac))]
    rs = np.random.RandomState(seed); wob = np.cumsum(rs.normal(0, 0.012, len(a)))
    pts = np.c_[cx + rx * (1 + wob) * np.cos(a), cy + ry * (1 + wob) * np.sin(a)]
    m = lines_mask([pts], 9)
    return m * (fbm(64, 4, 40) > 0.38)

def measure(f, n, F):
    olo, ohi, oc, alo, ahi = oval_box()
    snaps = [14, 30, 44]                                  # each snap disturbs the observed world
    hit = max([1 - (f - s) / 6 for s in snaps if 0 <= f - s < 6] + [0])
    shake_ = (hsh(F * 1.3) - 0.5) * 14 * hit, (hsh(F * 2.1) - 0.5) * 10 * hit
    img = surface(F, lx=0.15 + 0.004 * f, heat=0.08 + 0.8 * hit, shift=shake_, exposure_k=1 + 0.5 * hit)
    vol, _ = dust_volume(F, light=(1700, -60), k=0.45 + hit, ray=0.5); img += vol * 0.8
    rack = ss(20, 46, f)                                  # focus moves from the glass to the ground
    img = defocus(img, 9 * (1 - rack) + 0.5)
    # the glass layer
    cx, cy = oc
    g = reticle(cx + 30 * (1 - ss(0, 14, f)), cy, f)
    pad = [(26, 18), (10, 8), (0, 0)]; stage_ = sum(f >= s for s in snaps)
    if stage_:
        dx, dy = pad[stage_ - 1]
        g = np.maximum(g, brackets((olo[0] - dx - 16, olo[1] - dy - 16), (ohi[0] + dx + 16, ohi[1] + dy + 16)))
    g = defocus(g, 0.4 + 5 * rack)
    img += tint(g * 0.85, hexc('f4efe4')) + tint(blur(g, 6) * 0.3, hexc('f4efe4'))
    if f >= 50:
        pm = defocus(pencil(cx, cy, (ohi[0] - olo[0]) * 0.72, (ohi[1] - olo[1]) * 0.62, ss(50, 66, f)), 0.4 + 4 * rack)
        over(img, hexc('c4231c'), pm * 0.9)
    text(img, f'{F:05d}', 'mono', 16, 60, 60, hexc('f4efe4'), a=0.8, wght=500)
    text(img, f'×{[1.0, 2.5, 4.0, 4.0][stage_]:.1f}', 'mono', 16, 60, 84, hexc('f4efe4'), a=0.8)
    if stage_ >= 2: text(img, 'REG', 'mono', 14, ohi[0] + 26, olo[1] - 6, hexc('f4efe4'), a=0.9, wght=600)
    return develop(img, F, exposure=1.9)

# ------------------------------------------------------------------ 8 · specimen → drawing
@functools.lru_cache(None)
def wrinkles():
    yy, xx = _grid(); X = xx / S; Y = yy / S
    wa = fbm(66, 3, 3) * 6; h = np.zeros((H, W), np.float32)
    for k, (a, f_, amp) in enumerate(((0.3, 0.020, 1.0), (1.9, 0.034, 0.6), (-0.7, 0.051, 0.35), (1.1, 0.012, 0.8))):
        h += np.sin((X * math.cos(a) + Y * math.sin(a)) * f_ + wa * (k + 1)) * amp
    return (h * 0.05 + fbm(67, 5, 30) * 0.03).astype(np.float32)

def film(img, F, relax=1.0, light=(0.6, -0.6, 0.5)):
    """Translucent polymer laid over the scene: milky, wrinkled, its thickness catching light."""
    wr = wrinkles() * (0.4 + 0.6 * relax)
    dif, spc = shade(wr, light, 30, spec=40, spec_k=1.4)
    milk = hexc('d8d2c6')
    yy, xx = _grid(); back = np.exp(-(((xx / S - 1500) / 700) ** 2 + ((yy / S - 200) / 500) ** 2))   # light behind the film
    base = img * 0.55 + tint(0.05 + dif * 0.14 + back * 0.30, milk)
    return base + tint(spc * 1.3, WHITE) + tint(back * spc * 0.6, AMBER)

def specimen(f, n, F):
    olo, ohi, oc, alo, ahi = oval_box()
    scene = surface(F, lx=0.3, heat=0.12)
    img = film(scene, F, relax=1 - ss(0, 24, f))
    # thermal print head sweeping down: the form is written into the film
    yh = lerp(alo[1] - 60, ahi[1] + 60, ss(18, 52, f))
    yy, _ = _grid()
    pm = stroke(bowl_polys(2), 6) * (yy / S < yh)
    pm = pm * ((_grain_bank()[3] * 0.25 + 0.85) > 0.62)        # thermal dropout
    over(img, hexc('17140f'), np.clip(pm * 1.1, 0, 1))
    if 18 <= f < 54:
        head = np.exp(-((yy / S - yh) / 6) ** 2); img += tint(head[..., None][..., 0] * 0.6, FURNACE)
    lab = [('SPECIMEN 0049', 30, 600), ('FORM   STEM + BOWL', 20, 500), ('FOUND  SKIN · ROCK · WATER', 20, 500)]
    x0, y0 = ahi[0] + 80, max(140, alo[1] + 30)
    for k, (s, sz, wg) in enumerate(lab):
        if f >= 40 + k * 4: text(img, s, 'mono', sz, x0, y0 + k * 40, hexc('17140f'), wght=wg)
    if f >= 56:   # the first letters: a human reading of a form that was already there
        text(img, 'READS', 'mono', 20, x0, y0 + 150, hexc('17140f'), wght=500)
        for k, ch in enumerate('bdpq'):
            if f >= 56 + k * 3: text(img, ch, 'serif', 64, x0 + 110 + k * 54, y0 + 128, hexc('17140f'))
    for (x, y) in ((alo[0] - 50, alo[1] - 50), (ahi[0] + 50, ahi[1] + 50)):
        over(img, hexc('17140f'), lines_mask([[(x - 18, y), (x + 18, y)], [(x, y - 18), (x, y + 18)]], 1.6) * float(f >= 30))
    img = move(img, *jitter(F, 0.6, 13))
    return develop(img, F, exposure=1.05, halation=0.15)

# ------------------------------------------------------------------ 9 · symbol-hunting barrage
@functools.lru_cache(None)
def form_box(name):
    """Screen bbox of a form under the specimen placement (whole form, and its tightest part)."""
    cs = forms.contours(name)
    pts = [forms.to_screen(c, SP['cx'], SP['cy'], SP['scale'], SP['rot']) for c in cs]
    main = max(pts, key=lambda p: np.ptp(p[:, 1]))
    allp = np.concatenate(pts)
    return allp.min(0), allp.max(0), main.min(0), main.max(0)

@functools.lru_cache(None)
def fitted(ch, fnt, box, wght=None, flip=False):
    """A glyph scaled onto a box (keeps aspect within 25%), as a full-frame mask."""
    f = font(fnt, 600, wght); im = Image.new('L', (1400, 1400), 0); d = ImageDraw.Draw(im)
    d.text((700, 700), ch, font=f, fill=255, anchor='mm'); bb = im.getbbox()
    if bb is None: return np.zeros((H, W), np.float32)
    g = np.asarray(im.crop(bb), np.float32) / 255
    if flip: g = g[:, ::-1]
    (x0, y0), (x1, y1) = box; bw, bh = (x1 - x0) * S, (y1 - y0) * S
    sy = bh / g.shape[0]; sx = np.clip(bw / g.shape[1], sy * 0.75, sy * 1.25)
    g = cv2.resize(g, (max(1, int(g.shape[1] * sx)), max(1, int(g.shape[0] * sy))), interpolation=cv2.INTER_AREA)
    m = np.zeros((H, W), np.float32); cx, cy = (x0 + x1) / 2 * S, (y0 + y1) / 2 * S
    ox, oy = int(cx - g.shape[1] / 2), int(cy - g.shape[0] / 2)
    xa, ya = max(0, ox), max(0, oy); xb, yb = min(W, ox + g.shape[1]), min(H, oy + g.shape[0])
    if xb > xa and yb > ya: m[ya:yb, xa:xb] = g[ya - oy:yb - oy, xa - ox:xb - ox]
    return m

from PIL import Image, ImageDraw

def ghost(name, w=5):
    return stroke(poly_screen(name, SP['cx'], SP['cy'], SP['scale'], SP['rot'], jag=2, seed=21), w)

def barrage_states():
    mem = m1.mem()
    def box(name, part='all'):
        lo, hi, mlo, mhi = form_box(name)
        b = (lo, hi) if part == 'all' else (mlo, mhi)
        return (tuple(np.round(b[0], 1)), tuple(np.round(b[1], 1)))
    def grains(form_f):
        def fn(f, n, F):
            w, A = membrane._interp(m1.MEM_SCHED, form_f)
            img = membrane.render(mem[form_f], w, A, F, cx=SP['cx'], cy=SP['cy'], scale=SP['scale'], rot=SP['rot'], rim=False)
            return develop(img, F, exposure=1.6)
        return fn
    # materials: each takes a glyph mask and the found form it was fitted to
    def groove(gm, form, F):
        h, _ = terrain(); h = h - blur(gm, 4) * 0.45
        dif, spc = shade(h, (0.8, -0.4, 0.3), 14, spec=50, spec_k=1.2)
        img = tint(dif ** 1.6 * 0.25, hexc('6b625c')) + tint(spc, hexc('ffd9b0')) + tint(blur(gm, 6) * 0.5, FURNACE)
        return develop(img, F, exposure=1.3)
    def emboss(gm, form, F):
        h = blur(gm, 3) * 0.6 + fbm(73, 5, 40) * 0.05
        dif, spc = shade(h, (-0.85, -0.3, 0.35), 10, spec=30, spec_k=0.4)
        img = tint(0.25 + dif * 0.85, PAPER) + tint(spc, WHITE); over(img, RED, ghost(form, 3) * 0.55); return img
    def ink(gm, form, F):
        img = canvas(PAPER); over(img, hexc('3a5bd9'), np.roll(ghost(form, 9), int(10 * S), 1) * 0.8)
        over(img, INK, gm * ((_grain_bank()[F % 6] * 0.3 + 0.8) > 0.5)); return img
    def filmprint(gm, form, F):
        img = film(surface(F, lx=0.3, heat=0.1), F); over(img, hexc('17140f'), gm * 0.92); return develop(img, F, exposure=1.05, halation=0.1)
    def phosphor(gm, form, F):
        img = canvas(hexc('050805')); img += tint(ghost(form, 4) * 0.3, hexc('ffb347'))
        img += tint(blur(gm, 8) * 0.7, hexc('ffb347')) + tint(gm, hexc('ffd890')); return crush(img, 6)
    def stencil(gm, form, F):
        img = canvas(VIOLET); over(img, OFFWHITE, (gm > 0.5).astype(np.float32)); return img
    def xerox(gm, form, F):
        img = canvas(PAPER); over(img, INK, np.maximum(gm, ghost(form, 6) * 0.7))
        return threshold(img, 0.5, noise=0.25, frame=F)
    def G(ch, fnt, mat, form, part='all', wght=None, flip=False):
        b = box(form, part)
        return lambda f, n, F: mat(fitted(ch, fnt, b, wght, flip), form, F)
    black = lambda f, n, F: canvas(BLACK)
    # 2–4 frames each; the bowl family, then the fork family, then the channels
    return [
        (3, grains(140)), (2, G('b', 'serif', ink, 'bowl')), (3, G('b', 'bodoni', groove, 'bowl')),
        (2, G('d', 'serif', emboss, 'bowl', flip=False)), (2, G('ᛒ', 'runic', stencil, 'bowl')),
        (3, G('p', 'serif', filmprint, 'bowl')), (2, G('q', 'vt', phosphor, 'bowl')),
        (2, G('o', 'serif', xerox, 'bowl', 'main')), (2, G('a', 'serifi', ink, 'bowl', 'main')), (2, G('J', 'bodoni', groove, 'bowl')),
        (1, black),
        (3, grains(60)), (2, G('r', 'bodoni', groove, 'fork')), (2, G('ᚠ', 'runic', filmprint, 'fork')),
        (2, G('Y', 'anton', stencil, 'fork')), (2, G('^', 'vt', phosphor, 'fork', 'main')), (2, G('λ', 'mono', ink, 'fork')),
        (2, G('⤙', 'math', emboss, 'fork', 'main')), (2, G('l', 'serif', xerox, 'fork', 'main')),
        (1, lambda f, n, F: invert(develop(surface(F, heat=0.8), F))),
        (3, grains(212)), (2, G('n', 'serif', groove, 'channels', 'main')), (2, G('u', 'bodoni', ink, 'channels', 'main')),
        (2, G('ω', 'sans', filmprint, 'channels', 'main', wght=300)), (2, G('ᚢ', 'runic', emboss, 'channels', 'main')),
        (2, G('n', 'anton', stencil, 'channels', 'main')), (1, lambda f, n, F: canvas(WHITE)),
    ]

def build():
    E = []; A = E.append
    A((72, tag(stare, 'tone:55')))
    A((72, tag(measure, 'snap:14,30,44')))
    A((70, tag(specimen, 'print')))
    A((1, lambda f, n, F: canvas(BLACK)))
    for d, fn in barrage_states(): A((d, fn))
    return E
