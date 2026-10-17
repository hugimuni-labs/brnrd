# Sources

Real observational data used as anchor material in the mythos. Nothing here
is committed to the repo: `src/sources.py` fetches it at render time into
`out/sources/` (gitignored). Each asset is cropped hard, graded into a
scientific channel colour, and cut against a procedural shot carrying the
same curve.

## How it is obtained

The SunPy project bundles a handful of real solar images in its Python wheel
for its own test suite (`sunpy/data/test/`). `sources.fetch()` runs
`pip download --no-deps sunpy` and extracts only the files listed below. This
route was chosen because the render box that made this cut could reach PyPI
but not NASA, ESA, Helioviewer or Git LFS hosts. Anyone with normal network
access can replace these with full-resolution originals from the archives
named below.

## Assets

| name | what | used as | origin | status / credit |
|------|------|---------|--------|-----------------|
| `eui174` | Solar Orbiter EUI / Full Sun Imager, 17.4 nm, 2022-04-01 00:00:45 UT, 3040×3072 JPEG2000 | `sun`: inside an east-limb active region's loops, pulled back to the whole disk; `sun_ha` / `sun_uv`: the same loops re-read in crimson and violet | Solar Orbiter EUI data release via Helioviewer; copy shipped in the SunPy wheel | **Verify before public release.** Solar Orbiter data are public, and EUI data releases state CC BY 4.0 — the license text for this specific release was not checked from here. Credit: *ESA & NASA / Solar Orbiter / EUI team* |
| `aia193` | SDO / AIA 19.3 nm, 2013-06-24 17:31:30 UT, 410×410 | (fetched, not yet cut in) | Helioviewer JP2 via the SunPy wheel | NASA SDO data: public domain. Credit: *NASA/SDO and the AIA science team* |
| `hmi_cont` | SDO / HMI continuum intensity, 2023-01-31 03:39:23 UT, 512×512 | (fetched, not yet cut in) | Helioviewer JP2 via the SunPy wheel | NASA SDO data: public domain. Credit: *NASA/SDO and the HMI science team* |
| `aia171_raw` | SDO / AIA 17.1 nm level 1, downsampled to 128×128 | `raw`: a detector frame at its own pixels — the "almost ugly" measurement insert | SunPy test data | NASA SDO data: public domain. Credit: *NASA/SDO and the AIA science team* |
| `eit195_raw` | SOHO / EIT 19.5 nm, 2004-03-01 00:00:10 UT, 128×128 | `raw_eit`: one-frame insert inside the scale collapse | SunPy test data | SOHO is ESA/NASA; EIT data are freely usable with credit: *SOHO/EIT (ESA & NASA)* |

The SunPy wheel itself is BSD-2-Clause; that licence covers SunPy's code, not
the observations, whose terms are listed per row above.

## Slots: footage this cut wants and does not have

If an asset cannot be fetched, the shot renders a dark frame with a thin
registration cross instead — a visible hole, never a silent procedural
stand-in. Footage the treatment calls for that is not yet sourced:

1. **AIA 171 / 304 movie of a limb prominence or flare** (SDO, 12 s cadence) —
   would replace the still `eui174` loops with real motion. NASA SVS and
   Helioviewer both serve these; public domain.
2. **A scientific accretion-disk visualisation** (e.g. NASA SVS black-hole
   disk renders) — to stand beside the procedural lensed disk and blur the
   line between them.
3. **A ring-system image** (Cassini ISS, public domain) for one frame beside
   the procedural `belt` reveal.
4. **High-cadence lightning or Lichtenberg-figure footage** — for a
   1–2-frame insert beside the generated discharge.
