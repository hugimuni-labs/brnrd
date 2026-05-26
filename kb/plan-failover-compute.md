# Plan: failover compute — brnrd-spawns-on-laptop-down

Implementation plan for the **managed-compute failover** surface
of [managed mode](subject-managed-mode.md): when a user's daemon
is offline and failover is enabled, brnrd spawns a per-task
sandbox in **its own** cloud account, decrypts the user's AI
credentials into the sandbox, runs the task, returns the response
via the originating gate, tears down.

The wire contract lives in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"Failover dispatch" + "Credential vault endpoints" +
"Permission-prompt endpoints"; the pricing shape lives in
[`decision-pricing-shape.md`](decision-pricing-shape.md).

**Status update on 2026-05-26 (third-wave follow-up):** the
credential vault has been generalised to host both AI-runner
credentials AND docker-registry credentials in one encrypted
store (`POST /v1/accounts/credentials` with a `kind`
discriminator), and the pricing model has been reshaped to a
platform subscription ($5/month, no marketing tier name —
just "Subscribed") + metered credits (for compute overage).
This plan still organises around the AI-credential vault as
the load-bearing slice-1 work because AI creds are required
for *every* spawn; docker-registry creds are optional (only
needed for private images) and slot in as a small extension
to slice 1. Subscription mechanics live in
[`design-billing.md`](design-billing.md) and don't materially
change the failover-spawn implementation surface — they only
shift the project-cap / event-cap / included-credits numbers
the dispatcher reads from account-scope settings.

## Status

**Not started.** Blocked on:

- `decision-pricing-shape.md` acceptance — the per-task
  accounting hooks the dispatcher emits feed the billing model;
  the tier shape (Free / Subscribed / metered overage) and the
  per-tier caps need to be locked before billing surfaces
  are committed.
- `design-brnrd-protocol.md` acceptance — the generalised
  credential vault endpoints (AI + docker-registry),
  subscription endpoints, failover-dispatch decision tree,
  and permission-prompt API need to lock before backend
  implementation starts.
- The brnrd backend skeleton from
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
  — failover compute extends that skeleton, doesn't precede it.

Ship order: managed gates (the dispatcher) → failover compute on
brnrd-owned Fly pool → **BYO Fly Machines for subscribers
(parallel-ships with managed Fly)** → dashboard surfaces for
usage / audit / permission prompts. Each subsequent cloud env
we add managed support for (Modal / Daytona / etc.) ships BYO
for that env in the same release.

**BYO at launch — subscriber-only, Fly Machines only**
(reframed on 2026-05-26 per the locking pass in
`decision-pricing-shape.md` and the BYO-compute section in
`design-brnrd-protocol.md`). The earlier 2026-05-25 framing
deferred BYO entirely; the current framing recognises that BYO
on top of an already-shipping managed env is a small
incremental (~one credential `kind` value, one dispatcher
branch on credential presence) given the env class is shared
between managed and BYO callers. **Policy: if we ship a cloud
managed, BYO ships in the same release; we never BYO-only-for-
clouds-we-don't-manage.** Free stays managed-only on purpose
(the sub is the gate, BYO is cost-saving, subscribing is the
cost-saving move). Implementation details live in
`design-brnrd-protocol.md` § "BYO compute — subscriber feature,
parallel-shipped with managed".

## Goals

- An `@brr` comment on GitHub (or a TG message) lands a working
  PR even if the user's laptop is asleep, as long as the user has
  uploaded an AI credential and failover is enabled.
- A user can set up failover in under 5 minutes:
  `brr brnrd connect` → (optional, otherwise stay Free)
  `brr brnrd subscribe` → `brr brnrd creds add anthropic
  --key sk-ant-...` (or `--dir ~/.claude`) → optional
  `brr brnrd creds add docker-registry --registry ghcr.io
  --username --token` if the project uses a private image →
  `brr brnrd policy set --enable --mode ask --monthly-cap 100`.
- Spawn-to-response latency under 90 seconds for the warm
  Fly-Machines case (cold image rebuild excluded).
- Permission-prompt round trip (event arrives → prompt posted via
  gate → user taps Approve → spawn starts) under 5 seconds added
  latency vs auto-approve.
- Per-task accounting hooks emit every spawn outcome (cost,
  duration, exit status, project_id) to an account-scoped audit
  log queryable via `brr brnrd audit`.
- Subscription-auth users (Claude Pro, Codex Plus, Gemini OAuth)
  can use failover without provisioning API keys, by uploading
  their credential directory once.
- Free-tier user (10 spawn-credits one-time signup bonus,
  30-day expiry) or subscriber (300 spawn-credits/month
  included) sees clear remaining-budget signal in every
  permission prompt and via `brr brnrd audit`.

## Done definition

- Generalised credential vault endpoints
  (`POST /v1/accounts/credentials`,
  `GET /v1/accounts/credentials`,
  `DELETE /v1/accounts/credentials/{id}`) live on brnrd
  with per-account envelope-key encryption. Four payload
  shapes accepted: `api-key` (AI runner), `dir-tarball` (AI
  runner, preserves Claude Pro / Codex Plus / Gemini OAuth
  subscription auth), `registry-userpass` (Docker registry),
  and `cloud-token` (BYO cloud, subscriber-only at launch).
  `kind` discriminator: `ai-anthropic` / `ai-openai` /
  `ai-google` / `ai-github` / `docker-registry` /
  `cloud-platform`. `cloud-platform` writes + reads gate on
  `subscription.tier == "subscribed"` (403 otherwise);
  `provider` field on the credential matches the env class
  at dispatch time (`fly` at launch).
- Failover-policy endpoints
  (`POST/GET /v1/accounts/failover-policy`) live with
  per-tier compute budget (Free: 10-credit one-time signup
  bonus, 30-day expiry, plus any purchased top-ups;
  Subscribed: 300 credits/month from the subscriber grant
  plus any purchased top-ups), monthly cost cap, and the
  **six approval modes** (`ask`, `auto-approve-always`,
  `auto-approve-under-usd`, `auto-approve-under-per-day`,
  `auto-approve-below-monthly-limit`, `never`). Per-tier
  launch defaults per
  [`decision-pricing-shape.md`](decision-pricing-shape.md) §
  "Launch-tunable knobs": Free defaults to **`ask`** (no
  monthly grant → no natural envelope to auto-approve
  within); Subscribed defaults to
  **`auto-approve-below-monthly-limit`** (the 300-credit
  monthly grant + any purchased balance is the natural
  envelope; auto-approve any spawn whose estimated cost
  fits, falls back to `ask` once exhausted).
- Subscription endpoints
  (`/v1/accounts/subscription[/checkout|cancel|resume|portal]`)
  live and the brnrd-side Stripe webhook receiver handles
  `customer.subscription.*` + `invoice.*` events to flip
  `subscription.tier` on the account-scope settings store.
  Subscriber caps applied by the dispatcher (project count,
  event ceiling, audit retention, included-credit grant) on
  next dispatch decision after a webhook event lands.
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
  per-account credential vault).
- Failover sandbox image: small Debian-slim base + the runner
  binary; sized to support both API-key and dir-tarball AI
  credential shapes (env vars OR credential dir expansion before
  runner spawn). Build-worker side runs `docker login` against
  the user's docker-registry credential (when present) before
  pulling a private image; the resulting image is what the
  sandbox sees — registry credentials never enter the sandbox.
- CLI surface:
  - `brr brnrd creds add <ai-provider> {--key | --dir}`
  - `brr brnrd creds add docker-registry --registry <host>
    --username <u> {--token | --password} <secret>`
  - `brr brnrd creds list [--kind <kind>]`
  - `brr brnrd creds remove <id>`
  - `brr brnrd policy --enable | --disable | --mode <m>
    | --monthly-cap N | --monthly-cost-cap-usd N
    | --auto-approve-under-usd N | --auto-approve-under-per-day N
    | --auto-approve-below-monthly-limit`
  - `brr brnrd subscription status | start | cancel | resume | portal`
    (+ `brr brnrd subscribe` as shortcut for `subscription start`)
  - `brr brnrd audit [--since <date>]`
- One-shot per-task `task-key` issuance and acceptance on
  `POST /v1/daemons/responses` so failover sandboxes can post
  responses without holding an account-level API key.
- Per-spawn GH App installation token issued for the spawn's
  duration (push permission scoped to one repo, one spawn).
- Documentation in `src/brr/docs/managed-mode.md`: walk-through
  for the `add-credential` → `failover --enable` flow plus a
  troubleshooting section for common failure modes (revoked AI
  credential, cap hit, sandbox crash, prompt timeout).
- Tests cover: credential encrypt / decrypt round-trip for
  all three shapes (`api-key`, `dir-tarball`, `registry-userpass`),
  `docker login` flow before a private-image pull, dispatcher
  decision tree per branch (including the tier-based caps for
  Free vs Subscribed), cap enforcement under concurrent load,
  permission-prompt resolution via gate callback, one-shot
  task-key acceptance, audit-log writes per spawn outcome,
  sandbox boot end-to-end against a test Fly app, Stripe
  webhook handling for subscription lifecycle events.

## Slices

### Slice 1 — Credential vault + failover policy

Plumb the storage layer first; nothing else makes sense without
it. The vault is generalised from day one (AI + docker-registry
in one store) — the per-account encryption envelope + audit
infrastructure is the same for both kinds, so building two
separate stores would be more work than building one.

Steps:

1. Per-account envelope-key generation on first credential write;
   root key bound to a KMS the application service can read but
   the database cannot.
2. `POST /v1/accounts/credentials` accepting three payload
   shapes against the `kind` discriminator:
   - AI runner — `{kind: "ai-anthropic"|"ai-openai"|"ai-google"
     |"ai-github", shape: "api-key", payload: "sk-..."}`
   - AI runner with subscription auth — `{kind: "ai-...",
     shape: "dir-tarball", payload: "<base64 of gzipped tar
     of credential dir>"}`
   - Docker registry — `{kind: "docker-registry", shape:
     "registry-userpass", host: "ghcr.io", payload: "<base64
     of username:token>"}`
   Each stored as encrypted blob; never returned on GET.
3. `GET /v1/accounts/credentials` returning credential metadata
   only (id, kind, shape, host, created_at, last_used_at).
   `?kind=` filter for the CLI's `--kind` flag.
4. `DELETE /v1/accounts/credentials/{id}` with audit-log entry;
   in-flight spawns complete cleanly, new spawns refuse if they
   would have used the revoked credential.
5. `POST/GET /v1/accounts/failover-policy` with the five approval
   modes, monthly_spawn_cap (defaults: 5 Free, 300 Subscribed —
   sourced from `subscription.tier`), monthly_cost_cap_usd,
   per-mode thresholds, and an `enabled` bit.
6. CLI surface:
   - `brr brnrd creds add anthropic --key sk-ant-...`
   - `brr brnrd creds add anthropic --dir ~/.claude`
   - `brr brnrd creds add docker-registry --registry ghcr.io
     --username myorg --token <ghcr-pat>` (CLI handles
     dir → tar + base64 and username/token → base64
     transparently)
   - `brr brnrd creds list [--kind <kind>]`
   - `brr brnrd creds remove`
   - `brr brnrd policy --enable|--disable|--mode|--monthly-cap`

**Estimate.** ~600-700 LOC backend (vault + policy with the
`kind`-discriminated path; +100 vs the AI-only shape) +
~250 LOC CLI (+50 for the `docker-registry` add path) +
~300 LOC tests (+50 for the registry shape + `docker login`
integration test).

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
   namespace from the per-account credential vault).
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
3. Spawn invocation flow on brnrd side (build/host worker):
   - decrypt the user's AI credentials into a process-memory
     buffer
   - read the cloned repo's `brr.toml` for project-scope
     config (per [`design-config-layout.md`](design-config-layout.md))
   - if `docker.image` references a private registry: look up
     a `docker-registry` credential matching the image's host,
     decrypt it, run `docker login <host>` on the build worker,
     `docker pull <image>`, clear the credential material from
     memory (the registry credential lives only in the build
     worker's `~/.docker/config.json` for the spawn's duration
     — never enters the sandbox)
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

1. `brr brnrd audit` CLI surface — paginated list of recent
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
4. `brr brnrd` CLI man-page-style help text with the common
   flows inline.

**Estimate.** ~200 LOC CLI + ~600 LOC docs + screenshots + 1
short demo recording.

## What ships where

| Component | Lives at |
|-----------|----------|
| Generalised credential vault (AI + docker-registry) + failover policy endpoints | `src/brnrd/` (monorepo backend) |
| Subscription endpoints + Stripe webhook receiver | `src/brnrd/` |
| Permission-prompt endpoints + gate-callback handlers | `src/brnrd/` |
| Dispatcher decision tree | `src/brnrd/` |
| Server-side Fly Machines spawn flow | `src/brnrd/` |
| Failover-sandbox Docker image | `src/brnrd/sandbox/` (built into Fly app on deploy) |
| `brr brnrd` CLI verbs | `src/brr/cli/brnrd.py` |
| Documentation | `src/brr/docs/managed-mode.md` (bundled with brr) |
| Managed Fly pool app + secrets | brnrd operator (runbook, not code) |
| Audit-log table + queries | `src/brnrd/` |
| Manual invoicing workflow at launch | brnrd operator (CSV exporter on backend, email template, payment processor account) |

Monorepo layout per
[`decision-monorepo-structure.md`](decision-monorepo-structure.md):
backend lives at `src/brnrd/` alongside `src/brr/` (the daemon
core), sharing the kb and the `pyproject.toml`. Self-hosters of
brnrd can target the same backend code against their own Fly
app + their own credential vault.

## Out of scope

- Slack / Discord / GitLab adapters for the gate side — those
  are in
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md);
  failover dispatches them the same way once they ship.
- Server-side spawn for *online* daemons (load-shedding); deferred
  per `design-brnrd-protocol.md` "Out of scope".
- **BYO platform tokens for non-Fly clouds** at launch
  (Modal / Daytona / Codespaces / etc.). Each cloud's BYO
  ships in the same release as its managed support per the
  one-for-one rule; only Fly is managed at launch, so only
  BYO Fly ships at launch. Subscriber-only; vault gate sits
  on the same `kind=cloud-platform` write+read paths. See
  `design-brnrd-protocol.md` § "BYO compute — subscriber
  feature, parallel-shipped with managed". Daemon-side cloud
  envs (a laptop daemon fans out to the user's cloud via a
  first-party env extra like `brr[fly]` or a third-party env
  registered via the `brr.envs` entry point) remain
  independent of managed mode entirely and ship per
  [`research-cloud-envs.md`](research-cloud-envs.md) on
  their own clock.
- Modal / Daytona / E2B / Codespaces server-side callers — those
  follow the same shape as Fly; each is a separate small plan
  once usage justifies a second managed-compute backend, and
  each lands managed + BYO together in the same release.
- Payments integration mechanics themselves (Stripe Checkout +
  Customer Portal + Webhook handling). Lives in
  [`design-billing.md`](design-billing.md); this plan only
  spends credits, it doesn't issue them.
- Web dashboard for credentials / audit log / billing — CLI-first
  for this plan; dashboard is in
  [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md).

## Risks

- **Credential blast radius.** A compromised brnrd database
  leaks per-account credentials (AI runner + docker-registry).
  Mitigation: per-account envelope keys for both kinds; root
  key in KMS separately from the application database;
  subscription-auth shape (dir-tarball) and registry credentials
  are similarly scoped to the user's account on the provider
  side; audit log surfaces unexpected spawn patterns + unexpected
  credential reads to the user quickly; revoke via single CLI
  verb propagates immediately.
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
  add a `brr brnrd creds test <id>` CLI that runs a noop
  task against the credential and surfaces auth errors before
  the user discovers them at failover time.
- **Permission prompt fatigue.** Mitigated by per-tier
  defaults per
  [`decision-pricing-shape.md`](decision-pricing-shape.md) §
  "Launch-tunable knobs": Free defaults to `ask` (the
  conservative default for accounts with no monthly compute
  envelope); Subscribed defaults to
  `auto-approve-below-monthly-limit`, which uses the existing
  300-credit monthly grant + any purchased balance as the
  natural auto-approve envelope. Subscribers don't see a
  permission prompt for routine in-budget spawns; they only
  see one when the monthly envelope is exhausted (which
  becomes the upsell moment: top up to keep auto-approving,
  or wait for the cycle reset). The first-prompt shortcut
  ("Never ask again under $X") still exists for users who
  want a tighter per-spawn cap on top of the monthly
  envelope.
- **Free-tier abuse.** 10 spawn-credits one-time per Free
  account is intentionally bounded by signup count, not by
  active-user retention — Free is the try-it-out path, the
  subscription is the "use it for real" path. Plus the
  binding-uniqueness rule (per `design-brnrd-protocol.md` §
  "Binding uniqueness") prevents multi-account abuse from
  the gate-routing angle. Abuse vector is
  small under this shape, but: account creation requires email
  verification; monitor for abuse patterns (high spawn rate
  against low-event-count accounts); rate-limit per-IP account
  creation. Subscribers have a 300-credit included grant — at
  typical task size that's ~100 spawns/month before they have
  to top up; abuse would manifest as unusually-high top-up
  rate, which is easy to spot.
- **Pricing margin too thin (managed-compute tier).** If
  wholesale cloud prices drift up and brnrd can't pass it
  through fast enough, margin compresses. Mitigation: monthly
  margin review pre-launch and per-quarter post-launch; build in
  a "margin floor" alert; the published rate in
  `decision-pricing-shape.md` includes a 30% buffer over current
  Fly Machines pricing.
- **Subscription tier under-priced or over-priced.** $5/month
  is set deliberately at the sub-$5 psychological threshold to
  bias toward conversion volume; if churn data shows it's too
  high (users picking Free + topping up over churn), drop to
  $4 or rebalance the included-credits / event-cap mix; if
  users want more features bundled, raise to $7. Mitigation:
  revisit at the 3-month mark with conversion + churn data;
  lock the launch number now and adjust based on signal.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   strategic frame.
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
   for the wire contract this plan implements (Failover
   dispatch + Credential vault endpoints (AI + docker-registry)
   + Subscription endpoints + Permission-prompt sections).
3. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   the billing model the per-task accounting hooks feed.
4. [`research-cloud-envs.md`](research-cloud-envs.md)
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
- 2026-05-25 (pass-4 follow-up — third wave) — slice 1
  reshaped around the **generalised credential vault** (the
  earlier `POST /v1/accounts/ai-credentials` endpoint became
  `POST /v1/accounts/credentials` with a `kind` discriminator
  spanning AI runners AND docker-registry credentials in one
  encrypted store). Slice 3 spawn flow gains a `docker login`
  + private-image pull step on the build worker (registry
  cred never enters the sandbox). CLI surface in the slice
  estimates updated to include
  `brr brnrd creds add docker-registry` and (in the third-wave
  draft) `brr brnrd plus [status|upgrade|...]` for the new
  subscription endpoints. Per-tier caps supplied to the
  dispatcher from `subscription.tier` on account-scope
  settings — sourced via the Stripe webhook receiver. Done
  definition extended with the subscription endpoints.
  "Free-tier abuse" risk reframed for the smaller Free cap +
  the new paid tier. Driven by the user's pricing-reframe +
  "want private images and credential dir mounting" feedback.
- 2026-05-26 (third-wave follow-up) — pricing + naming
  refinements applied through the plan:
  - Subscription price set to **$5/month** (was $9 in the
    third-wave draft); included compute set to **300
    spawn-credits/month** (was 500). Per-tier failover-policy
    caps + dispatcher reads updated accordingly.
  - Subscription CLI sub-verb family renamed from `brr brnrd
    plus [status|upgrade|downgrade|resume|portal]` to
    noun-first `brr brnrd subscription [status|start|cancel|
    resume|portal]` + `brr brnrd subscribe` shortcut for
    `subscription start`. CLI surface in slice 1 +
    onboarding flow in Goals updated.
  - Tier value names switched to `subscribed` /
    `subscribed_past_due` / `free` (drop "Plus" branding); CLI
    `status` output updated accordingly.
  - "Subscription tier under-priced / over-priced" risk
    re-anchored around the $5 + 300-credit shape; "Free-tier
    abuse" risk's subscription-side numbers updated.
  Driven by the user's "I don't like Plus as a name or verb;
  $5 a month with the credits to make up for it" feedback.
  Latest pondering breadcrumb in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
- 2026-05-26 (locking pass — BYO Fly at launch + credit
  buckets in the dispatcher path). **BYO Fly Machines added
  to launch scope** as a subscriber-only feature parallel-
  shipping with managed Fly. Credential vault shape extended
  (fourth `kind` value `cloud-platform`, `provider`
  discriminator); subscriber gate on write + read paths;
  dispatcher branches on BYO-cred presence at dispatch time
  (same env class, two callers — managed token vs decrypted
  user token). Ship order updated to reflect the parallel
  release. "BYO platform tokens" out-of-scope entry rewritten
  to make clear non-Fly BYO follows non-Fly managed support
  (one-for-one rule). Done-definition's credential-vault
  bullet extended with the fourth payload shape +
  subscriber-gate semantics. **Per-tier credit caps now read
  from the bucketed ledger** (per `design-billing.md` §
  "Credit buckets and expiry policy"): the per-tier
  effective compute budget is sourced from
  `free_monthly` *(later renamed `free_signup_bonus`)* /
  `subscriber_monthly` grant size + any `purchased` balance
  the user holds. Dispatcher's pre-spawn balance check walks
  the bucket priority order (`free_signup_bonus` →
  `subscriber_monthly` → `promotional` → `purchased`) before
  deciding to enqueue vs spawn. Driven by the user's "since
  we charge per paying customer we can allow byo everything
  on top of that" + "we probably also gonna have to expire
  granted credits somehow" framing.
- 2026-05-26 (locking pass II — Free signup bonus, project
  cap unlock). **Free compute math reframed** around the
  10-credit one-time signup bonus (30-day expiry) replacing
  the prior 5/month activity-gated recurring grant — bounded
  by signup count rather than active-user retention. Done-
  definition + Goals updated accordingly. **Subscriber
  project cap reshaped** from flat 10 to tiered 25 / unlimited
  (after $10 cumulative top-ups); permission-prompt + audit
  surfaces show the unlock threshold + progress. **Multi-
  account abuse mitigation framing** added to the Free-tier-
  abuse risk: binding uniqueness (per `design-brnrd-protocol.md`
  § "Binding uniqueness") closes the multi-account-creates-
  many-bonus-windows attack at zero extra implementation cost
  (uniqueness is needed for routing correctness anyway).
  Driven by the user's "lets allow subscribers to have
  unlimited as soon as they spent smth small but reasonable
  on credits" + "the one time grant on free is probably good"
  + "we maybe need to implement project ownership."
- 2026-05-26 (locking pass III — sixth approval mode +
  per-tier defaults). **Sixth approval mode
  `auto-approve-below-monthly-limit` added** alongside the
  existing five (`ask`, `auto-approve-always`,
  `auto-approve-under-usd`, `auto-approve-under-per-day`,
  `never`). Semantics: auto-approve any spawn whose estimated
  cost fits inside the account's remaining monthly grant +
  any purchased balance; fall back to `ask` once the
  envelope is exhausted, until cycle reset or a top-up. The
  per-tier launch defaults
  ([`decision-pricing-shape.md`](decision-pricing-shape.md) §
  "Launch-tunable knobs") move from "ask everyone" to "ask
  for Free, auto-approve-below-monthly-limit for Subscribed"
  — subscribers stop getting routine in-budget prompts; the
  prompt appears only at the natural upsell moment when the
  monthly envelope is exhausted. CLI flag
  `--auto-approve-below-monthly-limit` added to the
  `brr brnrd policy set` verb. Permission-prompt-fatigue
  risk section rewritten to point at the per-tier default
  shape as the mitigation. Driven by the user's
  "auto-approve-below-monthly-limit is a good idea for a
  user facing config property."
