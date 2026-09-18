# Dungeon development

Serve this checkout with `PYTHONPATH=src python -m brr loom --port 7778`.
The explicit import path matters in a worktree with a host editable install.
Open `/loom/?fixture=live`, `empty`, `three`, or `eighty`. Fixtures freeze time to their
capture timestamp, including reset countdowns; no fixture polls the live feed.

`live.json` is a measured snapshot from the development feed on port 7779.
Private operational prose, routing metadata, shell commands, cloth and bench
contents are omitted before committing. The fixture labels the projection;
fuel, pack and warp additions are real readings, not hand-estimated examples.
`empty` deliberately omits fuel and pack. `three` and `eighty` are explicitly
synthetic: they are the two ends of the generative rule — three rooms and
eighty must open in the same frame, which `capture.mjs` asserts by measuring
ring 1 on the glass (`canvas.dataset.ring1` / `window.__loomFrame`).

Run `node src/brr/loom/static/dev/capture.mjs` with Playwright installed at
`/tmp/shotwork/node_modules`, or set `PLAYWRIGHT_MODULE` to its `index.mjs`.
`LOOM_URL` defaults to `http://127.0.0.1:7778`; `CAPTURE_DIR` defaults to
`/tmp/dungeon-gen1`. Captures use fresh browser contexts for repeatable placement.

The camera, opened doors and archive expansion are local reading state, never
writes to the work graph. The map itself is stored nowhere: it is a pure function
of the reading, so the same `state.json` always draws the same dungeon. (Gen 2's
localStorage slot table is gone — a ring's radius is a function of its population,
so a slot table could not have kept its promise anyway.)

The layout rule, in one paragraph: the hub is the origin; each topic owns an
angular sector sized by its room count; a room's ring is its dependency depth
(the deepest chain of `needs`, since a room is entered only after all of them);
a goal sits on its sector's outer rim; a ring's radius is whatever the busiest
sector needs to stand its rooms side by side, so nothing overlaps. Every distance
is a fraction of ring 1's radius and the first view is framed on ring 1 — which is
why the frame is identical at three rooms and at eighty, and why a crowded ring
renders its rooms as beads (`camera.z < .3`) rather than pretending to be legible.

The map's closed doors mark unresolved `needs`. The region mouths and central
court also carry thin navigation passages; these make no claim about work
dependencies. Lighting follows measured visits and the resident's place.
`?` or the face's question button opens all readings, including the full HUD.

Colour is the temperature of a reading, never decoration: amber is warm, alive,
spending, the resident's trace; ice is a reading gone low *and* the user's own
hand (his ready decisions, the selection brackets, a shut door he can open);
black is absent, paused or unknown — an unvisited room is dark, never dim amber,
and an unmeasured value takes `.unmeasured`, never a grey placeholder. The
temperature ramp (`temperature`/`tempColor`) has a sharp knee at 18–48 %, so a
window reads warm or reads cold and the muddy middle is a band, not a resting place.
