# Plan: control surface — the dashboard the engine shipped without

Status: active (opened 2026-06-29). Successor home for the reshape direction in
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
**Attempt-ledger rendering shipped 2026-06-29.** Progress cards now retain and
render failed runner attempts, quota/provider reasons, and fallback targets as a
compact attempt ledger.

The remaining CS2 half is to persist a **per-run status doc** (the run-state
object) carrying runner/core, **repo**, boundary, elapsed, commits, plan
position, attempt history. The card links to it. **Home reconciled (evt-puhl,
evt-qhk6):** the maintainer wants a larger, durable, beautifully-rendered
run-state object — so this lives in the **account dominion repo** (the
per-account home the resident's dominion consolidated into), the durable
brnrd-projectable store, not an ephemeral gist. See
`decision-account-centered-daemon.md` → "Account-scoped store".
("gist-per-run" was a placeholder for "a per-run state doc somewhere
web-visible.") Only the durable persistence half waits on the account repo
(CS4).

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

Also part of CS4 (the account repo itself, confirmed evt-qhk6): the daemon
**auto-creates** the account dominion repo on first `brr up`/install, with an
**override** to designate an existing repo (or stay purely local); the
account-scoped **dispatch inbox** (message-event queue the cheap dispatcher reads)
lives in that repo. The resident's dominion consolidates into it — no longer a
per-repo `brr-home` branch. See decision page → "Account-scoped store".

**First implementation slice shipped 2026-06-30.** The local daemon now resolves
an account context (`src/brr/account.py`) before dispatch: the current checkout
remains the default repo, `account.repo.<label>=<path>` registers additional
repos, `account.default_repo` selects the fallback, and a local account dominion
repo owns `account/repos.json`, `dispatch/inbox`, `dispatch/responses`, and
`run-state/<repo>/<run>.md`. The main loop scans repo-local inboxes plus the
account dispatch inbox, routes account message events by `repo:`/`repo_label`,
and keeps forge events direct when they appear in a registered repo's own inbox.
Run-state markdown documents are persisted under the account dominion. Manual
operator instructions for moving this repo's current `.brr/dominion` live in
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

Remaining CS4 work: move wake-time dominion injection/capture from the old
repo-local worktree to the account dominion path. This is the process-model
change that **waits on the operator migration** (`brr docs account-daemon`) —
the resident's memory plumbing should not be re-pointed mid-migration while the
live dominion still sits at `.brr/dominion`.

### CS5 — Inter-run plan home + injection
A web-visible plan store; the daemon preloads/auto-injects it into the wake the
way Recent Activity is injected (perception=injection, not a polling tax), and
surfaces it in the card + web view. **Two halves now resolved:** the *form* is an
**orphaned branch** (not a working-tree tracked file — it would pollute the user's
checkout), and the **cross-repo** store is the **account dominion repo**
(decision page, evt-puhl). **One narrow sub-fork remains:** whether *repo-scoped*
inter-run plans also live in the account dominion (tagged by repo — the simplest
shape, recommended now that the dominion is account-scoped) or ride a separate
per-repo `brr-plans` branch. Confirm that one cut with the maintainer before
building CS5.

### CS6 — Plain-language config + daemon-owned confirmation
Replace `shell=`/`core=`/`runner_policy=` knobs with: show the mandate (CS1), let
the user request changes in prose, the resident proposes a config change, a
*daemon-owned* confirmation step applies it (the resident cannot silently rewrite
its own selection policy). Standing preferences ("escalate to most capable")
become stored policy, not per-run flags. Review reshape step 4.

### CS7 — Cross-run decision/plan ledger
A user-facing through-line of recent decisions/definitions/plan-position so
coherent work stops feeling scrambled. `kb/log.md` is the resident's through-line;
this is its **user-facing projection**. Review reshape step 5; composes with CS5.

## Sequencing

CS1, CS2's card-rendering half, and CS3 shipped first because they were pure
projection / additive and made the existing engine legible without touching the
process model. CS4 is now the next architecture change (account daemon + account
dominion repo) and gates CS2's durable per-run status docs and CS5's cross-repo
half. CS6/CS7 are the richer UX and come last. Chunk across wakes per the
gardening plan's established cadence.

## Entry point for the next implementation run

The pure-projection runway is complete: CS1 shipped, CS2's attempt-ledger
rendering shipped, and CS3's repo label dimension shipped. CS4's first local
account-context slice has now landed: account registry, account dispatch inbox,
multi-repo event selection, direct repo-local forge routing, account run-state
docs, and the manual dominion move instructions.

No remaining CS4 fork waits on a maintainer decision: account daemon, `brr` as
the local verb, account dominion repo, auto-create-with-override, and account
dispatch inbox are all accepted. The run-state doc link is now web-visible
(`run_state_url`). The one remaining CS4 step — moving the resident's wake-time
dominion injection/capture onto the account dominion path — is **gated on the
operator running the `brr docs account-daemon` migration**, not on code. Only
CS5's narrow repo-scoped-plan-home cut and CS6/CS7's UX details remain later
decisions.

Concrete CS4 entry:

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

Acceptance for CS4: one account daemon can route at least two registered repos;
message events can target a repo through the dispatch inbox; forge events keep
their direct repo route; existing single-repo local installs still work; manual
operator instructions exist for moving this project's current dominion into the
new account home when the shape is functional.

## Companion pages

- [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md) — architecture.
- [`review-execution-model-coherence-2026-06-29.md`](review-execution-model-coherence-2026-06-29.md) — the framing review.
- [`plan-repo-gardening.md`](plan-repo-gardening.md) Part 2 — the engine half.
- [`design-runner-cores.md`](design-runner-cores.md) — dispatch policy.
- [`plan-resident-portals.md`](plan-resident-portals.md) — portal/injection plumbing for the view surface.
- [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md) — the brnrd projection of these surfaces.
