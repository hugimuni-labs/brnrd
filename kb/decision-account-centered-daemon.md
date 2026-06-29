# Decision: account-centered daemon (one daemon per account, repo-scoped runs)

Status: accepted on 2026-06-29 (maintainer, evt-ogga)

Resolves the two architecture forks parked in
[`review-execution-model-coherence-2026-06-29.md`](review-execution-model-coherence-2026-06-29.md)
§3 ("Genuine forks — need a maintainer decision"). The control-surface work
this unblocks is sequenced in [`plan-control-surface.md`](plan-control-surface.md).

## Context

Today brr is **daemon-per-repo**: `brr up` runs a daemon bound to one
`repo_root`, and that path is threaded through the whole run pipeline
(`daemon.py` carries `repo_root` into branch resolution, presence, portals,
fallback selection). A second repo means a second daemon process.

The 2026-06-29 review named the felt incoherence precisely: we shipped the
*engine* (Core selection, automatic local fallback, quality escalation, relay
spend-consent) **without the dashboard** — a control surface over the engine.
It then parked two forks that the engine work kept bumping into:

1. **Daemon-per-account + cheap dispatcher** vs daemon-per-repo.
2. **Where inter-run plans live** (abandoned plans, plan changes, concurrent
   plans, cross-repo plans).

The maintainer resolved both (evt-ogga) and asked to etch the new shape:
*account-centered daemon, still OSS self-deployment friendly, cheap
answer-or-respawn handler, a user view surface, and a control plane —
engine and dashboard shipped together.*

Two prior decisions this rests on, already accepted:

- [`decision-brnrd-repo-first-model.md`](decision-brnrd-repo-first-model.md) —
  **Repo** is the durable user-facing object; runtime selection is a dispatch
  decision, not the stable identity of the work target. The account daemon is
  the daemon-side counterpart of that control-plane-side decision.
- [`plan-repo-gardening.md`](plan-repo-gardening.md) §2A — the dispatcher hop is
  **skipped when a Shell (or Shell+Core) is pinned**. This decision extends that
  rule with a repo axis rather than inventing a new one.

## The shape (decision)

One daemon. Five named parts.

### 1. Account daemon

One long-lived daemon per **account**, not per repo. The account is the forge
identity plus the laptop it runs on. It owns what is genuinely account-scoped:

- the **channels** — typically one Telegram channel per account (the common case
  the maintainer named: single TG channel per forge account per laptop);
- the **runner envelope** — the Shells+Cores this account may select/escalate
  into (one catalog, not per-repo);
- the **set of managed repos** and a **default repo**;
- **inter-run plans** (part 4) and the **view surface / control plane** bindings
  (part 5).

This matches how users actually run today. Daemon-per-repo forced N daemons for
N repos sharing one channel and one machine; the account is the real long-lived
unit.

### 2. Repo-scoped runs

Work still executes **in a repo's worktree**. The per-repo execution model
(worktree off a seed ref, `brr/<run-id>` branch, push/land) is unchanged — it
becomes a **run dimension the account daemon selects**, not a separate daemon.
Every run, card, and activity record **names its repo** (the maintainer's "the
status should also show the repo").

### 3. Cheap answer-or-respawn dispatcher

The initial wake for an *ambiguous message event* runs on a **cheap Shell/Core**,
scoped to the **default repo** (the repo-based selector stays). It does one of:

- **answers cheaply in place** (a question, a quick edit), or
- emits a **respawn request** naming the target Runner *and optionally a
  different repo* — **respawn-in-another-repo**. The account daemon consumes that
  request and dispatches the real work into the named repo's worktree.

This is the existing 2A dispatcher (`RespawnRequest`, parked handoff) **extended
with a repo axis**. No new mechanism: `RespawnRequest` already carries
`shell=`/`core=`/`defer_until`; it gains an optional `repo`.

### 4. Inter-run plan home

Plans that **outlive a single wake** (abandoned plans, plan changes, concurrent
plans) live **in the repo, known and visible**:

- **web-visible** (a tracked file the forge renders, not a hidden ref);
- **referenced in status cards**;
- **auto-injected and preloaded by the daemon between wakes** — handled in the
  seam *between* runs, the way the playbook and Recent Activity already are.

**Intra-run** plans stay wherever the runner wants (scratch in `.brr/`, the
dominion, a todo list) — they don't survive the wake, so they don't need a home.

**Cross-repo** inter-run plans are the case that genuinely *requires* the account
daemon: a repo-scoped home cannot carry a plan that spans repos; only the
account daemon sees all the repos at once. So the two forks are one shape — this
is why they resolve together.

Recommended physical location (refined with the maintainer, evt-ohsp): an
**orphaned branch**, surfaced by the daemon — *not* a working-tree tracked file.
This reverses an earlier draft that recommended a working-tree file. The maintainer
raised a valid objection: a tracked file in the main tree **pollutes the user's
repo** (it shows up in their checkout, PRs, and `git log`), and users may not want
brr's bookkeeping there. An orphaned branch threads the needle:

- it is **in the repo** (pushable, fetchable, web-visible at its branch URL,
  git-historied) — so it still passes the "known and visible" test;
- it is **not in the working tree** — zero pollution of the user's checkout;
- the earlier "invisible unless you know the ref" objection is answered by the
  **daemon surfacing it** (card link + auto-injection between wakes) — the user
  never needs the ref; brr brings the plan to them.

Crucially this **reuses brr's existing dominion pattern** (`brr-home` is already
an orphaned branch holding the resident's durable memory) rather than inventing a
new mechanism — a dedicated user-facing sibling (e.g. `brr-plans`), or a
designated namespace, kept distinct from the private dominion. A gist stays the
weakest option (off-repo, separate auth). Cross-repo plans, which can't live in a
single repo's branch, point at an **account-level** store (the account daemon's
own orphaned branch, or a designated home repo) — the case that genuinely needs
the account model. Final placement is the maintainer's call on execution.

### 5. User view surface + control plane

The dashboard the engine shipped without. Five surfaces, all reading **one**
source of state:

- **Runner envelope** — the catalog of selectable Shells+Cores (name, class,
  cost_rank, quota/availability, `selected`), not just the one selected runner.
- **Per-run record** — runner/core, repo, boundary, elapsed, commits, plan
  position, attempt history; persisted (gist-per-run), card links to it.
- **Attempt ledger** — render the `codex→claude-haiku` "ran out of quota, retried
  cheaper" story instead of letting `attempt_failed` reasons vanish.
- **Activity** — running + scheduled runs, **with repo shown** (the 2E view
  already uses `repo_id`).
- **Plain-language config + daemon-owned confirmation** — show the envelope, let
  the user request changes in prose, the resident proposes a config change, and a
  *daemon-owned* step applies it (the resident cannot silently rewrite its own
  selection policy).

**brnrd is the hosted projection of this surface; the local daemon serves the
same state standalone.** That is the next invariant.

## OSS self-deploy invariant

The account is an **organizing** concept, not a **cloud** dependency. The account
daemon must run fully on a laptop with no brnrd cloud:

- account = local forge identity + config;
- repos = local checkouts;
- view surface = local files / a local web view.

brnrd cloud is an **optional hosted projection + relay**, never required. This is
the load-bearing constraint that keeps the reshape from collapsing into a
managed-only feature. Every part above must have a local-only realization first;
the brnrd projection is additive.

## Two routing axes — don't conflate them (corrected, evt-w02y)

An earlier draft folded "which repo" and "which Runner" into one "skip the
dispatcher when routing is unambiguous" rule. The maintainer caught the conflict:
*if the dispatcher runs in a repo, you don't "skip" it to reach the right repo —
running it is how you reach the right repo.* Repo routing is the dispatcher's
**output**, never a precondition you bypass. The clean model has **two
independent axes**:

### Axis A — which repo (determined by the event source, not a bypass)

- **Forge events** (issue/PR comment) are **repo-addressed at the gate** — they
  arrive carrying repo identity, so they **never touch the cheap dispatcher**;
  they start directly in that repo's worktree. This is not "skipping" a
  dispatcher — there is no dispatcher in this path.
- **Channel/message events** (Telegram) have **no inherent repo**. They enter
  through the cheap dispatcher, which runs **in the default repo** and either
  keeps the work there or emits a **respawn-in-another-repo**. For message events,
  repo routing is the dispatcher's *output*, never a precondition.

So the cheap dispatcher is specifically a **message-event construct** for the
no-inherent-repo case. Forge events route by construction; message events route
through the dispatcher.

### Axis B — which Runner / Shell+Core (this is the only real "bypass" — the 2A rule)

- **Shell/Core pinned** (`shell=`/`core=`) → skip the cheap *intent-parsing* wake;
  go straight to the named Runner. No cheap model is needed to parse "run on
  Opus." For a message event with Shell/Core pinned, the work runs directly in the
  default (or channel-configured) repo, with respawn-in-another-repo still
  available from inside the real run.
- **Nothing pinned** → the entry wake selects conservatively / parses intent (the
  dispatcher for message events; the first run for forge events).

| Event source | Repo (axis A) | Runner (axis B) |
| --- | --- | --- |
| Forge (issue/PR) | the event's repo (no dispatcher) | pinned → direct; else conservative default |
| Message, Shell/Core pinned | default / channel repo (dispatcher's intent job not needed) | the pin |
| Message, nothing pinned | **cheap dispatcher** in default repo → keep or respawn-into-another-repo | dispatcher parses intent |

**Where the cross-repo dispatcher runs:** the account daemon is its *home* (it
owns cross-repo dispatch). The cheap wake itself runs as a normal repo-scoped run
on the **default repo** — that buys it a real working directory and repo context
cheaply; its respawn output is what the account daemon consumes to cross repos.

## Consequences / migration

- **`brr up` becomes account-scoped.** It binds an account, discovers/registers a
  set of repos, picks a default. **The simple case stays simple:** a single repo
  today = an account with one repo and that repo as default — no UX regression, no
  required new config for the common path.
- **`repo_root` becomes per-run, not daemon-global.** The event loop, presence
  registry (`presence.py`), schedule (`schedule.py`), and `portal-state.json`
  gain a repo dimension. This is the bulk of the mechanical work.
- **Cards/activity show repo.** Half-done already: the 2E activity view uses the
  accepted repo-first `repo_id` vocabulary.
- Ties forward to [`subject-daemon.md`](subject-daemon.md) (lifecycle),
  [`subject-managed-mode.md`](subject-managed-mode.md) (the brnrd projection),
  [`plan-resident-portals.md`](plan-resident-portals.md) (the view surface as
  injected/portal state), and [`design-runner-cores.md`](design-runner-cores.md)
  (dispatch policy).

## Implementation notes (for the next, ornamenting model)

Outline of the core; the next model ornaments. Sequenced cheap→deep, each
reversible. Detailed in [`plan-control-surface.md`](plan-control-surface.md);
the load-bearing notes:

1. **Add a repo dimension before adding an account dimension.** The lowest-risk
   first move is to make a single daemon handle a `repo` field on events/runs
   while still bound to one repo_root as default. That threads `repo` through
   `RespawnRequest`, presence, schedule, and portal-state *without* changing the
   process model yet. Ship the envelope/per-run-record/attempt-ledger view
   surface (review steps 1+3) on top of that — those are pure projection and
   don't need the account model.
2. **Then lift the daemon to account scope.** Introduce account config (forge
   identity + a repo registry + default repo). `brr up` reads it; the event loop
   selects `repo_root` per run from the event's repo (or default). Keep
   single-flight (one run at a time across all repos) for v1 — concurrency across
   repos is a separate decision, not a prerequisite.
3. **Dispatcher repo axis.** `RespawnRequest.repo` (optional); the cheap
   default-repo dispatcher may set it; the daemon dispatches into that repo's
   worktree. Bypass per the table above — reuse the 2A pin-skip code path,
   add the forge-event-carries-repo and repo-pin cases.
4. **Inter-run plan injection.** A tracked plan file per the recommendation;
   the daemon preloads it into the wake the way Recent Activity is injected
   (perception=injection — free, not a polling tax). Surface it in the card and
   the web view.
5. **Local-first view surface, brnrd projection additive.** Build each surface
   as local state first (a file/local web), then the brnrd `/v1/...` projection
   reads the same state. Never the reverse — that would violate the OSS invariant.

## Open questions (genuine — surface, don't force)

- **Inter-run plan physical location.** Refined (evt-ohsp): **orphaned branch**
  surfaced by the daemon, recommended over a working-tree tracked file (which
  pollutes the user's checkout) — reusing the dominion's `brr-home` pattern. Open
  on execution: dedicated `brr-plans` sibling vs a namespace; and where cross-repo
  plans live (account-level orphaned branch vs a designated home repo).
- **Multi-account on one laptop.** Rare; defer. The account model should not
  *forbid* it, but v1 need not support it.
- **Repo discovery.** Explicit registry (user adds repos) vs scanning a
  workspace dir. Lean explicit for predictability; decide on execution.
- **Cross-repo concurrency.** v1 stays single-flight across all repos. Whether an
  account daemon eventually runs repos concurrently ties to the #128 claim model
  and `concurrent-worktrees` work — a later decision.
