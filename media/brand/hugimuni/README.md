# hugimuni — H and M on shared stems

The parent-company mark is one authored glyph, not two letters stacked in a box. H and M share the two verticals: H spends the middle on a crossbar, M spends it on the valley. The shoulders, bar, and crossed lower legs deliberately run through one another, so the eye can switch between H and M instead of resolving one as a container for the other.

Amber/sky is the canonical palette: BRNRD amber `#ff9a1f` plus cold sky `#69c7df`. The earlier coral/turquoise exploration is retired from generated assets; git history is its archive.

## Registers

The geometry is shared, but screen and print use different materials on purpose.

### Screen — emitted light

`hugimuni-amber-sky.svg` is the master screen mark. It is transparent and expects a dark host surface. The mark uses screen-blended amber/sky strokes, a restrained warm neutral shared-stem core, a modest bloom pass, thin hot cores, and phosphor texture masked onto the strokes.

The screen treatment is intentionally quieter than the first emissive draft:

- shared-stem neutral core: `16` instead of the old `26`
- bloom: `6px` at `0.58` opacity instead of `9px` at `0.9`
- hot cores: ~14% of stroke width at `0.48` opacity
- phosphor grain: `42/100`, with weaker overlay/scanline multipliers

The retro-computer texture should be noticed after the monogram, not before it.

`hugimuni-amber-sky-icon.svg` is the same screen mark on the near-black rounded board. The board is a composition, not part of the master logo.

### Flat — no filters

`hugimuni-amber-sky-flat.svg` is a transparent, filter-free vector fallback. Use it when SVG filters or bloom are undesirable, at very small sizes, or as a neutral handoff surface.

### Print — ink, not fake neon

`hugimuni-amber-sky.eps` and `hugimuni-amber-sky-lockup.eps` are Level-2 process-CMYK EPS files for physical production. `*-print.svg` files are portable visual proofs of that register.

The print register does **not** imitate glow, scanlines, or grain. The old print draft put a white tube through every crossbar and diagonal; at distance that turned the mark into a generic white monoline logo with coloured shadows. The current register instead keeps:

- full amber H and sky M strokes
- chromatic displacement on the shared stems
- a much narrower `10`-unit warm neutral stem core
- six small local pale highlight segments at the actual authored H/M crossings

Those local marks are deliberately short lines, not dots: dots read as rivets; short segments read as a material overlap / registration event.

`PRINT_BLACK` is explicit in `build.py` and intentionally defaults to process black. For a real stand or large-format job, replace it with the printer/RIP profile's requested rich black if the vendor specifies one. Do not invent a rich-black recipe here.

The lockup still uses PostScript base Helvetica for `UGI` / `UNI`. Outline the type in the final imposition/preflight application if the printer requires all type converted to paths.

## Deliverables

Running the generator writes the canonical family:

- `hugimuni-amber-sky.svg` — transparent emissive screen master
- `hugimuni-amber-sky-icon.svg` — screen mark on the rounded dark board
- `hugimuni-amber-sky-flat.svg` — transparent filter-free vector fallback
- `hugimuni-amber-sky.eps` — CMYK print mark
- `hugimuni-amber-sky-lockup.eps` — CMYK conference lockup
- `hugimuni-amber-sky-print.svg` — print mark proof
- `hugimuni-amber-sky-print-lockup.svg` — print lockup proof

For a printer that asks for PDF/X-4, export the EPS/outlined lockup through the final layout/imposition tool using the printer's actual ICC/profile settings. `build.py` deliberately does not guess production colour management.

```bash
python3 media/brand/hugimuni/build.py
```

## Geometry

The current geometry remains the authored one:

- stems: `x=152` and `x=360`, `y=156..356`
- H bar: `y=276`, overhanging each stem by `20`
- M shoulders: `20` outside the stems
- M legs cross rather than merely meeting at a V, with `TAIL=20`
- H/M weave has six derived crossing points; the print highlights are computed from the line intersections rather than hard-coded coordinates

That last rule matters. If the geometry moves, the print weave follows it automatically instead of leaving decorative highlights behind.
