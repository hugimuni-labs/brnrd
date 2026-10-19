# Glyph physics — the lexicon

The opening of the manifesto film (#2158) needs symbols that feel *found*
rather than drawn. This study sets up the vocabulary for that: every glyph
the film hunts has a **physical etymology**, a phenomenon whose own law
produces the shape. Every operation that turns one glyph into another is a
**physical operation**: a parity flip, a reversed velocity, a reading from
the other side, a change of frequency, a change of focus.

The next task, the cinematic techno-occult motion language, gets this as its
grammar. It has a closed set of tokens (glyph × physics × material) and a
closed set of verbs (the operations below). That set is what lets the
motion stay dense without becoming arbitrary.

## The rule

> A glyph is never typeset. It enters a simulation only as a force: an
> attractor set, a potential, a pole placement, an aperture, a trajectory,
> a die. What comes out is whatever that physics makes of it.

`src/glyphs.py` holds skeletons: a few strokes per glyph in an em box. No
generator ever sees a filled letterform. Legibility is an *outcome*, and
every generator has a knob that trades it off against chaos:

| generator | legibility knob | what the knob is physically |
|---|---|---|
| `chladni` | `K`: modes kept, n² + m² < K² | drive frequency |
| `breakdown` | leader progress, return stroke | how far the discharge has committed |
| `field` | where the light falls | which side of the field is observed |
| `rd` | kill-rate contrast along the skeleton | how hostile the channel is to growth |
| `holo` | propagation distance z | focus: the act of observation |
| `swarm` | temperature T | heat vs. the pull of the well |
| `mach` | v / c | how much faster than its medium the source moves |
| `press` | depth, heat | impact and its afterglow |

## Etymologies (native pairings)

Only these pairings are *honest*: the physics makes the shape from its own
law. The barrage still makes every physics try every glyph (`out/atlas.png`),
because the near-misses are part of the hunt. But the film resolves each
glyph in its native physics.

| family | glyph | physics | relation |
|---|---|---|---|
| fork | `l` | breakdown | one channel: the first path a discharge commits to |
| fork | `r` | breakdown | a channel that hesitates and hooks toward the nearer charge |
| fork | `ᚠ` `ᛉ` `ᚱ` | breakdown | stems that branch twice, keep their trunk, fold back |
| fork | `⤙` | breakdown | a route that splits at its end: fan-out |
| fork | `V` | mach | the shock cone of anything faster than its medium |
| fork | `^` | mach | the same cone, travelling the other way |
| fork | `Y` | mach | the wake: the cone, plus the trail that made it |
| fork | `A` | breakdown | a fork whose arms are crossed by their own branch |
| bowl | `b` | chladni | a cavity on a stem: grains fleeing the antinode |
| bowl | `d` `p` `q` | chladni | `b` under parity in x, in y, in both |
| bowl | `o` `c` `a` | rd | a void the reaction cannot enter: closed, open, against a wall |
| bowl | `J` | holo | a stem whose foot is still out of focus |
| channel | `n` / `u` | field | the line from N to S, read from above / from below |
| channel | `m` / `ω` | field | three poles, read from above / from below |
| channel | `ᚢ` | field | an arch with one leg pulled long |

The issue comment's relatives (`bd: p o l J a q c`, `r: ^ l Y V A ⤙`,
`n: u ω`) are all here, each with a mechanism instead of a resemblance.

## Operations: how glyphs turn into each other

These are the verbs. Each one is exact in its physics. None is a morph.

| from → to | operation | where it is literally true |
|---|---|---|
| `b ↔ d` | **parity in x** | DCT mode n picks up (−1)ⁿ under x → −x. Interpolating the coefficients passes through the **even-modes-only** state, the symmetric two-lobed form, which is the brief's "mirrored lobes" made exact |
| `b ↔ p`, `b ↔ q` | parity in y, in both | same, on m and on n + m |
| `n ↔ u`, `m ↔ ω` | **reading side** | 2-D field lines of a ± pole pair are circles through both poles; the upper arcs read `n`, the lower arcs `u`. The field doesn't choose. The light does |
| `V ↔ ^` | **velocity reversal** | the Mach cone's half-angle is asin(c/v); flip the velocity and the cone flips |
| `V → Y` | **trail visible** | the wake with and without the hot channel behind the source |
| `l → r → ᚠ` | **branching** | a leader before and after its first, second branch |
| `o ↔ c` | **closure** | a void in reaction-diffusion closed, then breached |
| any → legible | **frequency, focus, cooling** | `K` up, `z → 0`, `T` down |
| legible → any | **kick** | energy injected: a mode jump, a tap, a strike |

## Shape families ↔ physics

- **Fork** (stems, hooks, splits): breakdown and wakes. Anything that
  *travels and commits*: discharges, cracks, routes, signals.
- **Bowl** (cavities, counters, mirrored lobes): membranes and reactions.
  Anything that *stands and settles*: nodal sets, voids, orbits.
- **Channel** (arches, troughs, prongs): fields. Anything that *connects
  two poles*: conduction, attraction, the gap a current jumps.

`brnrd` uses all three: b (bowl), r (fork), n (channel), r (fork),
d (bowl, the parity of b). The word's own symmetry is physical: `b`/`d` is one
membrane under parity, and the two `r`s are two discharges that never take the
same path. The film's last image puts the five letters side by side, each
found by its own law, under one ruler.

## Materials (what the light looks like)

| material | used for | light |
|---|---|---|
| particulate field | dust in god rays (`swarm`) | amber scattering; mostly black |
| dark rock-metal | crack, press (`breakdown`, `press`) | furnace glow from inside the mark, copper rim light |
| living membrane | Chladni plate | near-black surface, bone/metal grains, raking light |
| paper + filings | field lines | aged off-white, dark filings, light from one side |
| schlieren | wakes | flat sodium amber field, split light/dark fronts |
| coherent light | holography | sodium 589 nm speckle on black |
| translucent polymer | specimen print | backlit off-white, two misregistered inks |
| coral relief | reaction–diffusion | copper relief, white-hot crests |

Warmth always has a cause (sodium, furnace, sun). Light is allowed to destroy
information: clipped return strokes, a scanner bar that overexposes the
specimen, flash frames, negatives.

## Motion rules (setup strength)

These are MIDA's rules, scaled down for the mythos.

1. **Vibration, not travel.** Shots don't move cameras; the world trembles.
   Grain hops, filing taps, leader tremble, speckle boil and mount jitter keep
   every frame alive. The only travelling things are signals (wake source,
   current pulse, Morse).
2. **Energy arrives as kicks.** Mode jumps, taps, strikes and impacts are
   single-frame events: an exposure spike, sometimes a photonegative frame,
   always a sound transient.
3. **Bursts are 2–4 frames per state.** The barrage holds each finding 2–3
   frames, and a single black frame resets the eye between runs.
4. **Holds are readable and never dead.** The holography stare holds ~2 s.
   The reticle locks, and the speckle slows *because* it locked.
5. **Each cut preserves one property.**
   - *shape:* crack `r` → specimen `r` → pressed `r` (same placement, same tree)
   - *angle:* the `r`'s branch → the Mach cone (asin(c/v) ≈ 34°)
   - *position:* subliminal Chladni `b` → the dust's `b` clump
   - *texture:* grain → grain (membrane macro → reaction crop → crack crop)
   - *rhythm:* membrane kicks → filings taps → Morse
6. **Shape-matching cuts are additive light, not dissolves.** The `match()`
   transition bends both images through a ripple and adds their light, so two
   phenomena coexist for 4–6 frames.
7. **Observation perturbs.** Focus creates the form (holo), light selects
   the reading (field), the reticle's lock settles the speckle, and grains
   jump on every frequency change.

## What this study does not settle

- **Playback-speed review.** Frames were reviewed as stills and contact
  sheets. The rhythm of the barrage needs eyes at 24 fps.
- **Font-free runes in labels.** Overlay labels use Unifont for runic
  glyphs and `⤙`. The *findings* never use a font, but the annotations do.
- **`A` and `J` are weak etymologies.** `A` as breakdown, and `J` as "out of
  focus", are honest but generic. A reflected shock for `A` was tried, but
  it made an hourglass, not an A.
- **The swarm is the least physical.** A Langevin potential well is a
  modelling convenience, not a phenomenon anyone photographs. It is used for
  dust (where it is honest) and as barrage filler.
