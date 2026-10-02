"""Original deterministic stereo score. No recorded or licensed samples.

A slowed mechanical ostinato, rubbed metallic partials, spatial echoes and a
common sub pulse. The usage wall removes the pulse. The fault collapses the
harmonic field before a heavier post-fault system rhythm takes over.
"""
from pathlib import Path
import json
import math
import subprocess
import wave
import numpy as np

RATE = 48000
DURATION = 84
RNG = np.random.default_rng(73007)
out = np.zeros((RATE * DURATION, 2), dtype=np.float64)

def place(at, signal, gain=1, pan=0):
    start = int(at * RATE)
    n = min(len(signal), len(out) - start)
    if n <= 0 or start < 0:
        return
    angle = (pan + 1) * math.pi / 4
    out[start:start+n, 0] += signal[:n] * gain * math.cos(angle)
    out[start:start+n, 1] += signal[:n] * gain * math.sin(angle)

def tone(at, dur, freq, gain, pan=0, metallic=False):
    t = np.arange(int(dur * RATE)) / RATE
    env = np.minimum(t / .15, 1) * np.minimum((dur-t) / .8, 1)
    env = np.clip(env, 0, 1)
    sig = np.sin(2*np.pi*freq*t + .8*np.sin(2*np.pi*.18*t))
    if metallic:
        sig = (sig + .24*np.sin(2*np.pi*freq*2.009*t) + .11*np.sin(2*np.pi*freq*4.03*t)) / 1.35
    place(at, sig * env, gain, pan)

def strike(at, gain=.3, pitch=100, pan=0, long=False):
    d = 2.3 if long else .7
    t = np.arange(int(d*RATE)) / RATE
    env = np.exp(-t*(2.8 if long else 10))
    sig = (np.sin(2*np.pi*pitch*t + 1.8*np.sin(2*np.pi*pitch*1.413*t)*np.exp(-t*4))
        + .25*np.sin(2*np.pi*pitch*2.713*t)) * env
    sig *= np.minimum(t*900, 1)
    place(at, sig, gain, pan)
    for delay, g in [(0.19,.19),(.37,.10),(.61,.06)]:
        place(at+delay,sig,gain*g,-pan)

def kick(at,gain=.4):
    t=np.arange(int(.5*RATE))/RATE
    phase=2*np.pi*(42*t+(115-42)*.022*(1-np.exp(-t/.022)))
    sig=np.sin(phase)*np.exp(-t*10)+RNG.normal(0,1,len(t))*.10*np.exp(-t*150)
    place(at,sig,gain)

def noise(at,dur,gain,pan=0,rise=False):
    t=np.arange(int(dur*RATE))/RATE
    raw=RNG.normal(0,1,len(t))
    # Smooth deterministic noise makes an air field rather than harsh static.
    smooth=np.convolve(raw,np.ones(15)/15,mode='same')
    env=(t/dur)**2 if rise else np.exp(-t*7/dur)
    env*=np.minimum(t*80,1)*np.minimum((dur-t)*30,1)
    place(at,smooth*env,gain,pan)

# Opening deep machinery; the same root survives every room.
for at,dur,freq,gain,pan in [(0,14.1,36.708,.12,-.15),(0,14.1,55,.05,.4),
    (17,13,73.416,.07,-.5),(17,13,110,.035,.5),
    (30,6,36.708,.12,0),(30,6,77.782,.035,.7),
    (42,28,36.708,.14,0),(42,28,55,.055,-.4),(42,28,146.832,.02,.5)]:
    tone(at,dur,freq,gain,pan,True)
# Cold fabrication pulse (96 bpm), stopped precisely at the boundary.
for i in range(20):
    at=1.5+i*.625
    if at >=14.1: break
    kick(at,.25)
    strike(at+.3125,.075,[293.665,220,329.628,146.832][i%4],(-1 if i%2 else 1)*.45)
for at in [3.4,5.3,7.2,9.6]: strike(at,.2,146.832,long=True)
strike(12,.3,110,long=True)
noise(13.1,.9,.23,rise=True)
# An abrupt metal shutter followed by genuine negative space.
strike(14.1,.55,59)
noise(14.1,.25,.4)
# Hand-carry: sparse, fragile typographic room; slower suspended partials.
for i,at in enumerate([17.3,19,21,23,25,27.4]):
    strike(at,.16,[220,293.665,261.626][i%3],pan=(-.65 if i%2 else .65),long=True)
    noise(at,.4,.045,pan=.4)
# Fresh session is a quantized harsher rhythm.
for i in range(12):
    at=30+i*.5
    kick(at,.25)
    strike(at+.25,.10,220 if i%2 else 155.56,pan=(-.4 if i%2 else .4))
noise(35,1.7,.5,rise=True)
# Fault: a composed harmonic tear, not a generic glitch sample.
for i in range(18):
    at=36+i*.145
    strike(at,.16+i*.008,73.416*2**((i%9)/12),pan=math.sin(i*1.2)*.85,long=True)
noise(36,3.1,.35,rise=True)
strike(38.4,.8,32,long=True)
noise(38.4,2.4,.35)
for i in range(6): tone(39+i*.25,2.1,146.832*2**(i/12),.018,math.sin(i)*.6,True)
# Post-fault: full 96 bpm rhythm. Invariant sub, changing overtones and space.
for i in range(43):
    at=43+i*.625
    kick(at,.46 if i%4==0 else .34)
    strike(at+.3125,.095,[146.832,220,293.665,329.628,261.626,196][i%6],pan=math.sin(i)*.55)
    if i%2==1:
        noise(at+.3125,.16,.12,pan=.35)
    if i%4==3:
        for j in range(4):
            strike(at+.375+j*.0625,.06,440+j*73.416,pan=(-.65+j*.43))
    if i%8==0:
        strike(at,.20,73.416,long=True)
        noise(at,1.2,.13)
for at in [48.7,54.9,60.8]:
    noise(at-.55,.55,.3,rise=True)
    strike(at,.45,49,long=True)
    for j in range(6): strike(at+j*.045,.13,330+j*85,pan=-.8+j*.32)
# Withdraw architecture, then the ownership statements receive their own weight.
tone(70,12.8,36.708,.08,0,True)
for at in [70,73.5,77,80]:
    kick(at,.65)
    strike(at,.38,73.416,long=True)
    noise(at,.8,.18)
    tone(at,2.4,146.832,.025,-.3,True)
# Short cross-channel room reflections. No dangerous peak normalization guesses.
for delay, gain in [(.083,.05),(.167,.04),(.341,.035),(.719,.025)]:
    n=int(delay*RATE)
    out[n:] += out[:-n,::-1].copy()*gain
# Master ramp and soft peak guard. Final loudness is measured by ffmpeg.
t=np.arange(len(out))/RATE
fade=np.minimum(t/.4,1)*np.minimum(np.maximum(84-t,0)/1.2,1)
out*=fade[:,None]
out=np.tanh(out*1.3)
peak=np.max(np.abs(out))
out*=.82/peak
public=Path(__file__).parent/'public'
public.mkdir(exist_ok=True)
raw=public/'score-raw.wav'
with wave.open(str(raw),'wb') as f:
    f.setnchannels(2); f.setsampwidth(2); f.setframerate(RATE)
    f.writeframes((out*32767).astype('<i2').tobytes())
first=subprocess.run(['ffmpeg','-hide_banner','-i',str(raw),'-af','loudnorm=I=-16:TP=-1.2:LRA=10:print_format=json','-f','null','-'],capture_output=True,text=True,check=True)
blob=first.stderr[first.stderr.rfind('{'):first.stderr.rfind('}')+1]
levels=json.loads(blob)
(public/'score-analysis.json').write_text(json.dumps(levels,indent=2)+'\n')
flt=('loudnorm=I=-16:TP=-1.2:LRA=10:measured_I='+levels['input_i']+':measured_TP='+levels['input_tp']+':measured_LRA='+levels['input_lra']+':measured_thresh='+levels['input_thresh']+':offset='+levels['target_offset']+':linear=true:print_format=summary')
subprocess.run(['ffmpeg','-y','-hide_banner','-i',str(raw),'-af',flt,'-ar','48000','-c:a','pcm_s16le',str(public/'score.wav')],check=True)
print('Original score:',public/'score.wav')
