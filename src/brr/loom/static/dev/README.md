# Dungeon development

Serve this checkout with `PYTHONPATH=src python -m brr loom --port 7778`.
The explicit import path matters in a worktree with a host editable install.
Open `/loom/?fixture=live`, `empty`, or `eighty`. Fixtures freeze time to their
capture timestamp, including reset countdowns; no fixture polls the live feed.

`live.json` is a measured snapshot from the development feed on port 7779.
Private operational prose, routing metadata, shell commands, cloth and bench
contents are omitted before committing. The fixture labels the projection;
fuel, pack and warp additions are real readings, not hand-estimated examples.
`empty` deliberately omits fuel and pack. `eighty` is explicitly synthetic.

Run `node src/brr/loom/static/dev/capture.mjs` with Playwright installed at
`/tmp/shotwork/node_modules`, or set `PLAYWRIGHT_MODULE` to its `index.mjs`.
`LOOM_URL` defaults to `http://127.0.0.1:7778`; `CAPTURE_DIR` defaults to
`/tmp/dungeon-gen1`. Captures use fresh browser contexts for repeatable placement.

The camera, opened doors and archive expansion are local reading state, never
writes to the work graph. Slot assignments persist per repository in localStorage
under `dungeon.slots.v1:`. A slot is retained when its room disappears; adding a
colliding room uses the next unused slot. Clearing browser storage reconstructs
the map deterministically from the currently present IDs.

The map's solid corridors mean `needs`. Dotted passages connect the generated
wing/hub layout for navigation and make no claim about work dependencies.
`?` or the face's question button opens all readings, including the full HUD.
