# Mythos — the causal arc

The opening of the manifesto film (#2158), authored again from first
principles after the direction update of 2026-10-03. The previous pass
(#2171, `artifacts/glyph-physics/`) proved that physics could *render*
glyphs. This one inverts it: the generators know only forces, frequencies,
fields and seeds, and whatever looks like a letter is something an observer
**found** afterwards.

> We filmed a universe that keeps accidentally inventing the same shapes,
> until humans begin trapping those shapes in symbols and machines.

The film ends on the first transmitted pulse. There is no `r`, no `brnrd`,
no text anywhere in the picture.

## The arc

**cosmos → field → matter → recurring form → observation → symbol →
inscription → engineering → communication**

| # | act | phenomenon (generator) | operator it discovers | what it hands on |
|---|-----|------------------------|-----------------------|------------------|
| 1 | energy without names | Zel'dovich collapse of a random potential (`lab/web`) | **line**, **node** | the densest node, which ignites |
| 2 | rotation / collapse | Keplerian disk, gap with edge waves, point-lens far side (`lab/disk`) | **loop**, **cavity**, **arc**, jet **line** | the lensed arc, standing as an arch |
| 3 | plasma topology | potential-field coronal loops; flux emergence; reconnection detected from connectivity change (`lab/plasma`) | **arch**, **bend**, **fork** | a loop's leg |
| 4 | scale collapse | the leg magnified into a mineral seam; hexagonal dendrite (`lab/breakdown`, `aniso=6`) | **branch** | the search habit of a growing tip |
| 5 | the strike | dielectric breakdown across a dark membrane (`lab/breakdown`) — twelve discharges grown, the observer keeps the one with the strongest fork/hook | **fork**, **hook**, **stem** | a burnt scar — a permanent mark |
| 6 | the stare | the scar cooling; the blast has swept dust off its own channel | (rest) | the scar as a boundary condition |
| 7 | living membrane | FDTD membrane, swept drive, grains hopping ∝ amplitude; the scar pins a node (`lab/membrane`) | **bowl**, **lobes**, **channel**, and grains collecting back *onto* the scar | a frozen plate where two histories touch |
| 8 | observation | polariser (photoelastic stress), squaring to the instrument, raster acquisition whose scan line kicks grains, drive cut, slit (`optics`) | — | the found form (`observe.features`) |
| 9 | capture | photogram on autocatalytically developing emulsion; one grease-pencil loop (`lab/develop`) | — | the form's skeleton as polylines |
| 10 | inscription | Douglas–Peucker die pressed into dark matter (`lab/relief`) | — | a channel |
| 11 | engineering | the channel snapped to a lithographic grid; a bus routes out (`observe.manhattan`, `world.layout`) | **line** again, now parallel | the longest route's steps |
| 12 | communication | the route as a conductive weft in a weave; the pulse leaves on a wire (`lab/weave`) | **pulse** | → telegraphy, the next section |

Every hand-on in the right-hand column is a real data dependency in
`src/world.py`, not a metaphor: change the lightning seed and the membrane's
patterns change, the found form changes, the die, the layout and the thread's
bends all change with it.

## Generate → observe → classify → select

The direction's largest technical correction is implemented literally.

* **Generate.** `src/lab/` generators accept physical parameters only. Grep
  them for a glyph table: there is none.
* **Observe / classify.** `src/observe.py` skeletonises any image and names
  primitive operators: *stem, hook, arch, fork, cavity, near-closure, bowl,
  mouth* (enclosure measured by ray-casting from dark pockets, with the
  opening direction telling bowl from arch). Composites — *bowl+stem, hooked
  fork, arch+legs* — are the b/d/p/q, r/ᚠ/Y and n/u/ᚢ families, named here
  and nowhere on screen.
* **Select.** `world.lightning()` grows twelve discharges and keeps the one
  `rate_tree` scores highest for a decisive fork near the middle plus hooked
  side branches. `world.found()` freezes the plate, classifies it, and picks
  the strongest closed form sitting against the scar's fork. On the current
  seeds it found a near-closed pocket beside a hooked stem — a relative of
  `b`/`ᚱ` that nobody drew.
* **Align.** `film._align` runs the same observer on inserts at assembly time
  and moves them so the operator they carry lands where the surrounding shot
  carries it.

`python src/film.py world` prints the selection ranking, the found form and
the observer's census.

## Cut grammar

Each major cut preserves exactly one property.

| cut | preserved | how it is guaranteed |
|-----|-----------|----------------------|
| web → disk | **position + light** | the ignited node is placed on `P_IGNITE`; the disk camera puts its approaching inner edge there; the disk opens overexposed and drains |
| disk (gap) | **magnification** | stepped objective-turret jumps, not a fly-through |
| disk → corona | **curve** | the lensed arc's apex and the hero loop's apex share `P_ARCH`, solved per shot |
| corona → crystal | **line** | three zoom steps on one loop leg, then a vein at the same angle |
| crystal → strike | **branch** | a dendrite's search hands over to a leader's |
| strike → stare → membrane → observation | **the object itself** | one continuous simulation; jump cuts skip time, not world |
| plate → capture | **negative space + position** | the found pocket is at screen centre on the slit and on the film |
| capture → press | **shape** | the die is the photogram's skeleton |
| conduct → thread | **line** | parallel bus traces → parallel weft |
| thread → wire | **motion** | the pulse keeps its direction and speed |

Inserts (1–2 frames): an arch in the web; corona in the lensed arc;
lightning's fork inside the reconnection; a disk gap inside the membrane;
a corona arch inside the membrane; a thread inside the layout. Stateful shots
keep running under them.

## Observation is interaction

No HUD, no labels, no reticles. Observation is shown only as a change of
imaging modality, and each one physically does something:

* **Polariser** — the membrane between crossed polarisers shows isochromatic
  fringes of its RMS strain. Light makes the matter stranger, not clearer.
* **Squaring** — the camera's homography is pulled from oblique to frontal:
  measuring is aligning the world to the instrument.
* **Raster** — rows are acquired top to bottom and the scan line *kicks the
  grains it reads* (`Membrane.probe`). To observe is to perturb.
* **Freeze** — the drive is cut so the plate can be measured; the grains
  settle where the measurement left them. That frozen state is what gets
  captured.
* **Slit** — Fresnel-edged jaws close on what was found.

## What came from #2171

Treated as a laboratory, not a storyboard. Kept and refactored away from
glyph conditioning:

* `breakdown` — space colonisation now runs on attractors scattered from a
  physical charge/stress density, with an optional crystal habit; main
  channel = the leader that reached ground; `scar()` leaves residue.
* `chladni` → `membrane` — the grain physics (hop ∝ amplitude, walk down its
  gradient) is kept; the glyph-envelope DCT fit is replaced by an FDTD
  membrane with a swept drive, a free whipping edge, and a scar-dependent
  pinning term.
* `press` → `relief` — takes captured polylines instead of a skeleton name.
* `rd` — its job (autocatalytic growth) moved into photographic development.
* `swarm` — its Langevin dust became the web's lattice particles; the
  potential is a Gaussian random field, not a glyph distance.
* `optics` — the lens/emulsion pipeline is kept; the `Glass` HUD is removed;
  starburst, defocus, slit, occulter, photoelastic, raster and film-edge
  modalities are new.
* the continuous-under-insert render model and deterministic seeded pipeline.

Dropped: the lexicon as worldview, labels, the word reveal, Morse `brnrd`,
math cards.

## Edges of this pass

What this pass could not verify or deliberately left open, most consequential
first:

1. **Real scientific footage is not used.** The direction invites NASA SDO /
   SVS material for the corona and the disk. This container could not be
   relied on for asset fetches, and committing media is blocked by the repo's
   hygiene check. The corona is therefore fully procedural and still reads
   more like a field visualisation than like AIA 171 Å imagery. A `sources/`
   fetch step with an attribution manifest is the natural next layer.
2. **The film was judged from stills and a single assembled render**, not on
   a calibrated display or with sound checked on monitors.
3. **The observer is heuristic.** It is good enough to select, align and
   capture on these seeds; it over-reports stems and under-reports forks in
   dense grain fields. Composite readings are not validated against human
   judgement.
4. **Physics is physically motivated, not physically exact.** Thin-lens
   lensing, potential-field corona, a 2-D membrane with a scalar pinning
   term — each is honest about its cheat in its docstring.
