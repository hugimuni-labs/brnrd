"""Build the contact sheets that the mask was accepted on.

    python3 verify_run.py CutV5      masked.mp4 outdir
    python3 verify_run.py CutShortV6 masked.mp4 outdir

Three passes, because the risk is not uniform:
  * `dense`  — every 2nd frame through the app-open animations, where the
    number is in motion and a static rect is a guess. Both leaks this mask
    had were found here and nowhere else.
  * `hz`     — one frame a second across every masked span plus a second of
    margin: the steady states, at the sampling rate the brief asked for.
  * `list`   — the chat-list band, where two more numbers live.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cut_timing as T  # noqa: E402
import verify_sheets as V  # noqa: E402

TOP = (40, 340)      # banner title, chat nav bar, the sweep
LIST = (430, 1480)   # the chat-list rows

DENSE = {
    "CutV5": [(325, 382), (995, 1072), (1985, 2052)],
    "CutShortV6": [(325, 382), (830, 900), (1275, 1380)],
}
LIST_WINDOW = {"CutV5": (326, 372), "CutShortV6": (326, 372)}


def main():
    comp, src, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    sl = {"CutV5": T.SCENES_V5, "CutShortV6": T.SCENES_SHORT_V6}[comp]
    os.makedirs(outdir, exist_ok=True)
    tag = comp.lower()

    dense = [f for a, b in DENSE[comp] for f in range(a, b, 2)]
    print(V.sheet(src, sl, dense, TOP, 460, 5, f"{outdir}/{tag}-dense", 50))

    hz = [f for a, b in V.masked_spans(sl, pad=60) for f in range(a, b + 1, 60)]
    print(V.sheet(src, sl, hz, TOP, 460, 5, f"{outdir}/{tag}-hz", 50))

    a, b = LIST_WINDOW[comp]
    print(V.sheet(src, sl, list(range(a, b, 3)), LIST, 420, 4, f"{outdir}/{tag}-list", 16))


if __name__ == "__main__":
    main()
