"""Render the mythos film (or a span of it) and build tiled review sheets.

  render.py video OUT.mp4 [--from F] [--to F] [--acts 1,2]     # muted video, parallel
  render.py sheet OUT.png --from F --to F [--every N] [--tile 240]  # every Nth frame, tiled + numbered
Env: MYTH_SCALE=0.5 renders half-res (previews); MYTH_JOBS=N workers.
"""
import os, sys, argparse, subprocess, importlib
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(__file__))
import core
from PIL import Image, ImageDraw

ACTS = os.environ.get('MYTH_ACTS', 'm1,m2,m3').split(',')

def edit_list():
    shots = []
    for a in ACTS: shots += importlib.import_module(a).build()
    cut = []; F = 0
    for dur, fn in shots: cut.append((F, F + dur, fn)); F += dur
    return cut, F

_CUT = None
def frame(F):
    global _CUT
    if _CUT is None: _CUT = edit_list()[0]
    for a, b, fn in _CUT:
        if a <= F < b:
            img = fn(F - a, b - a, F)
            return core.to8(core.finish(img.astype(np.float32), F))
    return np.zeros((core.H, core.W, 3), np.uint8)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('mode'); ap.add_argument('out')
    ap.add_argument('--from', dest='a', type=int, default=0); ap.add_argument('--to', dest='b', type=int, default=None)
    ap.add_argument('--every', type=int, default=1); ap.add_argument('--tile', type=int, default=240)
    ap.add_argument('--cols', type=int, default=12)
    o = ap.parse_args()
    cut, total = edit_list(); b = total if o.b is None else min(o.b, total)
    for a in ACTS:  # simulations run once here and are cached on disk before the workers fork
        m = importlib.import_module(a)
        if hasattr(m, 'prepare'): m.prepare()
    frames = list(range(o.a, b, o.every))
    jobs = int(os.environ.get('MYTH_JOBS', '7'))
    print(f'total {total} f ({total / core.FPS:.2f}s) · rendering {len(frames)} @ {core.W}x{core.H}', file=sys.stderr)
    with Pool(jobs) as pool:
        it = pool.imap(frame, frames, chunksize=2)
        if o.mode == 'video':
            p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{core.W}x{core.H}', '-r', str(core.FPS),
                                  '-i', '-', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', o.out], stdin=subprocess.PIPE)
            for k, im in enumerate(it):
                p.stdin.write(im.tobytes())
                if k % 60 == 0: print(f'  {frames[k]}', file=sys.stderr)
            p.stdin.close(); p.wait()
        else:
            t = o.tile; th = int(t * 9 / 16); cols = o.cols; rows = (len(frames) + cols - 1) // cols
            sheet = Image.new('RGB', (cols * t, rows * (th + 14)), (20, 20, 20)); d = ImageDraw.Draw(sheet)
            for k, im in enumerate(it):
                x = (k % cols) * t; y = (k // cols) * (th + 14)
                sheet.paste(Image.fromarray(im).resize((t, th), Image.BILINEAR), (x, y + 14))
                d.text((x + 3, y + 1), str(frames[k]), fill=(255, 200, 120))
            sheet.save(o.out)
    print('ok', o.out, file=sys.stderr)

if __name__ == '__main__':
    main()
