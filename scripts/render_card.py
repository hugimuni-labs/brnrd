#!/usr/bin/env python3
"""Render the seat's `.card` weaver-half (as `brnrd hud --card` reports it)
as a monospace, dark-background PNG, 1200px wide.

Usage: render_card.py <hud-output-file> <out.png>

<hud-output-file> is the captured stdout of:
  brnrd hud --card --outbox <run's outbox dir>
This script slices out everything between the "the weaver's half" marker
and the "the frame's half" marker, wraps it to fit the target width, and
draws it. It does not invoke brnrd itself (kept read-only / re-runnable).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1200
PADDING = 24
FONT_SIZE = 15
LINE_SPACING = 6
BG = "#1a1a19"
FG_PRIMARY = "#ffffff"
FG_SECONDARY = "#c3c2b7"
FG_HEADING = "#3987e5"

MPL_MONO = (
    Path(__file__).resolve().parents[1]
    / ".venv/lib/python3.13/site-packages/matplotlib/mpl-data/fonts/ttf/DejaVuSansMono.ttf"
)
FALLBACK_MONO_CANDIDATES = [
    Path("/Users/gurio/Source/Projects/brnrd/.venv/lib/python3.13/site-packages/matplotlib/mpl-data/fonts/ttf/DejaVuSansMono.ttf"),
]


def find_font() -> ImageFont.FreeTypeFont:
    for candidate in [MPL_MONO, *FALLBACK_MONO_CANDIDATES]:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), FONT_SIZE)
    return ImageFont.load_default()


def extract_weaver_half(hud_text: str) -> str:
    lines = hud_text.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if line.strip().startswith("── the weaver"):
            start = i + 1
        elif line.strip().startswith("── the frame") and start is not None:
            end = i
            break
    if start is None:
        raise SystemExit("no 'the weaver's half' marker found in hud output")
    return "\n".join(lines[start:end] if end is not None else lines[start:])


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <hud-output-file> <out.png>")
    hud_path, out_path = sys.argv[1], sys.argv[2]

    hud_text = Path(hud_path).read_text()
    weaver = extract_weaver_half(hud_text)

    font = find_font()
    draw_probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    char_w = draw_probe.textlength("M", font=font)
    wrap_cols = max(20, int((WIDTH - 2 * PADDING) / char_w))

    wrapped_lines: list[str] = []
    for raw_line in weaver.splitlines():
        if raw_line.strip() == "":
            wrapped_lines.append("")
            continue
        wrapped = textwrap.wrap(
            raw_line,
            width=wrap_cols,
            break_long_words=False,
            break_on_hyphens=False,
            subsequent_indent="  " if raw_line.startswith(("- ", "* ")) else "",
        )
        wrapped_lines.extend(wrapped or [""])

    line_h = FONT_SIZE + LINE_SPACING
    height = 2 * PADDING + line_h * len(wrapped_lines)

    img = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(img)

    y = PADDING
    for line in wrapped_lines:
        color = FG_SECONDARY
        stripped = line.strip()
        if stripped.startswith("#"):
            color = FG_HEADING
        elif stripped.startswith(("- [x]", "**")):
            color = FG_PRIMARY
        draw.text((PADDING, y), line, font=font, fill=color)
        y += line_h

    img.save(out_path)
    print(f"{out_path}: {Path(out_path).stat().st_size} bytes, {WIDTH}x{height}")


if __name__ == "__main__":
    main()
