# Design: billing — credits wallet on brnrd

**Status: proposed, not yet accepted.** The wallet, top-up,
debit, and refund mechanics that back the pricing model in
[`decision-pricing-shape.md`](decision-pricing-shape.md). One
load-bearing decision: brnrd uses a **credit wallet** (top-up,
no card on file by default), not a card-on-file subscription.
Credits are the standard for usage-metered services (OpenAI,
Anthropic, AWS, Mailgun, Twilio) and align with the data-
minimization pitch (no recurring identity-mapping; one-shot
purchases only). Subscription billing is a v-next concern if a
team tier ships.

## Scope

In scope:

- Wallet model (credit unit, top-up amounts, free-tier credit
  grant, paid-credit non-expiry).
- Debit mechanics (when does a spawn debit; what happens on
  partial / failed spawns).
- Top-up flow (Stripe Checkout one-shot purchases; no card on
  file by default; opt-in auto-topup on low balance).
- Zero-balance UX (what happens when a spawn would overshoot
  the balance).
- Refund policy.
- Free-tier credit grant semantics.
- Stripe integration shape from the brnrd side (Stripe France
  for HugiMuni SAS; payouts to Qonto; Stripe Tax for EU VAT).
- Audit log entries for every wallet operation.

Out of scope, explicitly:

- The dispatcher / spawn protocol itself
  ([`design-brnrd-protocol.md`](design-brnrd-protocol.md)).
- Pricing strategy / tier shape
  ([`decision-pricing-shape.md`](decision-pricing-shape.md)).
- Card-on-file subscriptions (v-next if a team / SLA tier
  ships).
- Crypto, invoicing (per-invoice prepay), or any non-Stripe
  payment rail at launch.

## Wallet model

| Concept | Value |
|---------|-------|
| Credit unit | **1 credit = $0.01** (10,000 credits = $100). Simple math, no exchange-rate weirdness on internal accounting |
| Top-up amounts (UI presets) | $5, $20, $50, $100, custom (min $5, max $500/single transaction at launch) |
| Currency at launch | USD on the wallet ledger; EUR / GBP / etc. accepted via Stripe at purchase time (Stripe converts; we hold credit-USD on the ledger) |
| Paid credit expiry | **Never** — paid credits don't expire; they're the user's purchased capacity |
| Free-tier credit grant | N free credits granted monthly per account; expire end-of-month if unused (don't accumulate) |
| Free-tier sizing | Per [`decision-pricing-shape.md`](decision-pricing-shape.md): 1000 events/month + 100 failover spawns/month. Translated to credits: ~$2.80/month worst-case (100 × ~$0.028/spawn) ≈ **300 free credits/month** (round up; covers spawn variance) |
| Debit order | Free credits drawn first; paid credits drawn only when free is exhausted |
| Account balance UI | Always shows split: "100 paid + 250 free this month = 350 available" |

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

Documented on the pricing page:

- **Unused paid credits**: refundable pro-rata within 30 days of
  purchase. User requests via dashboard or email; refund
  processed within 5 business days. Stripe handles the actual
  refund; brnrd debits the ledger correspondingly.
- **Used credits**: not refundable (the compute was consumed;
  brnrd paid Fly already).
- **Spawn failures attributable to brnrd** (Fly-side outage,
  brnrd-side bug): credit auto-refunded; user notified.
- **Spawn failures attributable to the user's task** (agent
  errored, code didn't compile): not refunded; the spawn ran.
- **Free credits** are not refundable (they were never paid for).
- **Account closure**: paid balance refunded in full within 30
  days; user receives final ledger statement.

The 30-day window is generous enough for "I made a mistake"
recovery and short enough that brnrd's cash position stays
predictable.

## Free-tier credit grant

Free credits are granted at the start of each calendar month
(UTC):

```
On the 1st of each month:
  for each account with free-tier enabled:
    grant 300 free credits
    expire any unused free credits from the previous month
```

Sized to cover the pricing-page commitment of 100 failover
spawns/month at the worst-case Fly cost. If users routinely hit
the cap, revisit the grant size (or the pricing cap).

New accounts grant 300 credits on signup, pro-rated for the
remainder of the calendar month if joining mid-month (e.g.
joining on the 15th = 150 credits for that month, full 300 the
next month). Removes a free-month gaming surface.

## Audit log entries

Every wallet operation appears in `account_audit`. Metadata-only
per the data-minimization principle:

| Operation | Fields |
|-----------|--------|
| `topup` | account_id, ts, amount_credits, stripe_payment_intent_id, source (manual / auto) |
| `debit_spawn` | account_id, ts, amount_credits, spawn_id, project_id, est_vs_actual_delta |
| `refund_paid` | account_id, ts, amount_credits, reason (user_request / brnrd_failure), stripe_refund_id |
| `refund_free_brnrd_failure` | account_id, ts, amount_credits, spawn_id |
| `grant_monthly_free` | account_id, ts, amount_credits, month |
| `expire_monthly_free` | account_id, ts, amount_credits, month |
| `auto_topup_enabled` / `auto_topup_disabled` | account_id, ts |
| `payment_method_added` / `removed` | account_id, ts, stripe_payment_method_id |

User-visible via `brr brnrd balance` (current totals) and the
dashboard's Cost / Audit view (full ledger).

## API surface (brnrd-side)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/accounts/wallet` | Current balance (paid + free split), per-month spend stats |
| `POST` | `/v1/accounts/wallet/checkout` | Create Stripe Checkout session; returns checkout_url |
| `POST` | `/v1/accounts/wallet/autotopup` | Enable/configure auto-topup (requires saved payment method) |
| `DELETE` | `/v1/accounts/wallet/autotopup` | Disable auto-topup |
| `GET` | `/v1/accounts/wallet/ledger` | Paginated ledger (filterable by op, date range) |
| `POST` | `/v1/accounts/wallet/refund` | Request refund of unused paid credits |
| `POST` | `/v1/internal/stripe/webhook` | Stripe webhook receiver (signed) |

The CLI's `brr brnrd topup`, `brr brnrd balance`, `brr brnrd
autotopup` verbs (per
[`decision-cli-shape.md`](decision-cli-shape.md)) wrap these
endpoints.

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

- **No subscriptions at launch**; only `checkout.session` for
  one-shot purchases.
- **No card vaulting at launch**, except when the user opts
  into auto-topup. Without auto-topup, no customer object
  holds card data on brnrd. (When auto-topup is on, a Stripe
  `Customer` is created and the payment method is attached,
  scoped to the auto-topup operation only.)
- **Webhook receiver** at `POST /v1/internal/stripe/webhook`,
  signature-verified with the Stripe signing secret; idempotent
  on `payment_intent.id`; small redelivery queue for transient
  failures.
- **Test mode** during dev: separate Stripe test API key in
  `BRNRD_STRIPE_API_KEY` env var; test webhook secret in
  `BRNRD_STRIPE_WEBHOOK_SECRET`. Stripe's test card numbers +
  SCA-triggering test cards cover the launch test matrix.

## What we do NOT do at launch

- **Card-on-file by default**. Only opt-in (via auto-topup).
- **Per-team / per-seat billing**. Per-account only; team /
  per-seat is the v-next surface if a team tier ships.
- **Invoicing / NET-30**. All B2B purchases are prepay via
  Checkout. Enterprise invoicing is a v-next ask.
- **Crypto / non-Stripe payment rails**. Stripe-only.
- **Custom currency on the ledger**. USD-only on the internal
  ledger; Stripe converts at purchase time. (UI shows balance
  in USD; "€18.00 = ~$20 = 2000 credits" displayed at top-up.)
- **Per-spawn dynamic pricing / surge multipliers**. Flat
  per-minute rate based on Fly billing + brnrd platform margin.
- **Trial-extension / promo codes**. Maybe v-next.

## Why credits, vs alternatives considered

| Model | Notes |
|-------|-------|
| **Credits (chosen)** | Industry-standard for usage-metered services. No card-on-file = supports data-minimization pitch. Predictable cost for user (they pre-pay a known amount). Smooth UX for occasional users. Auto-topup covers heavy users. |
| Subscription monthly fee | Doesn't match the metered cost shape; either over-charges occasional users or under-charges heavy ones. Bad for the "we charge for ops, not for AI usage" framing. |
| Pay-as-you-go card-on-file | Forces card-on-file always; works against the data-minimization story. Surprise bills are the #1 complaint with this model. |
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
   plugs in.
3. [`decision-cli-shape.md`](decision-cli-shape.md) for the
   `brr brnrd topup | balance | autotopup` verbs.

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
