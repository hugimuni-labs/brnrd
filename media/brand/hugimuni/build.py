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
CROSS = 268                           # H's crossbar
OVERHANG = 34                         # the crossbar runs past both stems
SPREAD = 50                           # M's shoulders sit outside the stems, so
RISE, DIP = 0, 10                    # each leg crosses its stem low, not at
                                      # the very top where nothing reads as woven
STROKE = 30
GHOST = 5                             # aberration offset on the shared stems
INK = "#0c0906"

PALETTES = {
    # his two proposals, both rendered rather than argued about
    "amber-sky": ("#ff9a1f", "#8fb6cc"),      # brnrd's own amber + a cold sky
    "coral-turquoise": ("#ff6f61", "#3ec9bd"),
}

# `mix-blend-mode` has to sit on the *drawing* group, not only on the isolating
# parent: a parent alone establishes the group and blends it against the page,
# leaving the children stacked in plain z-order — which renders as "the last
# colour wins" and looks exactly like a design decision rather than a bug.
ATTRS = (f'fill="none" stroke-width="{STROKE}" '
         'stroke-linecap="round" stroke-linejoin="round" '
         'style="mix-blend-mode:screen"')

STEMS = f'<path d="M {LEFT} {TOP} V {BOTTOM}"/><path d="M {RIGHT} {TOP} V {BOTTOM}"/>'
# His sketch, not my first nesting of the two: the M is the *larger* letter.
# Its diagonals cross the stems above their tops and its valley dips below
# their feet, and the H's bar runs past both stems.  The letters interpenetrate
# instead of one sitting inside the other — which is the difference between a
# monogram and two letters parked in the same box.
BAR_H = f'<path d="M {LEFT - OVERHANG} {CROSS} H {RIGHT + OVERHANG}"/>'
def vee_m() -> str:
    return (f'<path d="M {LEFT - SPREAD} {TOP - RISE} '
            f'L {AXIS} {BOTTOM + DIP} L {RIGHT + SPREAD} {TOP - RISE}"/>')


def svg(name: str) -> str:
    a, b = PALETTES[name]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{BOARD}" viewBox="0 0 {BOARD} {BOARD}">
  <title>hugimuni — H and M on shared stems ({name})</title>
  <rect width="{BOARD}" height="{BOARD}" rx="112" fill="{INK}"/>
  <g style="mix-blend-mode:screen">
    <g {ATTRS} stroke="{a}" transform="translate({-GHOST},0)">{STEMS}</g>
    <g {ATTRS} stroke="{b}" transform="translate({GHOST},0)">{STEMS}</g>
    <g {ATTRS} stroke="{a}">{BAR_H}</g>
    <g {ATTRS} stroke="{b}">{vee_m()}</g>
  </g>
</svg>
"""


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        globals()["SPREAD"] = int(sys.argv[1])
    for name in PALETTES:
        (OUT / f"hugimuni-{name}.svg").write_text(svg(name))
    print("wrote", " ".join(f"hugimuni-{n}.svg" for n in PALETTES))
