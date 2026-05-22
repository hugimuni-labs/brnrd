# Plan: managed gates — launch sequencing

Implementation plan for **Surface A** (managed gates / IO) of
[managed mode](subject-managed-mode.md), specified in
[`design-brr-run-protocol.md`](design-brr-run-protocol.md). Two
slices ship at launch, in this order: **GitHub App adapter first**
(largest BYO-setup pain relief), **Telegram bot adapter as
fast-follow** on the same backend.

Sister plan
[`plan-failover-compute.md`](plan-failover-compute.md) covers
Surfaces B (BYO failover compute) and C (managed compute) on top
of the same backend skeleton this plan stands up.

## Status

**Not started.** Blocked on:

- A small brr.run backend prototype (~3 days work) demonstrating
  the inbox-as-service protocol end-to-end (Telegram update →
  inbox → daemon poll → response → Telegram message). Most likely
  a FastAPI app + postgres on whichever PaaS is cheapest. Lives in
  a separate `brr-run` repo.
- [`design-brr-run-protocol.md`](design-brr-run-protocol.md)
  acceptance — the wire format needs to be locked before both
  sides start building in parallel.
- [`decision-pricing-shape.md`](decision-pricing-shape.md)
  acceptance — the launch needs a free-tier rate-cap floor
  decided before the gate adapters wire up rate limiting.

## Goals

- Both adapters reachable via `brr connect brr.run` on launch day.
- Launch announcement headlines both equally (TG + GH bot as the
  paid tier's two front doors).
- OSS paths for Telegram and GitHub gates remain 1:1 untouched —
  existing `[gate.telegram]` and `[gate.github]` config sections
  keep working exactly as today.

## Done definition

- The `cloud` gate adapter ships in `src/brr/gates/cloud.py`,
  registered alongside `telegram` / `slack` / `github` in the gate
  registry.
- `brr connect brr.run` CLI verb works end-to-end: signup, API key
  issuance, pairing for both TG and GH.
- One small docs page in `src/brr/docs/managed-mode.md`.
- Tests cover: long-poll happy path, long-poll timeout, response
  post, 401 on revoked key, restart-resume from persisted cursor.
- A sample brr.run backend skeleton is open-sourceable as a
  reference implementation in its own repo (so self-hosters can run
  their own brr.run-equivalent).

## Slice 1 — GitHub App adapter

The bigger pain-relief slice. Ship first.

**Steps:**

1. `src/brr/gates/cloud.py` — the cloud gate adapter (lifecycle,
   long-poll loop, response-post loop, cursor persistence). Common
   to both adapters: TG and GH both use this same gate; the
   webhook side is brr.run's concern, not the daemon's.
2. CLI plumbing: `brr connect brr.run`, `brr accounts pair github`.
   The pair verb opens the install URL in the user's browser and
   waits for brr.run to confirm the install webhook landed.
3. brr.run-side webhook receiver for `installation`,
   `installation_repositories`, `issue_comment`,
   `pull_request_review_comment` events; normalisation to the event
   shape from the design.
4. brr.run-side response forwarder: post comment / review reply on
   the originating PR / issue.
5. End-to-end smoke test: comment `@brr <task>` on a test repo →
   task lands in daemon inbox → daemon completes task → response
   posts back as a PR comment.

**Estimate.** ~600-800 LOC daemon-side (mostly the cloud gate
adapter, mostly shared with slice 2); ~500-700 LOC brr.run-side
(webhook handler + GitHub App JWT exchange + comment-post logic).

## Slice 2 — Telegram bot adapter (fast-follow)

One to two weeks after slice 1 ships. Reuses the brr.run backend
entirely; adds one webhook endpoint and one platform-specific
response formatter.

**Steps:**

1. brr.run-side webhook receiver for Telegram Bot API updates;
   normalisation to the same event shape used for GH.
2. brr.run-side response forwarder: post to `chat_id` via Telegram
   `sendMessage` API.
3. `brr accounts pair telegram` CLI flow (pairing-code path from
   the design).
4. Daemon-side: no new code — the cloud gate adapter handles TG
   events the same as GH events; the event shape is uniform.
5. Smoke test mirroring slice 1.

**Estimate.** Daemon-side ~0 new code (reuse from slice 1).
brr.run-side ~200-300 LOC.

## What ships where

| Component | Repo |
|-----------|------|
| `src/brr/gates/cloud.py` — cloud gate adapter | brr core |
| `brr connect brr.run` CLI verb | brr core |
| `brr accounts pair {telegram,github}` CLI verbs | brr core |
| `src/brr/docs/managed-mode.md` | brr core (bundled docs) |
| brr.run backend (FastAPI + postgres) | separate `brr-run` repo, OSS, reference implementation for self-hosters |
| Hosted bot operations (running `@brr_bot`, the brr.run GitHub App) | brr.run operator — not a code artifact |

## Out of scope

- Slack / Discord / GitLab adapters (same protocol, separate
  rollout — likely one to two months after launch each).
- The `fanout` multi-daemon routing policy (deferred per the
  design page).
- A web dashboard for managing daemons / bindings / pairings —
  CLI-first; dashboard is v-next.
- Payment / billing automation (manual invoicing for launch tier).

## Risks

- **Wire-format churn.** If
  [`design-brr-run-protocol.md`](design-brr-run-protocol.md)
  changes during the build, both sides need coordinated releases.
  Mitigation: lock the design with a `Status: accepted` banner
  before starting slice 1.
- **GitHub App approval delays.** Public GitHub Apps need a manual
  approval step for verified-creator badge; not blocking for
  launch but worth filing early.
- **Per-tenant blast radius.** A bug in brr.run's account scoping
  could leak events across accounts. Mitigation: query-level
  account context, integration tests per endpoint, audit logging
  from day one.

## Read next

1. [`design-brr-run-protocol.md`](design-brr-run-protocol.md) —
   the contract this plan implements.
2. [`plan-failover-compute.md`](plan-failover-compute.md) — the
   sister plan covering Surfaces B + C on top of the same
   backend skeleton this plan stands up.
3. [`subject-managed-mode.md`](subject-managed-mode.md) — the
   strategic context (three paid surfaces, work-continuity
   frame).
4. [`decision-pricing-shape.md`](decision-pricing-shape.md) — the
   pricing model that drives the free-tier rate caps.

## Lineage

- 2026-05-22 — drafted as part of the managed-mode KB shape
  rollout.
- 2026-05-22 — repointed at `design-brr-run-protocol.md` (renamed
  from `design-managed-gates.md`) and cross-linked to the new
  `plan-failover-compute.md` sister plan after the work-
  continuity reframe expanded the design's scope.
