#!/usr/bin/env bash
# Cut the stage loop from the rendered demo.  The first and final two frames
# carry a small RGB split plus scanline flash so an auto-loop reads as a seam.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SOURCE="${1:-/Users/gurio/Desktop/brnrd-demo-cut-v6.mp4}"
FFMPEG="${FFMPEG:-/opt/homebrew/bin/ffmpeg}"

if [[ ! -f "$SOURCE" ]]; then
  echo "Stage source not found: $SOURCE" >&2
  exit 1
fi

SEAM="between(n,0,1)+between(n,148,149)"
FILTER="fps=15,rgbashift=rh=-8:bh=8:enable='$SEAM',drawbox=x=0:y=540:w=iw:h=5:color=white@0.22:t=fill:enable='$SEAM'"

"$FFMPEG" -y -ss 47 -t 10 -i "$SOURCE" -an -vf "$FILTER,scale=1920:1080" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$ROOT/loop-3-stage.mp4"
"$FFMPEG" -y -i "$ROOT/loop-3-stage.mp4" -vf "scale=960:540:flags=lanczos" -loop 0 "$ROOT/loop-3-stage.gif"
"$FFMPEG" -y -ss 47 -t 10 -i "$SOURCE" -an -vf "$FILTER,crop=1440:1080:240:0" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$ROOT/loop-3-stage-phone-4x3.mp4"
"$FFMPEG" -y -i "$ROOT/loop-3-stage-phone-4x3.mp4" -vf "scale=720:540:flags=lanczos" -loop 0 "$ROOT/loop-3-stage-phone-4x3.gif"

# Eight frames make the cut and its seam inspectable without opening media.
"$FFMPEG" -y -i "$ROOT/loop-3-stage.mp4" -vf "select='not(mod(n,19))',scale=240:135,tile=8x1" -frames:v 1 "$ROOT/loop-3-stage-contact-sheet.png"
"$FFMPEG" -y -i "$ROOT/loop-3-stage-phone-4x3.mp4" -vf "select='not(mod(n,19))',scale=180:135,tile=8x1" -frames:v 1 "$ROOT/loop-3-stage-phone-4x3-contact-sheet.png"
