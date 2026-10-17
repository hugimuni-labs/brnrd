"""The lexicon plate: every glyph × every physics, native pairings bracketed.

    python atlas.py  →  out/atlas.png

Rows are glyphs, grouped by shape family; columns are generators. A cell is
what that physics makes of that skeleton. The bracketed cell is the glyph's
*etymology* — the pairing where the physics produces the shape from its own
law (poles for n/u, velocity for V/^, parity for b/d, branching for r).
Blank cells are pairings with no honest mechanism (field lines have no fork;
a wake has no bowl).
"""
from __future__ import annotations

import os
import sys
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))

import shots as S  # noqa: E402
from glyphs import FAMILIES, LEXICON, Placement  # noqa: E402
from optics import AMBER, BONE, MONO, H, W, font  # noqa: E402
from phys.field import POLES_EM  # noqa: E402

PHYS = ["chladni", "breakdown", "field", "rd", "holo", "swarm", "mach", "press"]
CELL = 168
PAD = 8
LEFT = 110
RIGHT = 560
TOP = 150


def honest(phys, g):
    if phys == "field":
        return g in POLES_EM
    if phys == "mach":
        return g in ("V", "^", "Y")
    return True


def _cell(args):
    g, phys = args
    if not honest(phys, g):
        return g, phys, None
    pl = Placement(960, 540, 820)
    I = S.find(phys, g, 3, pl)
    crop = I[540 - 450:540 + 450, 960 - 450:960 + 450]
    crop = cv2.resize(crop, (CELL, CELL), interpolation=cv2.INTER_AREA)
    v = 1 - np.exp(-np.clip(crop, 0, None) * 1.4)
    col = BONE if phys == "field" else AMBER
    rgb = (np.clip(v, 0, 1)[..., None] * col) ** (1 / 2.2)
    return g, phys, (rgb * 255).astype(np.uint8)


def main():
    out = Path(os.environ.get("GP_OUT", Path(__file__).resolve().parent.parent / "out"))
    out.mkdir(parents=True, exist_ok=True)
    rows = [g for fam in ("fork", "bowl", "channel") for g in FAMILIES[fam]]
    jobs = [(g, p) for g in rows for p in PHYS]
    cv2.setNumThreads(1)
    with Pool(4) as pool:
        cells = {(g, p): im for g, p, im in pool.imap_unordered(_cell, jobs)}
    Wd = LEFT + len(PHYS) * (CELL + PAD) + RIGHT
    Ht = TOP + len(rows) * (CELL + PAD) + 3 * 40 + 60
    im = Image.new("RGB", (Wd, Ht), (8, 7, 6))
    d = ImageDraw.Draw(im)
    fg = (236, 224, 200)
    dim = (150, 140, 125)
    amber = (255, 170, 70)
    d.text((LEFT, 40), "GLYPH PHYSICS — LEXICON", font=font(34), fill=fg)
    d.text((LEFT, 86), "every generator hunts every glyph; the bracket marks the glyph's etymology", font=font(18), fill=dim)
    for j, p in enumerate(PHYS):
        d.text((LEFT + j * (CELL + PAD) + CELL // 2, TOP - 28), p, font=font(16), fill=dim, anchor="ma")
    y = TOP
    rune = "/usr/share/fonts/opentype/unifont/unifont.otf"  # covers runes and ⤙
    for fam in ("fork", "bowl", "channel"):
        d.text((20, y + 8), fam.upper(), font=font(16), fill=amber)
        y += 40
        for g in FAMILIES[fam]:
            d.text((LEFT // 2, y + CELL // 2), g, font=font(64, rune), fill=fg, anchor="mm")
            for j, p in enumerate(PHYS):
                x = LEFT + j * (CELL + PAD)
                c = cells[(g, p)]
                if c is None:
                    d.text((x + CELL // 2, y + CELL // 2), "·", font=font(20), fill=(60, 55, 50), anchor="mm")
                    continue
                im.paste(Image.fromarray(c), (x, y))
                if LEXICON[g].physics == p:
                    L = 22
                    for (cx, cy, sx, sy) in ((x - 3, y - 3, 1, 1), (x + CELL + 2, y - 3, -1, 1),
                                             (x - 3, y + CELL + 2, 1, -1), (x + CELL + 2, y + CELL + 2, -1, -1)):
                        d.line([(cx + sx * L, cy), (cx, cy), (cx, cy + sy * L)], fill=amber, width=3)
            et = LEXICON[g]
            tx = LEFT + len(PHYS) * (CELL + PAD) + 16
            d.text((tx, y + 30), f"{g} ← {et.physics}", font=font(20, rune), fill=fg)
            words = et.reading.split()
            line, ly = "", y + 62
            for w_ in words:
                if len(line) + len(w_) > 40:
                    d.text((tx, ly), line, font=font(15), fill=dim)
                    line, ly = "", ly + 20
                line += ("" if not line else " ") + w_
            d.text((tx, ly), line, font=font(15), fill=dim)
            if et.twin:
                d.text((tx, ly + 26), f"twin: {et.twin}", font=font(15, rune), fill=amber)
            y += CELL + PAD
    im.save(out / "atlas.png", optimize=True)
    small = im.resize((im.width // 2, im.height // 2), Image.LANCZOS)
    small.save(out / "atlas-small.jpg", quality=88)
    print(out / "atlas.png", im.size)


if __name__ == "__main__":
    main()
