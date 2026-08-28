#!/usr/bin/env python3
"""Generate the canonical HugiMuni H/M mark for screen and print.

The geometry is shared by every register. Material is not: the screen master
emits light; print uses flat inks and small pale crossing knots instead of
trying to imitate glow.

Run: python3 media/brand/hugimuni/build.py
"""
from pathlib import Path

OUT = Path(__file__).parent

BOARD = 512
AXIS = BOARD / 2
LEFT, RIGHT = 152, 360
TOP, BOTTOM = 156, 356
CROSS = 276
OVERHANG = 20
SPREAD = 20
RISE, DIP = 0, 0
TAIL = 20
STROKE = 28
STEM_STROKE = 40
STEM_CORE = 16
PRINT_STEM_CORE = 10
GHOST = 7
GRAIN = 42
INTERSECTION = "#eadfca"
GROUND = "#050705"
GROUND_RX = 64
BLOOM_BLUR = 6
BLOOM_OPACITY = 0.58
CORE_BLUR = 0.8
CORE_OPACITY = 0.48

# One canonical palette. The former coral/turquoise exploration remains in git
# history rather than shipping beside the production mark as a second identity.
PALETTE = ("#ff9a1f", "#69c7df")
NAME = "amber-sky"

# Print values are explicit and intentionally boring. PRINT_BLACK is a hook for
# the actual printer/RIP profile: replace it with the vendor's requested rich
# black if the stand producer specifies one rather than guessing here.
PRINT_CMYK = ((0.00, 0.47, 0.88, 0.00), (0.56, 0.05, 0.03, 0.00))
PRINT_INTERSECTION = (0.03, 0.05, 0.13, 0.00)
PRINT_BLACK = (0.00, 0.00, 0.00, 1.00)
PRINT_KNOT_W = 3
PRINT_KNOT_LEN = 10

STEMS = f'<path d="M {LEFT} {TOP} V {BOTTOM}"/><path d="M {RIGHT} {TOP} V {BOTTOM}"/>'
BAR_H = f'<path d="M {LEFT - OVERHANG} {CROSS} H {RIGHT + OVERHANG}"/>'


def vee_m() -> str:
    drop = BOTTOM + DIP
    return (
        f'<path d="M {LEFT - SPREAD} {TOP - RISE} L {AXIS + TAIL} {drop}"/>'
        f'<path d="M {RIGHT + SPREAD} {TOP - RISE} L {AXIS - TAIL} {drop}"/>'
    )


def _attrs(width: float) -> str:
    return (
        f'fill="none" stroke-width="{width}" '
        'stroke-linecap="round" stroke-linejoin="round" '
        'style="mix-blend-mode:screen"'
    )


def _screen_glyph(scale, a: str, b: str, core: str) -> str:
    """Draw the shared screen geometry for one material pass."""
    return (
        f'<g {_attrs(scale(STEM_STROKE))} stroke="{a}" transform="translate({-GHOST},0)">{STEMS}</g>'
        f'<g {_attrs(scale(STEM_STROKE))} stroke="{b}" transform="translate({GHOST},0)">{STEMS}</g>'
        f'<g {_attrs(scale(STEM_CORE))} stroke="{core}">{STEMS}</g>'
        f'<g {_attrs(scale(STROKE))} stroke="{a}">{BAR_H}</g>'
        f'<g {_attrs(scale(STROKE))} stroke="{b}">{vee_m()}</g>'
    )


def screen_svg(*, with_ground: bool = False) -> str:
    """Emissive screen register; transparent master, optional icon board."""
    a, b = PALETTE
    grain = max(0, min(100, GRAIN)) / 100
    body = _screen_glyph(lambda w: w, a, b, INTERSECTION)
    cores = _screen_glyph(
        lambda w: max(2, round(w * 0.14)),
        "#fff6e4",
        "#eefbff",
        "#ffffff",
    )
    mask_body = _screen_glyph(lambda w: w, "#fff", "#fff", "#fff")
    ground = (
        f'  <rect width="{BOARD}" height="{BOARD}" rx="{GROUND_RX}" fill="{GROUND}"/>\n'
        if with_ground
        else ""
    )
    title_suffix = "icon" if with_ground else "screen"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{BOARD}" viewBox="0 0 {BOARD} {BOARD}">
  <title>hugimuni — H and M on shared stems ({title_suffix})</title>
  <defs>
    <filter id="hm-bloom" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="{BLOOM_BLUR}"/>
    </filter>
    <filter id="hm-core" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="{CORE_BLUR}"/>
    </filter>
    <filter id="hm-grain" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="3" seed="23"/>
      <feColorMatrix type="saturate" values="0"/>
    </filter>
    <pattern id="hm-scanlines" width="4" height="4" patternUnits="userSpaceOnUse">
      <path d="M0 3.5H4" stroke="#000" stroke-width="0.6" opacity="0.5"/>
    </pattern>
    <mask id="hm-strokes">
      <rect width="{BOARD}" height="{BOARD}" fill="#000"/>
      {mask_body}
    </mask>
  </defs>
{ground}  <g style="isolation:isolate">
    <g filter="url(#hm-bloom)" opacity="{BLOOM_OPACITY}">{body}</g>
    {body}
    <g filter="url(#hm-core)" opacity="{CORE_OPACITY}">{cores}</g>
    <g mask="url(#hm-strokes)">
      <rect width="{BOARD}" height="{BOARD}" filter="url(#hm-grain)" opacity="{grain * .42:.3f}" style="mix-blend-mode:overlay"/>
      <rect width="{BOARD}" height="{BOARD}" fill="url(#hm-scanlines)" opacity="{grain * .36:.3f}" style="mix-blend-mode:multiply"/>
    </g>
  </g>
</svg>
"""


def _segment_intersection(a1, a2, b1, b2):
    """Return the intersection of two finite segments, or None."""
    x1, y1 = a1
    x2, y2 = a2
    x3, y3 = b1
    x4, y4 = b2
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / den
    if not (0 <= t <= 1 and 0 <= u <= 1):
        return None
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def crossing_marks():
    """The six H/M weave crossings as local highlight segments.

    A short segment reads as a material overlap/registration event; a dot
    reads as a rivet. Bar crossings highlight horizontally, stem/leg
    crossings vertically.
    """
    drop = BOTTOM + DIP
    bar = ((LEFT - OVERHANG, CROSS), (RIGHT + OVERHANG, CROSS))
    stems = [((LEFT, TOP), (LEFT, BOTTOM)), ((RIGHT, TOP), (RIGHT, BOTTOM))]
    legs = [
        ((LEFT - SPREAD, TOP - RISE), (AXIS + TAIL, drop)),
        ((RIGHT + SPREAD, TOP - RISE), (AXIS - TAIL, drop)),
    ]
    specs = [
        (bar, stems[0], "h"),
        (bar, stems[1], "h"),
        (bar, legs[0], "h"),
        (bar, legs[1], "h"),
        (stems[0], legs[0], "v"),
        (stems[1], legs[1], "v"),
    ]
    marks = []
    for first, second, orientation in specs:
        point = _segment_intersection(*first, *second)
        if point is None:
            raise ValueError("HugiMuni geometry lost one of its six authored crossings")
        marks.append((*point, orientation))
    return marks


def _rgb_print_body(*, with_ground: bool) -> str:
    """Flat RGB proof/fallback using the exact print-register geometry."""
    a, b = PALETTE
    ground = f'  <rect width="{BOARD}" height="{BOARD}" fill="#000"/>\n' if with_ground else ""
    knots = "".join(
        (
            f'<path d="M {x - PRINT_KNOT_LEN / 2:.3f} {y:.3f} H {x + PRINT_KNOT_LEN / 2:.3f}" stroke="{INTERSECTION}" stroke-width="{PRINT_KNOT_W}" stroke-linecap="round"/>'
            if orientation == "h"
            else f'<path d="M {x:.3f} {y - PRINT_KNOT_LEN / 2:.3f} V {y + PRINT_KNOT_LEN / 2:.3f}" stroke="{INTERSECTION}" stroke-width="{PRINT_KNOT_W}" stroke-linecap="round"/>'
        )
        for x, y, orientation in crossing_marks()
    )
    return f"""{ground}  <g fill="none" stroke-linecap="round" stroke-linejoin="round">
    <g stroke="{a}" stroke-width="{STEM_STROKE}" transform="translate({-GHOST},0)">{STEMS}</g>
    <g stroke="{b}" stroke-width="{STEM_STROKE}" transform="translate({GHOST},0)">{STEMS}</g>
    <g stroke="{INTERSECTION}" stroke-width="{PRINT_STEM_CORE}">{STEMS}</g>
    <g stroke="{a}" stroke-width="{STROKE}">{BAR_H}</g>
    <g stroke="{b}" stroke-width="{STROKE}">{vee_m()}</g>
  </g>
  <g>{knots}</g>"""


def flat_svg() -> str:
    """Transparent, filter-free vector fallback for small/web uses."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{BOARD}" viewBox="0 0 {BOARD} {BOARD}">
  <title>hugimuni flat mark ({NAME})</title>
{_rgb_print_body(with_ground=False)}
</svg>
"""


def print_svg(*, lockup: bool = False) -> str:
    """Portable RGB visual proof of the EPS print register."""
    height = 690 if lockup else BOARD
    body = _rgb_print_body(with_ground=True)
    if height != BOARD:
        body = body.replace(f'height="{BOARD}"', f'height="{height}"')
    wordmark = ""
    if lockup:
        a, b = PALETTE
        wordmark = f"""
  <g fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-width="8">
    <path stroke="{a}" d="M142 535V580M178 535V580M142 557H178"/>
    <path stroke="{b}" d="M142 600V645M178 600V645M142 600L160 642L178 600"/>
  </g>
  <g fill="#ffffff" font-family="Helvetica, Arial, sans-serif" font-size="34">
    <text x="194" y="582">UGI</text><text x="194" y="647">UNI</text>
  </g>"""
    # _rgb_print_body is board-sized by construction; replace its background
    # line for the taller lockup proof while leaving the mark geometry alone.
    proof = _rgb_print_body(with_ground=False)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{height}" viewBox="0 0 {BOARD} {height}">
  <title>hugimuni print register ({NAME})</title>
  <rect width="{BOARD}" height="{height}" fill="#000"/>
{proof}{wordmark}
</svg>
"""


def eps(*, lockup: bool = False) -> str:
    """Level-2 process-CMYK EPS for physical production."""
    cmyk_a, cmyk_b = PRINT_CMYK
    height = 690 if lockup else BOARD

    def cmyk(value) -> str:
        return " ".join(f"{channel:.3f}" for channel in value)

    def line(x1, y1, x2, y2, width, color):
        return (
            f"{cmyk(color)} setcmykcolor {width} setlinewidth "
            f"{x1} {height-y1} moveto {x2} {height-y2} lineto stroke"
        )

    def highlight(x, y, orientation, color):
        half = PRINT_KNOT_LEN / 2
        if orientation == "h":
            return line(x - half, y, x + half, y, PRINT_KNOT_W, color)
        return line(x, y - half, x, y + half, PRINT_KNOT_W, color)

    commands = [
        f"{cmyk(PRINT_BLACK)} setcmykcolor 0 0 {BOARD} {height} rectfill",
        "false setoverprint",
        "1 setlinecap 1 setlinejoin",
    ]
    for x in (LEFT - GHOST, RIGHT - GHOST):
        commands.append(line(x, TOP, x, BOTTOM, STEM_STROKE, cmyk_a))
    for x in (LEFT + GHOST, RIGHT + GHOST):
        commands.append(line(x, TOP, x, BOTTOM, STEM_STROKE, cmyk_b))
    for x in (LEFT, RIGHT):
        commands.append(line(x, TOP, x, BOTTOM, PRINT_STEM_CORE, PRINT_INTERSECTION))

    commands.append(line(LEFT - OVERHANG, CROSS, RIGHT + OVERHANG, CROSS, STROKE, cmyk_a))
    drop = BOTTOM + DIP
    commands.extend(
        (
            line(LEFT - SPREAD, TOP - RISE, AXIS + TAIL, drop, STROKE, cmyk_b),
            line(RIGHT + SPREAD, TOP - RISE, AXIS - TAIL, drop, STROKE, cmyk_b),
        )
    )
    commands.extend(highlight(x, y, orientation, PRINT_INTERSECTION) for x, y, orientation in crossing_marks())

    if lockup:
        commands.extend(
            (
                line(142, 535, 142, 580, 8, cmyk_a),
                line(178, 535, 178, 580, 8, cmyk_a),
                line(142, 557, 178, 557, 8, cmyk_a),
                line(142, 600, 142, 645, 8, cmyk_b),
                line(178, 600, 178, 645, 8, cmyk_b),
                line(142, 600, 160, 642, 8, cmyk_b),
                line(178, 600, 160, 642, 8, cmyk_b),
                "0 0 0 0 setcmykcolor /Helvetica findfont 34 scalefont setfont",
                "194 108 moveto (UGI) show",
                "194 43 moveto (UNI) show",
            )
        )

    return "\n".join(
        (
            "%!PS-Adobe-3.0 EPSF-3.0",
            f"%%BoundingBox: 0 0 {BOARD} {height}",
            "%%LanguageLevel: 2",
            "%%DocumentData: Clean7Bit",
            "%%Creator: media/brand/hugimuni/build.py",
            "%%EndComments",
            *commands,
            "showpage",
            "%%EOF",
            "",
        )
    )


def _write(path: str, content: str):
    (OUT / path).write_text(content)


if __name__ == "__main__":
    _write(f"hugimuni-{NAME}.svg", screen_svg())
    _write(f"hugimuni-{NAME}-icon.svg", screen_svg(with_ground=True))
    _write(f"hugimuni-{NAME}-flat.svg", flat_svg())
    _write(f"hugimuni-{NAME}.eps", eps())
    _write(f"hugimuni-{NAME}-lockup.eps", eps(lockup=True))
    _write(f"hugimuni-{NAME}-print.svg", print_svg())
    _write(f"hugimuni-{NAME}-print-lockup.svg", print_svg(lockup=True))
    print("wrote canonical amber-sky screen, flat, icon, EPS, and print proof assets")
