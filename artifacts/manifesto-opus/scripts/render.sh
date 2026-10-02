#!/bin/bash
# Reproducible render: score → bundle → video in muted chunks (bounded temp disk) → mux + encode.
# usage: ./scripts/render.sh [OUT.mp4]   (from artifacts/manifesto-opus; needs node_modules — symlink or npm i)
set -e
cd "$(dirname "$0")/.."
OUT=${1:-out/brnrd-manifesto-opus.mp4}; mkdir -p out
# Scratch is scoped to this project: Remotion (os.tmpdir) follows TMPDIR, so every render cache lands here and
# clean() never touches the shared user temp dir (another render may be using it).
export TMPDIR="$PWD/out/tmp/"; mkdir -p "$TMPDIR"
clean() { rm -rf "$PWD/out/tmp" && mkdir -p "$PWD/out/tmp"; }
python3 audio/synth.py public/score_raw.wav
ffmpeg -v error -y -i public/score_raw.wav -af "loudnorm=I=-15:TP=-2:LRA=14,alimiter=limit=0.63:attack=1:release=60:level=disabled" -ar 48000 public/score.wav && rm public/score_raw.wav
npx remotion bundle src/index.ts --out-dir build --public-dir public >/dev/null
TOTAL=2880; N=4; STEP=$(( TOTAL / N )); : > out/chunks.txt
for k in $(seq 0 $((N-1))); do
  A=$((k*STEP)); B=$(( (k+1)*STEP - 1 ))
  [ -f out/v_$k.mp4 ] || npx remotion render build Film out/v_$k.mp4 --muted --props='{"muted":true}' --frames=$A-$B --concurrency=${CONC:-3} --crf=16 --timeout=180000 --log=error
  echo "file 'v_$k.mp4'" >> out/chunks.txt; clean
done
ffmpeg -v error -y -f concat -safe 0 -i out/chunks.txt -i public/score.wav -map 0:v -map 1:a \
  -vf "scale=in_range=full:out_range=tv,format=yuv420p" -c:v libx264 -preset slow -crf 17 -profile:v high \
  -color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709 -c:a aac -b:a 256k -shortest -movflags +faststart "$OUT"
echo "render ok → $OUT"
