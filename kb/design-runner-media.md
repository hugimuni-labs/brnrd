# Design: runner media, cost policy, and brnrd relay fallback

Status: active on 2026-06-27

This page is the implementation-design companion to
[`plan-cost-aware-cockpit.md`](plan-cost-aware-cockpit.md) and the pricing
decision in [`decision-llm-relay.md`](decision-llm-relay.md). It answers the
2026-06-27 maintainer ask: simple requests should use a cheaper model, costly
respawns should use a stronger model, and brnrd-owned relay credits should be a
smooth fallback when local runner quota is gone.

## Reconciled current state

brr currently has **runner profiles**, not **runner media**:

- `src/brr/prompts/runners.md` defines static CLI invocations for `claude`,
  `codex`, Gemini intent, and a few Claude aliases.
- `.brr/config` selects one `runner`, or a fully custom `runner_cmd`.
- The daemon resolves that runner before prompt assembly, injects
  `Runner: <medium>` into the Mode block, and can append a trusted quota summary
  from `runner.quota.*`, `BRR_RUNNER_QUOTA_*`, or `.brr/runner-quota.json`.
- The live `portal-state.json` resource facet has the future slots already:
  `quota`, `cost`, `coexisting_runs`, and `remote_scm`. Today only `quota` can
  be known; `cost` and the others truthfully render as unavailable.
- Boundary interweave is now native hooks: Claude and Codex reach the same
  runner boundary via `brr hook <phase>`, so fresh resource state can be woven
  into the resident at seed/stop and, when attention changes, at post-tool.

The missing piece is not "add a model flag." A runner medium has at least:
command syntax, model, auth source, hook capability, quota source, cost class,
owner (`user` or `brnrd`), fallback eligibility, and billing policy. Hiding those
inside a shell command would keep the current blindness.

## What the CLIs expose today

Local probes in this repo:

| CLI | Observed version | Useful selection knobs | Useful cost/quota knobs | Gaps |
| --- | --- | --- | --- | --- |
| Codex CLI | `codex-cli 0.141.0` | `codex exec --model`, `-c model=...`, `--profile`, `--json`, `exec resume`, app-server commands | No local help flag for a run budget cap. Official changelog says Codex Mini is a cheaper/smaller model with about 4x more subscription usage and the CLI/IDE can suggest switching near a 5-hour limit. OpenAI Admin Usage/Costs APIs exist for API-key org usage. | ChatGPT/Codex subscription quota and on-demand-credit balance are not exposed as a clean local CLI API in the observed help. Treat them as error/suggestion/manual-snapshot signals until proved otherwise. |
| Claude Code | `2.1.191` | `--model`, `--fallback-model`, named/resumable sessions, `--output-format stream-json`, `--max-turns` | `--max-budget-usd` exists for print mode. Anthropic API responses expose rate-limit headers; the Admin API can expose usage/cost/rate-limit reports with admin credentials. Claude Agent SDK usage records include token counts and `costUSD` caveats. | Subscription/OAuth Claude Code quota may differ from API-key Admin API visibility. Do not assume API headers cover every auth mode. |
| Gemini CLI | not installed in this environment | Bundled profile intent is `gemini -p --yolo`; docs/repo indicate hooks exist | Gemini API quotas are per project, measured across RPM/TPM/RPD plus spend-based limits, and billing/account caps sit at billing-account level. | No local firing/version probe here, so Gemini remains intent until installed and smoked. |

Implication: provider collectors must be **pluggable and provenance-tagged**.
Some signals are authoritative (brnrd wallet balance, API headers for brnrd-owned
keys). Some are best-effort (CLI error text, manually supplied snapshot). The
portal should show the source and freshness rather than pretending all quota
signals have equal quality.

## Decision: introduce runner media

Add a runner-medium layer above static profiles. A profile remains "how to invoke
this CLI." A medium is "when and why to use this invocation."

Sketch:

```toml
[runner]
policy = "cost-aware"
default_class = "economy"

[[runner.media]]
name = "codex-mini-local"
profile = "codex"
model = "gpt-5-codex-mini"
provider = "openai"
owner = "user"
class = "economy"
cost_rank = 10
quota_source = "codex-local"
hooks = "codex"

[[runner.media]]
name = "codex-strong-local"
profile = "codex"
model = "gpt-5.1-codex"
provider = "openai"
owner = "user"
class = "strong"
cost_rank = 40
quota_source = "codex-local"
hooks = "codex"

[[runner.media]]
name = "brnrd-codex-relay"
profile = "codex"
model = "gpt-5-codex-mini"
provider = "openai"
owner = "brnrd"
class = "relay"
cost_rank = 30
quota_source = "brnrd"
billing = "llm-relay"
consent = "spend-plan"
```

This should align with the existing three-scope config model:

- **Project scope**: preferred policy, known media names, project default class.
- **Local scope**: which local profiles are installed, machine-specific auth env
  names, `runner_cmd` escape hatches.
- **Account scope**: brnrd relay enabled/disabled, spending caps, wallet balance,
  user-wide medium preference.

Legacy compatibility can be narrow: the existing `runner=codex` becomes an
implicit medium named `codex-local` with unknown cost metadata. Since the product
is pre-release, do not preserve multiple spellings beyond the useful shim.

## Dispatch policy

The healthy default is not "always cheap" and not "always smart." It is a small
policy table whose inputs are cheap to know before the run:

| Situation | First medium | Escalation |
| --- | --- | --- |
| Small ask, explanation, narrow edit, docs lookup | `economy` local medium | Strong local only if the run asks to respawn or fails for quality, not for missing stdout. |
| Normal implementation | `economy` or `balanced` local medium, depending on repo default | Strong local on explicit user ask, repeated failed attempt, or resident-authored escalation. |
| Wide refactor, release, security, billing, destructive operation | `strong` local medium or PLAN first | Spend-plan before brnrd relay or managed paid path. |
| Local quota exhausted | Next local medium in same or lower cost class if available | brnrd relay only after spend-plan consent. |
| Provider/CLI outage | Different provider local medium if available | brnrd relay if it is a different failure domain and consent allows it. |
| No usable local runner | brnrd relay or queued setup guidance | spend-plan plus wallet/top-up route. |

The "respawn is handled by the more expensive model" part should be explicit:
an economy run may emit a **respawn request** rather than grinding. That request
is a parked portal: reason, proposed stronger medium, what context should carry
forward, and the spend/consent posture. The daemon then starts a new run on that
medium once policy allows it. This keeps cheap models useful without making them
responsible for deciding invisible billing.

Avoid reviving an LLM triage stage. The first selector should be deterministic
and conservative. The resident can escalate after it has read the repo and knows
the task is harder than the event body looked.

## Quota and credit signals

Use a three-grade signal model:

| Grade | Examples | How it enters brr |
| --- | --- | --- |
| Authoritative live | brnrd wallet/relay balance; response headers for brnrd-owned API keys; cloud provider quota API for a managed key | daemon collector writes structured snapshot with source, freshness, and reset windows |
| Provider/account historical | OpenAI organization usage/cost APIs; Anthropic Admin API reports; Google billing/usage views | async collector, never on prompt critical path; feeds historical pre-analysis |
| Best effort local | Codex/Claude subscription error text; CLI suggestions; manually supplied `.brr/runner-quota.json` | snapshot or failure classifier, source marked best-effort |

The live portal state should carry this as structured data, not only a string:

```json
{
  "resources": {
    "runner_media": {
      "selected": "codex-mini-local",
      "fallbacks": ["codex-strong-local", "brnrd-codex-relay"],
      "quota": {
        "status": "known",
        "source": "manual-snapshot",
        "summary": "weekly 42% - resets 3d",
        "fresh_at": "2026-06-27T12:00:00Z"
      },
      "credits": {
        "status": "known",
        "owner": "brnrd",
        "summary": "relay balance $4.20, per-run cap $1.00"
      },
      "cost": {
        "status": "historical",
        "summary": "last 20 narrow edits: median 0.18 relay credits"
      }
    }
  }
}
```

The current hook renderer can keep a compact one-line projection, but the
underlying portal file should become rich enough for the resident and future UI
to inspect without parsing prose.

## Brnrd-owned intelligence fallback

`decision-llm-relay.md` already made the pricing call: BYO stays free/default;
when brr uses brnrd-owned LLM capacity, the wallet pays provider cost plus a
transparent 10-15% relay service fee. This design turns that into runner
dispatch:

1. **Local first.** Use the user's configured local runner media while quota is
   available.
2. **Relay as the smooth fallback.** When local quota is absent/exhausted, the
   daemon proposes `brnrd-codex-relay` (OpenAI/Codex first, because the image and
   profile already exist).
3. **Spend-plan consent.** Before relay use, show: model, reason for fallback,
   wallet balance, per-run cap, provider-cost line, relay-service-fee line, and
   what happens at cap. This is an estimate/cap envelope for consent, not a
   promise of exact final spend.
4. **Hard stop at cap.** The brnrd relay should enforce a per-run budget server
   side. The runner's own `--max-budget-usd` can be used where available
   (Claude API-key print mode), but brnrd cannot rely on CLI flags for the
   financial guardrail.
5. **Debit from a relay/intelligence bucket, not compute credits silently.**
   The user-facing wallet can say "Intelligence credits" if that is the product
   language, but the ledger must keep separate line items:
   `llm_provider_cost`, `llm_relay_service_fee`, and `managed_compute_ops`.
   Opaque blended credits would violate the accepted relay decision.
6. **Top-up path is the fallback UX.** If the relay balance is empty, the gate
   reply should offer top-up or "wait for local quota reset / configure a local
   runner." The paid path is the easiest fallback, not the only path.

Fairness/footgun guardrails:

- Relay is opt-in per spend plan until the user has set an explicit auto-approve
  policy.
- No card-on-file auto-topup by default.
- Per-run and per-day caps are enforced by brnrd, not just by runner prompt.
- Every relay run emits an audit row with provider, model, prompt/input/output
  token counts where available, provider cost, service fee, total, and cap
  outcome.
- Free users can receive a tiny one-time relay trial only if the abuse math is
  explicit; otherwise keep the free bonus compute-only and make relay require a
  top-up.

## Custom runners and the unified cost interface

Custom `runner_cmd` remains useful, but it is unmanaged. In cost-aware mode it
should be treated as:

- selectable only when the user explicitly configured it as default, or marked
  it with metadata;
- no automatic billing or quota claims unless it declares a collector;
- no brnrd fallback equivalence unless it names provider/model/owner.

A custom runner can join the policy by declaring a medium:

```toml
[[runner.media]]
name = "my-local-agent"
cmd = "my-agent --model cheap"
owner = "user"
class = "economy"
cost_rank = 15
quota_source = "operator-snapshot"
cost_source = "none"
hooks = "none"
```

That keeps brr open without forcing arbitrary CLIs into a billing interface they
cannot truthfully satisfy.

## User-facing shape

The remote user should see these as simple choices:

- `cheap/default` runs for ordinary work.
- `strong` is available for harder work and respawns.
- `brnrd relay` appears only when local quota/auth cannot carry the task, or
  when the user explicitly picks it.
- The live card says the selected medium and quota posture.
- A spend-plan message gates paid relay or managed compute before spend.
- The dashboard/CLI can list configured media, current quota source/freshness,
  relay balance, and fallback policy.

Example gate wording:

```text
Local Codex is out of weekly quota. I can continue with brnrd Codex relay.

Medium: gpt-5-codex-mini via brnrd
Cap: $0.75 for this run
Billing: provider cost + 12% relay service fee, shown separately
Balance: $4.20 relay balance

Approve / Queue until local reset / Configure own runner
```

## Implementation sequence

1. **Data model only:** add runner-medium schema and a resolver that turns legacy
   `runner=` into one implicit medium. No dispatch changes yet.
2. **CLI/display:** `brr runners list` or `brr config get runner.media` shows
   media, owner, class, hooks, quota source, and known freshness.
3. **Portal upgrade:** replace the flat `resources.quota` string with structured
   `runner_media` while preserving the current compact hook line.
4. **Deterministic selector:** choose economy/balanced/strong by policy and user
   override; keep one-run execution otherwise unchanged.
5. **Failure classifier:** distinguish quota, auth, provider outage, quality
   escalation, and no-response validation. Only quota/auth/provider errors enter
   fallback policy automatically.
6. **Respawn portal:** let a resident request a stronger medium with reason and
   carry-forward context.
7. **brnrd relay consent:** spending-plan prompt, wallet balance read, cap
   enforcement, audit rows. Codex/OpenAI first.
8. **Provider collectors:** async collectors for OpenAI, Anthropic, Gemini, each
   provenance-tagged; never block prompt assembly on network.
9. **Historical spend:** aggregate completed runs by medium and task shape for
   historical pre-analysis. Keep the existing guardrail: no projected dollar
   promise for local runs; paid relay gets a cap/quote envelope for consent.

## Standing portal candidates

These belong in `portal-state.json` and the hook seed/closeout capsule, not only
in prose:

- selected runner medium, class, model, owner, and fallback chain;
- runner-medium capability proof (`hooks: codex`, fire-verified version, or
  fallback);
- local quota posture with source/freshness/confidence;
- brnrd relay/intelligence balance, per-run cap, and auto-approve policy;
- cost telemetry for this run so far, when available;
- whether an escalation/respawn has already been requested;
- whether paid relay consent is pending, approved, denied, or capped.

As this grows, `portal-state.json` likely deserves a small owner module rather
than staying as a large helper in `daemon.py`; the portal is becoming a product
surface, not just heartbeat state.

## Sources

- Local probes: `codex-cli 0.141.0`, `claude 2.1.191`, no `gemini` binary on
  this host.
- OpenAI Codex CLI reference:
  <https://developers.openai.com/codex/cli/reference>
- OpenAI Codex changelog, Codex Mini and on-demand credits:
  <https://developers.openai.com/codex/changelog>
- OpenAI organization usage/cost APIs:
  <https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs/>
- Claude Code CLI reference:
  <https://code.claude.com/docs/en/cli-reference>
- Anthropic rate-limit headers and Admin API:
  <https://platform.claude.com/docs/en/api/rate-limits>,
  <https://platform.claude.com/docs/en/manage-claude/admin-api>
- Gemini API rate limits and billing:
  <https://ai.google.dev/gemini-api/docs/rate-limits>,
  <https://ai.google.dev/gemini-api/docs/billing>
