"""Act III — inheritance: capture → press → conduct → thread → wire.

Each shot here is handed its subject by the one before; nothing is designed
fresh. The photogram exposes what the observer found on the frozen plate.
The die is that photogram's skeleton after Douglas–Peucker. The layout is the
die snapped to a lithographic grid with routes leaving its ends. The live
thread's path is the longest route, smoothed. The wire carries the first
pulse, keyed to the film's one rhythm.

Screen geometry is shared too: the found form sits at screen centre in the
plate's last frames, on the film, in the press, and in the layout — so each
cut preserves position and shape at once.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

import optics as O
import observe as OB
import world as Wd
from lab import arclength_field, bump, fbm, polyline_distance, smoothstep
from lab.develop import Emulsion, grease_loop
from lab.relief import Relief
from lab.weave import Weave
from optics import (AMBER, BONE, COPPER, FURNACE, PHOSPHOR, STEEL, WHITEHOT, H, W, blur,
                    develop, splat, tint, up)


def _map():
    """Grain-image coords (found()['image']) → screen, matching plate.frontal."""
    F = Wd.found()
    sc = F["scale"]
    R = F["R"] / sc * Wd.PX
    z = 400.0 / R * Wd.PX / sc
    fx, fy = F["form"][1], F["form"][2]
    A = np.float32([[z, 0, W * 0.5 - fx * z], [0, z, H * 0.5 - fy * z]])
    return A, z


def _to_screen(polys):
    A, _ = _map()
    return [np.asarray(p, np.float32) @ A[:, :2].T + A[:, 2] for p in polys]


# ── capture: the photogram ─────────────────────────────────────────────────

CAPTURE_LEN = 50


def capture(need, seed=21):
    F = Wd.found()
    A, z = _map()
    sc = F["scale"]
    scar = cv2.resize(Wd.scar_map(), (Wd.NX * sc, Wd.NY * sc), interpolation=cv2.INTER_AREA)
    I = F["image"] / (np.percentile(F["image"], 99.0) + 1e-6)
    obj = np.clip(np.maximum(np.clip(I * 1.8 - 0.25, 0, 1), scar), 0, 1)
    mask = cv2.warpAffine(obj, A, (W, H), flags=cv2.INTER_LINEAR)
    mask = blur(mask, 1.2)
    em = Emulsion(W, H, seed=seed)
    em.expose(mask, amount=1.2, scatter=2.0)
    base, rebate, holes = O.film_edge(W, H, 150, pitch=128)
    loop = None
    for k in range(max(need) + 1):
        if k >= 4:
            em.develop(dt=0.07, steps=1)
        if k not in need:
            continue
        T = em.transmit()
        # Light table beneath: warm, uneven, overexposed at its centre.
        table = 0.6 + 1.8 * np.exp(-(((np.arange(W) - W * 0.55) / 900.0) ** 2))[None, :] \
            * np.exp(-(((np.arange(H)[:, None] - H * 0.45) / 700.0) ** 2))
        light = table * (T * (1 - rebate) + rebate * 0.04) + holes * table * 1.4
        hdr = tint(light.astype(np.float32), AMBER * 0.65 + BONE * 0.35)
        if k < 3:
            # The exposure: the lamp fires through the film.
            hdr = hdr * 0.2 + tint(np.full((H, W), 4.0 * (3 - k), np.float32), WHITEHOT)
        gp = smoothstep(30, 44, k)
        if gp > 0:
            g = grease_loop(W, H, W * 0.5 + 10, H * 0.5 - 6, 330, 300, seed=seed, progress=gp, width=9)
            hdr = hdr * (1 - 0.85 * g[..., None]) + tint(g * 0.9, np.array([0.85, 0.12, 0.06], np.float32))
        yield k, develop(hdr, exposure=1.1, frame=k, seed=seed, grain=0.05, bloom_k=0.3, vignette=0.6,
                         weave=0.9)


# ── press: the die meets dark matter ──────────────────────────────────────

PRESS_LEN = 40
IMPACT = 11


def _die_screen():
    F = Wd.found()
    return _to_screen(F["die"])


def press(need, seed=11):
    die = _die_screen()
    half = [p / 2 for p in die]
    r = Relief(half, 960, 540, seed=seed, width=8.0)
    rng = np.random.default_rng(seed)
    pts = np.concatenate([np.stack([np.interp(np.linspace(0, len(p) - 1, 40), np.arange(len(p)), p[:, 0]),
                                    np.interp(np.linspace(0, len(p) - 1, 40), np.arange(len(p)), p[:, 1])], 1)
                          for p in die])
    n = 1800
    sel = pts[rng.integers(0, len(pts), n)]
    ang = rng.uniform(0, 2 * math.pi, n)
    spd = rng.lognormal(2.2, 0.6, n)
    vel = np.stack([np.cos(ang), np.sin(ang)], 1) * spd[:, None]
    for k in range(max(need) + 1):
        if k not in need:
            continue
        if k < IMPACT:
            depth, shadow, heat = 0.0, 1 - 0.85 * smoothstep(0, IMPACT, k), 0.0
            sh = rng.normal(0, 0.4 + 2.5 * k / IMPACT, 2)
        else:
            j = k - IMPACT
            depth = 1.0 + 0.15 * math.exp(-j / 2) * math.cos(j * 2.2)
            shadow, heat = 1.0, 2.8 * math.exp(-j / 15)
            sh = rng.normal(0, 40 * math.exp(-j / 3.5), 2)
        S = up(r.shade(depth, light=(-0.7, -0.45)))
        Hh = up(r.heat(1.0)) * heat
        hdr = tint(S * shadow * 0.07, STEEL * 0.6 + COPPER * 0.4) + tint(Hh, FURNACE) + tint(blur(Hh, 25) * 0.6, FURNACE)
        if k >= IMPACT:
            j = k - IMPACT
            pos = sel + vel * j + np.array([0, 0.9]) * j * j
            prev = sel + vel * max(0, j - 1) + np.array([0, 0.9]) * max(0, j - 1) ** 2
            lum = np.full(n, 3.0 * math.exp(-j / 6), np.float32)
            sp = splat(pos, lum) + splat((pos + prev) / 2, lum) + splat(prev, lum * 0.5)
            hdr += tint(blur(sp, 1.0), WHITEHOT * 0.5 + FURNACE * 0.5)
        hdr = O.shift(hdr, *sh)
        flash = 6.0 if k == IMPACT else 1.0
        yield k, develop(hdr, exposure=1.6 * flash, frame=k, seed=seed, grain=0.07, bloom_k=0.45,
                         weave=0.6 + (3 if IMPACT <= k < IMPACT + 6 else 0))


# ── conduct: the trace becomes a layout ───────────────────────────────────

CONDUCT_LEN = 44


def _layout_screen():
    die = _die_screen()
    return Wd.layout(die, grid=12.0, exit_x=W + 200, pitch=30.0)


def conduct(need, seed=12):
    die = _die_screen()
    lay, routes = _layout_screen()
    r_die = Relief([p / 2 for p in die], 960, 540, seed=11, width=8.0)
    r_lay = Relief([p / 2 for p in lay + routes], 960, 540, seed=11, width=5.0)
    S_die = up(r_die.shade(1.0, light=(-0.7, -0.45)))
    S_lay = up(r_lay.shade(0.8, light=(-0.7, -0.45)))
    H_die = up(r_die.heat(1.0))
    ch_lay = up(r_lay.channel)
    arc_lay = up(r_lay.arclen)
    for k in range(max(need) + 1):
        if k not in need:
            continue
        snap = smoothstep(8, 18, k)          # lithography: organic → grid
        grow = smoothstep(14, 36, k)         # routes reach out
        s = ((k * 0.045) % 1.2)
        C_die = up(r_die.current(s, 0.05, 0.25))
        C_lay = up(r_lay.current((k - 14) * 0.03, 0.04, 0.3)) * (k > 14)
        reveal = (arc_lay <= grow + 0.02).astype(np.float32)
        base = tint(((1 - snap) * S_die + snap * S_lay) * 0.06, STEEL * 0.6 + COPPER * 0.4)
        hdr = base + tint(H_die * 0.5 * math.exp(-k / 20) * (1 - snap), FURNACE) \
            + tint(C_die * 3.0 * (1 - snap), WHITEHOT) + tint(blur(C_die, 12) * 2 * (1 - snap), COPPER) \
            + tint(ch_lay ** 2 * reveal * snap * 0.35, COPPER) \
            + tint(C_lay * reveal * 3.0, WHITEHOT) + tint(blur(C_lay * reveal, 10) * 1.5, COPPER)
        yield k, develop(hdr, exposure=1.5, frame=k, seed=seed, grain=0.06, bloom_k=0.5)


# ── thread: the conductor is a fibre ──────────────────────────────────────

THREAD_LEN = 48


def _route_bend(pitch):
    """The live weft inherits the steps of the longest route, smoothed."""
    _, routes = _layout_screen()
    p = max(routes, key=lambda q: np.sum(np.abs(np.diff(q, axis=0))))
    xs = np.linspace(0, 1, W)
    t = np.linspace(0, 1, len(p))
    y = np.interp(xs, t, p[:, 1] - p[0, 1])
    y = cv2.GaussianBlur(y.astype(np.float32)[None, :], (0, 0), 90)[0]
    y = y / (np.abs(y).max() + 1e-6) * pitch * 0.45
    return np.repeat(y[None, :], H, 0).astype(np.float32)


def thread(need, seed=31):
    pitch = 190.0
    wv = Weave(W, H, pitch=pitch, seed=seed, angle=-0.05,)
    route = _route_bend(pitch)
    row = int(H * 0.5 // pitch)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    for k in range(max(need) + 1):
        if k not in need:
            continue
        px = -200 + k * 62.0
        img, live, hgt = wv.render(light=(-0.8, -0.45), route=route, live_row=row, pulse_x=px, pulse_w=70)
        # Low key: one raking source; the weave is mostly shadow.
        rake = (np.clip(1.1 - (xx / W) * 1.0, 0.04, 1.1) ** 2)[..., None]
        warm = np.array([1.0, 0.72, 0.45], np.float32)
        glow = blur(live, 2) * 4 + blur(live, 22) * 3
        # The pulse lights the fibres around it: the only real light here.
        spill = blur(live, 120)[..., None] * 9.0
        hdr = img * warm * (0.05 * rake + spill) + tint(glow, WHITEHOT * 0.5 + AMBER * 0.5)
        # Macro depth of field: a tilted focal plane through the live thread.
        depth = (yy / H + 0.25 * hgt / 2)
        hdr = O.defocus(hdr, depth, 0.5 + 0.01 * math.sin(k * 0.1), aperture=22.0)
        yield k, develop(hdr, exposure=1.7, frame=k, seed=seed, grain=0.06, bloom_k=0.5, vignette=0.6)


# ── wire: the first transmitted pulse ─────────────────────────────────────

WIRE_LEN = 40


def wire(need, seed=41):
    x = np.arange(W, dtype=np.float32)
    sag = 560 + 60 * ((x - W / 2) / (W / 2)) ** 2
    speed = 74.0
    for k in range(max(need) + 1):
        if k not in need:
            continue
        # One pulse launched at k=0 from the left edge; a second, one RHYTHM
        # later, is just entering when the film cuts away.
        amp = np.zeros(W, np.float32)
        for t0 in (0, Wd.RHYTHM * 2):
            c = (k - t0) * speed
            amp += np.exp(-((x - c) / 55.0) ** 2) * (k >= t0)
        vib = 2.0 * np.sin(x / W * math.pi * 3 + k * 1.9) * (0.3 + amp.max())
        y = sag + vib - 5 * amp
        img = np.zeros((H, W), np.float32)
        pts = np.stack([x, y], 1)
        cv2.polylines(img, [(pts * 16).astype(np.int32)], False, 0.35, 2, cv2.LINE_AA, shift=4)
        glow = np.zeros_like(img)
        for xi in np.flatnonzero(amp > 0.05)[::2]:
            cv2.circle(glow, (int(xi), int(y[xi])), 3, float(amp[xi]), -1)
        hdr = tint(blur(img, 0.8), COPPER * 0.6) + tint(blur(glow, 1.5) * 2.5 + blur(glow, 24) * 3, WHITEHOT * 0.6 + AMBER * 0.4)
        yield k, develop(hdr, exposure=1.6, frame=k, seed=seed, grain=0.06, bloom_k=0.5, vignette=0.6)
