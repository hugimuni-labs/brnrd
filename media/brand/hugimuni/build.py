#!/usr/bin/env python3
"""hugimuni — H and M on shared stems.

H and M already share their skeleton: two verticals.  H spends its middle on a
crossbar, M spends it on a valley.  Superimposed on one pair of stems they are
a single glyph that reads as either letter depending on which stroke your eye
follows — and giving each letter its own hue makes the choice the viewer's.

Where the two cross, the colours are *averaged* rather than stacked: the group
blends in ``screen``, so an overlap is genuinely a third colour and not a
z-order accident.  The shared stems carry both hues offset around a neutral
core, which is the same chromatic-aberration grammar brnrd's screen register
already speaks — the parent and the product rhyme without wearing each other's
mark.

Run: python3 media/brand/hugimuni/build.py
"""
from pathlib import Path

OUT = Path(__file__).parent

BOARD = 512
AXIS = BOARD / 2
LEFT, RIGHT = 152, 360                # the shared stems
TOP, BOTTOM = 156, 356
CROSS = 276                           # H's crossbar
OVERHANG = 20                         # the crossbar runs past both stems
SPREAD = 20                           # M's shoulders sit outside the stems, so
RISE, DIP = 0, 0                     # each leg crosses its stem low, not at
                                      # the very top where nothing reads as woven
STROKE = 28
STEM_STROKE = 40                      # the outer legs carry the silhouette
GHOST = 7                             # aberration offset on the shared stems
GRAIN = 58                            # phosphor texture strength, 0–100
INK = "#080b09"
INTERSECTION = "#eadfca"              # warm phosphor white; tunable on the bench
GROUND_ON = True                      # the contrast backdrop behind the glow —
GROUND = "#050705"                    # flat and grainless (grain belongs to the
GROUND_RX = 64                        # letters); off ⇒ fully transparent mark

PALETTES = {
    # his two proposals, both rendered rather than argued about
    "amber-sky": ("#ff9a1f", "#69c7df"),      # brnrd amber + cyan phosphor
    "coral-turquoise": ("#ff6f61", "#3ec9bd"),
}

# `mix-blend-mode` has to sit on the *drawing* group, not only on the isolating
# parent: a parent alone establishes the group and blends it against the page,
# leaving the children stacked in plain z-order — which renders as "the last
# colour wins" and looks exactly like a design decision rather than a bug.
ATTRS = (f'fill="none" stroke-width="{STROKE}" '
         'stroke-linecap="round" stroke-linejoin="round" '
         'style="mix-blend-mode:screen"')
STEM_ATTRS = (f'fill="none" stroke-width="{STEM_STROKE}" '
              'stroke-linecap="round" stroke-linejoin="round" '
              'style="mix-blend-mode:screen"')

STEMS = f'<path d="M {LEFT} {TOP} V {BOTTOM}"/><path d="M {RIGHT} {TOP} V {BOTTOM}"/>'
# His sketch, not my first nesting of the two: the M is the *larger* letter.
# Its diagonals cross the stems above their tops and its valley dips below
# their feet, and the H's bar runs past both stems.  The letters interpenetrate
# instead of one sitting inside the other — which is the difference between a
# monogram and two letters parked in the same box.
BAR_H = f'<path d="M {LEFT - OVERHANG} {CROSS} H {RIGHT + OVERHANG}"/>'
TAIL = 20                             # how far each leg runs past the other


def vee_m() -> str:
    """Two legs that *cross*, not a V that meets.

    His read, and it is the one that finally answers "the bottom is not
    properly intersected": a V's two strokes touch at a vertex and stop, and
    a vertex is not a crossing. Running each leg past the other puts a real
    intersection at the foot of the mark, so the weave closes at both ends
    instead of only at the top.
    """
    drop = BOTTOM + DIP
    return (f'<path d="M {LEFT - SPREAD} {TOP - RISE} L {AXIS + TAIL} {drop}"/>'
            f'<path d="M {RIGHT + SPREAD} {TOP - RISE} L {AXIS - TAIL} {drop}"/>')


def _attrs(width: float) -> str:
    return (f'fill="none" stroke-width="{width}" '
            'stroke-linecap="round" stroke-linejoin="round" '
            'style="mix-blend-mode:screen"')


def _glyph(scale, a: str, b: str, core: str) -> str:
    """Every stroke of the mark, once — widths through ``scale``, colours
    swapped per pass. The same shape draws the bloom halo, the body, the
    white-hot cores, and the grain mask."""
    return (
        f'<g {_attrs(scale(STEM_STROKE))} stroke="{a}" transform="translate({-GHOST},0)">{STEMS}</g>'
        f'<g {_attrs(scale(STEM_STROKE))} stroke="{b}" transform="translate({GHOST},0)">{STEMS}</g>'
        f'<g {_attrs(scale(max(2, STEM_STROKE - GHOST * 2)))} stroke="{core}">{STEMS}</g>'
        f'<g {_attrs(scale(STROKE))} stroke="{a}">{BAR_H}</g>'
        f'<g {_attrs(scale(STROKE))} stroke="{b}">{vee_m()}</g>'
    )


def svg(name: str) -> str:
    """The emissive render (2026-08-28, from the maintainer's generated
    reference): three passes — bloom halo, saturated body, blurred white-hot
    core — screen-blended in one isolated group so intersections blaze, and
    the grain masked *onto the strokes* (turbulence overlay + scanline
    multiply) so GRAIN modulates the letter light itself. Transparent
    ground: the old rounded ink board read as a grained monitor bezel."""
    a, b = PALETTES[name]
    grain = max(0, min(100, GRAIN)) / 100
    body = _glyph(lambda w: w, a, b, INTERSECTION)
    cores = _glyph(lambda w: max(2, round(w * 0.2)), "#fff6e4", "#eefbff", "#ffffff")
    mask_body = _glyph(lambda w: w, "#fff", "#fff", "#fff")
    ground = (f'  <rect width="{BOARD}" height="{BOARD}" rx="{GROUND_RX}" fill="{GROUND}"/>\n'
              if GROUND_ON else "")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{BOARD}" viewBox="0 0 {BOARD} {BOARD}">
  <title>hugimuni — H and M on shared stems ({name})</title>
  <defs>
    <filter id="hm-bloom" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="9"/>
    </filter>
    <filter id="hm-core" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="1.1"/>
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
    <g filter="url(#hm-bloom)" opacity="0.9">{body}</g>
    {body}
    <g filter="url(#hm-core)" opacity="0.65">{cores}</g>
    <g mask="url(#hm-strokes)">
      <rect width="{BOARD}" height="{BOARD}" filter="url(#hm-grain)" opacity="{grain * .6:.3f}" style="mix-blend-mode:overlay"/>
      <rect width="{BOARD}" height="{BOARD}" fill="url(#hm-scanlines)" opacity="{grain * .55:.3f}" style="mix-blend-mode:multiply"/>
    </g>
  </g>
</svg>
"""


PRINT_CMYK = {
    "amber-sky": ((0.00, 0.47, 0.88, 0.00), (0.56, 0.05, 0.03, 0.00)),
    "coral-turquoise": ((0.00, 0.66, 0.54, 0.00), (0.66, 0.00, 0.24, 0.00)),
}


def eps(name: str, *, lockup: bool = False) -> str:
    """Level-2, process-CMYK EPS for physical production.

    Glow, blend modes, scanlines and grain are screen material. The print
    register keeps the woven strokes, uses authored process values, and
    redraws every stroke with a paper-white core so crossings cannot inherit
    the last-painted colour.
    """
    cmyk_a, cmyk_b = PRINT_CMYK[name]
    height = 690 if lockup else BOARD

    def cmyk(value) -> str:
        return " ".join(f"{channel:.3f}" for channel in value)

    def line(x1, y1, x2, y2, width, color):
        return (f"{cmyk(color)} setcmykcolor {width} setlinewidth "
                f"{x1} {height-y1} moveto {x2} {height-y2} lineto stroke")

    commands = [f"0 0 0 1 setcmykcolor 0 0 {BOARD} {height} rectfill",
                "false setoverprint", "1 setlinecap 1 setlinejoin"]
    for x in (LEFT - GHOST, RIGHT - GHOST):
        commands.append(line(x, TOP, x, BOTTOM, STEM_STROKE, cmyk_a))
    for x in (LEFT + GHOST, RIGHT + GHOST):
        commands.append(line(x, TOP, x, BOTTOM, STEM_STROKE, cmyk_b))
    commands.append(line(LEFT - OVERHANG, CROSS, RIGHT + OVERHANG, CROSS, STROKE, cmyk_a))
    drop = BOTTOM + DIP
    commands.extend((
        line(LEFT - SPREAD, TOP - RISE, AXIS + TAIL, drop, STROKE, cmyk_b),
        line(RIGHT + SPREAD, TOP - RISE, AXIS - TAIL, drop, STROKE, cmyk_b),
    ))
    paper = (0, 0, 0, 0)
    for x in (LEFT, RIGHT):
        commands.append(line(x, TOP, x, BOTTOM, STEM_STROKE - GHOST * 2, paper))
    commands.append(line(LEFT - OVERHANG, CROSS, RIGHT + OVERHANG, CROSS, 5.5, paper))
    commands.extend((
        line(LEFT - SPREAD, TOP - RISE, AXIS + TAIL, drop, 5.5, paper),
        line(RIGHT + SPREAD, TOP - RISE, AXIS - TAIL, drop, 5.5, paper),
    ))
    if lockup:
        commands.extend((
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
        ))
    return "\n".join((
        "%!PS-Adobe-3.0 EPSF-3.0", f"%%BoundingBox: 0 0 {BOARD} {height}",
        "%%LanguageLevel: 2", "%%DocumentData: Clean7Bit",
        "%%Creator: media/brand/hugimuni/build.py", "%%EndComments",
        *commands, "showpage", "%%EOF", "",
    ))


def print_svg(name: str, *, lockup: bool = False) -> str:
    """Portable visual proof of the flat EPS register."""
    a, b = PALETTES[name]
    height = 690 if lockup else BOARD
    white = "#ffffff"
    wordmark = ""
    if lockup:
        wordmark = f"""
  <g fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-width="8">
    <path stroke="{a}" d="M142 535V580M178 535V580M142 557H178"/>
    <path stroke="{b}" d="M142 600V645M178 600V645M142 600L160 642L178 600"/>
  </g>
  <g fill="{white}" font-family="Helvetica, Arial, sans-serif" font-size="34">
    <text x="194" y="582">UGI</text><text x="194" y="647">UNI</text>
  </g>"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{height}" viewBox="0 0 {BOARD} {height}">
  <title>hugimuni print register ({name})</title>
  <rect width="{BOARD}" height="{height}" fill="#000"/>
  <g fill="none" stroke-linecap="round" stroke-linejoin="round">
    <g stroke="{a}" stroke-width="{STEM_STROKE}" transform="translate({-GHOST},0)">{STEMS}</g>
    <g stroke="{b}" stroke-width="{STEM_STROKE}" transform="translate({GHOST},0)">{STEMS}</g>
    <g stroke="{a}" stroke-width="{STROKE}">{BAR_H}</g>
    <g stroke="{b}" stroke-width="{STROKE}">{vee_m()}</g>
    <g stroke="{white}" stroke-width="{STEM_STROKE - GHOST * 2}">{STEMS}</g>
    <g stroke="{white}" stroke-width="5.5">{BAR_H}{vee_m()}</g>
  </g>{wordmark}
</svg>
"""


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        globals()["SPREAD"] = int(sys.argv[1])
    for name in PALETTES:
        (OUT / f"hugimuni-{name}.svg").write_text(svg(name))
        (OUT / f"hugimuni-{name}.eps").write_text(eps(name))
        (OUT / f"hugimuni-{name}-lockup.eps").write_text(eps(name, lockup=True))
        (OUT / f"hugimuni-{name}-print.svg").write_text(print_svg(name))
        (OUT / f"hugimuni-{name}-print-lockup.svg").write_text(print_svg(name, lockup=True))
    print("wrote screen SVG + print EPS/SVG mark and lockup variants")
