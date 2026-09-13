from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    output = Path(sys.argv[1])
    frames = [Path(value) for value in sys.argv[2:]]
    if len(frames) != 8:
        raise SystemExit(f"expected 8 frames, got {len(frames)}")

    tile_size = (320, 180)
    sheet = Image.new("RGB", (tile_size[0] * 4, tile_size[1] * 2), "#0c0906")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    times = ("0.5s", "3.0s", "5.5s", "8.5s", "11.5s", "14.0s", "17.0s", "19.0s")

    for index, (path, timestamp) in enumerate(zip(frames, times, strict=True)):
        with Image.open(path) as frame:
            tile = frame.convert("RGB").resize(tile_size, Image.Resampling.LANCZOS)
        x = (index % 4) * tile_size[0]
        y = (index // 4) * tile_size[1]
        sheet.paste(tile, (x, y))
        draw.rectangle((x + 7, y + 7, x + 52, y + 23), fill="#0c0906")
        draw.text((x + 11, y + 10), timestamp, fill="#f0c85c", font=font)

    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)
    print(output.resolve())


if __name__ == "__main__":
    main()
