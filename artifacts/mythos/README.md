# mythos

The opening of the manifesto film (#2158), rebuilt around a causal
cosmology. About 32 s, 1080p24, with a diegetic score. It runs from a random
potential collapsing into filaments, through a lensed disk, coronal arches,
a crystal seam and a lightning strike across a membrane. Vibration then
shakes the membrane into near-forms around the scar the strike left. An
instrument observes the result and captures one form, which is pressed, laid
out as a circuit, and woven into a thread. The film ends on the first pulse
transmitted down a wire.

No glyph is ever an input. The generators know forces, frequencies and seeds.
`observe.py` finds the forms afterwards, and `world.py` passes each act's
output to the next. [`CAUSALITY.md`](CAUSALITY.md) is the treatment: the arc,
the operator grammar, the cut grammar, what was taken from #2171 and what this
pass could not verify.

## Layout

```
src/lab/         generators, glyph-free: web, disk, plasma, breakdown, membrane,
                 develop, relief, weave
src/observe.py   generate → observe → classify → select: skeleton topology,
                 enclosure pockets, composites, capture, Douglas–Peucker, manhattan
src/world.py     the causal spine: discharge selection, the plate simulation,
                 the found form, the die, the layout
src/optics.py    the instrument: lens + emulsion, and the observation
                 modalities (defocus, slit, occulter, polariser, raster, film edge)
src/shots/       cosmos.py · plate.py · inherit.py
src/film.py      the EDL, parallel render, operator-aligned inserts, assembly
audio/score.py   the score, derived from the EDL and from the plate's drive
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./render.sh
```

You also need `ffmpeg` on PATH. A full render takes about 20 min on 4 cores.
Outputs land in `out/`, which is gitignored: `mythos.mp4` (web weight),
`mythos-master.mp4`, `contact-4.jpg`, and `notebook.jpg`, which shows what
selection chose and what the observer found. Renders stay out of this repo
(see the 2026-10-02 media move to `hugimuni-labs/animations`).

`MYTHOS_OUT=/some/dir` relocates the output. `python3 src/film.py render plate`
re-renders one shot. Frames are cached, so delete `out/frames/<shot>` first.
After changing a generator upstream of the plate, also delete `out/state/`.
