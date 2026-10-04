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
    "corona": (SC.corona, {}, 1),
    "corona_uv": (SC.corona, {"channel": "uv"}, 1),
    "crystal": (SC.crystal, {}, 1),
    "plate": (SP.plate, {}, 4),
    "capture": (SI.capture, {}, 1),
    "press": (SI.press, {}, 1),
    "conduct": (SI.conduct, {}, 1),
    "thread": (SI.thread, {}, 2),
    "wire": (SI.wire, {}, 1),
    "black": (None, {}, 1),
}


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

    # I. Energy without names. Near-total dark; a ray finds dust; filaments
    # condense out of a random potential. Two radiation fronts leave a node.
    black(6)
    run("web", 0, 40)
    hold("corona_uv", 46, 1, dim=0.35)        # an arch, for one frame, in the wrong place in time
    run("web", 41, 64)
    # The node ignites; the lens can't hold it.
    run("web", 64, SC.WEB_LEN)
    # Preserve position + light: the ignition IS the disk's hot inner edge,
    # and the disk is what is left when the overexposure drains away.
    run("disk", 0, 30)
    # Magnification jumps into the gap — stepped, like an objective turret.
    hold("disk", 29, 2, zoom=(1.6, P_IGNITE[0] - 200, P_IGNITE[1] + 120))
    hold("disk", 29, 1, zoom=(2.7, P_IGNITE[0] - 280, P_IGNITE[1] + 160))
    run("disk", 33, 50)
    # The far side of the disk, bent over the shadow: an arch over a cavity.
    run("disk", 52, 66)
    hold("corona", 2, 2)                      # same arch, other ontology (P_ARCH shared)
    run("disk", 68, SC.DISK_LEN)
    # II. Preserve curve: lensed arc → coronal arch.
    run("corona", 0, 40)
    hold("plate", 33, 2, align=("fork", None))  # the reconnection's fork, as lightning
    run("corona", 42, 48)
    hold("corona_uv", 48, 2)                   # the hot channel: only the reconnected loops
    run("corona", 50, 76)
    # Scale collapse: a loop leg, magnified until it is a seam in rock.
    hold("corona", 76, 2, zoom=(1.8, P_ARCH[0] - 330, 640))
    hold("corona", 77, 2, zoom=(3.2, P_ARCH[0] - 360, 680))
    hold("corona", 78, 1, zoom=(5.5, P_ARCH[0] - 370, 700))
    # III. Preserve line: the leg is a vein; a dendrite grows up it.
    run("crystal", 0, 22)
    hold("plate", 26, 1)                       # premonition: the leader's tip
    run("crystal", 23, SC.CRYSTAL_LEN)
    # IV. Preserve branch: the dendrite's search becomes a leader's.
    run("plate", 2, 54)                        # strike: hesitation, return stroke, negative
    run("plate", 54, 92)                       # the stare
    run("plate", 92, 128)                      # shaken into near-forms
    hold("disk", 40, 2, align=("cavity", "plate"))  # a gap in a disk ↔ a pocket in grains
    run("plate", 130, 150)
    run("plate", 158, 172)                     # jump cut: the world kept moving
    hold("corona", 30, 1, align=("arch", "plate"))
    run("plate", 173, 196)
    # V. Observation: polariser, squaring, raster, freeze, slit.
    run("plate", 196, Wd.PLATE_LEN)
    # VI. Preserve negative space + position: the found pocket on the film.
    run("capture", 0, SI.CAPTURE_LEN)
    # Preserve shape: the photogram's skeleton is the die.
    run("press", 0, SI.PRESS_LEN)
    run("conduct", 0, 30)
    hold("thread", 20, 1)                      # parallel conductors are threads
    run("conduct", 31, SI.CONDUCT_LEN)
    # Preserve line: a route → a fibre.
    run("thread", 0, SI.THREAD_LEN)
    # Preserve motion: the pulse leaves the weave on a wire.
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


def _operator_at(img, op):
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
                best = (s, f.x, f.y)
    return None if best is None else (best[1], best[2])


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


def frame(E, i):
    e = E[i]
    img = _load(e["src"])
    if "zoom" in e:
        img = _zoom(img, *e["zoom"])
    if "align" in e:
        img = _align(img, e, E, i)
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
