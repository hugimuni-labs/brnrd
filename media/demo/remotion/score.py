#!/usr/bin/env python3
"""Score the cut from its own timeline (no samples, no licence): a CRT hum, a
phosphor blip on every callout, one rising tone where the PR lands, five ticks
under the boot glitch. Pure python — writes out/score.wav.

  python3 score.py <total_frames> <callouts.json> [outro_frames]
"""
import array, json, math, random, sys, wave

SR = 48000
FPS = 60
total_frames = int(sys.argv[1])
callouts = json.load(open(sys.argv[2]))
outro_frames = int(sys.argv[3]) if len(sys.argv) > 3 else 192
dur = total_frames / FPS
n = int(dur * SR)
buf = [0.0] * n

def env_exp(t, tau): return math.exp(-t / tau)

# 1. the hum — 60 + 120 Hz, a slow breath, plus a whisper of filtered noise
rnd = random.Random(7)
lp = 0.0
fade_out_start = (total_frames - outro_frames) / FPS
for i in range(n):
    t = i / SR
    breath = 0.85 + 0.15 * math.sin(2 * math.pi * t / 7.0)
    lp = lp * 0.995 + (rnd.random() - 0.5) * 0.005
    hum = 0.030 * math.sin(2 * math.pi * 60 * t) + 0.012 * math.sin(2 * math.pi * 120 * t) + lp * 0.6
    g = min(1.0, t / 1.2)
    if t > fade_out_start:
        g *= max(0.0, 1 - (t - fade_out_start) / 0.8)
    buf[i] += hum * breath * g

def add_tone(start_s, length_s, f0, f1, amp, tau):
    s0 = int(start_s * SR)
    m = int(length_s * SR)
    for k in range(m):
        if s0 + k >= n: break
        t = k / SR
        f = f0 + (f1 - f0) * (k / m)
        buf[s0 + k] += amp * env_exp(t, tau) * math.sin(2 * math.pi * f * t)

# 2. blips — one per callout start (amber a fifth up)
for c in callouts:
    frame = c[4]; amber = "amber" in json.dumps(c) or c[1].startswith(("sent", "3 minutes", "4 s", "40 minutes", "76", "merged", "the block", "NEEDS", "the strand"))
    add_tone(frame / FPS, 0.11, 1174 if amber else 880, 660, 0.16, 0.035)

# 3. the rising tone where the PR lands (the "76 seconds later" callout)
for c in callouts:
    if c[1].startswith("76"):
        add_tone(c[4] / FPS - 0.2, 1.6, 220, 440, 0.12, 0.9)

# 4. the boot glitch: five ticks, 190 ms apart, then the flicker buzz
boot0 = (total_frames - outro_frames) / FPS
for k in range(5):
    add_tone(boot0 + k * 0.19, 0.03, 1320, 1320, 0.14, 0.012)
add_tone(boot0 + 5 * 0.19, 0.44, 110, 110, 0.06, 0.25)

peak = max(abs(x) for x in buf) or 1.0
scale = 0.8 / peak
out = array.array("h", (int(max(-1.0, min(1.0, x * scale)) * 32767) for x in buf))
with wave.open("out/score.wav", "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(out.tobytes())
print("score.wav", round(dur, 2), "s", "peak", round(peak, 3))
