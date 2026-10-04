"""Generate → **observe → classify → select**.

The generators in ``lab/`` know forces, frequencies and seeds. This module is
the other half of the experiment: it looks at what they made and names the
topology it finds, after the fact. Glyph-likeness is a discovery criterion
here, never an input anywhere.

Primitive operators, found on the skeleton of a thresholded image:

    stem    a long, nearly straight run
    hook    a run that is straight and then turns hard at one end
    arch    a run that turns ~180° with both ends on the same side
    fork    a junction with three or more substantial arms
    cavity  a hole: a region of dark fully enclosed by light
    near    a near-closure: a hole that exists only once a hair-gap is bridged

Composite readings (what a human would start calling letters, and what the
film never names on screen):

    bowl+stem   a cavity whose rim is tangent to a stem   (b d p q family)
    hooked fork a fork with one arm hooked                 (r ᚠ Y family)
    arch+legs   an arch whose ends continue as stems       (n u ᚢ family)

The edit uses this three ways: to *select* seeds and frames whose physics
happened to produce a strong operator; to *align* match cuts so the same
operator sits at the same place across two unrelated phenomena; and to
*capture* — to lift the skeleton around a found form out of one shot and hand
it, as polylines, to the next generator in the causal chain.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

N8 = [(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]


@dataclass
class Feature:
    op: str
    x: float
    y: float
    size: float
    score: float
    extra: dict = field(default_factory=dict)


# ── skeleton ───────────────────────────────────────────────────────────────


def binarize(I: np.ndarray, pct=88.0, open_=1) -> np.ndarray:
    I = I.astype(np.float32)
    I = cv2.GaussianBlur(I, (0, 0), 1.0)
    t = np.percentile(I, pct)
    B = (I > max(t, 1e-6)).astype(np.uint8)
    if open_:
        B = cv2.morphologyEx(B, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return B


def thin(B: np.ndarray, max_iter=200) -> np.ndarray:
    """Zhang–Suen thinning, vectorised."""
    S = np.pad(B.astype(np.uint8), 1)
    for _ in range(max_iter):
        changed = False
        for sub in (0, 1):
            P = [np.roll(np.roll(S, -dy, 0), -dx, 1) for dy, dx in N8]
            # P order (clockwise from N): p2..p9 = N, NE, E, SE, S, SW, W, NW
            p2, p3, p4, p5, p6, p7, p8, p9 = (P[1], P[2], P[3], P[4], P[5], P[6], P[7], P[0])
            nb = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            seq = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            A = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8) for i in range(8))
            if sub == 0:
                c = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                c = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            m = (S == 1) & (nb >= 2) & (nb <= 6) & (A == 1) & c
            if m.any():
                S[m] = 0
                changed = True
        if not changed:
            break
    return S[1:-1, 1:-1]


def crossings(S: np.ndarray) -> np.ndarray:
    """Crossing number: 0→1 transitions around each pixel's 8-ring. 1 at an
    end, 2 along a line (even on a staircase), ≥3 at a true junction."""
    Sp = np.pad(S.astype(np.uint8), 1)
    ring = [np.roll(np.roll(Sp, -dy, 0), -dx, 1)[1:-1, 1:-1] for dy, dx in N8]
    ring.append(ring[0])
    c = sum(((ring[i] == 0) & (ring[i + 1] == 1)).astype(np.int32) for i in range(8))
    return c * S.astype(np.int32)


def neighbours(S: np.ndarray) -> np.ndarray:
    k = np.ones((3, 3), np.float32)
    k[1, 1] = 0
    return (cv2.filter2D(S.astype(np.float32), -1, k, borderType=cv2.BORDER_CONSTANT) * S).astype(np.int32)


def trace_runs(S: np.ndarray, min_len=6):
    """Skeleton → pixel chains between nodes (endpoints / junctions), plus
    closed loops that have no node at all. Every skeleton pixel that is not a
    node belongs to exactly one chain."""
    S = S.astype(bool)
    h, w = S.shape
    nb = neighbours(S)
    cn = crossings(S)
    node = S & ((nb == 1) | (cn >= 3) | (nb == 0))
    # A junction is a small cluster; keep one pixel of each as its node.
    used = np.zeros_like(S)
    runs = []

    def nbrs(y, x):
        for dy, dx in N8:
            yy, xx = y + dy, x + dx
            if 0 <= yy < h and 0 <= xx < w and S[yy, xx]:
                yield yy, xx

    def walk(path):
        y, x = path[-1]
        while True:
            nxt = None
            for yy, xx in nbrs(y, x):
                if (yy, xx) == path[-2] if len(path) > 1 else False:
                    continue
                if node[yy, xx] and len(path) > 1:
                    nxt = (yy, xx)
                    break
                if not used[yy, xx] and not node[yy, xx]:
                    nxt = (yy, xx)
                    break
            if nxt is None:
                return path
            path.append(nxt)
            if node[nxt]:
                return path
            used[nxt] = True
            y, x = nxt

    ys, xs = np.nonzero(node)
    for sy, sx in zip(ys, xs):
        for yy, xx in nbrs(sy, sx):
            if node[yy, xx] or used[yy, xx]:
                continue
            used[yy, xx] = True
            p = walk([(sy, sx), (yy, xx)])
            if len(p) >= min_len:
                runs.append(np.array([(q[1], q[0]) for q in p], np.float32))
    # Pure loops.
    ys, xs = np.nonzero(S & ~used & ~node)
    for sy, sx in zip(ys, xs):
        if used[sy, sx]:
            continue
        used[sy, sx] = True
        p = walk([(sy, sx)])
        if len(p) >= min_len:
            p.append(p[0])
            runs.append(np.array([(q[1], q[0]) for q in p], np.float32))
    return runs, node, nb


# ── run geometry ──────────────────────────────────────────────────────────


def _turning(run, k=5):
    if len(run) < 2 * k + 2:
        return np.zeros(0)
    d = run[k:] - run[:-k]
    a = np.arctan2(d[:, 1], d[:, 0])
    da = np.diff(np.unwrap(a))
    return da


def classify_run(run):
    L = float(np.sum(np.hypot(*np.diff(run, axis=0).T)))
    chord = float(np.hypot(*(run[-1] - run[0])))
    straight = chord / (L + 1e-6)
    tt = _turning(run)
    total = float(np.sum(tt)) if len(tt) else 0.0
    ops = []
    if straight > 0.92 and L > 22:
        ops.append(("stem", straight * min(1, L / 60)))
    if len(tt) > 8:
        n = len(tt)
        head = abs(float(np.sum(tt[: n // 3])))
        tail = abs(float(np.sum(tt[-n // 3:])))
        mid = abs(float(np.sum(tt[n // 3: -n // 3])))
        if mid < 0.35 and max(head, tail) > 0.9:
            ops.append(("hook", min(1, max(head, tail) / 1.6) * min(1, L / 50)))
        if abs(abs(total) - math.pi) < 0.8 and straight < 0.75 and L > 30:
            ops.append(("arch", (1 - abs(abs(total) - math.pi) / 0.8) * min(1, L / 70)))
    return L, ops, total


# ── features ──────────────────────────────────────────────────────────────


def features(I: np.ndarray, pct=88.0, min_hole=40, max_hole_frac=0.08, scale=1.0):
    """All primitive and composite features of a luminance image."""
    B = binarize(I, pct)
    h, w = B.shape
    feats: list[Feature] = []
    S = thin(B)
    runs, node, nb = trace_runs(S)
    run_info = []
    m = 4
    for r in runs:
        if (r[:, 0].min() < m or r[:, 1].min() < m or r[:, 0].max() > w - m or r[:, 1].max() > h - m):
            continue  # the plate's rim is not a finding
        L, ops, total = classify_run(r)
        run_info.append((r, L, ops))
        for op, sc in ops:
            c = r[len(r) // 2]
            feats.append(Feature(op, float(c[0]) * scale, float(c[1]) * scale, L * scale, sc,
                                 {"run": r * scale}))
    # Forks: junction pixels with ≥3 long arms.
    jy, jx = np.nonzero(S.astype(bool) & (crossings(S) >= 3))
    if len(jx):
        ends = []
        for r, L, ops in run_info:
            ends.append((r[0], L, ops))
            ends.append((r[-1], L, ops))
        E = np.array([e[0] for e in ends]) if ends else np.zeros((0, 2))
        used = set()
        for x, y in zip(jx, jy):
            if (x // 4, y // 4) in used:
                continue
            used.add((x // 4, y // 4))
            if not len(E):
                break
            d = np.hypot(E[:, 0] - x, E[:, 1] - y)
            arms = [ends[i] for i in np.flatnonzero(d < 3.5)]
            long_arms = [a for a in arms if a[1] > 14]
            if len(long_arms) >= 3:
                sc = min(1.0, sum(sorted(a[1] for a in long_arms)[-3:]) / 150)
                hooked = any(op == "hook" for a in long_arms for op, _ in a[2])
                feats.append(Feature("fork", x * scale, y * scale, sc * 150 * scale, sc))
                if hooked:
                    feats.append(Feature("hooked fork", x * scale, y * scale, sc * 150 * scale, sc))
    # Cavities, bowls, arches: dark pockets measured by how much light encloses them.
    feats += pockets(B, scale=scale, max_r=max_hole_frac * min(w, h) * 4)
    # Composites.
    stems = [f for f in feats if f.op == "stem"]
    arches = [f for f in feats if f.op == "arch" and "run" in f.extra]
    for cav in [f for f in feats if f.op in ("cavity", "near", "bowl")]:
        for st in stems:
            run = st.extra["run"]
            d = np.min(np.hypot(run[:, 0] - cav.x, run[:, 1] - cav.y))
            if 0.4 * cav.size < d < 0.8 * cav.size and 1.3 * cav.size < st.size < 3.5 * cav.size \
                    and cav.score > 0.7:
                feats.append(Feature("bowl+stem", (cav.x + st.x) / 2, (cav.y + st.y) / 2,
                                     st.size, 0.5 * (cav.score + st.score),
                                     {"cavity": cav, "stem": st}))
    for ar in arches:
        run = ar.extra["run"]
        legs = 0
        for st in stems:
            r2 = st.extra["run"]
            for e in (run[0], run[-1]):
                if np.min(np.hypot(r2[:, 0] - e[0], r2[:, 1] - e[1])) < 4 * scale:
                    legs += 1
        if legs >= 2:
            feats.append(Feature("arch+legs", ar.x, ar.y, ar.size, ar.score))
    return feats


def pockets(B, scale=1.0, min_r=4.0, max_r=60.0, rays=24):
    """Dark pockets and how enclosed they are. From each local maximum of the
    distance-to-light, cast rays; the fraction that hits light within 3.2× the
    pocket radius is its enclosure. ≥0.92 cavity, 0.75–0.92 near-closure,
    0.5–0.75 an open pocket — a bowl if it opens upward, an arch if it opens
    downward, a hook-mouth sideways. The opening direction is the mean of the
    rays that escaped."""
    h, w = B.shape
    D = cv2.distanceTransform((1 - B).astype(np.uint8), cv2.DIST_L2, 5)
    mx = cv2.dilate(D, np.ones((9, 9), np.uint8))
    cand = np.argwhere((D >= mx - 1e-3) & (D >= min_r) & (D <= max_r))
    ang = np.linspace(0, 2 * np.pi, rays, endpoint=False)
    ca, sa = np.cos(ang), np.sin(ang)
    out = []
    taken = []
    order = np.argsort(-D[cand[:, 0], cand[:, 1]])
    for i in order:
        y, x = cand[i]
        r = float(D[y, x])
        if any(math.hypot(x - a, y - b) < 2.2 * max(r, rr) for a, b, rr in taken):
            continue
        reach = 3.2 * r
        ts = np.linspace(r * 0.9, reach, 24)
        X = np.clip((x + np.outer(ca, ts)).astype(int), 0, w - 1)
        Y = np.clip((y + np.outer(sa, ts)).astype(int), 0, h - 1)
        inside = ((x + np.outer(ca, ts)) >= 0) & ((x + np.outer(ca, ts)) < w) & \
                 ((y + np.outer(sa, ts)) >= 0) & ((y + np.outer(sa, ts)) < h)
        hit = (B[Y, X] > 0) & inside
        hits = hit.any(1)
        enc = float(hits.mean())
        if enc < 0.5:
            continue
        taken.append((x, y, r))
        miss = ~hits
        if miss.any():
            ox, oy = float(ca[miss].mean()), float(sa[miss].mean())
        else:
            ox, oy = 0.0, 0.0
        if enc >= 0.92:
            op = "cavity"
        elif enc >= 0.75:
            op = "near"
        elif oy < -0.5:
            op = "bowl"       # opens upward: cup, u
        elif oy > 0.5:
            op = "arch"       # opens downward: n
        else:
            op = "mouth"      # opens sideways: c
        out.append(Feature(op, x * scale, y * scale, r * 2 * scale, enc,
                           {"open": (ox, oy), "r": r * scale}))
    return out


def best(feats, op, near=None, radius=1e9, min_size=0.0):
    c = [f for f in feats if f.op == op and f.size >= min_size]
    if near is not None:
        c = [f for f in c if math.hypot(f.x - near[0], f.y - near[1]) < radius]
    if not c:
        return None
    if near is None:
        return max(c, key=lambda f: f.score * f.size)
    return max(c, key=lambda f: f.score * f.size / (1 + math.hypot(f.x - near[0], f.y - near[1]) / radius))


def census(feats):
    out = {}
    for f in feats:
        out[f.op] = out.get(f.op, 0) + 1
    return out


# ── capture: lift a found form out as polylines ───────────────────────────


def capture(I: np.ndarray, center, radius, pct=80.0, min_len=10, close=5, smooth=1.6):
    """The skeleton within ``radius`` of ``center``, as polylines. This is what
    the apparatus takes away from the phenomenon. Thresholded against the
    local region only (the instrument's exposure is set for what it frames),
    and hair-gaps closed — a measurement is always a little more continuous
    than the thing measured."""
    cy, cx = int(center[1]), int(center[0])
    m = np.zeros(I.shape, np.uint8)
    cv2.circle(m, (cx, cy), int(radius), 1, -1)
    J = cv2.GaussianBlur(I.astype(np.float32), (0, 0), smooth)
    t = np.percentile(J[m > 0], pct)
    B = ((J > t) & (m > 0)).astype(np.uint8)
    if close:
        B = cv2.morphologyEx(B, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close, close)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(B)
    keep = np.zeros_like(B)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 25:
            keep[lab == i] = 1
    S = thin(keep)
    runs, _, _ = trace_runs(S, min_len)
    return [r for r in runs if len(r) >= min_len]


def simplify(polys, eps=3.0):
    """Douglas–Peucker: abstraction as an algorithm. What survives is what a
    hand would keep."""
    out = []
    for p in polys:
        q = cv2.approxPolyDP(np.asarray(p, np.float32).reshape(-1, 1, 2), eps, False).reshape(-1, 2)
        if len(q) >= 2:
            out.append(q.astype(np.float32))
    return out


def manhattan(polys, grid=8.0, diag=True):
    """Lithography: snap every segment to 0/45/90° on a grid. A form becomes
    a layout."""
    out = []
    for p in polys:
        p = np.round(np.asarray(p, np.float32) / grid) * grid
        q = [p[0]]
        for a, b in zip(p[:-1], p[1:]):
            dx, dy = b - a
            if diag and abs(abs(dx) - abs(dy)) < grid:
                q.append(b)
                continue
            if abs(dx) > abs(dy):
                q.append(np.array([b[0], a[1]]))
            else:
                q.append(np.array([a[0], b[1]]))
            q.append(b)
        out.append(np.array(q, np.float32))
    return out
