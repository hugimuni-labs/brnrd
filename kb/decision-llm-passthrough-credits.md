# Decision: compute cost relay (retired)

**Status: retired 2026-06-15.** This page is superseded by
[`decision-llm-relay.md`](decision-llm-relay.md), which updates the
"relay at cost, no margin" framing to "provider cost plus a transparent
relay service fee (10–15%)". All references should point to that page.

---

<!-- retained for history; content below reflects the relay-at-cost stance -->

**Original status (proposed 2026-06-15):** Supersedes the "we do **not** charge for
AI usage" clause of
[`decision-pricing-shape.md`](decision-pricing-shape.md) (accepted
2026-05-26). That page stands as written for the subscription +
compute-credit shape; this page changes one thing it asserted — that
Anthropic / OpenAI / Google bills always belong directly to the user — and
replaces it with a relay-at-cost model for LLM traffic and a small ops
margin for managed compute infrastructure.

Replaces `decision-llm-passthrough-credits.md` (proposed 2026-06-14),
which framed passthrough as a revenue product rather than a cost relay.
The page has been renamed accordingly.

## The pivot in one sentence

The subscription is the only margin-bearing revenue line. Compute costs —
LLM tokens and managed cloud infrastructure — are **relayed through the
wallet**, not marked up for profit. The value is in the coordination
platform; brr does not profit on the AI relay.

## Why

The $15-ticket data point from #114 still motivates the relay: a user
ran out of their monthly Codex and Claude quotas mid-week and the work
stalled. That interruption is the resource worth solving for — not by
selling AI access as a product line, but by providing a transparent relay
that lets the work continue on metered spend the user controls.

The key framing shift: the interruption is a *continuity* failure, not a
*feature* gap. "Pass tokens on our account and charge the wallet" is the
smallest fix — a convenience relay that removes a wall, not a new product
that replaces the user's own subscription.

## What "relay at cost" means (and one nuance)

**LLM relay: no margin.** When brr passes tokens through brnrd's
Anthropic / OpenAI / Google account, the wallet is charged at provider
cost — input/output per-M-token rates, no markup. brr is not in the
business of profiting on AI traffic; the wallet charge is a cost relay,
not a revenue line. Transparent billing — "you spent $0.47 on Claude
sonnet this run at Anthropic's current rate" — is the mechanism that makes
this trustworthy.

**Managed compute: small ops margin.** A Fly Machine managed by brnrd has
operational overhead not in the cloud bill: setup, monitoring, credential
management, failure handling. A modest ops margin on managed infra
(separate from the LLM relay) covers this overhead explicitly rather than
hiding it in an opaque credit multiplier. The distinction matters: "we
don't profit on AI; we charge a small ops margin on managed compute" is
honest. The current `$0.01/credit` rate in
[`decision-pricing-shape.md`](decision-pricing-shape.md) should be
annotated as an ops-margin rate for managed compute, not a general "AI
credits" rate.

## Decision

1. **LLM relay is a cost-pass-through resource, not a product.** When a
   user has no usable credential (no key, or quota exhausted), brr relays
   tokens on brnrd's account and bills the wallet at provider cost, no
   markup. Start with **Codex / OpenAI** (the CLI is already in the
   bundled image), then widen to other providers as demand appears.

2. **BYO stays free and is the default.** A user who brings their own key
   pays brnrd nothing for AI usage — the original clause holds. The relay
   is opt-in and only activates when BYO is unavailable.

3. **Bundled Codex on brnrd's token is the managed fallback.** When a run
   finds no usable credential, the fallback chain is: own key → brnrd
   relay → bundled-on-brnrd-token. Each step is surfaced through the
   spending-plan / consent checkpoint (§ below) — never silent.

4. **Managed compute carries an explicit ops margin.** Fly Machines run on
   brnrd's account include a small margin to cover ops overhead.
   The rate is distinct from the LLM relay rate and should be labelled as
   such in the billing UI ("managed compute ops") rather than rolled into
   an opaque credits rate.

5. **Docker + current runner shape is the starting point.** The bundled
   image already installs the Codex CLI; the relay path is a credential
   fallback plus a billing hook, not new architecture.

### What this does NOT change

- Subscription tiers and compute-credit wallet mechanics in
  [`decision-pricing-shape.md`](decision-pricing-shape.md).
- The relay-not-store / data-minimisation stance.
- BYO remaining free and the default.

## Spending plan: the consent mechanism

Relay-at-cost is trustworthy only when the user can see what they are
about to spend before they spend it. The spending plan is the mechanism:

A run that is about to consume significant tokens (relay or managed
compute) emits a structured **spend projection** before committing — what
it intends to do, which runner/model, estimated cost at relay rates — and
pauses for user approval. User approves, cancels, or reshapes the task.
Then the run continues.

This is not a multi-step orchestrator or a dependency graph. It is a
single consent checkpoint that sits one layer earlier than it does today —
before spending, not just before spawning a Fly Machine.

The connection to `design-run-event-model.md` Q4: that page recommends
keeping cost attribution at the **run** granularity and making the run's
*decision to fold* the consent point — "the resident defers folding an
expensive stuck event if cost/consent says so." The spending plan is what
the resident shows the user before that decision is made. These two form
one coherent model: the run projects its spend; the user approves (or
doesn't); the run then decides what to fold in and what to postpone.

Implementation design for the spending plan belongs in a dedicated design
page (or in `design-run-event-model.md` as the Q4 implementation slice),
not here. This page records the decision that the mechanism must exist and
that it is the precondition for relay-at-cost being trustworthy at all.

## Runner type vs. model (out of scope here)

The previous draft of this page included a "model selector" section. That
section conflated two distinct concerns:

- **Runner type** (which execution environment — Docker+Codex, local
  Claude Code, managed cloud, brnrd passthrough): a project/account-level
  infrastructure setting changed infrequently.
- **Model** (which LLM within a runner): a task preference changeable per
  conversation.

These are not a pricing decision. They belong in a UX/config design page
alongside the `runner` / `model` config key design. The runner-type
fallback chain in Decision point 3 above is the only runner-relevant thing
this page needs to say.

## Sequencing

1. This decision accepted — supersedes the BYO-only clause.
2. Spending plan / consent-checkpoint design page (Q4 implementation slice
   from `design-run-event-model.md`).
3. Codex relay endpoint + wallet billing hook; bundled-on-brnrd-token
   fallback in the Docker credential block.
4. Consent/projection layer learns "relay tokens" + "managed compute ops"
   as spend sources — chains into the self-spend-tracking work flagged
   on #114.
5. Billing UI separates LLM relay rate from managed compute ops rate.

## Open questions

- Exact managed-compute ops margin rate — should be specified in
  [`design-billing.md`](design-billing.md) with the ledger mechanics.
- Whether relay opt-in requires an explicit one-time consent flow (click
  "enable relay") or activates automatically when credentials are absent
  (with the spending plan as the per-run gate). Lean toward the latter —
  it removes a setup step while still gating each spend.
- Abuse surface for the brnrd-hosted relay: rate limiting and attribution
  per run; ties to the consent-as-projection redesign.

## Companions

- [`decision-pricing-shape.md`](decision-pricing-shape.md) — the page this
  supersedes one clause of; wallet and credit mechanics.
- [`design-billing.md`](design-billing.md) — wallet / ledger / Stripe the
  relay charge rides on; managed-compute ops margin rate should be defined
  here.
- [`design-run-event-model.md`](design-run-event-model.md) — Q4 is the
  billing/retry interaction; the spending-plan implementation slice.
- [`design-co-maintainer.md`](design-co-maintainer.md) — §6 delivery
  floor, §9 the consent surface.
- [`subject-managed-mode.md`](subject-managed-mode.md) — hosted surfaces.
- [`plan-failover-compute.md`](plan-failover-compute.md) — compute
  failover; this page reframes which failure (LLM quota) is likelier than
  compute env failure.
