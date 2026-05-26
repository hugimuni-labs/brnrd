# Design: billing — subscription + credit wallet on brnrd

**Status: proposed, not yet accepted; reshaped 2026-05-26
(third-wave follow-up) — added the platform subscription as a
second billing leg alongside the existing credit wallet, after
the credits-only shape proved self-defeating for sustainability.
Subscription tier deliberately has no marketing name; CLI verb
is `brr brnrd subscription`. Refined 2026-05-26 (locking pass)
with the explicit credit-bucket / per-source expiry policy,
account-dormancy bound on purchased credits, and the
subscriber-only BYO-compute billing implication (subscribers
who BYO contribute pure subscription revenue with zero compute
margin).** Two billing legs that back the pricing model in
[`decision-pricing-shape.md`](decision-pricing-shape.md):

1. **Subscription** — $5/month recurring Stripe subscription
   ($50/year annual alternative). Unlocks bigger project
   headroom (10 vs 3 on Free), generous event + compute
   included grants, full dashboard, 90-day audit, email
   support. Reshaped on 2026-05-25 (pass-4 follow-up, third
   wave) after the credits-only model proved self-defeating
   for sustainability; reshaped again 2026-05-26 to drop the
   "Plus" branding and to land on $5/month + 300 included
   credits.
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
  expiry policy** (`free_monthly` / `subscriber_monthly` /
  `purchased` / `promotional`), debit priority, activity-gated
  Free grants, account-dormancy bound on the
  purchased-never-expires guarantee.
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
| Account balance UI | Always shows split: "200 purchased + 230 subscriber this month = 430 available" or "4 Free this month = 4 available" |
| Bucket model | **Four sub-buckets at launch, with per-source expiry**: `free_monthly`, `subscriber_monthly`, `purchased`, plus `promotional` reserved for future use. Full table + debit priority + expiry policy in the next section. |
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

## Zero-balance UX

When a spawn would be dispatched but the wallet is at or near zero:

```
Event arrives.
Dispatcher checks wallet.estimated_balance_after_spawn():
  if balance ≥ est_cost + safety_margin:
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

Auto-topup design:

- Off by default; user opts in.
- User picks: trigger threshold (default: when balance < 100
  credits) + top-up amount (default: $20).
- Saved payment method required; **only when auto-topup is
  enabled** does brnrd hold a Stripe customer + card-on-file.
  Without auto-topup, no card is stored.
- Per-day auto-topup cap (default $50, configurable) to prevent
  runaway.
- Every auto-topup hits the audit log + gate notify.

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
- **`free_monthly` / `subscriber_monthly` / `promotional`
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
| `free_monthly` | Account creation (prorated) + every cycle for Free accounts (activity-gated) | End of current cycle | **No** | No (was never charged for) | `grant_free_monthly` / `expire_free_monthly` |
| `subscriber_monthly` | Subscription start (prorated) + every renewal | End of current billing cycle | **No** | No (included in the sub) | `grant_subscriber_monthly` / `expire_subscriber_monthly` |
| `purchased` | Stripe Checkout top-up confirmed | **Never** (account-dormancy bounded; see "Account dormancy" below) | Yes | Pro-rata within 30 days (see Refund policy) | `topup` / `refund_purchased` |
| `promotional` *(future-proofing; not used at launch)* | Signup bonus / referral / support-issued goodwill | Per-grant `expires_at` (typically 30-90 days) | No | No | `grant_promotional` / `expire_promotional` |

### Debit priority (FIFO within bucket, expiry-aware across buckets)

```
On debit(amount):
  1. Drain free_monthly until empty or amount satisfied
  2. Drain subscriber_monthly until empty or amount satisfied
  3. Drain promotional grants in soonest-expiring-first order
  4. Drain purchased grants FIFO (oldest top-up first)
  5. If still short, the spawn's final debit is allowed to leave
     purchased slightly negative (≤ one spawn's hard cap);
     enqueue subsequent events until top-up.
```

This means a user's `purchased` balance is **always preserved
last**. The monthly allowance gets consumed first, exactly
matching the user's intuition ("my grant runs out, then I dip
into what I paid for"). Promotional credits with near-term
expiry drain ahead of long-lived purchases. The implementation
is one priority-ordered loop over bucket records, ~30 lines.

### Per-bucket expiry mechanics

**`free_monthly` and `subscriber_monthly` — use-it-or-lose-it
at cycle boundary.**

```
On the 1st of each UTC month (Free accounts):
  for each Free account:
    if account had any activity in the prior month:
      expire any unused free_monthly balance
      grant 5 free_monthly credits
    else:
      account is "dormant Free"; no new grant issued;
      existing free_monthly balance also expires
      (activity-gated; resumes on next active month)

On subscription renewal (subscribed accounts, per Stripe's renewal):
  expire any unused subscriber_monthly balance
  grant 300 subscriber_monthly credits

On subscription cancel-at-period-end (period boundary):
  expire any unused subscriber_monthly balance
  flip tier=free; next month grants free_monthly under
  activity-gating
```

The **activity gate on Free** is the small but important lever:
5 credits / month × 100,000 long-tail one-time-signup accounts
would be $5,000 / month of pointless compute cost on the
liability side without it. Activity = any event processed in the
prior month OR any dashboard login OR any CLI command that hit
the backend. Active Free users never notice the gate; dormant
ones simply don't accumulate a grant they're not using.
Subscriber grants refresh unconditionally — the subscription
itself is the activity signal.

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

For Free users:

```
4 credits available
    4  this month (Free, refreshes Jun 1)
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
| `grant_free_monthly` | account_id, ts, amount_credits, month, activity_gate_state (`gated` / `passed`) |
| `expire_free_monthly` | account_id, ts, amount_credits, month |
| `grant_promotional` | account_id, ts, amount_credits, expires_at, campaign_id |
| `expire_promotional` | account_id, ts, amount_credits, campaign_id |
| `topup` | account_id, ts, amount_credits, stripe_payment_intent_id, source (manual / auto), bucket=`purchased` |
| `debit_spawn` | account_id, ts, amount_credits, spawn_id, project_id, sub_bucket (`free_monthly` / `subscriber_monthly` / `promotional` / `purchased`), est_vs_actual_delta |
| `spawn_byo` *(BYO subscribers; no wallet debit)* | account_id, ts, spawn_id, project_id, provider (`fly` / future), scheduled_machine_id, estimated_cost_usd |
| `refund_purchased` | account_id, ts, amount_credits, reason (user_request / brnrd_failure / dormancy_deletion), stripe_refund_id |
| `refund_grant_brnrd_failure` | account_id, ts, amount_credits, spawn_id, sub_bucket |
| `auto_topup_enabled` / `auto_topup_disabled` | account_id, ts |
| `payment_method_added` / `removed` | account_id, ts, stripe_payment_method_id |
| `account_marked_dormant` | account_id, ts, last_activity_at, dormant_state (`dormant` / `dormant_long`) |
| `account_reactivated` | account_id, ts, prior_dormant_state |

User-visible via `brr brnrd balance` (current totals + sub
status) and the dashboard's Cost / Audit view (full ledger).

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
| `GET` | `/v1/accounts/wallet` | Current balance with sub-bucket split (`paid + subscriber_monthly + free_monthly`), per-month spend stats |
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
  four sub-buckets (`free_monthly`, `subscriber_monthly`,
  `purchased`, `promotional`), per-bucket expiry mechanics,
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
