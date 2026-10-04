"""The causal spine: what each act inherits from the one before.

Shots are pictures; this module is the world they are pictures *of*. It holds
the few objects that pass from one phenomenon to the next, so that the
sequence is a chain of inheritance rather than a montage:

    lightning tree ──scar──▶ membrane boundary condition
    membrane grains ──observe/classify──▶ found form
    found form ──photogram──▶ captured polylines
    captured polylines ──Douglas–Peucker──▶ die
    die ──manhattan──▶ layout ──route──▶ the live thread

Every object here is computed (and cached under ``out/state``) from seeds and
physical parameters only. The lightning tree is *selected*, not designed:
twelve discharges are grown and the one whose topology the observer rates
highest is kept. The found form is whatever the observer rates highest on
the frozen plate — nobody tells it what to look for beyond the operator
vocabulary.
"""
from __future__ import annotations

import json
import math
import os
import pickle
from pathlib import Path

import cv2
import numpy as np

import observe as OB
from lab import fbm, smoothstep
from lab import breakdown as BD
from lab.membrane import Membrane

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("MYTHOS_OUT", ROOT / "out"))
STATE = OUT / "state"

# The plate: simulated at NX×NY, drawn at PX× that ("plate px").
NX, NY, PX = 360, 200, 4
PW, PH = NX * PX, NY * PX

# The film's one shared rhythm. The gap body in the disk orbits in RHYTHM
# frames; the membrane's drive beats at it; the last pulse is keyed to it.
RHYTHM = 18


def _cached(name):
    def deco(fn):
        def wrap(*a, **kw):
            STATE.mkdir(parents=True, exist_ok=True)
            p = STATE / f"{name}.pkl"
            if p.exists():
                with open(p, "rb") as f:
                    return pickle.load(f)
            v = fn(*a, **kw)
            with open(p, "wb") as f:
                pickle.dump(v, f)
            return v
        return wrap
    return deco


# ── 1. the discharge, selected by observation ──────────────────────────────


def _grow(seed):
    rng = np.random.default_rng(seed)
    w, h = NX, NY
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    x0 = w * rng.uniform(0.38, 0.55)
    # Charge density: rises toward the grounded far edge; inclusions from fbm.
    dens = (0.12 + yy / h) ** 2.2 * np.exp(0.9 * fbm(w, h, seed + 10, 5, 4))
    dens *= np.exp(-((xx - x0) / (w * 0.36)) ** 2)
    att = BD.scatter(dens, 1100, rng, scale=PX)
    tree = BD.grow((x0 * PX, 6.0), att, seed=seed, step=9, influence=95, kill=15,
                   noise=0.45, bias=(0, 0.22))
    # The return stroke takes the leader that reached ground.
    tip = int(np.argmax(tree.pos[:, 1]))
    tree.set_main(tip)
    return tree


def rate_tree(tree):
    """How strongly a discharge shows the fork/hook operators near the middle.
    Topology is read straight off the tree: a fork is a node on the main
    channel where a heavy side branch leaves at a decisive angle; a hook is a
    side branch whose own run the observer classifies as hooked."""
    if tree.pos[tree.tip, 1] < PH * 0.9:
        return -1.0, None
    best, where = 0.0, None
    dmax = tree.desc.max()
    for i in np.flatnonzero(tree.main > 0):
        kids = tree.children[i]
        side = [c for c in kids if tree.main[c] == 0 and tree.desc[c] > 0.08 * dmax]
        if not side:
            continue
        on = [c for c in kids if tree.main[c] > 0]
        if not on:
            continue
        for c in side:
            # Branch angle between the main continuation and the side branch,
            # measured a few nodes downstream.
            def walk(j, n=6):
                for _ in range(n):
                    ks = tree.children[j]
                    if not ks:
                        break
                    j = max(ks, key=lambda q: tree.desc[q])
                return tree.pos[j] - tree.pos[i]
            a, b = walk(on[0]), walk(c)
            ang = abs(math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a @ b)))
            mid = 1 - abs(tree.pos[i, 1] / PH - 0.42) * 2
            cx = 1 - abs(tree.pos[i, 0] / PW - 0.5) * 2
            s = (tree.desc[c] / dmax) ** 0.5 * math.exp(-((ang - 40) / 22) ** 2) * max(mid, 0) * max(cx, 0)
            if s > best:
                best, where = s, (float(tree.pos[i, 0]), float(tree.pos[i, 1]), ang)
    hooks = 0
    for p in tree.polylines(min_desc=0.03 * dmax)[:24]:
        _, ops, _ = OB.classify_run(p[:, :2])
        hooks += sum(1 for op, sc in ops if op == "hook" and sc > 0.5)
    return best * (1 + 0.15 * min(hooks, 4)), where


@_cached("lightning")
def lightning():
    """Grow twelve discharges; keep the one the observer rates highest."""
    ranked = []
    for seed in range(12):
        t = _grow(seed)
        s, where = rate_tree(t)
        ranked.append((s, seed, where))
    ranked.sort(reverse=True)
    s, seed, where = ranked[0]
    tree = _grow(seed)
    return {"tree": tree, "seed": seed, "score": s, "fork": where,
            "ranking": [(round(a, 4), b) for a, b, _ in ranked]}


def scar_map():
    L = lightning()
    return BD.scar(L["tree"], PW, PH, width=1.6, prune=0.004 * L["tree"].desc.max())


# ── 2. the plate: one continuous world from the strike to the freeze ──────

# Plate timeline (local frames of the `plate` shot).
STRIKE = (0, 54)       # leader → return stroke at STRIKE_RS → cooling
STRIKE_RS = 38
REST = (54, 96)        # the stare: the scar cooling, grains unorganised
DRIVE_ON = 90          # the drive starts (grains begin to tremble under the stare)
VIBRATE = (96, 196)    # shaken into near-forms
OBSERVE = (196, 262)   # polariser, slit, raster; the scan perturbs
FREEZE = 248           # the drive is cut so the plate can be measured
PLATE_LEN = 262


def drive_freq(k):
    """The drive sweep. Slow glides between resonant bands, with abrupt small
    retunings that destroy whatever near-form had settled."""
    if k < DRIVE_ON:
        return 0.0
    t = k - DRIVE_ON
    base = np.interp(t, [0, 30, 60, 90, 120, 150, 170],
                     [0.0105, 0.0128, 0.0172, 0.0150, 0.0201, 0.0186, 0.0178])
    jump = 0.0012 * (t > 44) - 0.0016 * (t > 78) + 0.0011 * (t > 112)
    return float(base + jump)


def drive_amp(k):
    if k < DRIVE_ON or k >= FREEZE:
        return 0.0
    beat = 0.75 + 0.25 * math.cos(2 * math.pi * (k - DRIVE_ON) / RHYTHM)
    return float(smoothstep(DRIVE_ON, DRIVE_ON + 10, k)) * beat


def scan_row(k):
    """Raster acquisition sweeps the plate (sim rows) during the observation."""
    a, b = 214, 246
    if not (a <= k < b):
        return None
    return (k - a) / (b - a) * (NY + 10) - 5


def plate_sim(upto, seed=7):
    """Yield (k, membrane) for k in [0, upto): the plate world, stepped."""
    m = Membrane(NX, NY, seed=seed, n_grains=210_000, scar=scar_map(), pin=0.16,
                 damping=0.0045, drive=(0.71, 0.33))
    for k in range(upto):
        if k == STRIKE_RS:
            # The return stroke blasts the dust off its own channel: the scar
            # starts clean, and only the vibration can bring grains back to it.
            x = np.clip(m.p[:, 0].astype(int), 0, NX - 1)
            y = np.clip(m.p[:, 1].astype(int), 0, NY - 1)
            hit = m.scar[y, x] > 0.15
            ang = m.rng.uniform(0, 2 * np.pi, hit.sum())
            r = m.rng.uniform(2.5, 9.0, hit.sum()) * (0.5 + m.scar[y[hit], x[hit]])
            m.p[hit] += np.stack([np.cos(ang), np.sin(ang)], 1).astype(np.float32) * r[:, None]
            np.clip(m.p[:, 0], 0.5, NX - 1.5, out=m.p[:, 0])
            np.clip(m.p[:, 1], 0.5, NY - 1.5, out=m.p[:, 1])
            m.prev = m.p.copy()
        f = drive_freq(k)
        a = drive_amp(k)
        if a > 0 or k >= FREEZE:
            m.run(f if f > 0 else 0.015, steps=150, amp=a)
        kick = None
        row = scan_row(k)
        if row is not None:
            kick = m.probe(row, width=2.5, strength=1.6)
        if k >= FREEZE:
            # Drive off: amplitude rings down, grains settle where they are.
            m.shake(amp=0.4 * math.exp(-(k - FREEZE) / 3), drift=1.0, kick=kick)
        elif k >= DRIVE_ON:
            m.shake(amp=1.5 * (0.3 + 0.7 * smoothstep(DRIVE_ON, DRIVE_ON + 14, k)), kick=kick)
        yield k, m


# ── 3. what the observer found, and what the apparatus took away ──────────


def grain_image(m, scale=2):
    from optics import splat, blur
    return blur(splat(m.p * scale, None, NX * scale, NY * scale), 0.8)


@_cached("found")
def found():
    """Freeze the plate, classify it, choose a form, capture it."""
    m = None
    for k, m in plate_sim(PLATE_LEN):
        pass
    sc = 2
    I = grain_image(m, sc)
    feats = OB.features(I, pct=89)
    L = lightning()
    fx, fy, _ = L["fork"]
    fork = (fx / PX * sc, fy / PX * sc)
    # The observer prefers a strong closed form that sits against the scar's
    # fork — where the two histories (lightning, vibration) touch.
    best, bscore = None, -1
    for f in feats:
        if f.op not in ("cavity", "near", "bowl", "arch", "mouth", "bowl+stem"):
            continue
        d = math.hypot(f.x - fork[0], f.y - fork[1])
        w = {"bowl+stem": 1.6, "cavity": 1.2, "near": 1.0, "bowl": 0.9, "arch": 0.9, "mouth": 0.7}[f.op]
        s = w * f.score * min(1.0, f.size / 40) * math.exp(-d / 120)
        if s > bscore:
            best, bscore = f, s
    if best is None:
        best = OB.Feature("fork", fork[0], fork[1], 60, 0.0)
    cx, cy = best.x, best.y
    R = max(70.0, 2.4 * best.size)
    # Capture: the grains' skeleton plus the scar's, around the found form.
    scar = cv2.resize(scar_map(), (NX * sc, NY * sc), interpolation=cv2.INTER_AREA)
    both = np.maximum(I / (np.percentile(I, 99.5) + 1e-6), scar * 0.9)
    polys = OB.capture(both, (cx, cy), R, pct=84, min_len=14)
    # Root the capture where the main channel enters the region.
    tree = L["tree"]
    mp = tree.pos[tree.main > 0] / PX * sc
    din = np.hypot(mp[:, 0] - cx, mp[:, 1] - cy)
    root = mp[np.argmax(din < R)] if (din < R).any() else np.array([cx, cy - R])
    polys.sort(key=lambda p: min(np.hypot(*(p[0] - root)), np.hypot(*(p[-1] - root))))
    polys = [p if np.hypot(*(p[0] - root)) <= np.hypot(*(p[-1] - root)) else p[::-1] for p in polys]
    die = OB.simplify(polys, eps=2.2)
    die = [d for d in die if np.sum(np.hypot(*np.diff(d, axis=0).T)) > 14]
    return {"grains": m.p.copy(), "image": I, "scale": sc, "form": (best.op, cx, cy, best.size, best.score),
            "R": R, "polys": polys, "die": die, "root": root,
            "census": OB.census(feats), "fork": fork}


def layout(die, grid=6.0, exit_x=None, pitch=None):
    """The die as a lithographic layout, with a bus leaving it: each stroke's
    far end is routed out to the right on its own track — a trace becomes a
    circuit by being connected to elsewhere. Tracks are assigned in order of
    height so routes never cross."""
    lay = OB.manhattan(die, grid=grid)
    ends = []
    for p in lay:
        a, b = p[0], p[-1]
        ends.append(b if b[0] >= a[0] else a)
    ends.sort(key=lambda e: e[1])
    if not ends:
        return lay, []
    pitch = pitch or grid * 3
    y0 = float(np.mean([e[1] for e in ends])) - pitch * (len(ends) - 1) / 2
    x_turn = max(e[0] for e in ends) + grid * 4
    routes = []
    for i, e in enumerate(ends):
        ty = round((y0 + i * pitch) / grid) * grid
        xj = x_turn + grid * 2 * (i if ty < e[1] else len(ends) - i)
        pts = [(float(e[0]), float(e[1])), (float(xj), float(e[1])), (float(xj + abs(ty - e[1])), float(ty)),
               (float(exit_x if exit_x else xj + 4000), float(ty))]
        routes.append(np.array(pts, np.float32))
    return lay, routes


def summary():
    L = lightning()
    F = found()
    return {"lightning_seed": L["seed"], "lightning_ranking": L["ranking"], "fork": L["fork"],
            "found": F["form"][:1] + tuple(round(v, 2) for v in F["form"][1:]),
            "census": F["census"], "die_strokes": len(F["die"])}


if __name__ == "__main__":
    print(json.dumps(summary(), indent=1, default=str))
