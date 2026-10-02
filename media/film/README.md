# brnrd — brand film

`brnrd-brand-film.mp4`: 14 s, 1080×1080, 60 fps. It is fully procedural: one
canvas program (`film.js`) where every frame is a pure function of `t`.

## The idea

The film is one system that never cuts. Sixteen orbital strands are on screen
from the first second to the last. Each scene is a different *configuration*
of the same strands, never new ones:

| t | scene | the strands become |
| --- | --- | --- |
| 0–2.5 | "I don't want brnrd to be a tool for AI geeks." | an orrery drawing itself around the line; it contracts on "That's too small." |
| 2.5–4.7 | "I want it to feel like a colleague." | two interlinked orbit systems, one for you and one for the colleague; plasma starts flowing |
| 4.7–7.4 | "while you build the business." | four functions on one great orbit, with spokes to a resident core; each node runs its own process cycle and hands off to the next |
| 7.4–10.1 | "You step away." / "It keeps working." | the network tilts back into a plane and keeps spinning and working; the line "You step away." recedes in depth |
| 10.1–11.4 | "Build the business. / Keep the freedom." | calm Keplerian orbits around the resident, now a small sun |
| 11.4–14 | end card | every strand condenses into the lockup's divider; brnrd and HugiMuni come out of that line; the resident leaves as the final particle, down the divider and around `brnrd.dev` |

Outgoing headlines don't cut away. They dissolve into particles that are swept
into the orbits. The ember that appears with the network is the same object as
the sun in the calm scene and the final particle on the end card.

The HugiMuni mark is drawn from its canonical stroke geometry
(`media/brand/hugimuni/`): amber = H only, sky = M only, cream = H ∩ M.

## Render

```bash
NODE_PATH=$(npm root -g) node render.js                 # → brnrd-brand-film.mp4
NODE_PATH=$(npm root -g) node render.js --stills 3.9,13.1   # → stills/
```

Needs Playwright (Chromium) and ffmpeg. Open `index.html#play` to preview in
real time, or `index.html#6.5` to inspect one frame.

## Fonts

`fonts/` bundles Inter Tight and JetBrains Mono (latin subsets from Google
Fonts), both under the SIL Open Font License 1.1.
