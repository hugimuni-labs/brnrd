#!/usr/bin/env bash
# Full pipeline: causal spine → frames → cut (with score) → web weight → contact sheet → notebook.
set -euo pipefail
cd "$(dirname "$0")/src"
python3 -c "import numpy, scipy, cv2, PIL" 2>/dev/null || {
  echo "missing Python deps — run: pip install -r requirements.txt" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg not on PATH" >&2; exit 1; }
OUT="${MYTHOS_OUT:-../out}"
python3 film.py world
python3 film.py render
python3 film.py cut
# Web weight: grain is incompressible at constant quality, so encode two-pass
# to a fixed budget and let the grain soften.
for p in 1 2; do
  if [ "$p" = 1 ]; then tail=(-an -f mp4 /dev/null); else tail=(-c:a aac -b:a 128k -movflags +faststart "$OUT/mythos.mp4"); fi
  ffmpeg -y -loglevel error -i "$OUT/mythos-master.mp4" -c:v libx264 -preset slow -b:v 2500k \
    -maxrate 5000k -bufsize 10000k -pass "$p" -passlogfile "$OUT/x264" -pix_fmt yuv420p "${tail[@]}"
done
python3 film.py contact 4
python3 film.py notebook
