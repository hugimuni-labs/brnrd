# brnrd — the mythos film (#2154)

numpy/OpenCV/PIL frame renderer. Every shot is a pure function `(local frame, shot length, global frame) -> float RGB`;
each act module (`src/act*.py`) returns an edit list of `(frames, draw)`; `src/render.py` concatenates them.

    python3 -m venv ~/.cache/myth-venv && ~/.cache/myth-venv/bin/pip install numpy scipy pillow opencv-python-headless
    ./scripts/fetch_fonts.sh
    cd src && MYTH_SCALE=0.5 python render.py sheet /tmp/sheet.png --every 4     # review sheet, half-res
    cd src && python render.py video ../out/v.mp4                                  # full res, muted
    python audio/score.py out/score_raw.wav                                         # score keyed to the edit list

`MYTH_ACTS=act1,act2` selects acts. Grain, vignette, bloom, god rays, heightfield shading and the print media
(halftone, dither, threshold, misregister, crush) live in `src/core.py`.
