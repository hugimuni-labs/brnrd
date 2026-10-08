"""Real scientific source material: observed universe, not simulation.

A few anchors of actual solar data sit between the generated shots, so the
viewer stops being able to tell observation from reconstruction from myth.
They are never inserted as stock footage: each is cropped hard, graded into
the film's palette or a scientific channel colour, and cut against a
procedural shot that carries the same curve.

Where they come from: the SunPy project ships a handful of real solar images
inside its Python wheel (``sunpy/data/test``) for its own tests. PyPI is
reachable from a locked-down render box when NASA, ESA and Git LFS hosts are
not, so ``fetch()`` downloads that wheel and extracts only the images listed
in ``ASSETS``. Every asset is documented in ``../SOURCES.md`` (origin,
instrument, date, licence status, credit line).

If an asset cannot be had, ``load()`` returns ``None`` and the shot that
wanted it renders an explicit *slot*: a dark frame with a thin registration
cross where the footage belongs, so a missing source is visible in the cut
rather than silently replaced by a drawing of the Sun.
"""
from __future__ import annotations

import glob
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np

import world as Wd

SRC = Wd.OUT / "sources"

# name: (path inside the sunpy wheel, what it is)
ASSETS = {
    "eui174": ("sunpy/data/test/2022_04_01__00_00_45__SOLO-EUI-FSI_EUI_FSI_174.jp2",
               "Solar Orbiter EUI/FSI 17.4 nm full disk, 2022-04-01 00:00:45 UT, 3040×3072"),
    "aia193": ("sunpy/data/test/2013_06_24__17_31_30_84__SDO_AIA_AIA_193.jp2",
               "SDO/AIA 19.3 nm full disk, 2013-06-24 17:31:30 UT, 410×410 (Helioviewer JP2)"),
    "hmi_cont": ("sunpy/data/test/2023_01_31__03_39_23_200__SDO_HMI_HMI_continuum.jp2",
                 "SDO/HMI continuum intensity, 2023-01-31 03:39:23 UT, 512×512"),
    "aia171_raw": ("sunpy/data/test/aia_171_level1.fits",
                   "SDO/AIA 17.1 nm level-1, downsampled to 128×128 by SunPy"),
    "eit195_raw": ("sunpy/data/test/EIT/efz20040301.000010_s.fits",
                   "SOHO/EIT 19.5 nm, 2004-03-01 00:00:10 UT, 128×128"),
}


def fetch(force=False) -> dict:
    """Download the sunpy wheel from PyPI (no dependencies) and extract the
    listed assets as float32 .npy under out/sources. Returns {name: ok}."""
    SRC.mkdir(parents=True, exist_ok=True)
    want = {k for k in ASSETS if force or not (SRC / f"{k}.npy").exists()}
    if not want:
        return {k: True for k in ASSETS}
    wheel_dir = SRC / "wheel"
    wheel_dir.mkdir(exist_ok=True)
    wheels = glob.glob(str(wheel_dir / "sunpy-*.whl"))
    if not wheels:
        r = subprocess.run([sys.executable, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:",
                            "sunpy", "-d", str(wheel_dir)], capture_output=True, text=True)
        wheels = glob.glob(str(wheel_dir / "sunpy-*.whl"))
        if not wheels:
            print("sources: could not download the sunpy wheel; shots will render slots\n" + r.stderr[-400:])
            return {k: False for k in ASSETS}
    z = zipfile.ZipFile(wheels[0])
    names = set(z.namelist())
    out = {}
    for k, (path, _) in ASSETS.items():
        if k not in want:
            out[k] = True
            continue
        if path not in names:
            out[k] = False
            continue
        raw = z.read(path)
        try:
            arr = _decode(path, raw)
        except Exception as e:  # missing JPEG2000 or FITS support
            print(f"sources: cannot decode {k}: {e}")
            out[k] = False
            continue
        np.save(SRC / f"{k}.npy", arr.astype(np.float32))
        out[k] = True
    return out


def _decode(path, raw):
    if path.endswith(".jp2"):
        from PIL import Image
        return np.asarray(Image.open(io.BytesIO(raw))).astype(np.float32)
    from astropy.io import fits  # only needed for the two raw inserts
    with fits.open(io.BytesIO(raw)) as h:
        for hdu in h:
            if hdu.data is not None:
                return np.nan_to_num(np.asarray(hdu.data, np.float32))
    raise ValueError("no image HDU")


def load(name):
    p = SRC / f"{name}.npy"
    if not p.exists():
        fetch()
    return np.load(p) if p.exists() else None


def normalise(img, lo=0.5, hi=99.85, gamma=0.55):
    a = np.percentile(img, lo)
    b = np.percentile(img, hi)
    return (np.clip((img - a) / (b - a + 1e-9), 0, 1) ** gamma).astype(np.float32)


# Where the anchors live inside each image (px in the source), found by eye
# once and recorded here so shots are deterministic.
EUI_DISK = (2070.0, 1757.0, 640.0)    # centre x, y, radius of the solar disk
EUI_LOOPS = (1555.0, 1590.0)           # an active region with loops on the east limb


if __name__ == "__main__":
    print(fetch())
