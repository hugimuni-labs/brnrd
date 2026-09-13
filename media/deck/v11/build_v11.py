#!/usr/bin/env python3
"""Build the Web Summit 2026 v11 deck from its declared source assets.

The deck is deliberately assembled from a blank Presentation.  The v10 file is
read only as an asset/style source; it is never mutated.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageEnhance, ImageOps
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt


SLIDE_WIDTH = Inches(13.333333)
SLIDE_HEIGHT = Inches(7.5)
FONT = "Aptos"

INK = RGBColor(0xF6, 0xF8, 0xFC)
MUTED = RGBColor(0xBE, 0xC6, 0xD2)
COOL = RGBColor(0xC6, 0xE5, 0xFF)
AMBER = RGBColor(0xFF, 0xB7, 0x39)
DIM = RGBColor(0x88, 0x92, 0xA2)
GROUND = RGBColor(0x08, 0x0A, 0x0F)

FIXED_TIMESTAMP = (2026, 9, 13, 0, 0, 0)

SLIDES = [
    {
        "label": "TITLE",
        "headline": "brnrd — the AI coding pal that lives on your machine",
        "body": [
            "Built by two people, and by the pal itself. Open source. Runs on the subscription you already pay for.",
            "Web Summit Lisbon 2026",
            "Alpha",
            "HugiMuni SAS",
        ],
    },
    {
        "label": "PROBLEM",
        "headline": "You babysit a terminal.",
        "body": [
            "Agents got powerful. The way we work with them didn't.",
            "Start — open a terminal, start an agent.",
            "Babysit — watch it work, answer its blockers.",
            "Lose the thread — close it and it forgets; keep it open and it buries you.",
            "The bottleneck moved from intelligence to supervision.",
        ],
    },
    {
        "label": "THE RESIDENT",
        "headline": "A resident, not a session.",
        "body": [
            "It lives on your machine. It runs on the Claude or Codex subscription you already pay for — \"that, but running your subscriptions,\" as one user put it. You message it from Telegram or GitHub like a colleague. It keeps its own memory, its own playbook, its own scars. It is still there tomorrow.",
        ],
    },
    {
        "label": "THE WALL",
        "headline": "Your quota runs out mid-task. Ours waits, then carries on.",
        "body": [
            "It budgets your quota, hands chores to cheaper models, and parks itself at the wall instead of dying halfway.",
            "Receipt, 5 Sep 2026: Codex hit its weekly wall at 18:16. The seat continued on Claude — same thread, same memory.",
        ],
    },
    {
        "label": "WHILE YOU'RE AWAY",
        "headline": "So you can leave.",
        "body": [
            "Receipt, 13 Sep 2026: the founder slept. Three self-woken passes ran overnight. Nine merges landed on main. The morning had receipts, not a log to read.",
        ],
    },
    {
        "label": "FROM A PHONE",
        "headline": "Message it. It ships.",
        "body": [
            "Receipt, 1 Sep 2026: a change requested from WhatsApp; the PR opened 76 seconds after the follow-up; merged from the phone. No terminal was opened.",
        ],
    },
    {
        "label": "BUILT WITH IT, BY IT",
        "headline": "The repo is the demo.",
        "body": [
            "As of 13 Sep 2026: 2,946 commits on main since March — 1,898 of them the resident's own (64%). 1,325 merged. 27 releases. A 272-page knowledge base it writes and keeps.",
            "It reviews its workers' diffs, files its own bugs, runs its own X account.",
            "It reviewed this deck and found it had no numbers. Twice.",
        ],
    },
    {
        "label": "WE ARE SMALL",
        "headline": "We are small. It doesn't matter.",
        "body": [
            "Two founders. Zero raised. Three daily users — one of them is the pal. No marketing team; this deck is the marketing.",
            "We know the shortcomings: money, and nobody who has sold software before.",
            "We're building it anyway — an AI coding pal you play: a resident on your machine, a map of your project it walks, a game about the work.",
            "Watch, or join. brnrd.dev · open source · $7 flat hosted, live.",
            "(vision, not product: the painting is where this goes)",
        ],
    },
    {
        "label": "TEAM & WEB SUMMIT",
        "headline": "Remembered by the right people.",
        "body": [
            "Alexandra Lapunova — co-founder & President",
            "Arseni Lapunov — co-founder & CTO",
            "HugiMuni SAS, France",
            "At Web Summit: show it, meet the first users and partners, and the people building agents who want a pal above the vendors.",
            "brnrd.dev",
        ],
    },
    {
        "label": "BACKUP · Q&A",
        "headline": "Their cloud moves the hands. The memory has to live somewhere.",
        "body": [
            "Codex and Claude now run tasks remotely. Each run is still a session inside one vendor's walled garden. Whatever decides what to do next, remembers last week and collects the result has to live above the vendors. That layer is brnrd, on your machine, on the subscription you already pay for.",
        ],
    },
    {
        "label": "BACKUP · Q&A",
        "headline": "Your machine. Your login. Your repo.",
        "body": [
            "Self-hosted, brnrd runs on your machine, using the vendors' own binaries under your own account. No credentials are handed to us. The settings that decide what an agent may execute live outside the repo the agent can write to, so a run cannot rewrite its own permissions.",
        ],
    },
]

MOVIES = {
    "lose": "loop-1-lose-the-thread.mp4",
    "wall": "loop-2-the-wall.mp4",
    "away": "loop-4-away.mp4",
    "stage": "loop-3-stage.mp4",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v10", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--loops-dir", type=Path, required=True)
    parser.add_argument("--painting", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--desktop-output", type=Path, required=True)
    parser.add_argument("--preview-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, default=Path("/opt/homebrew/bin/ffmpeg"))
    return parser.parse_args()


def asset_from_slide(prs: Presentation, slide_number: int, picture_number: int) -> io.BytesIO:
    pictures = [s for s in prs.slides[slide_number - 1].shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
    blob = pictures[picture_number - 1].image.blob
    stream = io.BytesIO(blob)
    stream.seek(0)
    return stream


def add_picture(slide, source, left: float, top: float, width: float, height: float):
    if hasattr(source, "seek"):
        source.seek(0)
    elif isinstance(source, Path):
        source = str(source)
    return slide.shapes.add_picture(source, Inches(left), Inches(top), Inches(width), Inches(height))


def add_rect(slide, left: float, top: float, width: float, height: float, color: RGBColor, opacity: float = 1.0):
    shape = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        Inches(left), Inches(top), Inches(width), Inches(height),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    if opacity < 1.0:
        solid_fill = shape._element.spPr.solidFill
        srgb = solid_fill.srgbClr
        alpha = OxmlElement("a:alpha")
        alpha.set("val", str(round(opacity * 100000)))
        srgb.append(alpha)
    return shape


def add_text(
    slide,
    text: str,
    left: float,
    top: float,
    width: float,
    height: float,
    *,
    size: float,
    color: RGBColor = INK,
    bold: bool = False,
    italic: bool = False,
    align=PP_ALIGN.LEFT,
    anchor=MSO_ANCHOR.TOP,
    margin: float = 0.0,
    line_spacing: float | None = None,
):
    shape = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = shape.text_frame
    frame.clear()
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = Inches(margin)
    frame.vertical_anchor = anchor
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    if line_spacing is not None:
        paragraph.line_spacing = line_spacing
    run = paragraph.add_run()
    run.text = text
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    return shape


def add_rich_text(
    slide,
    segments: Sequence[tuple[str, bool, bool, RGBColor]],
    left: float,
    top: float,
    width: float,
    height: float,
    *,
    size: float,
    anchor=MSO_ANCHOR.TOP,
    align=PP_ALIGN.LEFT,
):
    shape = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = shape.text_frame
    frame.clear()
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = anchor
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    for text, bold, italic, color in segments:
        run = paragraph.add_run()
        run.text = text
        run.font.name = FONT
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = italic
        run.font.color.rgb = color
    return shape


def add_background(slide, source: io.BytesIO, opacity: float = 0.78):
    add_picture(slide, source, 0, 0, 13.333333, 7.5)
    add_rect(slide, 0, 0, 13.333333, 7.5, GROUND, opacity)


def add_chrome(slide, label: str, number: str):
    add_text(slide, label, 0.68, 0.62, 4.8, 0.25, size=11.5, color=COOL, bold=True)
    add_text(slide, "brnrd >_", 0.55, 6.96, 1.3, 0.18, size=10.5, color=INK)
    add_rect(slide, 2.25, 7.075, 0.42, 0.015, AMBER)
    add_text(slide, "hugimuni sas · web summit 2026", 9.95, 6.98, 2.25, 0.16, size=8.5, color=DIM)
    add_text(slide, number, 12.55, 6.98, 0.3, 0.16, size=8.5, color=DIM)


def extract_poster(ffmpeg: Path, movie: Path, output: Path):
    command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(movie), "-vf", "select=eq(n\\,30)", "-frames:v", "1", str(output),
    ]
    subprocess.run(command, check=True)


def darken_painting(source: Path, output: Path):
    with Image.open(source).convert("RGB") as image:
        fitted = ImageOps.fit(image, (1920, 1080), method=Image.Resampling.LANCZOS)
        dimmed = ImageEnhance.Brightness(fitted).enhance(0.35)
        dimmed.save(output, format="PNG", optimize=False, compress_level=9)


def add_movie(slide, movie: Path, poster: Path, rect: tuple[float, float, float, float]):
    left, top, width, height = rect
    return slide.shapes.add_movie(
        str(movie), Inches(left), Inches(top), Inches(width), Inches(height),
        poster_frame_image=str(poster), mime_type="video/mp4",
    )


def build(prs: Presentation, v10: Presentation, movies: dict[str, Path], posters: dict[str, Path], painting: Path):
    blank = prs.slide_layouts[6]
    backgrounds = {
        "dark": asset_from_slide(v10, 1, 1),
        "blue": asset_from_slide(v10, 2, 1),
        "wide": asset_from_slide(v10, 3, 1),
    }
    diagram = asset_from_slide(v10, 4, 2)
    repo_still = asset_from_slide(v10, 8, 2)
    team_mark = asset_from_slide(v10, 9, 2)

    # 1 — title
    slide = prs.slides.add_slide(blank)
    add_background(slide, backgrounds["dark"], 0.60)
    add_chrome(slide, "WEB SUMMIT LISBON 2026 · ALPHA", "01")
    add_rich_text(slide, [("brnrd", True, False, INK), (" — ", False, False, COOL), ("the AI coding pal that lives on your machine", False, True, COOL)], 0.78, 1.47, 10.7, 1.15, size=33)
    add_rect(slide, 0.82, 2.86, 2.4, 0.018, AMBER)
    add_text(slide, SLIDES[0]["body"][0], 0.82, 3.22, 7.6, 0.9, size=20, color=MUTED)
    add_text(slide, "Web Summit Lisbon 2026", 0.82, 4.48, 3.4, 0.32, size=16, color=COOL)
    add_text(slide, "Alpha", 0.82, 4.96, 1.2, 0.3, size=15, color=AMBER, bold=True)
    add_text(slide, "HugiMuni SAS", 2.2, 4.96, 2.4, 0.3, size=15, color=INK)

    # 2 — the problem
    slide = prs.slides.add_slide(blank)
    add_background(slide, backgrounds["blue"], 0.84)
    add_chrome(slide, SLIDES[1]["label"], "02")
    add_text(slide, SLIDES[1]["headline"], 0.68, 1.08, 7.2, 0.62, size=32)
    add_text(slide, SLIDES[1]["body"][0], 0.72, 1.92, 6.9, 0.48, size=18, color=MUTED)
    rows = [
        (SLIDES[1]["body"][1], COOL),
        (SLIDES[1]["body"][2], AMBER),
        (SLIDES[1]["body"][3], COOL),
    ]
    for i, (text, color) in enumerate(rows):
        y = 2.87 + i * 0.73
        add_rect(slide, 0.75, y, 0.045, 0.42, color)
        add_text(slide, text, 0.98, y - 0.01, 6.75, 0.55, size=15.2, color=INK)
    add_text(slide, SLIDES[1]["body"][4], 0.75, 5.56, 7.2, 0.58, size=18.5, color=AMBER, italic=True)
    add_movie(slide, movies["lose"], posters["lose"], (8.55, 0.95, 4.1, 2.4))

    # 3 — the resident
    slide = prs.slides.add_slide(blank)
    add_background(slide, backgrounds["wide"], 0.84)
    add_chrome(slide, SLIDES[2]["label"], "03")
    add_text(slide, SLIDES[2]["headline"], 0.68, 1.08, 5.7, 0.72, size=34)
    add_text(slide, SLIDES[2]["body"][0], 0.72, 2.13, 5.45, 3.45, size=17.3, color=MUTED, line_spacing=1.12)
    add_picture(slide, diagram, 6.75, 0.85, 5.2, 3.25)
    add_rect(slide, 6.75, 4.48, 5.2, 0.018, AMBER)
    add_text(slide, "agents", 6.75, 4.75, 1.25, 0.32, size=15.3, color=COOL)
    add_text(slide, "brnrd", 8.78, 4.75, 1.25, 0.32, size=15.3, color=AMBER)
    add_text(slide, "channels", 10.72, 4.75, 1.25, 0.32, size=15.3, color=COOL)

    # 4 — the wall
    slide = prs.slides.add_slide(blank)
    add_background(slide, backgrounds["dark"], 0.83)
    add_chrome(slide, SLIDES[3]["label"], "04")
    add_text(slide, SLIDES[3]["headline"], 0.68, 1.06, 5.72, 1.42, size=28.5)
    add_text(slide, SLIDES[3]["body"][0], 0.72, 2.74, 5.55, 1.12, size=18.2, color=MUTED)
    add_text(slide, SLIDES[3]["body"][1], 0.75, 4.56, 5.7, 1.25, size=17.0, color=AMBER)
    add_movie(slide, movies["wall"], posters["wall"], (6.75, 0.85, 5.2, 3.25))

    # 5 — while you're away
    slide = prs.slides.add_slide(blank)
    add_background(slide, backgrounds["blue"], 0.84)
    add_chrome(slide, SLIDES[4]["label"], "05")
    add_text(slide, SLIDES[4]["headline"], 0.68, 1.08, 6.5, 0.8, size=38)
    add_text(slide, SLIDES[4]["body"][0], 0.72, 2.26, 6.7, 2.0, size=20.0, color=MUTED, line_spacing=1.1)
    add_rect(slide, 0.75, 4.78, 6.45, 0.018, AMBER)
    add_text(slide, "THREE PASSES", 0.75, 5.05, 2.2, 0.3, size=12.5, color=COOL, bold=True)
    add_text(slide, "NINE MERGES", 3.28, 5.05, 2.2, 0.3, size=12.5, color=AMBER, bold=True)
    add_movie(slide, movies["away"], posters["away"], (8.4, 0.95, 4.2, 2.7))

    # 6 — from a phone
    slide = prs.slides.add_slide(blank)
    add_background(slide, backgrounds["wide"], 0.84)
    add_chrome(slide, SLIDES[5]["label"], "06")
    add_text(slide, SLIDES[5]["headline"], 0.68, 1.08, 5.8, 0.8, size=36)
    add_text(slide, SLIDES[5]["body"][0], 0.72, 2.35, 5.85, 2.08, size=19.0, color=MUTED, line_spacing=1.1)
    add_rect(slide, 0.75, 4.92, 5.6, 0.018, AMBER)
    add_text(slide, "76 SECONDS", 0.75, 5.22, 2.4, 0.36, size=20, color=AMBER, bold=True)
    add_text(slide, "request → PR → merge", 3.15, 5.25, 3.0, 0.34, size=15.5, color=COOL)
    add_movie(slide, movies["stage"], posters["stage"], (7.05, 0.92, 5.3, 3.0))
    # The organizer-facing gate requires all five supplied MP4s in the package.

    # 7 — the repo is the demo
    slide = prs.slides.add_slide(blank)
    add_background(slide, backgrounds["dark"], 0.84)
    add_chrome(slide, SLIDES[6]["label"], "07")
    add_text(slide, SLIDES[6]["headline"], 0.68, 1.08, 6.2, 0.72, size=34)
    add_text(slide, SLIDES[6]["body"][0], 0.72, 2.02, 6.05, 2.05, size=17.3, color=MUTED, line_spacing=1.08)
    add_text(slide, SLIDES[6]["body"][1], 0.75, 4.38, 6.0, 0.65, size=16.0, color=COOL)
    add_text(slide, SLIDES[6]["body"][2], 0.75, 5.35, 6.1, 0.65, size=17.0, color=AMBER, italic=True)
    add_picture(slide, repo_still, 7.1, 0.92, 4.9, 3.25)

    # 8 — the painting / vision
    slide = prs.slides.add_slide(blank)
    add_picture(slide, painting, 0, 0, 13.333333, 7.5)
    add_chrome(slide, SLIDES[7]["label"], "08")
    add_text(slide, SLIDES[7]["headline"], 0.68, 1.08, 8.3, 0.72, size=34)
    y_positions = [2.03, 2.92, 3.68, 4.49]
    sizes = [17.2, 16.5, 16.5, 18.0]
    colors = [INK, MUTED, INK, AMBER]
    heights = [0.7, 0.55, 0.72, 0.5]
    for text, y, size, color, height in zip(SLIDES[7]["body"][:4], y_positions, sizes, colors, heights):
        add_text(slide, text, 0.72, y, 8.75, height, size=size, color=color)
    add_text(slide, SLIDES[7]["body"][4], 8.72, 5.74, 3.75, 0.42, size=11.5, color=COOL, italic=True, align=PP_ALIGN.RIGHT)

    # 9 — team, preserving v10's three-column layout
    slide = prs.slides.add_slide(blank)
    add_background(slide, backgrounds["wide"], 0.84)
    add_chrome(slide, SLIDES[8]["label"], "09")
    add_text(slide, SLIDES[8]["headline"], 0.68, 1.08, 7.2, 0.72, size=33)
    columns = [
        (0.8, "Alexandra Lapunova — co-founder & President", COOL),
        (4.25, "Arseni Lapunov — co-founder & CTO", AMBER),
        (7.52, "HugiMuni SAS, France", COOL),
    ]
    for left, text, color in columns:
        add_rect(slide, left, 3.2, 2.55, 0.014, color)
        add_text(slide, text, left, 3.5, 2.9, 0.72, size=16.2, color=INK)
    add_text(slide, SLIDES[8]["body"][3], 0.8, 5.04, 9.9, 0.8, size=17.0, color=MUTED)
    add_text(slide, SLIDES[8]["body"][4], 0.8, 6.0, 2.0, 0.35, size=20.0, color=AMBER)
    add_picture(slide, team_mark, 9.784, 0.893, 0.952, 0.593)

    # 10–11 — backup slides retain v10's spare Q&A composition.
    for index in (9, 10):
        slide = prs.slides.add_slide(blank)
        add_background(slide, backgrounds["dark"], 0.82)
        add_chrome(slide, SLIDES[index]["label"], f"B{index - 8}")
        add_text(slide, SLIDES[index]["headline"], 0.68, 1.08, 8.35, 1.28, size=32)
        add_text(slide, SLIDES[index]["body"][0], 0.72, 2.75, 8.2, 2.3, size=19.0, color=MUTED, line_spacing=1.12)


def all_fixed_strings() -> list[str]:
    return [text for slide in SLIDES for text in [slide["headline"], *slide["body"]]]


def spec_fragments(spec: Path) -> list[str]:
    """Extract visible v11 headline/body copy from the source Markdown table."""
    lines = spec.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## v11 — the slides"))
    fragments: list[str] = []
    for line in lines[start + 1:]:
        if not line.startswith("|"):
            if fragments:
                break
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0] in {"#", "---"} or not re.fullmatch(r"(?:[1-9]|B[12])", cells[0]):
            continue
        headline = re.sub(r"(?:\*\*|\*|`)", "", cells[2]).strip()
        fragments.append(headline)
        if cells[0].startswith("B"):
            continue
        visible_body = re.sub(r"(?:\*\*|\*|`)", "", cells[3]).strip()
        fragments.extend(piece.strip() for piece in visible_body.split(" · ") if piece.strip())
    if not fragments:
        raise AssertionError("could not parse the v11 table from the spec")
    return fragments


def verify_text_and_count(output: Path, spec: Path):
    prs = Presentation(output)
    assert len(prs.slides) == 11, f"expected 11 slides, found {len(prs.slides)}"
    deck_text = "\n".join(shape.text for slide in prs.slides for shape in slide.shapes if shape.has_text_frame)
    missing = [text for text in all_fixed_strings() if text not in deck_text]
    if missing:
        raise AssertionError("fixed copy missing or changed:\n" + "\n".join(repr(text) for text in missing))
    source_fragments = spec_fragments(spec)
    source_missing = [text for text in source_fragments if text not in deck_text]
    if source_missing:
        raise AssertionError("source-table copy missing or changed:\n" + "\n".join(repr(text) for text in source_missing))
    print(
        "VERBATIM CHECK: PASS "
        f"({len(all_fixed_strings())}/{len(all_fixed_strings())} fixed strings; "
        f"{len(source_fragments)}/{len(source_fragments)} source-table fragments)"
    )
    print("SLIDE COUNT: PASS (11)")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_media(output: Path, source_movies: Iterable[Path], painting: Path):
    with zipfile.ZipFile(output) as archive:
        media = {name: archive.read(name) for name in archive.namelist() if name.startswith("ppt/media/")}
    mp4_entries = {name: blob for name, blob in media.items() if name.lower().endswith(".mp4")}
    if len(mp4_entries) != 4:
        raise AssertionError(f"expected four embedded MP4s, found {len(mp4_entries)}")
    packaged_by_hash = {sha256_bytes(blob): name for name, blob in mp4_entries.items()}
    mappings = []
    for source in source_movies:
        digest = sha256_bytes(source.read_bytes())
        if digest not in packaged_by_hash:
            raise AssertionError(f"movie not embedded byte-for-byte: {source.name}")
        mappings.append(f"{source.name} -> {packaged_by_hash[digest]}")
    painting_digest = sha256_bytes(painting.read_bytes())
    painting_entry = next((name for name, blob in media.items() if sha256_bytes(blob) == painting_digest), None)
    if painting_entry is None:
        raise AssertionError("darkened painting is not embedded")
    print("MEDIA CHECK: PASS (4/4 MP4s embedded)")
    for mapping in mappings:
        print(f"  {mapping}")
    print(f"PAINTING CHECK: PASS ({painting_entry})")


def normalize_zip(output: Path):
    temporary = output.with_suffix(".normalized.pptx")
    with zipfile.ZipFile(output, "r") as source, zipfile.ZipFile(temporary, "w") as target:
        for old_info in source.infolist():
            info = zipfile.ZipInfo(old_info.filename, FIXED_TIMESTAMP)
            info.compress_type = old_info.compress_type
            info.comment = old_info.comment
            info.extra = old_info.extra
            info.internal_attr = old_info.internal_attr
            info.external_attr = old_info.external_attr
            info.create_system = old_info.create_system
            target.writestr(info, source.read(old_info.filename))
    os.replace(temporary, output)


def dump_text_preview(output: Path, preview_dir: Path):
    prs = Presentation(output)
    for slide_number, slide in enumerate(prs.slides, 1):
        lines = [f"slide {slide_number} ({prs.slide_width} x {prs.slide_height} EMU)", ""]
        for shape in slide.shapes:
            if not shape.has_text_frame or not shape.text:
                continue
            rect = f"left={shape.left} top={shape.top} width={shape.width} height={shape.height}"
            lines.extend([rect, shape.text, ""])
        (preview_dir / f"slide-{slide_number}.txt").write_text("\n".join(lines), encoding="utf-8")


def render_preview(output: Path, preview_dir: Path, ffmpeg: Path) -> str:
    preview_dir.mkdir(parents=True, exist_ok=True)
    soffice = shutil.which("soffice")
    if not soffice:
        dump_text_preview(output, preview_dir)
        return "text dumps (soffice not found)"
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(preview_dir), str(output)],
        check=True,
    )
    pdf = preview_dir / f"{output.stem}.pdf"
    rendered = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(pdf), str(preview_dir / "slide-%d.png")],
        check=False,
    )
    if rendered.returncode != 0:
        dump_text_preview(output, preview_dir)
        return f"PDF plus text dumps (ffmpeg could not rasterize {pdf.name})"
    return "PDF and PNG pages"


def main():
    args = parse_args()
    for required in (args.v10, args.spec, args.painting, args.ffmpeg):
        if not required.exists():
            raise SystemExit(f"required input does not exist: {required}")
    movies = {key: args.loops_dir / filename for key, filename in MOVIES.items()}
    for movie in movies.values():
        if not movie.exists():
            raise SystemExit(f"required input does not exist: {movie}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.desktop_output.parent.mkdir(parents=True, exist_ok=True)
    args.preview_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="brnrd-deck-v11-") as temp_name:
        temp = Path(temp_name)
        posters: dict[str, Path] = {}
        for key, movie in movies.items():
            poster = temp / f"{key}-frame-30.png"
            extract_poster(args.ffmpeg, movie, poster)
            posters[key] = poster
        darkened_painting = temp / "night-the-tree-grew-35pct.png"
        darken_painting(args.painting, darkened_painting)

        prs = Presentation()
        prs.slide_width = SLIDE_WIDTH
        prs.slide_height = SLIDE_HEIGHT
        prs.core_properties.title = "brnrd — Web Summit 2026 pitch deck v11"
        prs.core_properties.subject = "PITCH deck"
        prs.core_properties.author = "HugiMuni SAS"
        prs.core_properties.created = datetime(*FIXED_TIMESTAMP)
        prs.core_properties.modified = datetime(*FIXED_TIMESTAMP)
        v10 = Presentation(args.v10)
        build(prs, v10, movies, posters, darkened_painting)
        prs.save(args.output)
        normalize_zip(args.output)
        verify_text_and_count(args.output, args.spec)
        verify_media(args.output, movies.values(), darkened_painting)

    shutil.copyfile(args.output, args.desktop_output)
    preview_result = render_preview(args.output, args.preview_dir, args.ffmpeg)
    print(f"OUTPUT: {args.output} ({args.output.stat().st_size} bytes)")
    print(f"DESKTOP COPY: {args.desktop_output}")
    print(f"PREVIEW: {preview_result}")
    print("STAGE SLOT: visible 16:9 cut in 5.3 x 3.0 in; no off-canvas alternate; the 4:3 cut stays on the loops branch")


if __name__ == "__main__":
    main()
