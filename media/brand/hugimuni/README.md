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

Two palettes, both his:

| file | pair |
| --- | --- |
| `hugimuni-amber-sky` | brnrd's own amber `#ff9a1f` + a cold sky `#8fb6cc` |
| `hugimuni-coral-turquoise` | coral `#ff6f61` + turquoise `#3ec9bd` |

The first ties the parent to the product; the second lets the parent stand
apart. That is the actual decision underneath the colour question.

**Where the crossings sit is geometry, not taste.** A straight leg crosses a
vertical exactly once, so the stems alone can only weave the mark in one band.
Six crossings need three strokes doing the work: the legs cross the stems high,
the bar crosses the stems at its own height, and the bar crosses the legs low —
which is why `CROSS` sits at 302 rather than at the optical middle. Move the bar
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
