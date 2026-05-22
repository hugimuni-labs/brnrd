# Decision: managed-mode pricing shape

**Status: proposed, not yet accepted on 2026-05-22.** Sets the
pricing model for brr.run's managed-mode surfaces. Companion to
[`subject-managed-mode.md`](subject-managed-mode.md) (the three
surfaces being priced) and
[`design-brr-run-protocol.md`](design-brr-run-protocol.md) (the
per-task accounting hooks the model rides on).

## Decision

Three-tier shape that aligns pricing with marginal cost:

| Tier | What it includes | Cost model |
|------|------------------|-----------|
| **Free dispatcher** | Surface A (managed gates) — TG bot, GH App, later Slack / Discord / GitLab; Surface B (BYO failover compute) — brr.run spawns sandboxes in the user's cloud account when the daemon is offline | Free, with sensible rate caps (initial: 1000 gate events / month; 200 failover spawns / month — revisit when empirics arrive) |
| **Usage-based managed compute** | Surface C (managed compute) — brr.run spawns sandboxes in *its* cloud account; user pays per-task | Pure pass-through + margin (target margin: 30-50% over wholesale cloud cost). No subscription fee. |
| **Team / SLA tier (later)** | Org-level features: audit log retention, SSO, priority support, longer event/response retention, higher rate caps; ships post-launch when individual usage proves out | $X / seat / month OR $X / month flat — decide closer to ship based on what early teams ask for |

The free dispatcher is the entry point. The usage-based tier is
the variable-cost product that funds operations. The team tier is
sticky revenue that supports the path to enterprise without
requiring per-user margin on the free tier.

## Why this shape

Three constraints shaped the decision:

1. **Non-VC-backed.** No "burn now, monetise later" runway. Every
   tier has to be either at-or-near zero marginal cost to operate,
   or revenue-positive per unit of usage. No subsidised growth.
2. **Everything is OSS self-hostable.** A user who doesn't like
   the pricing can fork brr.run and run their own. The pricing
   has to be honest enough that most users prefer hosted *not*
   because they can't self-host, but because operating it isn't
   worth their time. Pricing that looks like rent-seeking
   undermines this.
3. **Adopter goodwill.** Launching with "all free, paid later"
   creates bait-and-switch perception when the paid tier appears.
   The paid tier ships at launch with a clear free / paid split
   the user can reason about up front.

The shape above satisfies all three by mapping each tier to its
marginal cost:

- **Dispatcher costs are mostly fixed.** A webhook receiver + a
  postgres + a long-poll endpoint costs cents per user per month
  at moderate scale. Charging for it is rent-seeking; making it
  free is honest, and the rate caps bound the loss-leader
  exposure.
- **Compute costs are variable and significant.** Per-second
  cloud billing for spawned sandboxes is the real cost of running
  Surface C. Usage-based with margin makes this revenue-positive
  per task — never under water by construction.
- **SLA and team features cost human attention.** That's worth
  charging real money for; teams expect to pay for it; it doesn't
  apply to individual users.

The hosted-vs-self-host pitch reads cleanly: *"we run the ops so
you don't"* — not *"we charge for the privilege"*. Users who want
to operate brr.run themselves can; users who'd rather not pay
modest usage rates for the parts that cost us real money.

## Alternatives considered

### Alt 1 — Subscription for managed gates

Earlier framing was "$X / month for the managed bots." Rejected
because:

- Gates are approximately zero marginal cost to operate per user
  (webhook receiver + postgres). A flat subscription for something
  with no variable cost looks like rent-seeking, especially next
  to "all OSS, self-host if you want."
- Subscriptions create entry-point friction ("do I want to
  subscribe to test this?") — bad for top-of-funnel adoption.
- Doesn't address the actual operational-cost driver (compute).

Reasonable people could still argue the brand value of "I pay for
brr" matters more than the friction. The team tier captures that
audience later, after individual usage validates demand.

### Alt 2 — Pure pass-through with margin, no free tier

Everything billed by usage, no free tier. Rejected because:

- Kills the top-of-funnel for OSS users who want to try managed
  gates without entering a credit card. Adoption drops sharply.
- Even with usage-based pricing, the per-user cost of running
  gates is small enough that a free tier with rate caps doesn't
  meaningfully hurt unit economics, and helps growth a lot.

### Alt 3 — VC-style "free forever, paid enterprise later"

Free everything for individuals; only enterprise pays. Rejected
because:

- Requires runway we don't have. Without growth-funding, the
  operational cost of free-everything outgrows revenue.
- Distorts product priorities toward enterprise features early,
  away from what makes individual users love brr.
- Has been done to death; not a differentiator.

### Alt 4 — Subscription for compute, not usage-based

Flat $X / month gets you Y minutes of managed compute. Rejected
because:

- Cloud pricing is per-second; mismatched pricing units mean
  either we eat overage (under water risk) or the user always
  feels they're paying for unused capacity (churn risk).
- Pass-through with margin is the simplest, most defensible model
  for a variable-cost product. No need to invent a new pricing
  unit.

## Open questions to settle before launch

- **Exact rate caps for the free tier.** 1000 gate events / month
  and 200 failover spawns / month are initial guesses. First
  month of real usage data should set the empirical floor — the
  cap should be generous enough that hobby users never hit it,
  tight enough that genuine production-team users find the team
  tier compelling.
- **Margin on usage-based managed compute.** Target band 30-50%.
  Final number depends on per-platform wholesale cost variance
  and the operational overhead of running the spawn machinery
  (probably small). Settle pre-launch with a published per-second
  rate per platform.
- **Whether to bill BYO-failover for dispatch on top of the free
  tier.** Current decision: no — BYO means BYO, and the
  dispatcher is the same code path that's free for managed
  gates. Revisit only if BYO-failover ends up being the dominant
  source of dispatch load.
- **Team tier shape.** Per-seat vs flat? Both have precedent
  (Linear is per-seat, Plausible is flat). Defer until early
  teams ask.
- **Volume discounts on managed compute.** Probably not for v1 —
  pass-through pricing self-volume-discounts (cloud platforms
  bill less per minute at scale; we pass that through). Revisit
  if a power-user emerges who'd benefit from a custom rate.
- **Self-hosted brr.run.** When someone runs their own brr.run,
  they get all three tiers for free by construction (they're
  paying their own infra). The brand and pricing of *hosted*
  brr.run should not depend on suppressing self-hosting — quite
  the opposite. The team tier is the only place where hosted has
  real differentiation (SLA, support); free tier and managed
  compute are both honestly worth what we charge for them.

## Lineage

- 2026-05-22 — drafted as part of the work-continuity reframe of
  managed mode. Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  (monetisation timing) and the 2026-05-22 reframe breadcrumb.
  Status: proposed, awaiting acceptance before
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
  and [`plan-failover-compute.md`](plan-failover-compute.md) can
  proceed past their backend-prototype gate.
