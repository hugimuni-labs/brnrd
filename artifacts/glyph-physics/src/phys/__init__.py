"""Physics generators. Each finds glyphs its own way; none is handed a picture.

| module      | phenomenon                         | what the glyph becomes to it        |
|-------------|------------------------------------|-------------------------------------|
| chladni     | grains on a driven membrane        | a target envelope fit by plate modes |
| breakdown   | dielectric breakdown / venation    | an attractor set a channel grows to  |
| field       | iron filings between magnetic poles| pole placement                       |
| rd          | Gray–Scott reaction–diffusion      | a feed/kill landscape                |
| holo        | coherent light through an aperture | the aperture; focus is the reading   |
| swarm       | charged dust in a potential (Langevin) | a potential well                 |
| mach        | waves from a supersonic source (schlieren) | a trajectory                 |
| press       | relief pressed into dark matter    | a die                                |
"""
from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np


@lru_cache(maxsize=16)
def fbm(w: int, h: int, seed: int = 0, octaves: int = 6, base: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        n = base * 2 ** o
        g = rng.normal(size=(max(2, n * h // w), n)).astype(np.float32)
        acc += amp * cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)
        tot += amp
        amp *= 0.55
    acc /= tot
    return (acc - acc.mean()) / (acc.std() + 1e-6)


def sample(field: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = field.shape[:2]
    xi = np.clip(x.astype(np.int32), 0, w - 1)
    yi = np.clip(y.astype(np.int32), 0, h - 1)
    return field[yi, xi]


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)
