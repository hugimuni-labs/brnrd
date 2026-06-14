# Decision: sell LLM passthrough credits (and where the model lives)

**Status: proposed 2026-06-14.** Supersedes the "we do **not** charge for
AI usage" clause of
[`decision-pricing-shape.md`](decision-pricing-shape.md) (accepted
2026-05-26). That page stands as written for the subscription + compute-
credit shape; this page changes one thing it asserted — that Anthropic /
OpenAI / Google bills always belong directly to the user — and adds a
sellable LLM-access resource plus the configuration surface that makes it
usable. Drafted from the #114 thread; needs a decision before the code in
`runner.py` / `config.py` moves.

## Why (the data point and the pivot)

The closed-loop trigger: one co-maintainer ticket on #114 cost ~$15 in
Claude credits because the operator had exhausted both their Codex and
Claude monthly subscriptions that week — each a ~$20/month limit-based
plan that most users would actually run on. The likeliest failure mode is
**not** "the runner's compute environment died and needs a cloud
failover" (the case
[`plan-failover-compute.md`](plan-failover-compute.md) sells against). It
is **"the user ran out of their monthly LLM quota mid-week."** That is the
resource worth selling.

The accepted pricing decision deliberately said brnrd does not touch AI
billing — keep it clean, let the user's keys bill the user. That framing
is right for BYO and should stay the free default. But it leaves the most
common interruption unaddressed and forecloses a real, simple revenue
surface: brr already runs the user's LLM CLI; passing through tokens on
**our** account and billing the existing credit wallet is just charging
for a relay we are one config line away from offering.

## Identity constraint (what shapes the offer)

The product identity, in the operator's words: *a very simple,
using-what-you-have setup and a forge-centred, AI-co-maintained
workflow.* That cuts two ways here:

- **Sell broadly, gate lightly.** There is no reason to limit what we sell
  as long as users can self-service it easily. Passthrough LLM credits,
  bundled Codex, managed compute — all fair game as purchasable resources.
- **Do not bury the product under conflicting toggles.** Every knob we add
  (model, runner, passthrough-on/off, cloud-vs-local LLM) is a tax on the
  "using-what-you-have" promise. The shape below leans on **good
  defaults + one override surface**, not a matrix of switches.

## Decision

1. **LLM passthrough is a purchasable resource billed from the existing
   credit wallet** — the same wallet `decision-pricing-shape.md` already
   defines for compute overage. No second currency. Start with **Codex /
   OpenAI passthrough** (the CLI is already in the bundled image), then
   widen to other providers as demand appears.

2. **Bundled Codex is the fallback on our token.** When a run finds no
   usable user credential (no key, or quota exhausted), brr can fall back
   to a brnrd-hosted Codex on our account, **billing the user's credits**
   for the tokens — opt-in, surfaced through the consent/projection layer,
   never silent. This is the direct answer to the $15-ticket interruption:
   the work continues on metered credits instead of stalling.

3. **BYO stays free.** A user who brings their own key (or runs a local
   model) pays brnrd nothing for AI usage — the original clause holds for
   them. Passthrough is the opt-in path for users *without* their own
   subscription, which is exactly the TAM the BYO-only model excluded.

4. **If brnrd supplies the LLM, brnrd hosts it in the cloud** — the
   passthrough endpoint runs on our infrastructure on our account. But the
   behaviour is **overridable**: a user can always point the runner back
   at their own key / endpoint / local model. Cloud-hosted is the managed
   default, not a lock-in.

5. **Docker + the current runner shape is the starting point, not a
   rebuild.** The bundled image already installs the Codex CLI and mounts
   `~/.codex`; the passthrough path is an env-var/credential fallback in
   that existing block plus a billing hook, not new architecture.

### What this does NOT change

- The subscription + compute-credit tiers in `decision-pricing-shape.md`.
- The relay-not-store / data-minimisation stance.
- BYO remaining free and the default.

## The model selector (the "where it lives" half)

Today the LLM is selected by editing `runner` / `runner_cmd` in
`.brr/config` (`runner.py:resolve_runner` / `_build_cmd`) and **restarting
the daemon** — `config.py` has no hot-reload. The operator's lived pain:
*"each time a runner has an issue I have to go change the setting on the
laptop and restart."* Once we sell model access, the model also becomes a
billing-relevant product surface, so it should not be buried in a static
file that needs a laptop and a restart.

Proposed shape (one surface, not a matrix):

- **Promote `model` to a first-class config key**, distinct from `runner`
  (the CLI) and `runner_cmd` (the full argv escape hatch). Today the model
  is smuggled inside `runner_cmd`; a named `model` key the runner maps to
  the active CLI's `--model` flag is what a selector can target.
- **Make it changeable without a restart, from where the user already
  is** — i.e. as a message to the resident (a chat/forge command like
  `@brr-bot use model gpt-5.5`), which rewrites the dominion/config value
  and takes effect on the next wake. This fits the co-maintainer identity:
  you talk to your colleague, you don't SSH to its laptop. (Requires
  config hot-reload or a per-wake read, which the resident loop already
  does on each thought.)
- **Let the resident self-select on failure** as the strongest rung:
  when a run hits quota exhaustion / credit-low on the configured model,
  the fallback chain (own key → passthrough → bundled-on-our-token) is the
  same chain decision #2 defines. The "model selector" then becomes a
  *preference + a fallback policy*, not a thing the human edits mid-incident.
- **Managed dashboard** exposes the same setting for hosted users; it is
  the brnrd-side projection of the one config key, not a separate model.

## Sequencing

1. This decision accepted (or amended) — the supersession is the gate.
2. `model` config key + chat-command/hot-reload override (model selector).
3. Codex passthrough endpoint + wallet billing hook; bundled-on-our-token
   fallback in the docker credential block.
4. Consent/projection layer learns "passthrough tokens" as a spend source
   (chains into the self-spend-tracking work flagged on #114).

## Open questions

- Per-M-token pricing vs. a credit multiplier on provider cost — needs a
  margin model in [`design-billing.md`](design-billing.md).
- Whether bundled-on-our-token needs an explicit per-run consent prompt or
  rides a standing wallet authorisation with a projection cap.
- Abuse surface: a hosted Codex on our token is a resource to rate-limit
  and attribute carefully; ties to the consent-as-projection redesign.

## Companions

- [`decision-pricing-shape.md`](decision-pricing-shape.md) — the page this
  supersedes one clause of.
- [`design-billing.md`](design-billing.md) — wallet / ledger / Stripe the
  credit charge rides on.
- [`subject-managed-mode.md`](subject-managed-mode.md) — the hosted
  surfaces, including the dashboard model setting.
- [`plan-failover-compute.md`](plan-failover-compute.md) — the *other*
  fallback (compute, not LLM); this reframes which failure is likelier.
- `runner.py` (`resolve_runner`, `_build_cmd`, `runner`/`runner_cmd`),
  `config.py` (`load_config`/`write_config`, no hot-reload today) — the
  model-selector code surface.
