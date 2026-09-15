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
| **amber `#f2b134`**                 | **the resident only** — the shuttle glyph, its halo, trail and trace, sparks, the scan and the seat's trail, its context stack |
| ice `#8fd3ff`                       | the user's hand and the settled — hover, selection, focus plaque edge, console caret, `grant`, knots, PRs  |
| dark `#3a3328`                      | the between — branch lines, unlit runes                                                                    |
| green / red                         | receipts (course ticks, the live dot) / walls (quota under 10 %, a `refill` hold)                          |
| six muted hues                      | heddles, by slug order (stable as the feed reorders) — runes, chips, thin halos and arcs only; never fills                                 |

## Motion

- **The scan on the cloth.** The weft is time, so the scan line lives only on
  the cloth: one traverse per heartbeat tick (`shuttle.tick`, else
  `shuttle.transitions[-1].tick`, else every 10 s), thin and bright with a short
  wake, moving through the rail's *current* x-mapping, fisheye included. Each
  plaque it crosses pulses once and raises a **hairline thread** from the plaque
  into the tree, through that run's trail places in order (1 px, dots r 2, alpha
  ≤ 0.7, run-hued; amber for the seat), rising over ~400 ms and fading over the
  rest of the traverse. Nothing on the tree lights unless a thread from the weft
  reaches it; the seat keeps a faint hairline at rest. A trail is
  `cloth.rows[].trail` when present; else a live strand's places, the seat's
  last eight, a row's `places`, or the places of the beads its pass page attests
  (asked once, on first crossing). `?scan=off` removes the scan: the heartbeat
  pulses on the face and a plaque shows its thread on hover — the same under
  reduced motion. The canvas reports `data-scan` and `data-trails-lit`.
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
  `prefers-reduced-motion` stops the traverse, walks, sparks, glitches and the
  reveal; bloom stays.

## Two roots and the places beyond files

`tree.home.places` (account-home paths: dominion · knowledge · surface · bench)
grow a second, smaller, ice-tinted root left of the repo on the weft. A bead's
`place_kind` routes the shuttle: `file` → the repo tree, `home` → the home tree,
`forge · wire · shed · crew` → four fixed places (forge upper-right, shed beside
the face, crew beside the strands, wire lower-left). Walks between roots run
down one tree, along the weft, and up the other. The PRs of the passes in focus
sit as ice diamonds at the forge; a new one drops in. `clock` beads do not move
the shuttle. Until the feed ships these keys the live window shows the repo
root and four unvisited fixed places; the fixture exercises both.

## The bench renders pages

Selecting on the tree, rail, warp or a rune opens a page, each with a one-line
reading under its title and ‹ back: **pass** (contract excerpt, body, started →
ended, duration, topics, the card halves for the live run, produce with PR
links, strands as links, the last 12 beads), **bead** (the verb line, the
command whole, result size, the delta as a bar, places as links, the chip at
that boundary, ‹ prev · next ›), **item** (type/state/taken, needs and siblings
as links, refs, prompt, body), **place** (kind, heat, knots, the actions, passes
and beads that touched it, folds) and **heddle** (signature, counts by kind, the
index's last rows). What the frame does not attest renders as
_— reads more when the feed lands_. The page endpoint `GET
/loom/page/{kind}?id=…` is asked only when the frame advertises the kind in
`pages: [...]`, so a feed without it is never probed into a console of 404s;
unrecognised scalar fields from a page render under "From the feed".

## Contract

| Contract key                        | Mark / receipt                                                                                                                   |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `repo`, `at`                        | Tree root label; last-received timestamp on a stale frame; "since" and "waiting on" minutes are `at` minus the attested instants |
| `beat_ms`                           | Contract specifies 600 ms; the walk, reveal and face breath use this fixed beat                                                         |
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
| `cloth.rows[].parent`               | Strands group under their top ancestor: stacked under its plaque, counted `⌁n` on its mini plaque, satellites on its dot |
| `cloth.rows`                        | Focus rail: one plaque (name · topic chips · `now · this run · N min` or `HH:MM → HH:MM · N min` · body · knots · PRs); the ten nearest as mini plaques (title · runes), the rest as dots with topic arcs; at most three time labels |
| `tree.places` + `beads[].places`    | Upward path trie of the passes in view; the 30 hottest (60 when lifted) plus the shuttle, its trail, threads and the selection; `+N dim` opens on branch hover (never while lifted); a knot mark only where `knots ≥ 2`; depth > 4 elided `…/`; labels placed by priority with collision avoidance, the rest on hover |
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
reduced-motion, drawer, transport and fold-as-text behaviour, drives a scripted
lift sequence (one click = one toggle, intersection never widens, a second
click restores the counts, hover does not reflow while lifted, Esc drops all),
walks the pages (bead ‹ back returns to its pass), and samples frame times. The
canvas carries its own readout as `data-lifted`, `data-places`, `data-passes`,
`data-groups`, `data-warp`, `data-actor` and `data-runes`.
