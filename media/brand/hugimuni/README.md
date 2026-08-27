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

The generator now emits the normal SVG mark plus two genuine vector EPS
deliverables for each palette: the standalone mark and a conference-lockup
version with `HugiMuni` sized below it. The EPS treatment uses vector scanlines
and deterministic phosphor flecks rather than embedding a bitmap; `GRAIN`
controls their density and the live bench applies the same strength to its SVG
noise and scanlines. `INTERSECTION` is also exposed as a colour input there,
with warm phosphor white as the current default. The wordmark
uses standard PostScript Helvetica Bold; convert the text to outlines in the
final layout application if a printer requires embedded outlines.

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
on). The EPS variants keep their own opaque ink ground for print, with the
phosphor flecks sampled along the strokes to rhyme with the screen render.

Regenerate everything: `python3 media/brand/hugimuni/build.py` — writes the
SVG marks and the EPS mark/lockup variants beside this file. Tune first on
`/brand-bench`, copy the constants block back into `build.py`, rerun.
