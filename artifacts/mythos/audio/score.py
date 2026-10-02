"""The mythos score, synthesised from nothing and keyed to the edit list.

Mostly diegetic: what you hear is what is happening. Shots carry a tag via `fn.snd`:
  'silence'      duck the bed
  'crack'        fracture crackle, densest at the hesitation
  'drop'         a drop landing, and its rings
  'tone:<hz>'    the membrane's drive — the grains move because of this sound
  'impact'       discharge / strike
  'snap:a,b,..'  instrument clicks at local frames a, b, ...
  'print'        thermal print head
  'press:<f>'    rumble building to a strike at local frame f
  'hum'          mains hum of the circuit
  'spark'        a pulse running a wire
  'morse'        the register sounding b r n r d, in sync with the marks it presses
Short shots (≤ 4 frames) get a transient. Usage: score.py OUT.wav  (MYTH_ACTS as in render.py)
"""
import os, sys, math, wave
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
os.environ.setdefault('MYTH_SCALE', '0.1')
import render
from scipy.signal import butter, sosfilt

SR = 48000; FPS = 30
rs = np.random.RandomState(1)

def env(n, a=0.005, r=0.2):
    t = np.arange(n) / SR; return np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / max(r, 1e-4))
def lp(x, f, o=2): return sosfilt(butter(o, f, 'low', fs=SR, output='sos'), x)
def hp(x, f, o=2): return sosfilt(butter(o, f, 'high', fs=SR, output='sos'), x)
def bp(x, lo, hi): return sosfilt(butter(2, [lo, hi], 'band', fs=SR, output='sos'), x)
def noise(n): return rs.randn(n)
def sine(n, f, t0=0): return np.sin(2 * np.pi * f * (np.arange(n) + t0) / SR)

def drone(n, base, t0, bright=0.3, beat=0.07):
    t = (np.arange(n) + t0) / SR; x = np.zeros(n)
    for k, (m, a) in enumerate([(1, 1), (1.5, 0.3), (2, 0.45), (3, 0.15), (0.5, 0.6)]):
        x += a * np.sin(2 * np.pi * base * m * t + 0.3 * np.sin(2 * np.pi * beat * (k + 1) * t))
    return (x + lp(noise(n), 300 + 1500 * bright) * 0.25) * 0.18

def tick(n, f): return sine(n, f) * env(n, 0.001, 0.03)

def impact(n, low=48):
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * (low + 90 * np.exp(-t * 18)) * t) * env(n, 0.001, 0.6)
    crack = hp(noise(n), 1500) * env(n, 0.0005, 0.06)
    metal = sum(np.sin(2 * np.pi * f * t) * env(n, 0.001, 1.1 / (k + 1)) for k, f in enumerate([317, 463, 891, 1307])) * 0.2
    return body * 1.2 + crack * 0.6 + metal * 0.5

def crackle(n, density):
    """Sparse filtered clicks; density (0..1 array or scalar) shapes how often they fire."""
    x = np.zeros(n); d = np.broadcast_to(density, (n,))
    hits = np.flatnonzero(rs.rand(n) < d * 0.0025)
    for h in hits:
        m = min(400, n - h); x[h:h + m] += hp(noise(m), 2500) * env(m, 0.0001, 0.002) * rs.uniform(0.3, 1)
    return x

def place(mix, i0, x, k=1.0):
    m = min(len(x), len(mix) - i0)
    if m > 0: mix[i0:i0 + m] += x[:m] * k

def main(out):
    cut, total = render.edit_list()
    n = int(total / FPS * SR) + SR; mix = np.zeros(n)
    mix += drone(n, 41.2, 0, 0.15) * 0.55 * np.linspace(0.5, 1.0, n)        # the ground under everything
    for (a, b, fn) in cut:
        i0 = int(a / FPS * SR); L = int((b - a) / FPS * SR); tag = getattr(fn, 'snd', None) or ''
        seg = min(L + SR // 2, n - i0)
        if tag == 'silence':
            mix[i0:i0 + L] *= 0.12
        elif tag == 'crack':
            dens = np.interp(np.arange(L), [0, L * 0.38, L * 0.6, L], [0.3, 1.0, 0.6, 0.2])
            place(mix, i0, crackle(L, dens) * 0.9 + lp(noise(L), 120) * 0.15 * env(L, 0.3, L / SR))
        elif tag == 'drop':
            m = SR; t = np.arange(m) / SR
            plink = np.sin(2 * np.pi * (900 + 700 * np.exp(-t * 30)) * t) * env(m, 0.0005, 0.12)
            place(mix, i0, plink * 0.5 + lp(noise(m), 200) * env(m, 0.001, 0.08) * 0.6)
            place(mix, i0, drone(seg, 55, i0, 0.3) * env(seg, 0.4, L / SR) * 1.2)
        elif tag.startswith('tone:'):
            f = float(tag[5:])
            x = (sine(seg, f, i0) + 0.35 * sine(seg, 2 * f, i0) + 0.12 * sine(seg, 3 * f, i0)) * env(seg, 0.08, L / SR + 0.15)
            place(mix, i0, x * 0.32)
        elif tag == 'impact':
            place(mix, i0, impact(SR * 2) * 0.9)
        elif tag.startswith('snap:'):
            for s in tag[5:].split(','):
                j = i0 + int(int(s) / FPS * SR); m = int(0.25 * SR)
                place(mix, j, (hp(noise(m), 1800) * env(m, 0.0002, 0.006) + sine(m, 2100) * env(m, 0.0005, 0.02) * 0.4 + impact(m, 70) * 0.25))
        elif tag == 'print':
            t = np.arange(L) / SR; am = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 120 * t))
            g = np.interp(np.arange(L), [0, L * 0.25, L * 0.26, L * 0.75, L * 0.76, L], [0, 0, 1, 1, 0, 0])
            place(mix, i0, bp(noise(L), 1500, 6000) * am * g * 0.14)
        elif tag.startswith('press:'):
            k = int(tag[6:]); j = int(k / FPS * SR)
            rumble = lp(noise(j), 90) * np.linspace(0, 1, j) ** 2 * 0.9
            place(mix, i0, rumble); place(mix, i0 + j, impact(SR * 3, 38) * 1.3)
        elif tag == 'hum':
            x = sum(sine(seg, 50 * h, i0) / h for h in (1, 2, 3, 5)) * env(seg, 0.05, L / SR + 0.1)
            place(mix, i0, x * 0.08)
        elif tag == 'spark':
            place(mix, i0, crackle(L, 0.8) * 0.6 + bp(noise(L), 3000, 9000) * np.linspace(0, 1, L) * 0.05)
        elif tag == 'morse':
            import m3
            marks, tot = m3.morse_marks(); speed = (tot + 400) / ((b - a) - 20)
            for (s0, Lm) in marks:
                f0 = (s0 + 200) / speed; f1 = (s0 + Lm + 200) / speed
                j0 = i0 + int(f0 / FPS * SR); j1 = i0 + int(f1 / FPS * SR); m = j1 - j0
                place(mix, j0, sine(m, 700) * np.minimum(1, np.minimum(np.arange(m), m - np.arange(m)) / 200) * 0.22)
                c = int(0.05 * SR)
                place(mix, j0, hp(noise(c), 1200) * env(c, 0.0002, 0.004) * 0.6)
                place(mix, j1, hp(noise(c), 1200) * env(c, 0.0002, 0.004) * 0.35)
        if (b - a) <= 4 and tag not in ('impact',):
            f = [880, 1320, 660, 1760, 990, 1480][a % 6]
            m = min(int(0.12 * SR), n - i0)
            place(mix, i0, tick(m, f) * 0.25 + hp(noise(m), 3000) * env(m, 0.0005, 0.01) * 0.2)
    mix = hp(mix, 25); mix /= np.abs(mix).max() + 1e-9; mix *= 0.8
    st = np.stack([mix, np.roll(mix, 31)], 1)
    w = wave.open(out, 'wb'); w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((np.clip(st, -1, 1) * 32767).astype('<i2').tobytes()); w.close()
    print('score ok', out, total / FPS, file=sys.stderr)

if __name__ == '__main__':
    main(sys.argv[1])
