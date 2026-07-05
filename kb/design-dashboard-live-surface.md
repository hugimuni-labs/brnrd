# Design: the dashboard's live surface — Zachtronics-flow, envelope naming, plan hygiene

Status: active, opened 2026-07-05 (run-260705-1728-aoym). A discussion turn,
not a build turn — the maintainer asked to "discuss the dashboard and the
plan surface" before anything gets scoped into slices. This page is that
discussion's durable form: what's actually wrong today (screenshot audit),
what shape is being asked for, and the forks still open for the maintainer.

## The ask, decomposed

One message, several threads (verbatim framing preserved because the
specifics matter):

1. **A live, temporal-flow visualization** — "even more live, since the
   5-hourly window and my current runs are displayed as a moving thing,
   where I can natively schedule allocations, watch them, see the state and
   the live execution in terms of tasks and resources." Reference class
   named explicitly: Zachtronics games (TIS-100, SpaceChem, Opus Magnum) —
   not their UI chrome, but their sense of resources and time flowing
   through a system you can see and reason about spatially.
2. **Finance/profit is a *different* dashboard, deliberately deferred** —
   "the agent which also knows the 'finance' of the user's projects, it is
   whole different planning dashboard." Not in scope here.
3. **Input capture, wanted but flagged as a privacy tension** — "ideally
   also the input, although I have my reservations on the privacy avoidance
   point." Not resolved here; needs its own fork (see Open forks).
4. **Aesthetic bar: "favored by the AI-savvy, frontend-focused tech folk"**
   — explicit contrast with what exists now, called "ugly and cheesy, and
   largely dispersed or unimplemented."
5. **Naming**: continuing the round-8 "card" collision thread
   (`design-resident-boundary.md` §7) — "envelope" in "envelope gauge" was
   accepted, "gauge" wasn't: "gauge is a bit boring."
6. **Plan-surface hygiene**: "the current plan is a bunch of context
   overrides... I think we gotta advise on read-before-write kinda
   approach" — about `plans/<repo>/active.md`, not the dashboard code.

Also: quota color for the whole message — 25 minutes left in *that specific*
session window at time of typing, ~65% left, named later in a same-thread
follow-up as **Codex** quota, not this Claude run's. Worth keeping straight:
the 5-hour/weekly figures in the maintainer's framing are the multi-runner
reality this dashboard has to render, not a single provider's number.

## Current-state audit (screenshots, 2026-07-05 19:10–19:11, this run)

Four screenshots of the live dashboard, read directly rather than guessed
from code:

- **`/plans`** — literally a `<pre>`-rendered dump of `active.md`, headers
  and all (`## Ranked moves` shows as literal text, not a rendered
  heading). This is the exact shape `plan-brnrd-dashboard-mvp.md`'s CPS
  section already flagged as "ship plain, skin later" — confirmed still
  plain. Reads as raw prompt output because that's exactly what it is.
- **`/repos`** ("Repos through brnrd") and **`/dashboard`** — a green-on-
  black monospace "COMMAND DECK" aesthetic: functional information density,
  but the genre is generic terminal/hacker-dashboard, not particularly
  distinguished. This is the concrete referent for "cheesy" — it reads as
  a costume (the same one `identity-core.md` warns against wearing), not a
  considered visual language.
- **A real bug, not an aesthetic complaint**: the Activity/Runs view and
  the dashboard's "Recent daemon reports" panel both show the *same*
  message repeated verbatim 3-6 times with identical timestamps
  (`17:08:39` six times in a row; "Thanks this is a very good and deep
  overview..." three times across different "pending" run cards). Daemon
  reports are not being deduplicated before render. Worth its own GH issue
  — it actively undermines trust in the "live" framing the maintainer is
  asking to *strengthen*, since right now the log looks live but is
  actually stuttering.
- **The Budget/Runner-quotas card reads `UNKNOWN` for both the 5h and
  weekly bars** — this is the already-tracked gap: `plan-Gurio__brr/
  active.md` item 4 (B2, "live-quota gap") — the quota read is coded but
  reads a `brr_dir`-level cache nothing writes to yet. This single dead
  card is a large part of why "the whole live information and control
  point is missing": the one gauge meant to answer "how much runway do I
  have" answers nothing.
- **No temporal/flow visual language exists at all.** Every panel is a
  static card or a table row. Nothing represents the *moving* 5-hour
  window, nothing shows a task's position in a queued→running→done
  pipeline as motion or spatial flow — which is precisely the Zachtronics
  quality being asked for and precisely what's structurally absent, not
  just under-styled.

## Naming: "envelope ___"

Carried over from round 8's open fork (`design-weave-register.md` §Round
8, `decision ledger` same date). "Envelope" stays — it already does real
work (`design-resident-boundary.md` §2, the envelope-vs-limits model).
"Gauge" goes. Candidates, judged by: does it earn a place next to
"envelope," does it survive being said out loud in a standup, does it
resonate with the Zachtronics register the maintainer is already reaching
for:

- **`envelope loom`** *(recommended)* — a loom is warp-and-weft, rows and
  columns in motion, which is almost exactly the TIS-100/SpaceChem grid
  the maintainer described, and it lands a second reference for free: this
  project already has a "weave" register for the resident's own notation
  (`weave.md`). Same metaphor, two surfaces (resident's own notation vs.
  the resident's boundary state) — not a collision, a resonance.
- **`envelope manifold`** — industrial, pipe-network-coded (Opus Magnum's
  reagent lines, SpaceChem's molecule pipes); reads as "where multiple
  resource flows converge," which is literally what the boundary-state
  card is. Slightly more mouthful than "loom."
- **`envelope trace`** — plainer, closer to "gauge" in register (an
  oscilloscope trace), safer but doesn't clear the "not boring" bar as
  cleanly as the two above.

Not proposing to touch any of the *other* dashboard "gauge" language
(project-cap gauge, credits gauge, events gauge in
`plan-brnrd-dashboard-mvp.md`) — those are ordinary UI meters for
allowance/billing, a different concept than the resident's own
distance-from-envelope read; renaming stays scoped to
`design-resident-boundary.md` §7's "boundary state card."

**Not renamed yet** — this is a proposal for the maintainer's pick, not a
executed rename; `design-resident-boundary.md` §7 still says "boundary
state card" pending the word.

## A shape for the live-flow surface (proposal, not a build)

Sketching what "runs displayed as a moving thing" could concretely be,
scoped small enough to be a real slice rather than a research project:

- **A window-track component**: one horizontal bar per live quota window
  (Codex 5h primary, Codex weekly secondary, Claude session, Claude
  weekly, Fable weekly) — a filled portion for elapsed, a moving "now"
  tick, and markers for `schedule.md` entries due to fire inside that
  window. This is the direct answer to "watch the 5-hour window as a
  moving thing" and it's the same data B2 (already ranked #4) needs to
  read live — this view is B2's reason to exist, not separate work.
- **Task tokens, not table rows**: render each active/pending/scheduled
  run as a small token that visually occupies a lane (queued → running →
  responded/parked) rather than a static-status pill in a list. Doesn't
  need real animation to start — even a discrete left-to-right position
  keyed off `phase` is most of the win; motion (CSS transition on
  phase-change) is a cheap upgrade once the discrete version is right.
- **Dedup the report stream first** — the flow surface will look worse,
  not better, if it's animating six copies of the same event.

This is not a slice commitment — `plan-brnrd-dashboard-mvp.md`'s own
estimates put comparable single views at ~1 week each; a live temporal
component is at least that. It's here so the "what would 'live' actually
mean" conversation has a concrete shape to react to, per the maintainer's
own framing: "we don't need the whole persona 5 thing visually now, but
the conceptual live thing layer should be there."

## Plan-surface hygiene: read-before-write, applied not just advised

The maintainer's diagnosis was right and had a visible symptom: this run's
own wake-time bundle carried `plans/Gurio__brr/active.md`'s full text, and
roughly two-thirds of it was an append-only "Prior update: ..." chain —
four dated paragraphs stacked back to 2026-07-04, each one already
restated near-verbatim in `kb/log.md` and `ledger/decisions.md`. That's
"a bunch of context overrides" exactly: the file kept *adding* history
instead of *representing current state* and pointing at where the history
already lives.

Fixed this run, not just named: `active.md`'s header collapsed to one
dated line + a link, on the theory that a wake updating this file should
read what's already true here plus in `kb/log.md`/`ledger/decisions.md`
*before* writing, and add only a new pointer — not another paragraph the
file doesn't need to own. `playbook.md` and `run.md` already say "kb is
the shared through-line, dominion/plan is the working note" — this is
that principle applied to the one file that had quietly stopped following
it.

## Open forks (maintainer's call)

1. **Naming pick** — `envelope loom` (recommended) / `envelope manifold` /
   `envelope trace`, or none of the above.
2. **Priority** — does the live-flow dashboard work jump ahead of #227
   (ToS draft, currently ranked #1) and the morning-briefing scoping, or
   queue behind them? It's a multi-week body of work by the MVP plan's own
   estimates, not a quick reskin.
3. **How literal the Zachtronics metaphor goes** — window-tracks + task-
   tokens (this page's sketch) is the conservative reading; a fuller
   factory-floor visual (Persona-5-adjacent, explicitly *not* wanted yet
   per the maintainer's own line) is the maximal one. Recommend starting
   conservative and letting the aesthetic grow once the underlying live
   data is real (echoes the CPS "ship plain, skin later" resolution
   already accepted 2026-07-04).
4. **Input capture** — explicitly flagged by the maintainer as wanted but
   privacy-sensitive; not scoped here at all pending that call.

## Read next

- [`design-resident-boundary.md`](design-resident-boundary.md) §7 — the
  boundary-state-card concept this naming fork is about.
- [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md) — the
  accepted view inventory and slice plan; §"Gap: Current Planned State
  view" is the precedent for how a maintainer-floated dashboard gap gets
  scoped into this plan.
- [`design-weave-register.md`](design-weave-register.md) §Round 8 — the
  "card" naming collision this naming fork continues.
- `plans/Gurio__brr/active.md` (account dominion) — item 4 (B2) is the
  quota-read gap the window-track proposal above would finally give a
  reason to finish.
