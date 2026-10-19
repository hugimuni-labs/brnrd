#!/usr/bin/env bash
# Full pipeline: frames → cut (with score) → web weight → contact sheet → atlas.
set -euo pipefail
cd "$(dirname "$0")/src"
python3 -c "import numpy, scipy, cv2, PIL" 2>/dev/null || {
  echo "missing Python deps — run: pip install -r requirements.txt" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg not on PATH (brew install ffmpeg)" >&2; exit 1; }
OUT="${GP_OUT:-../out}"
python3 film.py render
python3 film.py cut
# Web weight: grain is incompressible at constant quality (CRF 27 gave 140 MB),
# so encode two-pass to a fixed 1.5 Mb/s budget (~10 MB) and let the grain soften.
for p in 1 2; do
  if [ "$p" = 1 ]; then tail=(-an -f mp4 /dev/null); else tail=(-c:a aac -b:a 128k -movflags +faststart "$OUT/glyph-physics.mp4"); fi
  ffmpeg -y -loglevel error -i "$OUT/glyph-physics-master.mp4" -c:v libx264 -preset slow -b:v 1500k \
    -maxrate 3000k -bufsize 6000k -pass "$p" -passlogfile "$OUT/x264" -pix_fmt yuv420p "${tail[@]}"
done
python3 film.py contact 4
python3 atlas.py
