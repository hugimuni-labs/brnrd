"""Act I — energy without names.

web      a random potential collapses into filaments; one node ignites
disk     the ignition *is* the inner edge of a disk; a gap; the lensed arc
corona   the arc is a magnetic arch; new flux rises; the corona reconnects
crystal  scale collapses: a loop's leg becomes a seam where a dendrite grows

Every shot is filmed like an instrument caught it: locked or nearly locked,
cropped too close, light allowed to destroy half the frame.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

import optics as O
from lab import bump, fbm, sample, smoothstep
from lab import breakdown as BD
from lab.disk import Disk
from lab.plasma import Corona, camera
from lab.web import Web
from optics import (AMBER, BONE, COPPER, FURNACE, HALPHA, STEEL, UV, WHITEHOT, H, W,
                    blur, develop, splat, tint, up)
from shots import P_ARCH, P_IGNITE


def _rot(v, a):
    c, s = math.cos(a), math.sin(a)
    return np.array([v[0] * c - v[1] * s, v[0] * s + v[1] * c])


# ── web: energy without names ─────────────────────────────────────────────

WEB_LEN = 76
IGNITE_AT = 56


def web(need, seed=3):
    L = max(need) + 1
    mid = Web(640, 360, (2400.0, 1350.0), seed=seed, smooth=70.0)
    far = Web(400, 225, (3600.0, 2025.0), seed=seed + 8, smooth=120.0)
    near = Web(120, 68, (2400.0, 1350.0), seed=seed + 4, smooth=260.0)
    D = lambda k: 0.35 + 2.1 * smoothstep(0, WEB_LEN + 10, k) ** 1.2
    # The node that will ignite: the densest point of the final web, moved
    # (by choosing where the camera looks) to the anchor the disk inherits.
    rho = mid.density(D(WEB_LEN), 240, 135)
    rho[:12] = rho[-12:] = 0
    rho[:, :12] = rho[:, -12:] = 0
    iy, ix = np.unravel_index(np.argmax(rho), rho.shape)
    node = np.array([(ix + 0.5) * mid.ex / 240, (iy + 0.5) * mid.ey / 135])
    off = np.asarray(P_IGNITE) - node
    ray = O.god_ray(W, H, (-300, -420), 0.66, 260, 0.45)
    ray2 = O.god_ray(W, H, (W + 160, -200), 2.32, 90, 1.1)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    rr = np.hypot(xx - P_IGNITE[0], yy - P_IGNITE[1])
    for k in range(L):
        if k not in need:
            continue
        drift = np.array([0.35 * k, -0.12 * k])
        p = mid.positions(D(k), off + drift)
        # Collapse onto the node in the last moments before ignition.
        pre = smoothstep(IGNITE_AT - 18, IGNITE_AT + 8, k)
        if pre > 0:
            d = np.asarray(P_IGNITE, np.float32) - p
            r = np.hypot(d[:, 0], d[:, 1])[:, None] + 1e-3
            p = p + d * (0.35 * pre * np.exp(-r / 160))
        pf = far.positions(D(k) * 0.9, off * 0.6 + drift * 0.5)
        pn = near.positions(D(k) * 0.7, off * 1.4 + drift * 2.2)
        I = blur(splat(p, mid.lum), 0.7)
        dens = blur(I, 5)
        I = I * np.clip(dens / (np.percentile(dens, 99.3) + 1e-6), 0, 3) ** 1.5
        If = blur(splat(pf, far.lum), 2.2)
        If = If * np.clip(blur(If, 8) / (np.percentile(blur(If, 8), 99) + 1e-6), 0, 3)
        In = blur(splat(pn, near.lum * 6), 9)  # near dust: out of focus, bokeh-soft
        # Radiation: two fronts leave the node before it ignites, briefly
        # lighting structure that is otherwise in the dark.
        front = 0
        for k0, v in ((18, 70), (38, 85)):
            if k >= k0:
                front += 2.5 * np.exp(-((rr - (k - k0) * v) / 90) ** 2) * np.exp(-(k - k0) / 14)
        sweep = 0.7 + 0.3 * math.sin(k * 0.05)
        light = 0.004 + 1.7 * ray * sweep + 0.35 * ray2 * bump(k, 24, 999, 16) + front
        hdr = tint(I * 0.55 * light, AMBER) + tint(If * 0.12 * light, COPPER) \
            + tint(In * 0.08 * (0.2 + light), AMBER) + tint((0.02 * ray + 0.006 * ray2) * sweep, AMBER)
        star = 0.0
        if k >= IGNITE_AT - 4:
            j = k - (IGNITE_AT - 4)
            amp = 0.3 * math.exp(0.42 * j)
            src = np.exp(-(rr / (2.5 + 0.6 * j)) ** 2) * amp + 0.02 * amp * np.exp(-rr / 60)
            hdr = hdr + tint(src, WHITEHOT)
            star = 0.008
        exp = 1.25 * smoothstep(-4, 10, k)
        if k >= IGNITE_AT:
            exp *= 1 + 0.6 * (k - IGNITE_AT) ** 1.6
        yield k, develop(hdr, exposure=exp, frame=k, seed=seed, grain=0.06, bloom_k=0.6,
                         star=star, vignette=0.55, lift=0.002)


# ── disk: rotation and collapse ───────────────────────────────────────────

DISK_LEN = 80
_DISK_T0 = 30.0
_DISK_DT = 0.11


def _disk_framing(d: Disk, k):
    """Three instruments on one phenomenon. Each places a physical feature
    on a named screen anchor rather than on the screen's centre."""
    if k < 30:
        incl, roll, scale = 1.47, -0.18, 620.0
        hot = np.array([-1.12, 0.0])  # approaching side, inner edge
        center = np.asarray(P_IGNITE) - scale * _rot(hot, roll)
        return dict(incl=incl, roll=roll, scale=scale, center=tuple(center))
    if k < 50:
        incl, roll, scale = 1.28, 0.30, 1050.0
        g0 = d.gap[0]
        phi = -1.75
        pt = np.array([g0 * math.cos(phi), g0 * math.sin(phi) * math.cos(incl)])
        center = np.array([820.0, 560.0]) - scale * _rot(pt, roll)
        return dict(incl=incl, roll=roll, scale=scale, center=tuple(center))
    incl, roll, scale = 1.535, -0.07, 980.0
    return dict(incl=incl, roll=roll, scale=scale, center=None)


def disk(need, seed=1):
    d = Disk(n=560_000, seed=seed)
    arch_center = None
    for k in sorted(need):
        t = _DISK_T0 + _DISK_DT * k
        fr = _disk_framing(d, k)
        if fr["center"] is None:
            if arch_center is None:
                # Find the lensed arc's apex with the camera at the origin, then
                # move the camera so that apex stands on P_ARCH.
                p, l, tmp, beh = d.project(t, incl=fr["incl"], roll=fr["roll"], scale=fr["scale"], center=(0, 0))
                sel = beh & (np.abs(p[:, 0]) < 0.08 * fr["scale"]) & (p[:, 1] < 0)
                apex_y = np.percentile(p[sel, 1], 2) if sel.any() else -0.7 * fr["scale"]
                arch_center = (P_ARCH[0], P_ARCH[1] - apex_y)
            fr["center"] = arch_center
        p, l, tmp, beh = d.project(t, **fr)
        jp, jl = d.jet(t, **fr)
        sb = 1.0 if k < 30 else (2.2 if k < 50 else 1.4)  # sparser crops read as density, not dots
        hot = blur(splat(p, l * tmp ** 2), 0.9 * sb)
        warm = blur(splat(p, l * tmp * (1 - tmp)), 1.0 * sb)
        cool = blur(splat(p, l * (1 - tmp) ** 2), 1.3 * sb)
        J = blur(splat(jp, jl), 1.4)
        g = 0.55 if k < 30 else (3.0 if k < 50 else 1.6)
        hdr = tint(hot * g, WHITEHOT) + tint(warm * g, AMBER) + tint(cool * g, COPPER) \
            + tint(J * 0.6, AMBER) + tint(blur(J, 18) * 0.5, FURNACE)
        if k < 30:
            # The ignition is still in the lens: an overexposure ramps down
            # and the disk is what was behind the glare.
            ex = 1.3 * (1 + 14 * math.exp(-k / 3.2))
        elif k < 50:
            ex = 1.6
        else:
            ex = 1.35 * (1 + 2.5 * math.exp(-(k - 50) / 2.0))
        yield k, develop(hdr, exposure=ex, frame=k, seed=seed, grain=0.07, bloom_k=0.55,
                         star=0.025 if k < 30 or k >= 50 else 0.0, vignette=0.5)


# ── corona: arches under tension, reconnection ────────────────────────────

CORONA_LEN = 88
EMERGE = (10, 58)


def _corona_sim(L, seed=2):
    c = Corona(seed=seed, n_lines=380)
    for k in range(L):
        prog = smoothstep(EMERGE[0], EMERGE[1], k)
        S = c.sources(k * 0.05, emerge=(0.05, 0.0, 2.1, 0.8, prog), shear=0.25)
        lines, mask = c.trace(S, step=0.016, n=430)
        closed = c.step_topology(lines, mask)
        yield k, c, lines, mask, closed


def _limb(cx, y0, curv=0.00006):
    xx = np.arange(W, dtype=np.float32)
    return y0 + curv * (xx - cx) ** 2


def corona(need, seed=2, channel="halpha"):
    L = max(need) + 1
    rng = np.random.default_rng(seed)
    pitch, scale = 0.16, 1.0
    center = None
    hero = 0
    yy = np.arange(H, dtype=np.float32)[:, None]
    gran = up(0.5 + 0.5 * fbm(W // 3, H // 3, seed + 3, 5, 40))
    spic = up(np.abs(fbm(W // 2, 60, seed + 4, 4, 160)), W, 60)
    for k, c, lines, mask, closed in _corona_sim(L, seed):
        if center is None:
            # The hero: a well-heated closed loop of middling height. Choose the
            # scale so its feet stand on the limb and its apex on P_ARCH.
            xy0, _ = camera(lines, pitch=pitch, scale=1.0, center=(0, 0))
            cands = []
            for i in np.flatnonzero(closed):
                m = mask[i]
                if m.sum() < 30:
                    continue
                P0 = xy0[i][m]
                j = np.argmin(P0[:, 1])
                foot = 0.5 * (P0[0, 1] + P0[-1, 1])
                hgt = foot - P0[j, 1]
                cands.append((c.heat[i] * min(1.0, hgt / 0.5) * (hgt < 1.4), i, P0[j], hgt, foot))
            hs = np.array([q[3] for q in cands])
            mid = [q for q in cands if np.percentile(hs, 35) <= q[3] <= np.percentile(hs, 75)]
            mid.sort(key=lambda q: -q[0])
            _, hero, apex, hgt, foot = mid[0]
            limb_y = 960.0
            scale = (limb_y - P_ARCH[1]) / hgt
            center = (P_ARCH[0] - scale * apex[0], limb_y - scale * foot)
            c.heat[hero] = max(c.heat[hero], np.percentile(c.heat, 97))
        if k not in need:
            continue
        xy, depth = camera(lines, pitch=pitch, scale=scale, center=center)
        # Only a minority of loops are heated at any moment (nanoflare-ish):
        # the set drifts slowly so the arcade breathes.
        hot_mask = 0.15 + 0.85 * (np.sin(c.phase * 3.1 + k * 0.03) > 0.1)
        hot_mask[hero] = 1.0
        # Plasma along field lines: sample every line densely, light it by its
        # own heating, blobs flowing footpoint→apex (siphon) and rain falling back.
        pts, wts, dep = [], [], []
        for i in range(len(lines)):
            m = mask[i]
            n = int(m.sum())
            if n < 6:
                continue
            P = xy[i][m]
            Z = lines[i][m][:, 2]
            # Interpolate ×3 so dense dots read as continuous plasma.
            s = np.linspace(0, n - 1, n * 3)
            Px = np.interp(s, np.arange(n), P[:, 0])
            Py = np.interp(s, np.arange(n), P[:, 1])
            Zs = np.interp(s, np.arange(n), Z)
            u = s / (n - 1)
            heat = c.heat[i] ** 2.6 * (1.0 if closed[i] else 0.15) * hot_mask[i]
            flow = 0.5 + 0.5 * np.sin(2 * np.pi * (u * 3.0 - k * 0.045) + c.phase[i]) ** 8
            foot = np.exp(-Zs / 0.05) * 1.6
            lum = heat * (0.25 + 0.75 * flow + foot) * (1 + 8.0 * c.flash[i]) + 0.6 * c.flash[i] * (0.5 + flow)
            if channel == "uv":
                lum = (c.flash[i] > 0.3) * (2 + c.flash[i]) * (0.4 + flow) + 0.04 * heat
            pts.append(np.stack([Px, Py], 1))
            wts.append(lum)
            dep.append(np.full(len(Px), float(np.mean(depth[i][m]))))
        P = np.concatenate(pts).astype(np.float32)
        Wt = np.concatenate(wts).astype(np.float32)
        Dp = np.concatenate(dep)
        nearL = Dp < np.median(Dp)
        Ia = blur(splat(P[nearL], Wt[nearL] * 0.3), 1.8)
        Ib = blur(splat(P[~nearL], Wt[~nearL] * 0.3), 4.0)  # far loops out of the focal plane
        I = Ia + Ib
        # Photosphere: the surface the loops stand on, far too bright to hold.
        limb = _limb(P_ARCH[0], 960.0 + 14, 0.00005)[None, :]
        limb = np.minimum(limb, 1e4)
        surf = 1 / (1 + np.exp(-np.clip((yy - limb) / 3.0, -60, 60)))
        chrom = np.exp(-np.clip(limb - yy, 0, None) / 9) * (yy < limb) * (0.6 + 0.8 * cv2.resize(spic, (W, 1))[0][None, :])
        sun = surf * (3.5 + 1.2 * gran) * (1 + 0.04 * math.sin(k * 0.7))
        if channel == "uv":
            hdr = tint(I * 1.2 + blur(I, 12) * 0.8, UV * 0.6 + WHITEHOT * 0.4) + tint(sun * 0.05, UV)
            yield k, develop(hdr, exposure=1.4, frame=k, seed=seed + 9, grain=0.09, bloom_k=0.4,
                             negative=False, vignette=0.6)
            continue
        hdr = tint(I + blur(I, 8) * 0.6 + blur(I, 50) * 0.6, AMBER * 0.55 + HALPHA * 0.45) \
            + tint(sun, WHITEHOT * 0.7 + AMBER * 0.3) + tint(chrom * 0.9, HALPHA)
        flash = float(c.flash.max())
        ex = 1.15 * (1 + 0.35 * min(flash, 3))
        yield k, develop(hdr, exposure=ex, frame=k, seed=seed, grain=0.07, bloom_k=0.55,
                         star=0.02, vignette=0.45, weave=0.8)


# ── crystal: the seam ─────────────────────────────────────────────────────

CRYSTAL_LEN = 44


def crystal(need, seed=4):
    L = max(need) + 1
    w, h = W, H
    rng = np.random.default_rng(seed)
    # A mineral vein crossing the frame steeply (it inherits a loop's leg).
    yy, xx = np.mgrid[0:h // 4, 0:w // 4].astype(np.float32) * 4
    ang = math.radians(68)
    across = (xx - 980) * math.sin(ang) - (yy - 560) * math.cos(ang)
    vein = np.exp(-(across / 230) ** 2) * np.exp(0.8 * fbm(w // 4, h // 4, seed + 1, 5, 3))
    att = BD.scatter(vein, 2600, rng, scale=4)
    root = (980 + 640 * math.cos(ang), 560 + 640 * math.sin(ang))
    tree = BD.grow(root, att, seed=seed, step=6, influence=55, kill=9, noise=0.15,
                   aniso=6, aniso_k=0.7, aniso_rot=ang)
    rock = up(0.6 * fbm(w // 4, h // 4, seed + 21, 6, 3) + 0.25 * fbm(w // 4, h // 4, seed + 22, 4, 12))
    gy, gx = np.gradient(cv2.GaussianBlur(rock, (0, 0), 3) * 12)
    shade = np.clip(0.35 - 0.5 * gx - 0.25 * gy, 0, None)
    sparkle = (rng.random((h, w)) > 0.9993).astype(np.float32) * up(np.clip(fbm(w // 8, h // 8, seed + 3, 3, 3), 0, None))
    vein_full = up(vein)
    for k in range(L):
        if k not in need:
            continue
        prog = smoothstep(-2, L - 4, k) ** 0.85
        I = BD.draw(tree, w, h, tree.steps * prog, core=0.9, tip_glow=1.6, width=0.8)
        glint = blur(I, 1.0) + 0.6 * blur(I, 6)
        # Raking light: the crystal faces catch it at angles; the vein is wet.
        tw = 0.5 + 0.5 * math.sin(k * 0.9)
        hdr = tint(shade * 0.05 * (0.4 + vein_full) + blur(sparkle, 0.8) * 3 * tw, STEEL * 0.5 + COPPER * 0.5) \
            + tint(glint * 0.8, WHITEHOT * 0.6 + STEEL * 0.4) + tint(blur(I, 30) * 0.4, AMBER)
        yield k, develop(hdr, exposure=1.5, frame=k, seed=seed, grain=0.08, bloom_k=0.45, vignette=0.55)
