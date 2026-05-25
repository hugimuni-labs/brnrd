# Design: managed gates and BRNRD control plane

Status: active direction as of 2026-05-25 (planning/in-flight only; not shipped).

This page captures the architecture and product shape requested in
[issue #39](https://github.com/Gurio/brr/issues/39). It is the canonical
planning doc for optional managed mode, global gate routing, and cloud fallback.

## Problem statement and goals

`brr` works well as a repo-local daemon, but managed usage needs a control plane
that can route one global bot across multiple projects and optionally run tasks
in cloud fallback mode.

Goals for this direction:

- Keep self-hosted/local `brr` free and fully viable.
- Add optional managed mode that removes setup friction (especially bot setup and
  project routing).
- Keep execution host-first by default.
- Require explicit permission/policy before cloud execution.
- Make cloud spend visible (estimated and final) per run and in aggregate.
- Support multi-project dispatch from global connectors (Telegram/Slack/etc).
- Define a minimal 80/20 rollout that can ship incrementally.

## Contradictions with current assumptions/docs

This direction intentionally updates several older assumptions.

| Current assumption/docs | Why it conflicts with issue #39 direction | Resolution in this design |
| --- | --- | --- |
| `README.md` says "No database, no cloud" | Managed global routing + cloud failover require persistent control-plane state. | Reframe as **no mandatory cloud for self-hosted mode**; managed mode is optional. |
| [`subject-fleet-overlays.md`](subject-fleet-overlays.md) marks fleet/brnrd as paused future work | Managed gates and multi-project dispatch are now active direction. | Mark as active and point to this design as canonical. |
| Fleet notes treated `brnrd` mostly as future operator abstraction | #39 asks for concrete product + architecture now. | Define explicit split: `brr` execution plane vs `brnrd` control plane. |
| Gate setup currently implies near project-per-bot operation (especially Telegram) | Managed mode needs one bot identity multiplexing many projects. | Add managed routing contract with thread/project bindings and explicit overrides. |

## Proposed architecture split (`brr` execution plane vs `brnrd` control plane)

### `brr` execution plane (local/self-hosted or managed worker)

- Executes tasks in `host` / `worktree` / `docker` envs.
- Owns repo-local runtime artifacts (`.brr/inbox`, `.brr/responses`, traces,
  task files, branch publish behavior).
- Reports run telemetry and lifecycle events to caller.
- Can run on user host (default free path) or cloud worker host (managed path).

### `brnrd` control plane (managed or self-hosted service)

- Workspace/tenant + project registry.
- Connector installs and routing table.
- Conversation/thread to project bindings.
- Host health tracking and dispatch policy decisions.
- Cloud permission/cap policy enforcement.
- Run ledger for usage/cost analytics and billing.
- Dashboard/API for project list, recent runs, spend and duration views.

### Boundary and responsibility rule

- `brr` should stay execution-focused and repo-local in behavior.
- `brnrd` should own global state, global connectors, and cross-project policy.
- Cloud failover uses the same execution contract as local runs; only scheduler,
  permissions, and state ownership move to `brnrd`.

## Global bot -> project dispatch model (managed gates)

Routing key:

- `(workspace, project, conversation)`

Resolution order for every incoming managed-gate event:

1. Explicit project override in message/command (e.g. `/project set foo` or
   explicit `project:foo` hint).
2. Existing conversation binding (`thread/chat -> project`).
3. Sender default project (if configured).
4. Interactive disambiguation prompt with allowed project options.

Required managed gate commands (text-first UX):

- `/project` (list/select/current)
- `/where` (show current resolved project)
- `/cloud` (show/update cloud policy when allowed)
- `/cost` (show recent cost + runtime summary)

Behavior notes:

- Conversation bindings should be persisted centrally (`brnrd`), not inferred
  from local daemon state.
- Routing should fail closed on ambiguity (ask user), not silently guess.

## Cloud execution policy (host-first, permission gate, budget/cap policy)

Policy modes per workspace/project:

- `never`: never cloud-run; fail/queue when host unavailable.
- `ask` (default): prompt before each cloud fallback.
- `always_under_cap`: auto-run in cloud while within configured budget cap.

Execution flow:

1. Resolve project from managed gate routing.
2. Check healthy local host runner for project.
3. If host healthy, run locally immediately.
4. If host unavailable or over SLA/queue threshold, evaluate cloud policy.
5. If user approval/policy allows, run in cloud worker pool.
6. Record estimated cost before run and actual final cost after run.
7. Return result with env marker (`host` or `cloud`) and cost summary.

Approval response options in `ask` mode:

- run once
- always allow under cap
- deny

## Cost/telemetry model (estimated + final cost, durations, simple dashboard data)

Minimum run ledger fields:

- ids: `run_id`, `workspace_id`, `project_id`, `conversation_id`
- route metadata: gate type/source, resolved project path
- execution metadata: `env=host|cloud`, runner/model/provider
- timing: queued_at, started_at, completed_at, queue_ms, run_ms, wall_ms
- usage/cost: token counts (when available), `estimated_cost`, `final_cost`,
  currency, estimate_confidence
- policy metadata: cloud policy mode, approval source, cap values and status
- outcome metadata: success/failure/canceled + failure class

Operator-facing outputs:

- Per-run response footer: environment, duration, estimated/final cost.
- Text summaries (`/cost`, `/stats`) for recent period.
- Dashboard v1: recent runs table + spend over time + host/cloud split +
  median runtime.

## Pricing framing (generous free event tier + managed convenience pricing; self-hosted remains free)

Baseline framing:

- **Self-hosted/local path:** free.
- **Free managed tier:** generous dispatch allowance (for example 1000 events/mo)
  with host-first behavior.
- **Managed base plan:** convenience fee for hosted control plane (routing,
  dashboard, policy, easier bot setup).
- **Usage component:** cloud runtime + model usage pass-through with margin,
  guarded by budget caps and policy.

Pricing should avoid raw spawn-count billing as the primary metric; runtime and
model usage better track actual cost/risk.

## Connector segregation strategy (global vs project-scoped connectors)

Two required scopes now, with optional personal scope later:

- **Global/workspace connectors**
  - Example: Telegram/Slack bot identities used as managed gates.
  - Owned/configured in `brnrd`.
  - Routed to projects by dispatch rules and conversation bindings.
- **Project-scoped connectors**
  - Example: repo-specific tools, project webhooks, project-local integrations.
  - Bound directly to one project and evaluated by project policy.

Policy matrix should be scope-aware from day one (allowed actions, secret scope,
audit visibility) so connector growth stays predictable.

## Upsun constraints and deployment implications

Issue #39 calls out Upsun as likely prototype host with read-only app containers.
Implications:

- Treat app containers as stateless at runtime.
- Do not rely on local filesystem writes for durable control-plane state.
- Put durable state in managed services (database/queue/object store), not app
  container disk.
- Keep ephemeral runtime writes in temp paths only.
- Materialize runtime credentials/config at task start from secret store, then
  clean up after execution.
- Configure routes/integration wiring via build/deploy hooks because runtime
  container mutability is constrained.

This fits a split where `brnrd` API/dispatcher is stateless app logic backed by
external persistent services.

## MVP 80/20 phased rollout

### Phase 1 — managed dispatch, local execution only

- `brnrd` workspace/project registry.
- Global bot installs and managed routing commands (`/project`, `/where`).
- Conversation/thread project binding persistence.
- Dispatch to existing host `brr` daemons.

### Phase 2 — cloud fallback with explicit policy

- Host health + failover decision engine.
- Cloud policy modes (`never|ask|always_under_cap`).
- Consent prompts + cap checks.
- Cloud worker execution path using existing docker-style runtime contract.
- Per-run estimated and final cost capture.

### Phase 3 — lightweight dashboard and billing controls

- Dashboard with recent runs, durations, spend, host/cloud split.
- `/cost` and `/stats` summaries from ledger data.
- Budget alerts and basic plan/limit enforcement.

## Open questions

- What is the exact SLA/threshold that triggers "host unavailable" fallback?
- Which cost-estimation model is acceptable for each provider before final
  billing data lands?
- Should cloud policy defaults be workspace-level only, or allow per-project
  overrides in MVP?
- Which connectors must be global in MVP vs explicitly deferred?
- How much of `brnrd` should be self-hostable in first managed launch?
- Which dashboard metrics are mandatory for first paid conversion vs nice-to-have?
