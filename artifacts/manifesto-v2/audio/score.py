# Score for manifesto v2, synthesised from nothing and locked to the frame numbers in src/cut.ts (30 fps).
# usage: python3 audio/score.py public/score_raw.wav
import sys, numpy as np
from scipy.signal import butter, sosfilt
from scipy.io import wavfile
SR = 48000; FPS = 30
DROPS = [(72, 90), (104, 126), (895, 912)]  # must match DROPS in src/cut.ts
DUR = (1260 - sum(y - x + 1 for x, y in DROPS)) / FPS; N = int(SR * DUR)
def outf(f):  # source frame -> output frame (events inside a dropped span snap to its cut)
    o = f
    for x, y in DROPS:
        if f > y: o -= y - x + 1
        elif f >= x: o -= f - x
    return o
L = np.zeros(N); R = np.zeros(N); rng = np.random.default_rng(11)
fr = lambda f: outf(f) / FPS
def add(sig, at, g=1.0, pan=0.0):
    i = int(at * SR); sig = sig[: max(0, N - i)]
    if len(sig) == 0: return
    L[i:i + len(sig)] += sig * g * np.sqrt(0.5 * (1 - pan)); R[i:i + len(sig)] += sig * g * np.sqrt(0.5 * (1 + pan))
def env(n, a=0.005, d=0.3): t = np.arange(n) / SR; return np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / d)
def lp(x, f, o=2): return sosfilt(butter(o, f / (SR / 2), 'low', output='sos'), x)
def hp(x, f, o=2): return sosfilt(butter(o, f / (SR / 2), 'high', output='sos'), x)
def bp(x, lo, hi): return sosfilt(butter(2, [lo / (SR / 2), hi / (SR / 2)], 'band', output='sos'), x)
def T(d): return np.arange(int(d * SR)) / SR
def sine(f, d): return np.sin(2 * np.pi * f * T(d))
def noise(d): return rng.standard_normal(int(d * SR))
def fm(f, ratio, idx, d, dec=0.4):
    t = T(d); e = env(len(t), 0.002, dec); return np.sin(2 * np.pi * f * t + idx * e * np.sin(2 * np.pi * f * ratio * t)) * e
def boom(d=2.5, f0=80, f1=30):
    t = T(d); f = f1 + (f0 - f1) * np.exp(-t * 7); return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), 0.002, d / 3)
def kick():
    k = boom(0.45, 140, 42) * 0.9; k[:480] += lp(noise(0.01), 3000) * 0.3; return k
def click(f=4000, d=0.012): return bp(noise(d), f * 0.6, min(f * 1.5, 20000)) * env(int(d * SR), 0.0003, d / 3)
def square(f, d): return np.sign(np.sin(2 * np.pi * f * T(d)))
def crush(x, bits=4, hold=8): y = np.repeat(x[::hold], hold)[: len(x)]; q = 2 ** bits; return np.round(y * q) / q
def pad(freqs, d, cut=900, det=0.004, fade=1.0):
    t = T(d); s = np.zeros_like(t)
    for f in freqs:
        for dd in (-det, 0, det): s += 2 * ((f * (1 + dd) * t) % 1) - 1
    s = lp(s / (len(freqs) * 3), cut); return s * np.minimum(1, np.minimum(t / fade, (d - t) / fade))
def riser(d, f0=300, f1=8000):
    n = noise(d); out = np.zeros_like(n); seg = int(0.03 * SR)
    for k in range(0, len(n), seg):
        u = k / len(n); fc = f0 * (f1 / f0) ** u; out[k:k + seg] = bp(n[k:k + seg + 1500], fc * 0.7, min(fc * 1.4, 20000))[: len(n[k:k + seg])]
    return out * (np.arange(len(n)) / len(n)) ** 2
def glitch(d, seed):
    r = np.random.default_rng(seed); n = int(d * SR); out = np.zeros(n); k = 0
    while k < n:
        L_ = int(r.integers(200, 2400)); f = r.choice([220, 440, 880, 1760, 3520, 110]) * r.uniform(0.9, 1.1)
        out[k:k + L_] = crush(np.sin(2 * np.pi * f * np.arange(L_) / SR), int(r.integers(2, 5)), int(r.integers(2, 12)))[: n - k] * r.uniform(0.3, 1); k += L_
    return out
# cut points of the bursts — every substitution gets a transient, each a different timbre
def hit(f, kind, g=0.5, pan=0.0):
    at = fr(f)
    if kind == 'click': add(click(int(rng.integers(1500, 9000))), at, g, pan)
    elif kind == 'blip': add(fm(float(rng.choice([660, 880, 1320, 1760])), 2.01, 3, 0.09, 0.03), at, g, pan)
    elif kind == 'thud': add(boom(0.35, 110, 50), at, g, pan)
    elif kind == 'crush': add(glitch(0.07, f), at, g, pan)
    elif kind == 'paper': add(hp(noise(0.08), 2500) * env(int(0.08 * SR), 0.001, 0.025), at, g, pan)
# ---- A. cold open
add(boom(1.6, 90, 32), 0, 0.9)
for f, k in [(3, 'paper'), (6, 'crush'), (8, 'thud'), (10, 'paper'), (13, 'click'), (16, 'blip'), (18, 'crush'), (21, 'click')]: hit(f, k, 0.55, 0.3 * np.sin(f))
# ---- B. codex: cold ticks, glass plinks at commits, sealed chord
for k in range(int((fr(137) - fr(22)) / 0.125)):
    at = fr(22) + k * 0.125; add(click(7000, 0.006), at, 0.22 if k % 4 == 0 else 0.08, 0.3 if k % 2 else -0.3)
add(sine(55, fr(137) - fr(22)) * np.minimum(1, T(fr(137) - fr(22)) / 1.5), fr(22), 0.18)
for i, f in enumerate([39, 45, 51, 55, 58]): add(fm(880 * [1, 1.125, 1.25, 1.5, 1.6875][i], 3.5, 4, 0.8, 0.25), fr(f), 0.2, -0.4 + i * 0.2)
hit(40, 'crush', 0.4); hit(47, 'thud', 0.5); hit(61, 'click', 0.5); hit(63, 'thud', 0.35)
add(fm(440, 2, 3, 2.0, 0.8) * 0.8 + fm(660, 2, 2, 2.0, 0.8) * 0.6, fr(92), 0.22)
hit(97, 'crush', 0.5); hit(100, 'thud', 0.3); hit(114, 'paper', 0.4)
add(pad([110, 164.8], fr(137) - fr(100), 700, fade=0.3), fr(100), 0.14)
add(square(1000, 0.066) * 0.25, fr(137), 0.5); add(riser(fr(143) - fr(130), 900, 9000), fr(130), 0.3)
add(boom(3.5, 95, 26), fr(144), 1.0); add(lp(noise(1.2), 2500) * env(int(1.2 * SR), 0.001, 0.22), fr(144), 0.6)
# ---- C. HOLD 1 — almost nothing: a low drone and the counter
add(lp(noise(fr(250) - fr(151)), 120) * 0.5 + sine(41.2, fr(250) - fr(151)) * 0.3, fr(151), 0.35)
for k in range(14): add(click(5000, 0.004), fr(151) + k * (fr(100) / 14), 0.06)
# ---- D. carried by hand
hit(251, 'thud', 0.7); add(bp(noise(0.12), 1500, 6000) * env(int(0.12 * SR), 0.005, 0.05), fr(256), 0.3)
for f in (267, 277): add(bp(noise(0.12), 400, 2400) * np.sin(np.linspace(0, np.pi, int(0.12 * SR))) ** 2, fr(f), 0.6); hit(f, 'crush', 0.35)
hit(280, 'thud', 0.7); add(riser(0.45, 300, 6000), fr(283), 0.25)
# ---- E. claude: warm, typographic
add(crush(square(523, 0.066), 3, 4) * env(int(0.066 * SR), 0.001, 0.05), fr(296), 0.35)
add(pad([110, 165, 220, 277], fr(397) - fr(298), 1400, fade=0.4), fr(298), 0.22)
add(fm(1046, 1.5, 2, 1.5, 0.6), fr(331), 0.2)
for f in (334, 350, 362): hit(f, 'thud', 0.4); add(fm(330, 1, 1, 0.6, 0.25), fr(f), 0.12)
hit(372, 'click', 0.4)
# ---- F. burn + mistral: crushed pulse, a cursor with no memory, the wrong release
add(glitch(fr(406) - fr(397), 3), fr(397), 0.4)
for k in range(int((fr(500) - fr(406)) / 0.125)):
    at = fr(406) + k * 0.125; add(crush(square(55 if k % 4 else 82.5, 0.1), 3, 16) * env(int(0.1 * SR), 0.001, 0.06), at, 0.3)
for f in range(432, 460, 4): hit(f, 'blip', 0.25, -0.5 if f % 8 else 0.5)
hit(460, 'crush', 0.5); add(boom(2.0, 120, 30), fr(469), 0.9); hit(471, 'crush', 0.6)
for f in range(474, 500, 6): add(crush(square(880, 0.08), 3, 6) * 0.3, fr(f), 0.4)
# ---- G. three sessions
for f, k in [(501, 'thud'), (504, 'paper'), (507, 'crush'), (510, 'thud'), (512, 'paper'), (514, 'crush'), (516, 'click'), (517, 'click'), (518, 'click')]: hit(f, k, 0.5)
add(fm(220, 1.41, 2, 0.6, 0.3), fr(519), 0.25)
# ---- H. rupture + wordmark burst + void
add(riser(fr(561) - fr(530), 200, 12000), fr(530), 0.45); add(glitch(fr(561) - fr(540), 9) * np.linspace(0.2, 1, int((fr(561) - fr(540)) * SR)), fr(540), 0.35)
for f, k in [(562, 'thud'), (565, 'paper'), (567, 'crush'), (569, 'click'), (571, 'crush'), (574, 'thud'), (577, 'paper'), (579, 'blip')]: hit(f, k, 0.6)
add(boom(2.6, 70, 28), fr(562), 0.7)
d = fr(680) - fr(582); add((sine(1760, d) + sine(1764.5, d)) * 0.5 * np.minimum(1, np.minimum(T(d) / 0.6, (d - T(d)) / 0.4)), fr(582), 0.035)
add(sine(36.7, d) * np.minimum(1, T(d) / 0.8), fr(582), 0.25)
# ---- I. the chain: a pulse underneath, a hit on every change of representation
for k in range(int((978 - 681) / 14) + 1):
    f = 681 + k * 14; add(kick(), fr(f), 0.55); add(click(9000, 0.01), fr(f + 7), 0.12)
for f in (704, 725, 744, 763, 791, 804, 807, 843, 849, 885, 887, 915, 925, 963, 973, 976):
    hit(f, ['thud', 'crush', 'click', 'paper'][f % 4], 0.45, 0.4 * np.sin(f))
for f in range(763, 790, 3): add(click(3000, 0.008), fr(f), 0.15)
add(riser(fr(915) - fr(900), 400, 7000), fr(900), 0.2)
add(pad([73.4, 110, 146.8], fr(963) - fr(681), 500, fade=0.8), fr(681), 0.12)
# ---- J. manifesto
add(boom(3.0, 85, 28), fr(979), 0.9)
for f in range(989, 1029, 3): hit(f, 'blip', 0.22, 0.6 * np.sin(f * 0.7))
add(riser(fr(1043) - fr(1030), 300, 9000), fr(1030), 0.2); hit(1044, 'click', 0.4)
d = fr(1157) - fr(1048); add(pad([65.4, 98, 130.8, 196], d, 800, fade=0.9), fr(1048), 0.28)
hit(1157, 'paper', 0.6); add(boom(3.2, 110, 26), fr(1159), 1.0); add(lp(noise(1.0), 3000) * env(int(SR), 0.001, 0.2), fr(1159), 0.5)
hit(1173, 'crush', 0.5); hit(1183, 'click', 0.4)
for f in (1196, 1198, 1200, 1202): hit(f, ['crush', 'paper', 'blip', 'click'][(f // 2) % 4], 0.55)
add(fm(130.8, 2, 1.5, 4.0, 1.6) + fm(196, 2, 1, 4.0, 1.4) * 0.6, fr(1204), 0.3); hit(1226, 'crush', 0.3)
out = np.stack([L, R], 1); out /= np.max(np.abs(out)) + 1e-9; out *= 0.9
wavfile.write(sys.argv[1], SR, (out * 32767).astype(np.int16)); print('score', DUR, 's')
