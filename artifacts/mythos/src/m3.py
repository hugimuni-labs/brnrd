"""Act 3 — engineering → signal. Shape-hunting becomes manufacture, manufacture becomes transmission.

The found form is pressed into dark metal (the die is never seen, only the light it takes away),
conducts, repeats across a wafer, unrolls into a serpentine trace (n u n u), flattens into a woven
weft, and leaves as a pulse on a wire. The register at the end embosses the name in Morse —
the first time brnrd appears, it is rhythm, not type.
"""
import functools, math
import numpy as np
import cv2
import forms
from core import *
from core import _grid, _grain_bank
from optics import *
from stage import *
from m2 import SP, bowl_polys, oval_box

MORSE = {'b': '-...', 'r': '.-.', 'n': '-.', 'd': '-..'}

@functools.lru_cache(None)
def metal():
    return (fbm(81, 6, 5) * 0.4 + cv2.resize(np.random.RandomState(82).rand(H // 2 + 1, 18).astype(np.float32), (W, H), interpolation=cv2.INTER_CUBIC) * 0.15).astype(np.float32)

@functools.lru_cache(None)
def impression():
    """The pressed form: channel pushed down, a lip of displaced metal raised beside it."""
    m = stroke(bowl_polys(2), 26)
    return (-blur(m, 5) * 0.55 + blur(m, 22) * 0.18).astype(np.float32), m

# ------------------------------------------------------------------ 10 · the press
def press(f, n, F):
    imp, m = impression()
    struck = f >= 22
    h = metal() + (imp if struck else 0)
    lx = -0.9 + 0.5 * clamp((f - 22) / (n - 22)) if struck else -0.9
    dif, spc = shade(h, (lx, -0.35, 0.32), 16, spec=40, spec_k=1.4)
    img = tint(dif ** 1.2 * 0.55, hexc('8c8f98')) + tint(spc, hexc('ffe2c0')) * 1.3
    yy, xx = _grid()
    if not struck:  # the die descends out of frame: only its shadow arrives, in three stepped poses
        edge = lerp(-200, 1200, step(f / 21, 3) * 0.85)
        shadow = np.clip((edge - yy / S) / 60, 0, 1)
        img *= (1 - 0.92 * shadow)[..., None]
        img += tint(np.exp(-((yy / S - edge) / 4) ** 2) * 0.7, AMBER)   # the die's lit lower edge
        vol, _ = dust_volume(F, light=(1700, -60), k=0.4 * (1 - step(f / 21, 3)), ray=0.4); img += vol
        dx, dy = jitter(F, 0.6)
    else:
        k = f - 22; heat = math.exp(-k / 22)
        img += tint(blur(m, 18) * heat * 0.9, FURNACE) + tint(blur(m, 3) * heat * 0.6, EMBER) + tint(m * heat ** 4 * 1.5, hexc('fff2dc'))
        # dust thrown out radially along the surface
        rs = np.random.RandomState(5); N = 5000; olo, ohi, oc, alo, ahi = oval_box()
        a = rs.rand(N) * 6.283; sp_ = rs.uniform(200, 1400, N); t = k / FPS
        dist = sp_ * (1 - np.exp(-t * 3)) / 3
        px = oc[0] + np.cos(a) * (dist + rs.uniform(0, 300, N)); py = oc[1] + np.sin(a) * (dist + rs.uniform(0, 300, N)) * 0.6 - 60 * t
        d = np.zeros((H, W), np.float32); splat(d, px, py, rs.uniform(0.3, 1, N) * math.exp(-t * 1.2))
        img += tint(blur(d, 1.5) * 4, EMBER) + tint(blur(d, 12) * 6, COPPER)
        amp = 26 * math.exp(-k / 4) * (1 if k % 2 else -1)
        dx, dy = amp * 0.4, amp
    img = tilt_dof(img, 520, depth=380, max_sigma=7)
    return develop(move(img, dx, dy), F, exposure=1.3)

# ------------------------------------------------------------------ 11 · conduction → wafer → serpentine
def conduct(f, n, F):
    imp, m = impression(); h = metal() + imp
    dif, spc = shade(h, (-0.4, -0.35, 0.32), 16, spec=40, spec_k=1.4)
    img = tint(dif ** 1.2 * 0.5, hexc('8c8f98')) + tint(spc, hexc('ffe2c0')) * 1.2
    img += tint(blur(m, 10) * 0.12, COPPER)
    polys = bowl_polys(2); s = (f / n) * 1.4
    head = stroke([p[int(len(p) * clamp(s - 0.18)):max(2, int(len(p) * clamp(s)))] for p in polys], 10)
    img += tint(blur(head, 8) * 1.0, FURNACE) + tint(head * 1.0, hexc('fff1d6'))
    return develop(move(img, *jitter(F, 0.6)), F, exposure=1.3)

def photomask(F):
    img = canvas(hexc('050505')); m = stroke(bowl_polys(0), 24) - stroke(bowl_polys(0), 8)
    over(img, hexc('f4f0e6'), np.clip(m, 0, 1))
    for (x, y) in ((120, 120), (1800, 120), (120, 960), (1800, 960)):
        over(img, hexc('f4f0e6'), lines_mask([[(x - 30, y), (x + 30, y)], [(x, y - 30), (x, y + 30)]], 3)); over(img, hexc('f4f0e6'), circle_mask(x, y, 18, 2))
    text(img, 'MASK 0049 · LAYER M1', 'mono', 18, 160, 1010, hexc('f4f0e6'), wght=600)
    return img

@functools.lru_cache(None)
def wafer_tile(k):
    """The impression shrunk into one die of an array."""
    imp, m = impression()
    if k <= 1.0: return m
    olo, ohi, oc, alo, ahi = oval_box()
    x0, y0 = int((alo[0] - 80) * S), int((alo[1] - 60) * S); x1, y1 = int((ahi[0] + 80) * S), int((ahi[1] + 60) * S)
    cell = m[max(0, y0):y1, max(0, x0):x1]
    ch = int(H / k); cw = max(1, int(cell.shape[1] * ch / max(1, cell.shape[0])))
    cell = cv2.resize(cell, (cw, ch), interpolation=cv2.INTER_AREA)
    pad = np.zeros((int(ch * 1.15), int(cw * 1.5)), np.float32); pad[:ch, :cw] = cell
    t = np.tile(pad, (H // pad.shape[0] + 2, W // pad.shape[1] + 2))
    oy = int(oc[1] * S) - ch // 2; ox = int(oc[0] * S) - cw // 2       # keep the original die where it was
    t = np.roll(np.roll(t, oy % pad.shape[0], 0), ox % pad.shape[1], 1)
    return t[:H, :W].copy()

def wafer(f, n, F):
    k = [1.0, 2.4, 5.5][min(2, int(f / n * 3))]
    m = wafer_tile(k)
    h = metal() * 0.4 - blur(m, 1.5) * 0.3
    dif, spc = shade(h, (-0.5, -0.4, 0.4), 10, spec=60, spec_k=1.2)
    film_ = fbm(84, 3, 2)   # thin-film interference: restrained, bronze ↔ violet
    base = hexc('3a2c26') * (1 - film_[..., None]) + hexc('2c2440') * film_[..., None]
    img = tint(dif * 0.6 + 0.15, np.ones(3, np.float32)) * base * 2.2 + tint(spc, hexc('ffe6c8')) * 0.8
    img += tint(m * 0.7, COPPER) + tint(blur(m, 4) * 0.3, FURNACE)
    return develop(move(img, *jitter(F, 0.5)), F, exposure=1.2)

@functools.lru_cache(None)
def serpentine_path(y0=540, amp=300, pitch=420, x0=-300, x1=2220):
    """n u n u: the channel family unrolled into a meander."""
    pts = []; x = x0; up = True
    while x < x1:
        r = pitch / 4
        for a in np.linspace(math.pi, 0, 18) if up else np.linspace(math.pi, 2 * math.pi, 18):
            pts.append((x + r + r * math.cos(a), (y0 - amp if up else y0 + amp) - (r * math.sin(a) if up else -r * math.sin(a) * -1)))
        nxt = (x + 2 * r, y0 + amp if up else y0 - amp)
        pts.append(nxt); x += 2 * r; up = not up
    p = np.array(pts, np.float32)
    # resample by arc length
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1); cum = np.r_[0, np.cumsum(seg)]; u = np.linspace(0, cum[-1], 3000)
    return np.c_[np.interp(u, cum, p[:, 0]), np.interp(u, cum, p[:, 1])]

def serpentine(f, n, F, flatten=0.0):
    p = serpentine_path().copy()
    if flatten > 0: p[:, 1] = 540 + (p[:, 1] - 540) * (1 - flatten)
    m = lines_mask([p], 70)
    h = metal() * 0.3 + blur(m, 10) * 0.6
    dif, spc = shade(h, (-0.6, -0.5, 0.45), 10, spec=60, spec_k=1.6)
    img = tint(dif * 0.10 + 0.02, hexc('2a2440')) + tint(blur(m, 2) * (0.25 + dif * 0.9), hexc('c8763e')) + tint(spc * m, hexc('ffe9cc'))
    s = (F % 40) / 40 * 1.5
    head = lines_mask([p[int(3000 * clamp(s - 0.08)):max(2, int(3000 * clamp(s)))]], 30)
    img += tint(blur(head, 16) * 1.0, FURNACE) + tint(blur(head, 4) * 0.8, hexc('fff1d6'))
    img = oblique_(img, 0.18)
    img = tilt_dof(img, 520, depth=200, max_sigma=14)
    return develop(move(img, *jitter(F, 0.7)), F, exposure=1.25)

def oblique_(img, k):
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[W * k, -H * 0.15], [W * (1 - k), -H * 0.15], [W * (1 + k * 0.5), H * 1.05], [-W * k * 0.5, H * 1.05]])
    return cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

# ------------------------------------------------------------------ 12 · weave
def weave(f, n, F, k=1.0):
    """Macro of a woven textile; one copper weft carries the pulse."""
    yy, xx = _grid(); X = (xx / S - 960) / k + 960; Y = (yy / S - 540) / k + 540
    pw = 150.0; ph = 128.0
    wi = np.floor(X / pw); wj = np.floor(Y / ph)
    over_ = ((wi + wj) % 2 == 0)
    fx = (X % pw) / pw; fy = (Y % ph) / ph
    warp_r = np.sqrt(np.clip(1 - ((fx - 0.5) / 0.44) ** 2, 0, 1))
    weft_r = np.sqrt(np.clip(1 - ((fy - 0.5) / 0.42) ** 2, 0, 1))
    # each crossing: the upper thread humps over the lower
    warp_h = warp_r * (0.6 + 0.4 * np.where(over_, np.sin(fy * math.pi), 1 - np.sin(fy * math.pi)))
    weft_h = weft_r * (0.6 + 0.4 * np.where(over_, 1 - np.sin(fx * math.pi), np.sin(fx * math.pi)))
    top_warp = warp_h > weft_h
    h = np.maximum(warp_h, weft_h).astype(np.float32)
    twist = np.where(top_warp, np.sin((Y * 0.35 + X * 0.12)), np.sin((X * 0.35 + Y * 0.12))) * 0.05
    dif, spc = shade(h * 0.6 + twist, (-0.6, -0.55, 0.45), 8, spec=40, spec_k=0.9)
    copper_row = (wj == 4) & ~top_warp
    base = np.where(copper_row[..., None], hexc('b06a3a'), np.where(top_warp[..., None], hexc('2a2622'), hexc('3b342c')))
    img = base * (0.15 + dif[..., None] * 1.1) + tint(spc * (0.4 + 0.8 * copper_row), hexc('ffe2c4'))
    img *= (0.25 + 0.75 * np.clip(h, 0, 1))[..., None]
    s = ((F % 36) / 36) * 2400 - 200
    pulse = np.exp(-((X - s) / 120) ** 2) * copper_row
    img += tint(blur(pulse.astype(np.float32), 6) * 1.6, FURNACE) + tint(pulse * 1.4, hexc('fff0d4'))
    img = oblique_(img, 0.14)
    img = tilt_dof(img, 600, depth=200, max_sigma=13)
    return develop(move(img, *jitter(F, 0.6)), F, exposure=1.6)

# ------------------------------------------------------------------ 13 · telegraph
def wire(f, n, F):
    yy, xx = _grid(); X = xx / S; Y = yy / S
    sky = np.clip(1 - Y / 1080, 0, 1)
    img = tint(sky ** 2.2 * 0.6, AMBER) + tint(sky ** 6 * 0.6, hexc('ffe0b0')) + tint((1 - sky) * 0.05, hexc('302018'))
    vol, _ = dust_volume(F, light=(1300, 980), k=0.6, ray=0.6); img += vol * 0.7
    y = 380 + (X - 960) * 0.12 + 0.00012 * (X - 960) ** 2       # a sagging span
    d = np.abs(Y - y); m = np.exp(-(d / 2.4) ** 2)
    img = img * (1 - m[..., None] * 0.95)
    s = -200 + (f / n) * 2600
    sp = np.exp(-((X - s) / 40) ** 2) * np.exp(-(d / 6) ** 2)
    img += tint(blur(sp.astype(np.float32), 6) * 2.5, hexc('fff0d0')) + tint(blur(sp.astype(np.float32), 30) * 1.5, FURNACE)
    ins = circle_mask(1460, 380 + 500 * 0.12 + 0.00012 * 500 ** 2 + 16, 22); img = img * (1 - ins[..., None] * 0.9) + tint(ins * 0.08, hexc('8ab0a0'))
    img = defocus(img, 0.6)
    return develop(move(img, *jitter(F, 0.9)), F, exposure=1.2)

@functools.lru_cache(None)
def morse_marks():
    """(start, length) along the tape in px for b r n r d."""
    out = []; x = 0; dot = 70
    for ch in 'brnrd':
        for sym in MORSE[ch]:
            L = dot if sym == '.' else dot * 3
            out.append((x, L)); x += L + dot
        x += dot * 3
    return out, x

def register(f, n, F):
    """Paper tape under a stylus. Each pulse presses a mark; no ink, only relief under raking light."""
    marks, total = morse_marks()
    speed = (total + 400) / (n - 20)
    off = f * speed - 200                                # tape advance: marks move left past the stylus at x=1200
    sx = 1200
    yy, xx = _grid(); X = xx / S; Y = yy / S
    tape = np.clip((np.minimum(Y - 150, 940 - Y)) / 6, 0, 1).astype(np.float32)
    h = fbm(91, 5, 50) * 0.05
    em = np.zeros((H, W), np.float32)
    for (a, L) in marks:
        xa = sx - (off - a)                              # mark's screen x once pressed
        if off - a < 0: continue                         # not yet under the stylus
        xb = min(sx, xa + L)
        em = np.maximum(em, rect_mask(xa, 470, xb, 620))
    h = h - blur(em, 7) * 0.9 + blur(em, 22) * 0.16
    dif, spc = shade(h, (-0.9, -0.2, 0.25), 18, spec=30, spec_k=0.5)
    paper = tint(tape * (0.15 + dif * 0.85), hexc('e6dcc6')) + tint(tape * spc, WHITE) * 0.4
    img = paper + tint((1 - tape) * 0.03, hexc('3a2a20'))
    yy0 = np.exp(-((Y - 360) / 60) ** 2) * tape.max()
    # the stylus: a dark arm from above, lowering on a pulse
    pressing = any(0 <= off - a < L for (a, L) in marks)
    tip = 470 if pressing else 400
    arm = poly_mask([(sx - 40, -10), (sx + 40, -10), (sx + 12, tip), (sx - 12, tip)])
    img = img * (1 - arm[..., None] * 0.95) + tint(arm * 0.05, hexc('8a8f98'))
    if pressing: img += tint(blur(circle_mask(sx, tip + 10, 10), 10) * 2.5, FURNACE)
    img += tint(np.exp(-((X - 1900) / 900) ** 2) * tape * 0.18, AMBER)
    img = tilt_dof(img, 545, depth=260, max_sigma=8)
    return develop(move(img, *jitter(F, 0.4)), F, exposure=1.35)

def build():
    E = []; A = E.append
    A((60, tag(press, 'press:22')))
    A((26, tag(conduct, 'hum')))
    A((2, lambda f, n, F: photomask(F)))
    A((20, wafer))
    A((1, lambda f, n, F: canvas(BLACK)))
    A((34, tag(lambda f, n, F: serpentine(f, n, F, flatten=ss(22, 34, f) * 0.8), 'hum')))
    A((52, tag(lambda f, n, F: weave(f, n, F, k=lerp(1.25, 1.0, f / n)), 'hum')))
    A((26, tag(wire, 'spark')))
    A((2, lambda f, n, F: invert(develop(weave(0, 1, F), F))))
    A((104, tag(register, 'morse')))
    A((18, tag(lambda f, n, F: canvas(BLACK), 'silence')))
    return E
