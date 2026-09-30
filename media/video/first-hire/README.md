# Your first hire doesn't have to be a hire

A 55 s vertical (1080×1920, 30 fps, silent) motion piece introducing brnrd to
small-business owners. It is built entirely from type, cards and flat shapes.
There is no footage, no audio and no logo artwork: the coworker's face is the
typeset awake frame `b^n^d` (it blinks to `b-n-d`), and its name is plain
`brnrd` type.

| file | what |
| --- | --- |
| `scene.html` | the whole piece; `render(t)` poses every element as a pure function of time. Open `scene.html?t=23.4` to inspect a moment. |
| `render.mjs` | screenshots each frame in headless Chromium, encodes H.264 with ffmpeg |
| `brnrd-first-hire.mp4` | the render |

```bash
node media/video/first-hire/render.mjs                # full render, ~1 min
node media/video/first-hire/render.mjs --stills 9,31  # review PNGs
```

It reuses Playwright from `src/frontend/node_modules` (run `npm ci` there first)
and needs `ffmpeg` on PATH. Type is Avenir Next, a macOS system font; on other
systems the fallback font will change the text metrics.

## Scenes

| time | scene |
| --- | --- |
| 0–6 | too many jobs: a baker, job cards crowding in, **6 JOBS AT ONCE** |
| 6–12.5 | what outsourcing costs (Malt 2026 France day rates, with disclaimer) |
| 12.5–16.5 | **What if your first hire wasn't a hire?** The brnrd profile card |
| 16.5–27 | bakery: one request → five connected tasks → done stack → kept by brnrd |
| 27–37.5 | realtor: TO DO → WORKING → DONE, a timeline, then brnrd surfaces 3 buyers |
| 37.5–44.5 | chatbot re-briefing vs a coworker whose yesterday carries into today |
| 44.5–50 | busywork moves to brnrd; customers, creative direction and decisions stay with the owner |
| 50–55 | You run the business. brnrd runs the busywork. End frame |

The brnrd chip introduced in scene 3 stays on screen through scene 7. Finished
cards fly into it and stay stacked beneath it. That continuity is the argument,
so keep it if you edit.
