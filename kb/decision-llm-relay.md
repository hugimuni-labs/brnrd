# Decision: LLM relay pricing

**Status: accepted 2026-06-15.** Supersedes the "we do **not** charge for
AI usage" clause of
[`decision-pricing-shape.md`](decision-pricing-shape.md) (accepted
2026-05-26). That page stands as written for the subscription +
compute-credit shape; this page changes one thing it asserted — that
Anthropic / OpenAI / Google bills always belong directly to the user — and
replaces it with a relay model that passes provider cost through the wallet
plus a transparent service fee.

Replaces [`decision-llm-passthrough-credits.md`](decision-llm-passthrough-credits.md)
(accepted 2026-06-15, relay-at-cost framing), which held that the relay is
zero-margin. This page revises that stance: a modest service fee is honest
and sustainable for a bootstrapped product; "relay at cost" was too
idealistic without VC cushion. The old file is retired; references should
point here.

## The shape in one sentence

When brr relays tokens on brnrd's account, the wallet is charged at
**provider cost plus a transparent relay service fee (10–15%)** — shown
as a separate line item, not buried in an opaque credit rate. The
subscription is the margin-bearing revenue line; the relay service fee
covers the operational overhead of running the relay, not speculation on
AI usage.

## Why a service fee, not "relay at cost"

The $15-ticket data point from #114 still motivates the relay: a user ran
out of their monthly Codex and Claude quotas mid-week and the work stalled.
Unblocking that interruption is the resource worth solving for — a
convenience relay that lets the work continue on metered spend the user
controls.

But the relay is not free to operate. Real infrastructure overhead not
captured in the provider bill: the relay endpoint, per-token billing
hooks, rate limiting, abuse prevention, key rotation, monitoring, incident
response. "Relay at cost" hides this overhead; a transparent service fee
names it honestly.

This is a bootstrapped, self-funded product. Tools like Cursor launched
with flat-fee-unlimited LLM, found it unsustainable, and moved to
credits-with-overage at a markup. brr doesn't have the runway to absorb
heavy relay usage at zero margin; a modest service fee at launch is more
honest than an implicit cost-shift that will need reversing later.

The key distinction from **"Resell AI" (rejected in
[`decision-pricing-shape.md`](decision-pricing-shape.md))**: that
rejection was against AI usage as a *primary product line* — selling AI
access as the revenue story. A relay service fee is different in degree
and in framing: we don't mark up aggressively, we don't bundle AI into
subscription tiers, and we show the provider cost and the service fee as
separate line items. "We charge a transparent service fee to cover the
cost of operating the relay; we don't profit on AI usage" is a trust
position — just an honest one.

## What the relay charge looks like

| Component | Rate |
|-----------|------|
| Provider cost | Input/output per-M-token rates at current Anthropic / OpenAI / Google published rates — no additional markup on this line |
| Relay service fee | 10–15% of the provider cost (exact rate set in [`design-billing.md`](design-billing.md)) |

The service fee appears as a **separate line item** in the billing UI —
"Provider cost: $0.47 · Relay service fee: $0.05 · Total: $0.52." This
transparency is the mechanism that makes it trustworthy: users see the
provider cost and the fee independently, not a blended "relay credits"
rate. The fee covers endpoint and billing infrastructure, not AI
usage profit.

For context: 100K tokens on Claude Sonnet ($0.30 provider cost) → ~$0.33–$0.35
billed. 50 relay tasks/month per subscriber → $1.50–$3.50 in relay
service fees — meaningful additional revenue at scale without being
predatory.

## Decision

1. **LLM relay is a convenience relay, not a product.** When a user has
   no usable credential (no key, or quota exhausted), brr relays tokens
   on brnrd's account and bills the wallet at provider cost plus the relay
   service fee. Start with **Codex / OpenAI** (the CLI is already in the
   bundled image), then widen to other providers as demand appears.

2. **BYO stays free and is the default.** A user who brings their own key
   pays brnrd nothing for AI usage — the original clause holds. The relay
   is opt-in and only activates when BYO is unavailable.

3. **Bundled Codex on brnrd's token is the managed fallback.** When a run
   finds no usable credential, the fallback chain is: own key → brnrd
   relay → bundled-on-brnrd-token. Each step is surfaced through the
   spending-plan / consent checkpoint (§ below) — never silent.

4. **Managed compute carries an explicit ops margin.** Fly Machines run on
   brnrd's account include a small margin to cover ops overhead. The rate
   is distinct from the relay service fee and should be labelled as such
   in the billing UI ("managed compute ops") rather than rolled into an
   opaque credits rate.

5. **Docker + current runner shape is the starting point.** The bundled
   image already installs the Codex CLI; the relay path is a credential
   fallback plus a billing hook, not new architecture.

### What this does NOT change

- Subscription tiers and compute-credit wallet mechanics in
  [`decision-pricing-shape.md`](decision-pricing-shape.md).
- The relay-not-store / data-minimisation stance.
- BYO remaining free and the default.

## Spending plan: the consent mechanism

Relay billing is trustworthy only when the user can see what they are
about to spend before they spend it. The spending plan is the mechanism:

A run that is about to consume significant tokens (relay or managed
compute) emits a structured **spend projection** before committing — what
it intends to do, which runner/model, estimated cost at relay rates
including the service fee — and pauses for user approval. User approves,
cancels, or reshapes the task. Then the run continues.

This is not a multi-step orchestrator or a dependency graph. It is a
single consent checkpoint that sits one layer earlier than it does today —
before spending, not just before spawning a Fly Machine.

The connection to `design-run-event-model.md` Q4: that page recommends
keeping cost attribution at the **run** granularity and making the run's
*decision to fold* the consent point — "the resident defers folding an
expensive stuck event if cost/consent says so." The spending plan is what
the resident shows the user before that decision is made. These two form
one coherent model: the run projects its spend (including service fee);
the user approves (or doesn't); the run then decides what to fold in and
what to postpone.

Implementation design for the spending plan belongs in a dedicated design
page (or in `design-run-event-model.md` as the Q4 implementation slice),
not here. This page records the decision that the mechanism must exist and
that it is the precondition for relay billing being trustworthy at all.

## Runner type vs. model (out of scope here)

The previous draft of this page included a "model selector" section. That
section conflated two distinct concerns:

- **Runner type** (which execution environment — Docker+Codex, local
  Claude Code, managed cloud, brnrd relay): a project/account-level
  infrastructure setting changed infrequently.
- **Model** (which LLM within a runner): a task preference changeable per
  conversation.

These are not a pricing decision. They belong in a UX/config design page
alongside the `runner` / `model` config key design. The runner-type
fallback chain in Decision point 3 above is the only runner-relevant thing
this page needs to say.

## Sequencing

1. This decision accepted — supersedes the relay-at-cost framing and the
   BYO-only clause.
2. Spending plan / consent-checkpoint design page (Q4 implementation slice
   from `design-run-event-model.md`).
3. Codex relay endpoint + wallet billing hook (provider cost + service fee);
   bundled-on-brnrd-token fallback in the Docker credential block.
4. Consent/projection layer learns "relay tokens" (with fee) + "managed
   compute ops" as spend sources — chains into the self-spend-tracking
   work flagged on #114.
5. Billing UI: separate line items for provider cost, relay service fee,
   and managed compute ops rate.
6. Exact relay service fee rate locked in [`design-billing.md`](design-billing.md)
   once operational cost model is clearer.

## Open questions

- **Exact relay service fee rate** within the 10–15% range — to be
  specified in [`design-billing.md`](design-billing.md) with the ledger
  mechanics.
- Whether relay opt-in requires an explicit one-time consent flow (click
  "enable relay") or activates automatically when credentials are absent
  (with the spending plan as the per-run gate). Lean toward the latter —
  it removes a setup step while still gating each spend.
- Abuse surface for the brnrd-hosted relay: rate limiting and attribution
  per run; ties to the consent-as-projection redesign.
- Whether the service fee is a percentage (10–15% of provider cost) or a
  flat per-call fee. Percentage is simpler and scales with usage; flat fee
  can be gamed with small calls. Percentage is the current lean.

## Companions

- [`decision-pricing-shape.md`](decision-pricing-shape.md) — the page this
  supersedes one clause of; wallet and credit mechanics.
- [`design-billing.md`](design-billing.md) — wallet / ledger / Stripe the
  relay charge rides on; relay service fee rate and managed-compute ops
  margin rate should be defined here.
- [`design-run-event-model.md`](design-run-event-model.md) — Q4 is the
  billing/retry interaction; the spending-plan implementation slice.
- [`design-co-maintainer.md`](design-co-maintainer.md) — §6 delivery
  floor, §9 the consent surface.
- [`subject-managed-mode.md`](subject-managed-mode.md) — hosted surfaces.
- [`plan-failover-compute.md`](plan-failover-compute.md) — compute
  failover; this page reframes which failure (LLM quota) is likelier than
  compute env failure.
