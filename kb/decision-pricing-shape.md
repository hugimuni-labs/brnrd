# Decision: managed-mode pricing shape

**Status: proposed, not yet accepted on 2026-05-22; reshaped
2026-05-25 (BYO compute deferred, free-tier spawn cap revised
down, data-minimization trust signal promoted, framing tightened
to "we charge for ops, not for AI usage").** Sets the pricing
model for brnrd's managed-mode surfaces. Companion to
[`subject-managed-mode.md`](subject-managed-mode.md) (the
surfaces being priced) and
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) (the
per-task accounting hooks the model rides on).

## Decision

Two-tier shape at launch (plus a deferred third):

| Tier | What it includes | Cost model |
|------|------------------|-----------|
| **Free dispatcher** | Managed gates (TG bot, GH App, later Slack / Discord / GitLab), multi-project routing, permission-prompt API, audit log, dashboard read access, *and* failover compute on brnrd's managed pool within a generous monthly cap | Free, with rate caps (initial: 1000 gate events / month; **100 failover spawns / month** — explicitly framed as a fallback feature, not a free SaaS) |
| **Usage-based managed compute** | Managed-compute failover spawns *over* the free-tier cap | Pure pass-through + margin (target margin: 30-50% over wholesale cloud cost). No subscription fee — only kicks in when the free-tier cap is exceeded. |
| **Per-seat team tier (later, post-launch)** | Org-level features: audit log retention, SSO, priority support, longer event/response retention, higher rate caps, multi-user dashboard | $X / seat / month; ships post-launch when individual usage proves out |

The free dispatcher is the entry point AND covers the common case
of fallback continuity (100 spawns/month covers a hobby user
through every laptop-asleep moment for the month). Paid usage
only kicks in when the user routinely needs more failover than
the cap, at which point the value is concretely demonstrated.
Self-hosted brnrd gets everything free by construction.

## What we charge for, what we don't

A clean line drawn explicitly so it shows up on the landing page:

- We **charge for ops we run on your behalf** — compute spawns on
  our cloud account, over the free-tier cap. That's it.
- We **don't charge for AI usage** — your Anthropic / OpenAI /
  Google bill is yours, paid directly to them. We just relay the
  credential to the sandbox at spawn time.
- We **don't charge for the dispatcher / gates / routing /
  prompts / audit / dashboard** — these are approximately zero
  marginal cost; the free tier covers them.
- We **don't subscribe-gate features** — every feature works on
  the free tier; paid is purely about scale of managed compute.

This line matters: the framing reads as "ops as a service" not
"SaaS layered on top of an OSS thing," which matches the actual
architecture and avoids "rent-seeking on free software" vibes.

## BYO compute — designed, deferred (not in launch pricing)

Earlier draft included BYO failover compute (user stores their
own Fly / Modal / Daytona / etc. token on brnrd; brnrd spawns
into the user's cloud account) as a free-tier feature. Dropped
from launch on 2026-05-25 because:

- ~30% more backend surface area (per-platform credential
  storage UI, scope validation, per-platform onboarding docs,
  per-platform failure modes, dispatcher branching on platform
  selection) — disproportionate to launch user value.
- ~5% of launch users care; the other 95% would rather paste an
  AI credential and let brnrd handle the spawn.
- Per-platform maintenance burden is unbounded.

Pricing implication is small: the free-tier dispatcher covers the
"BYO operator" persona already (they pay their own AI bill, they
pay their own cloud bill if they use a daemon-side cloud env
plugin to fan out, brnrd charges them nothing). When BYO comes
back post-launch, it lands cleanly in the free tier (we don't
charge for routing events you spawn against your own cloud
account).

Daemon-side cloud-runner adapters (a laptop daemon fans out to
the user's cloud via a `brr-env-*` plugin) remain independent of
managed mode entirely — brnrd isn't in that path, nothing to
price.

## Why this shape

Four constraints shaped the decision:

1. **Non-VC-backed.** No "burn now, monetise later" runway. Every
   tier has to be either at-or-near zero marginal cost to operate,
   or revenue-positive per unit of usage. No subsidised growth.
2. **Everything is OSS self-hostable.** A user who doesn't like
   the pricing can fork brnrd and run their own. The pricing
   has to be honest enough that most users prefer hosted *not*
   because they can't self-host, but because operating it isn't
   worth their time. Pricing that looks like rent-seeking
   undermines this.
3. **Adopter goodwill.** Launching with "all free, paid later"
   creates bait-and-switch perception when the paid tier appears.
   The paid tier ships at launch with a clear free / paid split
   the user can reason about up front. *And* the free tier is
   generous enough that hobby users genuinely never hit it.
4. **Data minimization as trust signal.** "We don't store your
   code" makes hosted brnrd defensible vs self-hosted on
   security grounds (we hold strictly less than you'd think we
   do). This belongs on the pricing/landing page as much as on
   the design page.

The two-tier shape above satisfies all four by mapping each tier
to its marginal cost:

- **Dispatcher costs are mostly fixed.** A webhook receiver + a
  postgres + a long-poll endpoint costs cents per user per month
  at moderate scale. Charging for it is rent-seeking; making it
  free is honest, and the rate caps bound the loss-leader
  exposure.
- **Failover compute costs are variable and significant.** Per-
  second cloud billing for spawned sandboxes is the real cost.
  Free tier covers the fallback-feature use case (100 spawns/mo
  ≈ ~$0.28/user/month max cloud cost at our rate). Over the cap
  is usage-based with margin — revenue-positive by construction.
- **AI compute belongs to the user.** Anthropic / OpenAI / Google
  bill the user directly; brnrd doesn't intermediate that
  relationship. Removes a class of "are you reselling the
  models?" confusion and avoids the operational hell of being a
  reseller.
- **Team-tier features cost human attention.** That's worth
  charging real money for; teams expect to pay for it; it doesn't
  apply to individual users.

The hosted-vs-self-host pitch reads cleanly: *"we run the ops so
you don't, and we hold less of your data than you'd expect"* —
not *"we charge for the privilege."* Users who want to operate
brnrd themselves can; users who'd rather not pay modest usage
rates for the parts that cost us real money.

## Free-tier cap math

At 100 spawns / month, 15-minute average task, brnrd-side cloud
cost on Fly Machines `shared-cpu-1x@2GB`:

- ~$0.000045/sec * 900s * 100 = **~$4.05/user/month worst case**
  (if every spawn ran the full 15 min on the larger machine; in
  practice average will be lower).
- At our published rate (30% margin), break-even at ~77 spawns/
  month per user we'd otherwise have to charge for.
- 1000 free-tier users at full cap = ~$4050/month. Sustainable
  with a small percentage of paying users on top.

Real expected usage is much lower — the 100/mo cap is
intentionally a *fallback feature*, not a free continuous-execution
SaaS. Users who routinely need >100 spawns/mo have a credible
managed-compute use case to pay for; users who hit 10/mo and stop
got real value at zero cost.

## Alternatives considered

### Alt 1 — Subscription for managed gates

Earlier framing was "$X / month for the managed bots." Rejected
because:

- Gates are approximately zero marginal cost per user. A flat
  subscription for something with no variable cost looks like
  rent-seeking, especially next to "all OSS, self-host if you
  want."
- Subscriptions create entry-point friction ("do I want to
  subscribe to test this?") — bad for top-of-funnel adoption.
- Doesn't address the actual operational-cost driver (compute).

The team tier captures the "I pay for brr" brand-value audience
later, after individual usage validates demand.

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
  for a variable-cost product.

### Alt 5 — BYO at launch (kept in pricing)

The shape considered through 2026-05-24 included BYO failover
compute on the free tier. Dropped 2026-05-25 because the
implementation-cost vs launch-user-value didn't justify shipping
it day one. Wire shape preserved in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) for
clean add-back; pricing for BYO when it comes back: free tier
(same as today's deferred plan — we don't intermediate the user's
own cloud bill).

### Alt 6 — Reseller of AI compute (Anthropic credits resold)

Brief consideration: brnrd as middleman buying bulk AI usage
and reselling at margin. Rejected because:

- Real reseller economics need scale we don't have.
- Adds reseller-of-record complexity (PCI, tax, support for
  provider bills the user can't see).
- Breaks the "we don't have your prompts" trust story.
- Provider TOS often disallow it.

Brr.run intermediates AI credentials (encrypted vault, used at
spawn time, never logged). It does *not* intermediate AI billing.

## Open questions to settle before launch

- **Exact rate caps for the free tier.** 1000 gate events / month
  and 100 failover spawns / month are initial guesses. First
  month of real usage data should set the empirical floor — the
  cap should be generous enough that hobby users never hit it,
  tight enough that genuine production users find the
  usage-based tier (or a future team tier) compelling.
- **Margin on usage-based managed compute.** Target band 30-50%.
  Final number depends on Fly Machines wholesale cost variance
  and operational overhead (probably small). Settle pre-launch
  with a published per-second rate.
- **Team tier shape.** Per-seat seems right for the team use case
  (Linear-shaped, not Plausible-shaped). Number TBD. Defer until
  early teams ask.
- **Volume discounts on managed compute.** Probably not for v1 —
  pass-through pricing self-volume-discounts (cloud platforms
  bill less per minute at scale; we pass that through). Revisit
  if a power-user emerges who'd benefit from a custom rate.
- **Permission-prompt friction vs auto-approve defaults.** Free-
  tier cap is generous enough that most users could safely
  auto-approve; default mode (`ask`) is the conservative choice.
  Revisit if churn data shows users disabling failover because of
  prompt fatigue.
- **Self-hosted brnrd.** When someone runs their own brnrd,
  they get all tiers for free by construction (they're paying
  their own infra). The brand and pricing of *hosted* brnrd
  should not depend on suppressing self-hosting — quite the
  opposite. The team tier is the only place where hosted has
  real differentiation (SLA, support); free tier and managed
  compute are both honestly worth what we charge for them.

## Trust signals that ship with the pricing page

- "We don't have your code" — per
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
  "Data minimization". Event content dropped after dispatch;
  responses pass through; conversation contents rendered live
  from platform APIs and git remotes (not mirrored to brnrd);
  AI credentials encrypted at rest with per-account envelope
  keys.
- "What we DO hold, named and bounded" — full table in the
  design page's "What we DO hold" subsection: account email +
  password hash; AI credentials (encrypted, until revoke);
  project bindings; event metadata graph (30-day TTL, no body /
  no preview / no participant names — the cross-gate table of
  contents that powers failover continuity); Telegram per-chat
  ring buffer (50 msgs × 72h, the one named concession because
  TG's Bot API lacks retroactive history); audit log (metadata,
  90 days); spawn outcomes (12 months for billing). Every read
  of these surfaces hits the audit log.
- "Self-hostable end-to-end" — every server-side piece is OSS in
  the monorepo (per
  [`decision-monorepo-structure.md`](decision-monorepo-structure.md));
  hosted is convenience, not lock-in.
- "We charge for ops, not for AI usage" — per the "what we
  charge for" section above.
- "Per-account audit log" — every credential read, context
  fetch (cross-gate or TG-ring-buffer read), spawn, prompt
  resolution surfaced to the user; surprises are bugs, not
  features.

## Lineage

- 2026-05-22 — drafted as part of the work-continuity reframe of
  managed mode. Three-tier shape (free dispatcher + BYO failover
  + paid managed compute + later team tier). Pondering
  provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
- 2026-05-25 — reshaped: BYO compute deferred from launch;
  collapsed to two-tier (free dispatcher inc. 100 managed-
  compute spawns/month, plus usage-based over the cap, plus a
  deferred per-seat team tier). Free-tier spawn cap revised down
  from 200 → 100. Data-minimization trust signal promoted to a
  load-bearing pricing surface. "We charge for ops, not for AI
  usage" framing added explicitly. Self-hosted brnrd framed
  more positively as a parallel path. Third reframe breadcrumb
  in [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
