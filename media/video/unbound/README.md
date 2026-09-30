# Unbound

A 34.5 s landscape (1920×1080, 30 fps, silent) promo. It works as a slightly
sinister systems poem about session captivity: work starts in one provider's
harness and has to be carried by hand into the next, until brnrd routes a
resident through all of them. The enemy is provider-bound continuity, not the
models. As in `first-hire`, brnrd appears only as type (`brnrd`, `b^n^d`,
`b^_^d`), never as logo artwork. Provider rooms are stylised impressions, and
none of them uses a provider's logo.

| file | what |
| --- | --- |
| `scene.html` | the whole piece on one canvas; `render(t)` paints every frame as a pure function of time (all noise is hashed). Open `scene.html?t=17.9` to inspect a moment. |
| `render.mjs` | screenshots each frame in headless Chromium, encodes H.264 with ffmpeg (`--crf`, default 23) |
| `brnrd-unbound.mp4` | the render |

```bash
node media/video/unbound/render.mjs                    # full render, ~5 min
node media/video/unbound/render.mjs --stills 14.6,23.5 # review PNGs
```

It reuses Playwright from `src/frontend/node_modules` and needs `ffmpeg` on
PATH. Type is SF Mono / Menlo, Charter and Avenir Next Condensed (macOS system
fonts). On other systems the fallbacks will change the metrics.

## Scenes

| time | scene |
| --- | --- |
| 0–2.2 | the void: a low-amplitude field of tiny blocks |
| 2.2–7.9 | Codex emerges from blue vapor (threshold apparition), writes the retry budget, opens PR #2146; the follow-up hits a usage limit |
| 7.9–8.6 | **SESSION ENDED**, **FOLLOW-UP PENDING** insert |
| 8.6–12.8 | Claude: the user re-explains and re-pastes the diff (a cold margin counts ×3); Claude continues the hooks path |
| 12.8–16.4 | Mistral, a low-res CRT with 2/9 files in view: a "small" release checks out main, keeps main in a conflict, tags v0.10.0 without #2146. The repo graph shows the clobber, then the frame fractures |
| 16.4–17.4 | **CONTEXT LOST**: fragments of the three sessions drift apart |
| 17.4–20.8 | a spectral stream breaks through the walls of the three rooms; brnrd annotates them (ROUTED, ONE THREAD); the name types in and flickers `b^n^d` → `b^_^d` |
| 20.8–22.6 | the cursor block match-cuts into the resident ◆; its ledger carries the thread, the PR, memory, and a flag on the bad tag |
| 22.6–25.2 | hard substitutions through Codex, Claude, Mistral and Antigravity; the resident and ledger stay identical, and each room picks up the thread (the release is re-cut as v0.10.1 with #2146) |
| 25.2–29.2 | the room becomes one grid tile; the grid relabels providers → machines → projects → processes while the resident's route runs on unbroken |
| 29.2–32.2 | THE MODEL IS REPLACEABLE. / THE WORK IS YOURS / OWN YOUR AGENTS. |
| 32.2–34.5 | `brnrd` with the resident as its cursor |

The resident ◆ and the spectral routing line are the invariant. Everything
around them changes style, so keep them unchanged if you edit.
