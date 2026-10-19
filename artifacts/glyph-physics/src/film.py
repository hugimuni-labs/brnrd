"""The edit: an edit decision list over physics shots, then render + assemble.

    python film.py render   # render every (shot, frame) the edit needs, 4 workers
    python film.py cut      # assemble frames → out/glyph-physics.mp4 (+ score)
    python film.py stills   # a few full-res stills + a contact sheet

The EDL is a list of output frames. Each one names a source (shot, local
frame) and optional effects: a magnification jump (crop/zoom), or a blend
toward a second source through a ripple warp — the shape-matching cut.
Shots keep running underneath inserts, so a cut away and back lands in a
world that kept moving.
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

import shots as S  # noqa: E402
from glyphs import Placement  # noqa: E402
from optics import FPS, H, W  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("GP_OUT", ROOT / "out"))
FRAMES = OUT / "frames"

C = Placement(960, 560, 820)
OFF = Placement(760, 640, 1150)
SMALL = Placement(1260, 470, 520)
LEFT = Placement(600, 540, 760)

# Barrage cards: (physics, glyph, palette, placement, flags).
CARDS = [
    # 0–2: early fork premonitions
    ("breakdown", "Y", "furnace", C, ()), ("breakdown", "ᚠ", "furnace", OFF, ()),
    ("breakdown", "ᛉ", "furnace", SMALL, ("neg",)),
    # 3–8: one glyph, every physics (b)
    ("chladni", "b", "amber", C, ("label",)), ("rd", "b", "copper", C, ()),
    ("swarm", "b", "amber", OFF, ()), ("holo", "b", "furnace", C, ()),
    ("press", "b", "steel", C, ("label",)), ("breakdown", "b", "bone", C, ()),
    # 9–16: one physics, every fork
    ("breakdown", "l", "furnace", C, ()), ("breakdown", "r", "furnace", LEFT, ("label",)),
    ("breakdown", "Y", "furnace", C, ()), ("breakdown", "ᚠ", "furnace", C, ("label",)),
    ("breakdown", "ᛉ", "furnace", OFF, ()), ("breakdown", "A", "furnace", C, ()),
    ("breakdown", "⤙", "furnace", C, ("label",)), ("breakdown", "ᚱ", "bone", C, ()),
    # 17–21: channels, one field read many ways
    ("field", "n", "bone", C, ("label",)), ("field", "u", "bone", C, ("neg",)),
    ("field", "m", "amber", C, ()), ("field", "ω", "amber", C, ("label",)),
    ("field", "ᚢ", "phosphor", C, ()),
    # 22–24: wakes
    ("mach", "V", "amber", C, ("label",)), ("mach", "^", "amber", C, ()),
    ("mach", "Y", "amber", C, ("label",)),
    # 25–31: bowls
    ("chladni", "d", "amber", C, ()), ("chladni", "p", "amber", C, ()),
    ("chladni", "q", "amber", C, ("label",)), ("rd", "o", "copper", C, ("label",)),
    ("rd", "c", "copper", C, ()), ("rd", "a", "copper", OFF, ()),
    ("holo", "J", "furnace", C, ("label",)),
    # 32–39: the alternation — the word's letters, each by its own law
    ("chladni", "b", "amber", C, ("reg",)), ("breakdown", "r", "furnace", C, ("reg",)),
    ("field", "n", "bone", C, ("reg",)), ("breakdown", "r", "furnace", C, ("reg", "neg")),
    ("chladni", "d", "amber", C, ("reg",)), ("swarm", "r", "amber", C, ()),
    ("swarm", "n", "phosphor", C, ()), ("swarm", "d", "amber", C, ()),
    # 40–43: swarm & holo strays
    ("holo", "ω", "furnace", OFF, ()), ("swarm", "ᚠ", "steel", C, ()),
    ("press", "n", "steel", C, ()), ("rd", "ω", "copper", C, ()),
]

SHOTS = {
    "dust": (S.dust, {}),
    "crack": (S.crack, {}),
    "mach_v": (S.mach, {}),
    "mach_up": (S.mach, {"up_": True, "seed": 31}),
    "mach_y": (S.mach, {"trail": 5.0, "seed": 32}),
    "field_n": (S.field, {}),
    "field_m": (S.field, {"glyph": "m", "twin": "ω", "seed": 41}),
    "chl": (S.chladni, {}),
    "cavity": (S.cavity, {}),
    "math": (S.math_cards, {}),
    "holo": (S.holo, {}),
    "spec": (S.specimen, {}),
    "bar": (S.barrage, {"cards": CARDS}),
    "press": (S.press, {}),
    "current": (S.current, {}),
    "word": (S.word, {}),
    "tele": (S.telegraph, {}),
    "white": (S.solid, {"value": 1.0}),
    "black": (S.solid, {"value": 0.0}),
}

STATELESS = {"bar", "math", "white", "black"}


# ── the edit ───────────────────────────────────────────────────────────────


def build_edl():
    E = []

    def run(shot, a, b, **fx):
        for k in range(a, b):
            E.append({"src": (shot, k), **fx})

    def hold(shot, k, n, **fx):
        for _ in range(n):
            E.append({"src": (shot, k), **fx})

    def black(n=1):
        hold("black", 0, n)

    def white(n=1):
        hold("white", 0, n)

    def match(a, ka, b, kb, n, center=(960, 560)):
        """Shape-matching cut: A bends into B through a ripple over n frames."""
        for i in range(n):
            w = (i + 1) / (n + 1)
            E.append({"src": (a, ka + i), "mix": (b, kb + i), "w": w, "warp": (center, 1 - abs(2 * w - 1))})

    # I. Before the symbol — dust, a god ray, structure glimpsed.
    run("dust", 12, 50)  # the black fade-in is cut: the ray is already there
    run("dust", 50, 64)
    hold("chl", 104, 2)                 # subliminal: grains already hold a b
    run("dust", 66, 90)
    hold("crack", 39, 1)                # premonition of the strike
    run("dust", 91, 104)
    black(2)
    # II. First glowing bend — the crack commits to r.
    run("crack", 0, 39)
    white(1)
    run("crack", 40, 60)
    hold("bar", 0, 3); hold("bar", 1, 2); hold("bar", 2, 2); black(1)
    # The same angle under another ontology.
    run("mach_v", 0, 18)
    hold("crack", 58, 2)                # preserve angle: the r's branch ↔ the cone
    run("mach_v", 20, 40)
    run("mach_up", 0, 26)
    run("mach_y", 0, 12)
    black(1)
    # III. One field, two readings.
    run("field_n", 0, 30)
    hold("chl", 30, 2)
    run("field_n", 32, 70)
    hold("bar", 18, 2)
    run("field_n", 72, 96)
    run("field_m", 0, 50)
    white(1)
    # IV. The membrane — shaken into legibility, parity, flips.
    run("chl", 0, 46)
    hold("math", 0, 2)
    run("chl", 48, 96)
    hold("math", 7, 2)
    run("chl", 98, 172)
    match("chl", 172, "cavity", 0, 6)
    run("cavity", 6, 50)
    # Violent scale collapse: grain → crystal → bifurcation, math between.
    hold("chl", 110, 3, zoom=(2.4, 900, 620))
    hold("math", 2, 2)
    hold("cavity", 49, 3, zoom=(3.2, 960, 560))
    hold("math", 3, 2)
    hold("crack", 59, 3, zoom=(4.5, 760, 700))
    hold("math", 6, 2)
    hold("chl", 111, 2, zoom=(5.5, 940, 600))
    hold("math", 5, 2)
    black(2)
    # V. The stare — focus as observation; registration.
    run("holo", 0, 84)
    match("holo", 84, "spec", 0, 4, center=(860, 590))
    run("spec", 4, 44)
    # Symbol-hunting barrage.
    dur = [3, 2, 2, 3, 2, 2, 3, 2, 2, 2, 3, 2, 2, 3, 2, 2]
    for i, c in enumerate(range(3, 32)):
        hold("bar", c, dur[i % len(dur)])
        if i in (5, 14, 22):
            black(1)
    for rep in range(2):
        for c in range(32, 37):
            hold("bar", c, 2)
    for c in range(37, 44):
        hold("bar", c, 2)
    black(1)
    # The press, then the current.
    run("press", 0, 11)
    black(1)
    run("press", 12, 44)
    run("current", 0, 44)
    # The word was always latent.
    run("word", 0, 40)
    hold("bar", 33, 1)
    run("word", 41, 118)
    white(1)
    # The pulse leaves.
    # Cut mid-transmission: the signal is handed to the next section, not finished.
    run("tele", 0, 64)
    black(14)
    return E


# ── rendering ──────────────────────────────────────────────────────────────


def needs(E):
    n = defaultdict(set)
    for e in E:
        n[e["src"][0]].add(e["src"][1])
        if "mix" in e:
            n[e["mix"][0]].add(e["mix"][1])
    return n


def _job(args):
    name, need = args
    fn, kw = SHOTS[name]
    d = FRAMES / name
    d.mkdir(parents=True, exist_ok=True)
    todo = {k for k in need if not (d / f"{k:05d}.png").exists()}
    if not todo:
        return name, 0
    if name in STATELESS:
        gen = fn(todo, **kw)
    else:
        gen = fn(need if todo else todo, **kw)
    n = 0
    for k, img in gen:
        if k in todo:
            cv2.imwrite(str(d / f"{k:05d}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 1])
            n += 1
    return name, n


def render(only=None):
    E = build_edl()
    N = needs(E)
    jobs = []
    for name, need in N.items():
        if only and name not in only:
            continue
        if name in STATELESS and len(need) > 8:
            ks = sorted(need)
            for i in range(4):
                jobs.append((name, set(ks[i::4])))
        else:
            jobs.append((name, need))
    # Longest first.
    jobs.sort(key=lambda j: -max(j[1]))
    cv2.setNumThreads(1)
    with Pool(4) as p:
        for name, n in p.imap_unordered(_job, jobs):
            print(f"  {name:8s} +{n}", flush=True)
    print(f"EDL: {len(E)} frames = {len(E) / FPS:.2f}s")


def _load(src):
    name, k = src
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


_yy, _xx = np.mgrid[0:H, 0:W].astype(np.float32)


def _ripple(img, center, amt, phase):
    dx, dy = _xx - center[0], _yy - center[1]
    r = np.sqrt(dx * dx + dy * dy) + 1e-3
    disp = amt * 18 * np.sin(r / 22 - phase) * np.exp(-r / 900)
    mx = _xx + dx / r * disp
    my = _yy + dy / r * disp
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def frame(e, i):
    img = _load(e["src"])
    if "zoom" in e:
        img = _zoom(img, *e["zoom"])
    if "mix" in e:
        b = _load(e["mix"])
        center, amt = e["warp"]
        img = _ripple(img, center, amt, i * 0.9)
        b = _ripple(b, center, amt, i * 0.9 + 1.3)
        w = e["w"]
        # Additive light-mix, not a dissolve: the two phenomena co-exist.
        a = img.astype(np.float32) * (1 - w) + b.astype(np.float32) * w
        a += 0.6 * np.minimum(img, b).astype(np.float32) * (1 - abs(2 * w - 1))
        img = np.clip(a, 0, 255).astype(np.uint8)
    return img


def cut(path=None, audio=True):
    E = build_edl()
    OUT.mkdir(parents=True, exist_ok=True)
    path = Path(path or OUT / "glyph-physics-master.mp4")
    silent = OUT / "silent.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow",
           "-crf", "16", "-pix_fmt", "yuv420p", str(silent)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i, e in enumerate(E):
        p.stdin.write(frame(e, i).tobytes())
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
    tiles = [cv2.resize(frame(e, i), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
             for i, e in enumerate(E) if i % step == 0]
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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "render"
    if cmd == "render":
        render(set(sys.argv[2:]) or None)
    elif cmd == "cut":
        cut(audio="--silent" not in sys.argv)
    elif cmd == "contact":
        contact(int(sys.argv[2]) if len(sys.argv) > 2 else 4)
    elif cmd == "len":
        E = build_edl()
        print(len(E), len(E) / FPS)
