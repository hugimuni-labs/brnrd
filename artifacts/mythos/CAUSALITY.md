# Mythos — the causal arc and the hidden spine

The opening of the manifesto film (#2158). The film as a whole goes:
**creation → semantics in nature → evolution of symbols → the theft → the
introduction of brnrd**, which brings corporate-fed intelligence back to
people. This section is the creation, and the first two acts of meaning.

> We filmed a universe that keeps accidentally inventing the same shapes,
> until humans begin trapping those shapes in symbols and machines.

## The contract

1. **Physics first, symbol second.** No generator in `src/lab/` takes a
   glyph, skeleton or target. Forms are generated from forces, frequencies,
   fields and seeds, and *found* afterwards by `src/observe.py`.
2. **Letters are recognition, not display.** A letter appears only when the
   observer has found the operator it belongs to, aligned onto that finding,
   for 1–4 frames, and always as a **physical object people made** — metal
   type, a rune cut in stone, ink on polymer, wax pencil on film. Never as
   text on the screen. If the physics on a given seed does not produce the
   operator, the letter does not appear.
3. **Recognition matures.** Early on it is uncertain: two or three candidate
   readings flicker as faint double exposures and none wins. Later it is
   confident: one form, one object, a hard cut. Nature makes near-forms; an
   observer guesses; the guesses converge. That arc is the pre-history of
   symbol-based intelligence the rest of the film is about.
4. **The hidden spine is the name.** `b r n r d` is a near mirror palindrome
   around `n`, by design: `n` is the arch between two poles; `r` the fork at
   either foot; `b`/`d` a cavity and its parity twin. Matter keeps falling
   into the configuration the name spells. The creation never spells it. The
   letters collected here — the lightning's `r`, the field's `n`/`u`, the
   membrane's `b`/`d` — are reserved as the material the **introduction**
   later builds its wordmark from (and the kaomoji mascot `b^n^d` /
   `b^u^d` / `b^_^d` with it). The **theft** section harvests the same forms:
   vectorised, kerned, licensed, flattened into tokens.
5. **The face is the one deliberate easter egg, and it must be earned.**
   `world.faces()` looks for `b^n^d` as a configuration — two mirrored
   pockets with an arch between — on every vibrating frame. It appears in the
   cut only above a quality threshold (`film.FACE_MIN`). On the current seeds
   nothing qualifies (the best candidates read as gaps, not a face), so it is
   absent from this cut — by rule, not by omission.
6. **Creation stuns; punk comes later.** This section aims at awe: restraint,
   scale, holds, real data. Raw, ripped, misregistered energy belongs to the
   theft and introduction.
7. **Colour is a way of observing.** The ground state is warm black, amber,
   copper, furnace, bone. Saturated colour arrives only as a change of
   imaging modality, for a few frames: H-alpha crimson and EUV violet on the
   same solar arches; a cyan negative on a return stroke; photoelastic
   spectra under a polariser; phosphor green when a raster reads the plate;
   UV violet through a photomask; solder-mask green on the board; prismatic
   fringes only at optical faults.
8. **Scale is mostly ambiguous, occasionally undeniable.** Ambiguity →
   revelation → ambiguity: dust → a disk; dust → a ringed world; an arch →
   a star; a star → a seam in rock.

## The arc

**cosmos → field → matter → recurring form → observation → symbol →
inscription → engineering → communication**

| # | act | phenomenon | revelation / recognition | hands on |
|---|-----|-----------|--------------------------|----------|
| 1 | energy without names | Zel'dovich collapse of a random potential (`lab/web`) | — | the densest node, which ignites |
| 2 | rotation / collapse | Keplerian disk, gap with edge waves, lensed far side (`lab/disk`) | the glare drains: the dust was a disk | a lensed arc |
| 3 | debris and belts | ring particles with resonance gaps; a perspective camera inside the ring plane (`lab/rings`) | dust → a ringed world, backlit; `o`/`c`/`O` guessed over the gap | the arc of a ring |
| 4 | plasma | potential-field coronal loops, flux emergence, reconnection (`lab/plasma`), cut with **real** Solar Orbiter EUI loops in crimson and violet, raw AIA detector frames | `n`/`ᚢ`/`∩` guessed; then `n` cut in stone, then seen from below as `u`; the arches stand on a **real star** | a limb |
| 5 | scale collapse | magnification jumps through the limb | — | a line |
| 6 | matter | hexagonal dendrite in a seam (`lab/breakdown`, `aniso=6`) | — | a branch law |
| 7 | the strike | twelve discharges grown across a membrane; the observer keeps the best fork (`lab/breakdown`) | `Y`/`ᚠ`/`r` guessed during the leader; at the return stroke (cyan negative) `r` in metal type | a burnt scar |
| 8 | the stare | the scar cooling; dust blasted off its own channel | — | the scar as a boundary condition |
| 9 | living membrane | FDTD membrane, swept drive, grains collect back onto the scar (`lab/membrane`), too close, folds through focus | `b`/`d`/`q`/`p` cycle as the drive retunes | a frozen plate |
| 10 | observation | polariser (photoelastic stress), squaring to the instrument, phosphor raster whose scan line kicks grains, drive cut, slit | — | the found form |
| 11 | capture | photogram developing on polymer (`lab/develop`) | a hand circles the form and writes `d` in wax | its skeleton |
| 12 | inscription | the type that presses it reads `b` — a sort is mirror-reversed; Douglas–Peucker die pressed into dark matter (`lab/relief`) | `d` cast as `b` | a channel |
| 13 | engineering | UV through a chrome photomask; copper etch; solder-mask board, current along a bus (`shots/inherit`) | — | the bus |
| 14 | communication | the route as a conductive weft (`lab/weave`); the first pulse leaves on a wire | — | → telegraphy |

Every hand-on in the last column is a data dependency in `src/world.py` (and
`shots/inherit.py`), not a metaphor: change the lightning seed and the
membrane's patterns, the found form, the die, the layout, the bus and the
thread's bends all change with it.

## Generate → observe → classify → select

* **Generate.** `src/lab/` takes physical parameters only.
* **Observe / classify.** `src/observe.py` names primitive operators on a
  skeleton (*stem, hook, fork*) and dark pockets by enclosure (*cavity,
  near, bowl, arch, mouth*), and composites (*bowl+stem, hooked fork,
  arch+legs*) — the b/d/p/q, r/ᚠ/Y and n/u/ᚢ families.
* **Select.** `world.lightning()` keeps the best of twelve discharges;
  `world.found()` picks the strongest form where the scar's fork and the
  vibration meet; `world.faces()` looks for `b^n^d`.
* **Recognise.** `src/recognize.py` turns a finding into a letter-object at
  the finding's place, scale and orientation. `film._anchor` supplies the
  place: the corona's recorded arch (`anchor_corona_arch.json`), the
  lightning's fork projected through the plate camera, the found form, or a
  live `observe` pass on the frame.
* **Align.** `film._align` moves inserts so the operator they carry lands
  where the surrounding shot carries it.

`python src/film.py world` prints the selection; `python src/film.py
notebook` draws it.

## Cut grammar

Each major cut preserves exactly one property: position + light (ignition →
disk), magnification (disk gap), curve (arc → arch), line (limb → seam),
branch (dendrite → leader), the object itself (strike → stare → membrane →
observation, one simulation), negative space + position (plate → film),
shape (film → die → mask → etch → board), line (bus → thread), motion
(thread → wire). Recognition cuts preserve position and shape by
construction: the letter stands where the operator was.

## Real material

See [`SOURCES.md`](SOURCES.md). Real Solar Orbiter and SDO/SOHO data enter the
plasma act; the rest is procedural. Named slots record the footage still
wanted (AIA movies, an accretion-disk visualisation, Cassini rings,
lightning).

## What came from #2171

Treated as a laboratory: breakdown, the grain physics (now inside an FDTD
membrane), press (now `relief`, fed captured polylines), the lens/emulsion
pipeline, and the continuous-under-insert render model — refactored away
from glyph inputs. Its "letters found by physics" survive, inverted, as
recognition events.

## Edges of this pass

Most consequential first:

1. **The EUI image's licence was not verified from here** (see `SOURCES.md`).
   Fine for an internal cut; check before any public release.
2. **Real footage is still a still.** The EUI loops are one exposure given a
   slow intensity boil; an AIA movie would replace it with real motion.
3. **No face on these seeds.** The detector runs; nothing passes the bar.
   Trying more membrane seeds (or drive sweeps) is the honest way to get one.
4. **The observer is heuristic** — good enough to select, align, capture and
   place recognitions on these seeds, not validated against human judgement.
5. **Judged from stills and assembled renders,** not on a calibrated display,
   and the sound was not checked on monitors.
