# Plan: failover compute — brr.run-spawns-on-laptop-down

Implementation plan for Surfaces B (BYO failover compute) and C
(managed compute) of [managed mode](subject-managed-mode.md). Both
ride the same code path; the difference is whose cloud token is
used and who pays the cloud bill. The wire contract lives in
[`design-brr-run-protocol.md`](design-brr-run-protocol.md) →
"Failover dispatch" + "Cloud-token security model"; the pricing
shape lives in
[`decision-pricing-shape.md`](decision-pricing-shape.md).

## Status

**Not started.** Blocked on:

- `decision-pricing-shape.md` acceptance — the per-task accounting
  hooks the dispatcher emits feed the billing model; the pricing
  needs to be locked before billing surfaces are committed.
- `design-brr-run-protocol.md` acceptance — the cloud-credential
  endpoints and failover-dispatch decision tree need to lock
  before backend implementation starts.
- The brr.run backend skeleton from
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
  — failover compute extends that skeleton, doesn't precede it.

Ship order: Surface A (gates) → Surface B (BYO failover) →
Surface C (managed compute). Each builds on the previous; B and
C reuse 90% of each other's code, separated only by which token
the dispatcher picks.

## Goals

- An `@brr` comment on GitHub (or a TG message) lands a working
  PR even if the user's laptop is asleep, as long as failover is
  enabled and the user has a working cloud credential on file.
- A user can set up Surface B in under 5 minutes:
  `brr accounts add-credential fly` → paste token →
  `brr accounts failover --enable --platform fly`.
- Surface C is available as a flag (`--platform brr-managed`) on
  the same CLI verb, charging the user's account at the rate
  published in
  [`decision-pricing-shape.md`](decision-pricing-shape.md).
- Spawn-to-response latency under 90 seconds for the warm Fly
  Machines case (cold image rebuild excluded).
- Per-task accounting hooks emit every spawn outcome (cost,
  duration, platform, exit status) to an account-scoped audit
  log queryable via `brr accounts audit`.

## Done definition

- Cloud-credential storage endpoints (
  `POST /v1/accounts/cloud-credentials`,
  `GET /v1/accounts/cloud-credentials`,
  `DELETE /v1/accounts/cloud-credentials/{id}`) live on brr.run
  with per-account envelope-key encryption.
- Failover-policy endpoints
  (`POST/GET /v1/accounts/failover-policy`) live on brr.run with
  monthly spawn cap + monthly cost cap enforcement.
- Failover-dispatch internal flow (decision tree per the design)
  runs in the brr.run dispatcher.
- One server-side cloud-runner adapter caller wired up
  (`FlyMachineEnv` from
  [`plan-env-fly-machines.md`](plan-env-fly-machines.md)),
  reusing the adapter code from the daemon path.
- CLI surface: `brr accounts add-credential <platform>`,
  `brr accounts list-credentials`,
  `brr accounts remove-credential <id>`,
  `brr accounts failover --enable|--disable|--platform|--monthly-cap|--monthly-cost-cap`,
  `brr accounts audit`.
- One-shot per-task `task-key` issuance and acceptance on
  `POST /v1/daemons/responses` so failover sandboxes can post
  responses without holding an account-level API key.
- `brr-managed` pseudo-platform wired into the failover policy
  for Surface C; brr.run-side pool of pre-warmed Fly Machines (or
  similar) sized small for launch.
- Documentation in `src/brr/docs/managed-mode.md`: walk-through
  for the `add-credential` → `failover --enable` flow plus a
  troubleshooting section for common failure modes (revoked
  token, cap hit, sandbox crash).
- Tests cover: credential encrypt / decrypt round-trip,
  dispatcher decision tree per branch, cap enforcement, one-shot
  task-key acceptance, audit-log writes per spawn outcome.

## Slices

### Slice 1 — Cloud-credential storage + failover policy

Plumb the storage layer first; nothing else makes sense without
it.

Steps:

1. Per-account envelope-key generation on first credential write;
   root key bound to a KMS the application service can read but
   the database cannot.
2. `POST /v1/accounts/cloud-credentials` accepting
   `{platform, token, scope}` and storing encrypted; never
   returning the secret material on any GET endpoint after.
3. `GET /v1/accounts/cloud-credentials` returning the credential
   metadata only (id, platform, created_at, last_used_at, scope
   summary).
4. `DELETE /v1/accounts/cloud-credentials/{id}` with audit-log
   entry; in-flight spawns complete cleanly, new spawns refuse.
5. `POST/GET /v1/accounts/failover-policy` with monthly_spawn_cap,
   monthly_cost_cap_usd, preferred_platform, enabled bit.
6. CLI surface: `brr accounts add-credential`, list, remove,
   `brr accounts failover --enable|--disable|--monthly-cap`.

**Estimate.** ~400-500 LOC backend + ~150 LOC CLI + ~200 LOC
tests.

### Slice 2 — Dispatcher decision tree + Fly server-side caller

Wire the failover-spawn path end-to-end with one adapter.

Steps:

1. Dispatcher decision tree (per the design's "Failover dispatch"
   diagram): event arrives → daemon online? → enqueue OR check
   failover policy → cap check → spawn.
2. Per-event one-shot task-key issuance (Bearer token scoped to
   one `event_id`, 1-hour TTL, single use for
   `POST /v1/daemons/responses`).
3. Server-side `FlyMachineEnv` caller — same code as the daemon
   adapter (from
   [`plan-env-fly-machines.md`](plan-env-fly-machines.md)),
   called with the decrypted token from credential storage and
   passed the event payload + task-key as environment.
4. Failover sandbox boot script: clone repo, run runner CLI on
   the event task, push branch, POST response, exit.
5. Spawn-outcome accounting:
   `POST /v1/internal/spawns` on spawn start,
   `PATCH /v1/internal/spawns/{id}` on finish with cost and exit
   status. Roll into `account_usage_month` aggregate row.
6. Gate-side notification when failover fires
   ("queued task to Fly Machines failover — eta ~90s") so the
   user knows what's happening.

**Estimate.** ~600-800 LOC backend + reuses the cloud-runner
adapter from the daemon-side plan + ~300 LOC tests.

### Slice 3 — Surface C: managed compute pool

The third paid surface. Implementation-wise nearly free given
slice 2: brr.run holds its own Fly token, registers a synthetic
`brr-managed` platform in the failover policy options, and routes
to its own pool when selected.

Steps:

1. Operator-side: register the `brr-managed` Fly app, store the
   pool-control token in brr.run's own secret store (not the
   per-account credential store).
2. Wire `platform = "brr-managed"` into the failover-policy
   acceptance set; dispatch routes to the pool spawn path
   instead of the per-account credential path.
3. Per-task billing accounting using the published margin from
   `decision-pricing-shape.md`. Roll into a `account_billing` row
   per month for invoicing.
4. Soft launch cap: pool size capped to N concurrent machines
   until usage patterns are known; hard 429 with friendly message
   over cap.
5. CLI surface: same `brr accounts failover --platform brr-managed`
   — no new verb needed.
6. Manual invoicing workflow at launch (skip payments
   integration); CSV export from `account_billing` to
   send-an-email-to-pay flow until usage justifies Stripe.

**Estimate.** ~200-300 LOC backend on top of slice 2 + the
operator-side pool setup (small) + the invoicing workflow
(content + CSV exporter, ~100 LOC).

### Slice 4 — Documentation + onboarding polish

Cashes out the value into something a user can actually pick up.

Steps:

1. `src/brr/docs/managed-mode.md` walk-through covering both BYO
   and managed-compute paths.
2. Per-platform credential-generation guides for the supported
   adapters (Fly is the first; others follow as adapter plans
   ship).
3. Troubleshooting section: revoked token, cap hit, sandbox
   crash, missing git remote, no branch push permission.
4. `brr accounts` CLI man-page-style help text with the common
   flows inline.

**Estimate.** ~600 LOC docs + screenshots + 1 short demo
recording.

## What ships where

| Component | Repo |
|-----------|------|
| Credential storage + failover policy endpoints | brr.run backend (separate repo) |
| Dispatcher decision tree | brr.run backend |
| Server-side `FlyMachineEnv` caller | brr.run backend (imports from brr-env-fly-machines as a library) |
| `brr accounts` CLI verbs (add/list/remove credential, failover, audit) | brr core |
| Documentation | brr core (bundled docs) |
| `brr-managed` pool operations | brr.run operator — not a code artifact |
| Manual invoicing workflow | brr.run operator (CSV exporter on backend, email template, payment processor account) |

## Out of scope

- Slack / Discord / GitLab adapters for the gate side — those are
  in
  [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md);
  failover dispatches them the same way once they ship.
- Server-side spawn for *online* daemons (load-shedding); deferred
  per `design-brr-run-protocol.md` "Out of scope".
- Modal / Daytona / E2B / Codespaces server-side callers — those
  follow the same shape as Fly; each is a separate small plan
  once that adapter ships its daemon-side plugin.
- Payments integration (Stripe / Paddle / etc.); manual invoicing
  at launch is enough until usage justifies the integration cost.
- Web dashboard for credentials / audit log / billing — CLI-first;
  dashboard is v-next.

## Risks

- **Cloud-credential blast radius.** A compromised brr.run
  database leaks per-platform tokens whose scope is the load-
  bearing security boundary. Mitigation: aggressive scope
  minimisation in onboarding docs (one Fly app, codespace-only
  PAT, etc.); per-account envelope keys; root key in KMS
  separately; audit log surfaces unexpected spawn patterns to
  the user quickly.
- **Cost-cap evasion via concurrent spawns.** A burst of events
  could race past the monthly cap if the spawn-start check isn't
  serialised properly. Mitigation: serialise cap check + spawn
  reservation in one transaction; revisit the rate cap on
  failover spawns (default 5/min) if races still occur.
- **Sandbox push permission.** The spawned sandbox needs to push
  the resulting branch back. For GitHub-hosted repos, the GH App
  install delegates this cleanly. For non-GitHub remotes, the
  user needs to provision a per-account deploy key — extra setup
  step. Mitigation: surface this in onboarding, default-disable
  failover for non-GitHub remotes until the user opts in.
- **Cold-start variance.** Fly Machines warm-image spawn is
  ~300ms but a cold image rebuild can take tens of seconds. Users
  expecting "instant" failover may be confused. Mitigation:
  gate-side notification ("spawning sandbox, ~90s") at dispatch;
  surface cold-start time in the audit log so users can see when
  it happens.
- **Pricing margin too thin.** If wholesale cloud prices drift up
  and brr.run can't pass it through fast enough, margin
  compresses. Mitigation: monthly margin review pre-launch and
  per-quarter post-launch; build in a "margin floor" alert.

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   strategic frame.
2. [`design-brr-run-protocol.md`](design-brr-run-protocol.md)
   for the wire contract this plan implements (Failover dispatch
   + Cloud-token security model sections).
3. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   the billing model the per-task accounting hooks feed.
4. [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
   for the cross-adapter patterns each server-side caller uses.
5. [`plan-env-fly-machines.md`](plan-env-fly-machines.md) for the
   first daemon-side adapter that the server-side caller reuses.

## Lineage

- 2026-05-22 — drafted as part of the work-continuity reframe.
  Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  (reframe breadcrumb: always-on-box demoted, brr.run-as-
  failover-dispatcher is the answer).
