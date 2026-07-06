# Design: the quota-scheduling loom — cost-per-item, span tracking, user-owned pacing

Status: active, opened 2026-07-06 (telegram thread, main event +
follow-up evt-...oyak). Companion to
[`design-dashboard-live-surface.md`](design-dashboard-live-surface.md)
(the Zachtronics-mechanics deconstruction this page's economics *render
through*) and [`plan-director-execution.md`](plan-director-execution.md)
§B6 (weekly-quota smoothing — this page is the missing half of that
workstream, not a parallel one). This page exists because the maintainer's
message this run went past "how do we visualize quota" (already answered)
into "what is the actual mechanic, and how does it teach users to own
their own load" — a product-shape question, not a rendering one.

## The core reframe (verbatim intent, compressed)

Providers (Anthropic, OpenAI) meter usage in 5h session windows plus a
weekly window. The maintainer's read of *why*: this isn't arbitrary
throttling, it's load-smoothing — providers have an incentive to spread
demand across the timeline rather than let every subscriber burn their
whole allocation in the first hour of a billing period, because unsmoothed
demand means provisioning (and paying) for peak rather than average load.
That incentive isn't going away. Two user failure modes fall out of it
directly:

- **once-a-day-session pattern** → the weekly quota goes mostly unused,
  less total work gets done than the subscription paid for.
- **maximize every session's burn** → the weekly allocation is gone in
  ~3 days, then the user is quota-dead for the rest of the week.

Neither is a user mistake exactly — it's an absence of visibility. The
user has no rendered view of "how much do I have left, across which
windows, and what would spending it on *this* task cost me." brnrd
already sits at exactly the seam that could supply that view (director
tick, dashboard, the resident's own CPS-ranked move list) — it just
doesn't yet.

**The proposed mechanic, stated plainly**: every workable item (a CPS
ranked move, an ad-hoc telegram/dashboard-triggered run — "out-of-
planning," in the maintainer's words, but *still* placed on the loom
automatically once it exists) carries a **cost estimate** the resident
attaches when it proposes the item. The estimate is guessed from
historical span data the daemon has to start actually keeping (per-task
token/time/window-% burn, logged, queryable by the resident mid-run — this
is new instrumentation, not a report format). The user sees, per item:
roughly what it will cost, across which quota windows, and can choose to
run it now, queue it against a specific window's remaining headroom, or
defer it — the same way a Zachtronics player looks at a level's rules
before committing an approach. The resident, and the director tick
specifically ("the director tick should have its place there too"), is the
thing continuously re-deriving that queue against live quota state, not a
one-time planner.

Two disclaimers the maintainer named as load-bearing, not incidental:

1. **Estimates are guesses, and brnrd says so.** No claim to precision —
   the estimate is directional, sized from history, always fallible. This
   is not new territory: `decision-hosted-execution-liability.md` already
   establishes brnrd disclaims correctness of *execution*; this extends
   the same posture to disclaiming correctness of *cost prediction*.
2. **Kill switches are available, opt-in.** A user who doesn't trust the
   pacing can hard-stop a run mid-flight rather than trust the estimate to
   self-correct. Not scoped to a mechanism here — named as a requirement
   the eventual implementation must satisfy, likely already close to
   existing budget/`.keepalive` machinery (`daemon-substrate.md`) rather
   than new surface.

## What has to be tracked (the actual gap)

The maintainer's own list, verbatim, is the spec for what's currently
*not* logged anywhere queryable:

- token consumption (raw, per-provider)
- 5h-window percent consumed
- 1-week-window percent consumed
- USD cost, subscription-attributed (i.e., "this task cost you N% of a
  $20/mo subscription you're already paying for")
- USD cost, credit/overage-attributed (i.e., "this task would cost you
  $X of wallet credits if run as managed compute" — ties directly into
  [`decision-pricing-shape.md`](decision-pricing-shape.md)'s credit-bucket
  model)

— each of these needed **twice**: as a historical actual (what a
comparable past task cost, the basis for the estimate) and as a forward
guess (what *this* queued task is expected to cost). The maintainer flags
the two $-centered numbers as the most important to render for actual
decision-making — time/token/percent numbers matter for the resident's own
estimation math, but the user decides in dollars.

This is a **real, current gap**, not a rendering gap:
[`plan-director-execution.md`](plan-director-execution.md) §B6 is already
blocked on exactly this — "needs an observed week or two of per-runner
daily burn before the smoothing curve and routing weight are more than a
guess — not a code gap, a data gap." This message is effectively B6's
data requirement stated top-down, from the product-need side, rather than
bottom-up from "the smoothing algorithm needs input." **They're the same
missing table.** Whoever scopes B6 next should scope it as *this* page's
tracking table, not a narrower one — building the log twice (once for
smoothing, once for cost display) would be the redundancy this repo's
kb-health preflight already warns about in other pages.

## Tracking-table schema (etched 2026-07-06)

Etched in response to a direct ask ("could we make your life easier
somehow" — spend-per-core is real but disstructured data to guesstimate
from) plus two load-bearing technical notes from the same message, both
folded in below rather than left as caveats:

1. **Claude's usage number is a TUI scrape (`claude_usage.py`), not a free
   read** — it has to be *synced by the daemon at the moment a run closes
   out*, not lazily at next-wake. A closed run's cost calculation is the
   trigger, not a side effect of some other wake happening to need quota.
2. **Weekly-window percent maps cleanly to subscription dollars; the 5h
   window does not, and shouldn't be forced to.** The 5h window resets
   every five hours regardless of spend — it's a *pacing/rate-limit*
   signal (this page's own opening reframe: anti-burst, not a cost meter).
   Only the weekly bucket has a defined `$ = (subscription_price / 100) ×
   pct_delta` relationship. A schema that tries to derive a 5h-window
   dollar figure would be manufacturing precision that isn't there —
   exactly the "estimates are guesses, brnrd says so" disclaimer already
   accepted above, now made concrete as a rule: **5h fields are
   pacing-only and carry no USD derivation; only weekly fields do.**

**One row per closed run** (`run_ledger`, name provisional) is the unit —
not a raw per-token-event span log. The resident's own estimation need is
"what did a task shaped like this one cost last time," which is a rollup
question; a finer-grained event stream is real future instrumentation
(useful for audit/debugging a specific run) but out of scope for what
unblocks estimation, B6, and the cost-display ask all three name. Columns:

| Field | Source | Notes |
|---|---|---|
| `run_id`, `event_id` | `Run.id`/`Run.event_id` (`src/brr/run.py`) | join key back to `.brr/runs/<id>/` |
| `started_at`, `ended_at`, `wall_clock_seconds` | daemon-tracked | already implicit in run lifecycle, not currently persisted past the run's own dir |
| `runner_shell`, `runner_core` | `task.meta["runner_name"]` / runner catalog | which quota bucket this run drew from — `quota=claude-local` / `codex-local` per the catalog |
| `repo_label`, `source_system`, `external_refs[]` | `task.meta["repo_label"]`, commit/PR/issue numbers touched | the connector field `design-dashboard-live-surface.md`'s commit/PR/issue ask needs — see below |
| `task_classification` | resident-assigned free-text/slug at dispatch or close-out (e.g. `dashboard-slice`, `kb-brainstorm`, `bugfix`) | the *only* field that makes rollup-by-shape possible; without it every row is a singleton no future estimate can match against |
| `tokens_input`, `tokens_output`, `tokens_cache_read`, `tokens_cache_creation` | Claude: `claude_status.py` result JSON (`modelUsage`); Codex: rollout `token_count` events | raw, per-provider, already computed once per run today and currently thrown away after the wake ends |
| `context_window_used` | `claude_status.py` (`modelUsage[model].contextWindow`) / Codex `model_context_window` | |
| `weekly_pct_delta` | before/after diff, see below | the one field with a clean $ mapping |
| `five_hour_pct_delta` | before/after diff | pacing-only, **no USD field derived from this** |
| `usd_subscription_attributed` | `(subscription_price_for_shell / 100) × weekly_pct_delta` | null when `weekly_pct_delta` is null (e.g. mid-window scrape failure) — never backfilled from the 5h number |
| `usd_credits_equivalent` | tokens × the existing `$0.01/credit` managed-compute rate (`decision-pricing-shape.md`) | this is *not* what the run actually cost (BYO runs cost the user's own subscription) — it's "what this would have cost as managed compute," the number the credit-pack pricing question needs |
| `estimate_vs_actual` | `actual` for a closed row; `forward_guess` for a queued-but-not-run item | so the same table (not a parallel one) can hold both a historical actual and a live estimate render can diff against |

**Before/after diff mechanic** (answers technical note 1 directly): the
daemon already reads a quota snapshot for B1/B2 pacing before dispatching
work. Add a forced-refresh read (`claude_usage.load_or_refresh_snapshot`,
already exists, currently only called opportunistically) at the moment
`_run_worker_and_finalize` calls `publish(repo_root, task)`
(`src/brr/daemon.py:4032`) — the existing close-out hook, not a new
lifecycle phase — and diff its weekly/5h percentages against whatever the
daemon read most recently before this run started. The delta *is*
`weekly_pct_delta`/`five_hour_pct_delta`. Codex needs no scrape at all —
`codex_status.load_levels()` already reads live, so before/after is two
cheap reads instead of a scrape twice.

**Storage, sequenced not to overbuild in one pass:** start local
(`.brr/run-ledger.jsonl`, append-only, written at the same close-out point
above) — cheap, no server round-trip, already how the daemon's own
per-run state doc (`_persist_run_state_doc`) works. Mirroring it to the
account backend is the exact same shape already shipped three times
(`ActivityRecord`, `Repo.plan_md`, `Daemon.quota_json`/`_publish_quota` in
`src/brr/gates/cloud.py:524`) — worth doing once the local shape is
proven, not in the first cut, so the schema doesn't get bent to fit a
migration before anyone has queried a single real row.

**What "make your life easier" actually buys**, concretely: today the
resident reads scattered per-run text (a Claude result JSON here, a Codex
rollout there) and guesses. With `task_classification` populated even
loosely, a resident mid-plan can `grep`/query `run-ledger.jsonl` for prior
rows sharing a classification and read off an actual `usd_credits_equivalent`
range instead of eyeballing token counts — the rollup-by-classification
query is future work (a small CLI/kb helper), but the schema has to carry
the join key *now* or that query is a migration later instead of a filter.

## Cohering with the dashboard's rendering vocabulary

This page and [`design-dashboard-live-surface.md`](design-dashboard-live-surface.md)
were written from two different entry points — "how do we let users pace
their own quota" here, "what does the live dashboard actually look like"
there — but they resolve to **one data model observed from two angles**,
not two designs that happen to be adjacent. Read side by side, three
places where a decision in one *is* a decision in the other:

- **The commit/PR/issue semi-hierarchy is this page's `source_system`/
  `external_refs` field, not a separate rendering-only concern.** The
  dashboard page's same-thread follow-up names it directly: "commits
  belong to PRs, PRs and Issues are referenced... make design extensible
  to support later ticket-systems integration (linear, jira, etc.)... but
  not implement it." That's a schema requirement on *this* page's
  `run_ledger` table (a run's `external_refs` need parent/child shape —
  a commit's PR, a PR's issue — plus a `source_system` connector field
  reserved now, GitHub-only wired), not an independent dashboard-only
  data shape. One connector field, two consumers: the cost rollup groups
  by it, the SpaceChem-molecule render walks it as a tree.
- **The Opus Magnum "solution report" *is* this table's per-run row,
  rendered.** `design-dashboard-live-surface.md`'s Zachtronics mapping
  names "token consumption → the Opus Magnum solution report, per-run" as
  the rendering answer without yet naming what gets written down for that
  report to hold real numbers. This page's `run_ledger` row is exactly
  that write-down — the report is a view over one row (or a `forward_guess`
  row, for a still-queued item), not a separate artifact to generate.
- **The window-track's time axis and this page's `five_hour_pct_delta`/
  `weekly_pct_delta` are the same numbers at two grains.** The window-track
  (already shipped, slice 2) shows the *whole window's* live percent; this
  table's per-run delta is what would let a future view show which run
  *consumed* how much of that draining bar — the difference between "the
  gauge is at 40%" and "this run cost 6 points of that 40%." Not scoped to
  build here; naming it so slice 3 (whatever renders the CPS/run flow
  atop the window-track) doesn't have to re-derive the join.

Net: whoever slices the tracking-table implementation and whoever slices
the commit/PR/issue render should read *this* section before either
starts, since building the connector field twice — once "for cost," once
"for the loom view" — is the redundancy both pages already separately warn
about.

### Addendum: early-supporter leaderboard, named not designed

Raised in the same message as a candidate shape for the credit-pack add-on
above (§Pricing tension): a $60+/$100+ one-time tier with an optional,
redactable-nickname leaderboard of contributed amounts. The maintainer's
own framing — "it could be that's the idea only sounds good but puffs
tho" — is the right level of commitment for now: captured because it's a
real, cheap-to-remember idea that belongs next to the credit-pack
discussion it modifies, not designed or scoped. If the credit-pack add-on
above (§Pricing tension) ever gets built, this is the natural place to
revisit whether a leaderboard earns its keep or is decoration on a feature
that works fine without it.

## Out-of-planning runs still land on the loom

A telegram message or (questioned, not committed) a dashboard text-input
box always becomes a run — that's already true today, structurally
(every inbound event dispatches to a run). The new part: that run should
be visible on the same loom surface as planned CPS items, not a separate
invisible lane. Concretely, this means the loom's rendering (whatever
slices `design-dashboard-live-surface.md`'s window-track/SpaceChem-
molecule mapping ships) needs a "spontaneous" or "unplanned" item state
alongside queued/running/done — not a different view, the same one with
one more origin tag. Small addition once the loom exists; named here so
it isn't discovered as a gap after the visual is already built around an
assumption that everything arrives pre-planned.

## Business model implication: subscriptions, credits, and eventually our own quota

The maintainer names a longer arc explicitly: "later, we could also
propose our own quotas as well as credits that we already agreed on."
Read against `decision-pricing-shape.md` (already accepted: platform
subscription + metered wallet credits, AI usage billed direct to the
provider, not resold) — this doesn't contradict that decision, it extends
its endgame. The current shape bills for *brnrd's own* compute and
platform ops, explicitly not for AI usage. The scheduling-loom vision
describes a future where brnrd's own understanding of *provider* quota
economics is good enough, and its own infra mature enough, that brnrd
could sell headroom directly (its own quota pool, bought at volume,
resold with margin, or a genuinely separate model-hosting arrangement) —
the "not self-sufficient yet" framing from the follow-up event names
this precisely: **the loom/scheduling mechanic is worth building now,
on top of BYO subscriptions, specifically because it's also the
infrastructure a future owned-quota product would need anyway.** Not a
near-term commitment; recorded so the near-term work (tracking table,
estimate rendering) is built with that shape in mind rather than as a
disposable stopgap.

### Pricing tension raised, not resolved: $60 one-time vs. $10/month

The follow-up event reopens pricing with a concrete question: "between
one-time 60$ payment and a 10$ monthly sub — what is likely yield more in
6 months?" Real math, not a vibe:

Model a steady monthly cohort of `U` new buyers/month for 6 months
(apples-to-apples acquisition rate; real conversion rates likely *do*
differ by price framing, which this model doesn't capture — noted as a
limitation, not solved here).

- **$60 one-time**: cohort `m`'s revenue lands entirely in month `m`.
  Cumulative by month 6: `60 × U × 6 = 360U`.
- **$10/month subscription**, monthly retention `r`: cohort `m`
  contributes `10 × Σ_{k=0}^{6-m} r^k` by month 6. Summed across 6 cohorts:
  - `r = 1.00` (no churn, upper bound): `210U`
  - `r = 0.95`: `~193U`
  - `r = 0.85` (plausible early-SaaS monthly retention): `~165U`

**At 6 months, one-time wins on raw cash collected under every retention
scenario modeled** — even zero churn tops out at `210U`, well under
`360U`, because most cohorts haven't had 6 months to accumulate yet. The
crossover only favors subscription **past** month 6-8, and only grows
from there as retained cohorts keep compounding while one-time revenue
per user is capped forever at $60.

What the model doesn't capture, and matters for a no-investor bootstrap
specifically:

- **Predictability.** MRR is plannable cash flow; one-time revenue depends
  on continuous new-customer acquisition with zero renewal safety net.
  For a project explicitly avoiding investor runway
  (`decision-hosted-execution-liability.md`'s framing, `plan-financial-
  growth.md`), smoothed recurring cash is worth more than the same total
  dollar amount arriving lumpy.
- **This isn't actually a live decision to make from scratch.**
  `decision-pricing-shape.md` already shipped and locked ($5/$7 monthly,
  PR #40, accepted 2026-05-26) — a real, reasoned pricing shape with its
  own rejected-alternatives table (credits-only, per-project add-on, etc.
  already tried and discarded). $10/mo sits close to the existing $7
  public price, not a different order of magnitude; $60 one-time doesn't
  correspond to any tier that decision defined, and the `purchased`
  credit bucket (non-expiring, pro-rata refundable) already gives a
  one-time-purchase-shaped option *inside* the current model without
  replacing the subscription.
- **Recommendation, not a unilateral change**: this is exactly the
  "genuine fork" shape (`run.md` → reconsider intent) — a real product/
  values call about which the code and existing decision don't resolve
  the question themselves, since the existing decision didn't consider a
  one-time SKU at all. Read the math above as leaning toward *keep the
  accepted subscription shape*, and treat "$60 one-time" as a candidate
  **add-on** (e.g., a bigger one-time credit-pack purchase, using the
  already-modeled `purchased` bucket) rather than a subscription
  replacement — not as a decided change to `decision-pricing-shape.md`.
  Flagged back to the maintainer rather than edited into the accepted
  decision unilaterally.

## Read next

- [`design-dashboard-live-surface.md`](design-dashboard-live-surface.md)
  §Zachtronics-mechanics deconstruction — the rendering vocabulary
  (window-track, SpaceChem molecules, Opus Magnum solution report) this
  page's economics has to render through.
- [`plan-director-execution.md`](plan-director-execution.md) §B6 — the
  quota-smoothing workstream this page's tracking-table requirement
  directly unblocks; scope B6 against this page's tracking list, not a
  narrower one.
- [`decision-pricing-shape.md`](decision-pricing-shape.md) — the accepted
  subscription+credits shape; this page's pricing question is a candidate
  addendum to that page, not yet applied there.
- [`decision-hosted-execution-liability.md`](decision-hosted-execution-liability.md)
  — the disclaimer posture this page's "estimates are guesses" stance
  extends.
