#!/bin/sh
# Re-cut the scene clips from the maintainer's phone recording (not committed:
# ~2.3 GB source, derived clips ~60 MB). Offsets = seconds from recording start.
F="$HOME/Downloads/ScreenRecording_09-01-2026 15-06-56_1.MP4"
cut(){ ffmpeg -hide_banner -loglevel error -y -ss $2 -i "$F" -t $3 -vf "scale=720:-2" -r 60 -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -g 30 -an $1.mp4; }
cut c01_block 29 7; cut c02_ask 36 81; cut c03_wait 117 143; cut c04_reply 260 20
cut c05a_steer 295 43; cut c05b_folded 428 8; cut c06_push 2496 66; cut c07_prup 2672 7
cut c08a_merge 2744 27; cut c08b_ci 2808 109; cut c09_gone 2925 17
