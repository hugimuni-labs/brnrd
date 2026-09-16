"""Mask the phone number on an already-rendered cut.

    python3 mask_render.py CutV5      in.mp4 out.mp4
    python3 mask_render.py CutShortV6 in.mp4 out.mp4

Builds one RGBA overlay frame per render frame (transparent except where
mask_spec says a number is readable), then composites with ffmpeg. The camera
is recomputed from the composition's own source, so the rects land exactly
where the composition would have drawn them.
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw  # noqa: E402

import cut_timing as T  # noqa: E402
import mask_spec as M  # noqa: E402

W, H = 1920, 1080


def overlay_frames(scene_list, outdir):
    """Write %06d.png for every frame; return the count of masked frames."""
    empty = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    empty_path = os.path.join(outdir, "_empty.png")
    empty.save(empty_path)
    total = T.total_frames(scene_list)
    masked = 0
    for f in range(total):
        path = os.path.join(outdir, f"{f:06d}.png")
        r = T.resolve(scene_list, f)
        rects = M.rects_for(r[0], r[1]) if r else []
        if not rects:
            os.link(empty_path, path)
            continue
        masked += 1
        _, _, z, tx, ty = r
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for x0, y0, x1, y1 in rects:
            box = [tx + x0 * z, ty + y0 * z, tx + x1 * z, ty + y1 * z]
            if box[2] < 0 or box[0] > W or box[3] < 0 or box[1] > H:
                continue
            d.rounded_rectangle(box, radius=max(4, 12 * z), fill=M.FILL,
                                outline=M.EDGE, width=max(1, int(round(2 * z))))
        im.save(path)
    os.unlink(empty_path)
    return total, masked


def main():
    comp, src, dst = sys.argv[1], sys.argv[2], sys.argv[3]
    scene_list = {"CutV5": T.SCENES_V5, "CutShortV6": T.SCENES_SHORT_V6}[comp]
    tmp = tempfile.mkdtemp(prefix="mask-ov-")
    try:
        total, masked = overlay_frames(scene_list, tmp)
        print(f"{comp}: {total} frames, {masked} carry a mask", flush=True)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", src,
            "-framerate", "60", "-start_number", "0", "-i", os.path.join(tmp, "%06d.png"),
            "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[v]",
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-crf", os.environ.get("MASK_CRF", "21"), "-preset", "slow", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", dst,
        ]
        subprocess.run(cmd, check=True)
        print("wrote", dst, os.path.getsize(dst), "bytes")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
