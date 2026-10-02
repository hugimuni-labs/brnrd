# Report — manifesto v2 — the MIDA grammar

Dispatched by run run-261001-1411-wrh5. Branch: `brr/manifesto-film-v2` (cut from `origin/brr/manifesto-film-opus`).

## Status
**Done. Submitted for frame review.** Deliverables:
- `brnrd-manifesto-v2.mp4` (40.0 s, 1920×1080, 30 fps, −15.7 LUFS integrated, sample peak −2.2 dBFS)
- `brnrd-manifesto-v2-contact.png` (every 10th frame)
- source in `artifacts/manifesto-v2/` on `brr/manifesto-film-v2`

Three full renders, each reviewed **at every frame** (all 1260/1200 frames tiled at 240 px), plus 960 px spot checks of the hairline states. Sub-second inserts were checked frame by frame on those sheets, not on a stills sheet.

## Findings so far
- "The previous brnrd-unbound version" = `media/video/unbound/` (commit adcf8fa9, 34.5 s canvas piece, `scene.html` + `render.mjs`). Found.

## MIDA — the grammar, extracted (working notes, frame-indexed @23.98 fps)
The clip is one ~5.0 s cycle (120 frames) played ~3× (cycle starts #0, #120, #240). Cycle anatomy:
| frames | dur | what | device |
|---|---|---|---|
| 0–3 | 4f | X mark as doubled, misregistered paper print, purple | print/texture medium |
| 4–7 | 2f+2f | an eye: yellow scratch drawing → cyan detailed drawing | same subject, two line media |
| 8–11 | 4f | eye shrunk between two flat-vector X glyphs | scale inversion, flat vector |
| 12–27 | 16f (0.67 s) | rotated aerial crowd photo, yellow annotation ticks | **readable hold** |
| 28–29 | 2f | grey HUD "NO SIGNAL" target diagram | annotation insert |
| 30–32 | 3f | the same HUD, degraded dark-green raster | raster/material switch of the previous frame |
| 33–41 | 9f | runner silhouette on acid-yellow; ink blobs mask from edges; 2f white scratch | foreground masking, posed on 2s |
| 42 | 1f | extreme close crop of a mesh-textured body, "DESTROY" | 1-frame scale inversion |
| 43 | 1f | black | punctuation |
| 44–47 | 4f | the same figure as purple dithered halftone | medium change, same subject |
| 49–56 | 8f | 3D bending tape, red LED text "CLOSED" | dimensional shot |
| 57–68 | 12f | LED text breaks up and *becomes* the X mark → dark red → bright faceted red → green (2f) | typography → geometry; palette swaps on same form |
| 69–86 | 18f (0.75 s) | thermal-white crowd, 8-bit icon top-right that swaps to "STACK" checker | **hold**, tiny annotation change inside |
| 87–101 | 15f | vertical frame-filling RESIST; 4f halftone burst; 4f diagonal micro-annotation | type as architecture |
| 102–106 | 5f | DISRUPT on olive | word substitution |
| 107–119 | 13f | X back in purple print; 3f flat lavender brush X | material swap, return to origin |

Rules I take from it:
1. **One subject, many renderings.** The X mark appears as print, flat vector, LED pixels, faceted 3D, brush paint; the figure as silhouette, halftone, thermal. The *medium* changes on every cut; the *subject* is what you track.
2. **Holds are readable and carry tiny internal change** (the icon swap during the thermal hold) — never a dead frame.
3. **Bursts are 1–4 frames per state**, and a burst often re-renders the *previous* frame rather than introducing a new subject (HUD → degraded HUD).
4. **One flat dominant field per shot** (purple / black / acid-yellow / red / thermal grey). Colour is changed by hard swap, never by a gradient transition.
5. **Type is huge, rotated, frame-filling**, and gets texture bursts rather than motion.
6. **Motion is stepped** (poses on 2s), not tweened. Only the tape shot moves continuously.
7. **Punctuation frames** — a single black frame, a single extreme crop — reset the eye.
8. **Typography becomes geometry** inside the shot (LED text → mark).
What I do *not* take: the palette, the street-poster subject matter, the vertical format.

## The persistent artifact: `#482`
I chose **PR #482** (`feat/offline-sync`, "retry on 409"), the same piece of work the opus cut already carried. In this cut it takes the job MIDA's X mark does: it is the one subject, and every appearance renders it in a different medium:
misregistered CMY print → Silkscreen pixel → a scale-inverted "48" crop → halftone → terminal diff line → Doto dot-matrix → wireframe outline extrusion → serif on the Claude page → the white chip in `brnrd ls` → the riser through every machine → the white branch in the repo plan → the rail → the timeline → a Code128 barcode → thermal → the underline of THE WORK IS YOURS (eight beads, coloured by the harness that made them).
Why a PR number rather than a branch shape or a code block:
- A number survives any typeface, resolution, or projection. Nothing else in the film survives both a 1-bit photocopy and an axonometric extrusion.
- It is short enough to fill a frame.
- It reads to developers as "a unit of work" without any explanation.
The diff line `+ retry(on: 409, max: 3)` and the hashes ride along as secondary marks.

The resident is never drawn as a thing that travels. Its passage is a **fault**: a band where the next representation already holds, misregistered by a few px, with spectral hairlines at both edges (diagram → terminal). Elsewhere it is a coordinate split on the #482 riser, or the session frames re-indexed into horizontal strips. Chromatic colour appears only on those seams.

## The "previous brnrd-unbound version"
Found: `media/video/unbound/` (commit adcf8fa9, 34.5 s, canvas + Playwright). I took from it:
- its editorial instinct: abrupt `SESSION ENDED` / `CONTEXT LOST` substitutions;
- the wordmark discipline: brnrd as type only, `b^n^d`, `b^_^d`;
- the rule that the invariant stays still while everything around it changes style.

I took no assets from it.

## Beat map (output timecode; bursts vs holds)
| t (s) | kind | what |
|---|---|---|
| 0.00–0.73 | **BURST** | hairline → #482 as misregistered print (3f) → pixel (2f) → "48" scale-inverted crop (2f) → halftone (3f) → terminal diff line (3f) → codex logo, white (2f) → Doto dot-matrix (3f) → 1 black frame |
| 0.73–2.07 | build | Codex, **flat top view** (a 2D diagram): lineage A draws, commits land. Inserts: 2f invert, 2f giant hash `c88e114`, a 4f bracket annotation, 2f `3f9a1c2` |
| 2.10–2.60 | build | hard cut to **axonometric**: the PR volume seals. 3f xerox print switch inside. Jump cut |
| 2.60–2.70 | insert | #482 as a wireframe outline extrusion (3f) |
| 2.70–3.17 | build | close on the follow-up tip. 2f thermal/xerox switch |
| 3.17–3.27 | **raw** | the deliberately ugly frame: an unstyled Times "429 Too Many Requests" page, pixel-crushed (2f), then a white flash |
| 3.27–3.60 | slam | USAGE LIMIT drops |
| **3.63–6.97** | **HOLD 1** (3.3 s) | stranded: the wall, frozen. Only the floor lattice re-indexes, and a `follow-up pending` counter ticks |
| 6.97–8.47 | **BURST** | carried by hand: ⌘C (3f) → slip → **copy 1** (xerox, a line lost) → slip → **copy 2** (halftone, "3/6 lines") → ⌘V on ivory (3f) → the slip fills the frame and becomes the page |
| 8.47–11.83 | build | Claude: clawd (2f, ink pixel) → the page **flat** (print) → "≈" insert (3f) → hard cut **dimensional**: the words stand up, B rebuilt beside the ghost of A. 3f halftone switch inside |
| 11.83–15.30 | build | burn to bitmap → Mistral: 2f pixel crush; a fresh cursor hunts heads (2f invert); **HEAD?** (2f) → v2.4.0 drops → **v2.4.0** full-frame (3f) → NOT IN RELEASE strobe, "#482 missing" (4f) |
| 15.30–16.27 | **BURST** | the three sessions substituted 3f → 2f → 1f (inverted) → "3 sessions 1 work 0 memory" (5f) → black |
| 16.27–17.33 | rupture | the three frozen sessions **re-indexed into one image**: horizontal strips, each from a different session, misregistered and sheared, spectral seams. The strips multiply (5 → 46) and collapse to one hairline |
| 17.33–18.00 | **BURST** | brnrd · brωrd · b^n^d · b>_<d · b·_·d · BRNRD (vertical) · *brnrd* · b^_^d, 2–3f each, alternating black/paper/white grounds |
| **18.00–21.27** | **HOLD 2** (3.3 s) | the void: the three sessions as faint frames in disagreeing coordinate systems, slowly shearing. One word. One fault line. A 2f brnrd→brωrd blink |
| 21.27–22.80 | chain | **2D diagram** on paper (three harness boxes, one continuous #482 line) → in-shot rebase to **axonometric** while the boxes extrude → 2f invert |
| 22.83–24.00 | chain | negative-space swap to black: **provider environments occupy the faces** of the same structure (the frozen codex/claude/mistral frames mapped onto the façades). The **resident passes as a fault band**: inside it, the next representation (the terminal) already holds, offset, with spectral edges |
| 24.03–24.97 | chain | **terminal**: `brnrd ls`, every row carries the #482 chip in one column |
| 24.97–26.67 | chain | **rows become machine blocks**: the rows extrude into slabs as the projection rebases front → iso. Machine labels land on the slabs. The #482 column becomes a riser through every machine, with a coordinate fault on it. 3f "same seat · other machine" |
| 26.70–28.07 | chain | 6f cascade through a permuted axis frame → **repo topology** in plan (four lanes, the #482 branch in white) |
| 28.10–28.83 | chain | 2f white hairline flash → the **branch becomes a rail** (perspective, sleepers carrying hashes) → **the rail turns 90° and flattens** |
| 28.83–30.07 | chain | **process timeline**: commit · commit · limit · carried · rebuilt · seat · strand · v2.4.1 |
| 30.10–30.60 | **BURST** | the timeline ticks stretch into bars → Code128 barcode "#482 retry on 409" on paper (3f) → thermal #482 (2f) → 1 black frame |
| 30.63–32.77 | type | THE MODEL / IS REPLACEABLE, entering through a tear. MODEL is substituted every 3 frames (mono, serif, codex logo, pixel, Anton, clawd, Doto, Bodoni) |
| **32.93–36.53** | **HOLD 3** (3.6 s) | THE WORK / IS YOURS, dead still. The #482 lineage draws under it with eight beads coloured by the harness that made them. Only the background rules re-index |
| 36.57–37.83 | type | #482 print (2f) → OWN / YOUR / AGENTS (Anton, frame-filling) with a fault that stays. 4f halftone switch, 4f annotation frame |
| 37.83–39.47 | end | wordmark burst (2f each) → **brnrd**, the fault line broken around it. A 2f b^_^d blink |
| 39.50–40.00 | — | black |

Count: three holds of 3.3–3.6 s; seven bursts of 1–3-frame states. 25 shots are 1–6 frames long.

## What I kept from the opus cut, and what I cut
Kept, re-framed with static hard-cut cameras instead of keyframed tours:
- the Codex hall and USAGE LIMIT wall;
- the paper slip losing lines;
- the ivory page whose words stand up as architecture;
- the Mistral bitmap world with the cursor and the v2.4.0 drop;
- the tear/fracture/diffraction engine.

Cut entirely: the 30 s topology flythrough, the four "readings" camera chase, the explanatory captions, and the frozen-panel tableau.

## Weak sections (honest)
1. **The Codex act (0.73–3.2 s) is still the most "opus-looking" stretch**: blue wireframe on navy. It is now 2.5 s with five media switches inside, but its *base* look is the 3D visualizer he disliked. Next move: open it as a flat printed diagram and reserve the blue glass for the wall alone.
2. **The rail (28.1–28.5 s) is the weakest state.** It reads as sleepers/ladder more than a physical rail. I shortened it to a ~0.35 s glimpse rather than ship a long weak shot. It needs a real rail profile, lighting and a vanishing point.
3. **HOLD 1 (the wall) may read as dead** to someone who expects MIDA-length holds (≈0.7 s). Its internal change (the lattice drift and the counter) is subtle at small sizes. The brief asked for 3–4 s holds; I kept all three at 3.3–3.6 s.
4. **The terminal and the slabs leave the right half of the frame empty.** It is deliberate negative space, but slightly underpowered.
5. **The resident-as-fault-band transition is used once** (diagram → terminal). Elsewhere the resident is a coordinate split on the riser or the strip interleave. A second band transition (e.g. repos → rail) would make the metaphor land harder.
6. **The score is functional, not composed.** Every cut gets a distinct synthesized transient, holds are near-silent drones, and the chain sits on a plain 128 bpm pulse. There is no melodic motif tied to #482.
7. `brωrd` in Bodoni Moda: the Google subset has no Greek, so the ω falls back to a system serif. It reads fine, but the mixed glyph was not a choice.
8. The manifesto typesetting (Inter Tight 800, left-aligned) is inherited from opus. Only the OWN YOUR AGENTS card was re-designed in the MIDA register.

## What I would still change
Items 1, 2 and 5 first: a flat-print opening for Codex, a modelled rail, a second fault-band substitution. Then a #482 motif in the score, three notes that recur in every medium the artifact takes.
