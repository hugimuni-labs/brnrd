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

## Light

| Ink                                 | Means                                                                                                      |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| ground `#0b0906` → vignette         | the room; faint scanlines live in the ground layer, under every glyph                                      |
| bone `#e8dcc0`                      | type and places; brightness follows `heat`                                                                 |
| **amber `#f2b134`**                 | **the resident only** — the shuttle glyph, its halo, trail and trace, sparks, the sweep, its context stack |
| ice `#8fd3ff`                       | the user's hand and the settled — hover, selection, focus plaque edge, console caret, `grant`, knots, PRs  |
| dark `#3a3328`                      | the between — branch lines, unlit runes                                                                    |
| green / red                         | receipts (course ticks, the live dot) / walls (quota under 10 %, a `refill` hold)                          |
| six muted hues                      | heddles, by position — runes, chips, thin halos and arcs only; never fills                                 |

## Motion

- **The sweep (sonar / echography).** A line rotates about the shuttle's place,
  one turn every four beats (2.4 s), eased within each beat, with a 90° phosphor
  wedge and speckle grain behind it. A place pings (brighten + one ring, 300 ms)
  when the line crosses it; a place that just received a boundary pings bright
  the next time it is crossed.
- **The walk.** The shuttle follows the branch curves to each new measured
  place in 300–600 ms, eased. The last 8 places leave a fading amber trail; the
  walked route holds light for 900 ms. Each boundary lands as 12–20 sparks with
  gravity and a glowing bar in the stack beside the glyph.
- **The glitch.** ~120 ms slice offset + chroma split on the changed element
  only: a landed block, a rune whose `lit` moved, the face's state word, a knot
  count rising, a thread's status, a lift. Sweep reveals, glitch marks.
- **The reveal.** On first load the regions arrive 300 ms apart in reading
  order — heddles, warp, window, cloth, bench, face — each with a one-line
  reading under its title. `?` replays it. The shuttle carries
  `you are here → the resident` for six seconds after, and on hover.
- Every flicker derives from the animation clock: Space freezes every pixel.
  `prefers-reduced-motion` stops rotation, walks, sparks, glitches and the
  reveal; bloom and the static range rings stay.

## Contract

| Contract key                        | Mark / receipt                                                                                                                   |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `repo`, `at`                        | Tree root label; last-received timestamp on a stale frame; "since" and "waiting on" minutes are `at` minus the attested instants |
| `beat_ms`                           | Contract specifies 600 ms; the sweep, walk and pings use this fixed beat                                                         |
| `shuttle.state`, `since`            | Face: weaving · at the shed · in the box · handing off · released; since HH:MM                                                   |
| `run.name`, `shell`, `core`, `card` | Default bench: now, plan ticks, vector, ledger verbatim; `course n/m` in the face footer                                         |
| `run.mood`, `mood_glyph`            | Face glyph (fit whole, blinks every eighth beat — presentational), and the shuttle on the tree at the newest bead's last place    |
| `hud.full.await`, `resource_hold`   | Lamps: await (ice) · operator · strands · any (amber) · wall (red); "waiting on: him Nm / a strand / a wall"                     |
| `hud.full.resources.allowance`      | Fuel bezel: needle at spent ÷ tokens, readout, percentage; `stake` in the footer when armed                                      |
| `hud.full.resources.quota.pacing`   | `pace` ratio and recommendation; `?` when absent                                                                                 |
| `hud.full.resources.correspondent`  | Correspondent line: its summary, else its note                                                                                   |
| `hud.full.attention`, `outbound`    | `pending` and `deliveries` in the face footer                                                                                    |
| `hud.quota`                         | Session · week · fable: three glowing bars, percentage left; null is `?`, with no fill                                           |
| `hud.ctx_tokens`                    | ctx column, 50k a tick                                                                                                           |
| `hud.strands`                       | Face rows: title, status, remaining-allowance bar, `+ask → grant`; on the tree a small ⌁ at its last attested place, else fanned off the trunk; `done` converges to the root |
| `heddles[].rune`, `lit`, `slug`     | Rune in its hue, glow by `lit`; click or 1–6 lifts (rises, underline); several lifted = intersection, named in ice                |
| `heddles[].signature`, `last_lit`   | Lifted-layer bench receipt                                                                                                       |
| `warp.goals`, `warp.items`          | ◎ goals; items as rings in their topic hue, held dim, ties to visible prerequisites; done and retired hidden                     |
| `beads`                             | Shuttle position, trail, landing sparks, context stack bars (length by `delta`); click a bar for the block                       |
| `cloth.rows`                        | Focus rail: one plaque (name · topic chips · `now · this run · N min` or `HH:MM → HH:MM · N min` · body · knots · PRs); neighbours shrink 0.9^d to 0.35 with topic arcs; at most three time labels |
| `tree.places` + `beads[].places`    | Upward path trie of the passes in view; the 30 hottest (60 when lifted) plus the shuttle, its trail, threads and the selection; `+N dim` opens on branch hover; depth > 4 elided `…/`; labels placed by priority with collision avoidance, the rest on hover |
| `bench.folds`                       | Place receipt lists available folds and marks; click fetches `/loom/bench?path=…` as plain text                                 |

`1–6` lift the first six supplied heddles; all supplied heddles remain clickable.
Esc drops all; Space freezes the animation clock, not ingestion; `?` replays
the reading; `L` opens the legend. Wheel over the cloth focuses passes; over the
warp it scrolls. Keyboard users can Tab through topic, place, work and pass
receipts. Below 1000 px wide or 820 px tall the bench becomes a closable drawer.

Every action surface exists and is unwired: the place radial (`fold · explain ·
fix · test · split · read`), a thread's `grant`, a fold's `keep · drop`, the
console, lift. Each says _reaches the shuttle in pass 2 — the local gate is not
wired yet_ on use.

The fixture invents every identity, measurement, timestamp and relationship for
layout inspection; its PR numbers echo project vocabulary without attesting
those PRs' contents. Its folds are empty. Replay duplicates fixture blocks with
new times/context totals, and remains visibly labelled **FIXTURE / REPLAY**.

`dev/capture.mjs` shoots the fixture and the live feed, asserts the pause,
reduced-motion, drawer, transport and fold-as-text behaviour, and samples frame
times.
