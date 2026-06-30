# Plan: control surface — the dashboard the engine shipped without

Status: shipped on 2026-07-01 (opened 2026-06-29; CS1-CS7 shipped by 2026-06-30; CS6b shipped 2026-07-01). Successor home for the reshape direction in
[`review-execution-model-coherence-2026-06-29.md`](review-execution-model-coherence-2026-06-29.md)
§3. Architecture: [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md).
The *engine* half lives in [`plan-repo-gardening.md`](plan-repo-gardening.md)
Part 2 (Core selection, fallback, escalation, relay); this plan is the *control
surface* over it.

## Why this exists

The execution-model review found the engine sound but the work *felt* scrambled
because there is **no control surface over it** — "we shipped the engine without
the dashboard." The maintainer's correction: ship them together. This plan turns
the review's five sequenced reshape steps into slices, now unblocked by the
account-daemon decision (the two forks are resolved). Each slice is reversible;
the first three are pure projection (no architecture change) and land first.

## Slices

### CS1 — Runner mandate facet (highest leverage, pure projection)
**Shipped 2026-06-29.** `claude-bare-api-only-*` profiles are generated from
the Core registry plus an auth-variant base profile, so the old static triplet in
`prompts/runners.md` no longer duplicates `_BUNDLED_CORES`. The runner mandate
now projects through both `resources.runner.catalog` in `portal-state.json` and
the Run Context Bundle's "Runner Mandate" section. The selectable Runner/Core
view uses one source (`available_runner_catalog()` over the generated profiles)
and marks the active Runner, answering "what may be selected?" for both the
user-facing surface and the resident's respawn decisions.

### CS2 — Persist + surface the per-run record
**Shipped across 2026-06-29/30.** Progress cards retain and render failed runner
attempts, quota/provider reasons, and fallback targets as a compact attempt
ledger. The larger **per-run status doc** (the run-state object) is persisted
under the **account dominion repo** with runner/core, repo, boundary, elapsed,
commits, plan position, and attempt history. The card records a forge-renderable
`run_state_url` when the account dominion has a remote, and otherwise falls back
to the doc basename so remote chat surfaces never leak host-local paths. Home
reconciled (evt-puhl, evt-qhk6): "gist-per-run" meant "a per-run state doc
somewhere web-visible"; the accepted home is the local-first account dominion
repo, not an ephemeral gist and not a forge repo created without opt-in. See
`decision-account-centered-daemon.md` → "Account-scoped store".

### CS3 — Repo dimension on runs/cards/activity
**Shipped 2026-06-29 for the local run-state surface.** Repo labels now flow
through task/run metadata, conversation run rows, live portal-state `run.repo`,
progress-card rendering, presence, schedule-created events, and respawn metadata.
This completes migration step 1 from the decision page — a repo dimension before
the account dimension, so run/card/activity surfaces can say which repo they
belong to without the process-model change yet. Cross-repo dispatch still belongs
to CS4.

### CS4 — Account daemon + cross-repo dispatcher
Lift the daemon from per-repo to per-account (decision page, migration step 2):
account config (forge identity + repo registry + default repo); `brr up` reads
it; the event loop selects `repo_root` per run. Add `RespawnRequest.repo` and the
**respawn-in-another-repo** dispatch (step 3). Route on two axes per the decision's
table: *which repo* (forge events repo-addressed at the gate → no dispatcher;
message events → dispatcher output) and *which Runner* (reuse the 2A Shell/Core
pin-skip path). Keep single-flight across repos for v1. **OSS invariant:
local-only first, brnrd projection additive.**

Also part of CS4 (the account repo itself, confirmed evt-qhk6; remote default
clarified 2026-06-30): the daemon auto-creates only a **local** account dominion
git repo on first `brr up`/install, with an override to designate an existing
repo/path. Remote durability is explicit: existing git remote now, user-approved
forge repo creation through OAuth later, and S3-compatible storage as a future
backend. The account-scoped **dispatch inbox** (message-event queue the cheap
dispatcher reads) lives in that repo. The resident's dominion consolidates into
it — no longer a per-repo `brr-home` branch. See decision page → "Account-scoped
store".

**First implementation slice shipped 2026-06-30.** The local daemon now resolves
an account context (`src/brr/account.py`) before dispatch: the current checkout
remains the default repo, `account.repo.<label>=<path>` registers additional
repos, `account.default_repo` selects the fallback, and a local account dominion
repo owns `account/repos.json`, `dispatch/inbox`, `dispatch/responses`, and
`run-state/<repo>/<run>.md`. The main loop scans repo-local inboxes plus the
account dispatch inbox, routes account message events by `repo:`/`repo_label`,
and keeps forge events direct when they appear in a registered repo's own inbox.
Run-state markdown documents are persisted under the account dominion. Manual
operator instructions for moving this repo's old `.brr/dominion` live in
`brr docs account-daemon`.

**Run-state URL projection shipped 2026-06-30.** `forges.view_blob_url`
projects a file committed to a forge-hosted repo to a clickable web URL, and
`account.run_state_blob_url` derives a run-state doc's URL from the account
dominion's remote. `_persist_run_state_doc` now records both `run_state_path`
(a host-local dev breadcrumb) and, when the dominion tracks a remote,
`run_state_url`; the progress card renders the URL when present and falls back
to the doc *basename* (never the absolute host path, which a remote chat reader
cannot open). A purely-local dominion simply carries no URL yet — the projection
is additive, lit by adding a remote, per the OSS invariant.

Also fixed in passing: the account context auto-created its store in the
developer's **real** `~/.local/state` during full-daemon tests, and a stale
registry there then leaked one test's `default_repo` into another (silently
no-op'd event routing). A `tests/conftest.py` autouse fixture now redirects
`XDG_STATE_HOME` per test, so the default account location is pristine and
disposable.

**Wake-time dominion rehome shipped 2026-06-30.** Prompt injection, matched
pitfalls, `schedule.md`, thread-of-record hints, and capture-at-sleep now prefer
`repos/<repo>/dominion/` inside the account dominion repo. `.brr/dominion` stays
readable/captured as a legacy fallback so partially migrated installs wake with
memory rather than going blank. Fresh default account homes use
`$XDG_STATE_HOME/brnrd/...`; an existing `$XDG_STATE_HOME/brr/...` account home
is accepted as a legacy fallback until the operator moves it or sets
`account.dominion_path`.

### CS5 — Inter-run plan home + injection
**Shipped 2026-06-30.** The sub-fork resolved: repo-scoped plans live in the
account dominion tagged by repo (simplest shape, consistent with the account-scoped
dominion). The narrow sub-fork of a separate per-repo `brr-plans` branch was
retired in favour of this single home. Implementation:

- `account.py`: `PLANS_PATH`, `repo_plans_path()`, `active_plan_path()`,
  `cross_repo_plans_path()` — the plan home helpers. `resolve_context` now creates
  `plans/` alongside the other account-store directories.
- `prompts.py`: `_build_inter_run_plan_block()` reads `plans/<repo-slug>/active.md`
  and `plans/_cross-repo/active.md` from the account dominion; returned as an
  injected block ("Active inter-run plan") between the dominion digest and pitfalls.
- Injection is **perception=injection**: the plan rides in automatically; the
  resident never polls for it. Silent when no plan file exists — never a
  constant tax.
- The resident writes/updates the plan file; retiring it is as simple as
  emptying or deleting `active.md`.

### CS6 — Plain-language config + daemon-owned confirmation
**Shipped 2026-06-30/2026-07-01.** The stored runner policy infrastructure and
daemon-owned confirmation loop are in place:

- `account.py`: `RUNNER_POLICY_PATH`, `runner_policy_path()` (repo-scoped),
  `account_runner_policy_path()` (account-wide, `runner-policy/_account/policy.md`).
- `prompts.py`: `_build_runner_policy_block()` reads both and injects a "Stored
  runner policy" block when either file is present.
- Operators can edit standing preferences directly; resident-originated changes
  flow through CS6b. The daemon injects the resulting files into each wake so
  the resident sees them when selecting a runner or proposing a respawn.

**CS6b shipped 2026-07-01:** resident-originated policy changes now use a
daemon-owned parked portal. The resident emits an outbox file with
`runner_policy: propose` frontmatter and the proposed markdown policy body.
The daemon parks it under `runner-policy/_proposals/<id>.md`, sends an approval
prompt to the conversation, and handles later `approve runner-policy <id>` /
`reject runner-policy <id>` replies before dispatching a runner. Approval is
conversation-scoped and is the only resident-originated path that mutates
`runner-policy/<repo>/policy.md` or `runner-policy/_account/policy.md`;
rejection leaves the policy unchanged. Direct operator file edits remain
possible, but the resident no longer silently rewrites its own selection policy.

### CS7 — Cross-run decision/plan ledger
**Shipped 2026-06-30 (storage + injection half).** The ledger home is
established:

- `account.py`: `LEDGER_PATH`, `decisions_ledger_path()` pointing at
  `ledger/decisions.md` in the account dominion.
- `prompts.py`: `_build_decision_ledger_block()` reads and injects the ledger when
  present as a "Decision ledger" block — the user-facing through-line alongside
  `kb/log.md`.
- The resident creates and maintains `ledger/decisions.md` with key decisions and
  current plan-position in plain language. Web-visible via the account dominion
  remote when one is configured — the local-first durability model.

**Composes with CS5:** the active plan (CS5) is the tactical "what we're doing
now"; the decision ledger (CS7) is the strategic "what we've decided and why".

## Sequencing

CS1, CS2, and CS3 shipped first because they were pure projection / additive and
made the existing engine legible without touching the process model. CS4 then
moved the architecture to the account daemon + account dominion repo, unlocking
CS2's durable run-state docs and the cross-repo home CS5 built on. CS5-CS7 landed
together in a single wake: the storage + injection infrastructure for plans,
runner policy, and the decision ledger is small and symmetric — three new block
builders wired into `_build_injected_blocks()`, three sets of path helpers in
`account.py`. CS6b then closed the remaining write-authority gap with a narrow
daemon-control event for policy proposal approval.

## Current checkpoint

All control-surface slices are now shipped. What landed across CS1-CS7 plus
CS6b:

- **CS1**: runner mandate catalog projected into every wake.
- **CS2**: per-run state docs persisted and linked from the card.
- **CS3**: repo label dimension across runs, cards, and activity records.
- **CS4**: account daemon + account dominion repo; multi-repo dispatch; wake-time
  resident memory rehomed into the account dominion.
- **CS5**: inter-run plan home (`plans/<repo-slug>/active.md`) injected each
  wake; cross-repo plans at `plans/_cross-repo/active.md`.
- **CS6**: runner policy store (`runner-policy/`) injected each wake.
- **CS6b**: runner-policy proposal files and daemon-owned approval/rejection
  handling; only approved resident-originated proposals mutate policy files.
- **CS7**: decision ledger (`ledger/decisions.md`) injected when the resident
  maintains it — the user-facing projection alongside `kb/log.md`.

CS4 shipped through four concrete cuts:

1. Add the account config/registry layer (`forge identity`, `repo registry`,
   default repo, account dominion repo location/override) while preserving the
   local-only OSS invariant.
2. Make `brr up` resolve an account context first, then select `repo_root` per
   run rather than assuming the daemon belongs to one checkout.
3. Land the message-event dispatcher path: account dispatch inbox in the account
   dominion repo, cheap routing to a target repo, forge events still
   repo-addressed at the gate.
4. Rehome durable run-state docs into the account dominion repo; the card can
   then link the larger run-state object that CS2 deferred.

Acceptance for CS4 is now met: one account daemon can route at least two
registered repos; message events can target a repo through the dispatch inbox;
forge events keep their direct repo route; existing single-repo local installs
still work; manual operator instructions exist for moving this project's current
dominion into the new account home; and wake-time resident memory now reads from
the account dominion path with the repo-local path as a legacy fallback.

## Companion pages

- [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md) — architecture.
- [`review-execution-model-coherence-2026-06-29.md`](review-execution-model-coherence-2026-06-29.md) — the framing review.
- [`plan-repo-gardening.md`](plan-repo-gardening.md) Part 2 — the engine half.
- [`design-runner-cores.md`](design-runner-cores.md) — dispatch policy.
- [`plan-resident-portals.md`](plan-resident-portals.md) — portal/injection plumbing for the view surface.
- [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md) — the brnrd projection of these surfaces.
