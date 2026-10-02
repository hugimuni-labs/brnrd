"""Shots. Each is a generator that steps its physics every local frame and
yields developed 8-bit frames only for the local indices the edit needs.

Physics runs continuously inside a shot even while the edit is cut away to an
insert, so returning to a shot returns to a world that kept moving.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

import optics as O
from glyphs import LEXICON, SKELETONS, Placement, glyph_distance, place, resample
from optics import (AMBER, BONE, COPPER, FURNACE, PHOSPHOR, STEEL, WHITEHOT, W, H,
                    Glass, develop, splat, tint, up)
from phys import fbm, sample, smoothstep
from phys import breakdown as BD
from phys.chladni import Membrane
from phys.field import Filings, poles_px
from phys.holo import Aperture
from phys.mach import Wake
from phys.press import Relief
from phys.rd import Reaction
from phys.swarm import Dust, potential

SERIF = "/usr/share/fonts/truetype/freefont/FreeSerif.ttf"
RUNEMONO = "/usr/share/fonts/opentype/unifont/unifont.otf"  # covers runes and ⤙


def bump(k, a, b, ramp=4):
    """0→1→0 window over [a, b] with soft edges."""
    return float(smoothstep(a - ramp, a, k) * (1 - smoothstep(b, b + ramp, k)))


def blur(img, s):
    return cv2.GaussianBlur(img, (0, 0), s)


def shake_img(img, dx, dy):
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REFLECT)


# ── 1. before the symbol: dust in a god ray ────────────────────────────────


def dust(need, seed=1):
    L = max(need) + 1
    d = Dust(110_000, W, H, seed)
    d.lum = d.rng.lognormal(0, 1.3, len(d.lum)).astype(np.float32)
    ray = O.god_ray(W, H, (-220, -260), 0.62, 150, 0.7)
    ray2 = O.god_ray(W, H, (W + 200, -300), 2.25, 60, 1.2)
    Fb = potential("b", Placement(1180, 560, 760), W, H, 0.04, 3)
    Fr = potential("r", Placement(700, 520, 700), W, H, 0.04, 4)
    m = Membrane(160_000, seed=5)
    m.set_field(m.plate(4, 9), 400)
    for _ in range(30):
        m.shake()
    plate_glimpse = blur(splat(m.p * 4), 1.2)
    for k in range(L):
        wb = 0.35 * bump(k, 28, 38, 5)
        wr = 0.35 * bump(k, 72, 82, 4)
        T = 0.6 + 0.4 * (1 - 2 * (wb + wr))
        d.step([Fb, Fr], [wb, wr], T=T, wind=0.6, k=0.008)
        if k not in need:
            continue
        beam = sample(ray, d.p[:, 0], d.p[:, 1]) + 0.35 * sample(ray2, d.p[:, 0], d.p[:, 1]) * bump(k, 40, 999, 20)
        lum = d.lum * (0.006 + 2.0 * beam ** 1.5)
        I = splat(d.p, lum) + 0.45 * splat(d.prev, lum)
        I = blur(I, 0.8)
        haze = (0.03 * ray + 0.012 * ray2) * (1 + 0.12 * math.sin(k * 0.7) * math.sin(k * 0.23))
        hdr = tint(I * 0.6, AMBER) + tint(haze, AMBER)
        if k in (52, 53, 61):  # hidden structure flashes into visibility
            hdr += tint(plate_glimpse * (0.25 if k != 61 else 0.12), BONE)
        exp = 1.2 * smoothstep(0, 24, k)
        if k in (52, 61):
            exp *= 2.5
        yield k, develop(hdr, exposure=exp, frame=k, grain=0.07, seed=seed, bloom_k=0.5)


# ── 2. first glowing bend: a crack in dark rock commits to r ───────────────

CRACK_PL = Placement(860, 590, 900)


def crack_trees():
    main = BD.grow("r", CRACK_PL, seed=0, step=7, influence=60, kill=9, density=7)
    ghost = BD.grow("r", CRACK_PL, seed=3, step=7, influence=60, kill=9, density=7)
    return main, ghost


def crack(need, seed=2):
    L = max(need) + 1
    main, ghost = crack_trees()
    rock = Relief("r", CRACK_PL, W // 2, H // 2, seed=9)
    rock_sh = up(rock.shade(0.0, light=(0.8, -0.4)))
    rng = np.random.default_rng(seed)
    for k in range(L):
        if k not in need:
            continue
        # Leader: hesitates (stalls), then commits.
        prog = np.interp(k, [6, 16, 22, 30, 38], [0, 0.35, 0.42, 0.8, 1.0])
        upto = main.steps * prog
        rs = 1.0 if k in (39, 40) else (0.5 if k == 41 else 0.0)
        cool = math.exp(-max(0, k - 41) / 14)
        I = BD.draw(main, W, H, upto, tremble=0.5, seed=k, return_stroke=rs,
                    core=0.6 + 0.6 * cool)
        g = BD.draw(ghost, W, H, ghost.steps * np.interp(k, [0, 14], [0, 0.7]), tremble=0.6, seed=k + 99, core=0.5)
        I = I + g * max(0, 1 - k / 22)
        core = blur(I, 1.0)
        halo = blur(I, 7) * 2.2 + blur(I, 40) * 3.0
        light = blur(I, 60) * 6 + 0.012
        hot = WHITEHOT * (0.5 + 0.5 * cool) + FURNACE * (1 - cool)
        hdr = tint(core, hot) + tint(halo, FURNACE) + tint(rock_sh * light, COPPER)
        exp = 1.3 + (3.0 if rs >= 1 else 0)
        dx, dy = (rng.normal(0, 6, 2) if rs else (0, 0))
        img = develop(shake_img(hdr, dx, dy), exposure=exp, frame=k, seed=seed, grain=0.06,
                      bloom_k=0.45, negative=(k == 40))
        yield k, img


# ── 3. the same angle under another ontology: a supersonic wake ────────────


def mach(need, seed=3, up_=False, trail=0.0, glyph="V"):
    L = max(need) + 1
    w = Wake(640, 360, c=2.6, seed=seed)
    v = 4.6
    p0 = (318, -30) if not up_ else (322, 400)
    vel = (0.0, v) if not up_ else (0.0, -v)
    theta = math.degrees(math.asin(2.6 / v))
    for k in range(L):
        if k not in need:
            continue
        t = 40 + k * 1.15
        P = w.field(p0, vel, t, emit_dt=0.55, history=70, width=1.2, trail=trail)
        S = Wake.schlieren(P)
        S = cv2.resize(S, (W, H), interpolation=cv2.INTER_CUBIC)
        base = 0.32 + 0.04 * math.sin(k * 1.7)
        I = np.clip(base + 3.2 * S, 0, None)
        hdr = tint(I, AMBER) * 1.1
        gl = Glass(seed + k)
        sx, sy = (p0[0] + vel[0] * t) * 3, (p0[1] + vel[1] * t) * 3
        if 8 < k:
            gl.text(sx + 40, sy - 20 if not up_ else sy + 10, f"θ = asin(c/v) = {theta:.1f}°", 20, 190, col=(30, 18, 8))
            gl.text(60, 60, "SCHLIEREN · KNIFE-EDGE 0°", 16, 160, col=(30, 18, 8))
            gl.text(60, 84, f"v/c = {v / 2.6:.2f}", 16, 160, col=(30, 18, 8))
        gl.frame_count(k, 120)
        yield k, develop(hdr, exposure=1.6, frame=k, seed=seed, grain=0.08, overlay=gl.array(),
                         vignette=0.5, bloom_k=0.2)


# ── 4. filings: one field, two readings ────────────────────────────────────


def field(need, seed=4, glyph="n", twin="u"):
    L = max(need) + 1
    f = Filings(960, 540, 0.11, seed)
    pl = Placement(480, 280, 420)
    P = poles_px(glyph, pl)
    d = glyph_distance(glyph, 960, 540, pl, 0.02, seed)
    d2 = glyph_distance(twin, 960, 540, pl, 0.02, seed)
    paper = 0.82 + 0.06 * fbm(960, 540, seed + 30, 6, 8)
    yy = np.linspace(0, 1, 540, dtype=np.float32)[:, None] * np.ones((1, 960), np.float32)
    rng = np.random.default_rng(seed)
    for k in range(L):
        if k % 3 == 0:
            f.tap(0.22 if k % 12 else 0.5)
        if k not in need:
            continue
        jit = [(x + rng.normal(0, 0.8), y + rng.normal(0, 0.8), q) for x, y, q in P]
        lic, mag = f.render(jit, steps=16, h=0.9)
        # Where the light falls decides the reading: top light → glyph, bottom → twin.
        s = smoothstep(0.35, 0.65, (k % 96) / 96.0 * 2 if k < 96 else 1.0)
        top = np.exp(-((yy - 0.25) / 0.35) ** 2)
        bot = np.exp(-((yy - 0.85) / 0.35) ** 2)
        light = (1 - s) * top + s * bot
        sel = (1 - s) * np.exp(-(d / 16) ** 2) + s * np.exp(-(d2 / 16) ** 2)
        ink = np.clip(lic * 9.0 * (0.5 + 1.2 * sel), 0, 1)
        I = paper * (0.06 + 0.9 * light) * (1 - 0.95 * ink)
        hdr = tint(up(I.astype(np.float32)), BONE * np.array([1.0, 0.93, 0.82], np.float32))
        gl = Glass(seed + k, col=(40, 30, 22))
        for (x, y, q) in P:
            gl.text(x * 2 - 8, y * 2 + 26, "N" if q > 0 else "S", 22, 200, path=RUNEMONO)
        reading = glyph if s < 0.5 else twin
        gl.text(80, H - 90, f"{glyph} | {twin}   one field, read from either side", 20, 200, path=RUNEMONO)
        gl.text(80, H - 64, f"reading: {reading}   (light from {'above' if s < 0.5 else 'below'})", 16, 170, path=RUNEMONO)
        gl.ruler(80, 60, W - 80, 60, 60, 120)
        gl.frame_count(k, 140)
        yield k, develop(hdr, exposure=1.3, frame=k, seed=seed, grain=0.07, overlay=gl.array(),
                         vignette=0.6, bloom_k=0.15)


# ── 5. the membrane: shaken into legibility ────────────────────────────────

CHL_PL = Placement(232, 140, 190)  # in membrane coords (480×270)


def chladni(need, seed=5):
    L = max(need) + 1
    m = Membrane(220_000, seed=seed)
    Cb = m.coeffs("b", CHL_PL, width=5.0, wobble=0.03, seed=seed)
    Cd = Cb * m.parity_x
    # Placement is centred on the plate, so y-parity maps b to p only around the plate centre.
    Cp = Cb * m.parity_y
    Cq = Cb * m.parity_x * m.parity_y
    modes = [(2, 5), (3, 7), (4, 9), (6, 11), (5, 13)]
    plate = [m.plate(a, b) for a, b in modes]
    norm = lambda C: C / (np.abs(C).max() + 1e-9)
    Cb, Cd, Cp, Cq = map(norm, (Cb, Cd, Cp, Cq))
    plate = [norm(p) for p in plate]
    sheen = fbm(W // 2, H // 2, seed + 40, 5, 5)
    ray = O.god_ray(W, H, (W + 300, 100), 2.9, 260, 0.5)
    for k in range(L):
        kick = 0.0
        if k < 46:
            i = min(k // 9, len(modes) - 1)
            C, K, label = plate[i], 400, f"MODE ({modes[i][0]},{modes[i][1]})"
            if k % 9 == 0:
                kick = 3.0
        elif k < 96:
            a = smoothstep(46, 86, k)
            C = (1 - a) * plate[-1] + a * Cb
            K = 9 + 18 * smoothstep(46, 92, k)
            label = "MODE (5,13) → b"
        elif k < 118:
            C, K, label = Cb, 27, "b"
        elif k < 150:
            w_ = smoothstep(120, 146, k)
            C, K = (1 - w_) * Cb + w_ * Cd, 27
            label = "b → d  PARITY x" if 0.3 < w_ < 0.7 or w_ < 0.3 else "d"
        else:
            seq = [(Cd, "d"), (Cq, "q"), (Cp, "p"), (Cb, "b")]
            j = min((k - 150) // 6, 3)
            C, label = seq[j][0], seq[j][1]
            K = 27
            if (k - 150) % 6 == 0:
                kick = 4.0
        m.set_field(C, K)
        amp = 2.0 + 0.6 * math.sin(k * 0.9)
        m.shake(amp=amp, kick=kick)
        if k not in need:
            continue
        p, pp = m.grains_full(W, H)
        lum = 0.5 + m.rng.random(len(p)).astype(np.float32) ** 6 * 4  # metallic sparkle
        # Grains on an antinode are airborne: motion-blurred, they read as haze.
        lum *= np.exp(-2.2 * sample(m.U, p[:, 0] * m.w / W, p[:, 1] * m.h / H))
        I = splat(p, lum) + 0.5 * splat(pp, lum * 0.6)
        I = blur(I, 0.7)
        U = up(m.U)
        surf = 0.006 + 0.004 * up(sheen) + 0.012 * U * (0.5 + 0.5 * math.sin(k * 2.3))
        light = 0.25 + 1.8 * ray
        hdr = tint(I * 0.32 * light, BONE * np.array([1, 0.85, 0.65], np.float32)) + tint(surf * light, COPPER)
        if kick:
            hdr *= 2.2
        gl = Glass(seed + k)
        K_shown = K if K < 100 else 0
        gl.text(70, 60, f"DRIVE {0.42 + 0.06 * (K if K < 100 else 8 + 4 * (k // 9)):.2f} kHz", 16, 170)
        gl.text(70, 84, f"MODES n²+m² < K²   K = {K_shown:4.1f}" if K_shown else "MODES  free plate", 16, 170)
        gl.text(70, 108, label, 16, 190, path=RUNEMONO)
        if 118 <= k < 150:
            mid = 960
            gl.line([(mid, 140), (mid, H - 140)], 90)
            gl.text(mid + 10, 140, "x → −x", 14, 150)
        gl.frame_count(k, 140)
        yield k, develop(hdr, exposure=1.5, frame=k, seed=seed, grain=0.06, overlay=gl.array(),
                         bloom_k=0.4, negative=(kick > 3.5 and k % 2 == 0))


# ── 6. cavities: a reaction that can only live in the form ─────────────────


def cavity(need, seed=6, glyph="o", then="c"):
    L = max(need) + 1
    pl = Placement(240, 140, 210)
    r = Reaction(glyph, pl, seed=seed, width=9.0)
    sheen = up(fbm(480, 270, seed + 2, 5, 6))
    for _ in range(25):
        r.step(24)
    for k in range(L):
        if k == 34:
            r.retarget(then, pl, width=9.0)
        V = r.step(30, agitate=0.01 if k % 7 == 0 else 0.0)
        if k not in need:
            continue
        hgt = blur(up(V), 1.5)
        gy, gx = np.gradient(hgt * 30)
        dif = np.clip(0.6 - 0.7 * gx - 0.5 * gy, 0, None)
        I = hgt * dif
        hdr = tint(I * 1.1, COPPER) + tint(np.clip(hgt - 0.25, 0, None) * 0.5 * dif ** 4, WHITEHOT) \
            + tint((0.004 + 0.003 * sheen) * np.ones_like(I), STEEL)
        gl = Glass(seed + k)
        gl.text(70, H - 70, f"F={r.F.max():.4f}  k={r.k.min():.4f}   {glyph} → {then}", 16, 160, path=RUNEMONO)
        yield k, develop(hdr, exposure=1.6, frame=k, seed=seed, grain=0.07, overlay=gl.array(), bloom_k=0.35)


# ── 7. math inserts ───────────────────────────────────────────────────────

EQUATIONS = [
    "∇⁴w − β⁴w = 0",
    "sin θ = c / v",
    "∂v/∂t = D∇²v + uv² − (F+k)v",
    "B = Σ qᵢ (r − rᵢ) / |r − rᵢ|²",
    "∇·(σ∇φ) = 0",
    "U(z) = 𝓕⁻¹[𝓕[U₀]·e^{ikz√(1−λ²f²)}]",
    "dv = −γv dt − ∇V dt + √(2γT) dW",
    "(−1)ⁿ",
]


def math_cards(need, seed=7):
    for k in sorted(need):
        rng = np.random.default_rng(seed * 31 + k)
        eq = EQUATIONS[k % len(EQUATIONS)]
        gl = Glass(seed + k, col=(236, 224, 200))
        size = int(rng.choice([70, 96, 140]))
        x = rng.uniform(-200, 500)
        y = rng.uniform(250, 800)
        gl.text(x, y, eq, size, 255, path=SERIF)
        gl.regmark(rng.uniform(100, W - 100), rng.uniform(100, H - 100), 18, 200)
        gl.ruler(0, y + size * 1.3, W, y + size * 1.3, 80, 160)
        bg = rng.choice([0, 1, 2])
        base = [np.zeros((H, W, 3), np.float32), tint(np.full((H, W), 0.05, np.float32), AMBER),
                tint(np.full((H, W), 0.02, np.float32), PHOSPHOR)][bg]
        yield k, develop(base, exposure=1.0, frame=k, seed=seed, grain=0.09, overlay=gl.array(),
                         negative=bool(k % 5 == 3))


# ── 8. the stare: coherent light, focus as observation ─────────────────────


def holo(need, seed=8, glyph="r"):
    L = max(need) + 1
    pl = Placement(CRACK_PL.cx / 2, CRACK_PL.cy / 2, CRACK_PL.size / 2)
    a = Aperture(glyph, pl, 960, 540, seed, slot=2.6)
    t = 0.0
    lock = 48
    for k in range(L):
        z = float(np.interp(k, [0, 20, 34, 42, 48, 200], [260, 120, -30, 25, 6, 5]))
        t += 0.16 if k < lock else 0.025
        if k not in need:
            continue
        I = a.intensity(z, t)
        I = up(I)
        hdr = tint(I * 1.1, AMBER) + tint(blur(I, 30) * 0.6, FURNACE)
        gl = Glass(seed + k)
        # The reticle hunts, then locks; the speckle settles when it does.
        tx, ty = CRACK_PL.cx + 20, CRACK_PL.cy - 40
        if k < lock:
            e = (lock - k) / lock
            rx = tx + 260 * e * math.sin(k * 0.37)
            ry = ty + 180 * e * math.cos(k * 0.29)
        else:
            rx, ry = tx, ty
        gl.reticle(rx, ry, 300, 160)
        if k >= lock:
            gl.brackets(tx - 330, ty - 380, tx + 300, ty + 380, 34, 230, 2)
            gl.text(tx + 320, ty - 380, f"SPECIMEN 0x72 · {glyph}", 18, 220, path=RUNEMONO)
            gl.text(tx + 320, ty - 354, "registered", 16, 180)
        gl.text(70, 60, f"z = {z / 1000:+.3f} m", 16, 170)
        gl.text(70, 84, "λ = 589 nm  Na D", 16, 150)
        gl.frame_count(k, 130)
        flash = 2.4 if k == lock else 1.0
        yield k, develop(hdr, exposure=1.7 * flash, frame=k, seed=seed, grain=0.06, overlay=gl.array(),
                         bloom_k=0.55)


# ── 9. specimen → drawing: the crack, printed on translucent film ──────────


def specimen(need, seed=9):
    L = max(need) + 1
    main, _ = crack_trees()
    ink = BD.draw(main, W, H, main.steps, core=1.0, tip_glow=0.0, prune=1, return_stroke=0.6)
    ink = np.clip(blur(ink, 1.8) * 4.5, 0, 1)
    wr = fbm(W // 2, H // 2, seed + 50, 6, 3)
    gy, gx = np.gradient(cv2.GaussianBlur(wr, (0, 0), 3) * 30)
    wrinkle = up(np.clip(0.92 + 0.12 * gx - 0.05 * gy, 0.7, 1.15).astype(np.float32))
    tone = up(0.5 + 0.5 * fbm(W // 4, H // 4, seed + 51, 5, 30))
    back = O.god_ray(W, H, (W / 2, -800), math.pi / 2, 900, 0.2)
    for k in range(L):
        if k not in need:
            continue
        drift = 0.6 * k
        a = shake_img(ink, drift + 0, 0)
        b = shake_img(ink, drift + 4.0, 2.5)
        film = 0.22 + 0.45 * back
        bar = np.exp(-((np.arange(W, dtype=np.float32)[None, :] - (k - 14) * 110) / 60) ** 2)
        film = film * wrinkle + 1.6 * bar * (14 <= k <= 34)
        I_rgb = tint(film, BONE)
        I_rgb *= (1 - 0.97 * (a * (0.85 + 0.15 * tone))[..., None])
        I_rgb *= (1 - 0.45 * b[..., None] * (1 - np.array([0.25, 0.55, 0.85], np.float32)))
        gl = Glass(seed + k, col=(40, 28, 20))
        gl.text(1340, 160, "SPECIMEN 0x72", 26, 230)
        gl.text(1340, 196, "found in: breakdown, basalt", 16, 200)
        gl.text(1340, 220, "first branch 35.2°  (cf. sin θ = c/v)", 16, 200)
        gl.text(1340, 244, "read as: r  ·  family: fork", 16, 200, path=RUNEMONO)
        gl.text(120, H - 120, "fig. 2", 22, 210, path=SERIF)
        gl.ruler(120, H - 80, W - 120, H - 80, 100, 170)
        for x, y in ((90, 90), (W - 90, 90), (90, H - 160), (W - 90, H - 160)):
            gl.regmark(x, y, 16, 210)
        yield k, develop(I_rgb, exposure=1.25, frame=k, seed=seed, grain=0.05, overlay=gl.array(),
                         vignette=0.5, bloom_k=0.1)


# ── 10. the barrage: every physics hunts every glyph ───────────────────────

PALETTES = {
    "amber": (np.zeros(3, np.float32), AMBER),
    "furnace": (np.zeros(3, np.float32), FURNACE),
    "bone": (BONE * 0.85, np.array([0.08, 0.06, 0.05], np.float32)),
    "phosphor": (np.zeros(3, np.float32), PHOSPHOR),
    "copper": (COPPER * 0.12, WHITEHOT),
    "steel": (STEEL * 0.05, BONE),
}


def find(phys, g, seed, pl_full: Placement):
    """A finding: scalar light image (H×W) of glyph g as physics `phys` makes it."""
    if phys == "chladni":
        m = Membrane(150_000, seed=seed)
        k = m.w / W
        pl = Placement(pl_full.cx * k, pl_full.cy * k, pl_full.size * k, pl_full.rot)
        m.set_field(m.coeffs(g, pl, 5.0, 0.03, seed), 26)
        for _ in range(34):
            m.shake()
        return np.clip(blur(splat(m.p * (1 / k)), 0.8) * 0.4, 0, 3)
    if phys == "breakdown":
        if g == "r":  # seeds whose leader commits cleanly (the others wander)
            seed = (0, 1, 5)[seed % 3]
        tr = BD.grow(g, pl_full, seed=seed, step=7, influence=60, kill=9, density=7)
        I = BD.draw(tr, W, H, tr.steps, return_stroke=0.4)
        return blur(I, 1.2) + blur(I, 10) * 0.8
    if phys == "field":
        f = Filings(960, 540, 0.11, seed)
        pl = Placement(pl_full.cx / 2, pl_full.cy / 2, pl_full.size / 2)
        lic, _ = f.render(poles_px(g, pl), 16)
        d = glyph_distance(g, 960, 540, pl, 0.02, seed)
        return up(np.clip(lic * 9 * (0.3 + 1.2 * np.exp(-(d / 12) ** 2)), 0, 2).astype(np.float32))
    if phys == "rd":
        pl = Placement(pl_full.cx / 4, pl_full.cy / 4, pl_full.size / 4)
        r = Reaction(g, pl, seed=seed, width=7.0)
        for _ in range(45):
            V = r.step(24)
        return up(blur(V, 0.6) * 1.5)
    if phys == "holo":
        pl = Placement(pl_full.cx / 2, pl_full.cy / 2, pl_full.size / 2)
        a = Aperture(g, pl, 960, 540, seed, 2.6)
        return up(a.intensity(18 + 30 * (seed % 3), seed * 0.7) * 1.3)
    if phys == "swarm":
        d = Dust(110_000, W, H, seed)
        F = potential(g, pl_full, W, H, 0.03, seed)
        for _ in range(45):
            d.step([F], [1.0], T=0.25, wind=0.15)
        return blur(splat(d.p, d.lum) * 0.5, 0.8)
    if phys == "mach":
        w = Wake(640, 360, 2.6, seed)
        cx, cy = pl_full.cx / 3, pl_full.cy / 3
        upw = g == "^"
        vel = (0, -4.6) if upw else (0, 4.6)
        t = 50.0
        p0 = (cx, cy + 4.6 * t - 60) if upw else (cx, cy - 4.6 * t + 70)
        P = w.field(p0, vel, t, emit_dt=0.55, history=70, width=1.2, trail=5.0 if g == "Y" else 0)
        return up(np.abs(Wake.schlieren(P)) * 0.9)
    if phys == "press":
        pl = Placement(pl_full.cx / 2, pl_full.cy / 2, pl_full.size / 2)
        r = Relief(g, pl, 960, 540, seed)
        return up(r.shade(1.0) * 0.8 + r.heat(1.2))
    raise KeyError(phys)


def barrage(need, cards, seed=10):
    """`cards`: local index → (phys, glyph, palette, placement, flags)."""
    for k in sorted(need):
        phys, g, pal, pl, flags = cards[k]
        I = find(phys, g, seed + k, pl)
        bg, fg = PALETTES[pal]
        if pal == "bone":
            hdr = (bg[None, None, :] * (1 - np.clip(I, 0, 1)[..., None] * 0.95)).astype(np.float32)
            hdr = hdr + tint(np.clip(I - 1, 0, None) * 0.0, fg)
        else:
            hdr = bg[None, None, :] + tint(I, fg)
        gl = Glass(seed + k)
        col = (40, 28, 20) if pal == "bone" else None
        if "label" in flags:
            et = LEXICON.get(g)
            gl.text(70, H - 100, f"{g}  ←  {phys}", 30, 230, col=col, path=RUNEMONO)
            if et and et.physics == phys:
                gl.text(70, H - 62, et.reading, 16, 200, col=col)
        if "reg" in flags:
            gl.brackets(pl.cx - pl.size * 0.4, pl.cy - pl.size * 0.5, pl.cx + pl.size * 0.4, pl.cy + pl.size * 0.5, 30, 220)
        yield k, develop(hdr, exposure=1.5, frame=k, seed=seed, grain=0.08, overlay=gl.array(),
                         negative="neg" in flags, bloom_k=0.4)


# ── 11/12. the press, then the current ─────────────────────────────────────

PRESS_PL = Placement(470, 285, 430)  # relief coords (960×540)


def press(need, seed=11, glyph="r"):
    L = max(need) + 1
    r = Relief(glyph, PRESS_PL, 960, 540, seed)
    rng = np.random.default_rng(seed)
    impact = 12
    sparks = None
    for k in range(L):
        if k not in need:
            continue
        if k < impact:
            depth = 0.0
            shadow = 1 - 0.8 * smoothstep(0, impact, k)
            heat = 0.0
            sh = rng.normal(0, 0.4 + 2.5 * k / impact, 2)
        else:
            j = k - impact
            depth = 1.0 + 0.15 * math.exp(-j / 2) * math.cos(j * 2.2)
            shadow = 1.0
            heat = 2.6 * math.exp(-j / 16)
            sh = rng.normal(0, 40 * math.exp(-j / 3.5), 2)
        S = up(r.shade(depth, light=(-0.7, -0.45)))
        Hh = up(r.heat(1.0)) * heat
        hdr = tint(S * shadow * 0.07, STEEL * 0.6 + COPPER * 0.4) + tint(Hh, FURNACE) + tint(blur(Hh, 25) * 0.6, FURNACE)
        if k >= impact:
            j = k - impact
            if sparks is None:
                n = 1600
                pts = resample(place(SKELETONS[glyph], Placement(PRESS_PL.cx * 2, PRESS_PL.cy * 2, PRESS_PL.size * 2)), 4)
                sel = pts[rng.integers(0, len(pts), n)]
                ang = rng.uniform(0, 2 * math.pi, n)
                spd = rng.lognormal(2.2, 0.6, n)
                sparks = (sel, np.stack([np.cos(ang), np.sin(ang)], 1) * spd[:, None])
            p0, v0 = sparks
            pos = p0 + v0 * j + np.array([0, 0.9]) * j * j
            prev = p0 + v0 * max(0, j - 1) + np.array([0, 0.9]) * max(0, j - 1) ** 2
            lum = np.full(len(pos), 3.0 * math.exp(-j / 6), np.float32)
            sp = splat(pos, lum) + splat((pos + prev) / 2, lum) + splat(prev, lum * 0.5)
            hdr += tint(blur(sp, 1.0), WHITEHOT * 0.5 + FURNACE * 0.5)
        hdr = shake_img(hdr, *sh)
        flash = 6.0 if k == impact else 1.0
        yield k, develop(hdr, exposure=1.6 * flash, frame=k, seed=seed, grain=0.07, bloom_k=0.45,
                         weave=0.6 + (3 if impact <= k < impact + 6 else 0))


def current(need, seed=12, glyph="r"):
    L = max(need) + 1
    r = Relief(glyph, PRESS_PL, 960, 540, seed=11)
    S = up(r.shade(1.0, light=(-0.7, -0.45)))
    base_heat = up(r.heat(1.0))
    # Manhattan traces leaving the stroke ends: the rune wired into a circuit.
    polys = place(SKELETONS[glyph], Placement(PRESS_PL.cx * 2, PRESS_PL.cy * 2, PRESS_PL.size * 2))
    traces = np.zeros((H, W), np.float32)
    rng = np.random.default_rng(seed)
    for p in polys:
        for end in (p[0], p[-1]):
            x, y = end
            pts = [(x, y)]
            for _ in range(3):
                if rng.random() < 0.5:
                    x += rng.choice([-1, 1]) * rng.uniform(80, 400)
                else:
                    y += rng.choice([-1, 1]) * rng.uniform(80, 300)
                pts.append((x, y))
            cv2.polylines(traces, [np.int32(pts)], False, 1.0, 3)
    traces = blur(traces, 1.0)
    for k in range(max(need) + 1):
        if k not in need:
            continue
        s = (k % 16) / 12.0
        C = up(r.current(s, 0.05, 0.25))
        tr_on = smoothstep(10, 36, k)
        hdr = tint(S * 0.06, STEEL * 0.6 + COPPER * 0.4) + tint(base_heat * 0.25, FURNACE) \
            + tint(C * 3.0, WHITEHOT) + tint(blur(C, 12) * 2, COPPER) + tint(traces * tr_on * 0.5, COPPER)
        gl = Glass(seed + k)
        if k > 20:
            gl.text(W - 360, 120, "Q1  · stem → gate", 16, 180)
            gl.text(W - 360, 144, "branch → drain", 16, 160)
        yield k, develop(hdr, exposure=1.5, frame=k, seed=seed, grain=0.06, overlay=gl.array(), bloom_k=0.5)


# ── 13. the word was always latent: b r n r d, each found by its own law ───

WORD = "brnrd"
CELL_X = [960 + (i - 2) * 330 for i in range(5)]
CELL_Y = 520
CELL_S = 400
WORD_PHYS = ["chladni", "breakdown", "field", "breakdown", "chladni"]
WORD_READ = ["nodal set", "breakdown", "field line", "breakdown", "parity of b"]


def word(need, seed=13):
    L = max(need) + 1
    pls = [Placement(x, CELL_Y, CELL_S) for x in CELL_X]
    # b and d share one membrane: superposed modes, one plate, two letters.
    m = Membrane(260_000, seed=seed)
    k_ = m.w / W
    pb = Placement(pls[0].cx * k_, pls[0].cy * k_, pls[0].size * k_)
    pd = Placement(pls[4].cx * k_, pls[4].cy * k_, pls[4].size * k_)
    db = glyph_distance("b", m.w, m.h, pb, 0.03, seed)
    dd = glyph_distance("d", m.w, m.h, pd, 0.03, seed + 1)
    A = 1 - np.exp(-(np.minimum(db, dd) / 4.0) ** 2)
    Cbd = cv2.dct(A.astype(np.float32))
    plate = m.plate(5, 13)
    plate = plate / np.abs(plate).max() * np.abs(Cbd).max()
    trees = [BD.grow("r", pls[1], seed=0, step=6, influence=50, kill=8, density=6),
             BD.grow("r", pls[3], seed=5, step=6, influence=50, kill=8, density=6)]
    f = Filings(960, 540, 0.16, seed)
    pn = Placement(pls[2].cx / 2, pls[2].cy / 2, pls[2].size / 2)
    poles = poles_px("n", pn)
    dn = up(glyph_distance("n", 960, 540, pn, 0.02, seed))
    win = np.zeros((H, W), np.float32)
    x0, x1 = CELL_X[2] - 150, CELL_X[2] + 150
    win[:, x0:x1] = 1
    win = blur(win, 60)
    rng = np.random.default_rng(seed)
    for k in range(L):
        a = smoothstep(4, 40, k)
        K = 8 + 22 * a
        kick = 3.0 if k in (6, 14, 22) else 0.0
        if k >= 96:
            kick = 0.5 * (k - 96)
        m.set_field((1 - a) * plate + a * Cbd, K)
        m.shake(amp=2.0 + 0.5 * math.sin(k * 1.3), kick=kick)
        if k % 3 == 0:
            f.tap(0.25)
        if k not in need:
            continue
        p, pp = m.grains_full(W, H)
        lum = 0.5 + m.rng.random(len(p)).astype(np.float32) ** 6 * 3
        lum *= np.exp(-4.0 * sample(m.U, p[:, 0] * m.w / W, p[:, 1] * m.h / H))
        G = blur(splat(p, lum) + 0.5 * splat(pp, lum * 0.6), 0.7) * 0.45
        mask = np.zeros(W, np.float32)
        for i in (0, 4):
            mask[max(0, CELL_X[i] - 165):CELL_X[i] + 165] = 1
        G *= blur(mask[None, :].repeat(8, 0), 60)[0][None, :]
        Bt = np.zeros((H, W), np.float32)
        for i, tr in enumerate(trees):
            prog = smoothstep(8 + 10 * i, 34 + 10 * i, k)
            rs = 1.0 if k in (34 + 10 * i, 35 + 10 * i) else 0.0
            if k >= 96:
                rs = 0.3 * (k % 2)
            Bt += BD.draw(tr, W, H, tr.steps * prog, tremble=0.5 + (k >= 96) * 2, seed=k + i, return_stroke=rs, prune=2)
        Bt = blur(Bt, 1.0) + blur(Bt, 8) * 1.2
        jit = [(x + rng.normal(0, 0.8), y + rng.normal(0, 0.8), q) for x, y, q in poles]
        lic, _ = f.render(jit, 14)
        Fn = up(lic) * 4.0 * (0.015 + 1.6 * np.exp(-(dn / 9) ** 2)) * win * smoothstep(10, 30, k)
        hdr = tint(G, BONE * np.array([1, 0.85, 0.65], np.float32)) + tint(Bt, FURNACE * 0.5 + WHITEHOT * 0.5) \
            + tint(Fn, AMBER)
        gl = Glass(seed + k)
        if 40 <= k < 104:
            gl.ruler(CELL_X[0] - 180, CELL_Y + 215, CELL_X[4] + 180, CELL_Y + 215, 100, 160)
            for i in range(5):
                gl.text(CELL_X[i], CELL_Y + 250, WORD_READ[i], 15, 190, anchor="ma")
                gl.text(CELL_X[i], CELL_Y + 272, WORD_PHYS[i], 13, 130, anchor="ma")
            gl.brackets(CELL_X[0] - 200, CELL_Y - 260, CELL_X[4] + 200, CELL_Y + 300, 40, 220, 2)
        gl.frame_count(k, 130)
        exp = 1.5 * (2.5 if kick >= 3 else 1.0) * (1 + 0.15 * (k >= 96) * (k % 2))
        img = develop(hdr, exposure=exp, frame=k, seed=seed, grain=0.06, overlay=gl.array(), bloom_k=0.45,
                      negative=(k in (6, 22) or (k >= 110 and k % 3 == 0)))
        yield k, img


# ── 14. weave → telegraph: the pulse leaves ────────────────────────────────

MORSE = {"b": "-...", "r": ".-.", "n": "-.", "d": "-.."}


def morse_train(word="brnrd", unit=2):
    seq, t = [], 0
    for ch in word:
        for s in MORSE[ch]:
            n = unit if s == "." else 3 * unit
            seq.append((t, n))
            t += n + unit
        t += 2 * unit
    return seq, t


def telegraph(need, seed=14):
    L = max(need) + 1
    seq, _ = morse_train()
    x = np.arange(W, dtype=np.float32)
    sag = 520 + 40 * ((x - W / 2) / (W / 2)) ** 2
    speed = 120.0
    rng = np.random.default_rng(seed)
    for k in range(L):
        if k not in need:
            continue
        # Pulse amplitude along the wire at time k (signal emitted at x=0 travels at `speed`).
        tt = k - x / speed
        amp = np.zeros(W, np.float32)
        for t0, n in seq:
            amp += ((tt >= t0) & (tt < t0 + n)).astype(np.float32)
        vib = 3.0 * np.sin(x / W * math.pi * 3 + k * 1.9) * (0.3 + amp.mean())
        y = sag + vib + amp * -6
        img = np.zeros((H, W), np.float32)
        pts = np.stack([x, y], 1).astype(np.int32)
        cv2.polylines(img, [pts], False, 0.25, 2)
        glow = np.zeros_like(img)
        for xi in np.flatnonzero(amp > 0)[::2]:
            cv2.circle(glow, (int(xi), int(y[xi])), 3, 1.0, -1)
        hdr = tint(blur(img, 0.8), COPPER) + tint(blur(glow, 1.5) * 2.5 + blur(glow, 20) * 3, WHITEHOT * 0.6 + AMBER * 0.4)
        gl = Glass(seed + k)
        gl.text(70, 470, "TX", 16, 180)
        marks = "  ".join(MORSE[c] for c in "brnrd")
        gl.text(70, 600, marks, 18, 150)
        gl.frame_count(k, 120)
        yield k, develop(hdr, exposure=1.6, frame=k, seed=seed, grain=0.06, overlay=gl.array(), bloom_k=0.5)


def solid(need, value=1.0, col=WHITEHOT, seed=99):
    for k in sorted(need):
        hdr = np.ones((H, W, 3), np.float32) * col * value
        yield k, develop(hdr, exposure=3.0 if value > 0 else 1.0, frame=k, seed=seed, grain=0.1, bloom_k=0)
