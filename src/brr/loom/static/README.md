# The loom screen

A dependency-free Canvas 2D window. The DOM holds the bench, keyboard receipt
controls, legend and console. Serve this directory at `/loom/` with the loom
feed; open `/loom/?src=dev` for the labelled fixture. Add `&replay` to replay
fixture boundaries every four 600 ms beats; this is never enabled for live data.

The frame sends `state` SSE events at `/loom/events`; initial state comes from
`/loom/state.json`. Failed connections preserve the last frame and label it
reconnecting. All requests stay under `/loom/`; there are no remote fonts,
packages, analytics or outgoing writes. A fold is fetched only when selected.
Unknown measurements remain unknown. Directory nodes derive from measured paths.

| Contract key                        | Mark / receipt                                                                                                                                                                                |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repo`, `at`                        | Tree root; last-received timestamp on a stale frame                                                                                                                                           |
| `beat_ms`                           | Contract specifies 600 ms; smoothstep motion uses this fixed beat                                                                                                                             |
| `shuttle.state`                     | Gauge word: weaving, at the shed, in the box, fresh shuttle threaded, released                                                                                                                |
| `run.name`, `shell`, `core`, `card` | Default bench: now, plan ticks, vector, ledger verbatim                                                                                                                                       |
| `run.mood_glyph`                    | White-gold shuttle at the latest bead's last place; hidden if that place is outside the lifted set                                                                                            |
| `hud.chip`                          | Gauge string, wrapped character by character without abbreviation                                                                                                                             |
| `hud.quota`                         | Three labelled bars showing percentages left; null is `?`, with no fill                                                                                                                       |
| `hud.strands`                       | Smaller glyphs branch from the pass root (the contract supplies no thread places); remaining allowance bars, click for exact numbers; under a lift only explicit signature thread IDs qualify |
| `heddles[].rune`, `lit`, `slug`     | Stable hue by rune; glow follows measured `lit`; click lifts; several lifted means intersection                                                                                               |
| `heddles[].signature`, `last_lit`   | Lifted-layer bench receipt                                                                                                                                                                    |
| `warp.goals`                        | Goals marked ◎ above the work                                                                                                                                                                 |
| `warp.items`, `needs`, `state`      | Ready threads bright, held dim, ties to visible prerequisites; done ticks at cloth; retired hidden                                                                                            |
| `beads`                             | Small beads by their places; click for block, context, delta, time; fresh boundaries produce one beat of sparks and update the five-block context stack                                       |
| `cloth.rows`                        | One bead per pass, chronological left to right; topic arcs, PR diamond; hover plaque and click receipt                                                                                        |
| `tree.places` + `beads[].places`    | One upward path trie; heat controls brightness, unknown heat has a neutral dim mark; knot count labelled ⊙                                                                                    |
| `bench.folds`                       | Place receipt lists available folds and marks; click fetches `/loom/bench?path=…` as plain text                                                                                               |

`1–6` lift the first six supplied heddles; all supplied heddles remain clickable.
Esc drops all; Space freezes the animation clock, not ingestion; `?` opens the
legend. Wheel over the tree/warp reveals overflowing measured rows. Keyboard
users can Tab through topic, place, work and pass receipts. Below 1000 px the
bench becomes a closable drawer. Reduced motion removes particles, glows that
pulse, movement and block entrances.

The fixture invents every identity, measurement, timestamp and relationship for
layout inspection; its PR numbers echo project vocabulary without attesting
those PRs' contents. Its folds are empty. Replay duplicates fixture blocks with
new times/context totals, and remains visibly labelled **FIXTURE / REPLAY**.
