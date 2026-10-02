"""A diegetic score derived from the edit list.

Nothing is scored *over* the picture: every sound is the phenomenon on screen.
The membrane's drive tone is the frequency moving the grains; the filings
rattle on each tap; the leader crackles and the return stroke cracks; the
press lands; the wire keys `brnrd` in Morse. Black frames are silence — the
cut takes the sound with it, which is most of what makes a flash aggressive.
"""
from __future__ import annotations

import math
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 48_000
FPS = 24
SPF = SR // FPS


def _env(n, a=0.002, d=0.2):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / d)


def _noise(rng, n):
    return rng.normal(0, 1, n).astype(np.float32)


def _lp(x, a):
    """One-pole low-pass, a ∈ (0,1): smaller = darker."""
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):  # short buffers only
        acc += a * (x[i] - acc)
        y[i] = acc
    return y


def _lp_fast(x, k):
    """Moving-average low-pass for long buffers."""
    if k <= 1:
        return x
    c = np.cumsum(np.concatenate([[0], x]))
    y = (c[k:] - c[:-k]) / k
    return np.concatenate([y, np.full(k - 1, y[-1])]).astype(np.float32)


def write(E, path):
    n = len(E) * SPF
    L = np.zeros(n, np.float32)
    R = np.zeros(n, np.float32)
    rng = np.random.default_rng(7)
    t = np.arange(n) / SR

    def add(i0, sig, pan=0.0, g=1.0):
        i1 = min(n, i0 + len(sig))
        if i0 >= n or i1 <= i0:
            return
        s = sig[: i1 - i0] * g
        L[i0:i1] += s * math.sqrt(0.5 * (1 - pan))
        R[i0:i1] += s * math.sqrt(0.5 * (1 + pan))

    # Per-frame gates for continuous beds.
    names = [e["src"][0] for e in E]
    locs = [e["src"][1] for e in E]
    gate = {}
    for nm in set(names):
        g = np.repeat(np.array([1.0 if x == nm else 0.0 for x in names], np.float32), SPF)
        gate[nm] = _lp_fast(g, 240)

    # Bed: a low beating drone under the whole mythos, ducked on black.
    alive = np.repeat(np.array([0.0 if x == "black" else 1.0 for x in names], np.float32), SPF)
    alive = _lp_fast(alive, 96)
    drone = (np.sin(2 * np.pi * 41.2 * t) + 0.7 * np.sin(2 * np.pi * 41.9 * t + 1) +
             0.35 * np.sin(2 * np.pi * 82.6 * t) + 0.15 * np.sin(2 * np.pi * 123.8 * t + 2))
    swell = 0.5 + 0.5 * np.clip(t / (n / SR), 0, 1)
    add(0, (drone * 0.10 * swell * alive).astype(np.float32))
    air = _lp_fast(_noise(rng, n), 30) * 0.5 * (gate["dust"] + 0.4 * gate.get("holo", 0))
    add(0, air * alive, 0.0, 0.5)

    # Membrane drive: a sine whose pitch follows the shot's mode number.
    for nm, base in (("chl", 210.0), ("word", 180.0)):
        if nm not in gate:
            continue
        loc = np.repeat(np.array([l if x == nm else 0 for x, l in zip(names, locs)], np.float32), SPF)
        f = base * (1 + 0.012 * loc)
        ph = 2 * np.pi * np.cumsum(f) / SR
        tone = np.sin(ph) + 0.3 * np.sin(2 * ph) + 0.12 * np.sin(3.01 * ph)
        hiss = _lp_fast(_noise(rng, n), 2) * 0.4  # grains chattering
        add(0, ((0.10 * tone + 0.12 * hiss * (0.5 + 0.5 * np.sin(ph / 7))) * gate[nm] * alive).astype(np.float32))

    # Holography: sodium hum.
    if "holo" in gate:
        hum = sum(np.sin(2 * np.pi * 58.9 * h * t) / h for h in (1, 2, 3, 5, 8))
        add(0, (0.06 * hum * gate["holo"] * alive).astype(np.float32))

    # Current: a gated buzz.
    if "current" in gate:
        saw = ((t * 100) % 1.0 - 0.5) * 2
        add(0, (0.06 * saw * gate["current"] * (0.6 + 0.4 * np.sin(2 * np.pi * 1.5 * t) ** 2)).astype(np.float32))

    prev = None
    for i, e in enumerate(E):
        nm, k = e["src"]
        i0 = i * SPF
        changed = (nm, k) != prev and (prev is None or prev[0] != nm or nm in ("bar", "math"))
        prev = (nm, k)
        if changed and nm not in ("black",):
            # Every cut is a transient: a hard tick whose colour follows the shot.
            n_ = int(0.03 * SR)
            tick = _noise(rng, n_) * _env(n_, 0.0005, 0.008)
            add(i0, tick, rng.uniform(-0.6, 0.6), 0.35)
        if nm == "white":
            n_ = int(0.5 * SR)
            add(i0, _noise(rng, n_) * _env(n_, 0.001, 0.06), 0, 0.6)
        if nm == "crack":
            n_ = SPF
            if 6 <= k < 39:
                cr = (rng.random(n_) < 0.004 + 0.02 * (k / 39)).astype(np.float32) * rng.normal(0, 1, n_)
                add(i0, cr, rng.uniform(-0.4, 0.4), 0.8)
            if k == 40:
                m = int(1.8 * SR)
                boom = np.sin(2 * np.pi * 38 * np.arange(m) / SR) * _env(m, 0.003, 0.5)
                add(i0, (_noise(rng, m) * _env(m, 0.0005, 0.12) * 0.8 + boom).astype(np.float32), 0, 0.9)
        if nm in ("mach_v", "mach_up", "mach_y") and k in (0,):
            m = int(1.4 * SR)
            sweep = _lp_fast(_noise(rng, m), 12) * np.linspace(0, 1, m) ** 2
            add(i0, sweep.astype(np.float32), -0.3 if nm == "mach_up" else 0.3, 0.9)
        if nm in ("mach_v", "mach_up") and k == 24:
            m = int(0.25 * SR)
            nwave = np.where(np.arange(m) < m // 2, 1.0, -1.0) * _env(m, 0.0005, 0.08)
            add(i0, nwave.astype(np.float32), 0, 0.6)
        if nm in ("field_n", "field_m") and k % 3 == 0:
            m = int(0.12 * SR)
            rattle = _noise(rng, m) * _env(m, 0.0005, 0.02 if k % 12 else 0.05)
            add(i0, rattle * (rng.random(m) < 0.35), rng.uniform(-0.5, 0.5), 0.45 if k % 12 else 0.8)
        if nm in ("chl", "word") and (k % 9 == 0 and k < 46 and nm == "chl" or k in (6, 14, 22) and nm == "word"
                                      or nm == "chl" and k >= 150 and (k - 150) % 6 == 0):
            m = int(0.6 * SR)
            thud = np.sin(2 * np.pi * 55 * np.arange(m) / SR) * _env(m, 0.002, 0.15)
            add(i0, (thud + 0.3 * _noise(rng, m) * _env(m, 0.0005, 0.03)).astype(np.float32), 0, 0.7)
        if nm == "math":
            m = int(0.07 * SR)
            blip = np.sin(2 * np.pi * (1800 + 400 * (k % 5)) * np.arange(m) / SR) * _env(m, 0.0005, 0.03)
            add(i0, blip.astype(np.float32), 0.5 if k % 2 else -0.5, 0.25)
        if nm == "bar" and changed:
            m = int(0.09 * SR)
            f = 90 + 40 * (k % 7)
            body = np.sin(2 * np.pi * f * np.arange(m) / SR) * _env(m, 0.0008, 0.05)
            add(i0, body.astype(np.float32), rng.uniform(-0.7, 0.7), 0.45)
        if nm == "press" and k == 12:
            m = int(2.5 * SR)
            tt = np.arange(m) / SR
            sub = np.sin(2 * np.pi * (30 + 30 * np.exp(-tt * 12)) * tt) * _env(m, 0.001, 0.7)
            ring = sum(np.sin(2 * np.pi * f * tt) for f in (411, 667, 1013)) * _env(m, 0.001, 0.4) * 0.1
            add(i0, (sub + ring + _noise(rng, m) * _env(m, 0.0003, 0.05)).astype(np.float32), 0, 1.0)
        if nm == "word" and k >= 96:
            m = SPF
            add(i0, _noise(rng, m) * 0.15 * (k - 96) / 22, rng.uniform(-0.6, 0.6), 0.5)

    # Telegraph: key the Morse as the pulses leave the transmitter.
    import shots
    seq, _ = shots.morse_train()
    starts = [i for i, e in enumerate(E) if e["src"][0] == "tele" and e["src"][1] == 0]
    if starts:
        s0 = starts[0] * SPF
        for t0, d in seq:
            m = d * SPF
            tt = np.arange(m) / SR
            key = np.sin(2 * np.pi * 740 * tt) * np.minimum(1, np.minimum(tt, (m / SR - tt)) / 0.004)
            add(s0 + t0 * SPF, (0.25 * key).astype(np.float32), 0.0)

    # Silence on black frames: the cut takes the sound with it.
    black = np.repeat(np.array([0.0 if x == "black" else 1.0 for x in names], np.float32), SPF)
    black = _lp_fast(black, 48)
    L *= black
    R *= black
    st = np.stack([L, R], 1)
    st /= np.abs(st).max() + 1e-9
    raw = Path(path).with_suffix(".raw.wav")
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((st * 0.9 * 32767).astype(np.int16).tobytes())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
                    "loudnorm=I=-16:TP=-1.5:LRA=14", "-ar", str(SR), str(path)], check=True)
    raw.unlink()
