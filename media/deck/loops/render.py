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


def header(draw: ImageDraw.ImageDraw, chapter: str, title: str) -> None:
    text(draw, (92, 76), "brnrd >_", 32, AMBER, bold=True)
    text(draw, (92, 136), chapter.upper(), 19, MUTED, bold=True)
    text(draw, (92, 184), title, 42, PHOSPHOR, bold=True)
    draw.line((92, 248, W - 92, 248), fill=LINE, width=2)


def glow_dot(image: Image.Image, xy: tuple[int, int], r: int, color: str) -> None:
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    x, y = xy
    gd.ellipse((x - r * 3, y - r * 3, x + r * 3, y + r * 3), fill=color + "48")
    glow = glow.filter(ImageFilter.GaussianBlur(r * 2))
    image.alpha_composite(glow) if image.mode == "RGBA" else None
    ImageDraw.Draw(image).ellipse((x - r, y - r, x + r, y + r), fill=color)


def loop_lose(i: int) -> Image.Image:
    image, draw = canvas()
    header(draw, "02 · lose the thread", "Keep it open and it buries you.")
    x0, y0, x1, y1 = 230, 310, 1690, 930
    rounded(draw, (x0, y0, x1, y1), radius=30)
    rounded(draw, (x0, y0, x1, y0 + 74), fill="#10191d", radius=30)
    draw.rectangle((x0, y0 + 46, x1, y0 + 74), fill="#10191d")
    text(draw, (x0 + 44, y0 + 37), "telegram · brnrd / project", 23, PALE_AMBER, bold=True, anchor="lm")
    text(draw, (x1 - 40, y0 + 37), "LIVE", 18, AMBER, bold=True, anchor="rm")
    entries = [
        ("you", "can you trace the deploy issue?", 408),
        ("brnrd", "reading the run history…", 493),
        ("you", "also check the quota before retrying", 578),
        ("brnrd", "one moment — preserving context", 663),
    ]
    for sender, body, y in entries:
        col = PALE_AMBER if sender == "you" else PHOSPHOR
        text(draw, (x0 + 54, y), sender, 19, col, bold=True)
        text(draw, (x0 + 190, y), body, 19, "#b1c0c2")
        draw.line((x0 + 54, y + 37, x1 - 54, y + 37), fill="#142025", width=1)
    draw.line((x0 + 42, 790, x1 - 42, 790), fill=LINE, width=2)
    # Counter changes textually but does not move; the spinner is the only
    # moving mark, making the stuck state legible at a glance.
    count = 1208 + i // 8
    text(draw, (x0 + 54, 834), f"{count:,} messages · still loading", 18, MUTED)
    cx, cy = x1 - 90, 837
    angle = i * (math.tau / 12)
    draw.arc((cx - 22, cy - 22, cx + 22, cy + 22), 35, 290, fill=AMBER, width=5)
    ex, ey = cx + 18 * math.cos(angle), cy + 18 * math.sin(angle)
    draw.ellipse((ex - 5, ey - 5, ex + 5, ey + 5), fill=PALE_AMBER)
    return image


def loop_wall(i: int) -> Image.Image:
    image, draw = canvas()
    header(draw, "05 · the wall", "The thread stays. The core changes.")
    rounded(draw, (250, 344, 1670, 840), radius=30)
    text(draw, (320, 425), "run-260913-0009-1h9d", 23, PALE_AMBER, bold=True)
    text(draw, (320, 470), "the thread carries on", 19, MUTED)
    progress = max(0.0, 0.12 * (1 - min(i, 30) / 30))
    pct = int(round(progress * 100))
    text(draw, (320, 595), "codex · week", 27, PHOSPHOR, bold=True)
    text(draw, (1540, 595), f"{pct}%", 40, AMBER if pct else PALE_AMBER, bold=True, anchor="ra")
    rounded(draw, (320, 640, 1540, 706), fill="#11191d", outline=LINE, radius=18)
    if progress:
        draw.rounded_rectangle((324, 644, 324 + int(1212 * progress), 702), radius=14, fill=AMBER)
    flipped = i >= 30
    if flipped and i < 42:
        bloom = (42 - i) / 12
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((1100 - 200, 755 - 90, 1100 + 200, 755 + 90), fill=(255, 174, 36, int(110 * bloom)))
        image = Image.alpha_composite(image.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(38))).convert("RGB")
        draw = ImageDraw.Draw(image)
    text(draw, (320, 770), "core", 19, MUTED)
    text(draw, (430, 770), "claude" if flipped else "codex", 35, PALE_AMBER if flipped else PHOSPHOR, bold=True)
    if flipped:
        text(draw, (1340, 770), "boundary ✓ resumed", 20, AMBER, anchor="ra")
    else:
        text(draw, (1340, 770), "boundary · waiting", 20, MUTED, anchor="ra")
    return image


def loop_stage(i: int) -> Image.Image:
    image, draw = canvas()
    header(draw, "04 · message from the work", "No terminal required.")
    px0, py0, px1, py1 = 650, 290, 1270, 990
    rounded(draw, (px0, py0, px1, py1), fill="#0b1115", outline="#57666a", radius=50, width=4)
    rounded(draw, (px0 + 22, py0 + 54, px1 - 22, py1 - 34), fill="#0f191d", outline="#17262b", radius=24)
    text(draw, (px0 + 54, py0 + 91), "Telegram", 21, PHOSPHOR, bold=True)
    text(draw, (px0 + 54, py0 + 126), "brnrd · online", 16, MUTED)
    text(draw, (px0 + 54, py1 - 73), "Message", 18, MUTED)
    # Banner enters once in the first .8 seconds, then is completely still.
    t = min(1.0, i / 12)
    ease = 1 - (1 - t) ** 3
    y = int(py0 + 162 - (190 * ease))
    bx0, bx1 = px0 + 44, px1 - 44
    rounded(draw, (bx0, y, bx1, y + 148), fill="#18262a", outline=AMBER, radius=19, width=3)
    text(draw, (bx0 + 22, y + 29), "brnrd", 18, AMBER, bold=True)
    text(draw, (bx0 + 22, y + 65), "PR is up: #1749", 20, PHOSPHOR, bold=True)
    text(draw, (bx0 + 22, y + 101), "github.com/…/pull/1749", 15, PALE_AMBER)
    text(draw, (px0 + 44, py0 + 262), "dashboard · quiet below", 16, "#4c6268")
    for row in range(4):
        yy = py0 + 308 + row * 60
        draw.line((px0 + 48, yy, px1 - 48, yy), fill="#16242a", width=10)
    return image


def loop_away(i: int) -> Image.Image:
    image, draw = canvas()
    header(draw, "08 · while you're away", "The morning has receipts.")
    rounded(draw, (195, 325, 1195, 910), fill="#0a1519", outline=LINE, radius=30)
    # one quiet island and tree, frozen beneath the three event lights
    draw.ellipse((340, 665, 930, 825), fill="#132d2b", outline="#2d6259", width=2)
    draw.rectangle((615, 525, 634, 706), fill="#76562c")
    for box in [(530, 410, 640, 580), (610, 365, 750, 560), (705, 435, 820, 595)]:
        draw.ellipse(box, fill="#1e5d53", outline="#4d9d86", width=2)
    draw.ellipse((213, 345, 222, 354), fill=PHOSPHOR)
    text(draw, (238, 350), "night shift", 16, MUTED, anchor="lm")
    knots = [(390, "#1959"), (545, "#1960"), (700, "#1961")]
    for n, (y, label) in enumerate(knots, start=1):
        lit = i >= n * FPS
        col = AMBER if lit else "#35514f"
        if lit:
            draw.ellipse((1025 - 30, y - 30, 1025 + 30, y + 30), fill="#2b260f")
            draw.ellipse((1011, y - 14, 1039, y + 14), fill=AMBER)
            # Five-frame spark makes each recorded merge feel like an event,
            # without introducing any wandering scenery.
            if n * FPS <= i < n * FPS + 5:
                for dx, dy in ((0, -43), (0, 43), (-43, 0), (43, 0)):
                    draw.line((1025 + dx // 2, y + dy // 2, 1025 + dx, y + dy), fill=PALE_AMBER, width=3)
            draw.line((1048, y, 1120, y), fill=AMBER, width=2)
        else:
            draw.ellipse((1014, y - 11, 1036, y + 11), outline=col, width=3)
            draw.line((1048, y, 1120, y), fill=col, width=2)
        text(draw, (1140, y), label, 25, PALE_AMBER if lit else MUTED, bold=lit, anchor="lm")
    text(draw, (1280, 412), "MORNING DIGEST", 19, MUTED, bold=True)
    text(draw, (1280, 472), "three self-woken passes", 24, PHOSPHOR, bold=True)
    text(draw, (1280, 520), "nine merges while you slept", 19, PALE_AMBER)
    # A real time range, with the clock moving once through the established night.
    hour = 2 + round(5 * i / (FRAMES - 1))
    text(draw, (1635, 810), f"{hour:02d}:00", 55, AMBER, bold=True, anchor="ra")
    text(draw, (1635, 855), "02:00 → 07:00", 16, MUTED, anchor="ra")
    return image


LOOPS = {
    "loop-1-lose-the-thread": loop_lose,
    "loop-2-the-wall": loop_wall,
    "loop-3-stage": loop_stage,
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
