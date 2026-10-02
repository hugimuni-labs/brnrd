# brnrd — manifesto film (opus cut)

96 s · 1920×1080 · 30 fps · H.264 + AAC · −15.2 LUFS, true peak −1.6 dBFS.
*The model is replaceable. The work is yours. Own your agents.*

## Treatment
Every world is one scene graph pushed through a single projective camera: a free 3×3 matrix blended element-wise, plus perspective and screen-space faults (`src/engine.ts`). Blending the matrix across an axis permutation shears the whole coordinate system mid-move. That is the "reprojection" look, not a preset. **The resident is the only thing drawn outside the projection.** It is a screen-space glyph, pixel-identical in every frame, and every tear passes *under* it.

| t (s) | beat |
|---|---|
| 0–13.6 | **Codex**: cold glass hall. A hairline unfolds into lineage A, five commits, then PR #482 sealed in a measured volume. The follow-up tip is working until a **USAGE LIMIT** wall drops through it; the world freezes. |
| 13.7–18 | **Carry**: the context lifts off as a paper slip and loses three of six lines in flight. The slip fills the frame and becomes the page. |
| 18–25 | **Claude**: the ivory page, where the surviving words stand up as serif architecture. Lineage B is rebuilt beside the ghost of A ("≈ context"). |
| 25–34 | **Mistral**: the page burns down to dithered bitmap. A fresh session hesitates between the two heads, picks A, and drops **v2.4.0** on it; B strobes *NOT IN RELEASE*. |
| 34–39 | The three sessions as frozen panels in a void, with broken links between them. |
| 39–47 | **Rupture**: the resident enters, and image-space tears plus depth-sheared geometric faults follow it, with thin diffraction at every split end. A 1-second cascade flips through five projections. Wrong reconnections flicker, then one white thread joins the three heads, and the panels lie down as planes. |
| 47–75.5 | **Topology**: one stack of planes, rails, modules and risers, read four ways by reprojection alone: providers (iso) → machines (axes swapped, planes become racks) → projects (plan view) → processes (perspective chase). The work trails the resident as one lineage whose beads keep the colour of the plane that made them, ending in **v2.4.1**. |
| 75.5–79 | The system folds into the resident. |
| 79–96 | The three manifesto lines, each entering through a healing tear; *MODEL* cycles through the provider typefaces while *WORK* never moves. *OWN YOUR AGENTS.* keeps its fracture. Ends on the wordmark. |

## Build
    ln -s <some remotion node_modules> node_modules   # or: npm i
    ./scripts/render.sh out/brnrd-manifesto-opus.mp4   # ~25 min on the M1, scratch in out/tmp only
    node scripts/stills.mjs /tmp/stills 12.5 41.3 60    # stills (REBUNDLE=1 after edits)
    npx remotion studio src/index.ts

The score is `audio/synth.py` (numpy/scipy). It is synthesized from nothing and hand-locked to the times in `src/scenes.ts`.
