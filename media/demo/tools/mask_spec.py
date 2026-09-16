"""Where the phone number sits, in the phone's own coordinate space.

One source of truth for two consumers:
  * `remotion/src/cut/Phone.tsx` — the mask inside the composition, so any
    future render is masked at the source (mirrors the tables below by hand;
    keep the numbers in step).
  * `mask_render.py` — the post-pass that masks the *already rendered* mp4s,
    because the footage `v5.ts` reads (c04b_notify, c05_reply, c05c_folded and
    most of the rest) is no longer on this machine and cannot be re-cut.

Coordinates are the phone frame: 720 x 1560, origin top-left, exactly what
`Footage` draws into — so a rect here tracks every camera zoom and pan for
free, in both the composition and the post-pass.

Windows are given in *source seconds* (seconds from the start of the
maintainer's original screen recording), which is what a `Seg` already carries
as `clipStart + from + elapsed * rate`. Saying it that way makes the spec
independent of the cut: CutV5, CutShortV6 and any later trim all resolve the
same windows.
"""

# --- rects, phone coordinates ------------------------------------------------
BANNER_TITLE = (126, 92, 406, 138)     # iOS notification banner, the title line
CHAT_HEADER = (186, 102, 522, 174)     # WhatsApp chat nav bar, the contact title
OPEN_SWEEP = (110, 84, 720, 260)       # the app-open animation: the title sweeps
# The banner does not fade where it stands: as the app opens it slides *up* and
# off the top of the screen, and the title crosses y=84 on its way out — read
# off frames 1017 and 1993, where it escaped a rect that stopped at the banner.
BANNER_EXIT = (100, 0, 480, 152)
LIST_SELF = (0, 466, 540, 536)         # chat list, his own row
LIST_THIRD_A = (0, 1076, 540, 1150)    # chat list, +375 29 872-95-21
LIST_THIRD_B = (0, 1392, 540, 1456)    # chat list, +375 29 663-97-48
# The window opens at 37.88 rather than at the frame the list looks drawn:
# on frame 338 the rows render with the avatars still grey placeholders, which
# is what fooled a probe that looked for a dark avatar — and the two numbers
# are fully legible on it.
# the list rows run to x=0 on purpose: iOS's push animation slides the list
# left under the incoming chat, and a rect that started at the text would let
# the first glyphs out on the left (caught on frame 352).

# --- windows: clip -> [(t0, t1, rect), ...] in source seconds ----------------
# `None` for t0/t1 means "the whole clip" (used for the freeze stills, whose
# source time is a single instant).
WINDOWS = {
    # the ask, 13:07-13:08Z — home screen, chat list, then the chat
    "c02_ask.mp4": [
        (37.88, 39.20, LIST_SELF),
        (37.88, 39.20, LIST_THIRD_A),
        (37.88, 39.20, LIST_THIRD_B),
        (38.80, 39.62, OPEN_SWEEP),
        (39.40, 999.0, CHAT_HEADER),
    ],
    "c03_wait.mp4": [(None, None, CHAT_HEADER)],

    # the reply, 13:11Z — banner, the app opens, the chat
    "s_notify.png": [(None, None, BANNER_TITLE)],
    "c04b_notify.mp4": [
        (263.55, 263.98, BANNER_TITLE),
        (263.78, 264.02, BANNER_EXIT),
        (263.80, 264.38, OPEN_SWEEP),
        (264.18, 999.0, CHAT_HEADER),
    ],
    "c05_reply.mp4": [(None, None, CHAT_HEADER)],
    "s_sent.png": [(None, None, CHAT_HEADER)],

    # the steer folded, 13:14Z — banner, the app opens, the chat
    "s_folded.png": [(None, None, BANNER_TITLE)],
    "c05c_folded.mp4": [
        (428.55, 429.95, BANNER_TITLE),
        (429.72, 430.02, BANNER_EXIT),
        (429.82, 430.50, OPEN_SWEEP),
        (430.30, 999.0, CHAT_HEADER),
    ],
}

# Stills that carry the number in the committed asset itself and are masked in
# place by `mask_stills.py` (the render predates that, so the post-pass still
# covers their frames).
STILL_RECTS = {
    "s_notify.png": [BANNER_TITLE],
    "s_folded.png": [BANNER_TITLE],
    "s_sent.png": [CHAT_HEADER],
}

# the phosphor style: the cut's own background, a thin amber edge
FILL = (10, 13, 15, 255)   # opaque: at 96% the digits still ghost through
EDGE = (255, 179, 71, 70)


def rects_for(clip: str, src_t: float):
    """The phone-space rects to cover for this clip at this source second.

    Overlapping rects are merged into their bounding box — the windows overlap
    on purpose (a hand-off between two surfaces is where a number escapes), and
    without the merge the inner rect's edge draws a stray outline inside the
    outer one, which reads as a UI element rather than a mask.
    """
    out = []
    for t0, t1, rect in WINDOWS.get(clip, ()):
        if t0 is None or (t0 <= src_t <= t1):
            out.append(list(rect))
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                    out[i] = [min(a[0], b[0]), min(a[1], b[1]),
                              max(a[2], b[2]), max(a[3], b[3])]
                    out.pop(j)
                    changed = True
                    break
            if changed:
                break
    return [tuple(r) for r in out]
