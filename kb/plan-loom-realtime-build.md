# Plan: the loom realtime build — from polling gauges to a watchable ticker

Status: active — opened 2026-07-07 (run-260707-1728-czlk); slices 0/1
shipped that run, slice 2 (+ a root-canvas bug found while building it)
shipped 2026-07-07 (run-260707-1849-hnj8), slice 3 and the amber/ice
chrome pass shipped 2026-07-07 (run-260707-1930-7074) — see §Slice 1.5,
§Slice 2, and §Slice 3.
Direct response to
"you should realistically deeply expand the path to an actual loom
implementation... what is the minimal but true and evolvable shape we *can*
deliver within a week." [`design-quota-scheduling-loom.md`](design-quota-scheduling-loom.md)
and [`design-dashboard-live-surface.md`](design-dashboard-live-surface.md)
hold the reasoning and the six-mechanic deconstruction; this page converts
that into dated, ranked, checkable slices — the thing both pages were
missing. When a slice here disagrees with either design page, this page and
the live code win; the design pages stay the record of *why*.

## The gap, checked against running code, not assumed

Both design pages already diagnose "we have real data, no realtime feel."
This page checked exactly where that breaks, because "realtime" is not one
gap, it's two, and they need different fixes:

1. **Backend publish cadence was bounded by an unrelated long-poll.**
   `gates/cloud.py::_loop_once` publishes the dashboard snapshots
   (activity, plans, quota, live-runs, PR-review-queue, run-ledger) once per iteration,
   and the iteration itself is paced by the *inbox* long-poll's `wait=25`
   (`_POLL_WAIT_S = 25`, `gates/cloud.py:20`) — a constant chosen for chat
   responsiveness, never for dashboard freshness. Every published snapshot
   is therefore up to ~25s stale by construction, coupled to a completely
   unrelated concern.
2. **The frontend already polls — just not fast enough, and with no
   motion.** `+page.svelte` (not a gap I assumed away: checked directly)
   already runs `setInterval(refresh, POLL_MS)` at `POLL_MS = 20_000`, plus
   a 1s local tick for countdown rendering. So the skeleton for "watch it
   move" exists today. What's missing: the interval is 4x the "2 second
   delay is acceptable" bar, and every one of `LiveRuns.svelte`/
   `PRReviewQueue.svelte`/`WindowTrack.svelte` re-renders a plain list/bar
   on refresh — no enter/exit, no motion, nothing that reads as a *tick*
   rather than a page that redrew itself.

Net: today's ceiling is ~20-45s combined staleness with zero animated
motion at any cadence. Tightening the interval alone would still just be a
faster-refreshing table. Both dimensions have to move for this to become
the thing the maintainer is asking for — a surface where "the window close"
is something you can watch happen, not infer from a changed number.

Six mechanics were named in `design-dashboard-live-surface.md`
§Zachtronics-mechanics. Split by whether they need new backend collection:

- **Zero new backend data needed** (all sourced from already-shipped
  publishers): the window-track's draining edge (quota, shipped), the
  live-runs lane (queued→running→done, shipped), the PR-review-queue lane
  (shipped), and the token-consumption "solution report" (shipped in slice
  3: `run_ledger.jsonl` rows were already written per closed run; the slice
  added the server mirror, dashboard feed, and receipt card).
- **New backend collection required**: the KB node-map (needs read/write
  eventing on kb access — doesn't exist), the TIS-100 message-value pulse
  (needs a per-message event stream — doesn't exist), the CPS chapter-map
  (needs `active.md`'s "blocks:"/"depends on:" prose parsed into a graph —
  doesn't exist).

A week that tries to build all six ships none of them well. A week that
builds only the first group ships something real, live, and honest about
what it covers — and doesn't touch anything that isn't already trustworthy
data.

## Slices

### Slice 0 — decouple dashboard publish from the chat long-poll — owner: resident — *shipped 2026-07-07, this run*

`gates/cloud.py` gets a second daemon thread (`_dashboard_publish_loop`,
started from `run_loop` alongside the existing inbox loop) publishing the
same snapshots (`_publish_activity`/`_plans`/`_quota`/`_live_runs`/
`_pr_review_queue`/`_run_ledger`) every `_DASHBOARD_PUBLISH_INTERVAL_S` (3s), independent
of `_loop_once`'s 25s inbox long-poll. `_loop_once` keeps its own publish
calls too — harmless, idempotent overwrites, not worth touching the tested
main path for. Slice 0 itself had no schema change or new endpoint; slice
3 later joined the same publish cadence with the run-ledger snapshot.
Regression tests:
`test_dashboard_publish_tick_publishes_all_six_snapshots`,
`test_dashboard_publish_tick_noop_without_configured_state`,
`test_run_loop_starts_dashboard_publish_thread`. Full suite green (1366
passed).

### Slice 1 — tighten the frontend tick + real motion on the three live lanes — owner: resident — *shipped 2026-07-07, this run*

- `+page.svelte`: `POLL_MS` 20\_000 → 2\_000, matching the "2s acceptable"
  bar now that slice 0 makes backend data actually that fresh.
- `LiveRuns.svelte` / `PRReviewQueue.svelte`: added `svelte/transition`
  (`fly` in, `fade` out) and `svelte/animate` (`flip`) to the existing
  keyed `{#each}` blocks — a new live run now slides in, a resolved PR
  fades out, a reordered item animates to its new position, instead of a
  silent re-render. `WindowTrack.svelte` already had a CSS width
  transition on the draining bar; it needed the faster poll, not new
  motion code. Build/lint/`svelte-check` clean (0 errors/warnings).

### Slice 1.5 — the root-canvas bug, found while building slice 2 — owner: resident — *shipped 2026-07-07, run-260707-1849-hnj8*

Not in the original slice list — found by actually looking, not assumed:
asked to "check the screenshots to get an idea of how wrong it currently
looks," but the daemon has no telegram-photo ingestion at all (checked;
no `photo`/`file_id`/download code path anywhere in `src/brr`), so the
maintainer's own screenshots weren't reachable this run. Screenshotted
`https://brnrd.dev/` directly instead (Playwright + a real browser,
installed fresh this run) and found the actual bug: every component
(`WindowTrack`/`LiveRuns`/`PRReviewQueue`) is built against
`bg-slate-900`/`text-slate-100` — i.e. assumes a dark page — but **nothing
in the app ever sets a page-level background**. `grep` for
`bg-slate-950`/`min-h-screen` across `src/frontend/src` returned zero
hits. Net effect, confirmed via screenshot: a near-white page, near-
invisible pale-gray-on-white headings, translucent cards that read as
blank. This is likely most of "how wrong it is on how many various
levels" — a foundational bug, not a polish gap, and higher-leverage to
fix than any single component's redesign. Fixed in
`src/frontend/src/routes/layout.css`: `html { color-scheme: dark }` +
`body { background-color: #020617; color: #f1f5f9 }`. Verified before/
after via local Playwright screenshots (desktop + mobile viewport, the
maintainer's own primary device) — see the PR for both.

### Slice 2 — the first real mechanic: live-runs as a lane, not a list — owner: resident — [#270](https://github.com/Gurio/brr/issues/270) — *shipped 2026-07-07, run-260707-1849-hnj8*

Re-checked the issue's own "queued/running/done positions" framing
against the real data before building it blind: `presence.list_active`
(`src/brr/presence.py`) only ever holds *active* entries — registered on
run start, deregistered on finish — so there is no queued or done state
to render, only running-or-gone. "Done" already reads as the pre-existing
fade-out exit transition; "queued" isn't representable without a new
backend collector, which this plan deliberately deferred to keep this
slice at zero new backend data (see the gap-analysis table above) — not
silently built past that scope, and not silently reinterpreted without
saying so.

What shipped instead, honest about what the data actually carries:
`LiveRuns.svelte` now renders a responsive card grid (was a `<ul>` list)
— each run a card with a status dot + badge, primary/secondary label, age,
and an indeterminate scanning activity bar (no known total duration to
bind a real percent to, so a moving stripe — the Zachtronics "in motion"
tell — rather than a fabricated fill). The badge derives a real second
state from data slice 0/1 already ship fresh: `running` (heartbeat within
90s) vs. `stalling` (heartbeat older than that but not yet pruned at the
registry's 300s cutoff) — the same three-tier status palette as
`WindowTrack` (ample/low/critical → running/stalling/unknown), not a new
one. Position-in-lane motion reuses the existing keyed `{#each}` +
`svelte/animate:flip` from slice 1; a card moving order on refresh was
already correct behavior, it just needed to be a card, not a row.

Kept the palette question exactly as scoped in the design page and
reinforced live by the maintainer same-thread: psyche.network is a
container/element reference (card + progress-bar + status-badge shape)
only — substance, color, and composition stay this project's own
hearth/frost direction, not psyche's mint-green. Slice 2 deliberately
held the shared slate chrome rather than doing a partial recolor; slice 3
then applied the amber/ice pass across all live lanes at once (warm void
canvas, amber primary labels, stone chrome/meta, sky as stale/link/cold
signifier) so the dashboard reads as one theme instead of a lane-by-lane
patchwork.

Build/lint/`svelte-check` clean (0 errors/warnings); no backend touched,
no backend tests re-run.

Slices 0+1 shipped together as the single next largest actionable, exactly
as scoped: two files' worth of backend loop change, three components'
worth of frontend interval/transition change, zero new schema, zero new
endpoint, fully reversible.

### Slice 3 — the receipt: per-run solution-report card — owner: resident — [#271](https://github.com/Gurio/brr/issues/271) — *shipped 2026-07-07, run-260707-1930-7074*

Shipped the first backend-expanding loom slice. `Daemon.run_ledger_json`/
`run_ledger_updated_at` (+ migration) mirror the existing live-runs/
PR-review-queue publish pattern; `RunLedgerRowIn` keeps every field from
`src/brr/run_ledger.py::_ROW_FIELDS` nullable because ledger evidence is
honestly partial; `PUT /v1/daemons/run-ledger` stores the latest report.
The dashboard reads it with `GET /v1/dashboard/run-ledger?limit=N` (default
10, cap 50), dedupes by `run_id` across daemon repo registrations, keeps
the freshest report, and sorts newest `ended_at` first. `cloud.py` tails
the last 20 physical ledger lines, skips malformed JSON rows, and publishes
the snapshot from both dashboard publish call sites.

Frontend: `runLedger.ts` + `RunLedgerReceipt.svelte`, wired under the PR
review queue. Cards render the run label (`task_classification` or
`repo_label`), wall-clock, token in/out, weekly/5h deltas, and subscription
USD attribution; nulls render as `—`, matching the ledger's "unavailable →
null, not failure" invariant. The existing keyed enter/exit/flip motion is
enough for the "receipt just printed" behavior: a row only exists after a
run closes and appends to the ledger.

Same pass shipped the amber/ice chrome requested with #271: warm void body
canvas, parchment text, amber primary/heading labels, stone cards/meta/
tracks, and sky for stale badges + links. The fixed status constants stay
unthemed.

### Explicitly not this week — narrower than it reads, worth re-checking after slice 3

KB node-map, message-value pulse, CPS chapter-map — named in full above.
Each needs a new backend collector this plan deliberately doesn't start,
since none of slices 0-3 depended on them and building a collector before its
consumer is exactly the "accreted, not structured" pattern this page exists
to stop. Revisit now that slices 0-3 are live and the "does this actually read
as a loom" question has a real screen to answer it against, not a diagram
— that condition is now stronger than it was after slice 2 alone. Not
reopened this run because #271 + palette was the direct maintainer ask, not
because the other mechanics have fallen out of scope.

## Read next

[`design-dashboard-live-surface.md`](design-dashboard-live-surface.md) —
the six-mechanic reasoning and prior shipped slices this plan builds on.
[`design-quota-scheduling-loom.md`](design-quota-scheduling-loom.md) — the
`run_ledger` schema slice 3 reads from, and why token consumption never
backfills to a dollar figure outside the weekly window.
