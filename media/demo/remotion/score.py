#!/usr/bin/env python3
"""Score the cut from its own timeline — chiptune (his note, 2026-09-05: "more
8-bit, more Mario"): NES-shaped voices (square with duty, triangle, LFSR noise),
sample-held at 12 kHz and crushed to 6 bits. A pulse-bass ostinato and a
triangle drone under everything; a coin arpeggio on every callout (a fifth up
for amber); a power-up run where the PR lands ("76 seconds later"); five
descending notes with noise ticks under the boot glitch, then the buzz.
Pure python, no samples, no licence — writes out/score.wav.

  python3 score.py <total_frames> <callouts.json> [outro_frames]
"""
import array, json, math, random, sys, wave

SR = 48000
FPS = 60
HOLD = 4          # sample-and-hold every 4th sample → 12 kHz grit
BITS = 6          # output depth
total_frames = int(sys.argv[1])
callouts = json.load(open(sys.argv[2]))
outro_frames = int(sys.argv[3]) if len(sys.argv) > 3 else 192
dur = total_frames / FPS
n = int(dur * SR)
buf = [0.0] * n
rnd = random.Random(7)

def note(name):
    names = {"C":0,"C#":1,"D":2,"D#":3,"E":4,"F":5,"F#":6,"G":7,"G#":8,"A":9,"A#":10,"B":11}
    p, o = name[:-1], int(name[-1])
    return 440.0 * 2 ** ((names[p] - 9) / 12 + (o - 4))

def square(ph, duty=0.5): return 1.0 if (ph % 1.0) < duty else -1.0
def tri(ph):
    x = (ph % 1.0) * 4
    return (x - 1) if x < 2 else (3 - x)

_lfsr = [0x4000]
def noise():
    v = _lfsr[0]; bit = (v ^ (v >> 1)) & 1; v = (v >> 1) | (bit << 14); _lfsr[0] = v
    return 1.0 if v & 1 else -1.0

def voice(start_s, length_s, f0, f1=None, amp=0.1, wave_="sq", duty=0.5, attack=0.002, decay=None, vib=0.0):
    f1 = f0 if f1 is None else f1
    s0 = int(start_s * SR); m = int(length_s * SR); ph = 0.0
    decay = decay if decay is not None else length_s
    for k in range(m):
        i = s0 + k
        if i < 0 or i >= n: break
        t = k / SR
        f = f0 + (f1 - f0) * (k / m)
        if vib: f *= 1 + vib * math.sin(2 * math.pi * 6 * t)
        ph += f / SR
        if wave_ == "sq": v = square(ph, duty)
        elif wave_ == "tri": v = tri(ph)
        else: v = noise() if (k % 2 == 0) else 0.0
        env = min(1.0, t / attack) * max(0.0, 1 - t / decay)
        buf[i] += amp * env * v

boot0 = (total_frames - outro_frames) / FPS

# 1. the bed — triangle drone on A1 + a 25%-duty pulse-bass ostinato at 120 BPM,
#    quiet, fading in over 1.2 s and out before the boot glitch
voice(0.0, boot0, note("A1"), amp=0.045, wave_="tri", attack=1.2, decay=1e9)
pattern = ["A1", "A1", "E2", "A1", "G2", "A1", "E2", "D2"]
t = 0.4
step = 0.25
k = 0
while t + step < boot0 - 0.6:
    nm = pattern[k % len(pattern)]
    voice(t, step * 0.85, note(nm), amp=0.05, wave_="sq", duty=0.25, attack=0.003, decay=step * 0.8)
    t += step; k += 1

# 2. callouts — a coin arpeggio (root, fifth, octave), 35 ms each, 50% duty;
#    amber callouts a fifth up
AMBER = ("sent", "3 minutes", "4 s", "40 minutes", "76", "merged", "the block", "NEEDS", "the strand")
for c in callouts:
    s = c[4] / FPS
    base = note("E5") if c[1].startswith(AMBER) else note("A4")
    for j, ratio in enumerate((1.0, 1.5, 2.0)):
        voice(s + j * 0.035, 0.09, base * ratio, amp=0.17, wave_="sq", duty=0.5, decay=0.09)

# 3. where the PR lands — the power-up: a 12-note chromatic run then a held
#    major arpeggio, 25% duty, with vibrato on the hold
for c in callouts:
    if c[1].startswith("76"):
        s = c[4] / FPS - 0.25
        f = note("A4")
        for j in range(12):
            voice(s + j * 0.05, 0.06, f * 2 ** (j / 12), amp=0.15, wave_="sq", duty=0.25, decay=0.06)
        for j, nm in enumerate(("A5", "C#6", "E6", "A6")):
            voice(s + 0.62 + j * 0.09, 0.7 - j * 0.09, note(nm), amp=0.11, wave_="sq", duty=0.25, decay=0.7, vib=0.01)

# 4. the boot glitch — five descending notes with noise ticks, 190 ms apart,
#    then the buzz (a 55 Hz 12.5%-duty pulse) under the flicker
for k, nm in enumerate(("E5", "D5", "C5", "B4", "A4")):
    s = boot0 + k * 0.19
    voice(s, 0.02, 0, amp=0.16, wave_="noise", decay=0.02)
    voice(s + 0.01, 0.12, note(nm), amp=0.14, wave_="sq", duty=0.125, decay=0.12)
voice(boot0 + 5 * 0.19, 0.44, note("A1"), amp=0.08, wave_="sq", duty=0.125, decay=0.44)

# 5. the crush: sample-hold at SR/HOLD, quantize to BITS
peak = max(abs(x) for x in buf) or 1.0
scale = 0.85 / peak
levels = 2 ** (BITS - 1)
out = array.array("h")
held = 0.0
for i, x in enumerate(buf):
    if i % HOLD == 0:
        q = round(max(-1.0, min(1.0, x * scale)) * levels) / levels
        held = q
    out.append(int(held * 32767))
with wave.open("out/score.wav", "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(out.tobytes())
print("score.wav", round(dur, 2), "s", "peak", round(peak, 3), "bits", BITS, "hold", HOLD)
