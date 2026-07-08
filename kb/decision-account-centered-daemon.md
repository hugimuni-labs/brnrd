# Decision: account-centered daemon (one daemon per account, repo-scoped runs)

Status: accepted on 2026-06-29 (maintainer, evt-ogga)

**Follow-up fork (2026-07-01).** This decision still describes the
multi-repo/account-router lane, but no longer settles the default shape for a
single-repo OSS install or the long-term location of the agent-maintained KB.
That active design round lives in
[`design-home-scopes-and-knowledge.md`](design-home-scopes-and-knowledge.md).

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
- the **runner mandate** — the Shells+Cores this account may select/escalate
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

This now reuses the **dominion pattern lifted to account scope** rather than
creating repo-scoped branches in every project. A local account dominion repo
holds the durable state; repo-scoped material is tagged by repo inside it. A
gist stays the weakest option for structured account state (off-repo, separate
auth), though a secret gist can remain a lightweight renderer/plan publication
fallback when a full repo feels too heavy. Cross-repo plans naturally live in
the account store because they cannot belong to one managed repo's branch.

### 5. User view surface + control plane

The dashboard the engine shipped without. Five surfaces, all reading **one**
source of state:

- **Runner mandate** — the catalog of selectable Shells+Cores (name, class,
  cost_rank, quota/availability, `selected`), not just the one selected runner.
- **Per-run record** — runner/core, repo, boundary, elapsed, commits, plan
  position, attempt history; persisted (gist-per-run), card links to it.
- **Attempt ledger** — render the `codex→claude-haiku` "ran out of quota, retried
  cheaper" story instead of letting `attempt_failed` reasons vanish.
- **Activity** — running + scheduled runs, **with repo shown** (the 2E view
  already uses `repo_id`).
- **Plain-language config + daemon-owned confirmation** — show the mandate, let
  the user request changes in prose, the resident proposes a config change, and a
  *daemon-owned* step applies it (the resident cannot silently rewrite its own
  selection policy).

**brnrd is the hosted projection of this surface; the local daemon serves the
same state standalone.** That is the next invariant.

## Account-scoped store — the daemon's own home (recommendation, evt-puhl)

The account daemon has **no repo of its own**, yet several things are genuinely
account-scoped and need a durable, web-visible, OSS-local home: cross-repo
inter-run plans (part 4), the **run-state objects** (part 5's per-run record /
the maintainer's "larger run state object rendered beautifully in the browser"),
account config + repo registry + default repo, and the account-scoped **dispatch
queue** (the message-event inbox the cheap dispatcher reads). The maintainer
named the gap directly: *"brnrd daemon doesn't have a repo… where to keep the
plan then?"*

**Recommendation: a local-first account dominion repo** (working name
`brnrd-home` when/if projected to a forge) — **not** a fork of the brnrd source,
**not** a gist, and not created in the user's GitHub account without opt-in.

- *Why not fork-the-source* (the maintainer's "each install forks brr on behalf
  of the user"): it solves the right problem — give the daemon a repo — by the
  wrong means. Forking the tool's source **entangles user account-state with the
  tool's code**, pollutes the project's fork network, and makes update/merge
  semantics ugly (every account fork drifts from upstream). Take the idea's
  insight (the daemon should own a git repo), drop the entanglement.
- *Why not a gist*: off-repo, separate auth, no structure — already this page's
  weakest option for plans; the same verdict holds for the whole account store.
- *Why an account repo*: it is exactly the **dominion pattern lifted from
  repo-scope to account-scope**. The account repo is local-first because account
  state cannot hang off any one managed repo's branch. It holds the **OSS
  self-deploy invariant**: when self-deployed it is just a local git repo; a
  brnrd cloud projection or user-supplied remote is additive.

**The dominion *moves* to account scope — it is not a sibling (confirmed,
evt-qhk6; default creation clarified 2026-06-30).** This is a consolidation, not
a duplication: the resident's dominion stops being a per-repo orphaned branch and
becomes a repo-tagged directory inside the **account dominion repo**. This
follows from the daemon being per-account: one resident per account ⇒ one
dominion per account. The account dominion repo therefore unifies two things
under one home: the resident's durable memory and the account store below
(cross-repo plans, run-state registry, account config/registry, dispatch inbox).
Naming now tilts to `brnrd-home` / "account dominion repo" for the account-level
container, with `dominion/` kept inside it for the resident-owned directory.

Remote creation is deliberately opt-in: fresh startup creates only a local git
repo under the account state directory. A user who wants durability can point it
at an existing repo, approve creation of a new forge repo via OAuth, or later
choose another backend (for example S3-compatible storage).

**What lives in the account repo:** cross-repo inter-run plans (resolves the
cross-repo sub-fork of open question #1); the **run-state registry** (see the
CS2 reconciliation below); account config / repo registry / default repo; the
account-scoped dispatch inbox.

**What stays repo-local:** per-run **worktree execution artifacts** stay in the
*target repo's* `.brr/` — they are repo-scoped execution, not account state.
Repo-scoped (single-repo) inter-run plans can still ride that repo's own
orphaned branch; only **cross-repo** plans require the account repo.

**Event/run files under the account daemon** (the maintainer's "where should
they be now?"): split by scope — repo-scoped run files → target repo `.brr/`;
account-scoped queues (dispatch inbox, cross-repo state) → account repo. The
"reroute-to-another-repo as an event written into that repo's inbox" idea is
**unnecessary within one account daemon**: respawn-in-another-repo is *in-process*
(`RespawnRequest.repo`, part 3) because one daemon owns all the repos. A written
inbox-event handoff only earns its keep crossing a **daemon/account boundary**
(a different machine or account) — that is the case for an event file into
another inbox, not intra-account repo hops.

**Run-state object — reconciling CS2.** `plan-control-surface.md` CS2 says
"gist-per-run, delete on cleanup, no durable store." The maintainer now wants a
*larger, durable, beautifully-rendered* run-state object as part of the status
card. The account repo reconciles this: "gist-per-run" was a placeholder for *"a
per-run state doc somewhere web-visible"*; the account repo is the durable,
git-historied, **brnrd-projectable** home that satisfies the richer ask without a
new bespoke store. CS2 is updated to point here.

**Auto-create, overridable (confirmed, evt-qhk6).** The maintainer agreed the
daemon **auto-creates** the account repo on first `brr up`/install, *"but could be
overridable, agreed?"* — yes. So the shape is: auto-create by default (zero-config
common path), with an override letting the user **designate an existing repo** (or
opt out of cloud-side creation and keep it purely local). This resolves the
auto-create-vs-designate open question — it is *both*, default + escape hatch.

**Dispatch / inbox state lives in the account repo (confirmed, evt-qhk6).** The
account-scoped dispatch inbox — the message-event queue the cheap dispatcher reads
(part 3) — is account state, so it lives in the account repo, not in any managed
repo's `.brr/`. Maintainer: *"dispatch/inbox state — makes sense yes, let's do
it."*

Still open for execution: the exact remote backend UI. The local model is
settled; durability choices should be explicit and backend-abstracted (existing
git remote, GitHub repo creation through OAuth as an opt-in default, future
S3-compatible bucket).

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
   process model yet. Ship the mandate/per-run-record/attempt-ledger view
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

- **Inter-run plan physical location.** Refined (evt-ohsp): keep plans out of
  the project working tree, surfaced by the daemon, with cross-repo plans in the
  **account dominion repo** — see "Account-scoped store" above. With the
  dominion now consolidated to account scope (evt-qhk6), the simplest shape is
  that **all** inter-run plans ride the account dominion repo (repo-scoped ones
  tagged by repo), retiring the separate per-repo plan branch — but that final cut (one
  account home vs a per-repo `brr-plans` sibling) is the CS5 sub-fork still to
  confirm on execution.
- **Multi-account on one laptop.** Rare; defer. The account model should not
  *forbid* it, but v1 need not support it.
- **Repo discovery.** Explicit registry (user adds repos) vs scanning a
  workspace dir. Lean explicit for predictability; decide on execution.
- **Cross-repo concurrency.** v1 stays single-flight across all repos. Whether an
  account daemon eventually runs repos concurrently ties to the #128 claim model
  and `concurrent-worktrees` work — a later decision.
  Same-repo fan-out (worker-stack `spawn:` children, cap raised past 1) is a separate, narrower axis that doesn't require deciding this one first — see [`design-multi-workstream-concurrency.md`](design-multi-workstream-concurrency.md) (2026-07-08), which names this cross-repo question as one of its own open forks rather than re-deciding it.
