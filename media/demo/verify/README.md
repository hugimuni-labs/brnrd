# The sheets the mask was accepted on

Built by `../tools/verify_run.py` from the published mp4s, halved for the repo.
Each cell is cropped to a band of the **phone's** coordinate space — so a
zoomed frame and a full-view frame crop to the same thing — and labelled with
its render frame, the clip on screen, and the second of the original recording.

| sheet | pass | what it covers |
| --- | --- | --- |
| `*-dense-*.png` | every 2nd frame | the three app-open animations, where the number is in motion |
| `*-hz-1.png` | every 60th frame | every masked span plus a second of margin, at 1 Hz |
| `*-list-1.png` | every 3rd frame | the chat-list band, where two more numbers live |

All three leaks this mask had were found by the dense and list passes. The 1 Hz
pass — the sampling rate a reasonable brief asks for — was clean every time,
and would have shipped all three.
