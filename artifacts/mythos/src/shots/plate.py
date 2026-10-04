"""Act II — one plate, one continuous world.

    strike    a leader feels its way across a dark membrane; the return stroke
    rest      the stare: a burnt channel cooling, dust that the blast swept off it
    vibrate   a drive comes on; grains are shaken into near-forms — and back
              onto the scar, because the scar pins a node
    observe   an apparatus touches the plate: a polariser turns (tension shows
              as colour), the view is squared, a raster acquires it row by row
              and the scan line kicks the grains it reads; the drive is cut so
              the plate can be measured; a slit closes on what was found

All four phases are one simulation (``world.plate_sim``). Inserts cut away and
come back to a plate that kept moving. The camera is an instrument that
changes, not a camera that flies: its homography is re-set between phases,
and during observation it is pulled from oblique to frontal — measuring is
squaring the world to the instrument.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

import optics as O
import world as Wd
from lab import bump, fbm, smoothstep
from lab import breakdown as BD
from optics import (AMBER, BONE, COPPER, FURNACE, STEEL, UV, WHITEHOT, H, W, blur,
                    develop, splat, tint, up)

MG = 60                      # canvas margin around the plate (the free edge whips into it)
CW, CH = Wd.PW + 2 * MG, Wd.PH + 2 * MG


def _M(corners):
    src = np.float32([[MG, MG], [MG + Wd.PW, MG], [MG + Wd.PW, MG + Wd.PH], [MG, MG + Wd.PH]])
    return cv2.getPerspectiveTransform(src, np.float32(corners))


def _lerp(a, b, t):
    return [(ax + (bx - ax) * t, ay + (by - ay) * t) for (ax, ay), (bx, by) in zip(a, b)]


# Camera corners (screen positions of the plate's TL, TR, BR, BL).
CAM_STRIKE = [(-180, 70), (2010, 150), (2280, 1240), (-460, 1130)]
CAM_REST = [(-1500, -420), (2500, -120), (3600, 1900), (-1900, 1500)]
CAM_VIB = [(-640, 230), (1720, -170), (2620, 1180), (-120, 1500)]


def _frontal():
    """The instrument's own frame: square to the plate, centred on what the
    observer found, at a magnification where the form fills the slit."""
    F = Wd.found()
    sc = F["scale"]
    fx, fy = F["form"][1] / sc * Wd.PX, F["form"][2] / sc * Wd.PX   # plate px
    R = F["R"] / sc * Wd.PX
    z = 400.0 / R
    cx, cy = W * 0.5, H * 0.5
    x0, y0 = cx - fx * z, cy - fy * z
    return [(x0, y0), (x0 + Wd.PW * z, y0), (x0 + Wd.PW * z, y0 + Wd.PH * z), (x0, y0 + Wd.PH * z)], (fx, fy, R, z)


def _rest_cam():
    """Low and close on the fork, so the burnt channel crosses the frame."""
    fx, fy, _ = Wd.lightning()["fork"]
    # Build relative to the strike cam, then shift so the fork sits left of centre.
    M = _M(CAM_REST)
    p = cv2.perspectiveTransform(np.float32([[[fx + MG, fy + MG]]]), M)[0, 0]
    dx, dy = 820 - p[0], 560 - p[1]
    return [(x + dx, y + dy) for x, y in CAM_REST]


def camera(k):
    S, R, V = CAM_STRIKE, _rest_cam(), CAM_VIB
    Fr, _ = _frontal()
    drift = lambda c, a: [(x + a * math.sin(k * 0.021 + i), y + a * math.cos(k * 0.017 + i)) for i, (x, y) in enumerate(c)]
    if k < Wd.REST[0]:
        return drift(S, 6)
    if k < Wd.VIBRATE[0]:
        return drift(R, 3)
    if k < 206:
        return drift(V, 5)
    t = smoothstep(206, 224, k)
    return drift(_lerp(V, Fr, t), 5 * (1 - t))


def leader(k):
    a, b = Wd.STRIKE
    return float(np.interp(k, [3, 12, 21, 27, 33, Wd.STRIKE_RS], [0, 0.28, 0.33, 0.66, 0.93, 1.0]))


def plate(need, seed=7):
    L = max(need) + 1
    Lt = Wd.lightning()
    tree = Lt["tree"]
    F = Wd.found()
    scar = Wd.scar_map()
    scar_c = np.zeros((CH, CW), np.float32)
    scar_c[MG:MG + Wd.PH, MG:MG + Wd.PW] = scar
    char = blur(scar_c, 1.2)
    # Embers: heat is uneven along a burnt channel — thick channels hold it.
    ember = char ** 2.2 * np.clip(0.55 + 0.6 * up(fbm(CW // 6, CH // 6, seed + 9, 4, 6), CW, CH), 0.05, 1.6)
    sheen0 = up(fbm(CW // 4, CH // 4, seed + 1, 5, 6), CW, CH)
    plate_mask = np.zeros((CH, CW), np.float32)
    plate_mask[MG:MG + Wd.PH, MG:MG + Wd.PW] = 1
    plate_mask = blur(plate_mask, 1.0)
    yy, xx = np.mgrid[0:CH, 0:CW].astype(np.float32)
    ray = O.god_ray(CW, CH, (CW + 300, -200), 2.55, 520, 0.35)
    Fr, (fx, fy, R, z) = _frontal()
    found_scr = (W * 0.5, H * 0.5)
    rng = np.random.default_rng(seed)
    for k, m in Wd.plate_sim(L):
        if k not in need:
            continue
        hdr = np.zeros((CH, CW, 3), np.float32)
        un = up(m.u, Wd.PW, Wd.PH)
        un = un / (math.sqrt(float(m.rms2.max())) + 1e-6) if m.rms2.max() > 0 else un * 0
        U = np.zeros((CH, CW), np.float32)
        U[MG:MG + Wd.PH, MG:MG + Wd.PW] = un
        # The membrane: dark taut polymer. Its sheen is the instantaneous
        # displacement — highlights crawl as it vibrates.
        gy, gx = np.gradient(blur(U, 2.0) * 9 + 0.6 * sheen0)
        nz = 1 / np.sqrt(1 + gx * gx + gy * gy)
        spec = np.clip((-0.55 * gx + 0.75 * gy + 0.35) * nz, 0, None) ** 14
        lit = 0.25 + 1.6 * ray
        surf = (0.010 + 0.008 * sheen0 + 0.35 * spec * lit) * plate_mask
        # The free edge: a rim of light that whips with the plate.
        rim_y = MG - 7 * up(m.u[0:1, :], Wd.PW, 1)[0] / (math.sqrt(float(m.rms2.max())) + 1e-6) if m.rms2.max() > 0 else np.full(Wd.PW, MG, np.float32)
        rim = np.zeros((CH, CW), np.float32)
        pts = np.stack([np.arange(Wd.PW) + MG, rim_y], 1)
        cv2.polylines(rim, [(pts * 16).astype(np.int32)], False, 1.0, 2, cv2.LINE_AA, shift=4)
        # Grains: metallic dust, lifted by the surface and by their own hops.
        gp = m.p * Wd.PX + MG
        gl = np.full(len(gp), 1.0, np.float32)
        gl += (np.random.default_rng(seed + 3).random(len(gp)).astype(np.float32) ** 8) * 5  # sparkle
        lift = np.zeros(len(gp), np.float32)
        if m.rms2.max() > 0:
            uu = m.u[np.clip(m.p[:, 1].astype(int), 0, Wd.NY - 1), np.clip(m.p[:, 0].astype(int), 0, Wd.NX - 1)]
            lift = 7.0 * uu / (math.sqrt(float(m.rms2.max())) + 1e-6) + 2.5 * m.hop
        gp2 = gp.copy()
        gp2[:, 1] -= lift
        pp = m.prev * Wd.PX + MG
        pp[:, 1] -= lift
        G = blur(splat(gp2, gl, CW, CH) + 0.5 * splat(pp, gl * 0.5, CW, CH), 0.7)
        gray = lit * 0.022
        # Phase-specific light.
        flash = 0.0
        ex = 1.4
        neg = False
        if k < Wd.REST[0]:
            pr = leader(k)
            rs = 1.0 if k in (Wd.STRIKE_RS, Wd.STRIKE_RS + 1) else (0.45 if k == Wd.STRIKE_RS + 2 else 0.0)
            T = BD.draw(tree, CW, CH, tree.steps * pr, tremble=0.6, seed=k, return_stroke=rs,
                        offset=(MG, MG), width=0.9, core=0.6, prune=1.0)
            T = blur(T, 0.9) + blur(T, 6) * 0.9 + blur(T, 30) * 0.7
            flash = 2.0 * rs
            local = blur(T, 70) * 0.6  # the leader lights only what is near it
            col = WHITEHOT * 0.55 + UV * 0.45
            hdr += tint(T, col)
            cool = max(0.0, (k - Wd.STRIKE_RS) / 18.0)
            if k > Wd.STRIKE_RS:
                hdr += tint(ember * 2.6 * math.exp(-cool * 1.2), FURNACE)
            gray = 0.002 + flash * 0.4 + local
            # Corona glow at the electrode before the leader leaves.
            if k < 6:
                r0 = np.hypot(xx - tree.pos[0, 0] - MG, yy - tree.pos[0, 1] - MG)
                hdr += tint(np.exp(-r0 / 30) * 0.6 * (0.5 + 0.5 * math.sin(k * 2.7)), UV)
            ex = 1.5 * (1 + 3 * rs * (k == Wd.STRIKE_RS))
            neg = k == Wd.STRIKE_RS + 1
        else:
            age = k - Wd.STRIKE_RS
            heat = 2.2 * math.exp(-age / 22.0)
            flick = 1 + 0.12 * math.sin(k * 1.3) * math.sin(k * 0.37)
            hdr += tint(ember * heat * flick, FURNACE) + tint(blur(ember, 10) * heat * 0.4, FURNACE)
            # Burnt channel: darker than the membrane where it doesn't glow.
            surf = surf * (1 - 0.7 * char)
        hdr += tint(surf, STEEL * 0.4 + COPPER * 0.6) + tint(rim * (0.15 + 0.8 * ray) * plate_mask.max(), BONE)
        if k >= Wd.REST[0] and k < Wd.VIBRATE[0]:
            gray = gray * 0.6  # the stare: dust is only a glint under raking light
        hdr += tint(G * gray * (0.4 + 1.6 * ray), BONE * np.array([1.0, 0.86, 0.66], np.float32))
        # ── observation modalities, in plate space ────────────────────────
        if Wd.OBSERVE[0] <= k:
            # Polariser: backlight through the membrane; tension becomes colour.
            an = smoothstep(196, 206, k) * (1 - smoothstep(214, 224, k))
            if an > 0:
                st = np.zeros((CH, CW), np.float32)
                st[MG:MG + Wd.PH, MG:MG + Wd.PW] = up(np.clip(m.stress(), 0, 3), Wd.PW, Wd.PH)
                st = blur(np.minimum(st, np.percentile(st, 99)), 3.0)
                fr = O.photoelastic(st, 1.0) * plate_mask[..., None]
                back = 0.35 * (0.6 + 0.4 * sheen0)
                hdr = hdr * (1 - 0.8 * an) + an * fr * back[..., None] * np.array([1.0, 0.92, 0.8], np.float32)
                hdr += tint(G * 0.04 * an, BONE)
            row = Wd.scan_row(k)
            if row is not None or k >= 246:
                r = (row if row is not None else Wd.NY + 10) * Wd.PX + MG
                m_ = O.raster(hdr[..., 0], r, width=5, ahead_dim=0.08, trail=0.6)
                hdr = hdr * m_[..., None]
                hdr += tint(np.exp(-((yy - r) / 2.5) ** 2) * plate_mask * 0.5, PHOS_SCAN)
        # ── to the screen ─────────────────────────────────────────────────
        M = _M(camera(k))
        depth = cv2.warpPerspective(yy, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=float(CH))
        img = cv2.warpPerspective(hdr, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        if Wd.REST[0] <= k < Wd.VIBRATE[0]:
            # The stare: a thin focal band across the fork; the rest melts.
            fy_c = Lt["fork"][1] + MG
            img = O.defocus(img, depth / CH, fy_c / CH, aperture=16.0)
        elif k < 206 and k >= Wd.VIBRATE[0]:
            img = O.defocus(img, depth / CH, 0.45 + 0.08 * math.sin(k * 0.04), aperture=7.0)
        if k >= 246:
            # The slit closes on the found form; everything else goes dark.
            t = smoothstep(246, 258, k)
            hw = 900 * (1 - t) + 210 * t
            mask = O.slit(W, H, found_scr[0], found_scr[1], 0.0, hw, soft=5)
            img = img * mask[..., None]
        ex = ex if k < Wd.REST[0] else 1.5
        yield k, develop(img, exposure=ex, frame=k, seed=seed, grain=0.065, bloom_k=0.5,
                         negative=neg, vignette=0.5, star=0.01 if k < Wd.REST[0] else 0.0)


PHOS_SCAN = np.array([0.6, 1.0, 0.7], np.float32)
