#!/usr/bin/env python3
"""Generate the HugiMuni H/M identity from one flat region model.

The canonical mark is not neon tubing. It is two opaque letterforms occupying
one plane:

    amber = H only
    sky   = M only
    cream = H ∩ M

The flat SVG is the master artwork. Screen adds atmosphere around that same
artwork. EPS maps the same topology to process CMYK for physical production.
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
GHOST = 7

AMBER = "#ff9a1f"
SKY = "#69c7df"
INTERSECTION = "#f0e3cf"
GROUND = "#050705"
GROUND_RX = 64

# Screen material only. The flat mark remains the thing being branded.
BLOOM_BLUR = 7
BLOOM_OPACITY = 0.42
HOT_BLUR = 1.4
HOT_OPACITY = 0.28
GRAIN = 22

# Process values are output mappings, not the identity source. Replace these
# with the printer/RIP profile's preferred values when the production vendor
# gives us an ICC/profile target.
PRINT_AMBER = (0.00, 0.47, 0.88, 0.00)
PRINT_SKY = (0.56, 0.05, 0.03, 0.00)
PRINT_INTERSECTION = (0.03, 0.06, 0.15, 0.00)
PRINT_BLACK = (0.00, 0.00, 0.00, 1.00)


def _line(x1, y1, x2, y2, width):
    return (x1, y1, x2, y2, width)


def h_components():
    """H as three independent stroked vector components."""
    return [
        _line(LEFT - GHOST, TOP, LEFT - GHOST, BOTTOM, STEM_STROKE),
        _line(RIGHT - GHOST, TOP, RIGHT - GHOST, BOTTOM, STEM_STROKE),
        _line(LEFT - OVERHANG, CROSS, RIGHT + OVERHANG, CROSS, STROKE),
    ]


def m_components():
    """M as four components: displaced stems + the two crossing diagonals."""
    drop = BOTTOM + DIP
    return [
        _line(LEFT + GHOST, TOP, LEFT + GHOST, BOTTOM, STEM_STROKE),
        _line(RIGHT + GHOST, TOP, RIGHT + GHOST, BOTTOM, STEM_STROKE),
        _line(LEFT - SPREAD, TOP - RISE, AXIS + TAIL, drop, STROKE),
        _line(RIGHT + SPREAD, TOP - RISE, AXIS - TAIL, drop, STROKE),
    ]


def _svg_component(component, color):
    x1, y1, x2, y2, width = component
    return (
        f'<path d="M {x1:g} {y1:g} L {x2:g} {y2:g}" '
        f'stroke="{color}" stroke-width="{width:g}" '
        'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
    )


def _svg_group(components, color):
    return "".join(_svg_component(c, color) for c in components)


def _h_mask_def():
    # A luminance mask is deliberately used here rather than painted fake
    # highlights. It expresses the actual boolean rule H ∩ M while preserving
    # the authored rounded stroke geometry.
    return f'''<mask id="hm-h" maskUnits="userSpaceOnUse" x="0" y="0" width="{BOARD}" height="{BOARD}">
      <rect width="{BOARD}" height="{BOARD}" fill="#000"/>
      {_svg_group(h_components(), '#fff')}
    </mask>'''


def _mark_mask_def():
    return f'''<mask id="hm-mark" maskUnits="userSpaceOnUse" x="0" y="0" width="{BOARD}" height="{BOARD}">
      <rect width="{BOARD}" height="{BOARD}" fill="#000"/>
      {_svg_group(h_components(), '#fff')}
      {_svg_group(m_components(), '#fff')}
    </mask>'''


def _flat_art():
    """Three-region identity: H-only / M-only / H∩M."""
    h = _svg_group(h_components(), AMBER)
    m = _svg_group(m_components(), SKY)
    overlap = _svg_group(m_components(), INTERSECTION)
    return f'''<g id="hm-flat-art">
      <g id="hm-h-only">{h}</g>
      <g id="hm-m-only">{m}</g>
      <g id="hm-intersection" mask="url(#hm-h)">{overlap}</g>
    </g>'''


def flat_svg(*, with_ground=False):
    ground = (
        f'  <rect width="{BOARD}" height="{BOARD}" rx="{GROUND_RX}" fill="{GROUND}"/>\n'
        if with_ground else ''
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{BOARD}" viewBox="0 0 {BOARD} {BOARD}">
  <title>HugiMuni — canonical flat H/M mark</title>
  <defs>
    {_h_mask_def()}
  </defs>
{ground}  {_flat_art()}
</svg>
'''


def screen_svg(*, with_ground=False):
    """Screen register: the flat master plus light/texture, never new geometry."""
    ground = (
        f'  <rect width="{BOARD}" height="{BOARD}" rx="{GROUND_RX}" fill="{GROUND}"/>\n'
        if with_ground else ''
    )
    grain = max(0, min(100, GRAIN)) / 100
    # A separate white-hot overlap pass is allowed on screen because it is a
    # material treatment of the already-defined intersection region.
    hot = _svg_group(m_components(), '#fffaf1')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{BOARD}" viewBox="0 0 {BOARD} {BOARD}">
  <title>HugiMuni — emissive screen register</title>
  <defs>
    {_h_mask_def()}
    {_mark_mask_def()}
    <filter id="hm-bloom" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="{BLOOM_BLUR}"/>
    </filter>
    <filter id="hm-hot" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="{HOT_BLUR}"/>
    </filter>
    <filter id="hm-grain" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="0.72" numOctaves="2" seed="23"/>
      <feColorMatrix type="saturate" values="0"/>
    </filter>
    <pattern id="hm-scanlines" width="4" height="4" patternUnits="userSpaceOnUse">
      <path d="M0 3.5H4" stroke="#000" stroke-width="0.55" opacity="0.42"/>
    </pattern>
  </defs>
{ground}  <g style="isolation:isolate">
    <g filter="url(#hm-bloom)" opacity="{BLOOM_OPACITY}" style="mix-blend-mode:screen">{_flat_art()}</g>
    {_flat_art()}
    <g mask="url(#hm-h)" filter="url(#hm-hot)" opacity="{HOT_OPACITY}" style="mix-blend-mode:screen">{hot}</g>
    <g mask="url(#hm-mark)">
      <rect width="{BOARD}" height="{BOARD}" filter="url(#hm-grain)" opacity="{grain * .34:.3f}" style="mix-blend-mode:overlay"/>
      <rect width="{BOARD}" height="{BOARD}" fill="url(#hm-scanlines)" opacity="{grain * .28:.3f}" style="mix-blend-mode:multiply"/>
    </g>
  </g>
</svg>
'''


def _ps_cmyk(value):
    return " ".join(f"{channel:.3f}" for channel in value)


def _ps_path(component, *, height):
    x1, y1, x2, y2, _ = component
    return f"{x1:g} {height-y1:g} moveto {x2:g} {height-y2:g} lineto"


def _ps_stroke(component, color, *, height):
    *_, width = component
    return (
        f"{_ps_cmyk(color)} setcmykcolor {width:g} setlinewidth newpath "
        f"{_ps_path(component, height=height)} stroke"
    )


def _ps_intersection(hc, mc, *, height):
    """Paint exactly one pairwise H∩M region using strokepath clipping."""
    *_, hw = hc
    *_, mw = mc
    return "\n".join((
        "gsave",
        f"{hw:g} setlinewidth newpath {_ps_path(hc, height=height)} strokepath clip newpath",
        f"{_ps_cmyk(PRINT_INTERSECTION)} setcmykcolor {mw:g} setlinewidth newpath {_ps_path(mc, height=height)} stroke",
        "grestore",
    ))


def eps(*, lockup=False):
    """CMYK production mapping of the exact three-region flat topology."""
    height = 690 if lockup else BOARD
    commands = [
        f"{_ps_cmyk(PRINT_BLACK)} setcmykcolor 0 0 {BOARD} {height} rectfill",
        "false setoverprint",
        "1 setlinecap 1 setlinejoin",
    ]
    commands.extend(_ps_stroke(c, PRINT_AMBER, height=height) for c in h_components())
    commands.extend(_ps_stroke(c, PRINT_SKY, height=height) for c in m_components())
    for hc in h_components():
        for mc in m_components():
            commands.append(_ps_intersection(hc, mc, height=height))

    if lockup:
        # Preserve the existing two-row lockup grammar: an authored vector H/M
        # prefix followed by UGI / UNI. Type can be outlined at imposition.
        lock_h = [
            _line(142, 535, 142, 580, 8),
            _line(178, 535, 178, 580, 8),
            _line(142, 557, 178, 557, 8),
        ]
        lock_m = [
            _line(142, 600, 142, 645, 8),
            _line(178, 600, 178, 645, 8),
            _line(142, 600, 160, 642, 8),
            _line(178, 600, 160, 642, 8),
        ]
        commands.extend(_ps_stroke(c, PRINT_AMBER, height=height) for c in lock_h)
        commands.extend(_ps_stroke(c, PRINT_SKY, height=height) for c in lock_m)
        commands.extend((
            f"{_ps_cmyk(PRINT_INTERSECTION)} setcmykcolor /Helvetica findfont 34 scalefont setfont",
            "194 108 moveto (UGI) show",
            "194 43 moveto (UNI) show",
        ))

    return "\n".join((
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
    ))


def print_proof_svg(*, lockup=False):
    height = 690 if lockup else BOARD
    base = flat_svg(with_ground=False)
    # Pull only defs+art body out of the flat document for the proof.
    body = base.split('<defs>', 1)[1].split('</svg>', 1)[0]
    body = '<defs>' + body
    wordmark = ''
    if lockup:
        wordmark = f'''\n  <g fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-width="8">
    <path stroke="{AMBER}" d="M142 535V580M178 535V580M142 557H178"/>
    <path stroke="{SKY}" d="M142 600V645M178 600V645M142 600L160 642L178 600"/>
  </g>
  <g fill="{INTERSECTION}" font-family="Helvetica, Arial, sans-serif" font-size="34">
    <text x="194" y="582">UGI</text><text x="194" y="647">UNI</text>
  </g>'''
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{height}" viewBox="0 0 {BOARD} {height}">
  <title>HugiMuni — print proof</title>
  <rect width="{BOARD}" height="{height}" fill="#000"/>
  {body}{wordmark}
</svg>
'''


def write_all():
    (OUT / 'hugimuni-amber-sky-flat.svg').write_text(flat_svg())
    (OUT / 'hugimuni-amber-sky-flat-on-dark.svg').write_text(flat_svg(with_ground=True))
    (OUT / 'hugimuni-amber-sky.svg').write_text(screen_svg())
    (OUT / 'hugimuni-amber-sky-icon.svg').write_text(screen_svg(with_ground=True))
    (OUT / 'hugimuni-amber-sky-print.svg').write_text(print_proof_svg())
    (OUT / 'hugimuni-amber-sky-print-lockup.svg').write_text(print_proof_svg(lockup=True))
    (OUT / 'hugimuni-amber-sky.eps').write_text(eps())
    (OUT / 'hugimuni-amber-sky-lockup.eps').write_text(eps(lockup=True))
    print('wrote canonical flat + screen + CMYK print registers')


if __name__ == '__main__':
    write_all()
