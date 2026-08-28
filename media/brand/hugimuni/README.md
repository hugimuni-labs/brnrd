# HugiMuni — H and M on shared stems

The HugiMuni parent mark is one authored glyph: H and M occupy the same plane and share the two verticals. H spends its middle on a crossbar; M spends it on the valley and crossed lower legs. Neither letter contains the other.

Amber/sky is the canonical palette:

- amber `#ff9a1f`
- sky `#69c7df`
- warm cream `#f0e3cf`

The earlier coral/turquoise exploration is retired from generated assets; git history is its archive.

## The identity rule

The flat vector is the source of truth. Every colour boundary has one meaning:

- **amber = H only**
- **sky = M only**
- **cream = H ∩ M**

The cream areas are therefore actual overlap footprints, not drawn highlights. On the shared stems the displaced H and M naturally create amber / cream / sky registration bands. At bar/diagonal crossings the full geometric overlap turns cream. The lower M-on-M X stays sky because H is not present there.

That rule is the logo. Screen glow and print colour conversion are material treatments of it.

## Registers

### Flat — canonical

`hugimuni-amber-sky-flat.svg` is the transparent master artwork. It has no filters and should still feel complete by itself.

`hugimuni-amber-sky-flat-on-dark.svg` is only a review proof on the intended near-black ground. GitHub's transparency checkerboard is useful for verifying alpha and terrible for judging this palette.

`hugimuni-amber-sky-lockup.svg` is the canonical company lockup: the monogram with **HugiMuni as one word, centred strictly below the symbol**. The wordmark is deliberately quiet cream; the symbol already carries the three-colour identity.

### Screen — atmosphere, not a second logo

`hugimuni-amber-sky.svg` starts with the exact flat artwork and adds restrained bloom, a hot pass only in the real H∩M regions, and faint phosphor grain/scanlines. Remove every filter and the identity underneath remains intact.

`hugimuni-amber-sky-icon.svg` is that screen treatment on the rounded near-black board. The board is a composition, not part of the master mark.

### Print — the same topology in ink

`hugimuni-amber-sky.eps` and `hugimuni-amber-sky-lockup.eps` are Level-2 process-CMYK production mappings of the same three regions. H and M are stroked in process inks and H∩M is produced with `strokepath` clipping, so the print file does not fake the screen glow or rely on decorative white tubes.

`hugimuni-amber-sky-print.svg` and `hugimuni-amber-sky-print-lockup.svg` are RGB visual proofs of that geometry. They are not colour-managed press proofs.

The EPS lockup uses live PostScript Helvetica for the single `HugiMuni` word. Outline it in final imposition/preflight if the printer requires path-only artwork.

## RGB vs CMYK

Do not make CMYK the master identity space. Screens emit RGB light and browsers/SVG are RGB-first; print conversion depends on the press, stock, RIP and ICC profile. A CMYK tuple without a profile is not a universal colour.

The intended architecture is:

1. one geometric identity / one intended visual palette;
2. sRGB values for the canonical SVG and screen use;
3. explicit CMYK output mappings for print;
4. final PDF/PDF-X conversion using the actual printer profile when available.

`PRINT_AMBER`, `PRINT_SKY`, `PRINT_INTERSECTION` and `PRINT_BLACK` in `build.py` are therefore production hooks, not the master colour definition. Do not invent a rich-black recipe before the vendor supplies one.

## Lockup

The old two-row `H + UGI` / `M + UNI` construction is retired. It read as two words stacked vertically. The lockup now uses one centred `HugiMuni` word below the symbol in flat SVG, print proof and EPS.

## Tuning bench

`/brand-bench` mirrors the canonical HugiMuni model via `src/frontend/src/lib/hugimuniBrandGeometry.ts`.

For HugiMuni the bench now exposes:

- flat vs screen register;
- H/M geometry and registration offset;
- amber / sky / intersection / ground colours;
- screen-only bloom, hot-overlap and grain controls;
- small-size previews;
- the one-line HugiMuni lockup;
- a copyable constant block matching `build.py`.

The flat register is the one to judge first. Screen controls should never be used to rescue weak geometry.

## Generate

```bash
python3 media/brand/hugimuni/build.py
```

The generator writes:

- `hugimuni-amber-sky-flat.svg`
- `hugimuni-amber-sky-flat-on-dark.svg`
- `hugimuni-amber-sky-lockup.svg`
- `hugimuni-amber-sky.svg`
- `hugimuni-amber-sky-icon.svg`
- `hugimuni-amber-sky.eps`
- `hugimuni-amber-sky-lockup.eps`
- `hugimuni-amber-sky-print.svg`
- `hugimuni-amber-sky-print-lockup.svg`

## Next optical pass

The remaining work is logo design rather than format conversion. The most useful variables to judge in `/brand-bench` are `SPREAD`, `GHOST`, `STROKE`, `STEM_STROKE`, `CROSS` and `TAIL`: they control the shoulder "ears", the amount of chromatic registration, the crossbar/diagonal hierarchy, and the lower X. Tune those on the flat mark first at 320px, 32px and 16px; only then tune the screen atmosphere.
