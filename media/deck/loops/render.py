#!/usr/bin/env python3
"""Render the Web Summit deck loops without network or external assets.

Frames are drawn at 1920x1080, then the GIFs are deliberately reduced to
960x540.  MP4s retain the full-resolution frames for PowerPoint.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parent
W, H, FPS, SECONDS = 1920, 1080, 15, 4
FRAMES = FPS * SECONDS
FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
BG = "#070a0d"
PANEL = "#0d1317"
LINE = "#253238"
MUTED = "#72848a"
PHOSPHOR = "#c9eff0"
AMBER = "#ffae24"
PALE_AMBER = "#ffd47b"


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Menlo's bold face is at index 1 in Apple's TTC; ordinary is index 0.
    return ImageFont.truetype(FONT_PATH, size, index=1 if bold else 0)


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    # The restrained horizontal scan is present in the deck stills, but remains
    # static so it cannot become a second animation.
    for y in range(0, H, 12):
        draw.line((0, y, W, y), fill="#090e11")
    return image, draw


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, size: int,
         fill: str = PHOSPHOR, *, bold: bool = False, anchor: str = "la") -> None:
    draw.text(xy, value, font=font(size, bold=bold), fill=fill, anchor=anchor)


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *,
            fill: str = PANEL, outline: str = LINE, radius: int = 24, width: int = 2) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def thin_margin(draw: ImageDraw.ImageDraw) -> None:
    """The loop itself is the slide content: only a quiet enclosing margin."""
    draw.rectangle((42, 42, W - 42, H - 42), outline="#111a1e", width=2)


def loop_lose(i: int) -> Image.Image:
    image, draw = canvas()
    thin_margin(draw)
    x0, y0, x1, y1 = 230, 188, 1690, 892
    rounded(draw, (x0, y0, x1, y1), radius=30)
    rounded(draw, (x0, y0, x1, y0 + 74), fill="#10191d", radius=30)
    draw.rectangle((x0, y0 + 46, x1, y0 + 74), fill="#10191d")
    text(draw, (x0 + 44, y0 + 37), "telegram · brnrd / project", 23, PALE_AMBER, bold=True, anchor="lm")
    text(draw, (x1 - 40, y0 + 37), "LIVE", 18, AMBER, bold=True, anchor="rm")
    entries = [
        ("you", "could you leave it running while I sleep?", 308),
        ("brnrd", "I'll keep the thread and post the receipts.", 414),
        ("you", "try to make good use of the quota this night", 520),
        ("brnrd", "holding the context open.", 626),
    ]
    for sender, body, y in entries:
        col = PALE_AMBER if sender == "you" else PHOSPHOR
        text(draw, (x0 + 54, y), sender, 19, col, bold=True)
        text(draw, (x0 + 190, y), body, 19, "#b1c0c2")
        draw.line((x0 + 54, y + 37, x1 - 54, y + 37), fill="#142025", width=1)
    draw.line((x0 + 42, 730, x1 - 42, 730), fill=LINE, width=2)
    count = 1208 + round(7 * i / (FRAMES - 1))
    text(draw, (x0 + 54, 774), f"{count:,} unread · still loading", 18, MUTED)
    cx, cy = x1 - 90, 777
    angle = i * math.tau * 3 / FRAMES
    degrees = math.degrees(angle)
    draw.arc((cx - 26, cy - 26, cx + 26, cy + 26), degrees + 24, degrees + 296,
             fill=AMBER, width=6)
    ex, ey = cx + 23 * math.cos(angle + math.radians(296)), cy + 23 * math.sin(angle + math.radians(296))
    draw.ellipse((ex - 5, ey - 5, ex + 5, ey + 5), fill=PALE_AMBER)
    return image


def loop_wall(i: int) -> Image.Image:
    image, draw = canvas()
    thin_margin(draw)
    rounded(draw, (250, 292, 1670, 788), radius=30)
    text(draw, (320, 373), "run-260913-0009-1h9d", 23, PALE_AMBER, bold=True)
    text(draw, (320, 418), "the thread carries on", 19, MUTED)
    progress = max(0.0, 0.12 * (1 - min(i, 30) / 30))
    pct = int(round(progress * 100))
    text(draw, (320, 543), "codex · week", 27, PHOSPHOR, bold=True)
    text(draw, (1540, 543), f"{pct}%", 40, AMBER if pct else PALE_AMBER, bold=True, anchor="ra")
    rounded(draw, (320, 588, 1540, 654), fill="#11191d", outline=LINE, radius=18)
    if progress:
        draw.rounded_rectangle((324, 592, 324 + int(1212 * progress), 650), radius=14, fill=AMBER)
    flipped = i >= 30
    if flipped and i < 42:
        bloom = (42 - i) / 12
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((1100 - 200, 703 - 90, 1100 + 200, 703 + 90), fill=(255, 174, 36, int(110 * bloom)))
        image = Image.alpha_composite(image.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(38))).convert("RGB")
        draw = ImageDraw.Draw(image)
    text(draw, (320, 718), "core", 19, MUTED)
    text(draw, (430, 718), "claude" if flipped else "codex", 35, PALE_AMBER if flipped else PHOSPHOR, bold=True)
    if flipped:
        text(draw, (1340, 718), "boundary ✓ resumed", 20, AMBER, anchor="ra")
    else:
        text(draw, (1340, 718), "boundary · waiting", 20, MUTED, anchor="ra")
    return image


def loop_away(i: int) -> Image.Image:
    image, draw = canvas()
    thin_margin(draw)
    rounded(draw, (180, 210, 1740, 870), fill="#0b1011", outline="#263130", radius=22)
    text(draw, (260, 290), "02:00 → 07:00", 51, AMBER, bold=True)
    text(draw, (1548, 290), "WHILE YOU SLEPT", 19, MUTED, bold=True, anchor="ra")
    entries = [
        ("02:4x", "#1959 (`a1691bb0`) closes #1954."),
        ("07:5x", "#1960 (`270a9415`) — both drivers converted with `a1691bb0` as the pattern:"),
        ("12:1x", "He merged #1961 himself in the afternoon."),
    ]
    for number, (stamp, quote) in enumerate(entries, start=1):
        y = 342 + (number - 1) * 156
        onset = number * FPS - 6
        lit = i >= onset
        text(draw, (260, y), stamp, 20, MUTED if not lit else PALE_AMBER, bold=lit)
        text(draw, (388, y), "✣", 30, AMBER if lit else "#34403b", bold=True)
        if lit:
            # The quote types in as one event; only a new merge arrives each second.
            shown = quote[:min(len(quote), max(1, (i - onset + 1) * 7))]
            text(draw, (450, y), shown, 21, PHOSPHOR, bold=True)
            if i < onset + 5:
                for dx, dy in ((0, -34), (0, 34), (-34, 0), (34, 0)):
                    draw.line((402 + dx // 2, y + dy // 2, 402 + dx, y + dy), fill=PALE_AMBER, width=2)
        else:
            draw.line((450, y + 15, 1510, y + 15), fill="#18211f", width=2)
    text(draw, (260, 794), "three merges landed; the morning is already different.", 19, PALE_AMBER)
    return image


LOOPS = {
    "loop-1-lose-the-thread": loop_lose,
    "loop-2-the-wall": loop_wall,
    "loop-4-away": loop_away,
}


def contact_sheet(name: str, frames: list[Image.Image]) -> None:
    thumbs = [frame.resize((240, 135), Image.Resampling.LANCZOS) for frame in frames[::8][:8]]
    sheet = Image.new("RGB", (1920, 205), BG)
    d = ImageDraw.Draw(sheet)
    text(d, (24, 26), name, 20, PALE_AMBER, bold=True)
    for index, thumb in enumerate(thumbs):
        sheet.paste(thumb, (index * 240, 58))
        d.rectangle((index * 240, 58, index * 240 + 239, 192), outline="#345158", width=1)
    sheet.save(ROOT / f"{name}-contact-sheet.png")


def save_mp4(name: str, frames: list[Image.Image]) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    with tempfile.TemporaryDirectory(prefix=f"{name}-", dir="/tmp") as temp:
        directory = Path(temp)
        for index, frame in enumerate(frames):
            frame.save(directory / f"frame-{index:03d}.png")
        subprocess.run([
            ffmpeg, "-y", "-framerate", str(FPS), "-i", str(directory / "frame-%03d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(ROOT / f"{name}.mp4"),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    mp4 = bool(shutil.which("ffmpeg"))
    for name, render in LOOPS.items():
        frames = [render(i) for i in range(FRAMES)]
        gif_frames = [frame.resize((960, 540), Image.Resampling.LANCZOS) for frame in frames]
        # GIF delays are stored in centiseconds.  The 60/70 ms cadence totals
        # exactly four seconds while retaining the intended 15 fps cadence.
        delays = [60 if index % 3 == 0 else 70 for index in range(FRAMES)]
        gif_frames[0].save(ROOT / f"{name}.gif", save_all=True, append_images=gif_frames[1:],
                           duration=delays, loop=0, optimize=False, disposal=2)
        contact_sheet(name, frames)
        if mp4:
            save_mp4(name, frames)
        print(f"{name}: {FRAMES} frames at {FPS} fps; mp4={mp4}")


if __name__ == "__main__":
    main()
