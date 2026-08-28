# HugiMuni — H and M occupying the same plane

The mark has one canonical geometry and one simple material rule:

- **amber = H only**
- **sky = M only**
- **cream = H ∩ M**

That third region is not a highlight painted on top of the logo. It is the real
boolean overlap of two complete letterforms. The shared stems therefore produce
the broad amber / cream / sky registration band automatically, while the H bar
and M diagonals produce their own cream overlap footprints. The lower X stays
sky because it is M crossing itself, not H meeting M.

This is the identity source. Everything else is a material treatment of it.

## Flat first

`hugimuni-amber-sky-flat.svg` is the canonical master artwork. It is transparent,
filter-free and intentionally good enough to use directly. The SVG renders H and
M as opaque vectors and derives the cream intersection with a vector mask; there
are no fake white tubes, crossing dashes, glow, grain or z-order-dependent colour
accidents.

`hugimuni-amber-sky-flat-on-dark.svg` is the same artwork on the canonical dark
ground and exists mainly as a convenient review/proof surface. GitHub's
checkerboard is useful for confirming transparency but is not the intended brand
ground.

## Screen is atmosphere, not different geometry

`hugimuni-amber-sky.svg` starts from the exact flat artwork and adds only screen
material: a restrained bloom, a small hot pass on the already-defined H/M
intersection, and faint phosphor grain/scanlines. `hugimuni-amber-sky-icon.svg`
adds the rounded dark board around that screen register.

If all filters vanished, the logo underneath would still be the logo. That is
the test the earlier neon-tube approach failed.

## Print is a colour-space/export mapping

`hugimuni-amber-sky.eps` and the lockup are Level-2 process-CMYK renderings of the
same three-region topology. The EPS does not approximate the overlaps with little
cream marks: it uses `strokepath` clipping to paint the actual pairwise H ∩ M
regions. `false setoverprint` is explicit.

CMYK is deliberately **not** the master colour space. Browsers and normal SVG
pipelines are RGB-first, while CMYK output depends on the press, stock, RIP and
ICC profile. Putting CMYK values at the centre of the identity would make the
screen version less portable without making print more correct. Instead:

1. geometry/topology is shared exactly;
2. the canonical visual colours are authored in sRGB;
3. `PRINT_AMBER`, `PRINT_SKY`, `PRINT_INTERSECTION` and `PRINT_BLACK` map those
   colours for physical production;
4. when the stand printer supplies an ICC/profile or preferred rich black, tune
   those output values for that vendor rather than changing the master artwork.

The RGB `*-print.svg` files are **geometry proofs**, not contractual colour
proofs. A final press PDF/PDF-X should be exported with the actual vendor profile.

## Files

- `hugimuni-amber-sky-flat.svg` — canonical transparent master
- `hugimuni-amber-sky-flat-on-dark.svg` — canonical master on dark review ground
- `hugimuni-amber-sky.svg` — transparent emissive screen register
- `hugimuni-amber-sky-icon.svg` — screen register on rounded dark icon board
- `hugimuni-amber-sky-print.svg` — portable print-geometry proof on black
- `hugimuni-amber-sky.eps` — CMYK production mark
- `hugimuni-amber-sky-print-lockup.svg` / `hugimuni-amber-sky-lockup.eps` — stand lockup

The lockup keeps the existing vector H/M prefixes plus `UGI` / `UNI` in Helvetica,
a PostScript base face. Outline the continuation type during final imposition if
the printer's preflight requires path-only artwork.

The old coral/turquoise exploration is intentionally retired from the generated
asset set. Git history remains its archive; amber/sky is the HugiMuni identity.

## Regenerate

```bash
python3 media/brand/hugimuni/build.py
```

The important invariant is not a particular effect or file format. It is that
all registers preserve the same statement: **H, M, and the space they genuinely
share.**
