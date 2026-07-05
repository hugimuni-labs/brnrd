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
