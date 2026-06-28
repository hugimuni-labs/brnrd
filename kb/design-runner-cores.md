# Design: runner Shell/Core selection, cost policy, and brnrd relay fallback

Status: active on 2026-06-27 · foundation shipped 2026-06-28 · 2A/2B slices shipped 2026-06-29

> **2026-06-28 — shape adopted + foundation shipped (evt-y11i).** Two maintainer
> steers fixed the user-facing shape: (1) *model selection is a requirement, not
> a nicety — implement the right shape now*; (2) *carry low cognitive load —
> empower the runner to make the informed decision, do not ask the user to
> hand-tune execution details*, and (3) *expose the selected Shell/Core / cost /
> quota in the status card as governance*. Reconciliation: Shell/Core metadata rides
> the **existing profile frontmatter** (`runners.md`), not a new TOML config —
> this dissolves the config-format fork (`.brr/config` is flat `key=value` and
> cannot hold the `[[runner.profiles]]` array the sketch below shows), keeps the
> only user knobs `shell=` / `core=`, and makes the *selection policy
> brr's*. Shipped: `runner_select.py` (schema + implicit legacy shim +
> deterministic conservative `select_runner` + `RespawnRequest` shape), Shell/Core
> metadata on the bundled profiles, tests. Behaviour-neutral — no dispatch
> wiring yet. Open decisions for the wiring/card/respawn slices are at the foot
> of this page.

This page is the implementation-design companion to
[`plan-cost-aware-runner.md`](plan-cost-aware-runner.md) and the pricing
decision in [`decision-llm-relay.md`](decision-llm-relay.md). It answers the
2026-06-27 maintainer ask: simple requests should use a cheaper model, costly
respawns should use a stronger model, and brnrd-owned relay credits should be a
smooth fallback when local runner quota is gone.

## Reconciled current state

brr currently has **runner profiles** (Shell+Core combos), not a separate **media layer**:

- `src/brr/prompts/runners.md` defines static CLI invocations for `claude`,
  `codex`, Gemini intent, and a few Claude aliases.
- `.brr/config` selects one `runner`, or a fully custom `runner_cmd`.
- The daemon resolves that runner before prompt assembly, injects
  `Runner: <Shell+Core>` into the Mode block, and can append a trusted quota summary
  from `runner.quota.*`, `BRR_RUNNER_QUOTA_*`, or `.brr/runner-quota.json`.
- The live `portal-state.json` resource facet has the wall/state slots already:
  `quota`, `spend`, `context_window`, `coexisting_runs`, and `remote_scm`. Each
  renders three-state
  (evt-go5z): `known` carries a proven value; `absent` is affirmative-empty (no
  quota snapshot for this Shell/Core, no PR for the branch yet); `unimplemented`
  names a not-yet-built collector (`spend` for Codex, `coexisting_runs`). Today
  Codex contributes `quota` + `context_window`; Claude contributes `quota` via a
  cached `/usage` PTY scrape and `spend` + `context_window` via result JSON; and
  `remote_scm` reaches `known` when a PR is recorded. See
  `design-resident-boundary.md` §1/§8.
- Boundary interweave is now native hooks: Claude and Codex reach the same
  runner boundary via `brr hook <phase>`, so fresh resource state can be woven
  into the resident at seed/stop and, when attention changes, at post-tool.

The missing piece is not "add a model flag." A Shell/Core profile has at least:
command syntax, model, auth source, hook capability, quota source, cost class,
owner (`user` or `brnrd`), fallback eligibility, and billing policy. Hiding those
inside a shell command would keep the current blindness.

## What the CLIs expose today

Local probes in this repo:

| CLI | Observed version | Useful selection knobs | Useful cost/quota knobs | Gaps |
| --- | --- | --- | --- | --- |
| Codex CLI | `codex-cli 0.142.3` | `codex exec --model`, `-c model=...`, `--profile`, `--json`, `exec resume`, app-server commands | **Session-rollout `token_count` events (fire-verified 2026-06-28):** every `token_count` event in `$CODEX_HOME/sessions/.../rollout-*.jsonl` carries a `rate_limits` block — `primary` (5h: `used_percent`, `window_minutes:300`, `resets_at`), `secondary` (weekly: `10080`), `plan_type` — plus `info.model_context_window` + token usage. **This is exactly `/status`, on disk, no call/credits.** Subscription quota IS head-less-readable; `codex_status.py` reads the newest rollout's last `token_count` → wired into the facets. | Spend in $ is **not** handed over (subscription; tokens only → would need a price table). The `codex exec --json` *stdout stream* does NOT carry `rate_limits` (only `turn.completed` token `usage`) — quota lives only in the rollout file. Newest-rollout heuristic relies on single-flight. |
| Claude Code | `2.1.195` | `--model`, `--fallback-model`, named/resumable sessions, `--output-format json`, `--max-turns` | **Two wired seams (fire-verified 2026-06-28):** `--print --output-format json` carries `total_cost_usd` (spend), token `usage`, `modelUsage[model].contextWindow` (context), now wired by `claude_status.py`; interactive `/usage` in a PTY exposes subscription buckets (`Current session`, `Current week`), now cached by `claude_usage.py` and projected as `quota`. (`statusLine` is a TUI footer that **does NOT fire under `claude --print`**, so brr no longer registers it.) Also: `--max-budget-usd`, API rate-limit headers, Admin API reports. | Claude quota is **not head-less-native**: the working path is a best-effort TUI scrape (~15s live), cached by the daemon rather than run inside hooks. Result JSON still has no reset windows. |
| Gemini CLI | not installed in this environment | Bundled profile intent is `gemini -p --yolo`; docs/repo indicate hooks exist | Gemini API quotas are per project, measured across RPM/TPM/RPD plus spend-based limits, and billing/account caps sit at billing-account level. | No local firing/version probe here, so Gemini remains intent until installed and smoked. |

Implication: provider collectors must be **pluggable and provenance-tagged**.
Some signals are authoritative (brnrd wallet balance, API headers for brnrd-owned
keys). Some are best-effort (CLI error text, manually supplied snapshot). The
portal should show the source and freshness rather than pretending all quota
signals have equal quality.

## Decision: Shell/Core selection layer

Add a selection layer above static profiles. A profile remains "which Shell to
invoke and which Core to use." The selection layer is "when and why to prefer
this Shell/Core."

Field vocabulary sketch (config intent; adopted encoding is profile frontmatter
— see note below):

```toml
[runner]
policy = "cost-aware"
default_class = "economy"

[[runner.profiles]]
name = "codex-mini-local"
profile = "codex"
model = "gpt-5-codex-mini"
provider = "openai"
owner = "user"
class = "economy"
cost_rank = 10
quota_source = "codex-local"
hooks = "codex"

[[runner.profiles]]
name = "codex-strong-local"
profile = "codex"
model = "gpt-5.1-codex"
provider = "openai"
owner = "user"
class = "strong"
cost_rank = 40
quota_source = "codex-local"
hooks = "codex"

[[runner.profiles]]
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

> **Adopted encoding (2026-06-28): metadata on profiles, not a new TOML file.**
> The sketch above is the *full field vocabulary*, but
> `.brr/config` is a flat `key=value` reader (`config.py`) and cannot hold an
> array of tables. Rather than add a sectioned config parser (blast radius
> across every config consumer) or a second config file (more surface for the
> user to learn), the shipped foundation carries each field as an **optional
> frontmatter key on the existing `runners.md` profile** — which already parses
> arbitrary keys. A profile *is* a Shell/Core; the extra keys cost the user
> nothing until they want to retune. The richer dedicated-config form can still
> arrive later for accounts that declare profiles the host has no entry for
> (brnrd relay), but the *local* profiles that selection runs over need no new
> format.

This should align with the existing three-scope config model:

- **Project scope**: preferred policy, known runner profile names, project default class.
- **Local scope**: which local profiles are installed, machine-specific auth env
  names, `runner_cmd` escape hatches.
- **Account scope**: brnrd relay enabled/disabled, spending caps, wallet balance,
  user-wide runner preference.

Legacy compatibility can be narrow: the existing `shell=codex` becomes an
implicit runner profile named `codex-local` with unknown cost metadata. Since the
product is pre-release, do not preserve multiple spellings beyond the useful shim.

## Dispatch policy

The healthy default is not "always cheap" and not "always smart." It is a small
policy table whose inputs are cheap to know before the run:

| Situation | First choice | Escalation |
| --- | --- | --- |
| Small ask, explanation, narrow edit, docs lookup | `economy` local Shell/Core | Strong local only if the run asks to respawn or fails for quality, not for missing stdout. |
| Normal implementation | `economy` or `balanced` local Shell/Core, depending on repo default | Strong local on explicit user ask, repeated failed attempt, or resident-authored escalation. |
| Wide refactor, release, security, billing, destructive operation | `strong` local Shell/Core or PLAN first | Spend-plan before brnrd relay or managed paid path. |
| Local quota exhausted | Next local Shell/Core in same or lower cost class if available | brnrd relay only after spend-plan consent. |
| Provider/CLI outage | Different provider local Shell/Core if available | brnrd relay if it is a different failure domain and consent allows it. |
| No usable local runner | brnrd relay or queued setup guidance | spend-plan plus wallet/top-up route. |

The "respawn is handled by the more expensive model" part should be explicit:
an economy run may emit a **respawn request** rather than grinding. That request
is a parked portal: reason, proposed stronger Shell/Core, what context should carry
forward, and the spend/consent posture. The daemon then starts a new run on that
Shell/Core once policy allows it. This keeps cheap models useful without making
them responsible for deciding invisible billing.

Avoid reviving an LLM triage stage. The first selector should be deterministic
and conservative. The resident can escalate after it has read the repo and knows
the task is harder than the event body looked.

## Quota and credit signals

Use a three-grade signal model:

| Grade | Examples | How it enters brr |
| --- | --- | --- |
| Authoritative live | brnrd wallet/relay balance; response headers for brnrd-owned API keys; cloud provider quota API for a managed key | daemon collector writes structured snapshot with source, freshness, and reset windows |
| Provider/account historical | OpenAI organization usage/cost APIs; Anthropic Admin API reports; Google billing/usage views | async collector, never on prompt critical path; feeds historical pre-analysis |
| Readable local gauge (Codex) | **Codex session-rollout `token_count` events** — `rate_limits.{primary,secondary}` (5h/weekly subscription quota, used% + resets), `model_context_window` | `codex_status.py` reads the newest rollout's last event; on-disk, no `/status` call, no credits. *Wired* 2026-06-28. The real head-less subscription-quota gauge. |
| Readable local gauge (Claude) | **`claude --print --output-format json` result** — `total_cost_usd` (spend), `usage`, `modelUsage.contextWindow`; **interactive `/usage` PTY** — `Current session` + `Current week` subscription buckets | result JSON is terminal spend/context (`claude_status.py`); `/usage` is cached best-effort quota (`claude_usage.py`, 5-minute TTL), then hooks read portal-state rather than scraping directly. |
| Best effort local | Codex spend (needs a price table — no $ gauge); subscription error text; manually supplied `.brr/runner-quota.json` | snapshot or failure classifier, source marked best-effort |

The live portal state should carry this as structured data, not only a string:

```json
{
  "resources": {
    "runner": {
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

A custom runner can join the policy by declaring a profile:

```toml
[[runner.profiles]]
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
- The live card says the selected Shell/Core and quota posture.
- A spend-plan message gates paid relay or managed compute before spend.
- The dashboard/CLI can list configured runners, current quota source/freshness,
  relay balance, and fallback policy.

Example gate wording:

```text
Local Codex is out of weekly quota. I can continue with brnrd Codex relay.

Runner: gpt-5-codex-mini (codex Shell) via brnrd
Cap: $0.75 for this run
Billing: provider cost + 12% relay service fee, shown separately
Balance: $4.20 relay balance

Approve / Queue until local reset / Configure own runner
```

## Implementation sequence

1. **Data model only** *(shipped 2026-06-28, `runner_select.py`)*: Shell/Core
   profile schema, `implicit_runner()` legacy shim, and a deterministic
   conservative `select_runner()`. Shell/Core metadata rides the bundled profiles.
   No dispatch changes yet.
2. **CLI/display:** `brr runners list` shows profiles, owner, class, hooks, quota
   source, and known freshness.
3. **Portal upgrade:** replace the flat `resources.quota` string with structured
   `runner` resource while preserving the current compact hook line.
4. **Deterministic selector + user-facing knobs** *(shipped 2026-06-29)*:
   `resolve_runner()` now reads `shell=`/`core=` from `.brr/config` and uses
   `select_runner()` for cost-aware auto-detection. Legacy `runner=` still
   works. New tests cover all three paths. The flush-path latency fix also
   shipped here: `_collect_levels(refresh=False)` on the `_emit_flush` path
   prevents the ~18s PTY `/usage` scrape from blocking tool-boundary flushes;
   the heartbeat path keeps `refresh=True` and owns the cache refresh.
5. **Dynamic Core registry** *(shipped 2026-06-29, `runner_cores.py`)*:
   `available_cores()` returns `RunnerProfile` records for all Cores whose Shell
   binary is on PATH, from a bundled registry. Project `runners.md` entries
   extend/override the registry via the `extra=` parameter. No hardcoded model
   names in dispatch — updating the registry is the only brr change when a
   new model ships. `cores_for_shell()` lets a future CLI/display step list
   Cores per Shell without requiring the binary to be on PATH.
6. **Failure classifier:** distinguish quota, auth, provider outage, quality
   escalation, and no-response validation. Only quota/auth/provider errors enter
   fallback policy automatically.
7. **Respawn portal:** let a resident request a stronger Shell/Core with reason and
   carry-forward context.
8. **brnrd relay consent:** spending-plan prompt, wallet balance read, cap
   enforcement, audit rows. Codex/OpenAI first.
9. **Provider collectors:** async collectors for OpenAI, Anthropic, Gemini, each
   provenance-tagged; never block prompt assembly on network.
10. **Historical spend:** aggregate completed runs by runner and task shape for
    historical pre-analysis. Keep the existing guardrail: no projected dollar
    promise for local runs; paid relay gets a cap/quote envelope for consent.

## Standing portal candidates

These belong in `portal-state.json` and the hook seed/closeout capsule, not only
in prose:

- selected Shell/Core, class, model, owner, and fallback chain;
- Shell/Core capability proof (`hooks: codex`, fire-verified version, or
  fallback);
- local quota posture with source/freshness/confidence;
- brnrd relay/intelligence balance, per-run cap, and auto-approve policy;
- cost telemetry for this run so far, when available;
- whether an escalation/respawn has already been requested;
- whether paid relay consent is pending, approved, denied, or capped.

As this grows, `portal-state.json` likely deserves a small owner module rather
than staying as a large helper in `daemon.py`; the portal is becoming a product
surface, not just heartbeat state.

## Open questions / decisions pending (2026-06-28)

The foundation is wired; the dispatch/card/respawn slices each carry a call the
maintainer should make. Each has a recommended default so the next wake can
proceed unless redirected.

1. **Respawn trigger asymmetry — proactive is strongest for Codex; Claude is
   usable but cached.**
   Cost-aware *Shell/Core change* wants to fire before a wall, but the level seams
   are asymmetric: Codex exposes **cheap live** subscription quota (session
   rollout), while Claude exposes subscription quota through a cached
   interactive `/usage` PTY scrape. So a Claude-first run can see a quota wall
   before failure, but the probe is too heavy to run synchronously inside every
   hook. Its v1 posture should be proactive when the cached reading is fresh and
   reactive on classified quota/auth/provider **failure** when it is stale or
   absent. *Recommend:* accept this as v1 — cheap proactive for Codex, cached
   proactive for Claude, reactive fallback everywhere.

2. **Auto-respawn loop vs. parked request — depends on #128.** Fully automatic
   fallback (`runner: [a, b]`, retry on next Shell/Core) wants the run/event
   model's `defer_until` + re-claim (#128). *Recommend:* ship the **parked
   respawn request** first (the resident emits a `RespawnRequest` — reason,
   `proposed_runner`, carry-forward — to the outbox; the user re-sends on the
   chosen Shell/Core), which needs no #128. Promote to an automatic chain once
   #128 lands. This keeps the cheap models useful now without the daemon owning
   invisible billing decisions.

3. **What the deterministic v1 selector keys on.** The selector must stay
   conservative (no revived LLM triage). Today it keys on: explicit override →
   `runner_policy` → cheapest adequate local Shell/Core at/below `default_class`.
   *Open:* should v1 read *anything* from the event (source, body length) to pick
   a starting class? *Recommend:* **no** — start at the repo `default_class`
   (economy/balanced) and let the resident escalate via a `RespawnRequest` after
   it has read the repo and knows the task is harder than the body looked. Cheap,
   predictable, no body-heuristic guessing.

4. **`cost_rank` honesty.** The shipped ranks (gemini 10 < fable 15 < codex 25 <
   claude/sonnet 30 < opus 50) are **coarse relative ordering hints, not dollar
   figures** — they only decide tie-break order within a class. *Confirm:* this
   is the intended contract (selection ordering, never a price promise), and the
   exact numbers are brr defaults projects retune in their own `runners.md`.

5. **Card / portal exposure (the governance ask, evt-8q21).** The selected
   Shell/Core, class, model, and quota posture should surface in the status card
   and `portal-state.json` (a `runner` resource block — sketch in
   "Quota and credit signals" above). *Recommend:* extend the existing `resources`
   facet with a `runner` record (selected + fallbacks + quota source/freshness)
   and let the hook line carry a compact projection — the next slice, once the
   dispatch wiring chooses a Shell/Core to display.

6. **Reset windows (maintainer: "leave for now").** Satisfied for Codex
   (rollout `rate_limits.*.resets_at`, wired) and now best-effort for Claude via
   the `/usage` PTY scrape. *Confirm:* we accept the source-quality asymmetry
   (Codex native file vs. Claude TUI scrape) unless Anthropic exposes a first-
   class head-less seam.

## Sources

- Local probes (2026-06-28): `codex-cli 0.142.3`, `claude 2.1.195`, no `gemini`
  binary on this host.
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
