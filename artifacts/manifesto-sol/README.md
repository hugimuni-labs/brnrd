# THE LINE OUTLIVES THE ROOM

An independent 84-second manifesto film for brnrd. Source is procedural, frame-deterministic Remotion: no footage, copied provider interfaces or third-party music.

## Render

```sh
npm ci
python3 -m pip install -r requirements.txt
python3 score.py
node render.mjs --out /absolute/path/to/export
```

Use `node render.mjs --stills --out /absolute/path/to/review` for composition samples. Render uses concurrency 2; scratch and persistent frames live in `out/` inside the source project and are ignored by git. Completed frames are reused when rerunning after a failed mux; after changing the visual timeline, remove `out/frames/` before rendering. Python score generator requires NumPy. FFmpeg must be on PATH.

## Treatment

One numbered work prism travels a branch spine through cold precision, warm typographic space and amber raster space. Provider/session walls separate the work from its continuity. A topological fault breaks those coordinate systems; the resident becomes the only fixed point in a system that reprojects through providers, machines, projects and processes.

## Timeline

0–12: Codex, branch and PR. 12–17: follow-up meets usage limit. 17–25: manual context carry. 25–30: Claude restitches the branch. 30–36: a fresh Mistral session takes the wrong lineage to a release. 36–43: brnrd ruptures the world. 43–70: four projections of one topology. 70–84: ownership manifesto.

The score is synthesized from scratch, with no samples. Artwork and source authored for this commission.

## Validate the encoded film

```sh
python3 check.py /absolute/path/to/export/brnrd-continuity-sol.mp4 /absolute/path/to/export/review
```

The check decodes every frame and audio packet, counts the frames, measures AAC loudness and true peak, checks dark intervals inside the cinematic image area, and extracts narrative and sub-second motion contact sheets from the final MP4. `integrity.json` includes the file hash and full probe result.

## Design mechanics

The branch is explicit 3D geometry, not a diagram overlaid on a provider screenshot. The folded paper context packet occupies the same spatial scene as its prism. The rupture clips three coordinate fragments, displaces and rotates them independently, warps world vertices, and draws narrow cyan/copper diffraction at the fault. The second act keeps 72 node identities and 130 rail adjacencies while changing embeddings and the projection basis. No remote assets or network calls occur during rendering.

## Files

- `src/Film.tsx`: complete deterministic visual timeline and projection math.
- `score.py`: original stereo score and measured two-pass normalization.
- `render.mjs`: one bundle, concurrency 2, persistent JPEG frame pass, external FFmpeg H.264/AAC mux.
- `check.py`: checks and rendered-output contact sheets.
- `src/index.tsx`: 2520-frame, 30-fps composition.
