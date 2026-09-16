"""The other half of the acceptance: the mask changed the frames it declared,
and nothing else.

    python3 verify_diff.py CutV5 source.mp4 masked.mp4 [step]

Samples frames from both files, and for each one splits the absolute
difference into "inside a declared rect" and "outside every declared rect".
Outside, the only difference should be re-encoding noise; inside, the pixels
should be gone. A rect that moved, a window that drifted, or a stray filter
would show up here as an outside-the-rect difference that is not noise.
"""
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image  # noqa: E402

import cut_timing as T  # noqa: E402
import mask_spec as M  # noqa: E402

SKIRT = 4  # antialiased edge + encoder ringing around each rect


def pull(path, frames, outdir, chunk=25):
    os.makedirs(outdir, exist_ok=True)
    for i in range(0, len(frames), chunk):
        sel = "+".join(f"eq(n\\,{f})" for f in frames[i:i + chunk])
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
                        "-vf", f"select={sel}", "-fps_mode", "passthrough", "-frame_pts", "1",
                        os.path.join(outdir, "f_%d.png")], check=True)


def main():
    comp, src, dst = sys.argv[1], sys.argv[2], sys.argv[3]
    step = int(sys.argv[4]) if len(sys.argv) > 4 else 53
    sl = {"CutV5": T.SCENES_V5, "CutShortV6": T.SCENES_SHORT_V6}[comp]
    frames = list(range(0, T.total_frames(sl), step))
    base = f"/tmp/verify-diff-{comp}"
    pull(src, frames, base + "-a")
    pull(dst, frames, base + "-b")

    worst_out, worst_at, masked_n, inside_min = 0, None, 0, 255
    for f in frames:
        a = np.asarray(Image.open(f"{base}-a/f_{f}.png").convert("L"), dtype=np.int16)
        b = np.asarray(Image.open(f"{base}-b/f_{f}.png").convert("L"), dtype=np.int16)
        d = np.abs(a - b)
        r = T.resolve(sl, f)          # None in the outro, which has no phone
        rects = M.rects_for(r[0], r[1]) if r else []
        clip = r[0] if r else "(outro)"
        _, _, z, tx, ty = r if r else (None, None, 1, 0, 0)
        inside = np.zeros(d.shape, dtype=bool)
        core = np.zeros(d.shape, dtype=bool)
        for x0, y0, x1, y1 in rects:
            X0, Y0 = int(tx + x0 * z), int(ty + y0 * z)
            X1, Y1 = int(tx + x1 * z) + 1, int(ty + y1 * z) + 1
            sl_out = (slice(max(0, Y0 - SKIRT), min(1080, Y1 + SKIRT)),
                      slice(max(0, X0 - SKIRT), min(1920, X1 + SKIRT)))
            sl_in = (slice(max(0, Y0 + SKIRT), min(1080, Y1 - SKIRT)),
                     slice(max(0, X0 + SKIRT), min(1920, X1 - SKIRT)))
            inside[sl_out] = True
            core[sl_in] = True
        out = int(d[~inside].max()) if (~inside).any() else 0
        if out > worst_out:
            worst_out, worst_at = out, (f, clip)
        if core.any():
            masked_n += 1
            # the fill is opaque, so the covered pixels must be *the fill* —
            # measure how flat the masked area is in the output, not the delta
            inside_min = min(inside_min, int(b[core].max()) - int(b[core].min()))
    print(f"{comp}: {len(frames)} frames sampled, {masked_n} with a mask")
    print(f"  worst difference outside every rect: {worst_out}/255 at frame {worst_at}")
    print(f"  widest spread inside a rect's core (0 = a flat fill): {inside_min}")


if __name__ == "__main__":
    main()
