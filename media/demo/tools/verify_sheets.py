"""Contact sheets for reading the mask with your eyes.

    python3 verify_sheets.py CutV5 masked.mp4 outdir

A mask is only as good as the frames someone looked at, so this pulls the
frames where the number could be readable, crops them to a band of the
*phone's* coordinate space (so a zoomed frame and a full-view frame crop to
the same thing), labels each with its frame number and the clip on screen,
and tiles them. `band` is (y0, y1) in phone coordinates.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import cut_timing as T  # noqa: E402
import mask_spec as M  # noqa: E402

W, H = 1920, 1080
FONT = None
for cand in ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Monaco.ttf"):
    if os.path.exists(cand):
        FONT = ImageFont.truetype(cand, 18)
        break


def masked_spans(scene_list, pad=60):
    spans, total = [], T.total_frames(scene_list)
    for f in range(total):
        r = T.resolve(scene_list, f)
        if r and M.rects_for(r[0], r[1]):
            if spans and spans[-1][1] >= f - 1:
                spans[-1][1] = f
            else:
                spans.append([f, f])
    return [[max(0, a - pad), min(total - 1, b + pad)] for a, b in spans]


def extract(src, frames, outdir, chunk=25):
    """ffmpeg's expression parser gives up somewhere past ~60 terms (exit 244),
    so the select goes out in chunks rather than as one heroic line."""
    os.makedirs(outdir, exist_ok=True)
    for i in range(0, len(frames), chunk):
        part = frames[i:i + chunk]
        sel = "+".join(f"eq(n\\,{f})" for f in part)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
                        "-vf", f"select={sel}", "-fps_mode", "passthrough", "-frame_pts", "1",
                        os.path.join(outdir, "f_%d.png")], check=True)


def sheet(src, scene_list, frames, band, cell_w, cols, out_prefix, per_sheet):
    tmp = out_prefix + "-frames"
    extract(src, frames, tmp)
    y0, y1 = band
    cells = []
    for f in frames:
        p = os.path.join(tmp, f"f_{f}.png")
        if not os.path.exists(p):
            continue
        im = Image.open(p).convert("RGB")
        clip, t, z, tx, ty = T.resolve(scene_list, f)
        box = (tx, ty + y0 * z, tx + 720 * z, ty + y1 * z)
        cell = im.crop(tuple(int(round(v)) for v in box))
        ratio = (y1 - y0) / 720.0
        cell = cell.resize((cell_w, max(1, int(cell_w * ratio))))
        lab = Image.new("RGB", (cell_w, cell.height + 24), (0, 0, 0))
        lab.paste(cell, (0, 24))
        ImageDraw.Draw(lab).text((4, 3), f"n={f} {clip[:16]} {t:.2f}s",
                                 fill=(255, 179, 71), font=FONT)
        cells.append(lab)
    paths = []
    for i in range(0, len(cells), per_sheet):
        chunk = cells[i:i + per_sheet]
        ch = chunk[0].height
        rows = (len(chunk) + cols - 1) // cols
        sh = Image.new("RGB", (cols * cell_w, rows * ch), (20, 20, 20))
        for k, c in enumerate(chunk):
            sh.paste(c, ((k % cols) * cell_w, (k // cols) * ch))
        path = f"{out_prefix}-{i // per_sheet + 1}.png"
        sh.save(path)
        paths.append(path)
    return paths


if __name__ == "__main__":
    comp, src, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    sl = {"CutV5": T.SCENES_V5, "CutShortV6": T.SCENES_SHORT_V6}[comp]
    os.makedirs(outdir, exist_ok=True)
    print(comp, "masked spans:", masked_spans(sl))
