"""A diegetic score derived from the edit list.

Nothing is scored *over* the picture. Every sound is the phenomenon on screen,
heard by the same instrument that films it: the web's radiation is hiss; the
disk's shear is two low tones beating; the corona hums and snaps when it
reconnects; the crystal ticks as it grows; the leader crackles and the return
stroke cracks; the membrane's drive tone *is* the frequency moving the grains
(``world.drive_freq``, transposed into hearing); the raster acquisition steps;
cutting the drive is a silence you can hear. The press lands; the layout
buzzes; the wire keys one pulse. Black frames are silence — the cut takes the
sound with it, which is most of what makes a flash aggressive.
"""
from __future__ import annotations

import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import world as Wd  # noqa: E402
from shots import cosmos as SC  # noqa: E402
from shots import inherit as SI  # noqa: E402

SR = 48_000
FPS = 24
SPF = SR // FPS


def _env(n, a=0.002, d=0.2):
    t = np.arange(n) / SR
    return (np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / d)).astype(np.float32)


def _lp(x, k):
    """Moving-average low-pass (k samples)."""
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

    names = [e["src"][0] for e in E]
    locs = np.array([e["src"][1] for e in E], np.float32)

    def gate(*shots, smooth=96):
        g = np.repeat(np.array([1.0 if x in shots else 0.0 for x in names], np.float32), SPF)
        return _lp(g, smooth)

    def local(shot):
        """Per-sample local frame of ``shot`` (−1 elsewhere)."""
        v = np.array([l if x == shot else -1 for x, l in zip(names, locs)], np.float32)
        return np.repeat(v, SPF)

    noise = rng.normal(0, 1, n).astype(np.float32)

    # Web: radiation hiss, a sub swell that grows with collapse, the ignition.
    g = gate("web")
    lw = local("web")
    hiss = (noise - _lp(noise, 6)) * 0.05
    sub = np.sin(2 * np.pi * 31 * t) * np.clip(lw / SC.WEB_LEN, 0, 1) ** 2 * 0.25
    ign = np.clip((lw - (SC.IGNITE_AT - 6)) / 20, 0, 1)
    rise = np.sin(2 * np.pi * np.cumsum(60 + 900 * ign ** 2) / SR) * ign ** 2 * 0.2
    add(0, (hiss + sub + rise + _lp(noise, 3) * ign ** 3 * 0.6) * g)

    # Disk: shear as two low tones beating, ringed by its own hiss.
    g = gate("disk")
    drone = (np.sin(2 * np.pi * 36.0 * t) + 0.8 * np.sin(2 * np.pi * 37.3 * t + 1.0)
             + 0.25 * np.sin(2 * np.pi * 73.1 * t)) * 0.16
    add(0, (drone + _lp(noise, 40) * 0.25) * g)

    # Corona: mains-like hum of a magnetised plasma, crackle in the loops.
    g = gate("corona", "corona_uv")
    hum = sum(np.sin(2 * np.pi * 55 * h * t + h) / h for h in (1, 2, 3, 5, 7)) * 0.07
    crack = (rng.random(n) < 0.0015).astype(np.float32) * rng.normal(0, 1, n).astype(np.float32)
    crack = _lp(crack, 3) * 0.9
    add(0, (hum + crack) * g)

    # Crystal: glassy ticks, denser as it grows.
    g = gate("crystal", smooth=24)
    lc = local("crystal")
    dens = np.clip(lc / SC.CRYSTAL_LEN, 0, 1)
    pings = np.zeros(n, np.float32)
    idx = np.flatnonzero((rng.random(n) < 0.0009 * (0.2 + dens)) & (lc >= 0))
    for i0 in idx:
        m = int(0.03 * SR)
        f = rng.uniform(2800, 6200)
        s = np.sin(2 * np.pi * f * np.arange(m) / SR) * _env(m, 0.0003, 0.008)
        pings[i0:i0 + m] += s[: max(0, min(m, n - i0))] * 0.4
    add(0, pings * g, 0.2)

    # Plate: everything the membrane does.
    lp = local("plate")
    g = gate("plate", smooth=24)
    # Leader crackle, growing toward the stroke.
    lead = np.clip((lp - 3) / (Wd.STRIKE_RS - 3), 0, 1) * (lp < Wd.STRIKE_RS) * (lp >= 0)
    cr = (rng.random(n) < 0.002 + 0.03 * lead).astype(np.float32) * rng.normal(0, 1, n).astype(np.float32)
    add(0, cr * lead * 0.9 * g)
    # Drive tone: the membrane's own frequency, transposed ×9000 into hearing,
    # with the grains' chatter riding its amplitude.
    fd = np.array([Wd.drive_freq(int(k)) if k >= 0 else 0.0 for k in lp[::SPF]], np.float32)
    ad = np.array([Wd.drive_amp(int(k)) if k >= 0 else 0.0 for k in lp[::SPF]], np.float32)
    fd = _lp(np.repeat(fd, SPF), 480) * 9000
    ad = _lp(np.repeat(ad, SPF), 240)
    ph = 2 * np.pi * np.cumsum(fd) / SR
    tone = (np.sin(ph) + 0.35 * np.sin(2 * ph) + 0.12 * np.sin(3.01 * ph)) * 0.16
    chatter = (noise - _lp(noise, 2)) * (0.5 + 0.5 * np.sin(ph / 5)) * 0.12
    add(0, (tone + chatter) * ad * g)
    # Embers in the stare.
    rest = ((lp >= Wd.REST[0]) & (lp < Wd.VIBRATE[0])).astype(np.float32)
    emb = (rng.random(n) < 0.0004).astype(np.float32) * rng.normal(0, 1, n).astype(np.float32)
    add(0, _lp(emb, 4) * rest * 0.5 * g, -0.2)
    # Raster: a stepping acquisition, pitch rising row by row.
    scan = ((lp >= 214) & (lp < 246)).astype(np.float32)
    step_f = 320 + 900 * np.clip((lp - 214) / 32, 0, 1)
    stepper = np.sign(np.sin(2 * np.pi * 38 * t)) * np.sin(2 * np.pi * np.cumsum(step_f) / SR) * 0.05
    add(0, stepper * scan * g, 0.3)
    # Polariser: a thin shimmer while tension shows as colour.
    pol = ((lp >= 196) & (lp < 224)).astype(np.float32)
    add(0, (np.sin(2 * np.pi * 2093 * t) * np.sin(2 * np.pi * 0.7 * t) ** 2 * 0.03) * _lp(pol, 2400) * g)

    # Belt: ice and dust grinding at orbital speed — a fine, wide hiss.
    g = gate("belt")
    add(0, ((noise - _lp(noise, 3)) * 0.06 + _lp(noise, 60) * 0.3) * g, 0.1)
    # The observed Sun: a lower, older drone than the disk's, and the EUV
    # detector's own faint whine.
    g = gate("sun", "sun_ha", "sun_uv")
    add(0, (np.sin(2 * np.pi * 27.5 * t) * 0.2 + np.sin(2 * np.pi * 27.5 * 3.01 * t) * 0.05
            + np.sin(2 * np.pi * 7400 * t) * 0.006) * g)
    # Mask: the UV lamp's ballast hum. Etch: fizz.
    g = gate("mask")
    add(0, (np.sin(2 * np.pi * 120 * t) * 0.05 + np.sin(2 * np.pi * 240 * t) * 0.02) * g)
    g = gate("etch")
    fizz = (rng.random(n) < 0.02).astype(np.float32) * rng.normal(0, 1, n).astype(np.float32)
    add(0, (_lp(fizz, 2) * 0.35 + _lp(noise, 200) * 0.4) * g)

    # Capture: the developer, a slow liquid movement.
    g = gate("capture")
    slosh = _lp(noise, 300) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.6 * t)) * 0.8
    add(0, slosh * g)
    # Conduct: current buzz, gated by the pulse.
    g = gate("conduct")
    saw = ((t * 100) % 1.0 - 0.5) * 2
    add(0, saw * 0.05 * (0.5 + 0.5 * np.sin(2 * np.pi * 1.1 * t) ** 2) * g)
    # Thread: fibre creak around the pulse.
    g = gate("thread")
    add(0, _lp(noise, 20) * 0.35 * g)

    # Events.
    prev = None
    wire0 = None
    for i, e in enumerate(E):
        nm, k = e["src"]
        i0 = i * SPF
        if prev is not None and prev[0] != nm and nm != "black":
            # Every cut is a transient whose colour follows the shot.
            m = int(0.025 * SR)
            add(i0, rng.normal(0, 1, m).astype(np.float32) * _env(m, 0.0004, 0.006), rng.uniform(-0.6, 0.6), 0.3)
        if "zoom" in e and (prev is None or prev != (nm, k, e.get("zoom"))):
            m = int(0.05 * SR)  # objective turret
            add(i0, (np.sin(2 * np.pi * 1400 * np.arange(m) / SR) * _env(m, 0.0003, 0.01)
                     + rng.normal(0, 0.4, m) * _env(m, 0.0003, 0.004)).astype(np.float32), 0.4, 0.5)
        if nm == "disk" and k == 0:
            m = int(2.5 * SR)
            tt = np.arange(m) / SR
            add(i0, (np.sin(2 * np.pi * (24 + 40 * np.exp(-tt * 6)) * tt) * _env(m, 0.002, 0.9)).astype(np.float32), 0, 1.0)
        if nm in ("corona_uv",) or (nm == "plate" and "align" in e):
            m = int(0.4 * SR)
            add(i0, (rng.normal(0, 1, m) * _env(m, 0.0003, 0.03)
                     + np.sin(2 * np.pi * 48 * np.arange(m) / SR) * _env(m, 0.002, 0.15)).astype(np.float32), 0, 0.7)
        if nm == "plate" and k == Wd.STRIKE_RS:
            m = int(3.0 * SR)
            tt = np.arange(m) / SR
            boom = np.sin(2 * np.pi * (30 + 25 * np.exp(-tt * 8)) * tt) * _env(m, 0.003, 0.9)
            add(i0, (rng.normal(0, 1, m) * _env(m, 0.0003, 0.12) * 0.9 + boom).astype(np.float32), 0, 1.0)
        if nm == "plate" and k == Wd.FREEZE:
            m = int(0.3 * SR)  # the drive cut: a relay, then the ring-down is silence
            add(i0, (rng.normal(0, 1, m) * _env(m, 0.0002, 0.005)).astype(np.float32), 0.5, 0.6)
        if nm == "plate" and 246 <= k < 258 and k % 2 == 0:
            m = int(0.08 * SR)  # slit jaws
            add(i0, _lp(rng.normal(0, 1, m).astype(np.float32), 8) * _env(m, 0.002, 0.04), -0.3, 0.5)
        if nm == "capture" and k == 0:
            m = int(0.6 * SR)
            add(i0, (rng.normal(0, 1, m) * _env(m, 0.0003, 0.01) + np.sin(2 * np.pi * 100 * np.arange(m) / SR)
                     * _env(m, 0.002, 0.25) * 0.4).astype(np.float32), 0, 0.6)
        if nm == "capture" and 30 <= k < 44:
            m = SPF  # grease pencil on polymer
            add(i0, _lp(rng.normal(0, 1, m).astype(np.float32), 2) * 0.12, 0.2)
        if nm == "press" and k == SI.IMPACT:
            m = int(2.5 * SR)
            tt = np.arange(m) / SR
            sub = np.sin(2 * np.pi * (30 + 30 * np.exp(-tt * 12)) * tt) * _env(m, 0.001, 0.7)
            ring = sum(np.sin(2 * np.pi * f * tt) for f in (411, 667, 1013)) * _env(m, 0.001, 0.4) * 0.1
            add(i0, (sub + ring + rng.normal(0, 1, m) * _env(m, 0.0003, 0.05)).astype(np.float32), 0, 1.0)
        if nm == "conduct" and 8 <= k < 18 and k % 2 == 0:
            m = int(0.02 * SR)  # lithographic snap: relays
            add(i0, rng.normal(0, 1, m).astype(np.float32) * _env(m, 0.0002, 0.003), rng.uniform(-0.7, 0.7), 0.4)
        if nm in ("raw", "raw_eit"):
            m = int(0.04 * SR)  # a detector readout: a digital click
            add(i0, np.sign(np.sin(2 * np.pi * 2000 * np.arange(m) / SR)).astype(np.float32) * _env(m, 0.0002, 0.01), 0.5, 0.25)
        rec = e.get("rec")
        if rec and (prev is None or prev[0] != nm or E[i - 1].get("rec") is None):
            if rec["mode"] == "replace" and rec.get("carrier") == "type":
                m = int(0.25 * SR)  # lead on a stone: a dense clack
                tt = np.arange(m) / SR
                add(i0, (np.sin(2 * np.pi * 1800 * tt) * _env(m, 0.0002, 0.02) * 0.5
                         + rng.normal(0, 1, m) * _env(m, 0.0002, 0.006)).astype(np.float32), 0.2, 0.7)
            elif rec["mode"] == "replace":
                m = int(0.35 * SR)  # a chisel scrape
                add(i0, (_lp(rng.normal(0, 1, m).astype(np.float32), 3) * _env(m, 0.01, 0.12)), -0.2, 0.6)
        if rec and rec["mode"] == "ghost":
            m = int(0.05 * SR)  # a guess: the faintest tick, a different pitch each time
            f = 3200 + 500 * rec.get("j", 0)
            add(i0, (np.sin(2 * np.pi * f * np.arange(m) / SR) * _env(m, 0.0003, 0.012)).astype(np.float32), 0.0, 0.12)
        if nm == "wire" and k == 0:
            wire0 = i0
        prev = (nm, k, e.get("zoom"))

    # The wire: key down, one pulse's tone for as long as it is on the wire,
    # then the second pulse begins and the black takes it.
    if wire0 is not None:
        m = int(0.03 * SR)
        add(wire0, rng.normal(0, 1, m).astype(np.float32) * _env(m, 0.0002, 0.004), 0, 0.8)
        for t0, dur in ((0, 26), (Wd.RHYTHM * 2, 20)):
            m = dur * SPF
            tt = np.arange(m) / SR
            key = np.sin(2 * np.pi * 740 * tt) * np.minimum(1, np.minimum(tt, (m / SR - tt)) / 0.004)
            add(wire0 + t0 * SPF, (0.18 * key).astype(np.float32), 0.0)

    # Silence on black frames: the cut takes the sound with it.
    alive = np.repeat(np.array([0.0 if x == "black" else 1.0 for x in names], np.float32), SPF)
    alive = _lp(alive, 48)
    L *= alive
    R *= alive
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
