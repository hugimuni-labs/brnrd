"""The edit: an edit decision list over the shots, then render + assemble.

    python film.py world      # resolve the causal spine (selection, capture) and print it
    python film.py render     # render every (shot, frame) the edit needs, 4 workers
    python film.py cut        # assemble frames → out/mythos-master.mp4 (+ score)
    python film.py contact 4  # contact sheet of every 4th frame
    python film.py len        # duration

The EDL is a list of output frames. Each names a source (shot, local frame)
and optional optics applied at assembly: a magnification jump (``zoom``), or
an ``align`` that moves an insert so the operator the observer finds in it
lands where the outgoing shot had the same operator.

Cut grammar (see ../CAUSALITY.md): every major cut preserves exactly one
property — position, curve, line, branch, negative space, motion or rhythm.
Inserts of 1–3 frames show a different phenomenon carrying the same operator.
Stateful shots keep running underneath inserts.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import observe as OB  # noqa: E402
import world as Wd  # noqa: E402
from optics import FPS, H, W  # noqa: E402
from shots import P_ARCH, P_IGNITE  # noqa: E402
from shots import cosmos as SC  # noqa: E402
from shots import inherit as SI  # noqa: E402
from shots import plate as SP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = Wd.OUT
FRAMES = OUT / "frames"

# name: (fn, kwargs, split) — split > 1 renders the shot in that many
# interleaved chunks, each worker re-running the (cheap) physics from zero.
SHOTS = {
    "web": (SC.web, {}, 2),
    "disk": (SC.disk, {}, 2),
    "belt": (SC.belt, {}, 2),
    "corona": (SC.corona, {}, 1),
    "corona_uv": (SC.corona, {"channel": "uv"}, 1),
    "sun": (SC.sun, {}, 1),
    "sun_ha": (SC.sun, {"channel": "halpha", "path": "loops"}, 1),
    "sun_uv": (SC.sun, {"channel": "uv", "path": "loops"}, 1),
    "raw": (SC.raw, {}, 1),
    "raw_eit": (SC.raw, {"which": "eit195_raw", "seed": 72}, 1),
    "crystal": (SC.crystal, {}, 1),
    "plate": (SP.plate, {}, 4),
    "capture": (SI.capture, {}, 1),
    "press": (SI.press, {}, 1),
    "mask": (SI.mask, {}, 1),
    "etch": (SI.etch, {}, 1),
    "conduct": (SI.conduct, {}, 1),
    "thread": (SI.thread, {}, 2),
    "wire": (SI.wire, {}, 1),
    "black": (None, {}, 1),
}

# A face is shown only if the physics made one convincingly.
FACE_MIN = 0.75


# ── the edit ───────────────────────────────────────────────────────────────


def build_edl():
    E = []

    def run(shot, a, b, **fx):
        for k in range(a, b):
            E.append({"src": (shot, k), **fx})

    def hold(shot, k, n=1, **fx):
        for _ in range(n):
            E.append({"src": (shot, k), **fx})

    def black(n=1):
        hold("black", 0, n)

    def guess(shot, k, gs, anchor, carrier="stone", **kw):
        """Uncertain recognition: candidates flicker as ghosts, one frame each,
        over a world that keeps moving."""
        for j, g in enumerate(gs):
            E.append({"src": (shot, k + j), "rec": {"mode": "ghost", "g": g, "anchor": anchor, "j": j, **kw}})

    def see(shot, k, g, anchor, carrier, n=3, **kw):
        """Confident recognition: a hard cut to the letter as an object."""
        for j in range(n):
            E.append({"src": (shot, k + j), "rec": {"mode": "replace", "g": g, "anchor": anchor,
                                                     "carrier": carrier, "j": j, **kw}})

    # I. Energy without names. Dust, a ray; filaments condense; a node ignites.
    black(6)
    run("web", 0, 64)
    run("web", 64, SC.WEB_LEN)
    # Revelation by light: the glare drains and the dust was a disk.
    run("disk", 0, 30)
    hold("disk", 29, 2, zoom=(1.6, P_IGNITE[0] - 200, P_IGNITE[1] + 120))
    hold("disk", 29, 1, zoom=(2.7, P_IGNITE[0] - 280, P_IGNITE[1] + 160))
    run("disk", 33, 50)
    run("disk", 52, 64)
    # II. Debris and belts: inside the ring plane it is dust again — then a world.
    run("belt", 0, 30)
    guess("belt", 30, ["o", "c", "O"], ("planet",), alpha=0.3)
    run("belt", 33, SC.BELT_LEN)
    # III. Plasma: an arch that the eye cannot stop reading.
    run("corona", 0, 14)
    guess("corona", 14, ["n", "ᚢ", "∩"], ("arch",), alpha=0.3)
    run("corona", 17, 30)
    hold("sun_ha", 3, 2)                       # the same arches, observed, at 656 nm
    run("corona", 32, 40)
    hold("plate", 33, 2, align=("fork", None))  # reconnection's fork, as lightning
    run("corona", 42, 46)
    see("corona", 46, "n", ("arch",), "stone", n=3)  # recognised, and cut in stone
    see("corona", 49, "n", ("arch",), "stone", n=2, flipv=True)  # seen from below: u
    run("corona", 51, 58)
    hold("corona_uv", 58, 2)                   # another channel: only what reconnected
    hold("sun_uv", 4, 2)
    hold("raw", 0, 1)
    hold("raw", 1, 1)
    run("corona", 64, 76)
    # Revelation: the arches were standing on a star.
    run("sun", 0, SC.SUN_LEN - 4)
    # Scale collapse: back down through the limb into a seam in rock.
    hold("sun", SC.SUN_LEN - 4, 2, zoom=(2.4, 760, 420))
    hold("raw_eit", 0, 1)
    hold("sun", SC.SUN_LEN - 3, 1, zoom=(5.0, 700, 380))
    # IV. Matter: a crystal finds the same branch law.
    run("crystal", 0, 22)
    hold("plate", 26, 1)                       # premonition: the leader's tip
    run("crystal", 23, SC.CRYSTAL_LEN)
    # V. The strike: the leader hesitates; the eye guesses; the stroke commits.
    run("plate", 2, 22)
    guess("plate", 22, ["Y", "ᚠ", "r"], ("fork",), alpha=0.35)
    run("plate", 25, Wd.STRIKE_RS + 2)
    see("plate", Wd.STRIKE_RS + 2, "r", ("fork",), "ink", n=3)
    run("plate", Wd.STRIKE_RS + 5, 54)
    run("plate", 54, 92)                       # the stare
    run("plate", 92, 128)                      # shaken into near-forms
    hold("belt", 22, 2, align=("cavity", "plate"))
    run("plate", 130, 150)
    # The parity family cycles as the drive retunes: b d q p, none holds.
    guess("plate", 150, ["b", "d", "q", "p"], ("found",), alpha=0.32)
    run("plate", 158, 172)
    hold("corona", 30, 1, align=("arch", "plate"))
    run("plate", 173, 196)
    face = [f for f in Wd.faces() if f["score"] >= FACE_MIN]
    if face:
        hold("plate", face[0]["k"], 5, zoom=(1.25,) + _found_screen(face[0]["k"], face[0]))
    # VI. Observation: polariser, squaring, phosphor raster, freeze, slit.
    run("plate", 196, Wd.PLATE_LEN)
    # VII. Capture: the photogram; a hand circles the form and names it.
    run("capture", 0, SI.CAPTURE_LEN)
    run("capture", SI.CAPTURE_LEN - 8, SI.CAPTURE_LEN, rec={"mode": "grease", "g": "d", "anchor": ("note",)})
    # The sort that will press it reads mirror-reversed: d is cast as b.
    see("press", 0, "d", ("centre",), "type", n=2)
    run("press", 2, SI.PRESS_LEN)
    # VIII. Engineering: mask, etch, board.
    run("mask", 0, SI.MASK_LEN)
    run("etch", 0, SI.ETCH_LEN)
    run("conduct", 0, 30)
    hold("thread", 20, 1)                      # parallel conductors are threads
    run("conduct", 31, SI.CONDUCT_LEN)
    # IX. Communication.
    run("thread", 0, SI.THREAD_LEN)
    run("wire", 0, SI.WIRE_LEN)
    black(12)
    return E


# ── rendering ──────────────────────────────────────────────────────────────


def needs(E):
    n = defaultdict(set)
    for e in E:
        n[e["src"][0]].add(e["src"][1])
    return n


def _job(args):
    name, need, todo = args
    fn, kw, _ = SHOTS[name]
    d = FRAMES / name
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for k, img in fn(need, **kw):
        if k in todo:
            cv2.imwrite(str(d / f"{k:05d}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 1])
            n += 1
    return name, n


def render(only=None, workers=4):
    Wd.lightning()
    Wd.found()
    E = build_edl()
    N = needs(E)
    jobs = []
    for name, need in N.items():
        if name == "black" or (only and name not in only):
            continue
        d = FRAMES / name
        todo = sorted(k for k in need if not (d / f"{k:05d}.png").exists())
        if not todo:
            continue
        split = SHOTS[name][2]
        for i in range(split):
            part = set(todo[i::split])
            if part:
                # Stateful shots must step up to their largest needed frame,
                # but only develop the frames in this part.
                jobs.append((name, part, part))
    jobs.sort(key=lambda j: -max(j[1]) * (3 if j[0] in ("plate", "corona", "corona_uv") else 1))
    cv2.setNumThreads(1)
    with Pool(workers) as p:
        for name, n in p.imap_unordered(_job, jobs):
            print(f"  {name:10s} +{n}", flush=True)
    print(f"EDL: {len(E)} frames = {len(E) / FPS:.2f}s")


_BLACK = np.zeros((H, W, 3), np.uint8)


def _load(src):
    name, k = src
    if name == "black":
        return _BLACK
    img = cv2.imread(str(FRAMES / name / f"{k:05d}.png"))
    if img is None:
        raise FileNotFoundError(src)
    return img


def _zoom(img, z, cx, cy):
    w, h = W / z, H / z
    x0 = int(np.clip(cx - w / 2, 0, W - w))
    y0 = int(np.clip(cy - h / 2, 0, H - h))
    crop = img[y0:y0 + int(h), x0:x0 + int(w)]
    return cv2.resize(crop, (W, H), interpolation=cv2.INTER_CUBIC)


_ALIGN_CACHE: dict = {}


def _operator_at(img, op, with_size=False):
    """Where the observer finds ``op`` in a frame (screen px), or None."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    small = cv2.resize(g, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    feats = OB.features(small, pct=86, scale=4.0)
    ops = {"cavity": ("cavity", "near", "bowl"), "arch": ("arch", "mouth"), "fork": ("fork",)}[op]
    best = None
    for f in feats:
        if f.op in ops:
            s = f.score * f.size / (1 + math.hypot(f.x - W / 2, f.y - H / 2) / 500)
            if best is None or s > best[0]:
                best = (s, f.x, f.y, f.size)
    if best is None:
        return None
    return (best[1], best[2], best[3]) if with_size else (best[1], best[2])


def _align(img, e, E, i):
    """Shift an insert so the operator it carries lands where the shot around
    it carries the same operator (or on a fixed anchor)."""
    op, ref = e["align"]
    key = (e["src"], op, ref, i)
    if key not in _ALIGN_CACHE:
        src = _operator_at(img, op)
        if ref is None:
            # Align to the operator in the previous frame of the edit.
            j = i - 1
            while j >= 0 and "align" in E[j]:
                j -= 1
            dst = _operator_at(_load(E[j]["src"]), op) if j >= 0 else None
        else:
            j = i - 1
            while j >= 0 and E[j]["src"][0] != ref:
                j -= 1
            dst = _operator_at(_load(E[j]["src"]), op) if j >= 0 else None
        _ALIGN_CACHE[key] = None if (src is None or dst is None) else (dst[0] - src[0], dst[1] - src[1])
    d = _ALIGN_CACHE[key]
    if d is None:
        return img
    dx, dy = np.clip(d, -500, 500)
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REFLECT)


# ── recognition anchors: where a letter stands when the eye finds one ──────


def _plate_screen(k, pts):
    """Plate px (no margin) → screen px through the plate camera at frame k."""
    M = SP._M(SP.camera(k))
    p = np.float32(pts).reshape(-1, 1, 2) + SP.MG
    return cv2.perspectiveTransform(p, M).reshape(-1, 2)


def _found_screen(k, face=None):
    F = Wd.found()
    sc = F["scale"]
    if face is not None:
        pt = (face["x"] / sc * Wd.PX, face["y"] / sc * Wd.PX)
    else:
        pt = (F["form"][1] / sc * Wd.PX, F["form"][2] / sc * Wd.PX)
    x, y = _plate_screen(k, [pt])[0]
    return float(x), float(y)


def _anchor(rec, e, img):
    """(x, y, height, rotation°, mirror) for a recognition event."""
    kind = rec["anchor"][0]
    shot, k = e["src"]
    if kind == "arch":
        import json
        a = json.loads((Wd.STATE / "anchor_corona_arch.json").read_text())
        (x0, _), (x1, _) = a["feet"]
        top, base = a["apex"][1], a["limb"]
        return ((x0 + x1) / 2, (top + base) / 2, (base - top) * 1.02, 0.0, False)
    if kind == "fork":
        L = Wd.lightning()
        tree = L["tree"]
        fx, fy, _ = L["fork"]
        i = int(np.argmin(np.hypot(tree.pos[:, 0] - fx, tree.pos[:, 1] - fy)))
        up_, dn = i, i
        for _ in range(10):
            up_ = max(tree.parent[up_], 0)
        for _ in range(10):
            kids = [c for c in tree.children[dn] if tree.main[c] > 0]
            if not kids:
                break
            dn = kids[0]
        side = [c for c in tree.children[i] if tree.main[c] == 0]
        pts = _plate_screen(k, [tree.pos[up_], tree.pos[i], tree.pos[dn]] +
                            ([tree.pos[side[0]]] if side else []))
        u, c, d = pts[0], pts[1], pts[2]
        v = d - u
        rot = math.degrees(math.atan2(v[0], v[1]))     # stem along the channel
        mirror = False
        if side:
            sv = pts[3] - c
            mirror = (v[0] * sv[1] - v[1] * sv[0]) < 0
        h = float(np.hypot(*v)) * 0.95
        if rec["mode"] == "replace":
            # A made object is upright-ish and big: the hand straightens what
            # the eye saw, but keeps where it was and which way it leaned.
            return (float(c[0]), float(c[1]), max(h, 620.0), float(np.clip(rot, -14, 14)), mirror)
        return (float(c[0]), float(c[1]) + 0.1 * h, max(h, 420.0), rot, mirror)
    if kind == "planet":
        # The darkest cavity in the frame is the world itself, in silhouette.
        from lab.rings import look, project
        cam, tg, _ = SC._belt_cam(k)
        R = look(cam, tg)
        xy, d, _ = project(np.zeros((1, 3)), cam, R, 1300.0, W, H)
        return (float(xy[0, 0]), float(xy[0, 1]), 2.15 * 1300.0 / float(d[0]), 0.0, False)
    if kind == "found":
        x, y = _found_screen(k)
        return (x, y, 300.0, 0.0, False)
    if kind == "centre":
        return (W * 0.5, H * 0.5, 760.0, 0.0, False)
    if kind == "note":
        return (W * 0.5 + 420, H * 0.5 - 270, 210.0, -6.0, False)
    if kind == "observe":
        f = _operator_at(img, rec["anchor"][1], with_size=True)
        if f is None:
            return None
        return (f[0], f[1], max(160.0, f[2] * 1.6), 0.0, False)
    raise KeyError(kind)


def _recognise(img, e, i):
    import recognize as R
    rec = e["rec"]
    A = _anchor(rec, e, img)
    if A is None:
        return img  # the observer found nothing: no letter
    x, y, hgt, rot, mirror = A
    anchor = (x, y, hgt, rot)
    g = rec["g"]
    if rec["mode"] == "ghost":
        rng = np.random.default_rng(i)
        jit = tuple(rng.normal(0, 6, 2))
        return R.ghost(img, g, anchor, alpha=rec.get("alpha", 0.3), mirror=mirror, jitter=jit)
    if rec["mode"] == "replace":
        if rec.get("flipv"):
            g = {"n": "u"}.get(g, g)
        hdr = R.carrier(rec["carrier"], g, anchor, seed=i, mirror=mirror)
        out = R.to8(hdr, frame=i, seed=17, exposure=1.5 if rec["carrier"] == "type" else 1.3)
        return cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
    if rec["mode"] == "grease":
        # The hand writes it in the frames it is on screen.
        j = i - min(jj for jj, ee in enumerate(_EDL_REF) if ee.get("rec", {}).get("mode") == "grease")
        a = R.carrier("grease", g, anchor, seed=3)
        ys = np.arange(H, dtype=np.float32)[:, None]
        a = a * (ys < y - hgt / 2 + hgt * min(1.0, (j + 1) / 5))
        red = np.array([0.06, 0.12, 0.85], np.float32)  # BGR
        f = img.astype(np.float32) / 255
        f = f * (1 - 0.85 * a[..., None]) + a[..., None] * red * 0.9
        return (np.clip(f, 0, 1) * 255).astype(np.uint8)
    raise KeyError(rec["mode"])


_EDL_REF: list = []


def frame(E, i):
    global _EDL_REF
    _EDL_REF = E
    e = E[i]
    img = _load(e["src"])
    if "zoom" in e:
        img = _zoom(img, *e["zoom"])
    if "align" in e:
        img = _align(img, e, E, i)
    if "rec" in e:
        img = _recognise(img, e, i)
    if "dim" in e:
        img = (img.astype(np.float32) * e["dim"]).astype(np.uint8)
    return img


def cut(path=None, audio=True):
    E = build_edl()
    OUT.mkdir(parents=True, exist_ok=True)
    path = Path(path or OUT / "mythos-master.mp4")
    silent = OUT / "silent.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow",
           "-crf", "16", "-pix_fmt", "yuv420p", str(silent)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(len(E)):
        p.stdin.write(frame(E, i).tobytes())
    p.stdin.close()
    p.wait()
    if audio:
        sys.path.insert(0, str(ROOT / "audio"))
        import score
        wav = OUT / "score.wav"
        score.write(E, wav)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(wav),
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)], check=True)
    else:
        silent.replace(path)
    print(path, f"{len(E)} frames, {len(E) / FPS:.2f}s")


def contact(step=4, cols=16, scale=0.1):
    E = build_edl()
    tiles = [cv2.resize(frame(E, i), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
             for i in range(len(E)) if i % step == 0]
    th, tw = tiles[0].shape[:2]
    rows = []
    for r in range(0, len(tiles), cols):
        row = tiles[r:r + cols]
        row += [np.zeros_like(tiles[0])] * (cols - len(row))
        rows.append(np.hstack(row))
    sheet = np.vstack(rows)
    out = OUT / f"contact-{step}.jpg"
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(out)


def notebook():
    """The lab notebook: what selection chose and what the observer found.
    Left: the twelve discharges, ranked (the kept one boxed). Right: the frozen
    plate with the scar, the observer's findings, the found form, its capture
    (blue) and the die (red)."""
    L = Wd.lightning()
    F = Wd.found()
    from lab import breakdown as BD
    tiles = []
    for score_, seed in L["ranking"]:
        t = Wd._grow(seed)
        I = BD.draw(t, Wd.PW, Wd.PH, t.steps, return_stroke=0.3)
        im = cv2.resize(np.clip(cv2.GaussianBlur(I, (0, 0), 2) * 120, 0, 255).astype(np.uint8), (360, 200))
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
        if seed == L["seed"]:
            cv2.rectangle(im, (1, 1), (358, 198), (0, 160, 255), 2)
        tiles.append(im)
    left = np.vstack([np.hstack(tiles[i:i + 3]) for i in range(0, 12, 3)])
    sc = F["scale"]
    I = F["image"]
    g = np.clip(I / (np.percentile(I, 99.5) + 1e-6) * 255, 0, 255).astype(np.uint8)
    scar = cv2.resize(Wd.scar_map(), (Wd.NX * sc, Wd.NY * sc))
    vis = np.dstack([g, g, np.maximum(g, (scar * 160).astype(np.uint8))])
    for f in OB.features(I, pct=89):
        col = {"cavity": (255, 160, 0), "near": (255, 255, 0), "bowl": (0, 255, 255), "arch": (255, 0, 255),
               "mouth": (200, 100, 0), "stem": (0, 200, 0), "fork": (0, 0, 255)}.get(f.op)
        if col:
            cv2.circle(vis, (int(f.x), int(f.y)), max(3, int(f.size / 4)), col, 1)
    op, cx, cy, sz, s = F["form"]
    cv2.circle(vis, (int(cx), int(cy)), int(F["R"]), (0, 160, 255), 2)
    for p in F["polys"]:
        cv2.polylines(vis, [p.astype(np.int32)], False, (255, 128, 0), 1)
    for p in F["die"]:
        cv2.polylines(vis, [p.astype(np.int32)], False, (0, 0, 255), 2)
    right = cv2.resize(vis, (left.shape[0] * vis.shape[1] // vis.shape[0], left.shape[0]))
    out = OUT / "notebook.jpg"
    cv2.imwrite(str(out), np.hstack([left, right]), [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(out)


if __name__ == "__main__":
    import json
    cmd = sys.argv[1] if len(sys.argv) > 1 else "render"
    if cmd == "world":
        print(json.dumps(Wd.summary(), indent=1, default=str))
    elif cmd == "render":
        render(set(sys.argv[2:]) or None)
    elif cmd == "cut":
        cut(audio="--silent" not in sys.argv)
    elif cmd == "contact":
        contact(int(sys.argv[2]) if len(sys.argv) > 2 else 4)
    elif cmd == "notebook":
        notebook()
    elif cmd == "len":
        E = build_edl()
        print(len(E), len(E) / FPS)
