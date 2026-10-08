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


def _save_anchor(name, data):
    import json
    import world as Wd
    Wd.STATE.mkdir(parents=True, exist_ok=True)
    (Wd.STATE / f"anchor_{name}.json").write_text(json.dumps(data))


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
            # Record where the hero arch stands on screen: recognition events
            # align a carved ``n`` to exactly this arch.
            m = mask[hero]
            P0 = xy0[hero][m] * scale + np.array(center)
            _save_anchor("corona_arch", {"apex": [float(P_ARCH[0]), float(P_ARCH[1])],
                                         "feet": [P0[0].tolist(), P0[-1].tolist()], "limb": limb_y})
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


# ── sun: observed, not simulated — a scale revelation ─────────────────────

SUN_LEN = 64
SUN_HOLD = (14, 34)  # pull-out window


def _slot(k, seed):
    """Where a missing source belongs: dark, one registration cross."""
    img = np.zeros((H, W, 3), np.float32)
    cv2.line(img, (W // 2 - 30, H // 2), (W // 2 + 30, H // 2), (0.4, 0.4, 0.4), 1)
    cv2.line(img, (W // 2, H // 2 - 30), (W // 2, H // 2 + 30), (0.4, 0.4, 0.4), 1)
    return develop(img, exposure=1.0, frame=k, seed=seed, grain=0.08, bloom_k=0)


CHANNELS = {
    # EUI 17.4 nm sits where AIA 17.1 does; its conventional rendering is gold.
    "gold": (np.array([1.0, 0.62, 0.18], np.float32), 1.0),
    "halpha": (np.array([1.0, 0.035, 0.03], np.float32), 0.75),
    "uv": (np.array([0.55, 0.12, 1.0], np.float32), 0.8),
    "cyan": (np.array([0.35, 0.85, 1.0], np.float32), 1.0),
}


def sun(need, seed=51, channel="gold", path="reveal"):
    """``path='reveal'``: inside an active region's loops, held; then the
    camera falls back until the loops are a speck on the limb of a star.
    ``path='loops'``: the held close crop only (for inserts in other channels)."""
    import sources as SRCS
    a = SRCS.load("eui174")
    if a is None:
        for k in sorted(need):
            yield k, _slot(k, seed)
        return
    v = SRCS.normalise(a, lo=2, hi=99.95, gamma=0.8)
    v = np.clip((v - 0.08) / 0.92, 0, 1)      # crush the JPEG2000 floor to black
    lx, ly = SRCS.EUI_LOOPS
    dx_, dy_, dr = SRCS.EUI_DISK
    col, gain = CHANNELS[channel]
    shimmer = [fbm(W // 8, H // 8, seed + i, 3, 3) for i in range(2)]
    for k in sorted(need):
        if path == "loops":
            t = 0.0
        else:
            t = float(smoothstep(SUN_HOLD[0], SUN_HOLD[1], k)) ** 1.6
        # Log-zoom: scale screen px per source px from 2.4 to 0.5.
        s = math.exp(math.log(2.4) * (1 - t) + math.log(0.52) * t)
        cx = lx * (1 - t) + dx_ * t + 6 * math.sin(k * 0.13)
        cy = ly * (1 - t) + dy_ * t + 4 * math.cos(k * 0.11)
        M = np.float32([[s, 0, W / 2 - s * cx], [0, s, H / 2 - s * cy]])
        img = cv2.warpAffine(v, M, (W, H), flags=cv2.INTER_CUBIC, borderValue=0.0)
        # The corona is alive even in a still: a slow intensity boil.
        boil = up(shimmer[0] * math.cos(k * 0.21) + shimmer[1] * math.sin(k * 0.17))
        img = np.clip(img * (1 + 0.06 * boil), 0, 1)
        glow = col * 0.6 + AMBER * 0.4 if channel == "gold" else col
        hdr = tint(img ** 2.4 * 2.0 * gain, col) + tint(blur(img, 20) ** 2 * 0.25, glow)
        ex = 1.1 * (1 + 1.5 * math.exp(-k / 2.5)) if path == "reveal" else 1.2
        yield k, develop(hdr, exposure=ex, frame=k, seed=seed, grain=0.07, bloom_k=0.55,
                         star=0.01 * t, vignette=0.5)


# ── belt: dust in a beam turns out to be a ring around a world ────────────

BELT_LEN = 60
BELT_PULL = (14, 34)


def _belt_cam(k):
    t = float(smoothstep(BELT_PULL[0], BELT_PULL[1], k)) ** 1.4
    drift = 0.002 * k
    # Inside the ring plane, a hair above it, looking along the ring...
    near_c = np.array([1.95 + drift, -0.15, 0.010])
    near_t = np.array([3.0, 1.25, -0.02])
    # ...to far away and above, the planet's limb cut by the frame edge.
    far_c = np.array([2.7, -3.1, 0.62])
    far_t = np.array([0.15, 0.9, 0.05])
    # Log-interpolate the distance so the pull-back accelerates like a zoom.
    u = (math.exp(2.2 * t) - 1) / (math.exp(2.2) - 1)
    c = near_c * (1 - u) + far_c * u
    tg = near_t * (1 - u) + far_t * u
    return c, tg, t


def belt(need, seed=61):
    from lab.rings import Rings, look, planet, project
    rg = Rings(seed=seed)
    focal = 1300.0
    for k in sorted(need):
        cam, tg, t = _belt_cam(k)
        R = look(cam, tg)
        P = rg.positions(40.0 + 0.04 * k)
        xy, d, ok = project(P, cam, R, focal, W, H)
        lit = rg.lit(P)
        I_p, depth_p, hit, limb = planet(cam, R, focal, W, H, rg.sun, rg, seed=seed)
        # Particles behind the planet are hidden.
        xi = np.clip(xy[:, 0].astype(int), 0, W - 1)
        yi = np.clip(xy[:, 1].astype(int), 0, H - 1)
        ok &= d < depth_p[yi, xi]
        ok &= (xy[:, 0] > -50) & (xy[:, 0] < W + 50) & (xy[:, 1] > -50) & (xy[:, 1] < H + 50)
        # Constant surface brightness: per-particle light falls as 1/d², the
        # number of particles per pixel rises as d².
        lum = rg.albedo * lit * 3.2 / np.maximum(d, 0.07) ** 2 * (0.35 + 0.65 * t)
        # Depth of field: the nearest dust is enormous and soft.
        hdr_p = np.zeros((H, W), np.float32)
        layers = [(0, 0.08, 9.0), (0.08, 0.3, 3.5), (0.3, 1.2, 1.2), (1.2, 99, 0.7)]
        for lo, hi, bl in layers:
            sel = ok & (d >= lo) & (d < hi)
            if sel.any():
                hdr_p += blur(splat(xy[sel], lum[sel] * (1 + 4 * (hi < 0.1))), bl)
        hdr_p *= 1 + 0.0 * t
        sky = 0.0
        hdr = tint(hdr_p * (0.8 + 1.4 * t), BONE * 0.55 + AMBER * 0.45) \
            + tint(I_p * 0.45 + limb * 0.2 * hit, AMBER * 0.55 + COPPER * 0.45) + sky
        yield k, develop(hdr, exposure=1.4, frame=k, seed=seed, grain=0.07, bloom_k=0.5, vignette=0.5)


# ── raw: the instrument's own frame, before anyone made it beautiful ──────


def raw(need, seed=71, which="aia171_raw"):
    """A real 128×128 detector frame shown at its own resolution: nearest-
    neighbour pixels, a hard linear stretch, a grey ramp. Almost ugly, on
    purpose — between pristine images it says *this was measured*."""
    import sources as SRCS
    a = SRCS.load(which)
    for k in sorted(need):
        if a is None:
            yield k, _slot(k, seed)
            continue
        v = SRCS.normalise(a, lo=1, hi=99.5, gamma=0.7)
        # A sub-crop, so each frame is a different patch of the detector.
        rng = np.random.default_rng(seed + k)
        y0, x0 = rng.integers(10, 50, 2)
        crop = v[y0:y0 + 72, x0:x0 + 128]
        img = cv2.resize(crop, (W, int(W * crop.shape[0] / crop.shape[1])), interpolation=cv2.INTER_NEAREST)
        out = np.zeros((H, W), np.float32)
        hh = min(H, img.shape[0])
        out[:hh] = img[:hh]
        rgb = np.stack([out] * 3, -1) * np.array([0.92, 0.95, 1.0], np.float32)
        out8 = (np.clip(rgb, 0, 1) ** (1 / 1.8) * 255).astype(np.uint8)
        yield k, out8
