"""A channel that grows toward charge: dielectric breakdown, leaf venation,
a crack running through stressed rock. One algorithm, three materials.

Space colonisation (Runions et al. 2005): a scatter of attractors pulls the
nearest growing tip; tips step toward the mean direction of the attractors
they own, plus noise; attractors within the kill radius are consumed. The
glyph supplies a dense attractor set along its skeleton; a sparse random set
around it gives the stray branches every real discharge has. The root sits
off the glyph — the channel has to *find* the form, hesitate at the first
branch, and commit.

The result is a tree with a birth step per node. A shot reveals it up to a
step (the leader), then fires the whole committed path at once (the return
stroke) — the moment a near-form becomes a mark.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree
import cv2

from glyphs import SKELETONS, Placement, place, resample


class Tree:
    def __init__(self, pos, parent, birth, charge=None):
        self.pos = np.asarray(pos, np.float32)
        self.parent = np.asarray(parent, np.int32)
        self.birth = np.asarray(birth, np.int32)
        n = len(self.pos)
        desc = np.ones(n, np.float32)
        for i in range(n - 1, 0, -1):  # children always born after parents
            desc[self.parent[i]] += desc[i]
        self.desc = desc
        # How much of the glyph's charge each node carries: 1 on the skeleton,
        # falling off over ~2 steps. The committed path is what the glyph owned.
        if charge is not None and len(charge):
            dk, _ = cKDTree(charge).query(self.pos)
            on = np.exp(-(dk / 9.0) ** 2).astype(np.float32)
        else:
            on = np.zeros(n, np.float32)
        # A node also inherits charge from the path that led to it (the trunk).
        lead = on.copy()
        for i in range(n - 1, 0, -1):
            lead[self.parent[i]] = max(lead[self.parent[i]], lead[i] * 0.985)
        self.on = np.maximum(on, lead * 0.7)
        self.steps = int(self.birth.max()) if n else 0


# Where each glyph's leader enters (em coords, usually beyond a stroke end):
# the channel must travel into the form before it can trace it.
ROOT_EM = {"l": (0.5, 1.1), "r": (0.40, 0.95), "Y": (0.5, 1.1), "V": (0.5, 1.05),
           "^": (0.18, 0.8), "A": (0.5, -0.05), "ᚠ": (0.38, 1.1), "ᚱ": (0.36, 1.1),
           "ᛉ": (0.5, 1.1), "⤙": (-0.05, 0.5)}


def root_of(g: str, pl: Placement):
    return place([[ROOT_EM.get(g, (0.5, 1.1))]], pl)[0][0]


def grow(g: str, pl: Placement, root=None, *, seed=0, step=5.0, influence=46.0,
         kill=7.0, stray=70, stray_box=None, wobble=0.03, density=5.0,
         noise=0.35, max_iter=700) -> Tree:
    rng = np.random.default_rng(seed)
    if root is None:
        root = root_of(g, pl)
    pts = resample(place(SKELETONS[g], pl, wobble, seed), density)
    pts = pts + rng.normal(0, 1.6, pts.shape)
    if stray:
        if stray_box is None:
            s = pl.size
            stray_box = (pl.cx - 0.45 * s, pl.cy - 0.5 * s, pl.cx + 0.45 * s, pl.cy + 0.5 * s)
        x0, y0, x1, y1 = stray_box
        pts = np.concatenate([pts, rng.uniform([x0, y0], [x1, y1], (stray, 2))])
    glyph_pts = pts[: len(pts) - stray] if stray else pts
    att = pts.astype(np.float64)
    alive = np.ones(len(att), bool)
    nodes = [np.asarray(root, np.float64)]
    parent = [-1]
    birth = [0]
    it = 0
    while alive.any() and it < max_iter:
        it += 1
        P = np.asarray(nodes)
        tree = cKDTree(P)
        A = att[alive]
        d, owner = tree.query(A, distance_upper_bound=influence)
        ok = np.isfinite(d)
        if not ok.any():
            # Nothing in reach: the leader stalls and creeps toward the nearest charge.
            d2, o2 = tree.query(A)
            j = int(np.argmin(d2))
            owner = np.array([o2[j]])
            A = A[j:j + 1]
            ok = np.array([True])
        dirs = {}
        for a, o in zip(A[ok], owner[ok]):
            v = a - P[o]
            n = np.linalg.norm(v)
            if n > 1e-6:
                dirs.setdefault(int(o), []).append(v / n)
        new = []
        for o, vs in dirs.items():
            v = np.mean(vs, 0)
            v = v / (np.linalg.norm(v) + 1e-9) + rng.normal(0, noise, 2)
            v = v / (np.linalg.norm(v) + 1e-9)
            new.append((P[o] + step * v, o))
        for p, o in new:
            nodes.append(p)
            parent.append(o)
            birth.append(it)
        # Consume attractors reached by any node.
        P = np.asarray(nodes)
        dk, _ = cKDTree(P).query(att[alive])
        idx = np.flatnonzero(alive)
        alive[idx[dk < kill]] = False
    return Tree(nodes, parent, birth, glyph_pts)


def draw(tree: Tree, w: int, h: int, upto: float, *, tremble=0.0, seed=0,
         core=1.0, tip_glow=2.5, prune=1.0, return_stroke=0.0) -> np.ndarray:
    """Render the channel network as linear light.

    ``upto``: leader progress in growth steps. ``return_stroke``: 0..1 — the
    whole committed path firing at once, weighted toward the main channel.
    ``prune`` hides minor side branches (desc < prune)."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w), np.float32)
    P = tree.pos + (rng.normal(0, tremble, tree.pos.shape).astype(np.float32) if tremble else 0)
    vis = (tree.birth <= upto) & (tree.desc >= prune)
    vis[0] = True
    order = np.flatnonzero(vis)[1:]
    dmax = tree.desc.max()
    for i in order:
        j = tree.parent[i]
        if j < 0:
            continue
        dd = tree.desc[i]
        on = tree.on[i]
        lum = core * (0.12 + 0.25 * (dd / dmax) ** 0.35 + 0.9 * on)
        age = upto - tree.birth[i]
        if age < 4:
            lum += tip_glow * (1 - age / 4)
        lum += return_stroke * (4.0 * on + 0.6 * (dd / dmax) ** 0.5)
        th = 1 + int(1.5 * (dd / dmax) ** 0.5 + 2.0 * on * (0.5 + return_stroke))
        a = (int(P[j, 0] * 4), int(P[j, 1] * 4))
        b = (int(P[i, 0] * 4), int(P[i, 1] * 4))
        cv2.line(img, a, b, float(lum), th, cv2.LINE_8, shift=2)
    return img
