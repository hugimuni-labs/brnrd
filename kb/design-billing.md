# Design: billing — subscription + credit wallet on brnrd

**Status: accepted 2026-05-26** (locked in PR #40 MR review).

Billing has two launch legs, both implemented through Stripe:

1. **Subscription** for hosted platform cost: recurring monthly or annual
   Stripe subscription, self-managed through the Stripe Customer Portal.
   It backs the Subscribed tier in
   [`decision-pricing-shape.md`](decision-pricing-shape.md): $5/month for
   the supporter cohort, then $7/month for the public cohort; 300 managed
   compute credits/month included; higher project/event/audit/dashboard
   limits.
2. **Credit wallet** for hosted compute cost: one-shot Stripe Checkout
   top-ups at $0.01/credit, no card-on-file unless the user opts into
   auto-topup. Spawns debit the wallet at finalize.

The original split matched the two cost shapes known on 2026-05-26:
the subscription covers fixed hosted infrastructure (bots, dispatcher,
dashboard, postgres, support), and the wallet covers variable cloud
compute. **Partial supersession, 2026-06-15:** [`decision-llm-relay.md`](decision-llm-relay.md)
adds a third wallet-debited variable cost: when brr uses brnrd-owned LLM
capacity, the wallet is charged provider cost plus a transparent relay service
fee (10-15%), with provider cost and fee as separate line items. BYO AI
credentials still stay free/default; only relay usage enters brnrd billing.

The accepted overdraft envelope is part of the wallet contract:
spawn-start gate is `current_balance >= 0` and
`estimated_spawn_cost <= current_balance + max_overdraft_credits`.
`max_overdraft_credits` defaults to 0; Subscribed accounts can raise it
within `BRNRD_SUBSCRIBER_MAX_OVERDRAFT_CREDITS` (default 500 credits,
$5). Free cannot raise it.

## Scope

In scope:

- Stripe subscription lifecycle, prices, proration, dunning, Customer
  Portal, and account-tier transitions.
- Wallet top-ups, credit buckets, debit priority, zero-balance and
  overdraft behavior, refunds, auto-topup, and audit entries.
- LLM relay wallet debits at provider cost plus transparent relay service fee,
  as accepted in [`decision-llm-relay.md`](decision-llm-relay.md). The exact
  relay fee percentage remains a follow-up here.
- Per-source credit expiry:
  `free_signup_bonus`, `subscriber_monthly`, `purchased`, and future
  `promotional`.
- BYO-compute wallet bypass for subscribers.
- Stripe integration for HugiMuni SAS / Qonto / Stripe Tax / EU payment
  methods / VAT and SCA.
- Deferred-revenue accounting frame for purchased credits and
  subscriptions.

Out of scope:

- Dispatcher/spawn protocol details
  ([`design-brnrd-protocol.md`](design-brnrd-protocol.md)).
- Tier policy and sustainability math
  ([`decision-pricing-shape.md`](decision-pricing-shape.md)).
- Credential-vault internals
  ([`design-brnrd-protocol.md`](design-brnrd-protocol.md)).
- Account-dormancy service wiring beyond its effect on the wallet.
- Team / per-seat billing, invoicing, crypto, and non-Stripe payment
  rails.

## Subscription

The subscription leg is the platform-coverage leg.

| Concept | Contract |
|---------|----------|
| Stripe product | One "Brnrd Subscription" `Product`; separate recurring `Price` IDs for supporter monthly/yearly and public monthly/yearly so grandfathering is native. |
| Prices | Supporter: $5/month or $50/year. Public: $7/month or $70/year. Same features. |
| Cohort cutoff | New checkouts switch to public price after 200 supporters or 12 months from public launch, whichever comes first. Existing subscribers stay on their original `Price`. |
| Cadence | Monthly or annual; Stripe handles renewals, invoices, SCA, and dunning. |
| Grants | On subscribe and renewal, grant 300 `subscriber_monthly` credits, prorated on mid-cycle start. Unused grant expires at period end. |
| Account caps | `tier=subscribed`, 25 projects unless `project_cap_unlocked`, 10K events/month, 90-day audit retention, full dashboard. |
| Upgrade | Stripe Checkout creates subscription; webhook flips tier and grants prorated credits immediately. |
| Cancel | Cancel-at-period-end via Customer Portal or API. Access continues until the boundary, then tier returns to Free and caps apply. |
| Failed renewal | Stripe dunning grace keeps subscribed behavior while `past_due`; final failure returns account to Free. |

The CLI uses `brr brnrd subscription [status | start | cancel | resume |
portal]`, with `brr brnrd subscribe` as a shortcut for `subscription
start`.

## Wallet model

The wallet leg covers managed compute. One credit is $0.01. UI presets:
$5, $20, $50, $100, and custom amounts from $5 to $500 at launch.

The account balance is a sum of bucket rows, always shown with source
breakdown:

| Bucket | Source | Expiry | Refundable |
|--------|--------|--------|------------|
| `free_signup_bonus` | 10 credits on Free account creation | 30 days or full consumption | no |
| `subscriber_monthly` | 300 credits on subscription start/renewal | current billing period | no |
| `purchased` | one-shot Stripe top-up | no expiry; dormancy-bounded | pro-rata within 30 days |
| `promotional` | future campaigns/support grants | per grant | no |

Debit order preserves paid balance: grants first, soonest-expiring
promotions before longer-lived grants, then `purchased` FIFO.

## Top-up flow

Top-ups use Stripe Checkout, not Stripe Elements:

1. User runs `brr brnrd topup 20` or clicks a dashboard top-up action.
2. brnrd creates a one-shot Checkout session for the wallet top-up
   product.
3. User pays in Stripe; no card is stored unless auto-topup is enabled.
4. Verified webhook credits the `purchased` bucket idempotently by Stripe
   `payment_intent.id`.
5. Audit log records `topup`; gate/dashboard notify the user.

Checkout accepts card and the enabled local methods Stripe renders for
the customer's location. Failed or abandoned Checkout sessions do not
touch the ledger.

## Debit mechanics

Managed spawns debit at finalize, when actual cost is known:

1. Spawn starts after policy and wallet checks pass.
2. Spawn runs to success, failure, cancellation, or timeout.
3. Finalizer computes actual cloud cost plus platform margin and converts
   to credits with a ceiling.
4. Ledger appends `debit_spawn` rows against the bucket(s) drained.

Failure policy:

| Outcome | Debit |
|---------|-------|
| success | full actual cost |
| runner/agent failed after compute ran | full actual cost |
| cloud boot failed before runner start | no debit; ops failure |
| user cancellation | cost until cancellation |
| timeout/cost-cap kill | cost until enforced cap |

Finalize-time debit avoids reserve/true-up complexity. The overdraft
envelope bounds the final spawn that crosses zero, and the next spawn
requires the account to be non-negative again.

## BYO-compute spawns — wallet bypass

Subscribers who BYO a cloud provider run spawns in their own cloud
account with credentials from the vault. Those spawns bypass the wallet:
no `debit_spawn`, no credit consumption, and no brnrd compute markup.

Audit uses `spawn_byo` with metadata such as account, project, provider,
spawn ID, scheduled machine ID, and estimated cloud cost. The dashboard
can show "ran in your Fly account" without billing the user.

Mixed-mode subscribers use both paths: BYO Fly might bypass the wallet,
while a later managed Modal env would debit the wallet. Free users cannot
BYO, so the wallet is their only hosted-compute surface.

## Zero-balance UX (and the overdraft envelope)

The spawn-start gate is:

```python
current_balance >= 0
and estimated_spawn_cost <= current_balance + max_overdraft_credits
```

The first condition prevents a user who is already negative from starting
another spawn. The second prevents dispatching a spawn whose estimate
already exceeds the balance plus configured envelope.

Per-tier envelope:

| Tier | Default | User-configurable ceiling |
|------|---------|---------------------------|
| Free | 0 | locked at 0 |
| Subscribed | 0 | `BRNRD_SUBSCRIBER_MAX_OVERDRAFT_CREDITS`, default 500 |
| Future higher tiers | TBD | TBD |

If the check fails and auto-topup is enabled, brnrd can top up first and
then dispatch. Otherwise the event queues for up to 24 hours, the gate
notifies the user, and dispatch resumes within roughly 5 minutes of a
successful top-up.

If actual cost exceeds the estimate, the running spawn still finishes.
Per-spawn timeout and cost-cap policy bound the blast radius. A top-up
that lands while negative first clears the negative balance, then adds the
remainder to `purchased`. There is no interest or penalty fee.

Dashboard and gate responses must surface negative balance explicitly:
signed balance, envelope used, and a top-up action. Silent overdraft is
not allowed.

Auto-topup is opt-in. The user chooses a threshold and amount, must have a
saved Stripe payment method, and is protected by a per-day cap. Every
auto-topup emits an audit entry and user notification.

## Refund policy

Wallet:

- Unused `purchased` credits are refundable pro-rata within 30 days of
  purchase.
- Used credits are not refundable unless the spawn failure was brnrd's
  fault.
- Grant buckets (`free_signup_bonus`, `subscriber_monthly`,
  `promotional`) are not cash-refundable.
- Account closure refunds unused `purchased` balance where Stripe can
  refund the original payment; older balances use a manual refund path.

Subscription:

- Cancel-anytime means cancel-at-period-end; access remains until the
  paid period ends.
- Mid-period monthly refunds are discretionary for genuine mistakes.
- Annual cancellation refunds unused months pro-rata.
- Unused `subscriber_monthly` credits expire when the subscription period
  ends.

## Credit buckets and expiry policy

Bucket rows are append-only ledger resources with source, expiry, refund
policy, and audit operation. The ledger, not the UI, is source of truth.

```python
def debit(amount):
    drain("free_signup_bonus", amount)
    drain("subscriber_monthly", amount)
    drain_promotional_soonest_expiring(amount)
    drain_purchased_fifo(amount)
```

`free_signup_bonus` is granted once on account creation and never again on
tier transitions. `subscriber_monthly` refreshes each billing cycle and
does not roll over. `purchased` rows have no `expires_at`; account
dormancy pauses services, not balances. `promotional` exists for future
support/campaign grants and carries an explicit expiry per grant.

### Dashboard language

Dashboard language frames grants as allowances:

- "Your monthly allowance refreshes on <date>."
- "Your monthly allowance reset on <date>, new balance: 300 credits."
- "Purchased balance: 420 credits ($4.20 prepaid)."

It should not say "your paid credits expired" because paid credits do not
expire, and it should avoid making grant expiry feel like a surprise.

### Cumulative purchase tracking and the subscriber project cap unlock

The billing ledger feeds the project-cap unlock:

| Field | Source | Behavior |
|-------|--------|----------|
| `cumulative_purchased_credits_lifetime` | successful `topup` audit ops | monotonic |
| `cumulative_purchased_usd_lifetime` | credits / 100 | monotonic derived value |
| `project_cap_unlocked` | crosses $10 cumulative purchases | set once, never cleared |

Refunds do not decrement the cumulative counter. The unlock is a trust
signal about demonstrated usage, not refundable store credit.

### Account dormancy and the "purchased never expires" tail

Dormancy bounds operational cost without expiring user-paid balance:

| State | Trigger | Services | `purchased` balance |
|-------|---------|----------|---------------------|
| `active` | default | on | spendable |
| `dormant` | 24 months inactive | dispatch paused | preserved |
| `dormant_long` | 36 months inactive | paused + dashboard prompt | preserved |
| `deleted` | explicit deletion / GDPR | account removed | refunded where possible, then deleted |

Dormancy is enforced by account-state services. The wallet ledger never
expires `purchased` rows.

## Audit log entries

Every billing operation writes metadata-only audit rows:

- subscription: `subscription_started`, `subscription_renewed`,
  `subscription_canceled_at_period_end`, `subscription_canceled_immediate`,
  `subscription_plan_switched`;
- grants and expiry: `grant_subscriber_monthly`,
  `expire_subscriber_monthly`, `grant_free_signup_bonus`,
  `expire_free_signup_bonus`, `grant_promotional`, `expire_promotional`;
- wallet: `topup`, `debit_spawn`, `refund_purchased`,
  `refund_grant_brnrd_failure`;
- BYO: `spawn_byo`;
- payment method / automation: `auto_topup_enabled`,
  `auto_topup_disabled`, `payment_method_added`,
  `payment_method_removed`;
- account state: `account_marked_dormant`, `account_reactivated`;
- project unlock: `project_cap_unlocked`;
- overdraft: `overdraft_settings_changed`, `overdraft_consumed`,
  `overdraft_cleared`.

The user sees current totals and signed balance via `brr brnrd balance`
and the dashboard Cost / Audit view.

## API surface (brnrd-side)

Subscription endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/accounts/subscription` | current tier, plan, period, cancel state, recent invoices |
| `POST` | `/v1/accounts/subscription/checkout` | create subscription Checkout session |
| `POST` | `/v1/accounts/subscription/cancel` | cancel at period end |
| `POST` | `/v1/accounts/subscription/resume` | clear pending cancellation |
| `POST` | `/v1/accounts/subscription/portal` | create Customer Portal session |

Wallet endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/accounts/wallet` | balances by bucket, spend stats, cumulative purchase unlock state |
| `POST` | `/v1/accounts/wallet/checkout` | create one-shot top-up Checkout session |
| `POST` | `/v1/accounts/wallet/autotopup` | enable/configure auto-topup |
| `DELETE` | `/v1/accounts/wallet/autotopup` | disable auto-topup |
| `GET` | `/v1/accounts/wallet/ledger` | paginated/filterable ledger |
| `POST` | `/v1/accounts/wallet/refund` | request unused purchased-credit refund |

Shared:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/internal/stripe/webhook` | signed Stripe webhook for subscription and one-shot events |

## Stripe integration shape

Legal / account setup:

- Merchant: HugiMuni SAS, Stripe France account.
- Payouts: Qonto IBAN, normal Stripe payout schedule.
- One Stripe account holds both subscription and wallet products.

Payment methods at launch:

- cards, Apple Pay, Google Pay;
- SEPA Direct Debit;
- iDEAL, Bancontact, EPS, Giropay, P24 where Stripe renders them.

Stripe Checkout handles PCI scope and SCA / 3D Secure. Stripe Tax handles
VAT/sales-tax calculation. EU cross-border digital-service sales require
OSS registration through DGFiP; Stripe Tax exports the OSS report. Stripe
invoices provide the invoice PDF and tax fields; brnrd links to Stripe's
hosted invoice rather than generating PDFs.

2026 EU fee assumptions used in pricing math:

| Method | Rate |
|--------|------|
| EEA cards | 1.5% + EUR0.25 |
| UK cards | 2.5% + EUR0.25 |
| non-EEA cards | 3.25% + EUR0.25 |
| SEPA Direct Debit | 0.8%, capped EUR5 |
| iDEAL / Bancontact / EPS / Giropay / P24 | EUR0.80 |
| Stripe Tax | 0.5% on taxed transactions |
| FX conversion | +1% when customer currency differs from settlement |

Net managed-compute margin must absorb these payment fees; headline
compute margin should not be read as post-fee margin.

Brnrd-side Stripe configuration:

- "Brnrd Subscription" recurring product with supporter and public
  monthly/yearly `Price` IDs.
- "Brnrd Wallet Top-up" one-shot product with no `setup_future_usage`.
- Stripe Customer object created on first subscription or auto-topup.
- Customer Portal enabled for cards, invoices, plan switch, and cancel.
- Webhook endpoint verifies signatures and is idempotent on Stripe event
  IDs.
- Test mode uses `BRNRD_STRIPE_API_KEY` and
  `BRNRD_STRIPE_WEBHOOK_SECRET` with Stripe's test cards and SCA cases.

## Deferred-revenue accounting (for the implementer + accountant)

Purchased credits and subscription fees are deferred revenue under French
GAAP / IFRS: cash received before service delivery is a liability until
credits are consumed or subscription days elapse.

Purchased credits:

```text
payment succeeds: bank +, deferred revenue +
spawn debits purchased credits: deferred revenue -, revenue +
refund unused purchase: deferred revenue -, bank -
```

Subscription:

```text
invoice paid: bank +, deferred revenue +
each day of period: deferred revenue -, subscription revenue +
```

Grant buckets are not deferred revenue because no cash was received for
them. When consumed, they are compute cost of goods covered by the
subscription / marketing budget.

At launch scale one Qonto operating account is sufficient. A separate
reserve account becomes treasury hygiene once MRR or deferred-revenue
liability is material, not a legal launch requirement for SaaS prepaid
balances in France.

## Launch defaults + tunable knobs

Pricing policy is canonical in
[`decision-pricing-shape.md`](decision-pricing-shape.md). The billing
service reads these env-backed defaults at process start:

| Env var | Default |
|---------|---------|
| `BRNRD_FREE_SIGNUP_BONUS_CREDITS` | 10 |
| `BRNRD_FREE_SIGNUP_BONUS_EXPIRY_DAYS` | 30 |
| `BRNRD_PROJECT_CAP_UNLOCK_USD` | 10 |
| `BRNRD_SUBSCRIBER_MONTHLY_CREDITS` | 300 |
| `BRNRD_SUPPORTER_COHORT_SIZE` | 200 |
| `BRNRD_SUBSCRIBER_PRICE_SUPPORTER_USD` | 5 |
| `BRNRD_SUBSCRIBER_PRICE_PUBLIC_USD` | 7 |
| `BRNRD_DORMANCY_PAUSE_MONTHS` | 24 |
| `BRNRD_DORMANCY_PROMPT_MONTHS` | 36 |
| `BRNRD_SUBSCRIBER_MAX_OVERDRAFT_CREDITS` | 500 |

Supporter and public prices remain distinct Stripe `Price` IDs; changing
env defaults does not migrate existing subscriptions.

## What we do NOT do at launch

- Card-on-file for wallet by default.
- Per-team / per-seat billing.
- Invoicing / NET-30.
- Crypto or non-Stripe rails.
- Non-USD internal wallet ledger.
- Per-spawn surge pricing.
- Subscription free trial; Free tier is the trial.
- Stripe coupon/promo-code machinery, unless the first growth experiment
  needs it.

## Why two billing legs, vs alternatives considered

| Model | Decision |
|-------|----------|
| Subscription + wallet | chosen; fixed cost and variable cost are billed separately |
| Credits-only | rejected; fixed platform cost stayed unfunded |
| Subscription-only | rejected; either overcharges light compute users or undercharges heavy users |
| Pay-as-you-go card-on-file | rejected; surprise-bill model conflicts with launch trust posture |
| Invoiced prepay | deferred to enterprise/team demand |
| Pure self-hosted | remains supported and free, but does not sustain hosted brnrd |

## Open questions (not blocking)

- Volume discounts on large top-ups, probably at $100+.
- Manual gift / promotional credits for launch campaigns.
- Optional per-project cost caps so one project cannot drain a shared
  account wallet.

## Read next

1. [`decision-pricing-shape.md`](decision-pricing-shape.md) for the tier
   and pricing policy this page implements.
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) for spawn
   finalize hooks, subscription endpoints, and BYO dispatch.
3. [`design-config-layout.md`](design-config-layout.md) for account-scope
   `subscription.tier`.
4. [`decision-cli-shape.md`](decision-cli-shape.md) for the `brr brnrd`
   billing verbs.

## Lineage

- 2026-05-25: drafted as credit-wallet mechanics after the managed-mode
  pricing discussion adopted Stripe + HugiMuni SAS + Qonto.
- 2026-05-25: expanded with Stripe Europe details, SCA/VAT/OSS, local EU
  payment methods, fee assumptions, and invoice handling.
- 2026-05-25/26: subscription became a second billing leg after the
  credits-only model proved unable to fund hosted platform cost; "Plus"
  and $9/500-credit draft were replaced by unnamed Subscribed at
  $5/300 credits.
- 2026-05-26: bucketed ledger, Free signup bonus, $10 project unlock,
  BYO wallet bypass, deferred-revenue framing, launch env knobs, and
  overdraft envelope were locked.
- 2026-06-12: compacted from accumulated proposal history into this
  state-first implementation design; prior detail remains in git history.
