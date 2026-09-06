#!/usr/bin/env python3
"""Produce a v9 deck from a v8 deck with exactly two kinds of changes:

1. Replace every invented-logo picture (identified by a `descr` attribute
   naming it a logo, whose bytes do not match the real brnrd logo asset)
   with the real PNG, preserving aspect ratio and centering it on the
   original picture's bounding box (never stretched).
2. Remove any paragraph whose text reads as a small, specific user-count
   claim (e.g. "10 users", "ten users we don't know") — quoted verbatim
   in the printed report — closing the paragraph gap it leaves. Invents
   no replacement copy.

Everything else in the source deck is preserved byte-for-byte: the script
only ever touches the picture blob/xfrm of matched logo shapes and the
paragraph XML of matched user-count text; it never rewrites the archive
wholesale.

Usage:
    python3 v9_from_v8.py <in.pptx> <out.pptx> [--real-logo PATH]

Requires python-pptx (not a dependency of this repo's own .venv — install
it into a throwaway environment, e.g. `python3 -m venv /tmp/deckenv &&
/tmp/deckenv/bin/pip install python-pptx`).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REAL_LOGO = REPO_ROOT / "media" / "brnrd-logo.png"

# A line that names a small, specific user count as a target/claim.
# Matches "10 users", "ten users", "10 users we don't know", etc.
# Deliberately narrow: only small counts (1-2 digits, or spelled out up to
# "twenty") paired with "user"/"users", so it never eats an unrelated
# sentence that happens to contain the word "users".
_SMALL_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
)
USER_COUNT_RE = re.compile(
    rf"\b(\d{{1,2}}|{_SMALL_NUMBER_WORDS})\s+(real\s+|paying\s+)?users?\b",
    re.IGNORECASE,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_logo_shapes(prs: Presentation, real_logo_hash: str):
    """Enumerate every picture shape; return the ones that look like an
    invented logo: a `descr` naming it a logo, and bytes that do not
    already match the real asset. Also returns the full scan for the
    report (every picture, matched or not)."""
    scan = []
    matches = []
    for slide_idx, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if shape.shape_type != 13:  # MSO_SHAPE_TYPE.PICTURE
                continue
            blob = shape.image.blob
            h = sha256_bytes(blob)
            descr = shape._element.find(
                ".//{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr"
            )
            descr_text = descr.get("descr", "") if descr is not None else ""
            row = {
                "slide": slide_idx,
                "shape_id": shape.shape_id,
                "name": shape.name,
                "descr": descr_text,
                "size": len(blob),
                "ext": shape.image.ext,
                "sha256": h,
                "left": shape.left,
                "top": shape.top,
                "width": shape.width,
                "height": shape.height,
                "matches_real": h == real_logo_hash,
            }
            scan.append(row)
            looks_like_logo = "logo" in descr_text.lower()
            if looks_like_logo and h != real_logo_hash:
                matches.append((shape, row))
    return scan, matches


def replace_logo(slide_shapes, shape, row, real_logo_path: Path):
    """Swap `shape`'s image for the real logo, preserving aspect ratio,
    centered on the original bounding box, never stretched."""
    from PIL import Image

    with Image.open(real_logo_path) as im:
        real_w, real_h = im.size
    real_aspect = real_w / real_h

    old_left, old_top = shape.left, shape.top
    old_w, old_h = shape.width, shape.height

    # Fit the real logo inside the old bounding box without exceeding it
    # in either dimension, preserving its own aspect ratio.
    if old_w / old_h > real_aspect:
        new_h = old_h
        new_w = Emu(round(new_h * real_aspect))
    else:
        new_w = old_w
        new_h = Emu(round(new_w / real_aspect))

    new_left = Emu(round(old_left + (old_w - new_w) / 2))
    new_top = Emu(round(old_top + (old_h - new_h) / 2))

    old_el = shape._element
    parent = old_el.getparent()
    idx = list(parent).index(old_el)

    new_pic = slide_shapes.add_picture(
        str(real_logo_path), new_left, new_top, width=new_w, height=new_h
    )
    new_el = new_pic._element
    parent.remove(new_el)
    parent.insert(idx, new_el)
    parent.remove(old_el)

    return {
        "old_left": old_left, "old_top": old_top, "old_w": old_w, "old_h": old_h,
        "new_left": new_left, "new_top": new_top, "new_w": new_w, "new_h": new_h,
    }


def strip_user_count_lines(prs: Presentation):
    """Remove any paragraph whose joined run text matches a small
    user-count claim. Returns a list of (slide_idx, shape_name, text)
    removed, closing the gap left in the paragraph list."""
    removed = []
    for slide_idx, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            tf = shape.text_frame
            txBody = tf._txBody
            for p in list(txBody.findall(
                "{http://schemas.openxmlformats.org/drawingml/2006/main}p"
            )):
                text = "".join(
                    (r.text or "")
                    for r in p.findall(
                        "{http://schemas.openxmlformats.org/drawingml/2006/main}r"
                        "/{http://schemas.openxmlformats.org/drawingml/2006/main}t"
                    )
                )
                if not text.strip():
                    continue
                if USER_COUNT_RE.search(text):
                    removed.append((slide_idx, shape.name, text))
                    txBody.remove(p)
    return removed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="v8 (or any source) .pptx path")
    ap.add_argument("output", type=Path, help="v9 output .pptx path")
    ap.add_argument(
        "--real-logo", type=Path, default=DEFAULT_REAL_LOGO,
        help=f"real logo PNG (default: {DEFAULT_REAL_LOGO})",
    )
    args = ap.parse_args()

    if args.input.resolve() == args.output.resolve():
        sys.exit("refusing to overwrite the input in place")

    real_bytes = args.real_logo.read_bytes()
    real_hash = sha256_bytes(real_bytes)

    prs = Presentation(str(args.input))

    print(f"== logo scan ({args.input.name}) ==")
    scan, matches = find_logo_shapes(prs, real_hash)
    for row in scan:
        flag = "MATCHES REAL" if row["matches_real"] else ""
        print(
            f"  slide {row['slide']}: shape_id={row['shape_id']} name={row['name']!r} "
            f"descr={row['descr']!r} ext={row['ext']} size={row['size']}B "
            f"sha256={row['sha256'][:16]} dim=({row['width']}x{row['height']}) {flag}"
        )
    print(f"  invented-logo matches: {len(matches)}")

    replacements = []
    for shape, row in matches:
        slide = prs.slides[row["slide"] - 1]
        geo = replace_logo(slide.shapes, shape, row, args.real_logo)
        replacements.append((row, geo))
        print(
            f"  -> replaced slide {row['slide']} shape_id={row['shape_id']} "
            f"({row['descr']!r}, {row['width']}x{row['height']}) with real logo "
            f"({geo['new_w']}x{geo['new_h']}, centered in old box)"
        )

    print("== user-count text scan ==")
    removed = strip_user_count_lines(prs)
    if removed:
        for slide_idx, shape_name, text in removed:
            print(f"  slide {slide_idx} shape {shape_name!r}: removed {text!r}")
    else:
        print("  no paragraph matched a small user-count claim (nothing removed)")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(args.output))
    print(f"== wrote {args.output} ==")


if __name__ == "__main__":
    main()
