# brnrd — the mythos (#2158, opening of #2154)

A numpy/OpenCV frame renderer. Every shot is a pure function `(local frame, shot length, global frame) -> float RGB`.
Each act module (`src/m1.py` matter, `src/m2.py` observation, `src/m3.py` signal) returns an edit list of
`(frames, draw)`, and `src/render.py` concatenates them. **[`GRAMMAR.md`](GRAMMAR.md) is the visual language**:
shape families, materials, transition rules and the shot chain.

Needs `ffmpeg` on PATH (`brew install ffmpeg` on macOS). From the repo root:

```bash
python3 -m venv ~/.cache/myth-venv
~/.cache/myth-venv/bin/pip install numpy scipy pillow opencv-python-headless scikit-image
./artifacts/mythos/scripts/fetch_fonts.sh
cd artifacts/mythos/src
MYTH_SCALE=0.35 ~/.cache/myth-venv/bin/python render.py sheet /tmp/sheet.png --every 4 --tile 200
~/.cache/myth-venv/bin/python render.py video ../out/mythos_muted.mp4
cd .. && ~/.cache/myth-venv/bin/python audio/score.py out/score.wav
ffmpeg -i out/mythos_muted.mp4 -i out/score.wav -c:v copy -c:a aac -shortest out/mythos.mp4
```

The first command after `cd` is a low-res review sheet (every 4th frame). The second is the 1080p
muted video (~5 min on 4 cores). Then the score, keyed to the edit list, and the mux. `MYTH_JOBS`
sets the number of workers (default 7); `MYTH_ACTS=m1` renders one act; `--from F --to F` a range.
`cv2` is installed as `opencv-python-headless`. If the fonts are missing, the first shot that sets
type fails with `OSError: cannot open resource`.

| module | what |
|---|---|
| `forms.py` | the shape grammar: drum-mode nodal sets (`FORMS`), their contours, eigenfrequencies |
| `membrane.py` | the grain simulation (cached to `out/cache/`), and the powder-on-skin renderer |
| `optics.py` | the instrument: filmic exposure with halation, tilted macro DOF, jitter, foreground motes, chromatic aberration |
| `stage.py` | the dust volume with god rays, rock, form → screen polylines, the develop pass |
| `core.py` | frame buffer, shading, type, print media (halftone, dither, threshold, misregister, crush) |

`MYTH_ACTS=m1,m2` selects acts. Simulations run once in `render.py` before the workers fork.
