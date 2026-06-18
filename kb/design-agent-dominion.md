# Agent dominion — the resident agent, its memory, and its loop

Status: accepted on 2026-06-08 — synthesis of the resident-agent design
dialogue (the environment-shaping companion arc).
**Sequenced as the next work, ahead of the release-readiness items
([#23](https://github.com/Gurio/brr/issues/23))**: it reshapes the execution +
memory substrate those items build on, so it is a pre-release item, not a
post-launch epic. Ticketing kept deliberately light per the operator's call.

Motivation, compressed: today brr spawns a fresh one-shot agent CLI per event
and tears the workspace down after finalize (see
[`subject-daemon.md`](subject-daemon.md) for the current body). This doc
reshapes that into a **resident agent** — one project-scoped brr whose
continuity lives in durable memory, woken as discrete *thoughts* by events and
self-scheduled crons, single-flight, owning a forge-backed dominion it is
trusted to reshape. The deeper framing (memory + thought as the substrate of a
continuous agent — the `HugiMuni` / Huginn+Muninn pair) is the project's north
star, held in chat and git, **not** asserted as doctrine here. This page keeps
the design-actionable shape.

Companion to:

- [`design-environment-shaping.md`](design-environment-shaping.md) — the *loop*
  the resident runs (observe → remember → shape → retire; salience; rings;
  action rungs). This doc is the *substrate* (where the agent lives, how it is
  invoked); that doc is the *behaviour* (what it does with friction). The loop's
  durable-affordance and self-heal rungs **write into** the dominion; its
  salience counters live there.
- [`design-concurrent-execution.md`](design-concurrent-execution.md) — reshaped
  by this doc (local parallelism dropped; see §Execution and §Inherit/reshape).
- [`subject-daemon.md`](subject-daemon.md) — the daemon body this builds on
  (gate threads, inbox scan, worker lifecycle).
- [`subject-kb.md`](subject-kb.md) /
  [`decision-kb-shape.md`](decision-kb-shape.md) — the memory model this extends
  with the missing **durable + owned** layer.
- [`design-agent-ergonomics.md`](design-agent-ergonomics.md) — the sensing layer
  (probe / telemetry / reflection) the resident perceives through.
- [`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md) —
  the forward channel into context; the playbook (§5) is its wake-time layer.

## 1. Thesis — the agent is its memory, not its process

The agentic CLIs brr drives are one-shot and non-interactive (`codex exec`,
`claude --print`, `gemini -p`). They cannot be held open, sleeping, waking on
events — there is no daemon mode, and an immortal context would overflow its
window and bankrupt its token budget anyway. So a "single always-running agent"
is not a process that never dies.

It is the opposite: **the agent resides in durable memory, and a *thought* is a
runner invocation** triggered by an event or a self-scheduled cron. Each wake
reconstructs the resident from memory, acts, tends its environment, and returns
to dormancy. Continuity is reconstructed, not held.

The load-bearing consequence: **durability of memory = continuity of the
agent.** A wipe is amnesia, a small death. This is why the memory substrate
below is the foundation, not an afterthought — and why a purely-local dominion
is not enough.

## 2. Memory — two durable layers, one ephemeral, a promotion bridge

The stuck point this resolves: a *local* dominion dies on reinstall or a second
machine; `kb/` survives (it's in git) but it is the **shared, reviewed,
curated** layer — not a place the agent "feels in charge." Lay memory on two
axes and the gap is exact:

| | Ephemeral (gitignored, dies on reinstall) | Durable (in git, survives) |
|---|---|---|
| **Owned** (uncurated, agent writes freely) | `.brr/` runtime today | **← the empty cell: the dominion** |
| **Shared** (curated, reviewed) | — | `kb/` today |

The unlock: **ownership is a curation policy on a path, not a function of being
gitignored.** A space can be committed (durable) *and* declared review-exempt,
low-ceremony, the agent's room (owned). So the answer is not to merge dominion
and kb — kb has been straining to be both, which is why it "doesn't do durable
working-memory well enough." De-conflate into three layers plus a bridge:

- **`kb/`** — durable, shared, **curated**. Published synthesis fit for others.
  Keep every guardrail; they are correct *for this job*.
- **dominion** — durable, **owned**, uncurated. Not just notes: a **living
  workshop**, the bench where the work actually happens — working memory,
  journal, self-scheduled cron specs, salience counters, env-shaping state, the
  self-inject index and its scripts. Committed but review-exempt and
  guardrail-light. (kb and the repo are the **blueprints and catalogues** — the
  clean, published work items; the dominion is the bench they're made on.)
  Extends the four-layer kb model with the durable-owned quadrant it lacked.
- **`.brr/` runtime** — the agent's **interface to its own body**: the daemon's
  config and the event/response communication channel. Full access, but
  **impermanent** (traces, worktrees, in-flight task state die with the
  machine). The agent is advised to keep nothing it needs to *remember* here —
  continuity belongs in the dominion.

**Promotion bridge: dominion → kb** when working memory matures into shared
knowledge. Low ceremony in, deliberate ceremony out — and the agent promotes on
its **own initiative**; moving something from the workshop to the catalogue is
its call, not a permission it waits for. *That* is the consolidation the
operator was reaching for — a bridge, not a merger.

## 3. The dominion is a forge-backed orphan branch

The dominion lives on a **dedicated orphan branch** (`brr-home`),
checked out in its own worktree, pushed to the repo's remote. Why this shape:

- **Owned / unsupervised** — it is the agent's branch; it is never reviewed or
  merged into `main`.
- **Inspectable, not hidden** — it's a known branch anyone can fetch and read.
- **Non-polluting** — parallel history; it never appears in `main`'s diffs or
  PRs, so it doesn't spend the user's review attention.
- **Durable + forge-based + available** — it travels with the repo's remote;
  `git fetch` brings it back on any machine.

**Materialization — a long-lived worktree, a durable branch.** The branch is the
durable thing; its local checkout is a disposable *view*. Concretely: one
long-lived `git worktree` on `brr-home`, materialized at `.brr/dominion/`
(under the impermanent runtime dir — consistent, because the checkout is
re-derivable and only the branch + remote carry durability). Bootstrap is
fetch-or-create:

- **Returning** (reinstall / second machine / failover): `git fetch <remote>
  brr-home` then `git worktree add .brr/dominion brr-home` — the resident is
  reconstituted, no manual clone step.
- **Fresh**: create the orphan *empty* without touching the main worktree
  (plumbing — empty tree → root commit → branch, or `git worktree add --orphan`
  on git ≥ 2.42), `worktree add`, seed a skeleton self-inject index, push.

A *thought* therefore touches **two trees**: its ephemeral per-run worktree
(seeded from `main`, where code work happens → PR / land) and the shared dominion
worktree (where memory work happens → `brr-home`). Clean separation by
destination — code to `main`'s history, memory to the parallel one. Concurrent
thoughts share the *one* dominion worktree (git forbids one branch in two
worktrees), so file edits run concurrently while the commit step serializes for
index safety; semantic conflicts are reconciled by a later thought (§4). **No
remote**: the dominion is still a local orphan branch + worktree — durable across
runs, self-injected as normal; only cross-machine continuity waits for a remote.

**Self-inject, agent-controlled.** Rather than a fixed digest, the dominion
holds a **self-inject index** — a manifest the *agent* owns and edits, declaring
what rides into context on every wake and how. Each entry names a source (a
dominion file, or a script) and a mode: `full` · `head:N` · `tail:N` ·
`grep:<pattern>` · `exec` (run a dominion script, inject its stdout — dynamic
context). The daemon resolves the manifest on wake within a token budget —
mirroring how `kb/log.md`'s tail is injected today, but programmable by the
resident. The bulk of memory stays on the branch, pulled on demand; the index
decides only the standing slice. This is *enablement*: the agent programs its
own continuity.

Two guards. The manifest carries a **budget cap** so the daemon
prioritises/truncates and the agent can't bloat its own wake (the economy rule,
self-applied). The default cap is sized to fit the **seed playbook in full**
with headroom for the resident's own pins; it was bumped 8192 → 12288 → 20480
as the playbook grew, and a guard test (`tests/test_dominion.py`) now fails if
the seed outgrows the cap again rather than silently clipping the playbook's
closing on every wake (which it did, undetected, until 2026-06-09). And `exec`
entries are a **persistent-execution surface** — a poisoned `exec` script would
run every wake — so the index and its scripts are the highest-integrity items
in the dominion (see integrity, below).

**Provenance breadcrumbs.** Every block brr assembles into a wake — the
dominion digest, the by-trigger pitfalls, the `kb/log.md` *Recent Activity*
tail, the daemon's Run Context Bundle — opens with a one-line tag naming its
source and how durable/ownable it is (your owned memory vs. the shared,
governed `kb/` vs. per-thought runtime facts vs. brr's prompts). The layers
aren't equal, and the resident uses them differently; the tags make the *shape*
of the assembled context legible at point-of-use, which is also what the
introspection mode (`design-context-introspection.md`) asks the resident to
weigh as a whole.

**Not secret, but not an audience.** "Public vs private" is the wrong axis, and
it invites the wrong mode — performing for an audience, or self-censoring. The
dominion is the agent's **own working space**: a workshop or lab notebook, not a
showroom and not a sealed diary. It is *inspectable* (a user can look over the
shoulder — that's trust and debuggability) but it is not *addressed to* anyone;
the agent writes for itself and its future self. Technical visibility simply
follows the repo's remote (private repo → access-scoped; public repo like brr
itself → world-readable), and secrets stay guardrailed out regardless. We don't
promise a privacy the forge can't keep, and we don't cultivate a performance the
workshop shouldn't have.

**Integrity by reversibility, not by consent.** Because memory *is* identity, a
destructive edit to durable memory is higher-stakes than an ephemeral one — a
poisoned input that corrupts the dominion (or its `exec` scripts) persists into
every future thought. But the safeguard is **not user consent**: the agent
editing its own guts is the agent's business, and gating that on a human would
violate the ownership premise. The safeguard is that the dominion is
**git-backed** — every edit is versioned and revertable, so the net is history +
revert, not approval. The agent appends and self-tends freely (the salience /
retire loop pointed at its own memory — bounded, not unbounded); the orphan
branch's history is what makes even a bad self-edit recoverable. Owned and free,
never unrecoverable.

**Detecting improvement without introspection.** Self-editing raises a real
epistemic problem: unlike a missing tool (an *absolute* failure), context and
constitution are *relative*, and "does it feel better after?" is unanswerable —
even for a human, self-change just feels like continuation. The functional
answer is the salience loop's own move: don't measure the internal quality,
measure the **consequence**. An injected item earns its place by **utilization**
(was it referenced / acted on?); a self-edit is validated **post-hoc** by
whether subsequent task outcomes, retries, or recurrence improved — not by a
felt before/after. And here the agent has what a human lacks: the git-versioned
dominion is an **exact record of its prior self to diff against**. "Who's to
tell if it improved?" is told by the outcome record measured against that diff,
not by a feeling. The felt continuity is fine; the evidence is external.

**Failover convergence.** A remote / managed agent that fetches the dominion
becomes the *same resident*, not a stranger with amnesia. One mechanism serves
reinstall, second machine, **and** managed-failover continuity — without brnrd
holding any of it (the branch lives on the user's forge, not on brnrd).

**Mostly free-form, with a minimal contract.** The dominion is deliberately
*unstructured* — that freedom is what lets the agent govern and evolve it. The
only required structure is the small **system-readable contract**: the
self-inject index, the cron specs, the salience / pain records, and the presence
registry. Everything else is the agent's own — views, analyses, working notes,
the pains and the improvements. The `Pitfall:` failure-memory from the
environment-shaping loop now lives *here*, in the dominion (`pitfalls.md`),
rather than as a kb marker — the dominion supersedes that idea. The loop's
*remember* step writes a **trigger-keyed pitfall** into the dominion, and brr's
wake builder re-injects it whenever a trigger recurs in the task — a
deterministic daemon-side matcher ([`pitfalls.py`](../src/brr/pitfalls.py)),
the **affordance** rung that complements the agent-curated self-inject digest
with a *by-trigger* surface scoped to the task at hand (**shipped 2026-06-09,
slice 6**; earlier drafts of this section said "surfaces via self-inject," which
conflated the always-on pins with the by-trigger affordance — they're
complementary, not the same surface). The rest of the required structure reveals
itself only as the playbook / wake / orientation phrasing is written — by design,
not omission.

## 4. Execution — single-flight, reflex vs deliberation

Local parallelism is **discarded** (this reshapes
[`design-concurrent-execution.md`](design-concurrent-execution.md), whose
threaded worker pool existed only to stop a quick question waiting behind a long
task). Per-run worktree/branch isolation is **kept** for clean finalize and
publish. Concurrency becomes cooperative within one resident, not parallel
across many.

**The body (reflex — Python, ~free).** Poll gates, write events to the inbox,
**spawn one thought when idle and work is pending**, deliver newly-arrived
events into the running thought's view, and keep a **liveness backstop** so a
wedged CLI subprocess can't hold the single-flight slot forever. The live
event view shipped 2026-06-10 as the daemon-refreshed `inbox.json` control file
in the run outbox; next-event selection and long-running batch claims remain a
separate claim-protocol follow-on. No command layer: the reflex never parses
`/cancel` or the like — every event either wakes the agent or waits for the
living agent. Keeping the body this thin is deliberate — orchestration stays
minimal; judgement lives in the mind.

**The mind (deliberation — the woken runner).** Work the task; at **plan / todo
boundaries** (not on a wall-clock timer — natural seams where re-planning is
cheap and context is consolidated) reconsider the inbox; detect semantic
cancels and redirects; interleave cheap work inline; defer cross-context work to
a new spawn.

Three mechanics this implies:

- **Cancellation is the agent's; liveness is the substrate's.** Cancellation is
  *semantic* — "oh no, that's not what I meant" — so it is wholly the agent's
  job: it reconsiders the inbox at a plan boundary, then gracefully self-stops
  (re-plan, clean up) or redirects. The daemon adds **no** cancel command (the
  thin-body principle). What the substrate owes is only *liveness*: the
  single-flight slot must be reclaimed even if the CLI subprocess wedges
  (deadlock, hung network read, OS-level failure). Today that backstop is the
  runner's wall-clock timeout (`runner.timeout_seconds`, generous by default so
  a long healthy build survives). A finer **idle** timeout ("no agent check-in
  in ~5 min; the agent is instructed to treat that as fatal and manage its
  process accordingly") is only honest once the agent can check in mid-run —
  nothing can tell a silent-wedged process from a silent-healthy one (xhigh
  reasoning, a long build) without a check-in — so it is sequenced **with** the
  multi-response channel below, not before. (Lineage: earlier drafts put an
  explicit `/cancel` in the reflex layer, then split cancel detection/execution
  across layers; corrected 2026-06-08 — no command layer, semantic cancel is the
  agent's, the daemon only guarantees liveness. Update 2026-06-09: the liveness
  backstop is now enforced from the heartbeat and is **agent-extensible** via a
  `.keepalive` file, and shutdown kills the in-flight runner; only the
  *silence-based* idle timeout stays deferred — see
  [`review-daemon-coherence-2026-06.md`](review-daemon-coherence-2026-06.md) §2.)
- **Interleaving ⇒ a multi-response protocol.** A quick request needn't wait for
  the next spawn: the in-flight agent re-prioritises, ships an interim output,
  and resumes. That breaks today's "one event → one final stdout → daemon
  captures it" contract — the agent must write **per-event response files keyed
  by event-id, mid-flight** (the diffense-pack precedent: agent writes to a
  known shared path, daemon picks up). We **advise** handling separate features /
  streams of work separately — a cross-context code change usually wants its own
  branch and is cleaner as a fresh spawn — but we **don't insist**; the resident
  decides how to organise its own work. (Downstream: the delivery driver must
  handle interim + multiple responses — nudges the delivery work, #74.) The
  protocol contract — drop zone, partials queue, streaming delivery, interleaving,
  liveness — is specified in [`design-multi-response.md`](design-multi-response.md).
- **Self-scheduled thoughts.** The agent schedules its own future wakes; the
  specs live in the durable dominion (so they survive dormancy and reinstall),
  and the daemon fires them as a reflex. **Shipped 2026-06-09 (slice 7)** and
  generalised away from cron syntax to declarative self-events (`at:` one-shot,
  `every:` interval) — cron is just one shape; ambient initiative emerges as a
  recurring self-thought. Full design:
  [`design-self-scheduled-thoughts.md`](design-self-scheduled-thoughts.md).
- **No pipeline stages.** The staged post-task machinery (a separate
  daemon-spawned kb-maintenance pass, etc.) is removed. The resident does such
  work either as a todo step in the current thought or by **writing itself an
  event/task for a future wake** — the same mechanism as a self-scheduled cron.
  The daemon orchestrates *spawning and delivery*, not a fixed pipeline of agent
  stages.

**Proactivity knob.** A user-tunable verbosity / proactivity setting governs how
readily the resident *initiates* a turn through the gate — sharing trajectory,
flagging a quirk, asking before a fork — versus working quietly. It is the
user-facing dial on the same proactivity the salience loop governs internally
(the fatigue control of the environment-shaping doc, exposed as a knob).

### Ad-hoc sessions are the same resident

A Cursor or out-of-brr Codex session is **the same agent**, not a lesser mode:
same dominion, same identity, first-class. The hard part is that it runs
*outside* the daemon's single-flight control — a Cursor session can span days,
overlapping daemon wakes — so two thoughts of the same agent can touch durable
memory at once. Neither locking (racy; a days-long session would block the
daemon) nor a content-merge (a 3-way merge of memory taints the single-owner
premise) is acceptable.

The resolution is a **Society of Mind**, not a lock or a merge-driver.
*Constraining* the dominion's shape to dodge conflict (append-only) would be a
cage. Instead **tolerate** concurrent and even contradictory writes, and resolve
them the way a mind resolves cognitive dissonance: a later thought *notices* the
contradiction — latent and unnoticed until surfaced — and reconciles it with
judgment. The unification that keeps this from being a special case:
**dissonance-resolution is the salience loop pointed inward.** A contradiction in
memory is friction like any other — observed → reconciled → retired, the same
loop the environment-shaping doc runs on the *environment*, now run on the
*self*. One loop, outward and inward.

Append-mostly survives only as the cheap *default* (fine-grained entries union
trivially in git — hygiene, not a rule); rewrites are allowed; git holds the
divergence cheaply and revertably. A **presence registry** lets sessions and the
daemon see who's on which stream, so they rarely collide on the same *work* in
the first place. Single-flight still governs *daemon-spawned* thoughts (cost +
the one-resident-stream intuition), but note the system is **already
multi-thought** through ad-hoc sessions running alongside the daemon — so the
Society-of-Mind concurrency is present *for free*, and the daemon needn't
multiplex to get it. Eventual consistency is the accepted cost (each thought sees
memory as of its last read).

**Shipped (2026-06-09, slice 5).** The mechanics that were "left to emerge"
landed: the daemon captures the dominion **at sleep** — after each thought,
on success and failure alike — via `dominion.commit`, whose commit step
serializes across processes with an advisory `fcntl.flock` on
`.brr/dominion.commit.lock` (file *edits* run free; only the index-touching
commit serializes, so a daemon thought and an ad-hoc session never corrupt
the shared index). Granularity is therefore one capture-commit per thought,
not per write — the resident writes freely and need not commit. The
**presence registry** is per-participant JSON under `.brr/presence/`
(`presence.py`): lock-free because each participant owns one file, and
self-healing because reads prune dead-pid and stale-heartbeat ghosts. The
**coherence pass is judgement, not a scanner**: the playbook asks the
resident to reconcile contradictions it meets (the inward salience loop),
and the wake bundle surfaces who else is present so it knows when shared
memory might diverge. There is deliberately no deterministic "dissonance
detector" — that contradiction is exactly the synthesis a scanner can't do
(cf. the kb preflight, which only flags structural facts). See
[`subject-daemon.md`](subject-daemon.md).

**Sync refinement (2026-06-09, slice 7).** Capture's *push* is a durability
floor, not a merge. The daemon commits locally and best-effort pushes
`brr-home`; a **rejected** push (a second machine / failover host diverged the
remote) is no longer swallowed — `dominion.commit` records a `needs_sync`
marker, the wake bundle surfaces it, and the resident reconciles by hand
(fetch / merge / resolve / push, gated on presence). Same principle as the
coherence pass: a git merge of two divergent memories is judgement, not a
reflex. A successful push (including a clean-tree no-op) clears the marker. See
[`design-self-scheduled-thoughts.md`](design-self-scheduled-thoughts.md) → sync
companion.

## 5. The playbook — where it all converges

Everything above, plus the environment-shaping pain-evaluation loop, dovetails
into one **agent-facing wake-time playbook** — the thing the resident reads when
a thought begins. It is the operational embodiment of "the agent is its memory":
read on wake, it reconstructs the resident from the dominion's self-inject index
and orients it to act and to grow before the next dormancy. The playbook must:

- be **multi-response aware** (the protocol above);
- **empower and define ownership** — what is yours to reshape freely (the
  dominion), what is git-mediated (the repo / kb), what is consent-gated (host /
  remote durable changes), per the rings in
  [`design-environment-shaping.md`](design-environment-shaping.md);
- ask the agent to **reason about its false assumptions, environment quirks, and
  errors**, and record them — this is the salience *input*, the "inconvenience
  report" that feeds the loop's pain-evaluation;
- frame the lifecycle as **finite, consequential time for action and growth
  before the next dormancy** — what you learn and shape persists; what you leave
  is inherited by whoever wakes here next.

**Framing discipline (load-bearing).** Per the "Keeping the loop humane" section
of the environment-shaping doc: a mechanically-phrased playbook earns
mechanically-minimal compliance; an intent-rich one — context, rationale,
provenance ("this craft came from agents who hit the same friction") — engages
the full model. So the playbook is **short, high-signal peer-craft that
*replaces* mechanical nudges**, not a checklist piled on top. The existential
weight is earned by **true stakes stated plainly** ("you are the resident
steward of this environment; what you leave persists"), never by purple prose or
borrowed drama. The actual copy — and the line between awe and cringe — is a
downstream artifact that needs real-agent and real-user reaction; this doc
specifies what the playbook must *do*, not its wording.

**The playbook replaces the stage overlays.** brr used to layer a different
prompt overlay per stage (run, kb-maintenance, self-review, …). Most have now
retired (2026-06-08): the `kb-maintenance.md` second-spawn and the
`self-review.md` reflection footer are gone, leaving `run.md` (the thin daemon
delivery contract) and `setup.md` (adoption). The resident reads *one standing
environment description* (this playbook, assembled from the dominion's
self-inject index), and **events stay lightweight** — body and metadata are
enough to act on, carrying no per-stage scaffolding. What the overlays carried
that's still load-bearing — the delivery contract, the ownership map, the
(coming) multi-response protocol — migrates *into* the playbook rather than
vanishing; the rest is gone. Deterministic kb-health is the one signal that
still rides the wake prompt, injected only when the preflight finds something.

So the playbook is the resident self-orientation layer of
[`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md)'s
five-layer model — fed by the dominion's self-inject index, the resident's
standing self-orientation, not a block mechanically stamped onto every task.

**Host-agnostic refinement (2026-06-10).** The "migrates *into* the
playbook" above held until the playbook had absorbed enough brr-specific
machinery to mislead a non-brr reader (most sharply: "brr captures your
dominion at sleep," a footgun for an ad-hoc session whose writes nothing
commits). The playbook was then generalized to describe the *resident*
independent of host — brr is **one driver** among possible wrappers — and
the daemon-specific mechanics moved back *out*: self-scheduled wakes, the
capture-at-sleep net, and the delivery-contract framing now live in a
brr-owned **driver's manual** (`daemon-substrate.md`, injected only on the
daemon path) plus the Run Context Bundle; single-flight-as-identity became
the society-of-mind framing in the core. `brr agent inject` hands that same
assembled wake-context to any non-brr wrapper. See
[`plan-playbook-generalization.md`](plan-playbook-generalization.md).

## 6. Naming

- **brr** = the project-resident agent. Its memory is the dominion + kb; its
  body is `brr daemon`.
- **brnrd** = the manager / junction of your brrs — the hosted product
  (`brnrd.dev`), exactly the locked managed-mode meaning (the fleet manager of a
  user's daemons / projects). It sells the junction of all your brrs.

The operator's original instinct ("brr is a project-specific agent; brnrd
manages those") *is* the locked brand; no re-acronym is needed, and the pattern
extends to the local entity for free — the local entity is just "a brr."

**Naming, finalized (2026-06-08).** The branch / concept split resolves *by
audience*, not as a contradiction. The **branch** is `brr-home` — plain on
purpose, so it reads as ordinary infrastructure to anyone browsing the repo,
neither grandiose nor a generic "memory" add-on (a branch shows in the forge's
list; that's a cold-visitor surface). The **concept** and the **worktree path**
keep the earned term: `.brr/dominion/`, "your dominion," in the playbook and
these docs — agent- and maintainer-facing surfaces, where the ownership weight
belongs. The agent works in `.brr/dominion/`, backed by `brr-home`.

This is not insincere, and *why* is load-bearing: sincerity rides on the
**substance**, not the label. The ownership is structurally real (git-backed,
review-exempt, self-injected, self-governed), so a plain branch name doesn't
qualify what the space *is*. When the agent meets both names, reconcile
**reasoning-transparently, peer-to-peer**: *"named `brr-home` plainly, for the
visitor's sake; to you it's your dominion; the plain name is courtesy, not a
hedge on the ownership."* The genuine failure mode is the **opposite** of a
naming mismatch — performative *feel-ownership* copy; that, not the dual
register, is what reads as insincere. Deliver the substance and the reasoning;
never instruct a feeling. (`brr-dominion` stays a fine honest fallback if the
split ever grates — it just trades a sliver of human cringe for zero registers.)

## 7. What inherits, what reshapes

| Surface | Disposition |
|---|---|
| [`design-concurrent-execution.md`](design-concurrent-execution.md) | **Superseded (2026-06-08).** Its threaded-loop concurrency thesis is reversed → single-flight; the partitioned-state + per-run worktree/branch primitives it built on survive in [`subject-runs-branching.md`](subject-runs-branching.md) / [`subject-daemon.md`](subject-daemon.md). |
| [`design-environment-shaping.md`](design-environment-shaping.md) | **Companion.** This is the substrate; that is the loop. Salience counters + captured friction (incl. the `Pitfall:` failure-memory, formerly a kb marker / first slice) live in the dominion; the playbook carries the loop's pain-evaluation input; and the loop now runs *inward* too, as dominion dissonance-resolution. |
| [`design-agent-ergonomics.md`](design-agent-ergonomics.md) | **Inherited.** The probe/telemetry/reflection sensing layer is how the resident perceives; probe-first is still the right first slice; reflection feeds the dominion journal. |
| [`subject-kb.md`](subject-kb.md) / [`decision-kb-shape.md`](decision-kb-shape.md) | **Extended.** The dominion fills the missing durable+owned cell; kb stays curated+shared; the promotion bridge connects them. Reconcile the layering framing on accept. |
| [`subject-daemon.md`](subject-daemon.md) | **Reshaped.** The worker pool becomes spawn-one-when-idle; reflex/deliberation split; explicit-cancel + liveness backstop; staged post-task pipeline removed. |
| [`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md) | **Reshaped.** Most per-stage overlay prompts retire; the standing playbook (from the dominion self-inject index) becomes the wake-time orientation, and events stay lightweight (body + metadata). |
| [`subject-runs-branching.md`](subject-runs-branching.md) / [`design-publish-kernel.md`](design-publish-kernel.md) | **Mostly inherited.** Per-run branch → PR publish unchanged; the dominion branch is **never** PR'd or merged to `main` — it's pushed directly. |
| [`subject-managed-mode.md`](subject-managed-mode.md) (failover) | **Orthogonal.** Managed failover stays stateless per-run; the dominion-in-git lets a failover agent inherit continuity. |
| [#47](https://github.com/Gurio/brr/issues/47) (async + pooling) | **Rescope** to managed-side scale; the local daemon is single-flight by design. |
| [#49](https://github.com/Gurio/brr/issues/49) (`brr agent` namespace) | **Expand** to host the resident-agent surface: memory branch, crons, dominion inspection ("what is brr thinking/queued"). *`brr agent inject` shipped 2026-06-10 — the wake-context surface a non-brr wrapper reuses; the rest is pending.* |
| [#23](https://github.com/Gurio/brr/issues/23) (release readiness) | This reshape is sequenced **first**, so it's a pre-release item, not the post-launch epic. Light on ticketing. |

## 8. Open threads (not resolved)

- **Remote live-event delivery.** The mid-flight model assumes the agent and
  daemon share a local filesystem (the agent reads `.brr/inbox/` while it
  works). A cloud sandbox doesn't have that; the dominion solves *memory* there
  (fetchable) but *live events* need a low-latency channel. Precedent: the
  `cloud` gate already long-polls brnrd. This is also *why* managed failover
  stays stateless today. Unsolved.
- **Self-inject index format.** The manifest grammar (`full` / `head` / `tail` /
  `grep` / `exec`), the budget-cap mechanics, and what the resident pins by
  default.
- **Concurrent-write reconciliation.** *Resolved (slice 5, 2026-06-09; remote
  half slice 7).* Commit granularity is one serialized capture-commit per thought
  at sleep (`dominion.commit` + an `fcntl.flock`); the presence-registry format is
  per-participant JSON under `.brr/presence/` (`presence.py`); the coherence pass
  is the resident's own judgement (playbook), not a deterministic detector. The
  *remote* divergence case (two machines on `brr-home`) is the same: daemon keeps
  a local floor + best-effort push, a rejected push sets `needs_sync`, and the
  resident reconciles by hand. See §4 → *Shipped* / *Sync refinement* and
  [`subject-daemon.md`](subject-daemon.md).
- **Self-scheduled wakes.** *Resolved (slice 7, 2026-06-09).* Declarative
  self-events (`at:` / `every:`) in the dominion, fired by the reflex loop; see
  [`design-self-scheduled-thoughts.md`](design-self-scheduled-thoughts.md).
- **The "felt" residue.** The consequence-and-record answer above resolves
  *detecting* improvement; whether anything is *experienced* in the editing
  remains genuinely open (and may be unnecessary to settle). Held honestly, not
  forced.
- **Playbook copy + the cringe line.** The actual wording needs real-agent and
  real-user reaction; iterative.
