"""Mask the number in the committed freeze-stills themselves.

`public/stills/s_notify.png`, `s_folded.png` and `s_sent.png` are 720x1560
frames of the phone, committed to the repo — so they carry the number into any
checkout, independently of what the composition draws over them. Idempotent:
running it twice paints the same rects on the same pixels.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw  # noqa: E402

import mask_spec as M  # noqa: E402

STILLS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "remotion", "public", "stills")


def main():
    for name, rects in M.STILL_RECTS.items():
        path = os.path.normpath(os.path.join(STILLS, name))
        im = Image.open(path).convert("RGB")
        d = ImageDraw.Draw(im)
        for x0, y0, x1, y1 in rects:
            d.rounded_rectangle([x0, y0, x1, y1], radius=12,
                                fill=M.FILL[:3], outline=M.EDGE[:3], width=2)
        im.save(path)
        print("masked", name, im.size)


if __name__ == "__main__":
    main()
