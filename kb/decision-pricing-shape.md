# Decision: managed-mode pricing shape

**Status: accepted 2026-05-26** (locked in PR #40 MR review).

> **Partial supersession (accepted 2026-06-15):** the clause below that
> brnrd does *not* charge for AI usage is superseded by
> [`decision-llm-relay.md`](decision-llm-relay.md)
> (the LLM relay pricing decision). That page establishes: LLM traffic is
> relayed at provider cost plus a transparent relay service fee (10–15%);
> managed compute carries a small ops margin; BYO stays free and is the
> default; the relay activates only when user credentials are unavailable.
> The subscription tiers, wallet mechanics, and `$0.01/credit` compute
> rate on this page are unaffected, though the billing UI should
> distinguish "LLM relay provider cost", "relay service fee", and
> "managed compute ops" as separate line items.

Hosted brnrd launches with a small platform subscription plus metered
compute credits. Free stays genuinely usable for lightweight hosted
dispatch. Subscribed accounts buy the operational surface that costs
real money to run: higher project and event headroom, the full dashboard,
longer audit retention, email support, included managed compute, and
subscriber-only BYO compute.

Companions:
[`subject-managed-mode.md`](subject-managed-mode.md) describes the hosted
surfaces being priced;
[`design-billing.md`](design-billing.md) implements the subscription,
wallet, Stripe, and ledger mechanics;
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) carries the
protocol hooks;
[`decision-licensing-and-defense.md`](decision-licensing-and-defense.md)
records the license / pricing defense strategy; and
[`plan-financial-growth.md`](plan-financial-growth.md) sets this pricing
shape inside the wider no-investor, duo-run growth plan.

Billing is Stripe-integrated from day one. Subscription checkout uses
Stripe recurring subscriptions and the Customer Portal; wallet top-ups use
one-shot Stripe Checkout sessions. There is no manual-invoicing fallback
at launch, so edits here and in `design-billing.md` should move together.

## Decision

| Tier | Price | Projects | Events / month | Managed compute | Dashboard / audit / support |
|------|-------|----------|----------------|-----------------|-----------------------------|
| **Free** | $0 | 3 | 100, then soft-throttle | 10-credit one-time signup bonus, 30-day expiry; no recurring grant | Basic read-only gauges, 7-day audit, community support |
| **Subscribed, supporter cohort** | $5/month or $50/year | 25; unlimited after $10 cumulative top-ups | 10,000, then soft-throttle | 300 credits/month included; overage via wallet | Full dashboard, 90-day audit, email support |
| **Subscribed, public cohort** | $7/month or $70/year | same as supporter | same | same | same |
| **Compute overage** | $0.01/credit | n/a | n/a | One-shot wallet top-ups; opt-in auto-topup only | n/a |
| **Self-hosted brnrd** | $0 to brnrd | unlimited by deployment | unlimited by deployment | user/operator pays their own cloud | full self-hosted feature surface |

The paid tier deliberately has no marketing name. UI, CLI, docs, and API
state say `Subscribed`, `Subscriber`, or `subscription`; there is no
launch-time "Plus", "Pro", or "Premium" tier to unwind later.

The supporter cohort closes when either 200 subscribers have started a
subscription or 12 months have passed from public launch, whichever comes
first. Existing supporters stay on their original $5/$50 Stripe `Price`
forever; new subscriptions after the cutoff use the $7/$70 public price.

Team / per-seat billing is deferred to v-next. The likely shape is a
Linear-style per-seat add-on over the account subscription, but it ships
only after real team demand appears.

## What the subscription unlocks

Subscribing is not just "Free with bigger numbers." It buys a bundle of
features that have implementation, support, or operational cost:

- **Project headroom**: Free covers the common "side project + day-job +
  scratchpad" case at 3 projects. Subscribed starts at 25, then unlocks
  unlimited projects after $10 of cumulative compute top-ups.
- **Full dashboard**: cost charts, cross-project views, project-binding
  management, allowance gauges, and permission-prompt configuration.
- **Included managed compute**: 300 credits/month, framed as bundled
  compute on the house. The subscription remains the platform fee; unused
  included credits are not refundable or banked.
- **Subscriber-only BYO compute**: subscribers can bring a cloud-platform
  credential for any cloud env brnrd also offers as managed.
- **Operational headroom**: 10K events/month, 90-day audit retention, and
  email support.

This keeps the value proposition concrete: the user pays for hosted
continuity and operational convenience, not for access to the OSS daemon
or for AI usage.

## What we charge for, what we don't

brnrd charges a small monthly subscription for fixed platform cost:
always-on bots, the dispatcher, multi-project routing, dashboard, audit
log, permission-prompt machinery, and support.

brnrd charges metered cents for variable hosted compute: failover spawns
that run in brnrd's cloud account, beyond any included grant.

brnrd does **not** charge for AI usage. Anthropic / OpenAI / Google bills
belong directly to the user; brnrd only relays the encrypted credential
into a spawn when policy allows.

brnrd does **not** subscription-gate the OSS. Every brr/brnrd feature can
run against a self-hosted brnrd. The hosted subscription is for service
convenience, not source-code lock-in.

## Compute: managed vs BYO (subscriber choice)

| Concern | Managed compute | BYO compute |
|---------|-----------------|-------------|
| Eligible accounts | Free + Subscribed | Subscribed only |
| Spawn location | brnrd's cloud account | subscriber's cloud account |
| Cloud credentials | brnrd/operator-owned | subscriber stores `cloud-platform` credential in the vault |
| Who pays the cloud | brnrd, recovered through credits | subscriber pays provider directly |
| Wallet use | debits at spawn finalize | bypasses wallet entirely |
| Launch support | Fly Machines | BYO Fly Machines |

One rule covers launch and future clouds: if brnrd ships managed support
for a cloud env, subscribers can BYO that env in the same release. There
is no BYO-only cloud and no Free + BYO path.

Free stays managed-only for three reasons: Free should be setup-light;
BYO is structurally a cost-saving feature and subscribing is the
cost-saving move; and the vault / dispatcher path stays clean when
`subscription.tier == "subscribed"` is the only BYO gate.

The 300-credit subscriber grant remains granted even when a subscriber
uses BYO. It can lapse unused, cover another managed env, or sit unused
for that billing cycle; it is bundled compute, not a reimbursement.

The same BYO-for-subscribers principle applies to future hosted
connectors from
[`decision-connectors-layering.md`](decision-connectors-layering.md):
subscribers can bring their own OAuth/app credentials for connector
families brnrd also offers as managed.

## Event-cap overage

Event caps are soft throttles, not hard walls and not a metered billing
surface. Events should still reach the user eventually.

- **Free**: after 100 events/month, dispatch slows to roughly 1 event/hour.
  The throttle clears at the next monthly boundary.
- **Subscribed**: after 10,000 events/month, dispatch slows to roughly
  1 event/second and the user is asked to email support for a limit raise.

Every throttled gate reply includes a short footer explaining the
slowdown and how to lift it. The footer is a resolution to a visible
state, not generic payment bait.

## Free compute grant — one-time signup bonus, not recurring

Free receives a 10-credit signup bonus on account creation. It expires 30
days after creation or on full consumption, whichever comes first. Free
has no recurring monthly compute grant.

The signup-bonus shape bounds cost by signup count instead of retained
Free accounts: 100,000 Free signups cost at most $10,000 of managed
compute over the life of those signups, not $60,000/year of recurring
allowance. It also keeps the pitch simple: the managed dispatcher is free;
the bonus is just enough to test failover compute.

After the bonus, Free users can top up at $0.01/credit, subscribe for the
recurring 300-credit subscriber grant, self-host, or simply keep using
hosted dispatch without failover compute.

## Subscriber project cap — 25 default, unlimited after $10 of cumulative top-ups

Subscribed accounts start with 25 projects. The account unlocks unlimited
projects permanently after $10 of cumulative wallet top-ups.

Implementation contract:

- `cumulative_purchased_usd_lifetime` increases on every successful
  Stripe top-up and never decreases on refund.
- `project_cap_unlocked` flips once the cumulative total reaches $10 and
  stays true for the account.
- Effective project cap is `3` for Free, `25` for Subscribed before the
  unlock, and unlimited for Subscribed after the unlock.
- If an unlocked subscriber cancels, the flag persists but only matters
  while subscribed; Free still caps at 3 projects.

The unlock is a trust signal for sustained usage, not a new subscription
tier.

## Multi-account abuse mitigation: binding uniqueness, not fingerprinting

At launch, the useful abuse control is resource-binding uniqueness:

- a GitHub repo can be bound to only one brnrd account at a time;
- a Telegram chat can be bound to only one account/project pair at a
  time;
- Slack, Discord, and future chat bindings follow the same rule.

This is required for routing correctness anyway. It also removes most
leverage from duplicate Free accounts: unbound projects have no managed
gate routing value, and duplicate signup bonuses are too small to justify
fingerprinting, ML scoring, or IP-velocity systems at launch.

## Dashboard nudges + transparency

The dashboard surfaces usage against allowances as first-class state:
events this month, credit balance by bucket, project count, cumulative
top-ups toward the project unlock, and recent managed-compute spend.

Nudges are inline, dismissible, and tied to visible events:

| Trigger | User-facing action |
|---------|--------------------|
| Free nears or hits 100 events/month | explain soft throttle and link to subscribe/self-host/wait for reset |
| Free consumes or expires the signup bonus | link to top up or subscribe |
| Free tries to create a 4th project | explain Free's 3-project cap and subscription headroom |
| Subscriber nears credit-grant exhaustion | link to top up |
| Subscriber hits 25 projects before unlock | show dollars remaining to the $10 unlock |
| Subscriber nears 10K events/month | link to email support for a raised cap |

Anti-patterns avoided: blocking modals, cancellation friction, unequal
"no thanks" buttons, countdown timers, hidden hard caps, and nudge spam.

## Sustainability math

The shape matches each billing stream to its cost:

- fixed platform cost is covered by the subscription;
- variable hosted compute cost is covered by wallet debits after any grant;
- AI spend stays outside brnrd's revenue and liability model.

Back-of-envelope at launch assumptions: $5 supporter cohort, $7 public
cohort after the first 200, 300 included credits/month, and roughly 30%
of subscribers exceeding the included compute.

| Scenario | Subscription MRR | Compute revenue over grant | Compute + infra cost | Net direction |
|----------|------------------|----------------------------|----------------------|---------------|
| 50 supporters | ~$250 | ~$50 | ~$270 | near breakeven |
| 200 supporters | ~$1,000 | ~$200 | ~$800 | positive |
| 500 subscribers | ~$3,100 | ~$500 | ~$1,750 | comfortably positive |
| 1,000 subscribers | ~$6,600 | ~$1,000 | ~$3,300 | funds real maintainer time |

The earlier credits-only model needed far more users because compute
margin alone could not cover the platform's fixed cost.

## Credit buckets and expiry (per-source policy)

Credits live in a bucketed ledger. `design-billing.md` owns the schema,
debit loop, Stripe webhook handling, and accounting details; this page
owns the pricing policy.

| Bucket | Granted on | Expires | Rolls over | Refundable |
|--------|------------|---------|------------|------------|
| `free_signup_bonus` | Free account creation | 30 days or full consumption | no | no |
| `subscriber_monthly` | subscription start and renewal | end of billing cycle | no | no |
| `purchased` | Stripe top-up | never, account-dormancy-bounded | yes | pro-rata within 30 days |
| `promotional` | future campaigns / support grants | per grant | no | no |

Debit priority preserves paid balance: `free_signup_bonus`,
`subscriber_monthly`, soonest-expiring `promotional`, then oldest
`purchased`.

Dashboard language should frame grants as allowances that refresh or reset,
not as "credits expired." Purchased credits are shown as prepaid balance.

## Early-adopter price step ($5 supporter → $7 public)

Supporter and public prices are separate Stripe `Price` IDs on the same
subscription product:

- **Supporter**: $5/month or $50/year for the first 200 subscribers or
  first 12 months from public launch, whichever closes first.
- **Public**: $7/month or $70/year for new subscribers after the cutoff.

The feature set is identical. The step is a launch-cohort loyalty
mechanism and a small long-tail revenue lift, not a tier split.

## Subscription mechanics

Implementation belongs in
[`design-billing.md`](design-billing.md). The policy contract:

- Stripe recurring subscription; monthly and annual cadence.
- Annual price is fixed at $50 supporter / $70 public, about 17% off.
- Cancel-at-period-end by default; no immediate feature removal.
- Free-to-Subscribed upgrade grants prorated subscriber credits for the
  remaining billing period and raises caps immediately.
- Failed renewal enters Stripe dunning; final failure returns the account
  to Free.
- Subscription state is account-scoped (`free`,
  `subscribed_past_due`, `subscribed`) so daemon and brnrd-side reads use
  the same tier source.

## BYO compute — subscriber feature, parallel-shipped with managed

BYO compute was originally a separate deferred launch surface. The
accepted shape is narrower and cleaner: BYO is a subscriber sub-option of
Surface B and ships one-for-one with managed support per cloud.

At launch this means managed Fly Machines plus subscriber-only BYO Fly
Machines. Future managed Modal / Daytona / Codespaces support should add
BYO for that provider in the same release.

Daemon-side cloud envs are separate: a local daemon invoking the user's
cloud directly through `brr[fly]` or another env extra is not managed
mode and is not priced here.

## Rejected shapes

| Alternative | Why rejected |
|-------------|--------------|
| Credits-only | Active users could stay free because dispatcher usage did not reliably burn compute; fixed platform cost remained uncovered. |
| Subscription with no included compute | Felt abstract and stingy; included credits make the sub feel useful while remaining bounded. |
| Subscription with unlimited compute | Fixed price for unbounded variable cost can go underwater. |
| Per-project add-on pricing | Reads coin-operated; the account subscription is a simpler mental model. |
| Hard 1-project Free cap | Looked like a trial, not a generous OSS-adjacent Free tier. |
| Resell AI usage | Breaks trust story, adds reseller complexity, and is not viable at launch scale. |

## Launch-tunable knobs

Launch defaults are accepted policy, but each number should be wired to a
`BRNRD_*` env knob so ops can retune without a code release.

| Knob | Default |
|------|---------|
| `BRNRD_FREE_SIGNUP_BONUS_CREDITS` | 10 |
| `BRNRD_FREE_SIGNUP_BONUS_EXPIRY_DAYS` | 30 |
| `BRNRD_FREE_PROJECT_CAP` | 3 |
| `BRNRD_SUBSCRIBER_PROJECT_CAP` | 25 |
| `BRNRD_PROJECT_CAP_UNLOCK_USD` | 10 |
| `BRNRD_SUBSCRIBER_MONTHLY_CREDITS` | 300 |
| `BRNRD_SUPPORTER_COHORT_SIZE` | 200 |
| `BRNRD_SUBSCRIBER_PRICE_SUPPORTER_USD` | 5 |
| `BRNRD_SUBSCRIBER_PRICE_PUBLIC_USD` | 7 |
| `BRNRD_DORMANCY_PAUSE_MONTHS` / `BRNRD_DORMANCY_PROMPT_MONTHS` | 24 / 36 |
| `BRNRD_FREE_EVENT_CAP_MONTHLY` | 100 |
| `BRNRD_FREE_SOFT_THROTTLE_RATE_PER_HOUR` | 1 |
| `BRNRD_SUBSCRIBER_EVENT_CAP_MONTHLY` | 10000 |
| `BRNRD_SUBSCRIBER_SOFT_THROTTLE_RATE_PER_SEC` | 1 |

Post-launch tuning should watch median/p95 subscriber credit consumption,
signup-bonus consumption, project-count-at-unlock, and Free soft-throttle
hit rate.

## Deferred naming question

Only the post-launch subscription display name remains open. Current lean:
keep `Subscribed` / `subscription` unless user research shows a branded
name would help.

## Trust signals that ship with the pricing page

- "We don't have your code": brnrd stores metadata and credentials, not
  repo contents or conversation bodies.
- "What brnrd does hold is named and bounded": account identity,
  encrypted credentials, bindings, event metadata, limited Telegram
  buffer, audit entries, billing/spawn records.
- "Self-hosted stays free": hosted brnrd is convenience, not lock-in.
- "We charge for ops, not AI": user pays AI providers directly.
- "No card-on-file for wallet by default": top-ups are one-shot unless
  auto-topup is enabled.
- "Auditability is part of the product": billing, credential use, prompt
  resolution, and spawns are visible to the account.

## Lineage

- 2026-05-22: drafted from the managed-mode work-continuity reframe as
  Free dispatcher + paid managed compute + possible BYO.
- 2026-05-25: credits-only wallet model adopted, then rejected as the sole
  pricing surface because it left fixed platform cost uncovered.
- 2026-05-25/26: accepted split became subscription for platform plus
  metered credits for hosted compute; the "Plus" name and $9/500-credit
  draft were replaced with unnamed Subscribed at $5/300 credits.
- 2026-05-26: supporter/public price step, subscriber-only BYO, bucketed
  credit expiry, one-time Free signup bonus, $10 project unlock,
  binding-uniqueness abuse control, soft-throttle event overage, and
  Stripe-from-day-one callout were locked.
- 2026-06-12: compacted from accumulated proposal history into this
  state-first decision; prior detail remains in git history.
- 2026-07-06: maintainer reopened pricing shape, asking whether a $60
  one-time payment would yield more than a $10/month subscription over 6
  months. Analyzed, not applied: cohort math in
  [`design-quota-scheduling-loom.md`](design-quota-scheduling-loom.md)
  §"Pricing tension raised, not resolved" shows one-time wins the raw
  6-month cash race under every retention scenario modeled, but
  recommends reading that as a case for a one-time credit-pack **add-on**
  (using the existing non-expiring `purchased` bucket above) rather than
  replacing the accepted $5/$7 subscription — flagged as a fork for the
  maintainer, this page's numbers unchanged.
