#!/usr/bin/env python3
"""The five-slot skeleton — one geometry, every register.

`brr/emotes.py` already fixes the mark's shape: every frame of every emote is
exactly five display cells wide, "so the mark never jitters".  That rule is the
grid.  Slot 1 is ``b``, slot 5 is ``d``, and slots 2-4 are the state.
``brnrd`` is the resting frame; ``b·_·d`` is the same mark awake.

Everything below is derived from that.  The mark is *drawn*, not typeset, which
is also why it can't jitter: `WinkWordmark.svelte` renders those five cells in
a font stack with no guaranteed coverage for ``Я · ˋ ˊ ᵕ ‿``, and a substituted
glyph brings its own width.  Paths have no fallback stack.

Run: python3 media/brand/build.py
"""
from pathlib import Path

OUT = Path(__file__).parent

# ── the grid ────────────────────────────────────────────────────────────────
BOARD = 512
AXIS = BOARD / 2                     # every mirrored pair reflects through this
SLOT = 80                            # five of these, centred on the axis
SLOTS = [AXIS + (i - 2) * SLOT for i in range(5)]   # 96, 176, 256, 336, 416

STAVE_TOP = 92                       # the stone stands taller than the letter
BASELINE = 420
BOWL_TOP = 308   # bowls are true half-circles: BOWL_W == (BASELINE-BOWL_TOP)/2
BOWL_W = 62                          # how far the bowl reaches off its stave
STAVE_INSET = 24                     # the stave sits at its cell's outer edge,
                                     # the way a b's ascender does in its cell
STROKE = 22

STONE = "#0c0906"
MOLTEN = "#ff9a1f"
EMBER = "#ff6a00"
CREAM = "#f2ece1"
RED = "#ff3b30"
CYAN = "#3ad8e6"


CROWN = "none"                       # "none" = bare bars · "branch" =
                                     # Algiz arms · "fork" = the logo's trident


def crown(x: float) -> str:
    """What the stave wears at the top — the one open question in this mark.

    ``fork`` keeps the existing logo's square trident verbatim.  ``branch``
    replaces it with two arms rising off the stem, which is closer to an
    actual rune and stops the pair reading as cutlery at small sizes.
    """
    if CROWN == "none":
        return ""
    if CROWN == "fork":
        arm = 32
        return f"""
    <path d="M {x - arm} {STAVE_TOP + 40} H {x + arm}"/>
    <path d="M {x - arm} {STAVE_TOP + 40} V {STAVE_TOP + 8}"/>
    <path d="M {x + arm} {STAVE_TOP + 40} V {STAVE_TOP + 8}"/>"""
    arm, rise = 38, 54
    return f"""
    <path d="M {x} {STAVE_TOP + rise} L {x - arm} {STAVE_TOP + 4}"/>
    <path d="M {x} {STAVE_TOP + rise} L {x + arm} {STAVE_TOP + 4}"/>"""


def stave(x: float, flip: int) -> str:
    """One standing stone: the ascender of b/d, wearing the logo's crown.

    `flip` is -1 for b (bowl opens right) and +1 for d (bowl opens left), so
    the pair is one shape reflected through AXIS — which is the same mirror
    ``bRnЯd`` performs at the n, and the reason b and d were always the right
    two letters to carry the identity.
    """
    sweep = 1 if flip < 0 else 0     # b bulges right, d bulges left
    ry = (BASELINE - BOWL_TOP) / 2
    return f"""
    <path d="M {x} {STAVE_TOP} V {BASELINE}"/>{crown(x)}
    <path d="M {x - 24} {STAVE_TOP + 104} H {x + 24}"/>
    <path d="M {x - 24} {STAVE_TOP + 144} H {x + 24}"/>
    <path d="M {x} {BOWL_TOP} a {BOWL_W} {ry} 0 0 {sweep} 0 {BASELINE - BOWL_TOP}"/>"""


FACES = {
    # slot 2, slot 3, slot 4 — the state, drawn.  Names are emote handles.
    "rest":     ("dot", "bar", "dot"),      # b·_·d — awake, nothing wrong
    "up":       ("peak", "bar", "peak"),    # b^_^d — the r's own shape, smiling
    "kawaii":   ("peak", "lown", "peak"),   # b^n^d — the word with the n dropped
    "wide":     ("ring", "bar", "ring"),    # bˋoˊd — surprised, caught out
    "flat":     ("dash", "bar", "dash"),    # b-_-d — grinding, unamused
    "grip":     ("dot", "grit", "dot"),     # grip_ — the flake ends this wake
}
EYE_Y = 322
MOUTH_Y = 390
EYE_R = 15


def glyph(kind: str, x: float) -> str:
    if kind == "dot":
        return (f'<circle cx="{x}" cy="{EYE_Y}" r="{EYE_R}" '
                f'fill="url(#molten)" stroke="none"/>')
    if kind == "lown":
        # `b|^n^|d` with the n dropped — his read, and the one that fuses the
        # two frames: the awake face is the resting word with the n lowered
        # and the r-stems taken away.  Nothing is swapped, only moved.
        left, right, top = x - 26, x + 26, MOUTH_Y - 26
        return (f'<path d="M {left} {top + 14} V {MOUTH_Y + 14}"/>'
                f'<path d="M {right} {top + 14} V {MOUTH_Y + 14}"/>'
                f'<path d="M {left} {top + 14} L {x} {top} L {right} {top + 14}"/>')
    if kind == "peak":
        return f'<path d="M {x - 26} {EYE_Y + 24} L {x} {EYE_Y} L {x + 26} {EYE_Y + 24}"/>'
    if kind == "ring":
        return (f'<path d="M {x} {EYE_Y - 22} a 22 22 0 1 0 0.01 0" '
                f'fill="none"/>')
    if kind == "dash":
        return f'<path d="M {x - 20} {EYE_Y} H {x + 20}"/>'
    if kind == "bar":
        return f'<path d="M {x - 48} {MOUTH_Y} H {x + 48}"/>'
    if kind == "grit":
        return (f'<path d="M {x - 48} {MOUTH_Y} H {x + 48}"/>'
                f'<path d="M {x - 48} {MOUTH_Y - 28} H {x + 48}"/>')
    raise ValueError(kind)


def skeleton(face: str = "rest") -> str:
    parts = [stave(SLOTS[0] - STAVE_INSET, -1), stave(SLOTS[4] + STAVE_INSET, +1)]
    for kind, x in zip(FACES[face], SLOTS[1:4]):
        parts.append(glyph(kind, x))
    return "".join(parts)


# ── the resting frame ───────────────────────────────────────────────────────
# Slots 2-4 are the state, and `brnrd` is the state called *at rest* — so the
# same five slots must also spell the name.  Straight strokes, angular joins,
# spurs where a rune would carry one; the middle three sit at x-height so the
# two staves keep the mark's silhouette across both frames.

XTOP = BOWL_TOP                      # x-height — the same line b's bowl starts on


def letter_r(x: float, mirror: bool = False) -> str:
    """A caret with a tail.

    His note: *"r is like ^ in |^_^|"*.  So the letter and the eye are one
    shape — which `WinkWordmark`'s own frame list already assumed: `b^n^d`
    sits in it beside `b-n-d`.  The tail is what makes it an `r` in the word;
    drop the tail and the same peak is the awake frame's eye.
    """
    s = -1 if mirror else 1
    stem = x - s * 12
    return f"""
    <path d="M {stem} {XTOP} V {BASELINE}"/>
    <path d="M {stem} {XTOP + 4} L {x + s * 26} {XTOP - 20}"/>"""


def letter_n(x: float) -> str:
    left, right = x - 25, x + 25
    return f"""
    <path d="M {left} {XTOP} V {BASELINE}"/>
    <path d="M {right} {XTOP + 14} V {BASELINE}"/>
    <path d="M {left} {XTOP + 14} L {x} {XTOP} L {right} {XTOP + 14}"/>"""


def resting() -> str:
    """b r n ᴙ d — the name, in the same five slots as the face.

    The fourth letter is reversed, which is not decoration: it is the same
    mirror ``bRnЯd`` already performs on the site, and it makes the resting
    frame bilaterally symmetric about the identical axis the face uses.
    """
    return "".join([
        stave(SLOTS[0] - STAVE_INSET, -1),
        letter_r(SLOTS[1]),
        letter_n(SLOTS[2]),
        letter_r(SLOTS[3], mirror=True),
        stave(SLOTS[4] + STAVE_INSET, +1),
    ])


STROKE_ATTRS = (f'fill="none" stroke-width="{STROKE}" '
                'stroke-linecap="round" stroke-linejoin="round"')


def stone_svg(face: str = "rest") -> str:
    """Identity at rest: incised, molten, on rock.  Avatar, favicon, print."""
    body = resting() if face == "name" else skeleton(face)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{BOARD}" viewBox="0 0 {BOARD} {BOARD}">
  <title>brnrd — the five-slot mark, stone register</title>
  <defs>
    <linearGradient id="molten" gradientUnits="userSpaceOnUse" x1="0" y1="80" x2="0" y2="430">
      <stop offset="0" stop-color="{MOLTEN}"/>
      <stop offset="1" stop-color="{EMBER}"/>
    </linearGradient>
    <filter id="heat" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="10" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <rect width="{BOARD}" height="{BOARD}" rx="112" fill="{STONE}"/>
  <g {STROKE_ATTRS} stroke="url(#molten)" filter="url(#heat)">{body}
  </g>
</svg>
"""


def aberration_svg(face: str = "rest") -> str:
    """The same object in motion: seen through a lens, not carved on a rock.

    Three passes of one path set — the site's existing boot palette, kept
    exactly (`src/frontend/src/lib/assets/favicon.svg`).
    """
    body = (resting() if face == "name" else skeleton(face)).replace(
        'fill="url(#molten)"', f'fill="{CREAM}"')
    ghost = lambda colour, dx: (
        f'<g {STROKE_ATTRS} stroke="{colour}" opacity="0.55" '
        f'transform="translate({dx},0)">{body}</g>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{BOARD}" height="{BOARD}" viewBox="0 0 {BOARD} {BOARD}">
  <title>brnrd — the five-slot mark, screen register</title>
  <rect width="{BOARD}" height="{BOARD}" rx="112" fill="{STONE}"/>
  {ghost(RED, -7)}
  {ghost(CYAN, 7)}
  <g {STROKE_ATTRS} stroke="{CREAM}">{body}</g>
</svg>
"""


def sheet_svg() -> str:
    """Four states, one skeleton — the argument that this is a system."""
    names = list(FACES)
    w, gap = BOARD, 40
    total = len(names) * w + (len(names) - 1) * gap
    tiles = []
    for i, name in enumerate(names):
        x = i * (w + gap)
        tiles.append(f"""
  <g transform="translate({x},0)">
    <rect width="{w}" height="{w}" rx="112" fill="{STONE}"/>
    <g {STROKE_ATTRS} stroke="url(#molten)" filter="url(#heat)">{skeleton(name)}
    </g>
  </g>""")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{BOARD}" viewBox="0 0 {total} {BOARD}">
  <title>brnrd — one skeleton, four states</title>
  <defs>
    <linearGradient id="molten" gradientUnits="userSpaceOnUse" x1="0" y1="80" x2="0" y2="430">
      <stop offset="0" stop-color="{MOLTEN}"/>
      <stop offset="1" stop-color="{EMBER}"/>
    </linearGradient>
    <filter id="heat" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="10" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>{"".join(tiles)}
</svg>
"""


if __name__ == "__main__":
    import sys
    globals()["CROWN"] = sys.argv[1] if len(sys.argv) > 1 else CROWN
    suffix = "" if CROWN == "none" else f"-{CROWN}"
    (OUT / f"mark-stone{suffix}.svg").write_text(stone_svg("rest"))
    (OUT / f"mark-screen{suffix}.svg").write_text(aberration_svg("rest"))
    (OUT / f"states{suffix}.svg").write_text(sheet_svg())
    (OUT / f"wordmark-stone{suffix}.svg").write_text(stone_svg("name"))
    (OUT / f"wordmark-screen{suffix}.svg").write_text(aberration_svg("name"))
    print(f"wrote crown={CROWN}")
