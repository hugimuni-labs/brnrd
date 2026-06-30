# Design: runner management — capacity-aware dispatch and proactive headroom

**Status: superseded on 2026-06-16 by the portals framing —
[`plan-resident-portals.md`](plan-resident-portals.md) §G1
(runner selection & quota-aware fallback) and now also by
[`design-runner-cores.md`](design-runner-cores.md) (the Shell/Core selection
layer that was the §G1 "no design home yet" gap).** The maintainer called
this page "a much poorer framing": it treats runner choice as a standalone
*capacity-management subsystem* (registry → tracker → dispatcher) bolted beside
the daemon, when the live wound is narrower and the right home is the
**resident's portals** — the wake's own control surface, where the runner and its
quota are one surface the resident reads and the daemon falls back / defers along.
The reframe is in §G1 of the portals plan and in `design-runner-cores.md`.

This page is kept as a **reference mine**, not a live plan: the
mechanics below — the capacity tracker, the reactive/proactive
`work_class` split, the backoff/fallback chain, the subscription-tier
headroom table, and the `brnrd_managed` consent gate — are the most
detailed treatment we have and are the raw material `design-runner-cores.md`
drew on.
Read it for the *how*; take the *framing* from the portals plan.

Companions: [`subject-managed-mode.md`](subject-managed-mode.md) (brnrd compute
and credential vault); [`plan-failover-compute.md`](plan-failover-compute.md)
(brnrd-owned spawns when daemon is offline); [`decision-pricing-shape.md`](decision-pricing-shape.md)
(credit wallet); [`design-co-maintainer.md`](design-co-maintainer.md)
(proactive work as the co-maintainer's self-scheduled wakes);
[`design-self-scheduled-thoughts.md`](design-self-scheduled-thoughts.md)
(the schedule machinery this gates on).

## The problem

Today brr detects whichever runner binary is first on PATH and dispatches every
event to it — reactive user messages and self-scheduled proactive thoughts alike,
with no awareness of subscription tier, rate limits, or remaining headroom.
Three failure modes:

1. **Proactive work steals reactive capacity.** A self-scheduled forge-grooming
   wake fires every 6h regardless of whether the user's Codex basic subscription
   has requests to spare. A surge of reactive events hits a throttled runner.
2. **Pro/Plus headroom is invisible.** A user with ChatGPT Pro has more room to
   run proactive wakes, but the daemon doesn't know — it treats Pro the same as
   basic.
3. **Multiple runners aren't managed independently.** A user running Claude and
   Codex doesn't get independent rate-limit tracking or sensible load-splitting.

The tempting fix is scattered conditionals: `if subscription == 'pro': schedule
more`. That's the soup. The clean path is a **capacity layer** that sits between
the scheduler and the runner subprocess — all runners, all work classes, all
subscription types talk through one routing function.

## The three-layer model

```
┌──────────────────────────────────────────────────────┐
│  Work classification  (at event / wake creation)     │
│  reactive | proactive                                 │
└─────────────────────┬────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────┐
│  Capacity model                                       │
│  runner registry + per-runner runtime state           │
│  subscription tier → rate limits → headroom           │
└─────────────────────┬────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────┐
│  Dispatch policy                                      │
│  route reactive to best-available runner;             │
│  defer proactive when headroom is below threshold     │
└──────────────────────────────────────────────────────┘
```

## Layer 1 — runner registry

Runners are declared in `.brr/config`. The current single-runner detection
(first binary found on PATH) remains the **unchanged fallback** when no `[[runner]]`
tables are declared — zero breaking change for existing users.

```toml
[[runner]]
id = "claude-primary"
binary = "claude"           # binary on PATH
profile = "claude"          # entry in runners.md
subscription_tier = "pro"   # basic | plus | pro | api_key | brnrd_managed
is_primary = true           # preferred for reactive work

[[runner]]
id = "codex-secondary"
binary = "codex"
profile = "codex"
subscription_tier = "basic"
is_primary = false

[runner.rate_limits]        # optional — inferred from tier when absent
requests_per_day = 100
requests_per_hour = 10
```

### Subscription tiers and their default headroom

| Tier | Proactive headroom | Reactive floor | Notes |
|------|--------------------|----------------|-------|
| `basic` | 0% | 100% | On-demand only; proactive work deferred unless explicitly configured |
| `plus` | 20% | 80% | Some proactive scheduling viable (e.g. daily grooming) |
| `pro` | 40% | 60% | Regular proactive scheduling (hourly monitoring, forge grooming) |
| `api_key` | configurable | configurable | Budget-based; explicit `rate_limits` expected |
| `brnrd_managed` | credit-budget based | credit-budget based | Consent gate applies; see §4 |

"Proactive headroom" is a behavioral label, not a fraction of requests. A runner
at 0% proactive headroom won't be dispatched for proactive work when reactive
work is queued or when the runner is near its daily limit. The exact decision is
in the dispatch policy (§3), not encoded per-runner.

The tier defaults above are first-pass estimates; real rate-limit data for each
provider should calibrate them. The `rate_limits` table overrides tier defaults
when present.

## Layer 2 — capacity tracker

Per-runner runtime state, stored in `.brr/runner-capacity.json` (gitignored):

```json
{
  "claude-primary": {
    "requests_today": 3,
    "requests_this_hour": 1,
    "last_request_at": "2026-06-15T23:00:00Z",
    "backoff_until": null,
    "consecutive_429s": 0
  },
  "codex-secondary": {
    "requests_today": 47,
    "requests_this_hour": 8,
    "last_request_at": "2026-06-15T22:58:00Z",
    "backoff_until": "2026-06-15T23:05:00Z",
    "consecutive_429s": 2
  }
}
```

State updates:
- `record_request(runner_id)` — increment counters, update `last_request_at`
- `record_rate_limit(runner_id)` — set `backoff_until = now + backoff(consecutive_429s)`,
  increment `consecutive_429s`. Backoff: 60s × 2^n, cap at 30 min.
- `record_success(runner_id)` — reset `consecutive_429s = 0`

The file is read-modify-written under a file lock (`.brr/runner-capacity.lock`);
multiple worktree tasks share it safely at this scale.

`has_proactive_headroom(runner_id)` returns true when all of:
- `backoff_until` is null or in the past
- `requests_this_hour < rate_limits.requests_per_hour × (1 - reactive_floor_fraction)`
- `requests_today < rate_limits.requests_per_day × (1 - reactive_floor_fraction)`
- tier is not `basic` OR no reactive events are queued (basic can do proactive
  only when the reactive queue is empty)

## Layer 3 — dispatch policy

The dispatcher is a single function:

```python
def choose_runner(
    work_class: str,           # "reactive" | "proactive"
    registry: list[RunnerProfile],
    state: CapacityState,
) -> RunnerChoice | Defer | Wait:
```

**For reactive events:**
1. Try runners in order: primary first, then by `is_primary=false` order.
2. First runner that is not in backoff and is under its reactive limit → `RunnerChoice`.
3. All runners in backoff or at limit → `Wait(seconds=min_backoff_remaining)`.
   Reactive events are **never dropped** — they wait.

**For proactive events:**
1. Collect runners that are not in backoff and have proactive headroom.
2. If none → `Defer()`. The scheduler reschedules for the next interval; the
   event is not surfaced to the runner at all.
3. If candidates → `RunnerChoice(runner=runner_with_most_headroom)`.

This is the anti-soup invariant: **all awareness of reactive vs. proactive lives
inside `choose_runner`**. A schedule entry just declares its `work_class`; it
never inspects subscription tier or headroom directly.

## Work classification

Events carry a `work_class` field at creation:

- `reactive` (default for gate events): user-triggered — Telegram messages, GitHub
  comments, issue assignments. Must respond.
- `proactive` (default for schedule entries): self-scheduled wakes — forge grooming,
  monitoring, dominion reconcile. Can wait.

In `schedule.md`, `work_class:` is an optional field (default: `proactive`):

```markdown
## forge-grooming
every: 6h
work_class: proactive

## daily-digest
at: 09:00
work_class: proactive
```

The `work_class` field on a schedule entry does not need to say anything about
which runner to use or what subscription tier is required. The dispatcher handles
all of that.

## Behavioral examples

**Single basic Codex subscription:**
Reactive requests always dispatch immediately. Self-scheduled wakes (`work_class:
proactive`) only dispatch when the daily/hourly counter is well below the limit
and no reactive events are queued. Forge grooming may skip several 6h windows
during a heavy coding day; it will fire overnight when headroom is high.

**ChatGPT Pro + Codex basic (two runners):**
Claude Pro is primary for reactive; Codex basic is secondary. Proactive wakes
prefer Codex (less headroom pressure on the primary). If Codex hits its daily
limit, Claude Pro's 40% proactive headroom covers remaining proactive work.
Independent rate-limit tracking: a Codex rate-limit hit doesn't block Claude.

**Single Claude Pro:**
40% proactive headroom by default. Forge grooming, monitoring, and ambient
initiative (a recurring `every: 30m` minimal wake) run routinely alongside
reactive work. The 60% reactive floor keeps a user message from ever waiting on a
concurrent proactive task.

## brnrd-managed runner and the LLM service fee

The `brnrd_managed` tier is the daemon-online complement of the failover compute
path (`plan-failover-compute.md`). It uses the same credit wallet but fires from
a live daemon, not from brnrd's failover dispatcher.

**Capacity = credit wallet.** `has_proactive_headroom` for a `brnrd_managed`
runner substitutes wallet balance for rate-limit counters:
- `proactive_headroom > 0` iff `wallet_balance > reactive_reserve_credits`
- `reactive_reserve_credits` is a configurable floor (default: 50 credits = $0.50)
  below which only reactive work is dispatched

**Cost estimation and consent gate.** Before dispatching any event to a
`brnrd_managed` runner:
1. Estimate token count from the task body (rough: characters ÷ 4; a
   sliding-window average of actual spend per task type can replace this over
   time).
2. Multiply by the model's per-token rate from a maintained table.
3. Compare estimated cost to wallet balance.
4. If `estimated_cost > wallet_balance - reactive_reserve`: gate with a consent
   prompt (same permission-prompt flow as `plan-failover-compute.md` § "Six
   approval modes"). User can approve, deny, or top up.
5. If approved: proceed with dispatch; debit wallet on task completion.

The daemon holds the single-flight slot open while waiting for consent (up to a
configurable timeout, default: 5 min). This is the "cost and estimate and
consent-waiting" topic from the pricing design, applied at the dispatch layer.

**Unified language.** The dispatcher does not need to know whether it's routing to
a BYO subscription runner or a brnrd-managed runner — it calls
`has_proactive_headroom()` and `under_limit()` in both cases. The difference is
only in *how* those two predicates are computed inside `CapacityState`:
- BYO runners: reads `runner-capacity.json` counters vs. declared rate limits
- `brnrd_managed`: reads wallet balance vs. configured reserve

Same dispatch algorithm, same work-class semantics, different capacity source.

## Implementation shape

**New files:**
- `src/brr/runner_registry.py` — `RunnerProfile` dataclass, config loading,
  tier → default-limits table, PATH-detection fallback
- `src/brr/runner_capacity.py` — `CapacityState`, `record_*`, `has_proactive_headroom`,
  file-lock-guarded read/write of `.brr/runner-capacity.json`
- `src/brr/runner_dispatch.py` — `choose_runner()`, `RunnerChoice`, `Defer`, `Wait` types

**Changed files:**
- `src/brr/runner.py` — feed 429 / timeout signals into `CapacityState` after
  each invocation; accept `runner_id` from dispatch instead of calling
  `resolve_runner` directly
- `src/brr/daemon.py` — load runner registry from config; call `choose_runner`
  before invoking runner; handle `Defer` (drop proactive wake with a log entry,
  not an error) and `Wait` (re-queue reactive event after backoff)
- `src/brr/schedule.py` — add `work_class` field to schedule entry parsing;
  pass it through to the event that the scheduler emits
- `src/brr/envs/__init__.py` — carry `runner_id` in `RunContext` for tracing

**Config schema additions:**
- `[[runner]]` table array (optional; current PATH detection is the fallback)
- `runner.reactive_reserve_credits` (for `brnrd_managed` tier, default: 50)

## Relationship to existing designs

- **`plan-failover-compute.md`** — covers daemon-offline brnrd spawns; this
  design is the daemon-online complement. They share the credit wallet and the
  consent-prompt flow; the trigger differs (brnrd failover dispatcher vs. the
  local daemon dispatch layer).
- **GitHub issue #117 (forge grooming)** — the proactive-headroom gate is what
  makes forge grooming safe to schedule. The grooming entry declares `work_class:
  proactive`; the dispatcher gates it. No per-subscription conditionals anywhere
  near the grooming code.
- **`design-co-maintainer.md`** — the co-maintainer's ambient initiative is just
  recurring schedule entries with `work_class: proactive`. Runner management is
  the infrastructure that makes "proactive but safe" possible without manual
  throttling.
- **`decision-pricing-shape.md`** — the `brnrd_managed` tier's consent gate is
  the UX surface of the credit model. The reactive-reserve concept maps to the
  included compute grant: subscribers' 300 credits/month are held partly for
  reactive work, with proactive allowed in the remainder.
- **`design-agent-ergonomics.md`** — runner capacity state (available runners,
  backoff remaining, headroom fraction, wallet balance) is a natural ergonomics
  signal. The `brr ergonomics` CLI and the brnrd dashboard runner-status widget
  should surface it.

## Open questions

1. **Tier default calibration.** The proposed headroom fractions (basic: 0%,
   plus: 20%, pro: 40%) are first-pass. Real provider rate limits for
   Codex-basic, Claude Pro, ChatGPT Plus should calibrate them.
2. **Cost estimation quality.** Character-count heuristics mis-estimate for
   heavy tool-use tasks. A per-runner sliding-window average of actual tokens
   used (parseable from runner stdout / stderr in some CLIs) would improve
   consent-gate accuracy.
3. **Notification when all runners are in backoff for a reactive event.** The
   current design returns `Wait` and retries; should there be a user-facing
   notification after a configurable delay (e.g., "your message is queued, all
   runners are rate-limited, retrying in 4 min")?
4. **Proactive ceiling.** Should a Pro user with zero reactive activity be
   allowed to use 100% of capacity proactively? Or is the reactive floor
   always maintained as a safety buffer? Current proposal: reactive floor is
   always maintained, even when the queue is empty.
5. **brnrd-managed runner on a live daemon vs. failover.** When the daemon is
   online and uses a `brnrd_managed` runner for normal dispatch (not failover),
   does the same brnrd spawn infrastructure apply, or is the AI credential used
   directly from the local daemon? This needs a brnrd-protocol decision before
   Phase 4 is started.

## Phasing

**Phase 1 — runner registry + capacity state** (no behavior change)
Parse `[[runner]]` from config; build `CapacityState`; instrument existing runner
invocations to call `record_request` / `record_rate_limit` / `record_success`.
Single-runner PATH detection is the fallback; nothing breaks.

**Phase 2 — work classification + proactive gating**
Add `work_class` to schedule entries. Implement `choose_runner` with the
`basic`-tier proactive block (proactive deferred when reactive queue is non-empty
or runner near daily limit). The 80/20 basic/plus reactive floor configurable.

**Phase 3 — multi-runner dispatch**
Full `choose_runner` for multiple registered runners. Independent capacity tracking.
Reactive fallback chain. Backoff handling across runners.

**Phase 4 — brnrd-managed tier + consent gate**
`brnrd_managed` tier capacity model (wallet balance as headroom), cost estimation,
consent-prompt integration. Requires `plan-failover-compute.md` infrastructure to
be at least partially shipped.

Phases 1–3 are self-hostable, pure daemon-side, and decoupled from brnrd. Phase 4
is the managed-mode integration slice.

## Lineage

- 2026-06-15: drafted from a conversation on runner subscription management
  holistically — triggered by #117 (forge grooming needing proactive headroom),
  the LLM service fee / consent-waiting topic, and the multi-runner rate-limit
  question. Initial design by the resident agent.
