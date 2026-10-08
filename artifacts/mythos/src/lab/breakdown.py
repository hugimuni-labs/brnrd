"""A channel that grows up a potential: dielectric breakdown, a crack in
stressed rock, a crystal dendrite in a cooling seam. One law, three materials.

Space colonisation (Runions et al. 2005), driven by a *field* rather than a
target. Attractors are scattered with probability proportional to a charge /
stress density map (any array the caller builds from physical terms — an
electrode's potential, fbm inclusions, a mineral vein). The nearest growing
tip claims each attractor; tips step toward the mean direction of the
attractors they own, plus noise; attractors within the kill radius are
consumed. Nothing in here knows what a letter is.

``aniso`` snaps every step toward a lattice of preferred directions — the
crystal habit. ``aniso=0`` is lightning; ``aniso=6`` is ice or a metal
dendrite; ``aniso=4`` is a cubic salt.

The tree records a birth step per node, so a shot reveals it as a leader (up
to a step, with hesitations where it stalled) and then fires the main channel
at once — the return stroke.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from scipy.spatial import cKDTree


class Tree:
    def __init__(self, pos, parent, birth):
        self.pos = np.asarray(pos, np.float32)
        self.parent = np.asarray(parent, np.int32)
        self.birth = np.asarray(birth, np.int32)
        n = len(self.pos)
        desc = np.ones(n, np.float32)
        for i in range(n - 1, 0, -1):  # children are always born after parents
            desc[self.parent[i]] += desc[i]
        self.desc = desc
        self.steps = int(self.birth.max()) if n else 0
        # Main channel: from the root, always follow the heaviest child.
        children = [[] for _ in range(n)]
        for i in range(1, n):
            children[self.parent[i]].append(i)
        self.children = children
        main = np.zeros(n, np.float32)
        i = 0
        while True:
            main[i] = 1.0
            if not children[i]:
                break
            i = max(children[i], key=lambda c: desc[c])
        self.main = main
        self.tip = i

    def set_main(self, tip: int):
        """Make the path root→``tip`` the main channel (the one the return
        stroke takes — the leader that reached ground, not the heaviest)."""
        main = np.zeros(len(self.pos), np.float32)
        i = tip
        while i >= 0:
            main[i] = 1.0
            i = self.parent[i]
        self.main = main
        self.tip = tip

    def polylines(self, min_desc=1.0):
        """The tree as polylines (root→tip chains), heaviest first. Each branch
        starts on its parent so arc length stays continuous."""
        n = len(self.pos)
        seen = np.zeros(n, bool)
        polys = []
        order = np.argsort(-self.desc)
        for start in order:
            if seen[start] or self.desc[start] < min_desc:
                continue
            chain = [start]
            seen[start] = True
            i = start
            while True:
                kids = [c for c in self.children[i] if not seen[c] and self.desc[c] >= min_desc]
                if not kids:
                    break
                i = max(kids, key=lambda c: self.desc[c])
                chain.append(i)
                seen[i] = True
            p = self.parent[start]
            if p >= 0:
                chain = [p] + chain
            if len(chain) >= 2:
                polys.append(self.pos[chain])
        return polys


def scatter(density: np.ndarray, n: int, rng, scale=1.0):
    """n attractor points distributed ∝ density (an h×w map), in pixels × scale."""
    p = np.clip(density, 0, None).ravel().astype(np.float64)
    p /= p.sum()
    idx = rng.choice(len(p), n, p=p)
    h, w = density.shape
    y, x = np.divmod(idx, w)
    pts = np.stack([x, y], 1).astype(np.float64) + rng.uniform(0, 1, (n, 2))
    return pts * scale


def grow(root, attractors, *, seed=0, step=5.0, influence=46.0, kill=7.0,
         noise=0.35, aniso=0, aniso_k=0.0, aniso_rot=0.0, max_iter=900,
         bias=None) -> Tree:
    """Grow from ``root`` through ``attractors``. ``bias``: optional unit vector
    added every step (the field's mean direction — a far electrode)."""
    rng = np.random.default_rng(seed)
    att = np.asarray(attractors, np.float64)
    alive = np.ones(len(att), bool)
    nodes = [np.asarray(root, np.float64)]
    parent = [-1]
    birth = [0]
    it = 0
    if aniso:
        lattice = np.array([[math.cos(aniso_rot + 2 * math.pi * k / aniso),
                             math.sin(aniso_rot + 2 * math.pi * k / aniso)] for k in range(aniso)])
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
        dirs: dict[int, list] = {}
        for a, o in zip(A[ok], owner[ok]):
            v = a - P[o]
            nv = np.linalg.norm(v)
            if nv > 1e-6:
                dirs.setdefault(int(o), []).append(v / nv)
        for o, vs in dirs.items():
            v = np.mean(vs, 0)
            v = v / (np.linalg.norm(v) + 1e-9)
            if bias is not None:
                v = v + np.asarray(bias)
                v /= np.linalg.norm(v) + 1e-9
            v = v + rng.normal(0, noise, 2)
            v = v / (np.linalg.norm(v) + 1e-9)
            if aniso:
                j = int(np.argmax(lattice @ v))
                v = (1 - aniso_k) * v + aniso_k * lattice[j]
                v /= np.linalg.norm(v) + 1e-9
            nodes.append(P[o] + step * v)
            parent.append(o)
            birth.append(it)
        P = np.asarray(nodes)
        dk, _ = cKDTree(P).query(att[alive])
        idx = np.flatnonzero(alive)
        alive[idx[dk < kill]] = False
    return Tree(nodes, parent, birth)


def draw(tree: Tree, w: int, h: int, upto: float, *, tremble=0.0, seed=0, core=1.0,
         tip_glow=2.5, prune=1.0, return_stroke=0.0, scale=1.0, offset=(0.0, 0.0),
         width=1.0) -> np.ndarray:
    """The channel network as linear light at w×h (tree coords × scale + offset).

    ``upto``: leader progress in growth steps. ``return_stroke`` 0..1: the
    main channel firing at once. ``prune`` hides minor side branches."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w), np.float32)
    P = tree.pos * scale + np.asarray(offset, np.float32)
    if tremble:
        P = P + rng.normal(0, tremble, P.shape).astype(np.float32)
    vis = (tree.birth <= upto) & (tree.desc >= prune)
    vis[0] = True
    dmax = tree.desc.max()
    for i in np.flatnonzero(vis)[1:]:
        j = tree.parent[i]
        dd = (tree.desc[i] / dmax) ** 0.4
        on = tree.main[i]
        lum = core * (0.10 + 0.5 * dd + 0.6 * on)
        age = upto - tree.birth[i]
        if age < 4:
            lum += tip_glow * (1 - age / 4)
        lum += return_stroke * (4.0 * on + 1.2 * dd)
        th = max(1, int(round(width * scale * (1 + 2.2 * dd + 1.5 * on * (0.4 + return_stroke)))))
        a = (int(P[j, 0] * 16), int(P[j, 1] * 16))
        b = (int(P[i, 0] * 16), int(P[i, 1] * 16))
        cv2.line(img, a, b, float(lum), th, cv2.LINE_AA, shift=4)
    return img


def scar(tree: Tree, w: int, h: int, *, scale=1.0, offset=(0.0, 0.0), width=1.6, prune=2.0):
    """A permanent mark of where current flowed: 0..1 map, thicker where more
    current passed (descendant count). A Lichtenberg figure, as residue."""
    img = np.zeros((h, w), np.float32)
    P = tree.pos * scale + np.asarray(offset, np.float32)
    dmax = tree.desc.max()
    for i in range(1, len(P)):
        if tree.desc[i] < prune:
            continue
        j = tree.parent[i]
        dd = (tree.desc[i] / dmax) ** 0.45
        th = max(1, int(round(width * (0.6 + 2.4 * dd + 1.2 * tree.main[i]))))
        cv2.line(img, (int(P[j, 0] * 16), int(P[j, 1] * 16)), (int(P[i, 0] * 16), int(P[i, 1] * 16)),
                 float(0.35 + 0.65 * max(dd, tree.main[i])), th, cv2.LINE_AA, shift=4)
    return np.clip(img, 0, 1)
