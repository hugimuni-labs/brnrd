# Score + sound design for the manifesto film, synthesised from nothing, locked to the timeline in src/scenes.ts.
# usage: python3 audio/synth.py public/score.wav
import sys, numpy as np
from scipy.signal import butter, sosfilt
SR = 48000; DUR = 96.0; N = int(SR * DUR)
L = np.zeros(N); Rr = np.zeros(N)
rng = np.random.default_rng(7)
t_all = np.arange(N) / SR
def add(sig, at, gain=1.0, pan=0.0):
    i = int(at * SR); sig = sig[: max(0, N - i)]
    if len(sig) == 0: return
    L[i:i + len(sig)] += sig * gain * np.sqrt(0.5 * (1 - pan)); Rr[i:i + len(sig)] += sig * gain * np.sqrt(0.5 * (1 + pan))
def S(*xs):
    m = max(len(x) for x in xs); o = np.zeros(m)
    for x in xs: o[:len(x)] += x
    return o
def env(n, a=0.005, d=0.3):
    t = np.arange(n) / SR; return np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / d)
def lp(x, f, o=2): return sosfilt(butter(o, f / (SR / 2), 'low', output='sos'), x)
def hp(x, f, o=2): return sosfilt(butter(o, f / (SR / 2), 'high', output='sos'), x)
def bp(x, lo, hi): return sosfilt(butter(2, [lo / (SR / 2), hi / (SR / 2)], 'band', output='sos'), x)
def sine(f, dur, ph=0): t = np.arange(int(dur * SR)) / SR; return np.sin(2 * np.pi * f * t + ph)
def noise(dur): return rng.standard_normal(int(dur * SR))
def fm(f, ratio, idx, dur, d=0.4):
    t = np.arange(int(dur * SR)) / SR; e = env(len(t), 0.002, d)
    return np.sin(2 * np.pi * f * t + idx * e * np.sin(2 * np.pi * f * ratio * t)) * e
def boom(dur=2.5, f0=70, f1=32):
    t = np.arange(int(dur * SR)) / SR; f = f1 + (f0 - f1) * np.exp(-t * 6)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), 0.002, dur / 3)
def riser(dur, f0=200, f1=4000):
    n = noise(dur); t = np.arange(len(n)) / SR; out = np.zeros_like(n); seg = int(0.05 * SR)
    for k in range(0, len(n), seg):
        u = k / len(n); fc = f0 * (f1 / f0) ** u; out[k:k + seg] = bp(n[k:k + seg + 2000], fc * 0.7, min(fc * 1.4, 20000))[:len(n[k:k + seg])]
    return out * (t / dur) ** 2
def pad(freqs, dur, cut=900, det=0.004):
    t = np.arange(int(dur * SR)) / SR; s = np.zeros_like(t)
    for f in freqs:
        for d in (-det, 0, det): s += (2 * ((f * (1 + d) * t) % 1) - 1)
    s = lp(s / (len(freqs) * 3), cut, 2); fade = np.minimum(1, np.minimum(t / 1.2, (dur - t) / 1.2)); return s * fade
def metal(dur=3.0, base=180):
    t = np.arange(int(dur * SR)) / SR; s = np.zeros_like(t)
    for k, r in enumerate([1, 2.76, 5.4, 8.93, 13.34]): s += np.sin(2 * np.pi * base * r * t) * np.exp(-t * (1.2 + k)) / (k + 1)
    return s
def crush(x, bits=4, hold=6):
    y = np.repeat(x[::hold], hold)[: len(x)]; q = 2 ** bits; return np.round(y * q) / q
def square(f, dur): t = np.arange(int(dur * SR)) / SR; return np.sign(np.sin(2 * np.pi * f * t))

# ---- ACT I: CODEX — cold ticks, glass FM plinks, precise
add(sine(55, 10.6) * np.minimum(1, t_all[:int(10.6*SR)] / 3) * 0.5, 0.0, 0.25)
add(hp(noise(10.6), 6000) * 0.02, 0.0)
for k in range(int(10.6 / 0.25)):
    at = 1.0 + k * 0.25
    if at < 10.55: add(hp(noise(0.02), 5000) * env(int(0.02 * SR), 0.0005, 0.004), at, 0.35 if k % 4 == 0 else 0.15, 0.3 if k % 2 else -0.3)
for i, at in enumerate([2.3, 2.85, 3.3, 3.65, 3.95]): add(fm(880 * [1, 1.125, 1.25, 1.5, 1.6875][i], 3.5, 4, 1.2, 0.35), at, 0.22, -0.4 + i * 0.2)
add(fm(440, 2, 3, 3.0, 1.2) * 0.8 + fm(660, 2, 2, 3.0, 1.2) * 0.6, 6.6, 0.25)  # PR sealed
for i in range(3): add(fm(1760, 4, 1, 0.3, 0.08), 6.8 + i * 0.2, 0.12)
add(pad([110, 164.8], 3.2, 700), 7.5, 0.18)
add(riser(0.5, 800, 9000), 10.12, 0.25)
# LIMIT slam
add(boom(3.5, 90, 28), 10.62, 1.0); add(lp(noise(1.5), 2500) * env(int(1.5 * SR), 0.001, 0.25), 10.62, 0.7)
add(metal(4, 97), 10.62, 0.4); add(sine(4200, 3.0) * np.exp(-np.arange(int(3 * SR)) / SR * 0.6), 10.8, 0.03)
add(sine(41, 3.0) * np.minimum(1, np.arange(int(3*SR))/SR), 10.9, 0.25)
# carry: paper, lines falling
add(bp(noise(3.0), 1500, 6000) * np.hanning(int(3.0 * SR)), 13.8, 0.12)
for at in (15.0, 15.4, 15.75): add(S(bp(noise(0.6), 400, 3000) * env(int(0.6 * SR), 0.01, 0.15), fm(300, 1.41, 3, 0.8, 0.3) * 0.5), at, 0.25)
add(riser(1.6, 300, 6000), 16.3, 0.35)
# ---- CLAUDE: warm pad, felt plucks
add(pad([146.8, 220, 277.2, 329.6], 7.2, 1100), 17.95, 0.3)
add(lp(noise(0.5), 1200) * env(int(0.5 * SR), 0.001, 0.12), 17.95, 0.35)
for i, at in enumerate([18.8, 19.15, 19.5]): add(lp(fm(196 * (i + 1), 1, 1.5, 2.0, 0.6), 2000), at, 0.25, -0.3 + 0.3 * i)
for i, at in enumerate([20.8, 21.9, 23.0]): add(lp(fm([587.3, 659.3, 740][i], 2, 1.2, 2.5, 0.9), 2500), at, 0.3, 0.2)
add(lp(fm(880, 2, 1, 3, 1.5), 3000), 23.5, 0.2)
# burn to bitmap
add(crush(riser(1.0, 400, 5000), 3, 12), 24.9, 0.4)
# ---- MISTRAL: square arps, crushed
arp = [110, 130.8, 164.8, 220, 164.8, 130.8]
for k in range(int((33.9 - 25.9) / 0.125)):
    at = 25.9 + k * 0.125
    if 30.9 < at < 31.3: continue
    add(crush(square(arp[k % 6] * (2 if k % 12 > 5 else 1), 0.11) * env(int(0.11 * SR), 0.002, 0.05), 4, 4), at, 0.07, 0.4 if k % 2 else -0.4)
add(crush(lp(square(55, 8.0), 400), 5, 3) * 0.5, 25.9, 0.18)
for at, _ in [(28.9, 1), (29.4, 0), (29.7, 1), (29.9, 0), (30.05, 1), (30.17, 0), (30.26, 1), (30.36, 0)]: add(crush(square(1760, 0.04), 3, 8), at, 0.08)
add(S(crush(boom(2.5, 120, 35), 5, 10), crush(noise(0.4) * env(int(0.4 * SR), 0.001, 0.1), 3, 20)), 30.95, 0.9)
for k in range(14): add(crush(square(880 if k % 2 else 830, 0.06), 3, 10), 31.5 + k * 0.1, 0.07)
t0 = np.arange(int(2.0 * SR)) / SR; add(crush(np.sin(2 * np.pi * np.cumsum(220 * np.exp(-t0 * 1.5)) / SR) * np.exp(-t0), 4, 12), 31.9, 0.2)
# ---- SCATTER: void drone
add(pad([36.7, 55, 58.3], 5.2, 300, 0.008), 33.9, 0.5)
add(lp(noise(5.0), 180) * 0.5, 33.9, 0.3)
# ---- RUPTURE
add(riser(1.4, 100, 3000) * 0.6, 38.9, 0.4)
add(boom(4.0, 60, 24), 40.3, 0.9); add(metal(5, 61.7), 40.3, 0.45); add(metal(5, 263), 40.35, 0.2, 0.5)
for j in range(6):
    at = 40.8 + j / 6
    add(S(hp(noise(0.12), 1500) * env(int(0.12 * SR), 0.001, 0.04), fm(200 * 2 ** (j / 3), 3.3, 8, 0.15, 0.06)), at, 0.5, (-1) ** j * 0.7)
    for q in range(4): add(bp(noise(0.02), 3000, 9000), at + q * 0.03, 0.2)
add(sine(4000, 1.0) * np.hanning(int(SR)), 40.8, 0.03)
# the thread: a rising pure tone that resolves
tt = np.arange(int(2.4 * SR)) / SR; add(np.sin(2 * np.pi * np.cumsum(220 + 110 * tt / 2.4) / SR) * np.hanning(len(tt)) * 0.6, 41.8, 0.2)
add(pad([73.4, 110, 146.8], 6.0, 600), 42.0, 0.3)
add(riser(2.8, 150, 8000), 44.4, 0.3)
# ---- TOPOLOGY: dark pulse at 110 bpm
bpm = 110; beat = 60 / bpm; T0 = 47.2; T1 = 75.6
roots = [36.7, 36.7, 32.7, 41.2]  # D, D, C, E per reading
n_beats = int((T1 - T0) / beat)
for b in range(n_beats):
    at = T0 + b * beat; ri = 0 if at < 54.5 else 1 if at < 61.5 else 2 if at < 68.5 else 3
    if any(abs(at - x) < 0.45 for x in (54.5, 61.5, 68.5)): continue
    add(boom(0.45, 140, 45), at, 0.55)
    add(lp(np.sign(sine(roots[ri] * 2, beat * 0.9)) * env(int(beat * 0.9 * SR), 0.005, 0.18), 500), at + beat / 2, 0.22)
    if ri >= 1: add(hp(noise(0.05), 7000) * env(int(0.05 * SR), 0.001, 0.015), at + beat / 2, 0.18, 0.3)
    if ri >= 2:
        for q in (0.25, 0.75): add(hp(noise(0.03), 9000) * env(int(0.03 * SR), 0.0005, 0.01), at + beat * q, 0.12, -0.3)
    if ri == 3:
        for q in range(4): add(fm(roots[ri] * 8 * [1, 1.5, 2, 1.5][q], 2.01, 2, 0.2, 0.08), at + q * beat / 4, 0.06, (q - 1.5) / 2)
for i, ri in enumerate(range(4)):
    a = [47.2, 54.5, 61.5, 68.5][i]; b = [54.5, 61.5, 68.5, 75.6][i]; r0 = roots[ri]
    add(pad([r0 * 2, r0 * 3, r0 * 4.75 if ri == 2 else r0 * 4.8], b - a, 700 + 300 * ri), a, 0.22)
for x in (54.5, 61.5, 68.5):
    add(riser(0.45, 400, 12000), x - 0.45, 0.4)
    for q in range(5): add(S(hp(noise(0.08), 800) * env(int(0.08 * SR), 0.001, 0.03), fm(300 * 2 ** (q / 2.5), 3.7, 6, 0.1, 0.04)), x - 0.45 + q * 0.16, 0.45, (-1) ** q * 0.6)
    add(boom(2.0, 80, 30), x + 0.35, 0.8); add(metal(2.5, 90 + x), x + 0.35, 0.2)
add(fm(1318.5, 2, 1.5, 3.0, 1.5) + fm(1760, 3, 1, 3.0, 1.2), 73.0, 0.18)  # release v2.4.1
# collapse: everything sucked into a point, then nothing
add(riser(2.2, 6000, 200)[::-1][::-1] * 0.5, 75.6, 0.4)
tt = np.arange(int(2.3 * SR)) / SR; add(np.sin(2 * np.pi * np.cumsum(55 + 800 * (tt / 2.3) ** 3) / SR) * (tt / 2.3) ** 2, 75.6, 0.25)
add(boom(3.0, 50, 20), 77.9, 0.9)
add(sine(1318.5, 1.5) * np.exp(-np.arange(int(1.5 * SR)) / SR * 2), 78.2, 0.06)
# ---- MANIFESTO: three hits and a drone that refuses to resolve
add(pad([36.7, 55, 73.4, 77.8], 17.0, 400, 0.006), 79.0, 0.35)
for at in (79.4, 83.8, 88.0):
    add(boom(3.5, 75, 26), at, 0.95); add(metal(4, 73.4), at, 0.35); add(lp(noise(0.3), 3000) * env(int(0.3 * SR), 0.001, 0.08), at, 0.4)
for k in range(20): add(fm([293.7, 246.9, 415.3, 369.9][k % 4], 2.0, 1.5, 0.12, 0.05), 80.0 + k * 0.1, 0.07, (-1) ** k * 0.5)
tt = np.arange(int(1.5 * SR)) / SR
add(np.sin(2 * np.pi * np.cumsum(np.full(len(tt), 440.0)) / SR) * np.hanning(len(tt)) * 0.1, 84.1, 1)
add(fm(146.8, 1, 0.5, 5.0, 3.0) + fm(220, 1, 0.5, 5.0, 3.0) * 0.6, 91.6, 0.3)
# master: gentle glue, stereo width, fades
mix = np.stack([L, Rr], 1)
mix[:, 0] += 0.002 * lp(Rr, 300); mix[:, 1] += 0.002 * lp(L, 300)
fade = np.ones(N); fe = int(1.0 * SR); fade[-fe:] = np.linspace(1, 0, fe) ** 2; mix *= fade[:, None]
mix = np.tanh(mix * 1.4) / np.tanh(1.4)
mix /= np.max(np.abs(mix)) + 1e-9; mix *= 0.89
import wave
out = (mix * 32767).astype('<i2')
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'public/score.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(out.tobytes())
print('score ok', DUR, 's')
