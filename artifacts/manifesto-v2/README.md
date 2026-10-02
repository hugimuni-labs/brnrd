# brnrd — manifesto film v2 (the MIDA-grammar cut)

40.0 s · 1920×1080 · 30 fps · H.264 + AAC · ≈ −15.7 LUFS, peak −2.2 dBFS. Cut from `manifesto-opus`, re-edited.

**How it is built.**
- `src/cut.ts` is a frame-exact edit list `[first, last, draw]` in *source* frames. `DROPS` removes spans as jump cuts, so the output (1200 f) is tighter than the source (1260 f) without renumbering every shot.
- The opus worlds (`src/scenes.ts`, `src/engine.ts`) are unchanged except for `OVR.cam`, which lets the cut impose static, hard-cut framings in place of the opus camera tours.
- Everything editorial is new: the `#482` artifact in ten media, the xerox/halftone/crush/misregister print switches, the logo and annotation inserts, the session interleave, the void, the diagram → axon → terminal → slabs → repos → rail → timeline chain, the manifesto and the wordmark bursts.
- The score is `audio/score.py`. It is synthesised from nothing, and every event is keyed to a source frame and mapped through the same `DROPS`.

## Build
    ln -s <remotion node_modules> node_modules          # e.g. ~/Downloads/intro-1/v3/node_modules (remotion 4.0.529)
    FORCE=1 CONC=4 ./scripts/render.sh out/brnrd-manifesto-v2.mp4   # ~2 min on this Mac
    REBUNDLE=1 node scripts/stills.mjs /tmp/stills 0:1259:9          # source-frame stills (note: stills take *output* frames)
See `REPORT.md` for the grammar, the beat map and the known weak spots.
