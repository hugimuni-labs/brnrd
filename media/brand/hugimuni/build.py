#!/usr/bin/env python3
"""Generate the HugiMuni H/M identity from one flat region model.

The canonical mark is two opaque letterforms occupying one plane:

    amber = H only
    sky   = M only
    cream = H ∩ M

The flat SVG is the identity source. Screen adds atmosphere around that same
artwork. EPS maps the same topology to process CMYK for physical production.

The lockup follows the same rule rather than dropping to generic typed caps:
its H and M are scaled copies of the authored standalone H and M geometry,
kept separate so the word remains readable. Hugi is amber; Muni is sky.
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

# Lockup material. H and M reuse the mark's actual component geometry at a
# wordmark scale, but they do not overlap. Lowercase continuations stay quiet
# Helvetica for now so the custom initials carry the family resemblance without
# turning the whole word into a novelty alphabet.
LOCKUP_HEIGHT = 560
WORDMARK = "HugiMuni"
WORDMARK_SIZE = 42
WORDMARK_Y = 500
WORDMARK_GLYPH_HEIGHT = 32
WORDMARK_TEXT_WIDTH = 56
WORDMARK_GLYPH_TEXT_GAP = 2
WORDMARK_JOIN_GAP = 6

# Process values are output mappings, not the identity source. Replace these
# with the printer/RIP profile's preferred values when production gives us an
# ICC/profile target.
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


def _h_mask_def(mask_id="hm-h"):
    return f'''<mask id="{mask_id}" maskUnits="userSpaceOnUse" x="0" y="0" width="{BOARD}" height="{BOARD}">
      <rect width="{BOARD}" height="{BOARD}" fill="#000"/>
      {_svg_group(h_components(), '#fff')}
    </mask>'''


def _mark_mask_def(mask_id="hm-mark"):
    return f'''<mask id="{mask_id}" maskUnits="userSpaceOnUse" x="0" y="0" width="{BOARD}" height="{BOARD}">
      <rect width="{BOARD}" height="{BOARD}" fill="#000"/>
      {_svg_group(h_components(), '#fff')}
      {_svg_group(m_components(), '#fff')}
    </mask>'''


def _flat_art(*, ids=False, h_mask="hm-h"):
    """Three-region identity: H-only / M-only / H∩M."""
    h = _svg_group(h_components(), AMBER)
    m = _svg_group(m_components(), SKY)
    overlap = _svg_group(m_components(), INTERSECTION)
    art_id = ' id="hm-flat-art"' if ids else ''
    h_id = ' id="hm-h-only"' if ids else ''
    m_id = ' id="hm-m-only"' if ids else ''
    i_id = ' id="hm-intersection"' if ids else ''
    return f'''<g{art_id}>
      <g{h_id}>{h}</g>
      <g{m_id}>{m}</g>
      <g{i_id} mask="url(#{h_mask})">{overlap}</g>
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
{ground}  {_flat_art(ids=True)}
</svg>
'''


def _screen_body(*, h_mask="hm-h", mark_mask="hm-mark"):
    grain = max(0, min(100, GRAIN)) / 100
    hot = _svg_group(m_components(), '#fffaf1')
    return f'''<g style="isolation:isolate">
    <g filter="url(#hm-bloom)" opacity="{BLOOM_OPACITY}" style="mix-blend-mode:screen">{_flat_art(h_mask=h_mask)}</g>
    {_flat_art(ids=True, h_mask=h_mask)}
    <g mask="url(#{h_mask})" filter="url(#hm-hot)" opacity="{HOT_OPACITY}" style="mix-blend-mode:screen">{hot}</g>
    <g mask="url(#{mark_mask})">
      <rect width="{BOARD}" height="{BOARD}" filter="url(#hm-grain)" opacity="{grain * .34:.3f}" style="mix-blend-mode:overlay"/>
      <rect width="{BOARD}" height="{BOARD}" fill="url(#hm-scanlines)" opacity="{grain * .28:.3f}" style="mix-blend-mode:multiply"/>
    </g>
  </g>'''


def screen_svg(*, with_ground=False):
    """Screen register: the flat master plus light/texture, never new geometry."""
    ground = (
        f'  <rect width="{BOARD}" height="{BOARD}" rx="{GROUND_RX}" fill="{GROUND}"/>\n'
        if with_ground else ''
    )
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
{ground}  {_screen_body()}
</svg>
'''


def _wordmark_metrics():
    """Deterministic layout for custom H + `ugi` + custom M + `uni`."""
    source_left = min(LEFT - OVERHANG, LEFT - SPREAD)
    source_right = max(RIGHT + OVERHANG, RIGHT + SPREAD)
    source_top = min(TOP, TOP - RISE)
    source_bottom = max(BOTTOM, BOTTOM + DIP)
    scale = WORDMARK_GLYPH_HEIGHT / (source_bottom - source_top)
    glyph_width = (source_right - source_left) * scale
    total_width = (
        glyph_width * 2
        + WORDMARK_TEXT_WIDTH * 2
        + WORDMARK_GLYPH_TEXT_GAP * 2
        + WORDMARK_JOIN_GAP
    )
    start_x = AXIS - total_width / 2
    h_x = start_x
    ugi_x = h_x + glyph_width + WORDMARK_GLYPH_TEXT_GAP
    m_x = ugi_x + WORDMARK_TEXT_WIDTH + WORDMARK_JOIN_GAP
    uni_x = m_x + glyph_width + WORDMARK_GLYPH_TEXT_GAP
    return {
        "source_left": source_left,
        "source_bottom": source_bottom,
        "scale": scale,
        "glyph_width": glyph_width,
        "h_x": h_x,
        "ugi_x": ugi_x,
        "m_x": m_x,
        "uni_x": uni_x,
    }


def _wordmark_glyph_svg(components, color, x, metrics):
    scale = metrics["scale"]
    tx = x - metrics["source_left"] * scale
    ty = WORDMARK_Y - metrics["source_bottom"] * scale
    return (
        f'<g transform="translate({tx:.3f} {ty:.3f}) scale({scale:.5f})">'
        f'{_svg_group(components, color)}</g>'
    )


def _wordmark_svg():
    metrics = _wordmark_metrics()
    h = _wordmark_glyph_svg(h_components(), AMBER, metrics["h_x"], metrics)
    m = _wordmark_glyph_svg(m_components(), SKY, metrics["m_x"], metrics)
    return f'''<g id="hugimuni-wordmark">
    <g id="hugimuni-wordmark-h">{h}</g>
    <text x="{metrics['ugi_x']:.3f}" y="{WORDMARK_Y}" fill="{AMBER}" font-family="Helvetica, Arial, sans-serif" font-size="{WORDMARK_SIZE}" font-weight="400" textLength="{WORDMARK_TEXT_WIDTH}" lengthAdjust="spacingAndGlyphs">ugi</text>
    <g id="hugimuni-wordmark-m">{m}</g>
    <text x="{metrics['uni_x']:.3f}" y="{WORDMARK_Y}" fill="{SKY}" font-family="Helvetica, Arial, sans-serif" font-size="{WORDMARK_SIZE}" font-weight="400" textLength="{WORDMARK_TEXT_WIDTH}" lengthAdjust="spacingAndGlyphs">uni</text>
  </g>'''


def flat_lockup_svg(*, with_ground=False):
    """Canonical lockup: shared mark above, authored-colour wordmark below."""
    ground = (
        f'  <rect width="{BOARD}" height="{LOCKUP_HEIGHT}" fill="{GROUND}"/>\n'
        if with_ground else ''
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{LOCKUP_HEIGHT}" viewBox="0 0 {BOARD} {LOCKUP_HEIGHT}">
  <title>HugiMuni — canonical lockup</title>
  <defs>
    {_h_mask_def()}
  </defs>
{ground}  {_flat_art(ids=True)}
  {_wordmark_svg()}
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


def _ps_mark(*, height):
    commands = []
    commands.extend(_ps_stroke(c, PRINT_AMBER, height=height) for c in h_components())
    commands.extend(_ps_stroke(c, PRINT_SKY, height=height) for c in m_components())
    for hc in h_components():
        for mc in m_components():
            commands.append(_ps_intersection(hc, mc, height=height))
    return commands


def _transform_wordmark_components(components, x, metrics):
    s = metrics["scale"]
    source_left = metrics["source_left"]
    source_bottom = metrics["source_bottom"]
    transformed = []
    for x1, y1, x2, y2, width in components:
        transformed.append(_line(
            x + (x1 - source_left) * s,
            WORDMARK_Y - (source_bottom - y1) * s,
            x + (x2 - source_left) * s,
            WORDMARK_Y - (source_bottom - y2) * s,
            width * s,
        ))
    return transformed


def _ps_wordmark(*, height):
    metrics = _wordmark_metrics()
    h = _transform_wordmark_components(h_components(), metrics["h_x"], metrics)
    m = _transform_wordmark_components(m_components(), metrics["m_x"], metrics)
    baseline = height - WORDMARK_Y
    commands = []
    commands.extend(_ps_stroke(c, PRINT_AMBER, height=height) for c in h)
    commands.extend((
        f"{_ps_cmyk(PRINT_AMBER)} setcmykcolor /Helvetica findfont {WORDMARK_SIZE} scalefont setfont",
        f"{metrics['ugi_x']:.3f} {baseline:g} moveto (ugi) show",
    ))
    commands.extend(_ps_stroke(c, PRINT_SKY, height=height) for c in m)
    commands.extend((
        f"{_ps_cmyk(PRINT_SKY)} setcmykcolor /Helvetica findfont {WORDMARK_SIZE} scalefont setfont",
        f"{metrics['uni_x']:.3f} {baseline:g} moveto (uni) show",
    ))
    return commands


def eps(*, lockup=False):
    """CMYK production mapping of the exact flat topology."""
    height = LOCKUP_HEIGHT if lockup else BOARD
    commands = [
        f"{_ps_cmyk(PRINT_BLACK)} setcmykcolor 0 0 {BOARD} {height} rectfill",
        "false setoverprint",
        "1 setlinecap 1 setlinejoin",
        *_ps_mark(height=height),
    ]

    if lockup:
        commands.extend(_ps_wordmark(height=height))

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
    height = LOCKUP_HEIGHT if lockup else BOARD
    base = flat_svg(with_ground=False)
    body = base.split('<defs>', 1)[1].split('</svg>', 1)[0]
    body = '<defs>' + body
    wordmark = f'\n  {_wordmark_svg()}' if lockup else ''
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{height}" viewBox="0 0 {BOARD} {height}">
  <title>HugiMuni — print proof</title>
  <rect width="{BOARD}" height="{height}" fill="#000"/>
  {body}{wordmark}
</svg>
'''


def write_all():
    (OUT / 'hugimuni-amber-sky-flat.svg').write_text(flat_svg())
    (OUT / 'hugimuni-amber-sky-flat-on-dark.svg').write_text(flat_svg(with_ground=True))
    (OUT / 'hugimuni-amber-sky-lockup.svg').write_text(flat_lockup_svg())
    (OUT / 'hugimuni-amber-sky.svg').write_text(screen_svg())
    (OUT / 'hugimuni-amber-sky-icon.svg').write_text(screen_svg(with_ground=True))
    (OUT / 'hugimuni-amber-sky-print.svg').write_text(print_proof_svg())
    (OUT / 'hugimuni-amber-sky-print-lockup.svg').write_text(print_proof_svg(lockup=True))
    (OUT / 'hugimuni-amber-sky.eps').write_text(eps())
    (OUT / 'hugimuni-amber-sky-lockup.eps').write_text(eps(lockup=True))
    print('wrote canonical flat + authored wordmark + screen + CMYK print registers')


if __name__ == '__main__':
    write_all()
