# Agent dominion — the resident agent, its memory, and its loop

Status: proposed (2026-06-07) — synthesis of the resident-agent design
dialogue (the environment-shaping companion arc). Not yet accepted.
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
- **dominion** — durable, **owned**, uncurated. Working memory, journal,
  self-scheduled cron specs, salience counters, env-shaping state. Committed
  (survives reinstall, reachable anywhere) but review-exempt and
  guardrail-light. (Extends the four-layer kb model with the durable-owned
  quadrant it lacked — a kb-shape reconciliation note for when this is
  accepted.)
- **`.brr/` runtime** — genuinely ephemeral (traces, worktrees, in-flight task
  state). Dies with the machine; reconstructable; that's fine.

**Promotion bridge: dominion → kb** when raw working memory matures into shared
knowledge. Low ceremony in, deliberate ceremony out. *That* is the
consolidation the operator was reaching for — a bridge, not a merger.

## 3. The dominion is a forge-backed orphan branch

The dominion lives on a **dedicated orphan branch** (e.g. `brr-memory`),
checked out in its own worktree, pushed to the repo's remote. Why this shape:

- **Owned / unsupervised** — it is the agent's branch; it is never reviewed or
  merged into `main`.
- **Inspectable, not hidden** — it's a known branch anyone can fetch and read.
- **Non-polluting** — parallel history; it never appears in `main`'s diffs or
  PRs, so it doesn't spend the user's review attention.
- **Durable + forge-based + available** — it travels with the repo's remote;
  `git fetch` brings it back on any machine.

**The auto-injection pre-agreement.** The branch has a known layout, and a
bounded **digest** (pinned facts, active intentions, recent journal, scheduled
crons) is auto-injected by the daemon on every wake — mirroring how
`kb/log.md`'s tail is injected today. The bulk stays on the branch, pulled on
demand. This keeps the economy/vantage rule honest: durable memory is large, but
only the high-signal slice rides into context automatically; the rest is manual.

**Owned ≠ private.** A public remote means a public dominion. Secrets stay
guardrailed out regardless; truly-private agent memory would need a private
remote, trading some always-free-self-host simplicity. Acceptable, but named.

**Integrity / blast-radius.** Because memory *is* identity, a destructive edit
to durable memory is higher-stakes than an ephemeral task edit — a poisoned
input that corrupts the dominion persists into every future thought. So:
*appending* to the journal is free; *structural rewrite or deletion* of durable
memory sits a notch up the consent ladder; and the agent **self-tends** its
journal (the salience/retire loop pointed at its own memory — bounded, not
unbounded). Owned, not unlimited.

**Failover convergence.** A remote / managed agent that fetches the dominion
becomes the *same resident*, not a stranger with amnesia. One mechanism serves
reinstall, second machine, **and** managed-failover continuity — without brnrd
holding any of it (the branch lives on the user's forge, not on brnrd).

## 4. Execution — single-flight, reflex vs deliberation

Local parallelism is **discarded** (this reshapes
[`design-concurrent-execution.md`](design-concurrent-execution.md), whose
threaded worker pool existed only to stop a quick question waiting behind a long
task). Per-task worktree/branch isolation is **kept** for clean finalize and
publish. Concurrency becomes cooperative within one resident, not parallel
across many.

**The body (reflex — Python, ~free).** Poll gates, write events to the inbox,
**spawn one thought when idle and work is pending**, deliver newly-arrived
events into the running thought's view, honour an explicit `/cancel`
(hard-kill), and keep a **liveness backstop** (kill a runaway/looping thought
that has stopped checking in).

**The mind (deliberation — the woken runner).** Work the task; at **plan / todo
boundaries** (not on a wall-clock timer — natural seams where re-planning is
cheap and context is consolidated) reconsider the inbox; detect semantic
cancels and redirects; interleave cheap work inline; defer cross-context work to
a new spawn.

Three mechanics this implies:

- **Cancellation = detection vs execution.** Detection is *semantic* and
  therefore the agent's job — a static parser cannot catch "oh no, that's not
  what I meant." Execution is a graceful self-stop (the agent can re-plan and
  clean up) with the daemon hard-kill as backstop for the unresponsive case.
  Explicit `/cancel` is the reflex fast-path. (Earlier this doc put cancellation
  wholly in the reflex layer; corrected — static detection is insufficient.)
- **Interleaving ⇒ a multi-response protocol.** A quick request needn't wait for
  the next spawn: the in-flight agent re-prioritises, ships an interim output,
  and resumes. That breaks today's "one event → one final stdout → daemon
  captures it" contract — the agent must write **per-event response files keyed
  by event-id, mid-flight** (the diffense-pack precedent: agent writes to a
  known shared path, daemon picks up). Boundary: interleave reads / answers /
  same-context replans inline; cross-context *code* changes still want their own
  branch, so they defer to a fresh spawn. (Downstream: the delivery driver must
  handle interim + multiple responses — nudges the delivery work, #74.)
- **Self-scheduled crons.** The agent schedules its own future wakes; the cron
  specs live in the durable dominion (so they survive dormancy and reinstall),
  and the daemon fires them as a reflex.

## 5. The playbook — where it all converges

Everything above, plus the environment-shaping pain-evaluation loop, dovetails
into one **agent-facing wake-time playbook** — the thing the resident reads when
a thought begins. It is the operational embodiment of "the agent is its memory":
read on wake, it reconstructs the resident from the dominion digest and orients
it to act and to grow before the next dormancy. The playbook must:

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

The playbook is the wake-time layer of
[`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md)'s
four-layer model, fed by the dominion's auto-injected digest — the resident's
standing self-orientation, not a block mechanically stamped onto every task.

## 6. Naming

- **brr** = the project-resident agent. Its memory is the dominion + kb; its
  body is `brr daemon`.
- **brnrd** = the manager / junction of your brrs — the hosted product
  (`brnrd.dev`), exactly the locked managed-mode meaning (the fleet manager of a
  user's daemons / projects). It sells the junction of all your brrs.

The operator's original instinct ("brr is a project-specific agent; brnrd
manages those") *is* the locked brand; no re-acronym is needed, and the pattern
extends to the local entity for free — the local entity is just "a brr."

## 7. What inherits, what reshapes

| Surface | Disposition |
|---|---|
| [`design-concurrent-execution.md`](design-concurrent-execution.md) | **Reshaped.** Local parallelism dropped; the per-task partitioning simplifies; per-task worktree/branch isolation kept for clean publish. Gets a superseded-in-part marker when this doc is accepted (not before — it's a proposal). |
| [`design-environment-shaping.md`](design-environment-shaping.md) | **Companion.** This is the substrate; that is the loop. Salience counters + captured friction live in the dominion; the playbook carries the loop's pain-evaluation input. |
| [`design-agent-ergonomics.md`](design-agent-ergonomics.md) | **Inherited.** The probe/telemetry/reflection sensing layer is how the resident perceives; probe-first is still the right first slice; reflection feeds the dominion journal. |
| [`subject-kb.md`](subject-kb.md) / [`decision-kb-shape.md`](decision-kb-shape.md) | **Extended.** The dominion fills the missing durable+owned cell; kb stays curated+shared; the promotion bridge connects them. Reconcile the four-layer framing on accept. |
| [`subject-daemon.md`](subject-daemon.md) | **Reshaped.** The worker pool becomes spawn-one-when-idle; reflex/deliberation split; explicit-cancel + liveness backstop. |
| [`subject-tasks-branching.md`](subject-tasks-branching.md) / [`design-publish-kernel.md`](design-publish-kernel.md) | **Mostly inherited.** Per-task branch → PR publish unchanged; the dominion branch is **never** PR'd or merged to `main` — it's pushed directly. |
| [`subject-managed-mode.md`](subject-managed-mode.md) (failover) | **Orthogonal.** Managed failover stays stateless per-task; the dominion-in-git lets a failover agent inherit continuity. |
| [#47](https://github.com/Gurio/brr/issues/47) (async + pooling) | **Rescope** to managed-side scale; the local daemon is single-flight by design. |
| [#49](https://github.com/Gurio/brr/issues/49) (`brr agent` namespace) | **Expand** to host the resident-agent surface: memory branch, crons, dominion inspection ("what is brr thinking/queued"). |
| [#23](https://github.com/Gurio/brr/issues/23) (release readiness) | This reshape is sequenced **first**, so it's a pre-release item, not the post-launch epic. Light on ticketing. |

## 8. Open threads (not resolved)

- **Remote live-event delivery.** The mid-flight model assumes the agent and
  daemon share a local filesystem (the agent reads `.brr/inbox/` while it
  works). A cloud sandbox doesn't have that; the dominion solves *memory* there
  (fetchable) but *live events* need a low-latency channel. Precedent: the
  `cloud` gate already long-polls brnrd. This is also *why* managed failover
  stays stateless today. Unsolved.
- **Dominion layout + digest format.** What's pinned, what's auto-injected, size
  bounds on the journal.
- **Destructive-edit consent rung.** Exactly where structural rewrite / deletion
  of durable memory sits on the consent ladder, and how the agent's self-prune
  is bounded.
- **Ad-hoc agents and the dominion.** Whether a Cursor / out-of-brr Codex
  session *writes* the dominion or only reads it — interplay with the non-brr
  unification (a presence / working-set registry in the dominion so the daemon
  and ad-hoc agents don't collide).
- **Playbook copy + the cringe line.** The actual wording needs real-agent and
  real-user reaction; iterative.
