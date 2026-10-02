# glyph physics

A mythos-opening study for the manifesto film (#2158). Its job is to tie
physics to meaning before the next task builds a motion language on top.

Every recurring form in the film is a glyph *found* by a physical
process. Electrical breakdown finds `r`. Field lines between two poles find
`n`, and the same field read from below is `u`. A membrane's nodal set finds
`b`, and the same membrane under parity is `d`. A supersonic wake is `V`, and
reversing it gives `^`. [`LEXICON.md`](LEXICON.md) is the deliverable that
matters: etymologies, operations between glyphs, materials and motion rules.

The film (48 s, 1080p24, diegetic score; `media/video/glyph-physics/`) is that lexicon in motion. Physics
shots tremble, intercut by 1–4-frame inserts, flashes, negatives,
magnification jumps and shape-matching ripple cuts. It ends on `brnrd`, each
letter found by its own law, keyed out as Morse on a wire.

## Layout

```
src/glyphs.py      skeletons (no fonts), families, LEXICON, SYMMETRIES
src/phys/          eight generators: chladni, breakdown, field, rd, holo, swarm, mach, press
src/optics.py      the instrument: bloom, halation, CA, grain, jitter; Glass (reticles, labels)
src/shots.py       shots: generators that step physics per frame, yield developed frames
src/film.py        the edit (EDL), parallel render, assembly, contact sheets
src/atlas.py       the lexicon plate: every glyph × every physics
audio/score.py     score derived from the EDL
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./render.sh
```

You also need `ffmpeg` on PATH. A full render takes about 15 min on 4 cores, and
the outputs land in `out/` (gitignored).

`GP_OUT=/some/dir` relocates the output. `python3 src/film.py render crack chl`
re-renders single shots, but delete `out/frames/<shot>` first because frames
are cached.
