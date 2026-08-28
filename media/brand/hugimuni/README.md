# hugimuni — H and M on shared stems

A sketch for the parent org, done in the same evening and the same way: the
geometry is authored, the material is not generated.

H and M already share their skeleton — two verticals. H spends its middle on a
crossbar, M spends it on a valley. Superimposed on one pair of stems they are a
single glyph that reads as either letter depending on which stroke your eye
follows, and giving each letter its own hue hands that choice to the viewer
instead of resolving it.

**The two letters interpenetrate; neither contains the other.** The M is the
larger letter: its diagonals cross the stems *above* their tops and its valley
dips *below* their feet, and the H's bar runs past both stems. That is the
maintainer's sketch, and it is the difference between a monogram and two
letters parked in the same box — the first draft here nested the M inside the
H and read as an M with a line through it.

Where the strokes cross, the colours **average** rather than stack: every
drawing group blends in `screen`, so a crossing is genuinely a third colour.
The shared stems carry both hues offset around a pale core, which is the same
chromatic-aberration grammar brnrd's screen register already speaks — parent
and product rhyme without wearing each other's mark.

The generator emits the normal emissive SVG plus two genuine vector EPS
deliverables for each palette: the standalone mark and a conference lockup.
The EPS is a separate **stone/print register**, not a flattened imitation of
the screen effect: explicit process-CMYK inks, no raster content, no glow,
scanlines, or grain, and paper-white knockout cores on every stroke. That last
rule makes the diagonal/bar crossings white instead of whichever colour was
painted last. `false setoverprint` is explicit so the white clears every plate.

The lockup spells the company name as two rows: the mark's own vector `H` plus
lower-register `UGI`, then its own vector `M` plus lower-register `UNI`. The
continuations use Helvetica, a PostScript base face; outline them in the final
imposition application if the printer's preflight requires all type converted
to paths.

`STEM_STROKE` is deliberately independent from `STROKE`: the side legs can
carry more visual weight without fattening the crossbar and diagonals.

Two palettes:

| file | pair |
| --- | --- |
| `hugimuni-amber-sky` | brnrd's own amber `#ff9a1f` + a cold sky `#69c7df` |
| `hugimuni-coral-turquoise` | coral `#ff6f61` + turquoise `#3ec9bd` |

The first ties the parent to the product; the second lets the parent stand
apart. That is the actual decision underneath the colour question.

**Where the crossings sit is geometry, not taste.** A straight leg crosses a
vertical exactly once, so the stems alone can only weave the mark in one band.
Six crossings need three strokes doing the work: the legs cross the stems high,
the bar crosses the stems at its own height, and the bar crosses the legs low —
which is why `CROSS` sits at 276 rather than at the optical middle. Move the bar
up and the bottom half comes apart; that was the first draft's actual defect,
and no amount of widening the M fixed it.

The M levels with the stems rather than overshooting them: same cap height,
same feet, one silhouette. Only the valley drops below, which is the single
place the mark is allowed to break its own box.

One trap, the same family as the brnrd mark's: **`mix-blend-mode` has to sit on
the drawing group, not only on the isolating parent.** A parent alone
establishes the group and blends it against the page, leaving its children in
plain z-order — which renders as "the last colour wins" and looks exactly like
a design decision rather than a bug.

```bash
python3 media/brand/hugimuni/build.py
```

## The emissive render, and the ground it needs (2026-08-28)

The SVG mark is **transparent and self-lit**: three passes per stroke (bloom
halo → saturated body → blurred white-hot core), screen-blended in one
isolated group so every crossing adds up, with the grain — turbulence plus
scanlines — masked *onto the strokes* so `GRAIN` modulates the letter light
itself. There is no background board anymore; the old rounded ink rect read
as a grained monitor bezel.

**Dark grounds only.** Screen blending against a light ground washes the
mark toward white — that is physics, not a bug: a glow cannot exist on
white. Place the SVG on near-black (`#030504` is what /brand-bench judges
on). The EPS variants keep their own opaque process-black ground for print and
deliberately drop the phosphor texture. At a stand, the weave and the name have
to survive distance and a real RIP before they get to be atmospheric.

Regenerate everything: `python3 media/brand/hugimuni/build.py` — writes the
emissive SVG, the EPS mark/lockup variants, and flat `-print.svg` visual proofs
beside this file. The proofs intentionally share the EPS geometry and are the
portable review surface when a workstation has no PostScript rasterizer. Tune
the screen register first on `/brand-bench`; judge print from the flat proof.
