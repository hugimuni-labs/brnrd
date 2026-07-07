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
- **Daemon reports now collapse repeated snapshots by repo/record id**:
  the Activity/Runs view and the dashboard's "Recent daemon reports"
  panel keep the freshest row per daemon report instead of replaying the
  same record across reconnects. (Earlier versions rendered every
  token-scoped copy verbatim; fixed 2026-07-06 in
  `src/brnrd/activity_records.py`, `src/brnrd/routers/accounts.py`, and
  `src/brnrd_web/activity_dashboard.py`.)
- **The Budget/Runner-quotas card now renders real per-shell windows** —
  the `UNKNOWN` placeholder is gone. The dashboard reads the daemon-
  published quota snapshot, and the Svelte slice consumes the same
  `/v1/dashboard/quota` payload. (Earlier versions only had the
  placeholder or stale snapshots; fixed 2026-07-06 in the quota-publish
  path and slice 2.)
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

**Renamed 2026-07-05** — accepted, see "Resolved this run" below;
`design-resident-boundary.md` §7 now reads "the envelope loom."

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

## Resolved this run (2026-07-05, same-thread follow-up)

1. **Naming — `envelope loom`, accepted.** "loom sounds good... it makes
   sense also in terms the visible and living user surface... the weave
   and the weaver, and the runners and even shells&cores fold in pretty
   naturally." Supersedes `design-weave-register.md` §Round 8's parallel
   `gauge`/`envelope gauge` recommendation (a different branch reached a
   different candidate for the same fork before the two merged — see that
   page's addendum). `design-resident-boundary.md` §7 renamed. The
   maintainer separately noted "the portals a bit out, if you think of a
   better name, but it is fine" re: `daemon-substrate.md`'s "delivery
   portals" vocabulary — flagged, not acted on; explicitly not a request.
2. **Priority — dashboard live-surface work now leads the ranking.**
   "lets do the live flow dashboard first," direct instruction, resolves
   the fork this section used to pose as open. `plans/Gurio__brr/
   active.md` reordered accordingly. Not a claim that #227 (ToS) stops
   mattering — hosted-execution liability exposure is real and dated
   2026-07-04 — just that it no longer sits ahead of this work in
   execution order; noted in the same plan update.
3. **Persona-5-adjacent — correction, not a resolution.** This page
   previously read the maintainer's line as "explicitly not wanted yet."
   That was wrong: "not true - wanted, but agreeing to postpone if too
   much effort / too hard to do right." The postponement is a cost/
   difficulty call the maintainer is making, not a taste rejection —
   worth keeping straight since the two produce different revisit
   triggers (a cost call revisits when effort drops; a taste rejection
   wouldn't revisit at all). See
   [`design-brand-visual-language.md`](design-brand-visual-language.md)
   for the fuller visual-identity material this connects to (boot-glitch
   animation, HugiMuni/vegvisir/Huginn-Muninn, Loki/Severance reference
   calibration) — new page, out of this one's scope.
4. **Input capture** — still open, still explicitly flagged by the
   maintainer as wanted but privacy-sensitive; not scoped here at all
   pending that call. Untouched this run.

## Zachtronics-mechanics deconstruction (asked for explicitly this run)

The maintainer named the reference class (TIS-100, SpaceChem, Opus Magnum)
but flagged he hasn't played them enough to map their mechanics onto our
situation himself — "I am gonna rely on you deconstructing the Zach's
games mechanics and their relevance to our situation, but only the lanes
are too simple to fit I think." That's the actual ask: the single
queued→running→done lane (already sketched above) is one axis; the
maintainer's own metric list — commits/PRs/tickets interacted, messages
processed, KBs touched, time elapsed, token consumption, CPS progress —
needs more than one visual grammar, not a richer version of the same one.

**What the three games actually share**, stripped of genre chrome: a
constrained space, discrete *cycles* of time, small units moving through
it under rules you can see, and — this part matters as much as the
motion — **a post-run scorecard** that turns the run into a legible
receipt (Opus Magnum's cost/cycles/area report, shown against a par line
and a community histogram). The motion answers "what's happening now";
the scorecard answers "how did that go." Our dashboard needs both, and
today has neither in a temporal form.

**Proposed mapping — different metrics get different mechanics, not one
lane widened:**

- **Commits / PRs / tickets → SpaceChem molecules.** Coarse-grained,
  bonded units that move between discrete stages (queued → running →
  reviewed → merged/closed) along the same lanes already sketched. Low
  frequency, high visual weight — each one is worth noticing individually.
  This *is* the existing "task tokens, not table rows" sketch; naming the
  game it's actually borrowing from.
- **Messages processed → TIS-100 values.** Fine-grained, high-frequency,
  low individual weight — single values streaming node-to-node every
  cycle. Rendered as a thin fast pulse along the *same* lane geometry as
  the molecules above, not a separate widget: two particle scales sharing
  one spatial system is exactly how TIS-100 and SpaceChem already differ
  from each other while both being "Zachtronics." Gives the surface a
  legible fast/slow rhythm instead of one undifferentiated stream of dots.
- **KBs touched → a reactor-floor node map, not a flowing unit at all.**
  The kb is a graph already (103 pages, cross-linked — see this bundle's
  own kb-health graph stats). Render it as a node map; a touched page
  lights up on read/write and decays over a few seconds, the way a
  TIS-100 node highlights while its program executes or an Opus Magnum
  glyph highlights while an arm is on it. This is presence, not flow — the
  right mechanic for "what got read," which is a level, not a stream.
- **Time elapsed → the window-track's moving edge.** Already scoped above
  (window-track component) and already corrected by the maintainer
  earlier this thread: the track *runs out*, it doesn't fill up, and
  changes color by remaining level — that correction *is* a Zachtronics
  reading already (a depleting resource bar, not a progress bar).
- **Token consumption → the Opus Magnum solution report, per-run.** Not a
  live tick — a receipt, shown once a run closes: tokens spent against
  the run's own budget envelope as a par line (there's no community
  histogram to compare against here, but "actual vs. the envelope you were
  given" is the same shape of comparison, just with one bar instead of a
  population). This is the closest fit for the already-shipped
  `envelope loom` naming: the loom is the standing capsule; the solution
  report is what it hands you when a run finishes weaving.
- **CPS progress → an Opus Magnum chapter map, not a flat ranked list.**
  The ranked-moves list in `active.md` already carries dependency
  language ("blocks:", "depends on:") that a flat list renders as prose
  but a puzzle-map renders as position: done / in-flight / blocked-on-X,
  arranged by what unlocks what. Matches the CPS ask's own framing — "the
  forks, the blocks, the decisions" — better than a ranked list does,
  since blocks and forks are relationships, not just order.

**Usability guard, per the maintainer's own ask** ("keep me in line
centered on an average user usability/friendliness... some of my wants
are brnrd-dogfooding specific"): most of the above is resident-facing
detail (token consumption, the envelope loom itself, KB-node presence) —
exactly the material `ornament`/detail-level gating already exists for
(`design-brand-brnrd-brr.md`'s `quiet | moderate | rich` knob). An
external user's first screen should default to the coarse layer (commit/
PR/ticket flow, CPS map, plain quota bars) with the finer mechanics
(message pulses, KB node map) as an opt-in "operator" density level, not
the default — the same "ship plain, skin later" sequencing already
accepted for CPS applies here at the mechanic-selection layer, not just
the visual-polish layer.

**Still a proposal, not a build** — same caveat as the window-track
sketch above: this is the "what would it actually mean" answer the
maintainer asked for, sized to inform a slice plan, not sized as one.

## Same-thread follow-up (2026-07-05, run-260705-2039-m7yw): quota multi-axis, PR/issue extensibility, frontend replaced

A message split by Telegram's length limit across two events
(`evt-...-c2rb`/`evt-...-lff3`, re-sent whole as `evt-...-uxnb` "just in
case") — the middle run (`run-260705-2037-b3y1`) got killed by a daemon
restart mid-handling before it could act on the first half, so nothing
here was previously captured. Three concrete additions to the proposal
above, plus one approval:

1. **Quota needs two axes shown together, not time alone.** The
   window-track sketch above (§"A shape for the live-flow surface") only
   scoped the *time* edge draining. The maintainer's ask is sharper: show
   remaining-% and time-to-refill *together*, sorted, color-coded, per
   window (5h pacing + weekly, per runner shell) — "the token consumption
   is connected to the time and quota windows, but only for subscription
   based cases; the later brnrd-tokens should have their own place (and
   maybe the claude/codex/gemini tokens too, if the user has them)."
   Concretely: the window-track component needs a second encoded
   dimension (e.g. fill-color or a paired numeric readout) for %-remaining
   alongside the position-based time-remaining, and a visual separation
   between metered-subscription windows (Claude session/week, Codex 5h/
   week, Fable week) and any future brnrd-token or raw-provider-token
   ledger — those are a different resource class, not another row in the
   same table. This is the dashboard-side half of #237 (quota-publish
   plumbing) — #237's fix should carry both numbers, not just the single
   `remaining_pct` the Activity view already has a precedent for.
2. **Commits/PRs/issues are semi-hierarchical, and other ticket systems
   are coming.** "commits belong to PRs, PRs and Issues are referenced...
   make design extensible to support later ticket-systems integration
   (linear, jira, etc.), thoroughly reserve a planned space/connector
   shape... but not implement it." Reads as: the SpaceChem-molecule
   mapping above (§Zachtronics-mechanics) should model a commit→PR→issue
   *tree*, not three parallel flat streams, and the data shape backing it
   should carry a `source_system` / connector field now (GitHub today)
   even though only GitHub is wired up — so a Linear or Jira connector
   later is a new adapter, not a schema migration. No implementation this
   run; a reserved column/field is cheap, a real connector isn't scoped.
3. **Frontend stack: replace it, approved, not just audit.** Resolves the
   open item in `plan-brnrd-dashboard-mvp.md` ("frontend stack quality
   audit — not yet done... candidate for its own plan page") one step
   further than an audit: "current frontend stack is likely shit, it is a
   good time to replace it with something modern, extensible, thoroughly
   built, responsive, and little-code, easy-to-maintain." The maintainer
   is explicit he can't specify the stack himself ("I don't know this
   field well enough") and defers the actual pick to us, but sets one
   concrete bar in its place: **"it should survive a fireship review (or
   alike)"** — read as "don't ship something a tech-savvy, snarky
   audience would roast as dated or amateur," the same register as item
   4's "AI-savvy, frontend-focused tech folk" bar from the opening ask,
   now with a sharper, checkable phrasing. Not scoped to a specific
   framework choice in this page — that's real implementation work for
   whichever slice actually rebuilds the frontend — but the constraint is
   now explicit and load-bearing: modern, extensible, responsive,
   low-code-to-maintain, and reviewer-proof, not merely "not the current
   HTMX-era stack."
4. **Approval, not just discussion.** "Otherwise really love your
   proposal, lets implement it" — the live-flow surface + Zachtronics
   mapping above moves from "proposal, not a build" to accepted direction;
   still needs slice-sizing (per the existing ~1 week/view estimate) before
   it's a build turn, but the maintainer-side fork is closed.

## Shipped (2026-07-06): #237 slice 1 — daemon→dashboard quota-publish plumbing

"Alright, merged, let's build it" (same-thread follow-up, after PR #239
merged) read as: start the dashboard live-surface work, slice 1 first —
per `active.md`'s own next-step note, #237 (the dead quota card) is the
prerequisite for the window-track visual, so it went first rather than
the visual itself.

Mirrors the Activity/Plans publish shape a third time, as the ticket
asked:

- `src/brr/gates/cloud.py::_quota_snapshot` — daemon-side collector. Codex
  reads live (`codex_status.load_levels()`, no caching needed — same
  pattern `_collect_levels` already uses). Claude reads the most recently
  cached `/usage` scrape via a new
  `runner_quota.latest_claude_usage_outbox_dir(brr_dir)` helper — the real
  gap named in `plan-director-execution.md` §B2 ("`_fire_due_schedules`'s
  quota read is coded and tested but inert in production — it reads a
  `brr_dir`-level cache nothing writes to yet"): `claude_usage` only ever
  caches into a *run's own* outbox dir, never `brr_dir` itself, so a
  shared-level reader has to go find the freshest one a recent run left
  behind. Fixed in both consumers at once — `cloud.py`'s new quota publish
  and `daemon._fire_due_schedules`'s pacing read, which had the identical
  bug and is now unblocked as a side effect, not a separate follow-up.
- `PUT /v1/daemons/quota` (`src/brnrd/routers/daemons.py`) + `Daemon.
  quota_json`/`quota_updated_at` (`models.py`, migration in
  `migrations.py`) — same last-write-wins shape as `ActivityRecord`/
  `Repo.plan_md`.
- `activity_dashboard.py::_quota_views` replaces
  `_quota_shell_placeholders` — reads the real report, flags a shell
  "stale" past 300s without a fresh publish (the honest-fallback the
  ticket asked for) rather than silently trusting old numbers, and still
  renders an explicit "unknown" placeholder card for any shell with active
  runs but no quota report yet (older daemon build, cold cache) instead of
  omitting the panel. `dashboard.html`'s window row had a latent
  display bug fixed alongside this: it always showed "unknown" whenever
  `used`/`limit` were absent, even with a real `percent` in hand — the
  placeholder was the only shape ever rendered before, so nothing had
  exercised that branch.

**Not done, named for the next slice:** reset is carried as opaque display
text (`session_reset`/`week_reset`, Claude's raw TUI-parsed string; Codex's
reset isn't exposed as a separate field at all yet, only baked into its
`summary` string) — item 1 of the prior entry's "both numbers" ask is
satisfied for percent, but a machine-parseable reset epoch/duration (what
the window-track visual's position-based time-remaining axis will actually
need) isn't there yet. `codex_status.parse_token_count` would need a
`primary_resets_at`/`secondary_resets_at` epoch pass-through alongside the
existing `_remaining_percent` fields — small, additive, deferred rather
than bundled into this slice's diff. brnrd-token/raw-provider-token ledger
(item 1's second half) is untouched — no such ledger exists yet.

## Frontend stack confirmed + scaffolded, reset-epoch delegated (2026-07-06)

Same-thread follow-up: "Agreed on frontend proposed stack" confirms the
prior run's pick — **SvelteKit + Tailwind**, backend stays the existing
`dashboard_stats`/`_quota_views` JSON, no separate auth/data layer. That
resolved the stack half of the prior "next-move" fork; the
delegation-vs-in-thread half (also part of that fork) wasn't explicitly
re-confirmed by that one line, so it's decided here as the reversible,
already-recommended default (option 1: go as scoped) rather than bounced
back for a second round-trip — the maintainer had floated codex-shell
delegation himself the same thread, one message earlier.

Shipped this run:
- `frontend/` — `sv create` scaffold (minimal template, TS, Tailwind,
  prettier, eslint), swapped from `adapter-auto` to `adapter-static` with
  `fallback: 'index.html'` and project-wide `ssr = false`
  (`src/routes/+layout.ts`): a static SPA build, not a Node server of its
  own, matching the "backend stays FastAPI JSON" decision. `npm run build`
  and `npm run lint` both clean. Not wired into `src/brnrd_web/` yet and
  not linked from the live dashboard nav — scaffold only, per the prior
  run's own audit that standing up the replacement is its own slice.
- Reset-epoch plumbing (the gap named in the entry above) was initially
  queued as a **codex-shell respawn** — the first real test of worker-stack
  delegation to another Shell, per the maintainer's own suggestion. The
  task spec (covering both collectors) turned out asymmetric on
  inspection: Codex already parses a raw `resets_at` epoch internally and
  only had to stop discarding it (pure passthrough); Claude's TUI-scraped
  reset is free text with two *different* shapes between windows (session:
  `"11:59pm (Europe/Berlin)"`, no date; week: `"Jul 10, 12am (Europe/
  Berlin)"`, dated) — so that half needs a real next-occurrence-in-timezone
  computation, not a lookup.
- **The respawn surfaced a real daemon bug**, not just a delegation
  exercise: the queued event was counted identically to an unaddressed
  user message by `_pending_events_for_agent`, so the run that created it
  could never see `pending_event_count` reach zero (dispatching a respawn
  as a new run requires the *current* run to end and free the
  single-flight slot first) — the Stop-hook's fold-in-or-explain gate kept
  re-firing every phase even after `.card` correctly explained the event
  was queued on purpose. Fixed (excludes respawn-origin events from the
  count) and shipped as its own PR rather than folded silently into the
  dashboard work — see `kb/log.md` §2026-07-06.
- Given the loop, **the reset-epoch task was built directly this same run
  instead of waiting on the respawn dispatch**, and the now-redundant
  respawn event was canceled (marked `done`) rather than left to dispatch
  a duplicate. Shipped as its own PR: `session_resets_at`/`week_resets_at`/
  `week_models[*].resets_at` (Claude, computed via a new `_reset_epoch()`)
  and `primary_resets_at`/`secondary_resets_at` (Codex, passthrough).

**Budget/quota note, same thread:** a same-thread follow-up worried this
implementation might outrun the run's time budget or get killed by quota.
First answer: `.keepalive` already lets a run stretch to the daemon's hard
cap (4h for this run's 1h soft budget) with no harness change. That
wasn't the end of it — a real pushback followed ("users may not know
about the cap... seems unreasonable"), acted on directly rather than
re-explained: `.brr/config`'s `runner.timeout_seconds` raised 3600s→7200s
for this repo (hard cap now 8h). Whether the global code default
(`DEFAULT_RUNNER_TIMEOUT`) should also move, and whether a safety-capped
upsize vs. a truly uncapped budget is the right end state, are both named
as open forks rather than decided unilaterally — see `plans/Gurio__brr/
active.md` item 1 and the decision ledger.

Branches/PRs this run: `brr/frontend-svelte-scaffold-2026-07-06` (#241,
scaffold), `brr/fix-respawn-pending-attention-2026-07-06` (#242, the
Stop-hook bug), `brr/reset-epoch-plumbing-2026-07-06` (#243, the plumbing
itself, built directly).

Next: review and merge #241/#242/#243, then slice 2 = the window-track
view itself, built inside `src/frontend/` against real numbers.

**Addendum (2026-07-06):** `frontend/` moved to `src/frontend/` per a
same-thread ask ("the frontend folder could be moved into src/"); the
`.upsun/config.yaml` build hook and static-mount path were updated to
match in the same pass (`cd src/frontend && npm ci && npm run build`,
served at `/app/`). Build+lint verified clean from the new location
before committing. The `frontend/` mentions above are left as written —
historically accurate for what shipped at the time — this is the pointer
for the rename.

## Shipped (2026-07-06): slice 2 — the window-track view itself

"lets start with slice 2!" — same-thread go-ahead. Built inside
`src/frontend`, against real numbers, per the prior entry's own next-step:

- `GET /v1/dashboard/quota` (`src/brnrd_web/activity_dashboard.py::
  dashboard_quota_api`) — JSON twin of the Jinja dashboard's quota card,
  same session-cookie auth, same `_quota_views` data. 401 (not a login
  redirect) when unauthenticated, since this is fetched by JS.
- Closed the reset-epoch gap the prior slice named and deferred:
  `src/brr/gates/cloud.py`'s `_quota_window`/`_codex_quota_shell`/
  `_claude_quota_shell` now carry `resets_at` (unix epoch) alongside the
  existing display-text `reset` — `claude_usage.py`/`codex_status.py`
  already computed these epochs (`session_resets_at`/`week_resets_at`,
  `primary_resets_at`/`secondary_resets_at`) but nothing published them
  past the daemon boundary. `QuotaWindowIn` (`src/brnrd/schemas.py`)
  gained the matching field — without it pydantic's default
  `extra="ignore"` would have silently dropped it from `model_dump()`.
- `src/frontend/src/lib/{quota.ts,WindowTrack.svelte}` +
  `src/routes/+page.svelte` — the first real screen. One draining bar per
  window (dataviz skill's fixed status palette: good/warning/critical,
  never a categorical hue, icon+label pairing so color never carries
  meaning alone), a live countdown ticking off `resets_at` client-side
  between polls (20s poll, 1s tick), a stale-report badge, and a 401 →
  "log in" state rather than a blank screen. Polls the same
  daemon-published data the Jinja dashboard still renders — the two
  surfaces agree until the Jinja one is retired.
- `vite.config.ts` gained a dev-server proxy (`/v1`, `/login`, etc. →
  `localhost:8000` by default, `BRNRD_DEV_TARGET` override) mirroring
  `.upsun/config.yaml`'s passthru list, so `npm run dev` has a working
  local loop against a real `brnrd` backend — this didn't exist yet
  since nothing had fetched cross-origin JSON from the scaffold before.

1316 tests pass (backend); `svelte-check`/`eslint`/`prettier`/`vite build`
clean (frontend). Not done, still real: the KB/message/CPS mechanics from
the Zachtronics deconstruction above (only the time-window mechanic is
built); the Jinja dashboard's own quota card is untouched (still renders
the old way) — retiring it is a separate call once the SvelteKit surface
covers enough of `dashboard.html` to replace it outright, not bundled
into this slice. Branch: `brr/window-track-quota-view-2026-07-06`.

## Cohered with the quota-scheduling loom (2026-07-06)

[`design-quota-scheduling-loom.md`](design-quota-scheduling-loom.md)
§"Cohering with the dashboard's rendering vocabulary" names three places
where that page's cost/tracking design and this page's Zachtronics
rendering mapping are one data model, not two adjacent ones: the commit/
PR/issue semi-hierarchy (§Zachtronics-mechanics above, "commits belong to
PRs...") *is* that page's `run_ledger.source_system`/`external_refs`
connector field, not a separate render-only shape; the Opus Magnum
"solution report" mapping above is a view over that page's per-run row,
not a separate artifact; and the window-track's live time-axis and that
page's per-run `weekly_pct_delta`/`five_hour_pct_delta` are the same
percent at two grains (window-total vs. this-run's-share). Whoever slices
the commit/PR/issue render next should read that section first — the
connector field only needs building once.

## Reconsidered 2026-07-06: account-scoped control surface, PR review as its own mechanic

Direct maintainer prompt, same shape as the "when the task asks you to
reconsider" contract: "the execution control surface should be per
account rather than per repo... maybe needs to be reconsidered" plus "the
PRs are not properly integrated into the game-ified planning and
execution control surface."

**Checked against the actual shipped code before answering, not assumed.**
The hypothesis "the dashboard is repo-first and needs restructuring" turns
out false for the two pages already built: `/activity`
(`activity_dashboard.py::activity_page`) already queries
`_activity_views(db, repos, repo_id=repo_id or None)` — all repos, `repo_id`
an optional filter, never a required picker. `/plans`
(`plans_dashboard.py::plans_page`) already renders every repo's plan on
one page (`repo_plans` list) plus the account-level `cross_repo_plan_md`
and `decision_ledger_md`. Quota (`#237`, `#240`) was account/Shell-scoped
from the start — quota isn't a per-repo concept. So the *planning and
history* surfaces already match `decision-account-centered-daemon.md`'s
"one daemon per account, repo-scoped runs underneath it" — repo is
already a tag/filter on an account-first view, not a top-level gate.
Good news: nothing shipped needs undoing.

**The real gap is the *live* surface, and it doesn't exist yet.** Confirmed
by the Mode block's own line every wake carries: `coexisting-runs=
unimplemented (single-flight per dominion; no concurrent-run view yet)`.
The window-track slice shipped this run is quota (a rate, not a run list);
no page renders "what is my daemon actually doing right now, across every
repo it touches" — the closest thing today is the resident's own bundle
(`Also awake right now`), visible only to a resident wake, never to the
human. This is precisely the gap the maintainer is pointing at, just not
where the hypothesis first landed: the *live-flow* half of this page's own
proposal (§"A shape for the live-flow surface" above) is still unbuilt,
and when it *is* built, it must be account-scoped natively, not nested
under a repo page and then generalized later — a live run is organized by
"what's my daemon doing," not "which repo am I looking at."

**Concrete proof, from this same run:** while working this thread, a
director-tick event was found stuck in an infinite crash-restart loop —
26+ retries over ~50 minutes, real compute, real presence-registry churn
— invisible to the maintainer the entire time because the only place that
state existed was ephemeral `.brr/presence/*.json` files and a resident's
own wake-time bundle. A live, account-scoped runs view (even a bare list:
run id, repo, conversation, started-at, status) would have surfaced this
in seconds instead of it being found by accident during unrelated work.
This is the strongest concrete argument for building the live-runs slice,
not just a hypothetical one — see `kb/log.md` §2026-07-06 "crash-restart
loop" and PR #256 for the fix; this page names the *visibility* gap the
incident also exposed, which the fix itself doesn't address.

**PR review: not a stage in the run lane, a separate mechanic.** The
Zachtronics-mechanics deconstruction above already names a "reviewed"
stage inside the commit/PR/ticket "molecule" lane. The maintainer's sharper
point, re-read against that proposal: a PR sitting in "awaiting review" is
not resident-cost-bearing the way "queued → running" is — no tokens, no
wall-clock draining against a budget envelope, nothing the window-track's
core metaphor (a depleting resource) actually describes. It's a *different*
clock: human attention latency, unbounded, calendar-time not run-time.
Folding it into the same lane as an in-flight run would misrepresent what's
actually happening — the loom's depleting-resource metaphor is exactly
wrong for "waiting on a human whenever they next look." The fix isn't
"add a reviewed stage to the molecule lane," it's **a second, distinct
lane**: an account-scoped review queue (every open, unreviewed PR across
every repo the account touches — same account-first shape as Activity/
Plans), rendered as a bounded/aging queue rather than a draining track —
closer to a WIP buffer filling up than a countdown. Age is the signal
worth showing (a PR open 3 hours reads differently than one open 3 weeks),
not urgency manufactured to "scream" — the maintainer explicitly affirmed
checking GitHub on his own cadence is fine ergonomically; what's missing
isn't a nag, it's a legible "how much is actually waiting on me, across
everything" the resident's own planning loop can also read (the director
tick already greps `gh pr list` fresh every firing — this would give it,
and the human, the same durable, queryable answer instead of two separate
re-derivations of the same fact).

**What this changes in the existing plan, concretely:** both additions to
the slice queue landed natively account-scoped, not repo-first-then-
generalized: (1) a live/coexisting-runs view (#258) — daemon-side, this
needed a publish step mirroring the Activity/Plans/Quota pattern a fourth
time (`Daemon.live_runs_json` or similar, refreshed on the same heartbeat
the presence registry already ticks on); (2) a PR-review-queue lane
(#259), sourced the same way the director tick already gathers `gh pr
list` per repo, but persisted and rendered rather than re-derived
silently every 5h. Both are now shipped below; the architectural point
that they should be first-class account views still stands.
Cross-links: `decision-account-centered-daemon.md`
(the architecture this reconsideration confirms, not revises),
`kb/design-director-loop.md` §"Concurrent sub-spawns" (a spawn child is
exactly a "coexisting run" the live view would need to show, including
its `parent_run_id` rollup relationship to its parent).

## Shipped (2026-07-07): #258, live/coexisting-runs view

Built directly (not via `spawn:` — see `kb/design-director-loop.md`
§"spawn: can't be dogfooded in the run that lands it" for why), same
run as the sub-spawn slice-1 landing fix. Mirrors Activity/Plans/Quota a
fourth time exactly as scoped above: `src/brr/gates/cloud.py::
_live_runs_snapshot` reads the local presence registry
(`src/brr/presence.py`), `_publish_live_runs` PUTs `PUT
/v1/daemons/live-runs` alongside the other three publishers in
`_loop_once`; `Daemon.live_runs_json`/`live_runs_updated_at` (+
migration); `activity_dashboard.py::_live_runs_views` flattens/dedupes
across every `Daemon` row an account owns (one per repo it's registered
under — the same physical daemon publishes the same presence list under
each, deduped by run identity, freshest report wins); `GET
/v1/dashboard/live-runs` JSON endpoint. Frontend:
`src/frontend/src/lib/{liveRuns.ts,LiveRuns.svelte}`, wired into
`+page.svelte` below the quota tracks, same fixed status palette as
`WindowTrack.svelte`. 1339 backend tests pass; frontend build/lint/
svelte-check clean. PR #261, merged, deployed — verified live: `upsun
activity:log` showed the build+deploy succeed, the deployed JS bundle
was confirmed carrying the `LiveRuns` component (`grep` on the fetched
chunk), and the production postgres schema was confirmed carrying
`live_runs_json`/`live_runs_updated_at` via `upsun ssh` + `psql \d
daemons` — not just "the PR merged," the actual running system.

Not done, named for whoever picks it up next: `parent_run_id`/
`is_subspawn` enrichment (a spawn child showing its parent relationship)
was scoped out of this slice — the presence registry entries don't carry
that field today, only `run_ledger` rows do, and joining them was judged
gold-plating for a first cut.

## Shipped (2026-07-07): #259, PR-review-queue lane

Built as the second dashboard slice from the reconsidered live-surface
plan. Mirrors the Activity/Plans/Quota/Live-runs publish shape: `src/brr/
gates/cloud.py::_pr_review_snapshot` gathers open PRs with `gh pr list`
per connected repo, `_publish_pr_review_queue` PUTs `PUT
/v1/daemons/pr-review-queue` beside the other heartbeat publishers in
`_loop_once`; `Daemon.pr_review_queue_json`/`pr_review_queue_updated_at`
(+ migration); `activity_dashboard.py::_pr_review_queue_views` flattens/
dedupes across every `Daemon` row an account owns (one per repo it's
registered under — freshest report wins per `repo_label` + PR number);
`GET /v1/dashboard/pr-review-queue` JSON endpoint. Frontend:
`src/frontend/src/lib/{prReviewQueue.ts,PRReviewQueue.svelte}`, wired
into `+page.svelte` below the live-runs slice, with age from
`created_at`, stale after 300s, and a stable status palette that turns
draft/open into a quick visual read. Backend tests and the frontend build
stay green.

## Shipped (2026-07-07): the "lying Claude usage panel" + credits exposure

Direct maintainer report: the live quota bars were correct for Codex but
not for Claude ("claude's currently at 91% weekly used, and 5h used
completely" — confirmed by a fresh manual `/usage` probe run this same
thought). Root-caused, not just re-scraped:

**The staleness bug.** `_quota_views`' `stale` flag (`activity_dashboard.py`,
#237) was gated on `Daemon.quota_updated_at` — when the *daemon last
published* the quota payload. The daemon PUTs that payload every ~25-30s
poll tick regardless of whether the underlying data changed, so that
timestamp is *always* fresh — it can never trip the staleness gate. Claude's
quota shell is a cached interactive `/usage` PTY scrape
(`claude_usage.py`) that only refreshes while a Claude run is actively
heartbeating; with no Claude run active for a stretch, the dashboard kept
showing hours-old numbers flagged "known", never "stale". Codex reads its
quota live off the session rollout every tick, so it never exhibited this
gap — which is exactly why the maintainer saw "codex right, claude wrong."
Fixed by forwarding the scrape's own `updated_at` per shell
(`cloud.py::_claude_quota_shell`/`_codex_quota_shell`) and measuring
staleness against *that* clock, falling back to the publish timestamp only
when a shell carries none (`QuotaShellIn.updated_at` in `schemas.py`).

**Credits exposure.** The maintainer confirmed live, same thread: this very
run kept working (and billing, ~$1 → $1.15 → $3.92 over the session)
straight through Claude's 5h window hitting 100% used — the account falls
through to metered credits rather than blocking. `claude_status.py` already
collects a real per-run `total_cost_usd` from Claude's headless result
JSON for the boot-prompt `spend` facet, but nothing published it anywhere
visible. Added `cloud.py::_claude_credits_block` (new
`runner_quota.latest_claude_spend_outbox_dir` helper, same freshest-mtime
pattern as the quota one but over `.claude-result-levels.json`), a
`credits` field on the claude shell payload (`QuotaCreditsIn` schema), and
a small line in `WindowTrack.svelte` under the two bars — fixed sky hue,
the same "outside the firelight" signifier the stale badge already uses,
not a new status color. No credits collector exists for Codex (no
comparable metered-overage behavior observed there yet), so its shell
simply never carries the key — absent, not a fake zero.

**Follow-up, same date: idle refresh + account credits.** The stale badge
alone was still too weak: a stale Claude row could visually paint old 5h /
weekly percentages as if they were current. The dashboard publisher now
refreshes the cached Claude `/usage` PTY scrape on a bounded idle cadence
(`cloud.py::_CLAUDE_QUOTA_PUBLISH_MAX_AGE_SECONDS`, 240s) shorter than the
web stale threshold, while the run-heartbeat collector stays on the faster
quota-only path. If a refresh still fails or goes stale, `_quota_views`
keeps the shell row visible but clears numeric window values so the bars
render as `unknown`, not false headroom. The same pass fixed the parser
edge seen live where Claude's screen-reader output glued `Esc to cancel`
to `Current session`, and added a slow-path `wait_for_credits` capture so
Claude's `Usage credits` section (`spent / cap / reset`) reaches the same
`credits` block. A live probe on 2026-07-07 read Claude credits as 79%
left, EUR 8.69 / EUR 40.00 spent, reset Aug 1 (Europe/Berlin). Codex's
current rollout payload has a `rate_limits.credits` slot, but it was
`null` on this account/session; the dashboard does not display a fabricated
zero until Codex supplies a non-null balance.

Also fixed in the same run (a same-thread follow-up, not part of the
credits ask but landed on the same branch/PR): `remote_scm` stayed
`absent` for an entire run even after the resident created a real PR
mid-thought (`gh pr create` — not a GitHub-sourced task, which is the only
path that ever populated `task.meta['github_pr_number']`). Added a `.pr`
control file (same tier as `.card`/`.keepalive`) the resident writes right
after creating a PR; the daemon reads it each heartbeat
(`daemon._read_pr_control`) and prefers it over `task.meta`, keeping
`remote_scm` network-free per its own design (`brr.facets` docstring).
Documented in `src/brr/prompts/daemon-substrate.md`.

## Read next

- [`plan-loom-realtime-build.md`](plan-loom-realtime-build.md) — the
  week-scoped, ranked slices that turn this page's six-mechanic proposal
  into an actual build order; start there for "what ships next."
- [`design-quota-scheduling-loom.md`](design-quota-scheduling-loom.md) —
  the cost/tracking-table design this page's rendering vocabulary now
  explicitly shares a data model with (see above).
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
