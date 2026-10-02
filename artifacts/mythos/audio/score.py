"""The mythos score, synthesised from nothing and keyed to the edit list.

Holds get drones (the slow semantic beat); short shots get transients (the visual
burst); shots can carry a tag via an attribute `snd` on their draw function:
  'impact' · 'hiss' · 'tone:<hz>' · 'silence' · 'pulse:<bpm>' · 'choir'
Usage: score.py OUT.wav   (env MYTH_ACTS selects acts, same as render.py)
"""
import os, sys, math
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
os.environ.setdefault('MYTH_SCALE', '0.1')
import render
from scipy.signal import butter, sosfilt

SR = 48000; FPS = 30

def env(n, a=0.005, r=0.2):
    t = np.arange(n) / SR; e = np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / max(r, 1e-4)); return e

def lp(x, f, o=2): return sosfilt(butter(o, f, 'low', fs=SR, output='sos'), x)
def hp(x, f, o=2): return sosfilt(butter(o, f, 'high', fs=SR, output='sos'), x)
def bp(x, lo, hi): return sosfilt(butter(2, [lo, hi], 'band', fs=SR, output='sos'), x)

rs = np.random.RandomState(1)
def noise(n): return rs.randn(n)

def drone(n, base, t0, bright=0.3, beat=0.07):
    t = (np.arange(n) + t0) / SR; x = np.zeros(n)
    for k, (m, a) in enumerate([(1, 1), (1.5, 0.35), (2, 0.5), (3, 0.18), (0.5, 0.6)]):
        x += a * np.sin(2 * np.pi * base * m * t + 0.3 * np.sin(2 * np.pi * beat * (k + 1) * t))
    x += lp(noise(n), 300 + 1500 * bright) * 0.25
    return x * 0.18

def tick(n, f):
    t = np.arange(n) / SR; return np.sin(2 * np.pi * f * t) * env(n, 0.001, 0.03)

def impact(n):
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * (48 + 90 * np.exp(-t * 18)) * t) * env(n, 0.001, 0.5)
    crack = hp(noise(n), 1500) * env(n, 0.0005, 0.05)
    metal = sum(np.sin(2 * np.pi * f * t) * env(n, 0.001, 0.9 / (k + 1)) for k, f in enumerate([317, 463, 891, 1307])) * 0.2
    return body * 1.2 + crack * 0.6 + metal * 0.5

def main(out):
    cut, total = render.edit_list()
    n = int(total / FPS * SR) + SR; mix = np.zeros(n)
    # bed: a low drone that brightens act by act
    bed = drone(n, 41.2, 0, 0.2) * 0.8
    mix += bed * np.linspace(0.6, 1.0, n)
    for (a, b, fn) in cut:
        i0 = int(a / FPS * SR); L = int((b - a) / FPS * SR); tag = getattr(fn, 'snd', None)
        seg = min(L + SR // 2, n - i0)
        if tag == 'silence':
            mix[i0:i0 + L] *= 0.15; continue
        if tag == 'impact':
            mix[i0:i0 + min(SR * 2, n - i0)] += impact(min(SR * 2, n - i0)) * 0.9
        elif tag and tag.startswith('tone:'):
            f = float(tag[5:]); x = drone(seg, f, i0, 0.5) * env(seg, 0.3, L / SR + 0.3) * 2.2; mix[i0:i0 + seg] += x
        elif tag == 'hiss':
            mix[i0:i0 + L] += bp(noise(L), 2000, 9000) * 0.12
        elif tag == 'choir':
            x = sum(drone(seg, f, i0, 0.6, 0.11) for f in (110, 138.6, 164.8, 220)) * env(seg, 0.6, L / SR) * 0.9
            mix[i0:i0 + seg] += x
        if (b - a) <= 6 and tag != 'impact':
            # a burst frame gets its own transient, pitched by position so bursts feel composed
            f = [880, 1320, 660, 1760, 990, 1480][a % 6]
            m = min(int(0.12 * SR), n - i0); x = tick(m, f) * 0.35 + hp(noise(m), 3000) * env(m, 0.0005, 0.01) * 0.25
            mix[i0:i0 + m] += x
    mix = hp(mix, 25); mix /= np.abs(mix).max() + 1e-9; mix *= 0.8
    import wave
    w = wave.open(out, 'wb'); w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    st = np.stack([mix, np.roll(mix, 31)], 1)
    w.writeframes((np.clip(st, -1, 1) * 32767).astype('<i2').tobytes()); w.close()
    print('score ok', out, total / FPS, file=sys.stderr)

if __name__ == '__main__':
    main(sys.argv[1])
