# Design: billing — subscription + credit wallet on brnrd

**Status: accepted 2026-05-26** (locked in PR #40 MR review,
locking pass IV — added the **overdraft envelope feature**:
spawn-start gate is `current_balance >= 0` AND
`estimated_spawn_cost <= current_balance + max_overdraft_credits`;
per-account `max_overdraft_credits` setting (default 0;
Subscribed can raise within `BRNRD_SUBSCRIBER_MAX_OVERDRAFT_CREDITS`
= 500 credits = $5 default cap)). Fluid past the contracts.
Two billing legs, both Stripe-integrated from day one (no
manual-invoicing fallback at launch). The subscription tier
deliberately has no marketing name; CLI verb is
`brr brnrd subscription` / sibling `brnrd subscription`. Two billing legs that back the pricing model in
[`decision-pricing-shape.md`](decision-pricing-shape.md):

1. **Subscription** — $5/month recurring Stripe subscription
   ($50/year annual alternative) for the first 200 supporters,
   then $7/$70 for the public cohort. Unlocks bigger project
   headroom (25 vs 3 on Free, unlimited after $10 of
   cumulative top-ups), generous event + compute included
   grants, full dashboard, 90-day audit, email support.
   Reshaped on 2026-05-25 (pass-4 follow-up, third wave)
   after the credits-only model proved self-defeating for
   sustainability; reshaped again 2026-05-26 to drop the
   "Plus" branding and to land on $5/month + 300 included
   credits; refreshed 2026-05-26 (locking pass II) with the
   tiered project cap + signup-bonus shape.
2. **Credit wallet** — $0.01/credit, one-shot Stripe Checkout
   top-ups, no card-on-file by default (except opt-in
   auto-topup). Backs metered compute over the included grant
   on either tier.

Each billing leg matches its cost shape: the subscription
covers brnrd's fixed platform infrastructure (always-on bots,
dispatcher, multi-project routing, dashboard, postgres); the
wallet covers variable per-spawn cloud compute. Mixing them
in one model would either over-charge occasional users
(everything-subscription) or under-charge serious users
(everything-credits — the previous mistake).

## Scope

In scope:

- Subscription mechanics (price, billing cadence, Stripe
  product, upgrade/downgrade prorating, period boundaries).
- Wallet model + **credit bucket ledger schema and per-source
  expiry policy** (`free_signup_bonus` / `subscriber_monthly` /
  `purchased` / `promotional`), debit priority, Free
  signup-bonus mechanics, account-dormancy bound on the
  purchased-never-expires guarantee, deferred-revenue
  accounting framing.
- Debit mechanics (when does a spawn debit; what happens on
  partial / failed spawns; BYO-compute spawns that bypass the
  wallet entirely).
- Top-up flow (Stripe Checkout one-shot purchases; opt-in
  auto-topup).
- Zero-balance UX.
- Refund policy (both subscription and wallet).
- Stripe integration shape from the brnrd side (Stripe France
  for HugiMuni SAS; payouts to Qonto; Stripe Tax for EU VAT;
  shared Stripe account across both subscription + one-shot
  products).
- Audit log entries for every billing operation.
- BYO-compute billing impact (subscribers who BYO contribute
  pure subscription revenue; the wallet is bypassed for BYO
  spawns).

Out of scope, explicitly:

- The dispatcher / spawn protocol itself
  ([`design-brnrd-protocol.md`](design-brnrd-protocol.md)).
- Pricing tier shape / sustainability math / what gates each
  tier ([`decision-pricing-shape.md`](decision-pricing-shape.md)).
- The generalised credential vault internals (lives in
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) —
  not a billing concern, even though subscribers using
  private Docker images and BYO cloud need credentials in the
  vault).
- The dormancy state-machine wiring (lives in the brnrd
  backend's account-state engine; we only specify what
  dormancy means for the billing ledger here).
- Per-team / per-seat tier (v-next).
- Crypto, invoicing (per-invoice prepay), or any non-Stripe
  payment rail at launch.

## Subscription

The platform-coverage billing leg.

| Concept | Value |
|---------|-------|
| Stripe product | One Stripe `Product` ("Brnrd Subscription") with one recurring `Price` ($5/month) plus an annual `Price` variant ($50/year, ~17% off). Both products live under the same Stripe account as the wallet top-up products. |
| Billing cadence | Monthly or annual (user choice at checkout). Stripe handles renewals + dunning + 3DS re-auth. |
| Subscriber grants | On subscribe AND on every renewal: 300 spawn-credits granted to the account's "subscriber monthly grant" sub-bucket (separate from paid credits and from Free monthly grant). Replaces previous month's unused subscriber grant; doesn't accumulate. |
| Subscriber caps | Account flags update: `tier = "subscribed"`, `max_projects = 10`, `monthly_event_cap = 10000`, `audit_retention_days = 90`. Daemon + brnrd-side dispatcher both read `subscription.tier` from account-scope settings (per [`design-config-layout.md`](design-config-layout.md)) to apply the right caps. |
| Upgrade (Free → Subscribed) | Stripe Checkout for the subscription product. On `checkout.session.completed` + `customer.subscription.created`: flip `tier=subscribed`, grant prorated subscriber credits (`300 × days_remaining_in_month / days_in_month`), raise caps immediately. |
| Cancel (Subscribed → Free) | Cancel-at-period-end (Stripe `subscription.cancel_at_period_end = true`). At period boundary: flip `tier=free`, drop caps. If the account has >3 projects at the boundary, dashboard surfaces "pick which 3 projects to keep on Free" before nuking — daemons keep their state, brnrd just refuses to dispatch events for non-kept projects until the user resolves. Subscriber-included compute credits ARE clawed back at the boundary (they were the "what your sub bought" surface; consuming them is fine, but the grant doesn't outlive the subscription). |
| Mid-cycle upgrade | Stripe handles proration of the subscription charge automatically. Brnrd grants subscriber credits prorated to the remaining month. |
| Mid-cycle cancel | Period-end only; no immediate refund or removal of features. Avoids "I cancelled mid-month, where did my projects go?" surprise. |
| Failed renewal payment | Stripe dunning runs (3 retries over ~3 weeks); during dunning the subscription is `past_due` but the account stays on subscriber caps (grace period). Final dunning failure → `tier=free`; cancellation notification via gate + email. |

## Wallet model

The compute-coverage billing leg. Headline contract:

| Concept | Value |
|---------|-------|
| Credit unit | **1 credit = $0.01** (10,000 credits = $100). Simple math, no exchange-rate weirdness on internal accounting |
| Top-up amounts (UI presets) | $5, $20, $50, $100, custom (min $5, max $500/single transaction at launch) |
| Currency at launch | USD on the wallet ledger; EUR / GBP / etc. accepted via Stripe at purchase time (Stripe converts; we hold credit-USD on the ledger) |
| Account balance UI | Always shows split: "200 purchased + 230 subscriber this month = 430 available" or "7 signup bonus (expires May 15) = 7 available" |
| Bucket model | **Four sub-buckets at launch, with per-source expiry**: `free_signup_bonus` (one-time on Free account creation, 30-day expiry), `subscriber_monthly` (recurring with subscription, billing-cycle expiry), `purchased` (never expires, account-dormancy bounded), plus `promotional` reserved for future use. Full table + debit priority + expiry policy in the next section. |
| Debit order | Grants first (soonest-expiring within grants); `purchased` last, FIFO. Preserves the user's purchased balance. |

## Top-up flow

```
User                              brnrd                          Stripe
 │                                  │                              │
 │ brr brnrd topup 20               │                              │
 │ ───────────────────────────────► │                              │
 │                                  │ POST /v1/checkout/sessions   │
 │                                  │ ────────────────────────────►│
 │                                  │                              │ creates session
 │                                  │ ◄────────────────────────── │
 │                                  │ session_id + checkout_url    │
 │ ◄─────────────────────────────── │                              │
 │ "Pay at: https://checkout..."    │                              │
 │                                  │                              │
 │ (user clicks, pays in Stripe)    │                              │
 │ ────────────────────────────────────────────────────────────────►│
 │                                  │                              │ charges card,
 │                                  │ ◄────────────────────────── │ fires webhook
 │                                  │ checkout.session.completed   │
 │                                  │ + payment_intent.succeeded   │
 │                                  │                              │
 │                                  │ credit ledger += 2000        │
 │                                  │ audit log: topup +2000       │
 │                                  │                              │
 │                                  │ gate notify (if connected):  │
 │ ◄─────────────────────────────── │ "Topup received: 2000 cr"    │
```

Implementation notes:

- **Stripe Checkout** (not Stripe Elements) — hosted UI, no card
  data ever touches brnrd, PCI scope minimal. Each session is a
  one-shot purchase; no `setup_future_usage`.
- **Idempotency** keyed on Stripe's `payment_intent.id`. Replays
  of the webhook never double-credit.
- **Webhook verification** with the Stripe signing secret;
  webhook endpoint is `POST /v1/internal/stripe/webhook`. Webhook
  failures go to a small redelivery queue.
- **Failed payments** (e.g. 3DS challenge declined) leave the
  wallet untouched; user retries with a fresh checkout session.

## Debit mechanics

A spawn debits the wallet **at finalize**, not at spawn-start:

```
Spawn starts.
Spawn runs.
Spawn finalizes (success, failure, or timeout):
  cost = fly_billing_minutes × per_minute_rate + platform_margin
  cost_credits = ceil(cost × 100)        # USD → credits
  ledger.append(debit, cost_credits, spawn_id, project_id)
  if wallet.balance < 0:
    wallet.balance is allowed to go slightly negative on the
    final spawn that triggered the overshoot; spawn still
    completes; the user is asked to top up before the next
    spawn.
```

Why finalize-time, not start-time:

- Spawn duration / cost is only known at finalize.
- Start-time reservation would require reserve-then-true-up, two
  ledger entries per spawn, more code, more failure modes.
- The "negative slip" is bounded by one spawn's max cost (Fly
  Machine's spend-cap-per-invocation is bounded; brnrd sets a
  hard cap of $X per spawn at launch — e.g. 30 minutes max).

Failure-mode debits:

| Outcome | Debit |
|---------|-------|
| Spawn succeeded | Full cost |
| Spawn failed with output (e.g. runner errored, agent crashed) | Full cost (CPU was used) |
| Spawn failed before runner started (e.g. Fly Machine couldn't boot) | **No debit**; logged as brnrd-side failure; surfaced to ops |
| Spawn cancelled by user mid-run | Cost up to cancellation point |
| Spawn timed out at brnrd hard cap | Full cost up to cap |

## BYO-compute spawns — wallet bypass

Subscribers who BYO their cloud (per
[`decision-pricing-shape.md`](decision-pricing-shape.md)
§ "Compute: managed vs BYO") have their spawns dispatched to
the subscriber's own cloud account using credentials from the
vault. These spawns **do not touch the credit wallet** — the
subscriber pays the cloud provider directly, brnrd has no
visibility into the underlying cloud cost, no debit happens.

Billing implications:

- **Subscribers who BYO contribute pure subscription revenue**
  ($5/$7 per month, zero compute markup).
- **Subscribers who mix BYO + managed** (e.g. BYO Fly for some
  projects, managed compute for others) hit the wallet only on
  the managed spawns. The included `subscriber_monthly` grant
  applies to managed spawns only.
- **Audit log** for BYO spawns: `debit_spawn` is not emitted;
  instead `spawn_byo` is logged with metadata (account_id, ts,
  spawn_id, project_id, provider, scheduled_machine_id) so the
  dashboard can show "spawn ran in your Fly account, ~$X
  estimated cost" without us actually billing.
- **Free users** cannot BYO (the policy gate is
  `subscription.tier == "subscribed"`), so the wallet stays
  the universal compute-cost surface for Free.
- **Self-hosters** running their own brnrd don't enter this
  surface at all — their billing is whatever Stripe / cloud
  setup they configure on their own deployment.

The wallet's only role for BYO subscribers is preserving any
top-up balance they purchased before switching to BYO. The
balance stays on the ledger forever (per the `purchased`
no-expiry policy) and can be spent on managed envs the user
adds later (managed Modal once shipped, etc.) or refunded if
unused within 30 days of purchase.

## Zero-balance UX (and the overdraft envelope)

The spawn-start gate is **`current_balance >= 0`**, not
`>= estimated_spawn_cost`. The reason: estimating a spawn's
cost ahead of dispatch is approximate (token consumption is
agent-driven, runtime depends on the task), so requiring
the full estimate up-front would either reject legitimate
spawns whose actual cost ends up smaller OR over-charge
users for the gap. Instead, the gate is "do you have
non-negative balance?" — and the spawn that pushes you
below zero is allowed within a configurable **overdraft
envelope**.

### The overdraft envelope

Each account has a `max_overdraft_credits` setting (signed
integer; default 0; positive = "the last spawn is allowed
to leave my balance this many credits negative"). Per-tier
ceilings on what the user can set:

| Tier | Default | User-configurable up to |
|------|---------|-------------------------|
| **Free** | 0 (locked) | 0 — can't be raised |
| **Subscribed** | 0 (opt-in) | `BRNRD_SUBSCRIBER_MAX_OVERDRAFT_CREDITS` (default 500 credits = $5) |
| Higher tiers (future) | TBD | TBD higher ceiling within "ok-for-us limits" |

The ceiling env knob lets ops re-tune the cap without a
code release; the per-account knob lets each subscriber pick
their own comfort level within that cap (some prefer 0 / no
overdraft; some prefer the full envelope so the very last
spawn of the month doesn't get rejected mid-flow).

### Spawn-start check

```
Event arrives.
Dispatcher checks the wallet against the spawn's estimated cost:

  if current_balance >= 0
     AND estimated_spawn_cost <= current_balance + max_overdraft_credits:
    proceed (normal permission prompt or auto, per policy)
  else:
    a) if user has auto-topup enabled and a saved payment method:
         trigger top-up automatically; proceed once credited
    b) otherwise:
         enqueue the event (don't drop)
         gate notify: "Out of credits to spawn this task. Top up
         at https://brnrd.dev/wallet → we'll dispatch the
         queued task once balance is available."
         (event auto-dispatches within 5 min of top-up; queue
          TTL = 24h, after which the event is dropped with a
          gate notification)
```

Two conditions in one gate:

1. **`current_balance >= 0`** — must be non-negative at
   spawn start. If a prior spawn already pushed you
   negative, the next spawn waits until a top-up brings
   you back to zero (or positive).
2. **`estimated_spawn_cost <= current_balance + max_overdraft_credits`**
   — the estimate must fit inside the headroom (balance +
   overdraft envelope). If estimated cost is bigger than
   the headroom, reject up-front (don't quietly start a
   spawn we know we can't afford to land).

### What happens during + after the spawn

The spawn runs to completion even if actual cost exceeds
the estimate by some margin. Debit at completion is the
**actual** measured cost. Possible outcomes:

| Outcome | Where balance lands |
|---------|---------------------|
| Actual cost ≤ estimate | Balance stays in the planned range; may stay positive |
| Estimate accurate, balance was small | Balance lands as far negative as the estimate predicted (within the overdraft envelope) |
| Actual cost > estimate by small margin | Balance lands slightly more negative than expected; still bounded by reasonable runtime + the per-spawn safety multiplier the dispatcher applies |
| Actual cost wildly exceeds estimate | Caught by the per-spawn timeout / cost-cap kill in [`plan-failover-compute.md`](plan-failover-compute.md) — spawn is terminated before it can drive balance arbitrarily negative |

The dashboard surfaces both the current balance (signed)
AND the overdraft envelope when balance is negative:

```
Credit balance: -120 credits  ($−1.20)
Overdraft used: 120 / 500 credits  (24% of envelope)

Top up to keep failover compute running →
```

Banner nudge when balance crosses 0 (per
[`decision-pricing-shape.md`](decision-pricing-shape.md) §
"Dashboard nudges + transparency"):

```
You're $1.20 in the red — top up to keep failover compute
running. Your last few spawns dipped into your overdraft
envelope (500 credits = $5).
```

Gate-side nudge footer on the response from the spawn that
crossed zero:

```
[ this spawn used your overdraft envelope. balance: -$1.20.
  top up at brnrd.dev/topup to keep failover running → ]
```

### Why this shape

- **Friendly to the very-last spawn of the month.** A
  subscriber working through their 300-credit monthly grant
  shouldn't get a rejection at credit 301 just because the
  estimator was conservative. The overdraft envelope lets
  the spawn land, the resulting negative balance is the
  signal to top up.
- **Bounded.** The envelope is configurable but capped at
  the tier ceiling — there's no path to runaway debt.
  Per-spawn timeout + cost-cap kill (existing) bound a
  single spawn's blast radius.
- **Self-correcting.** Top-ups credit normally; if a top-up
  lands with a negative balance, the credit first clears
  the negative (no interest, no penalty fee), then the
  remainder goes into the `purchased` bucket. The user is
  whole as soon as they top up.
- **Honest in the UX.** Negative balance is always
  surfaced (dashboard, gate footer, the next-spawn
  rejection message). Silent overdraft would be the
  actually-mean version; explicit overdraft with a
  configurable cap is the friendly version.

Auto-topup design (unchanged from the pre-overdraft shape):

- Off by default; user opts in.
- User picks: trigger threshold (default: when balance < 100
  credits) + top-up amount (default: $20).
- Saved payment method required; **only when auto-topup is
  enabled** does brnrd hold a Stripe customer + card-on-file.
  Without auto-topup, no card is stored.
- Per-day auto-topup cap (default $50, configurable) to prevent
  runaway.
- Every auto-topup hits the audit log + gate notify.
- Plays well with the overdraft envelope: if both are on,
  auto-topup fires at the threshold and the overdraft is
  effectively never used; if auto-topup is off, overdraft
  is the soft-landing for the rare end-of-cycle spawn.

## Refund policy

Documented on the pricing page. Two billing legs, two policies:

**Wallet (one-shot top-ups):**

- **Unused `purchased` credits**: refundable pro-rata within 30
  days of purchase. User requests via dashboard or email;
  refund processed within 5 business days. Stripe handles the
  actual refund (against the original `payment_intent` where
  Stripe's 6-month refund window allows); brnrd debits the
  ledger correspondingly. Beyond the 30-day window the credits
  remain valid forever; they're just not cash-refundable
  anymore.
- **Used credits**: not refundable (the compute was consumed;
  brnrd paid Fly already).
- **Spawn failures attributable to brnrd** (Fly-side outage,
  brnrd-side bug): credit auto-refunded; user notified.
- **Spawn failures attributable to the user's task** (agent
  errored, code didn't compile): not refunded; the spawn ran.
- **`free_signup_bonus` / `subscriber_monthly` / `promotional`
  grant credits** are not refundable (they were never paid for
  in cash; they were granted on the house or bundled with a
  subscription / promotion).
- **Account closure**: unused `purchased` balance refunded to
  the original Stripe payment method within 30 days where
  Stripe's window allows, OR via manual refund process where
  it doesn't; user receives final ledger statement. Grant
  buckets are zeroed without refund.

**Subscription:**

- **Cancel anytime**: takes effect at period end (Stripe
  `cancel_at_period_end=true`). User retains subscriber access
  for the remainder of the paid period. No proration on
  cancel — cancelling on day 1 vs day 28 of a month gives the
  same remaining-period access until the boundary.
- **Refund of mid-period subscription charge**: at brnrd's
  discretion, for genuine cases (user accidentally subscribed,
  signed up and immediately realised wrong account, etc.).
  Documented as case-by-case rather than guaranteed — the
  generous-Free-tier + Customer Portal cancel-anytime should
  cover ~all real cases without needing this escape hatch.
- **Annual subscription cancel mid-year**: prorated refund of
  the unused months, returned to Stripe within 5 business days.
- **Subscriber credits granted that month** stay on the
  account through the period end; unused `subscriber_monthly`
  credits expire at the period boundary alongside the
  subscription (the bucket is cleared in the same transaction
  that flips `tier=free`).

The 30-day wallet window is generous enough for "I made a
mistake" recovery and short enough that brnrd's cash position
stays predictable. The cancel-at-period-end subscription
default avoids the "I cancelled mid-month, did I lose
everything?" panic that always-immediate cancels create.

## Credit buckets and expiry policy

The "temporal grouped resources" problem (grants vs purchases,
recurring vs one-shot, with-expiry vs without) is solved with
the standard **bucketed-ledger** shape used by OpenAI / Anthropic
/ AWS / GCP / Stripe Customer Balance / most metered SaaS
billing platforms. Each bucket has its own source, expiry
policy, rollover policy, and refund policy:

| Bucket | Granted on | Expires | Rolls over | Refundable | Audit op |
|--------|-----------|---------|-----------|------------|----------|
| `free_signup_bonus` | One-time on Free account creation (10 credits) | 30 days from creation OR on full consumption, whichever first | No | No (was never charged for) | `grant_free_signup_bonus` / `expire_free_signup_bonus` |
| `subscriber_monthly` | Subscription start (prorated) + every renewal | End of current billing cycle | **No** | No (included in the sub) | `grant_subscriber_monthly` / `expire_subscriber_monthly` |
| `purchased` | Stripe Checkout top-up confirmed | **Never** (account-dormancy bounded; see "Account dormancy" below) | Yes | Pro-rata within 30 days (see Refund policy) | `topup` / `refund_purchased` |
| `promotional` *(future-proofing; not used at launch)* | Referral / support-issued goodwill / campaign | Per-grant `expires_at` (typically 30-90 days) | No | No | `grant_promotional` / `expire_promotional` |

### Debit priority (FIFO within bucket, expiry-aware across buckets)

```
On debit(amount):
  1. Drain free_signup_bonus until empty or amount satisfied
  2. Drain subscriber_monthly until empty or amount satisfied
  3. Drain promotional grants in soonest-expiring-first order
  4. Drain purchased grants FIFO (oldest top-up first)
  5. If still short, the spawn's final debit is allowed to leave
     purchased slightly negative (≤ one spawn's hard cap);
     enqueue subsequent events until top-up.
```

This means a user's `purchased` balance is **always preserved
last**. The grants get consumed first, exactly matching the
user's intuition ("my grant runs out, then I dip into what I
paid for"). Promotional credits with near-term expiry drain
ahead of long-lived purchases. The implementation is one
priority-ordered loop over bucket records, ~30 lines.

A Free account that has both a `free_signup_bonus` AND
purchased credits (because the user topped up at $0.01/credit
without subscribing) drains the bonus first, then the
purchased — preserves the "your $5 top-up is still there
after the bonus runs out" expectation.

### Per-bucket expiry mechanics

**`free_signup_bonus` — one-time on Free signup, 30-day expiry.**

```
On Free account creation:
  grant 10 free_signup_bonus credits, expires_at = now + 30 days

On daily expiry sweep:
  expire any free_signup_bonus rows where expires_at <= now

On full consumption:
  bucket row removed (zero balance; no separate expiry needed)
```

The signup-bonus shape replaces the earlier "5 credits / month
activity-gated" recurring grant on 2026-05-26 (locking pass
II). Bounded by signup count (not by active-user retention) —
100K Free signups total caps cost at $10K total (one-time, not
per year). Removes the activity-gating logic that the
recurring grant required. See
[`decision-pricing-shape.md`](decision-pricing-shape.md)
§ "Free compute grant — one-time signup bonus, not recurring"
for the rationale + math + optics in full.

**`subscriber_monthly` — use-it-or-lose-it at billing cycle.**

```
On subscription start (Stripe webhook customer.subscription.created):
  grant 300 subscriber_monthly credits, prorated to days
  remaining in the current billing cycle

On subscription renewal (Stripe webhook invoice.paid):
  expire any unused subscriber_monthly balance
  grant 300 subscriber_monthly credits

On subscription cancel-at-period-end (period boundary):
  expire any unused subscriber_monthly balance
  flip tier=free; no free_signup_bonus is granted (bonus is
  one-time on Free account creation, not on tier transition)
```

The subscriber grant refreshes unconditionally on every
billing cycle — the subscription itself is the activity
signal.

**`purchased` — no expiry, bounded by account dormancy.**

Purchased credits are user property. The user paid; the user
keeps them, forever, period. This exceeds OpenAI's / Anthropic's
1-year expiry (which both generate user complaints) and matches
the strongest EU consumer-protection expectation on prepaid
digital balances.

The "forever" promise is bounded operationally by an account-
dormancy policy, not by credit expiry:

- **24 months of account inactivity** → account marked
  `dormant`; brnrd-side services pause (no spawn dispatch, no
  event processing, optional bot disconnection prompted). All
  `purchased` credits remain on the ledger, untouched.
- **36+ months of dormancy** → dashboard prompt for
  reactivation on next login; banner persists across sessions
  until acknowledged.
- **Reactivation** (login OR explicit user action) → account
  un-dormants, credits remain exactly as they were, services
  resume.
- **Deletion** (only on explicit user request OR GDPR
  right-to-erasure) → unused `purchased` credits refunded to
  the user's original Stripe payment method (where the
  6-month-old Stripe refund window allows) OR to a manual
  refund process; account + ledger entries deleted.

This bounds the operational tail on dormant accounts at
zero (paused = no compute spend by definition) without
touching the "credits are yours forever" promise that drives
the trust signal.

**`promotional` — per-grant `expires_at`.**

Reserved for future use (signup bonuses, referral credits,
support-issued goodwill). Each grant carries its own explicit
expiry timestamp; the bucket entries include both `expires_at`
and `source_campaign_id` for analytics. At launch the bucket
exists in the schema but no grants are issued — the launch
mechanic is the supporter cohort price, not a promotional
credit grant.

### Dashboard language (the optics layer)

The dashboard **never says "your credits expired."** It says:

- "Your monthly allowance refreshes on &lt;date&gt;." (forward-looking)
- "Your monthly allowance reset on &lt;date&gt;, new balance: 5
  credits." (just-happened)
- "Your purchased balance: 420 credits ($4.20 prepaid)." (long-
  lived, always visible)

The balance UI shows a single top-line number with a
breakdown on hover / expand:

```
430 credits available
    230  this month (subscriber, refreshes Jun 1)
    200  purchased (no expiry)
```

For Free users on the signup bonus:

```
7 credits available
    7  signup bonus (expires May 15, 23 days left)
```

For Free users whose signup bonus expired / consumed (no
purchased balance):

```
0 credits available — signup bonus consumed.
    Top up at $0.01/credit or subscribe for 300 credits/month.
```

For Free users who topped up after the signup bonus expired:

```
1,000 credits available
    1,000  purchased (no expiry)
```

For subscribers who BYO and don't spend the grant:

```
500 credits available
    300  this month (subscriber, refreshes Jun 1)
    200  purchased (no expiry)
```

The grant being prominently called "this month" + "refreshes"
makes the use-it-or-lose-it mechanic feel like an allowance,
not a deadline. Empirically this is how every monthly-allowance
product is communicated (mobile data, cloud quotas, gym
classes).

### Cumulative purchase tracking and the subscriber project cap unlock

Two account-level derived counters drive the
[subscriber project cap unlock policy](decision-pricing-shape.md)
(25 default → unlimited after $10 of cumulative top-ups):

| Field | Source | Update | Decrement on refund? |
|-------|--------|--------|---------------------|
| `cumulative_purchased_credits_lifetime` | Sum of all `topup` audit ops for this account, ever | On every successful `topup` (Stripe webhook) | **No** |
| `cumulative_purchased_usd_lifetime` | `cumulative_purchased_credits_lifetime / 100` (credits → USD at $0.01) | Derived; not separately stored | **No** |
| `project_cap_unlocked` | Derived: `cumulative_purchased_usd_lifetime >= 10` | Set on the topup that crosses the threshold; never cleared | **No** |

Mechanics:

- Both counters are **monotonic, never-decreasing**. Refunds
  don't decrement (the spend happened, even if the cash later
  came back). This is the right shape: the unlock is a trust
  signal about cumulative usage, not a "credit you can claw
  back."
- `project_cap_unlocked` is computed once when crossing the
  threshold and stored as a flag (not re-evaluated on every
  read) — keeps the cap check fast and decoupled from the
  ledger query path.
- The flag is **permanent on the account**. Subscription
  cancel → re-Free → re-subscribe later does NOT reset the
  flag. While Free, the user is capped at 3 projects per
  Free's cap (not the unlocked cap); the flag only matters
  while subscribed.
- New audit op `project_cap_unlocked_at` logs the timestamp +
  the topup that triggered the unlock, for the dashboard's
  "you unlocked unlimited projects on <date>" line.

Effective project cap at any point in time:

```
def effective_project_cap(account):
    if account.tier == "subscribed":
        if account.project_cap_unlocked:
            return None  # unlimited
        return 25
    elif account.tier == "free":
        return 3
```

Project-creation endpoint enforces this on each attempt;
returns a `subscription_hint` field in the 403 response that
the CLI / dashboard surface as the nudge ("subscribe for 25
projects, unlimited after $10 cumulative top-ups").

### Account dormancy and the "purchased never expires" tail

| Account state | Trigger | Effect on services | Effect on `purchased` balance |
|---------------|---------|-------------------|------------------------------|
| `active` | Default | All services on | Spendable normally |
| `dormant` | 24 mo no activity | Spawn dispatch paused; events queued or dropped per policy; bots may disconnect | Preserved exactly; no expiry |
| `dormant_long` | 36 mo no activity | Same as dormant + dashboard prompt persists | Preserved exactly; no expiry |
| `deleted` | Explicit user request OR GDPR | Account + ledger entries removed | Refunded to the original Stripe payment method (within Stripe's 6-month refund window) OR manual refund process |

The dormancy policy is a separate account-state machine from
the billing ledger. The ledger entries for `purchased` credits
have **no `expires_at` value at all**; the bucket has no expiry
sweep job. Dormancy is enforced at the service layer (dispatcher
+ event processor + bot adapter), not at the ledger layer. This
keeps the property "purchased credits are immortal data" clean
and verifiable from the ledger schema alone — important if a
user audits their balance, important for the trust signal,
important for any future migration off Stripe.

## Audit log entries

Every billing operation appears in `account_audit`. Metadata-only
per the data-minimization principle:

| Operation | Fields |
|-----------|--------|
| `subscription_started` | account_id, ts, plan (`monthly` / `annual`), stripe_subscription_id |
| `subscription_renewed` | account_id, ts, plan, period_start, period_end, stripe_invoice_id |
| `subscription_canceled_at_period_end` | account_id, ts, period_end |
| `subscription_canceled_immediate` | account_id, ts, reason (`dunning_failed` / `admin`) |
| `subscription_plan_switched` | account_id, ts, from_plan, to_plan, prorated_amount |
| `grant_subscriber_monthly` | account_id, ts, amount_credits, month, prorated_from? |
| `expire_subscriber_monthly` | account_id, ts, amount_credits, month |
| `grant_free_signup_bonus` | account_id, ts, amount_credits, expires_at |
| `expire_free_signup_bonus` | account_id, ts, amount_credits, reason (`time` / `consumed`) |
| `grant_promotional` | account_id, ts, amount_credits, expires_at, campaign_id |
| `expire_promotional` | account_id, ts, amount_credits, campaign_id |
| `topup` | account_id, ts, amount_credits, stripe_payment_intent_id, source (manual / auto), bucket=`purchased` |
| `debit_spawn` | account_id, ts, amount_credits, spawn_id, project_id, sub_bucket (`free_signup_bonus` / `subscriber_monthly` / `promotional` / `purchased`), est_vs_actual_delta |
| `spawn_byo` *(BYO subscribers; no wallet debit)* | account_id, ts, spawn_id, project_id, provider (`fly` / future), scheduled_machine_id, estimated_cost_usd |
| `refund_purchased` | account_id, ts, amount_credits, reason (user_request / brnrd_failure / dormancy_deletion), stripe_refund_id |
| `refund_grant_brnrd_failure` | account_id, ts, amount_credits, spawn_id, sub_bucket |
| `auto_topup_enabled` / `auto_topup_disabled` | account_id, ts |
| `payment_method_added` / `removed` | account_id, ts, stripe_payment_method_id |
| `account_marked_dormant` | account_id, ts, last_activity_at, dormant_state (`dormant` / `dormant_long`) |
| `account_reactivated` | account_id, ts, prior_dormant_state |
| `project_cap_unlocked` | account_id, ts, triggering_topup_id, cumulative_purchased_usd_at_unlock |
| `overdraft_settings_changed` | account_id, ts, old_max_overdraft_credits, new_max_overdraft_credits, actor (`user` / `admin`) |
| `overdraft_consumed` | account_id, ts, spawn_id, post_balance_credits (negative), overdraft_used_credits |
| `overdraft_cleared` | account_id, ts, by_topup_id, prior_negative_balance_credits |

User-visible via `brr brnrd balance` (current totals + sub
status + signed balance + overdraft envelope used) and the
dashboard's Cost / Audit view (full ledger).

## API surface (brnrd-side)

### Subscription endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/accounts/subscription` | Current state: `tier` (`free` / `subscribed` / `subscribed_past_due`), `plan` (`monthly` / `annual`), `period_end`, `cancel_at_period_end`, last 6 invoices summary |
| `POST` | `/v1/accounts/subscription/checkout` | Create Stripe Checkout session for the subscription product; body: `{plan: "monthly" | "annual"}`; returns `checkout_url` |
| `POST` | `/v1/accounts/subscription/cancel` | Mark `cancel_at_period_end=true` via Stripe API; returns updated subscription state |
| `POST` | `/v1/accounts/subscription/resume` | Clears `cancel_at_period_end` (re-activates a subscription marked for cancellation that hasn't expired yet) |
| `POST` | `/v1/accounts/subscription/portal` | Create Stripe Customer Portal session for card-update / invoice-download / plan-switch; returns `portal_url` |

### Wallet endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/accounts/wallet` | Current balance with sub-bucket split (`purchased + subscriber_monthly + free_signup_bonus + promotional`), per-month spend stats, `cumulative_purchased_usd_lifetime`, `project_cap_unlocked` flag |
| `POST` | `/v1/accounts/wallet/checkout` | Create Stripe Checkout session for one-shot top-up; returns checkout_url |
| `POST` | `/v1/accounts/wallet/autotopup` | Enable/configure auto-topup (requires saved payment method) |
| `DELETE` | `/v1/accounts/wallet/autotopup` | Disable auto-topup |
| `GET` | `/v1/accounts/wallet/ledger` | Paginated ledger (filterable by op, date range) |
| `POST` | `/v1/accounts/wallet/refund` | Request refund of unused paid credits |

### Shared

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/internal/stripe/webhook` | Stripe webhook receiver (signed). Handles both subscription events (`customer.subscription.created`, `.updated`, `.deleted`, `invoice.paid`, `invoice.payment_failed`) and one-shot events (`checkout.session.completed`, `payment_intent.succeeded`). |

The CLI's `brr brnrd subscription [status | start | cancel |
resume | portal]` verbs (with `brr brnrd subscribe` as a
shortcut for `subscription start`) wrap the subscription
endpoints; the CLI's `brr brnrd topup | balance | autotopup`
verbs wrap the wallet endpoints (per
[`decision-cli-shape.md`](decision-cli-shape.md)).

## Stripe integration shape

### Legal + account setup

- **Legal entity**: HugiMuni SAS (France). Stripe France
  account (Stripe Payments Europe Ltd is the EEA-of-record
  entity; French SAS is the merchant of record on invoices).
- **Payouts to Qonto** (the French neobank's regular IBAN).
  Settle in EUR; standard daily / weekly / monthly payout
  schedule, configurable in the Stripe Dashboard. No FX cost
  when settling EUR charges to a EUR account; small FX cost
  when EU customers top up with non-EUR cards.

### Payment methods enabled at launch

Charging is in USD on the wallet ledger; Stripe Checkout
displays the local-currency equivalent at top-up time and
converts at charge time (+1% FX when customer currency ≠
settlement currency).

| Method | Why |
|--------|-----|
| Card (Visa / Mastercard / Amex) | Default everywhere |
| **Apple Pay / Google Pay** | Friction-killer on mobile; one tap |
| **SEPA Direct Debit** | EU bank-account top-ups; low fees; no card needed |
| **iDEAL** | NL standard; high conversion rate vs card |
| **Bancontact** | BE standard |
| **EPS** | AT standard |
| **Giropay** | DE option |
| **P24** | PL standard |

All toggleable in the Stripe Dashboard with zero integration
work — Stripe Checkout renders them based on the customer's
location automatically. Worth enabling all EEA local methods
day-one; conversion rates for EU users paying with a non-card
method are materially higher than card-only.

### Strong Customer Authentication (PSD2 / SCA)

Mandatory under EU PSD2 for almost all EEA card charges. Stripe
Checkout handles 3D Secure 2 transparently — the challenge
triggers in-flow when required, the user completes it without
leaving Checkout. No code on brnrd's side. Stripe surfaces SCA
exemptions automatically when the transaction qualifies (low-
value, recurring, trusted-beneficiary).

### VAT compliance

The part most independent software vendors get wrong.

- **Stripe Tax** (paid add-on: 0.5%/transaction, no monthly
  fee) auto-calculates VAT based on customer location:
  - French customers → 20% TVA.
  - EU customers (non-FR, B2C) → customer-country VAT rate.
  - EU customers (B2B with valid VAT ID) → reverse-charge,
    zero VAT, VAT ID printed on the invoice (Stripe validates
    VAT IDs against VIES in real time at checkout).
  - UK customers → UK VAT.
  - US customers → state sales tax (auto-calculated).
  - Customers outside taxed regions → no tax.
- **OSS scheme registration** with the French DGFiP. This is
  not optional for selling digital services across the EU —
  without OSS, the SAS would need to VAT-register in every EU
  member state where it has customers. With OSS, you file one
  quarterly EU-wide VAT return through the French tax
  authority. Stripe Tax exports the OSS-formatted report.
- **TVA intracommunautaire** (HugiMuni SAS's intra-EU VAT
  number) — must appear on every invoice issued to EU B2B
  customers. Stripe inserts it automatically when configured
  on the Stripe Tax settings page.

### Tax invoicing

- Stripe auto-issues a tax invoice for every charge; the
  invoice complies with French B2B invoice requirements
  (HugiMuni SAS name + SIREN + TVA intracommunautaire,
  sequential numbering, customer details, VAT breakdown).
- Customer downloads the invoice from the dashboard's wallet
  page (the dashboard links to the Stripe-hosted invoice URL;
  brnrd doesn't generate PDFs itself).
- Invoices archived for 10 years per French commercial law —
  Stripe's invoice retention satisfies this; we don't operate
  a parallel archive.

### Stripe fees (2026 standard EU pricing)

| Customer card | Rate |
|---------------|------|
| EEA cards (the common case) | 1.5% + €0.25 |
| UK cards | 2.5% + €0.25 |
| Non-EEA cards | 3.25% + €0.25 |
| SEPA Direct Debit | 0.8% (capped €5) |
| iDEAL / Bancontact / EPS / Giropay / P24 | €0.80 per transaction |
| Apple Pay / Google Pay | Same as the underlying card |
| Currency conversion | +1% if charging currency ≠ settlement |
| Stripe Checkout | No additional fee |
| Stripe Tax | 0.5% per transaction (taxed transactions only) |

**Worked example — €20 top-up from a French Visa user**:
€0.25 + 1.5% + 0.5% (Stripe Tax) = €0.65 → 3.25% overhead.

**Worked example — €20 top-up via SEPA from a German user**:
0.8% + 0.5% (Stripe Tax) = €0.26 → 1.3% overhead. (SEPA is
materially cheaper than cards for small EU top-ups.)

The 1.5-3.5% overhead on top-ups is brnrd's platform cost; it
needs to be absorbed by the managed-compute margin. The
headline 30-50% margin over wholesale Fly cost lands closer to
**27-47% net of Stripe + Stripe Tax**, which is still healthy.

### What's configured on the brnrd side

- **Two Stripe products at launch**:
  - "Brnrd Subscription" — recurring subscription, with
    monthly ($5) and annual ($50) `Price` variants. Stripe
    handles renewals, dunning, SCA re-auth, customer portal.
    Product is intentionally unnamed in marketing copy beyond
    "the subscription" (no Plus / Pro / Premium suffix).
  - "Brnrd Wallet Top-up" — one-shot product, `checkout.session`
    with no `setup_future_usage`. Each top-up is independent.
- **Stripe Customer object** created when the user first
  subscribes OR opts into auto-topup (whichever comes first).
  Subscribers have a Customer with a card (Stripe requirement);
  pre-subscribe wallet top-ups don't need one (one-shot
  Checkout sessions complete without persisting card data).
- **Customer Portal** enabled in Stripe Dashboard so subscribers
  can update card, download invoices, switch monthly↔annual,
  cancel — without us building UI for any of it.
- **Webhook receiver** at `POST /v1/internal/stripe/webhook`,
  signature-verified with the Stripe signing secret; idempotent
  on Stripe event IDs; small redelivery queue for transient
  failures. Handles both subscription events
  (`customer.subscription.*`, `invoice.*`) and one-shot events
  (`checkout.session.completed`, `payment_intent.succeeded`).
- **Test mode** during dev: separate Stripe test API key in
  `BRNRD_STRIPE_API_KEY` env var; test webhook secret in
  `BRNRD_STRIPE_WEBHOOK_SECRET`. Stripe's test card numbers +
  SCA-triggering test cards cover the launch test matrix for
  both billing legs.

### Deferred-revenue accounting (for the implementer + accountant)

Purchased credits and subscription fees are **deferred
revenue** under French GAAP / IFRS — recognised as revenue
when the service is delivered, not when the cash arrives.
Standard double-entry accounting shape; Stripe's **Revenue
Recognition** add-on (included in Stripe Billing, free at
launch volumes, ~$0.50 per $1K of recognised revenue at
scale) automates the journal entries.

**Purchased credits — the cash-in-vs-service-out gap:**

```
On Stripe webhook payment_intent.succeeded (user paid €10):
  Bank (asset)              +€10
  Deferred revenue (liab)   +€10
  (no revenue recognised yet — service not delivered)

On debit_spawn from the purchased bucket (user used 100 credits = €1):
  Deferred revenue (liab)   −€1
  Revenue (income)          +€1
  (service delivered; revenue recognised)

On refund_purchased within 30-day window (€5 refunded):
  Deferred revenue (liab)   −€5
  Bank (asset)              −€5
  (cash returned; no revenue ever recognised on this portion)
```

**Subscription fees — recognised over the period:**

```
On Stripe webhook invoice.paid (€5 sub paid for the month of June):
  Bank (asset)              +€5
  Deferred revenue (liab)   +€5

Every day of June:
  Deferred revenue (liab)   −€0.17
  Revenue (income)          +€0.17
  (subscription service "delivered" daily)

By end of June: liability fully cleared; €5 of revenue recognised.
```

Stripe Revenue Recognition handles this **daily proration
automatically** when the subscription product is configured.
No code on brnrd's side; the accountant pulls the monthly
report and posts the journal entries (or uses a Stripe-to-
accounting bridge like Stripe Accounting Reports → Pennylane
/ Quickbooks / etc.).

**Grants are NOT deferred revenue:**

`free_signup_bonus` / `subscriber_monthly` / `promotional`
credits never enter the deferred-revenue account at all. No
cash was ever received against them; they're an **operational
cost of goods** when consumed (you pay Fly for the compute),
recorded directly on the income statement as expense, not on
the balance sheet as a liability:

```
On debit_spawn from a grant bucket (subscriber used 100 credits):
  Compute cost (expense)    +€0.30   # actual wholesale Fly cost
  (no liability cleared; no revenue recognised on this spawn —
   the subscription revenue already covers this via the daily
   subscription proration above)
```

The subscriber-monthly grant is effectively "bundled compute
COGS paid out of the subscription revenue" — the income side
is the daily subscription recognition; the COGS side is the
compute spend when the user consumes it. The net margin on a
subscriber who consumes their full 300-credit grant is
(€5/mo recognised revenue) − (€3/mo of Fly compute) = €2/mo
of platform-recovered margin, before Stripe fees.

**Practical setup at HugiMuni SAS scale:**

- One Stripe account + one Qonto IBAN at launch. No separate
  bank account needed for the deferred-revenue liability —
  the liability is tracked in the books, not in a separate
  cash account.
- French accountant sets up the chart of accounts:
  - `Bank` (asset)
  - `Produits constatés d'avance` / `Deferred revenue` (liability) — the line that holds the unconsumed `purchased` balance + unrecognised subscription portion.
  - `Revenue — subscriptions` (income)
  - `Revenue — compute credits` (income)
  - `Compute cost — Fly` (expense, COGS)
  - `Compute cost — promotional / signup-bonus` (expense, marketing or COGS depending on policy)
- Monthly bookkeeping reconciles Stripe payouts against the
  recognised revenue + cleared deferred revenue. Stripe's
  Revenue Recognition exports feed directly into this.
- **Bank-account separation (operating vs reserve)** is a
  treasury-hygiene practice for once MRR is meaningful (rule
  of thumb: ≥€10K MRR OR deferred-revenue liability
  persistently exceeds 3 months of operating costs). At launch
  scale, one Qonto operating account is sufficient. When it
  becomes warranted: split into operating (day-to-day spend)
  + reserve (held against deferred revenue + 1× monthly burn
  buffer). This is self-imposed discipline, not a legal
  requirement.
- **No legal segregation** required in France for SaaS prepaid
  balances. Gift cards (in some US states), escrow funds, and
  banking-as-a-service custodial funds are the products that
  require segregation; SaaS credit balances explicitly aren't.

**"What if Free signup bonuses eat through our cash?"** —
NOT a deferred-revenue problem (no cash was paid against
them). It's a COGS line on the income statement. The
signup-bonus shape bounds it at total signup count × 10
credits = $0.10 per signup, paid as you go from operating
cash. At 100K signups total: $10K of compute COGS, spread
over however long it takes to acquire those signups. Handled
by cash-runway management, not by escrow-shaped accounting.
The activity-gating logic that the recurring grant required
is no longer relevant.

## Launch defaults + tunable knobs

All launch-shape numbers below are sourced from
[`decision-pricing-shape.md`](decision-pricing-shape.md) §
"Launch-tunable knobs" — that page is the canonical list,
this page is the implementation. Each value the billing
layer reads is wired to a `BRNRD_*` env knob so ops can
re-tune without a code release:

| Used by | Env var | Default | Source line in pricing-shape |
|---------|---------|---------|------------------------------|
| Free signup-bonus grant | `BRNRD_FREE_SIGNUP_BONUS_CREDITS` | `10` | one-time grant on Free account creation |
| Free signup-bonus expiry | `BRNRD_FREE_SIGNUP_BONUS_EXPIRY_DAYS` | `30` | days from grant before expiry |
| Project-cap unlock threshold | `BRNRD_PROJECT_CAP_UNLOCK_USD` | `10` | cumulative top-up $ that flips `project_cap_unlocked` |
| Subscriber-monthly grant size | `BRNRD_SUBSCRIBER_MONTHLY_CREDITS` | `300` | credits granted on every renewal |
| Supporter cohort size | `BRNRD_SUPPORTER_COHORT_SIZE` | `200` | atomic counter; after this, new subs default to public price |
| Supporter price (monthly USD) | `BRNRD_SUBSCRIBER_PRICE_SUPPORTER_USD` | `5` | grandfathered forever for the first cohort |
| Public price (monthly USD) | `BRNRD_SUBSCRIBER_PRICE_PUBLIC_USD` | `7` | second Stripe `Price` ID |
| Annual discount %      | (derived; both prices map to ~17% off via fixed `$50` / `$70` annual `Price` IDs) | ~17% | |
| Account-dormancy pause | `BRNRD_DORMANCY_PAUSE_MONTHS` | `24` | months inactive → pause services, preserve `purchased` |
| Account-dormancy prompt | `BRNRD_DORMANCY_PROMPT_MONTHS` | `36` | months inactive → dashboard reactivation prompt persists |
| Subscriber overdraft envelope cap | `BRNRD_SUBSCRIBER_MAX_OVERDRAFT_CREDITS` | `500` (= $5) | ceiling on the per-account `max_overdraft_credits` setting for Subscribed; Free is locked at 0 |

The billing service reads these via the standard env-config
loader on startup; values are immutable for the lifetime of
a process (no hot-reload). Re-tuning is a config change +
rolling restart, not a code change. Two prices stay distinct
Stripe `Price` objects (cohort-grandfathering only works if
each subscriber's `subscription.items[].price` references the
price they signed up at).

## What we do NOT do at launch

- **Card-on-file for wallet by default**. Only opt-in via
  auto-topup. (Subscribers necessarily have a card on file —
  that's how recurring subscriptions work — but the wallet
  leg is one-shot unless the user explicitly enables
  auto-topup.)
- **Per-team / per-seat billing**. Per-account only; team /
  per-seat is the v-next surface (Linear-shape, ~$5/seat over
  the subscription base).
- **Invoicing / NET-30**. All purchases (subscription and
  one-shot top-ups) are prepay via Checkout. Enterprise
  invoicing is a v-next ask.
- **Crypto / non-Stripe payment rails**. Stripe-only.
- **Custom currency on the ledger**. USD-only on the internal
  ledger; Stripe converts at purchase time. (UI shows balance
  in USD; "€18.00 = ~$20 = 2000 credits" displayed at top-up.)
- **Per-spawn dynamic pricing / surge multipliers**. Flat
  per-minute rate based on Fly billing + brnrd platform margin.
- **Free trial of the subscription**. Maybe v-next. At launch,
  the Free tier is the trial — it works end-to-end for up to
  3 projects.
- **Promo / coupon codes** via Stripe's coupon engine. Trivial
  to add when the first growth experiment needs it; not at
  launch.

## Why two billing legs, vs alternatives considered

| Model | Notes |
|-------|-------|
| **Subscription + metered wallet (chosen, pass-4 follow-up wave 3, refined 2026-05-26)** | Each leg matches its cost shape: sub covers fixed platform infra; wallet covers variable compute. Both via Stripe (one account, two products). Subscription gives predictable recurring revenue from serious users; wallet preserves the no-card-on-file metered pattern for compute. EU compliance (Stripe Tax, OSS, SCA) applies identically to both products. |
| Credits-only (the previous shape) | Mixed events + compute in one credit unit. Active users never paid (compute cap not reached by the dispatcher-only use case); platform's fixed cost wasn't covered. Reframe driven by "current pricing won't make this project successful" feedback. |
| Subscription-only (no wallet) | Either over-charges occasional users (flat fee with included compute they don't use) or under-charges heavy users (flat fee for variable cost). Bad for the "you only pay for what you use" framing on compute. |
| Pay-as-you-go card-on-file (subscription replacement) | Forces card-on-file for everyone; surprise bills are the #1 complaint with this model. The wallet's one-shot pattern + opt-in auto-topup preserves the no-surprise property for compute. |
| Prepay annual / quarterly invoicing | B2B path only; doesn't scale to individual users. v-next if enterprise asks. |
| Pure self-hosted (no billing) | Already covered; the OSS path stays free and is explicitly endorsed. Doesn't sustain hosted infra. |

## Open questions (not blocking)

- **Volume discount tiers** (e.g. $100 top-up = 11,000 credits
  instead of 10,000)? Probably yes for $100+; defer to first
  pricing-page iteration.
- **Gift / promo credits** for early adopters / showHN crowd?
  Probably yes; manual grant via admin tool at launch.
- **Per-project caps** in addition to per-account caps? Probably
  yes; one project burning through the wallet shouldn't
  silently kill the others. Trivial to add to the policy
  endpoint.

## Read next

1. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   the pricing model this billing design implements.
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
   "Failover dispatch" for where the spawn-finalize debit hook
   plugs in; and the generalised credential vault (AI creds +
   docker-registry creds in one store).
3. [`design-config-layout.md`](design-config-layout.md) for
   `subscription.tier` as an account-scope read-only key the
   daemon + brnrd both consult.
4. [`decision-cli-shape.md`](decision-cli-shape.md) for the
   `brr brnrd subscription [status | start | cancel | resume |
   portal]` (plus `brr brnrd subscribe` shortcut),
   `brr brnrd topup | balance | autotopup`, and
   `brr brnrd creds` verbs.

## Lineage

- 2026-05-25 — drafted as part of the managed-mode reshape
  pass 4, after the user confirmed a credits-based wallet
  approach via Stripe + HugiMuni SAS + Qonto. Pondering
  provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  (fourth 2026-05-25 reframe breadcrumb).
- 2026-05-25 (pass 4 follow-up) — Stripe integration shape
  expanded into four subsections (legal + payouts, payment
  methods enabled at launch, SCA, VAT compliance, tax
  invoicing, fee table). Explicit list of EU-local payment
  methods to enable day-one (SEPA, iDEAL, Bancontact, EPS,
  Giropay, P24, Apple/Google Pay). OSS scheme registration via
  DGFiP called out as not-optional for cross-EU digital
  services. Fee table added with worked examples for a French
  card user (3.25% overhead) and a German SEPA user (1.3%
  overhead); 27-47% net managed-compute margin after Stripe
  spelled out. Driven by the user's "want to natively support
  European users, never used Stripe before" follow-up.
- 2026-05-25 (pass 4 follow-up — third wave) — **added the
  platform subscription as a second billing leg** alongside the
  existing credit wallet. Reflects the
  [`decision-pricing-shape.md`](decision-pricing-shape.md)
  reframe (subscription for platform + metered credits for
  compute). New "Subscription" section covers Stripe product
  setup, monthly / annual plans, prorated upgrade,
  cancel-at-period-end, dunning grace, subscriber credit grant
  (sized at 500/month in this pass) vs Free's 5/month. New
  `/v1/accounts/subscription` endpoint family; subscription
  events added to the Stripe webhook contract; audit log gains
  subscription-lifecycle entries; ledger gains `sub_bucket`
  to track which grant a debit drew from. "Refund policy"
  split into wallet vs subscription. "Why two billing legs"
  replaces "Why credits" alternatives table. "What we do NOT
  do at launch" clarified for the subscription leg. Customer
  Portal enabled in Stripe Dashboard so subscribers self-manage
  cards / invoices / cancellation without us building UI.
  Initial sketch named the tier "Brnrd Plus" at $9/month.
  Driven by the user's "current pricing won't make this
  project successful — need it to be more coherent, more
  sustainable, still ideally friendly" feedback.
- 2026-05-26 (third-wave follow-up) — **subscription tier
  unnamed**, "Plus" branding dropped from the Stripe product,
  the CLI verb, the docs, and the API state values; tier value
  is now `"subscribed"` (was `"plus"`); plan codes are now
  `"monthly"` / `"annual"` (were `"plus_monthly"` /
  `"plus_annual"`); subscriber-grant sub-bucket renamed from
  `plus_monthly` → `subscriber_monthly`. **Price set to
  $5/month** ($50/year) with **300 credits included** (was
  $9/month + 500 credits in the third-wave draft) — refined
  for community-friendly conversion at the sub-$5
  psychological threshold while still leaving $2/month
  platform-fee headroom over the included compute. **Free
  project cap raised from 1 → 3**; subscriber cap unchanged
  at 10; cancel UX adjusted to "pick which 3 to keep"
  accordingly. CLI verb wrapping the subscription endpoints
  reshaped from `brr brnrd plus [...]` to noun-first
  `brr brnrd subscription [status | start | cancel | resume |
  portal]` + `brr brnrd subscribe` shortcut for the start
  case. Audit-log operation `subscription_upgraded /
  downgraded` renamed to `subscription_plan_switched`
  (monthly↔annual is the only switch that exists; "tier
  upgrade" is just `subscription_started`). Driven by the
  user's "I don't like Plus as a name or verb; $5 a month
  with the credits to make up for it; properly tweaked Free
  might not need the 1-project cap" feedback.
- 2026-05-26 (locking pass — credit buckets + BYO billing).
  **Explicit credit-bucket / per-source expiry policy locked
  in.** Replaced "Monthly credit grants" section with a fuller
  "Credit buckets and expiry policy" section that defines the
  four sub-buckets (`free_monthly` *(later renamed
  `free_signup_bonus`)*, `subscriber_monthly`, `purchased`,
  `promotional`), per-bucket expiry mechanics,
  debit priority (grants first, purchased last, FIFO),
  activity-gated Free monthly grants (only refresh if the
  prior month had any activity — bounds dormant-account
  compute cost at zero), the "purchased never expires"
  guarantee, and the account-dormancy policy that bounds the
  liability tail (24mo pause / 36mo prompt / deletion only on
  explicit request or GDPR). Sub-bucket name `paid` →
  `purchased` everywhere (audit ops, debit-spawn `sub_bucket`,
  refund op). New audit ops: `grant_promotional` /
  `expire_promotional` (future-proofing),
  `account_marked_dormant` / `account_reactivated`,
  `spawn_byo` (BYO subscribers, no wallet debit). Refund
  policy clarified: pro-rata within 30 days for `purchased`;
  grants never refundable in cash; beyond 30d purchased
  credits stay valid forever but aren't cash-refundable.
  Dashboard never says "credits expired"; says "monthly
  allowance refreshes on &lt;date&gt;." **BYO-compute billing
  implications added**: subscribers who BYO contribute pure
  subscription revenue, wallet bypassed entirely; mixed-mode
  (BYO + managed) hits the wallet only on managed spawns;
  audit log distinguishes via `spawn_byo`. Scope expanded to
  cover the bucket policy + BYO billing impact. Driven by
  the user's "credit expiry shape + holistic credit-issuing
  optics" + "BYO everything for paying customers, no BYO for
  Free" framing.
- 2026-05-26 (locking pass II — Free signup bonus, project
  cap unlock, deferred-revenue accounting framing).
  **`free_monthly` bucket renamed to `free_signup_bonus`**
  with one-time grant on Free account creation (10 credits)
  + 30-day expiry (OR on full consumption). Activity-gating
  logic removed entirely (no longer needed). Audit ops
  renamed `grant_free_monthly` / `expire_free_monthly` →
  `grant_free_signup_bonus` / `expire_free_signup_bonus`
  (new `reason` field on the expiry op: `time` / `consumed`).
  Bucket table + debit priority + balance UI examples
  updated. New "Cumulative purchase tracking and the
  subscriber project cap unlock" section: two new monotonic
  account-state counters (`cumulative_purchased_credits_lifetime`
  + `cumulative_purchased_usd_lifetime`) + derived flag
  `project_cap_unlocked` (set on the topup that crosses $10
  threshold; permanent; survives cancel + re-subscribe).
  Effective project-cap function returns 3 (Free) / 25
  (Subscribed unlocked=false) / unlimited (Subscribed
  unlocked=true). New audit op `project_cap_unlocked`. New
  "Deferred-revenue accounting" section (for the implementer
  + accountant): purchased credits + subscription fees are
  deferred revenue under French GAAP / IFRS; grants are NOT
  deferred revenue (they're operational COGS); standard
  double-entry shape with worked examples; Stripe Revenue
  Recognition automates the daily proration on subscriptions
  + the per-debit recognition on purchased credits; HugiMuni
  SAS chart-of-accounts sketch (Bank / Deferred revenue /
  Revenue subscriptions / Revenue compute / Compute cost
  Fly / Compute cost promotional); bank-account separation
  (operating vs reserve) called out as treasury hygiene for
  ≥€10K MRR, not a legal requirement at launch; no legal
  segregation needed for SaaS prepaid balances in France.
  Driven by the user's "we maybe need to implement project
  ownership" + "could we have a separate bank account for
  the promise" + "start a bit stingier and relax as we go"
  + "throttling is a good idea, like it" framing.
- 2026-05-26 (locking pass III — env knobs + stale claims).
  **New "Launch defaults + tunable knobs" section** ties
  every launch-shape number this page implements to a
  `BRNRD_*` env var (Free signup-bonus credits + expiry,
  project-cap unlock USD, subscriber monthly grant size,
  supporter cohort size, both per-cohort prices, dormancy
  timings). Canonical list lives in
  [`decision-pricing-shape.md`](decision-pricing-shape.md) §
  "Launch-tunable knobs"; this page wires each value to the
  ledger / Stripe-product / dormancy-state-machine code that
  reads it. **Stale "(10 vs 3 on Free)" project-cap mention
  in the opening "Subscription" bullet** updated to the
  locked tiered shape "25 (unlimited after $10 of cumulative
  top-ups)". Driven by the user's "lock these as defaults +
  config knobs" MR-review feedback.
- 2026-05-26 (locking pass IV — overdraft envelope). New
  account setting `max_overdraft_credits` (signed integer;
  default 0; per-tier ceiling enforced server-side). New
  env knob `BRNRD_SUBSCRIBER_MAX_OVERDRAFT_CREDITS`
  (default 500 credits = $5) caps the per-account setting
  for Subscribed; Free is locked at 0 (no overdraft).
  Spawn-start gate reframed from "balance ≥ estimated
  cost + safety margin" to **TWO conditions**:
  `current_balance >= 0` AND
  `estimated_spawn_cost <= current_balance + max_overdraft_credits`.
  The first condition gates by the user's "I would change
  this" call on the prior `>= 1` framing — strictly
  non-negative is enough; the second condition bounds
  the spawn's worst-case landing inside the overdraft
  envelope. Once started, the spawn runs to completion;
  actual cost may push balance as far negative as the
  envelope allows; per-spawn timeout + cost-cap kill
  (existing) bound the blast radius of any single spawn.
  Next spawn requires `current_balance >= 0` again →
  top-up clears the negative + restores headroom; no
  interest, no penalty fees. Three new audit ops:
  `overdraft_settings_changed`, `overdraft_consumed`,
  `overdraft_cleared`. Dashboard shows signed balance +
  envelope-used gauge when negative; gate footer on the
  spawn that crossed zero. Driven by the user's "we'll
  need to allow people to go below credits ... default
  would be set to 0, and if you balance is 0 and above -
  you can run a cloud runner, but it will make your
  balance negative for that last runner's price ... for
  like higher tier paying cusotomer's we'd allow to
  configure it within ok-for-us limits." Status promoted
  from "proposed" to "accepted."
