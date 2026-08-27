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


def eps(name: str, *, lockup: bool = False) -> str:
    """Level-2 EPS: vector strokes, scanlines, grain, and standard PS type."""
    a, b = PALETTES[name]
    height = 640 if lockup else BOARD

    def rgb(value: str) -> str:
        return " ".join(f"{int(value[i:i + 2], 16) / 255:.4f}" for i in (1, 3, 5))

    def line(x1, y1, x2, y2, width, color):
        return (f"{rgb(color)} setrgbcolor {width} setlinewidth "
                f"{x1} {height-y1} moveto {x2} {height-y2} lineto stroke")

    commands = [f"{rgb(INK)} setrgbcolor 0 0 {BOARD} {height} rectfill",
                "1 setlinecap 1 setlinejoin"]
    for x in (LEFT - GHOST, RIGHT - GHOST):
        commands.append(line(x, TOP, x, BOTTOM, STEM_STROKE, a))
    for x in (LEFT + GHOST, RIGHT + GHOST):
        commands.append(line(x, TOP, x, BOTTOM, STEM_STROKE, b))
    commands.append(line(LEFT - OVERHANG, CROSS, RIGHT + OVERHANG, CROSS, STROKE, a))
    drop = BOTTOM + DIP
    commands.extend((
        line(LEFT - SPREAD, TOP - RISE, AXIS + TAIL, drop, STROKE, b),
        line(RIGHT + SPREAD, TOP - RISE, AXIS - TAIL, drop, STROKE, b),
    ))
    for x in (LEFT, RIGHT):
        commands.append(line(x, TOP, x, BOTTOM, STEM_STROKE - GHOST * 2, INTERSECTION))
    # Fine vector scanlines and deterministic phosphor flecks. The flecks
    # sample points *along the strokes* (2026-08-28 — grain belongs to the
    # letter light, not the field around it), jittered across each stroke's
    # own width; print rhymes with the screen render's stroke-masked grain.
    # This is deliberately not a bitmap texture hidden inside an EPS wrapper.
    for y in range(112, 405, 6):
        commands.append(line(72, y, 440, y, .35, "#29402f"))
    drop = BOTTOM + DIP
    segments = (  # (x1, y1, x2, y2, stroke width)
        (LEFT, TOP, LEFT, BOTTOM, STEM_STROKE),
        (RIGHT, TOP, RIGHT, BOTTOM, STEM_STROKE),
        (LEFT - OVERHANG, CROSS, RIGHT + OVERHANG, CROSS, STROKE),
        (LEFT - SPREAD, TOP - RISE, AXIS + TAIL, drop, STROKE),
        (RIGHT + SPREAD, TOP - RISE, AXIS - TAIL, drop, STROKE),
    )
    flecks = round(43 + max(0, min(100, GRAIN)) * 4.4)
    for i in range(flecks):
        x1, y1, x2, y2, width = segments[i % len(segments)]
        t = ((i * 37) % 97) / 97
        # jitter across the stroke, never past its edge
        across = (((i * 13) % 9) - 4) / 4 * (width / 2 - 1)
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        nx, ny = -dy / length, dx / length  # unit normal
        x = x1 + dx * t + nx * across
        y = y1 + dy * t + ny * across
        radius = .45 + (i % 4) * .22
        grain_tone = "#ffffff" if i % 7 == 0 else INTERSECTION
        commands.append(f"{rgb(grain_tone)} setrgbcolor newpath {x:.1f} {height-y:.1f} {radius:.2f} 0 360 arc fill")
    if lockup:
        commands.extend((
            f"{rgb(INTERSECTION)} setrgbcolor /Helvetica-Bold findfont 58 scalefont setfont",
            f"(HugiMuni) dup stringwidth pop 2 div neg {BOARD/2} add 130 moveto show",
        ))
    return "\n".join((
        "%!PS-Adobe-3.0 EPSF-3.0", f"%%BoundingBox: 0 0 {BOARD} {height}",
        "%%LanguageLevel: 2", "%%DocumentData: Clean7Bit",
        "%%Creator: media/brand/hugimuni/build.py", "%%EndComments",
        *commands, "showpage", "%%EOF", "",
    ))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        globals()["SPREAD"] = int(sys.argv[1])
    for name in PALETTES:
        (OUT / f"hugimuni-{name}.svg").write_text(svg(name))
        (OUT / f"hugimuni-{name}.eps").write_text(eps(name))
        (OUT / f"hugimuni-{name}-lockup.eps").write_text(eps(name, lockup=True))
    print("wrote SVG mark + EPS mark/lockup variants")
