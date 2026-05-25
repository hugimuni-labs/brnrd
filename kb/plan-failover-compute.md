# Plan: failover compute — brnrd-spawns-on-laptop-down

Implementation plan for the **managed-compute failover** surface
of [managed mode](subject-managed-mode.md): when a user's daemon
is offline and failover is enabled, brnrd spawns a per-task
sandbox in **its own** cloud account, decrypts the user's AI
credentials into the sandbox, runs the task, returns the response
via the originating gate, tears down.

The wire contract lives in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"Failover dispatch" + "AI-credential vault" + "Permission-prompt
endpoints"; the pricing shape lives in
[`decision-pricing-shape.md`](decision-pricing-shape.md).

## Status

**Not started.** Blocked on:

- `decision-pricing-shape.md` acceptance — the per-task
  accounting hooks the dispatcher emits feed the billing model;
  the pricing tier (free vs paid) and the free-tier cap need to
  be locked before billing surfaces are committed.
- `design-brnrd-protocol.md` acceptance — the AI-credential
  vault endpoints, failover-dispatch decision tree, and
  permission-prompt API need to lock before backend
  implementation starts.
- The brnrd backend skeleton from
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
  — failover compute extends that skeleton, doesn't precede it.

Ship order: managed gates (the dispatcher) → failover compute on
brnrd-owned Fly pool → dashboard surfaces for usage / audit /
permission prompts → (post-launch, if asked-for) BYO platform
tokens.

**BYO compute dropped from launch scope** on 2026-05-25 per
`decision-pricing-shape.md` updated rationale: implementation
surface area was disproportionate to the ~5% of users who'd value
it at launch. The wire protocol still supports BYO (designed,
deferred shape preserved in `design-brnrd-protocol.md`) so the
add-back is small when usage justifies it.

## Goals

- An `@brr` comment on GitHub (or a TG message) lands a working
  PR even if the user's laptop is asleep, as long as the user has
  uploaded an AI credential and failover is enabled.
- A user can set up failover in under 5 minutes:
  `brr accounts add-credential anthropic --key sk-ant-...` (or
  `--dir ~/.claude`) → `brr accounts failover --enable
  --mode ask --monthly-cap 100`.
- Spawn-to-response latency under 90 seconds for the warm
  Fly-Machines case (cold image rebuild excluded).
- Permission-prompt round trip (event arrives → prompt posted via
  gate → user taps Approve → spawn starts) under 5 seconds added
  latency vs auto-approve.
- Per-task accounting hooks emit every spawn outcome (cost,
  duration, exit status, project_id) to an account-scoped audit
  log queryable via `brr accounts audit`.
- Subscription-auth users (Claude Pro, Codex Plus, Gemini OAuth)
  can use failover without provisioning API keys, by uploading
  their credential directory once.
- Free-tier user with 100 spawns/month cap sees clear remaining-
  budget signal in every permission prompt and via `brr accounts
  audit`.

## Done definition

- AI-credential vault endpoints (
  `POST /v1/accounts/ai-credentials`,
  `GET /v1/accounts/ai-credentials`,
  `DELETE /v1/accounts/ai-credentials/{id}`) live on brnrd
  with per-account envelope-key encryption and both shapes
  (api-key + dir-tarball) accepted on the same endpoint.
- Failover-policy endpoints
  (`POST/GET /v1/accounts/failover-policy`) live with monthly
  spawn cap (default 100), monthly cost cap, and the five
  approval modes (`ask`, `auto-approve-always`, `auto-approve-
  under-usd`, `auto-approve-under-per-day`, `never`).
- Permission-prompt endpoints
  (`POST /v1/internal/prompts`, `PATCH /v1/internal/prompts/{id}`,
  `POST /v1/webhooks/prompts/...`) live and integrate with the
  GH App + Telegram gates from
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md).
- Failover-dispatch internal flow (decision tree per the design)
  runs in the brnrd dispatcher; the cap check + spawn
  reservation are serialised in a single transaction so a burst
  of events can't race past the cap.
- brnrd-owned Fly Machines pool registered, with a pool-control
  token in brnrd's own secret store (separate from the
  per-account AI-credential vault).
- Failover sandbox image: small Debian-slim base + the runner
  binary; sized to support both API-key and dir-tarball AI
  credential shapes (env vars OR credential dir expansion before
  runner spawn).
- CLI surface:
  - `brr accounts add-credential <provider> {--key | --dir}`
  - `brr accounts list-credentials`
  - `brr accounts remove-credential <id>`
  - `brr accounts failover --enable | --disable | --mode <m>
    | --monthly-cap N | --monthly-cost-cap-usd N
    | --auto-approve-under-usd N | --auto-approve-under-per-day N`
  - `brr accounts audit [--since <date>]`
- One-shot per-task `task-key` issuance and acceptance on
  `POST /v1/daemons/responses` so failover sandboxes can post
  responses without holding an account-level API key.
- Per-spawn GH App installation token issued for the spawn's
  duration (push permission scoped to one repo, one spawn).
- Documentation in `src/brr/docs/managed-mode.md`: walk-through
  for the `add-credential` → `failover --enable` flow plus a
  troubleshooting section for common failure modes (revoked AI
  credential, cap hit, sandbox crash, prompt timeout).
- Tests cover: AI-credential encrypt / decrypt round-trip for
  both shapes, dispatcher decision tree per branch, cap
  enforcement under concurrent load, permission-prompt resolution
  via gate callback, one-shot task-key acceptance, audit-log
  writes per spawn outcome, sandbox boot end-to-end against a
  test Fly app.

## Slices

### Slice 1 — AI-credential vault + failover policy

Plumb the storage layer first; nothing else makes sense without
it.

Steps:

1. Per-account envelope-key generation on first credential write;
   root key bound to a KMS the application service can read but
   the database cannot.
2. `POST /v1/accounts/ai-credentials` accepting both payload
   shapes:
   - `{provider, shape: "api-key", payload: "sk-..."}`
   - `{provider, shape: "dir-tarball", payload: "<base64 of
     gzipped tar of credential dir>"}`
   Each stored as encrypted blob; never returned on GET.
3. `GET /v1/accounts/ai-credentials` returning the credential
   metadata only (id, provider, shape, created_at, last_used_at).
4. `DELETE /v1/accounts/ai-credentials/{id}` with audit-log
   entry; in-flight spawns complete cleanly, new spawns refuse.
5. `POST/GET /v1/accounts/failover-policy` with the five approval
   modes, monthly_spawn_cap (default 100), monthly_cost_cap_usd,
   per-mode thresholds, and an `enabled` bit.
6. CLI surface:
   - `brr accounts add-credential` (both shapes; CLI handles
     dir → tar + base64 transparently)
   - `brr accounts list-credentials`
   - `brr accounts remove-credential`
   - `brr accounts failover --enable|--disable|--mode|--monthly-cap`

**Estimate.** ~500-600 LOC backend + ~200 LOC CLI + ~250 LOC
tests.

### Slice 2 — Dispatcher decision tree + permission-prompt API

The decision tree from the design, with the prompt-or-spawn fork
implemented end-to-end.

Steps:

1. Dispatcher decision tree (per the design's "Failover dispatch"
   diagram): event arrives → daemon online? → enqueue OR check
   failover policy → required-creds check → cap check → mode
   evaluation → spawn OR prompt OR enqueue.
2. Cap check + spawn reservation in one DB transaction; the
   running monthly counter and the reservation row are written
   atomically so concurrent events can't both consume the last
   spawn slot.
3. Permission-prompt internal endpoints:
   - `POST /v1/internal/prompts` — creates a prompt row (TTL
     6h, status=pending), surfaces it via the appropriate gate
     (TG message with inline buttons; GH issue comment with
     react-or-comment commands).
   - `PATCH /v1/internal/prompts/{id}` — internal update from
     the gate callback handler.
4. Permission-prompt external callback endpoints:
   - `POST /v1/webhooks/prompts/telegram/{prompt_id}/approve`
   - `POST /v1/webhooks/prompts/telegram/{prompt_id}/queue`
   - same shape for `github`
   Each verifies platform signing, marks the prompt resolved,
   and either fires the spawn or queues the event.
5. Per-event one-shot `task-key` issuance (Bearer token scoped
   to one `event_id`, 1-hour TTL, single use for
   `POST /v1/daemons/responses`).
6. Gate-side notification when failover fires
   ("queued task to managed failover — eta ~90s") so the user
   knows what's happening.

**Estimate.** ~700-900 LOC backend + ~300 LOC tests.

### Slice 3 — brnrd-owned Fly Machines pool + sandbox image

The compute side — managed pool, sandbox image, spawn flow.

Steps:

1. Operator-side: register the `brr-managed` Fly app, store the
   pool-control token in brnrd's own secret store (separate
   namespace from the per-account AI-credential vault).
2. Build the failover-sandbox Docker image:
   - Debian-slim base
   - runner binary (claude-cli, codex-cli, gemini-cli — all
     three preinstalled so one image serves all providers)
   - bootstrap script that:
     - reads task payload + AI credential material from env or
       mounted secret volume
     - if shape=dir-tarball, decodes + extracts to
       `$HOME/.claude/` (or provider-specific path)
     - if shape=api-key, exports as the provider's env var
     - clones the target repo using the per-spawn GH App
       installation token (or per-account deploy key for
       non-GH remotes)
     - invokes the runner CLI on the task body
     - on completion: pushes the branch, POSTs the response
       with the task-key, exits 0
     - on failure: writes orphan response to
       `.brr/failover-orphans/<event-id>.md` and pushes that,
       exits non-zero
3. Spawn invocation flow on brnrd side:
   - decrypt the user's AI credentials into a process-memory
     buffer
   - issue a per-spawn GH App installation token (scoped to one
     repo, 1-hour TTL)
   - create the Fly Machine with the task payload, AI
     credentials, GH token, task-key, project_id as secrets
   - return the spawn handle to the dispatcher
   - clear the AI-credential plaintext from memory
   - watch the machine to completion via Fly's machine API; on
     finish, record the outcome
4. Spawn-outcome accounting:
   `POST /v1/internal/spawns` on spawn start,
   `PATCH /v1/internal/spawns/{id}` on finish with cost and
   exit status. Roll into `account_usage_month` aggregate row.
5. Soft launch cap: pool concurrency capped to N (start with
   N=20) until usage patterns are known; concurrent requests
   over N get a friendly "managed pool busy, queued for ~60s"
   message via the gate.
6. Permission-prompt payload polish: include est_runtime,
   est_cost, current-month usage ("23/100 spawns used"), two
   buttons (Approve, Queue) and an inline link to raise cap.

**Estimate.** ~600-800 LOC backend + sandbox image (~200 LOC
shell) + operator-side pool setup (small, runbook-only) + ~300
LOC tests.

### Slice 4 — Audit log + documentation + onboarding polish

Cashes out the value into something a user can pick up.

Steps:

1. `brr accounts audit` CLI surface — paginated list of recent
   spawn-and-prompt events with timestamp, event_id, project,
   provider used, estimated cost, actual cost, exit status,
   approval mode applied.
2. `src/brr/docs/managed-mode.md` walk-through:
   - `add-credential` flow for each provider, both shapes
   - `failover --enable --mode ask` recommended default with
     the rationale
   - permission-prompt UX in TG and GH
   - monthly-cap mechanics and how to raise them
   - cost transparency (per-provider rates, per-spawn estimate)
3. Troubleshooting section: revoked AI credential, cap hit,
   sandbox crash, missing git remote, no branch push permission,
   prompt timeout.
4. `brr accounts` CLI man-page-style help text with the common
   flows inline.

**Estimate.** ~200 LOC CLI + ~600 LOC docs + screenshots + 1
short demo recording.

## What ships where

| Component | Lives at |
|-----------|----------|
| AI-credential vault + failover policy endpoints | `src/brnrd/` (monorepo backend) |
| Permission-prompt endpoints + gate-callback handlers | `src/brnrd/` |
| Dispatcher decision tree | `src/brnrd/` |
| Server-side Fly Machines spawn flow | `src/brnrd/` |
| Failover-sandbox Docker image | `src/brnrd/sandbox/` (built into Fly app on deploy) |
| `brr accounts` CLI verbs | `src/brr/cli/accounts.py` |
| Documentation | `src/brr/docs/managed-mode.md` (bundled with brr) |
| Managed Fly pool app + secrets | brnrd operator (runbook, not code) |
| Audit-log table + queries | `src/brnrd/` |
| Manual invoicing workflow at launch | brnrd operator (CSV exporter on backend, email template, payment processor account) |

Monorepo layout per
[`decision-monorepo-structure.md`](decision-monorepo-structure.md):
backend lives at `src/brnrd/` alongside `src/brr/` (the daemon
core), sharing the kb and the `pyproject.toml`. Self-hosters of
brnrd can target the same backend code against their own Fly
app + their own AI-credential vault.

## Out of scope

- Slack / Discord / GitLab adapters for the gate side — those
  are in
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md);
  failover dispatches them the same way once they ship.
- Server-side spawn for *online* daemons (load-shedding); deferred
  per `design-brnrd-protocol.md` "Out of scope".
- **BYO platform tokens** (Fly / Modal / Daytona / Codespaces /
  etc. tokens stored on brnrd, used to spawn in the user's own
  cloud). Wire shape is preserved in the design page as
  "designed, deferred"; add-back is small when usage justifies
  it. Daemon-side cloud-runner adapters (laptop fans out to
  user's cloud via a `brr-env-*` plugin) remain independent of
  managed mode entirely and ship per
  [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
  on their own clock.
- Modal / Daytona / E2B / Codespaces server-side callers — those
  follow the same shape as Fly; each is a separate small plan
  once usage justifies a second managed-compute backend.
- Payments integration (Stripe / Paddle / etc.); manual invoicing
  at launch is enough until usage justifies the integration cost.
- Web dashboard for credentials / audit log / billing — CLI-first
  for this plan; dashboard is in
  [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md).

## Risks

- **AI-credential blast radius.** A compromised brnrd database
  leaks per-account AI credentials. Mitigation: per-account
  envelope keys; root key in KMS separately from the application
  database; subscription-auth shape (dir-tarball) is similarly
  scoped to the user's account on the provider side; audit log
  surfaces unexpected spawn patterns to the user quickly; revoke
  via single CLI verb propagates immediately.
- **Cost-cap evasion via concurrent spawns.** A burst of events
  could race past the monthly cap if the spawn-start check isn't
  serialised properly. Mitigation: serialise cap check + spawn
  reservation in one transaction (see Slice 2); revisit the rate
  cap on failover spawns (default 3/min) if races still occur.
- **Sandbox push permission.** The spawned sandbox needs to push
  the resulting branch back. For GitHub-hosted repos, the GH App
  install delegates this cleanly via a per-spawn installation
  token. For non-GitHub remotes, the user needs to provision a
  per-account deploy key — extra setup step. Mitigation: surface
  this in onboarding, default-disable failover for non-GitHub
  remotes until the user opts in.
- **Cold-start variance.** Fly Machines warm-image spawn is
  ~300ms but a cold image rebuild can take tens of seconds. Users
  expecting "instant" failover may be confused. Mitigation:
  gate-side notification ("spawning sandbox, ~90s") at dispatch;
  surface cold-start time in the audit log so users can see when
  it happens; keep at least 2 warm machines in the pool.
- **Subscription-auth fragility.** Anthropic / OpenAI / Google
  may invalidate session-style auth on IP change or device
  change, causing dir-tarball-shape credentials to break after
  upload. Mitigation: document the API-key fallback prominently;
  add a `brr accounts test-credential <id>` CLI that runs a noop
  task against the credential and surfaces auth errors before
  the user discovers them at failover time.
- **Permission prompt fatigue.** If `ask` is the default mode,
  users get a prompt every time their laptop is asleep — fast
  path to disable failover or hit "auto-approve-always" without
  reading the cost. Mitigation: default to `ask` with a clear
  "Never ask again under $X" shortcut on the first prompt; nudge
  toward `auto-approve-under-usd` mode after first approve.
- **Free-tier abuse.** 100 spawns/month per account is generous
  for a fallback feature; could be abused as a free "agents in
  the cloud" service. Mitigation: cap is per-account, account
  creation requires email verification; monitor for abuse
  patterns (high spawn rate against low-event-count accounts);
  rate-limit per-IP account creation.
- **Pricing margin too thin (managed-compute tier).** If
  wholesale cloud prices drift up and brnrd can't pass it
  through fast enough, margin compresses. Mitigation: monthly
  margin review pre-launch and per-quarter post-launch; build in
  a "margin floor" alert; the published rate in
  `decision-pricing-shape.md` includes a 30% buffer over current
  Fly Machines pricing.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   strategic frame.
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
   for the wire contract this plan implements (Failover dispatch
   + AI-credential vault + Permission-prompt sections).
3. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   the billing model the per-task accounting hooks feed.
4. [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
   for the cross-adapter patterns the server-side Fly caller is
   one instance of.
5. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   for the gate-side work the prompt callbacks integrate with.
6. [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
   for the dashboard view on top of the audit log + cap + cost
   surfaces.
7. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
   for where `src/brnrd/` lives and how it relates to the
   daemon core.

## Lineage

- 2026-05-22 — drafted (then covering both BYO and managed
  compute as Surfaces B and C) as part of the work-continuity
  reframe. Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  (reframe breadcrumb: always-on-box demoted, brnrd-as-
  failover-dispatcher is the answer).
- 2026-05-25 — rewritten: BYO scope dropped at launch (preserved
  as designed-deferred sketch in `design-brnrd-protocol.md`);
  refocused on AI-credential vault (api-key + dir-tarball shapes
  on one endpoint), brnrd-owned Fly pool, permission-prompt
  API, monthly cap default of 100 spawns/month, and Upsun
  backend deployment notes. Third reframe breadcrumb in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
