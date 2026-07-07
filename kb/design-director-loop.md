# Design: the director loop — game pacing as product thesis

Status: active — design round opened 2026-07-01 from the maintainer's
five-part voice dump (evt-4mzl…evt-4nzp); scrutinised and mapped this wake.
Companion brand/naming exploration: [`design-brand-brnrd-brr.md`](design-brand-brnrd-brr.md).

## The thesis (compressed from the notes)

Terminal games are more fun than terminal engineering because games are
*designed*: they throw a meaningful decision at you at intervals, execute
hidden work, reveal progress, and hand you the next decision. Agentic coding
has the execution but not the design — agents either wait for commands,
over-explain, or run too far without a progression loop. The missing layer is
a **director**: something that decides when the user needs a decision, when
the agent continues silently, and when a result is revealed. The loop:

> meaningful choice → hidden execution → reveal → new state → next choice

with progression carried on the normal collaboration surfaces (issues, PRs,
commits, messages), not badges or XP. The external LLM's sharpest line holds
up: **this is a product thesis, not a UX garnish** — "a directed co-op
workflow" is a different category from "agent runner infrastructure."

## Verdict up front

The diagnosis is right and most of the machine already exists here under
other names. The dangerous parts are all in the *execution shape*: three
specific crashes to avoid, then a four-phase path where phase 1 costs zero
code. The single most important architectural call: **the director is a
stance the resident holds plus two small mechanical seams — not a daemon
component.**

## Where the crash is (scrutiny)

**1. Manufactured choice.** The notes' own run-end contract ("every agent run
should output options A–D") collides with a standing product guardrail:
`run.md` → *take the reversible calls yourself; hand genuine forks to the
user with options weighed*. A contract that **requires** options at every run
end manufactures forks that aren't there. The user learns within a week that
the options are filler, stops reading them, and now the product nags — the
exact "fake dopamine garbage" the notes reject, wearing a quest log's
clothes. Good games don't quiz you every 30 seconds either; between real
decisions there is flow. **Resolution:** the run-end contract must have
"nothing to decide — continuing / done" as its *most common* value. Options
appear only at genuine forks and arc boundaries. Pacing is the product;
choice frequency is not.

**2. The Director as daemon infrastructure.** The daemon is deterministic
machinery: it schedules, routes, injects, captures. It cannot rank moves or
judge when a reveal is due — that's model work. A "Director component" in
`daemon.py` would need its own LLM calls, its own budget, its own failure
modes, and would duplicate the resident. **Resolution:** the director is the
*resident* operating under a pacing contract (prompt layer), with the daemon
contributing only what it already knows how to do: inject state, park
continuations, render cards, fire scheduled wakes.

**3. Ambient cost.** A director that "watches project state and user
attention" continuously is a spend multiplier — the exact failure
`design-self-scheduled-thoughts.md` already names ("a thought that wakes for
nothing is friction you pay every cycle"). **Resolution:** event-driven
first. The loop advances when runs end and when the user speaks. A periodic
director tick is an *opt-in* `schedule.md` entry, and its body must include
its own silence condition ("message only if the ranking changed").

Positioning risk (game vocabulary vs B2B) is real but is a brand question —
handled in [`design-brand-brnrd-brr.md`](design-brand-brnrd-brr.md).

## What already exists (the mapping)

The notes were written without knowledge of the architecture; the striking
thing is how much of the director already shipped under other names:

| Game-loop element | Existing machinery |
| --- | --- |
| Decision point, "choose / override / delegate" | Genuine-fork surfacing (`run.md` reconsider contract); PLAN→approve parked portal; `runner_policy: propose` approval loop |
| Hidden execution | Single-flight daemon runs; worktrees; subagents |
| Reveal moments | Progress card narration (`.card`); mid-thought outbox replies; diffense review pack; `gate: forge` PR handoff |
| Quest log / ranked standing moves | **Inter-run plan home** — decided in [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md) §4: repo-tagged plans in the account dominion repo, daemon-injected between wakes, card-linked |
| Progression surfaces | Issues, PRs, commits, `kb/log.md`, the activity dashboard |
| "Keep the run open for follow-ups" | runner-owned `.keepalive` + `inbox.json` folding at plan boundaries; daemon-owned `delivered · attending` floor after current-thread delivery |
| Director's own clock | `schedule.md` self-scheduled thoughts |

So the thesis is not a new subsystem; it is a **completion criterion** for
things already half-built, plus a naming of the feel they should add up to.
What's genuinely missing is three things.

## The three real gaps

**Gap 1 — run-end next-move contract.** Today a run ends in free prose. The
loop wants a structured closeout: state (`done | continuing | blocked |
needs-choice | ready-for-review`), what changed, and — *only when real* — 2–4
options with a recommendation and a reason. This is where "what should we do
next?" stops being a blank page.

**Gap 2 — the standing move ranking.** The inter-run plan home is decided
but not built. The director loop is what it's *for*: a resident-curated,
ranked list of the likeliest valuable moves per repo, injected every wake,
rendered on the dashboard and card. Rank + refresh discipline is the delta
over the already-decided plan file.

**Gap 3 — pacing policy.** When to reveal, when to ask, when to shut up and
work. Pure prompt layer. The reveal moments are already enumerated (after
discovery, after decision, after diff, after tests, after PR); the contract
just has to say that reveals happen *at those seams and not continuously*.

## Implementation plan (sequenced so a lesser model can execute)

Each phase is independently shippable and reversible. Phase 1 needs no code.

**Phase 1 — pacing + closeout as prompt contract (no code).**
Add a short "The next move" section to the portals doc
(`src/brr/docs/portals.md`) and a sentence to the delivery-contract text in
`src/brr/prompts.py`: an addressed run's final reply ends with one of
(a) *done — receipt*, (b) *continuing — what's next and when to expect it*,
(c) *blocked — what's needed*, or (d) *a genuine fork: 2–4 numbered options,
a recommendation, one-line reason*. Explicitly: most runs end (a)–(c);
manufacturing (d) is the named failure mode. Acceptance: prose only, user
replies in prose, existing conversation threading routes it. This ships the
*feel* immediately.

**Phase 2 — parse the closeout (small code).**
An optional fenced block in the final stdout (or outbox frontmatter key
`next:`) carrying `state / options / recommended`. Daemon parses it into the
run record, renders options as a numbered list in the delivered reply, and
prepends the chosen option text when a short follow-up reply ("2", "B") is
matched to a pending option set on the same conversation key. Touch points:
`prompts.py` (contract text), `daemon.py` closeout path (parse + stash),
gate delivery rendering, `docs/portals.md`, tests mirroring the existing
outbox-frontmatter tests. No new process, no new store — it rides the run
record. ~1–2 focused wakes.

**Phase 3 — the quest log (already-decided work, now with a purpose).**
Execute the inter-run plan home per
[`decision-account-centered-daemon.md`](decision-account-centered-daemon.md)
§4 / implementation note 4, with the ranking discipline added: the file is a
ranked move list (`plans/<repo>.md` in the account dominion repo), the
resident re-ranks it at closeout when the run changed the picture, the
daemon injects the top of it each wake and links it from the card. Dashboard
renders it. This is the always-on surface that replaces "modal choice
spam" — the user *glances* at standing moves instead of being interrupted.

**Phase 4 — director tick (opt-in, existing machinery only).**
A `schedule.md` entry whose body is the director stance: "re-rank the move
list from repo/forge state; message the gate only if the top move changed or
something is newly blocked." No daemon feature at all — this is exactly the
ambient-initiative pattern `design-self-scheduled-thoughts.md` designed,
with the silence condition as the brake.

## The orchestrator/worker question (brnrd spawns brrs)

The notes also sketch a two-tier execution shape: a stingy, unhurried
orchestrator (brnrd) that holds the conversation and spawns focused workers
(brrs) for tedious bounded work, picking cores by task complexity. Scrutiny:

- **The rails exist.** Cheap answer-or-respawn dispatcher, `respawn: true`
  with `shell:`/`core:`/`quality: escalate`, in-run subagents, the
  cost-ranked runner catalog. Nothing new is needed to *behave* this way.
- **A mandatory tier split is premature** — the maintainer's own fence is
  right. Always-two-hops means latency on every trivial exchange, double
  context assembly, and a new failure surface, paid before models are fast
  enough to hide it.
- **The shape that works today:** delegation as *resident policy*, not
  process architecture. The resident keeps user-interfacing, commits, and
  judgement; it spawns subagents/respawns for bounded tedium; the stingy
  behaviours (grep before read, count lines before opening, keep the run
  open for follow-ups) are prompt-level and largely already present. When a
  worker wake is spawned, it gets the slim stack: task + files + structured
  result; no dominion write, no scheduling, no kb governance.
- **Revisit trigger:** when a strong-class core's time-to-first-token and
  cost make the orchestrator hop invisible, promote the split from policy to
  default. That's a model-economics date, not a design blocker.

This reframing also answers "how is a brr mechanically different from
brnrd": same rails, different injected stack — which is already how
subagents work. The naming half lives in the brand page (resolved
2026-07-02: `brr` stays retired as a name; the split is essence, not
vocabulary).

### Re-justifying the split (maintainer fork, 2026-07-04)

B3/B4 shipped the worker/resident split leaning on a pollution-risk framing
("worktrees mean it's unlikely to pollute your space"). The maintainer
pushed back correctly: dominion and kb are both git-versioned, worktrees
already isolate concurrent file mutation, and a diverged dominion/kb merges
mechanically like any other branch. Pollution was never the real risk the
split was guarding against — re-examined, the actual justification is two
things unrelated to data safety:

- **Judgment scope, not merge conflicts.** A worker wake has no continuity
  — it reads no recent-log, holds no pitfalls, won't be there to defend or
  revisit a call next week. Git can merge two divergent kb edits; it cannot
  merge two divergent *editorial judgments* about what's worth keeping in
  shared standing memory. That accountability gap, not file contention, is
  why a worker doesn't get kb governance or scheduling authority — a dozen
  bounded workers each free to schedule wakes or rewrite kb pages is a
  governance problem no worktree fixes.
- **Cost.** The resident stack (identity core, dominion, playbook, plans,
  policy, ledger, pitfalls, kb health, introspection) is real injected
  tokens on every wake. A worker doing "read this file, fix this bug,
  return a diff" pays for all of it and uses none of it. That overhead is
  waste, not caution.

Net: the split stands, on sharper ground than it shipped with. Not because
isolation is scarce (it isn't — worktrees + git already cover that) but
because standing-memory judgment and full-stack cost are real and orthogonal
to isolation.

**Respawn vs in-run subagents — not the same capability, don't unify.**
The maintainer also asked whether `respawn:` is still earning its keep, and
whether spawning should be unified into one mechanism now that "cheap
dispatcher escalates to a stronger core" is no longer the load-bearing
architecture (`decision-account-centered-daemon.md` §3 keeps that dispatcher
narrow — unpinned message events only, not the general spawn path). That
framing of `respawn:` is stale and worth retiring explicitly, but the
mechanism itself is not redundant with in-run subagents (the `Agent` tool):
a subagent is in-process, same Shell, supervised live in this conversation;
`respawn:` parks a brand-new top-level daemon event that can move to a
**different Shell entirely** (Codex ⇄ Claude), a different repo, or simply
outlive this run's return. An in-harness subagent structurally cannot do
any of that — it has no path to a different provider's CLI. So: keep both,
reframe `respawn:`'s stated purpose from "dispatcher escalation" to
"cross-runner / cross-repo / outlives-this-run handoff," and keep
`worker: true` as the orthogonal stack-weight dial it already is (B4) —
applies regardless of *why* the handoff happened, not tied to an escalation
story.

## Concurrent sub-spawns (maintainer, 2026-07-06): a real extension, not a violation

Direct proposal, same-thread as the run-ledger cost-tracking work and the
respawn-dedup fix: "the initial dispatcher should be able to create sub
spawns not respawns, so concurrency, for cost effectiveness, a cheaper
core or a shell having more quota is used... counted by daemon into the
run's cost... toggle-able, on by default." Paired, in the same message,
with real self-scrutiny: "goes a bit against the cost per core
calculation I proposed earlier... maybe I am wrong... the serial model of
execution might be truer to life" — and a concrete requirement: review
the lesser-light runner's produce before it reaches the user,
automatically, without stalling, "fitting the loom design we have
agreed on."

**What already exists, and isn't the gap.** In-run subagents (the `Agent`
tool) already give same-Shell concurrent, cost-tuned, reviewed-by-
construction sub-dispatch: a `model` override picks the cheaper core, a
call can run in the background while the parent keeps working, and the
parent synthesizes the subagent's output before it ever reaches the user
— review happens for free, by construction, because the parent is the one
who replies. None of that needs building. It also cannot reach a
different Shell (Codex has no in-process subagent path from a Claude
run) — that boundary is what "sub spawns not respawns" is actually
naming.

**The real gap: `respawn:` is cross-Shell but sequential-only.** Today's
only cross-Shell/cross-quota-pool mechanism is `respawn:`, and it is
strictly a hand-off, not a fork: `_queue_respawn_request` only dispatches
once the *parent run ends* — "single-flight is per-dominion, not
per-Shell" (2026-07-06, this same page's neighboring log entry). That is
why the maintainer's own "cohere the docs, then implement the ledger"
turn collapsed to one sequential order regardless of framing — there is
no primitive today for "start a child now, keep working, get notified
when it lands."

**Why single-flight exists, and why this proposal doesn't undermine it.**
§4 above discarded local parallelism because *dominion coherence* needs
one writer: durable memory (kb, playbook, schedule, ledger) can't
tolerate two concurrent resident thoughts editing it without becoming the
threaded-worker mess `design-concurrent-execution.md` was reversed to
escape. But a worker-stack child (`worker: true` — no dominion write, no
kb governance, no scheduling authority; already the resident/worker
split's own invariant, "Re-justifying the split" above) never touches the
surface single-flight protects. Today's daemon enforces one-worker-per-
dominion at the whole dispatch-loop level, uniformly for resident *and*
worker-stack runs — broader than what the dominion-coherence argument
actually requires. That gap between enforcement scope and justification
scope is real, and the maintainer's proposal sits exactly inside it: a
concurrent worker-stack child doesn't reopen the incoherent-durable-memory
problem single-flight was built to close, because it structurally cannot
write to the memory that problem was about.

**The "cost per core" tension, resolved, not dodged.** The existing
cost-ranked catalog (`design-runner-cores.md`) and the just-shipped
`run_ledger` schema (`design-quota-scheduling-loom.md` §"Tracking-table
schema", PR #254) both assume one core is *the* cost driver for a run.
Concurrent children don't break that — they make a parent run's true cost
a **rollup**: parent row + Σ(child rows), via an additive `parent_run_id`
field on the ledger schema, not a rewrite of the rows that already exist.
Not a conflict; an aggregation the schema is already shaped to carry.

**"The serial model might be truer to life" — yes, and that's preserved.**
The resident's own mind stays single-threaded: one dominion writer, one
train of thought, exactly as today. A sub-spawn is *delegation* — the
same shape as a person handing a bounded task to an assistant while
continuing their own single line of thinking — not the mind forking. The
proposal extends *what a resident can delegate to*, not *how many minds a
resident has*.

**What it would actually take to build (real work, not a toggle):**

1. A `spawn:` outbox frontmatter, sibling to `respawn:` — same `shell:`/
   `core:`/`worker: true` fields, but dispatched *immediately* (daemon
   starts the child alongside the still-running parent) rather than
   queued for after the parent ends. Strictly `worker: true` semantics,
   no exception — a resident-stack concurrent spawn is exactly the
   incoherent-dominion case §4 forecloses.
2. Relax the daemon's per-dominion dispatch loop from "one worker, full
   stop" to "one resident thought + up to N concurrent worker-stack
   children" — a cap (start at N=1, not unbounded — answers "toggle-able"
   with a conservative default rather than an open one), gated so a
   worker-stack child can never itself spawn or hold the resident slot.
3. `run_ledger` gains `parent_run_id`/`is_subspawn`, so parent cost is a
   query (rollup), and the catalog's existing cost-rank ordering picks the
   child's Shell/Core automatically (cheaper or quota-richer — no new
   selection logic needed, the ranking already exists).
4. A child-completion notification delivered *into the still-running
   parent thought* — the same shape `inbox.json` already uses for a
   mid-run user event — rather than the review self-wake convention
   documented in `dominion-playbook.md` §Delegation. The self-wake exists
   today because nothing else can tell a parent "your child is done"
   without either blocking (impossible, single-flight) or guessing a
   completion time (fragile — exactly what this week's respawn-dedup
   saga already illustrated: a squashed dispatch left a self-wake
   reviewing nothing). A live in-run notification is strictly better when
   the parent is still executing; the self-wake convention still covers
   the case where the parent has nothing else to do and would rather end.

**Verdict:** sound, worth building, doesn't contradict why single-flight
exists — but item 2 above touches the exact dispatch-loop invariant this
whole page's §4 was built around, which is why this is written up as a
design section and not shipped inline in the same run that raised it.
Recommended: build slice 1 (frontmatter + `parent_run_id` schema field +
cap-of-1 concurrent worker-stack child + live completion notification) as
its own reviewable PR, cap fixed at 1 until it's proven not to starve the
single resident slot of attention. Flagged back rather than built blind,
per the maintainer's own "you tell me, maybe I am wrong" — the answer is
"you're not wrong," but the specific cap/gating shape is a call worth his
nod before daemon.py's dispatch loop changes, not a blind default.

**Slice 1 shipped 2026-07-06** (nod given same thread as the proposal
above; PR: `brr/sub-spawn-slice1-2026-07-06`, unmerged pending review).
`spawn:` outbox frontmatter (sibling to `respawn:`, forced `worker: true`,
refuses nesting from a worker-stack caller) queues an event tagged
`spawn_immediate`; the daemon's `start()` loop now runs a `pool =
ThreadPoolExecutor(max_workers=2)` with a second `current_spawn` slot
scanned every tick independent of the resident's own `current` — capped
at 1 by the `current_spawn is None` gate, exactly as recommended, not
relaxed further. One inbox scan per tick still feeds both dispatch
decisions (spawn-marked vs. resident-lead events partition the same
scan) rather than doubling `list_pending` I/O. `run_ledger` gained
`parent_run_id`/`is_subspawn` (additive, per the "cost per core becomes a
rollup" resolution above). Completion notify reuses existing plumbing
exactly as item 4 envisioned: a plain pending inbox event tagged with the
parent's own `conversation_key`, picked up on the parent's next
`inbox.json` read if still running, or dispatched as the next ordinary
run if the parent already ended (a live improvement on the guessed-time
review self-wake for this one case, not a replacement for it generally).

Two gaps named rather than solved in this slice, both lower-stakes than
the dispatch-loop change itself: (1) `runner.kill_active()` tracks a
single module-global active subprocess, so daemon shutdown doesn't
prompt-reclaim a live spawn's process the way it does the resident's own
— `pool.shutdown(wait=True)` still drains it, just without the same kill
signal; (2) the main-loop dispatch wiring itself (the two-slot scan/cap
logic) has no automated end-to-end test — consistent with the rest of
`start()`'s loop, which wasn't unit-tested at that level before this
slice either (one pre-existing test, `test_start_preserves_error_event_
status`, exercises the loop directly and stayed green). The queueing
(`_queue_spawn_request`) and notify (`_notify_spawn_parent`) halves are
unit-tested; the loop-level wiring is code review + that one pre-existing
regression test, not a purpose-built integration test.

### #257 merge race, and spawn: can't be dogfooded in the run that lands it (2026-07-07)

Two related findings from the first real attempt to *use* the slice above,
same day it should have first become reachable.

**Finding 1 — #257 was marked MERGED on GitHub but never reached `main`.**
It was stacked on #254's own feature branch
(`brr/run-ledger-cost-tracking-2026-07-06`); the maintainer's own
github-web merge landed #257 onto that branch *after* #254 had already
been squash-merged off it onto `main` — so GitHub correctly reports #257
"merged" into a base that itself was orphaned. `git diff main <257's
head>` still showed the whole 447-line diff missing. Fixed by cherry-
picking the one commit unique to #257 onto a fresh branch off `main`
(PR #260, tests green, merged) — a real gap in "PR shows merged" ⇒ "code
is on main," worth remembering whenever a PR is stacked on another PR's
branch rather than on `main` directly: squash-merging the base out from
under a still-open stacked PR silently strands its commits.

**Finding 2 — a long-running `--dev-reload` daemon can't dogfood code it
hasn't reloaded, and reload is deliberately deferred.** Once #257/#260
were live on `main`, dispatching a `spawn: true` outbox message against
*this same run's* daemon process (up since before the merge) silently
failed: the running process's in-memory `daemon` module predates
`_queue_spawn_request` entirely, so the unrecognized `spawn:` frontmatter
key fell through to the plain-reply path and delivered the internal task
spec as two ordinary chat messages instead of dispatching anything —
confirmed by `current`/`other` delivery counters incrementing instead of
a new presence entry or `.brr/inbox/*.md` file appearing. Root cause:
`start()`'s dev-reload watcher only re-execs once `current is None`
(`src/brr/daemon.py` ~line 4603) — by design, so a live reload never
kills an in-flight thought — but that means the very run that first lands
code enabling a new dispatch primitive can never observe that primitive
working, on a long-lived dev daemon: the process has to finish this run,
restart into fresh code, and *then* a later wake can dispatch. Not a bug
to fix so much as a sequencing fact to remember: **a `spawn:`/`respawn:`
capability change and the first live test of it are structurally two
different wakes**, whenever the daemon is a persistent `--dev-reload`
process rather than a fresh-process-per-run production deploy. Documented
in the account dominion playbook (§Delegation) so a future wake doesn't
repeat the same "why did my spawn leak into chat" investigation from
scratch. The actual spawn-dispatch test (does a `spawn:` outbox message
correctly start a concurrent worker-stack child) is still open, queued for
whenever a wake follows a `main`-advancing merge by more than "the same
run."

**Addendum 2026-07-07 — the gap is broader than "spawn's own code
landed this run."** A second retry, same daemon restarted fresh (so
Finding 2's own scenario didn't apply — `_queue_spawn_request` was
already live in the running process's memory), still stalled: a
`spawn:` event was created correctly (confirmed: `evt-...-lr16` appeared
in `.brr/inbox/`, status `pending`, all frontmatter parsed right), but
sat undispatched for the rest of the run. Root cause is one level
deeper and more general than Finding 2's: `DevReloadWatcher.changed()`
(`src/brr/dev_reload.py`) snapshots the mtime/size of every `.py`/`.md`
file under `src/brr/` on every loop tick, and the main dispatch loop
gates *both* the resident slot and the concurrent-spawn slot on
`not reload_requested` (`src/brr/daemon.py` ~line 4625/4637) — deliberately,
so a live reload never races a running thought. But `reload_requested`
flips true the instant any watched file's mtime changes, independent of
whether the edit is committed, staged, or even correctness-relevant to
`spawn:` itself. This run edited `src/brr/runner.py` (an unrelated fix,
the argv/E2BIG crash) *before* dispatching the spawn — that edit alone
was enough to latch `reload_requested`, which then blocked the spawn
for the rest of the run, since reload itself requires `current is None`
(this same thought to end) and dispatch requires `not reload_requested`.

Net: on this repo's `--dev-reload` daemon, **any resident thought that
both edits brr's own package files *and* wants to use `spawn:` in the
same thought will have the spawn stall** — not just the thought that
lands `spawn:`'s own code, any thought that touches any watched file at
all, which for a project whose job is improving itself is close to "most
substantive resident turns." Confirmed by direct inspection, not
inferred: `_take_snapshot()` has no filter beyond suffix/name, and both
gates above read the same shared `reload_requested` local.

Not fixed this run — a real design question, not a bug to blind-patch:
decoupling the spawn-dispatch gate from `reload_requested` is plausible
(a spawn is a separate subprocess; it doesn't share the resident
process's image the way re-exec does, so running it against
momentarily-stale in-memory daemon code carries little of the risk that
motivates deferring *re-exec*) — except when the very edit that tripped
the watcher *is* a change to spawn-dispatch logic itself, in which case
dispatching under the stale code could silently reproduce whatever bug
prompted the edit. That trade-off (availability vs. staleness risk) is
value-laden enough to name back rather than decide unilaterally. Left
open: whether to split "gates re-exec" from "gates the spawn slot" into
two independent flags, or accept that this repo's own dogfooding of
`spawn:` is structurally rare (any resident code-editing turn kills it
for that turn) and lean on the review-self-wake fallback instead. The
actual spawn-dispatch test is *still* open after two attempts across two
different structural causes.

**Addendum 2026-07-07 (run-260707-0959-mnrr) — the spawn-dispatch test
closed, third attempt, across the reload boundary.** The `evt-...-lr16`
spawn stuck `pending` in Finding 2's addendum above wasn't lost: once
run-260707-0911-rdw4 ended and `brnrd up --dev-reload` reloaded into the
now-committed `spawn:` code, the queued event dispatched on its own, ran
codex to completion (`status: done` in the inbox record), and pushed PR
#263 (`brr/live-runs-label-2026-07-07`) — a real, bounded backend+frontend
slice (per-run `label` field on the live-runs dashboard card), not a toy.
A follow-up run (this one, self-scheduled via the `at:` entry the
dispatching run left in `schedule.md`) reviewed the whole diff directly
(not the worker's own summary — see `dominion-playbook.md` §"Reading
economically" exception for spawned diffs), re-ran the full pytest suite
(1341 passed) and `npm run build`/`lint`/`check` independently rather than
trusting codex's self-report of the same, confirmed it matched the brief
with one disclosed, correct deviation (codex caught that
`schemas.py::LiveRunIn` would've silently dropped the new `label` field —
a real schema boundary the brief's own "all pass-through, no allowlists"
framing missed), and merged it (`f167503`).

So: `spawn:` dispatch works end to end — a cross-Shell child queued while
its parent's own daemon process was still mid-run, survived across a
reload boundary between two different resident thoughts, completed
unattended, and reached a resident review/merge decision exactly along
the wait-and-review contract this page's Finding 2/addendum motivated
tightening in the boot prompts. What took three attempts wasn't `spawn:`
itself — it was this repo's own dev-reload gating (Findings 1/2 and the
addendum above), which is now a known, named cost of dogfooding on a
persistent `--dev-reload` daemon, not a spawn-primitive defect. The
`reload_requested`-vs-spawn-gate design question named above is still
open; it didn't need resolving for this test to close.

**Addendum 2026-07-07 (run-260707-1321-auhp) — a fourth spawn attempt
dispatched clean, then died silently mid-run; also confirms a real
`environment=host` concurrency hazard.** Dispatched live in response to
the maintainer's own "sub runs should work... hopefully it holds":
`spawn: true`, `shell: codex-mini`, a bounded #201 (worktree-hygiene
dry-run tool) task. Confirmed genuinely concurrent via `pstree` — a real
`codex`/`node` process tree alongside this thought's own `claude`
process, `Status: running` in its own run context, no dev-reload stall
(this thought hadn't touched `src/brr/*.py|*.md` before dispatch, so
Finding 2/3's gate never latched). ~9 minutes later the entire child
process tree had exited: zero commits on the branch it created, no
response file, **no completion or crash-notification event** — silence,
not a signal, despite `_notify_spawn_parent_of_crash` (this same day,
PR #266) supposedly covering exactly this shape. Working hypothesis, not
confirmed: that fix only fires when the daemon's own tracking future
*raises*; a codex child that simply exits (clean or otherwise) without
the wrapper treating it as an exception would slip past both the success
and crash notification paths. Narrower and different from Findings 1-3
(all dispatch-gate problems) — this one dispatched fine and died after.

Also confirmed live, not hypothetical: this repo's `.brr/config` has
`environment=host` (`HostEnv.prepare`: `cwd=repo_root`, no worktree
isolation — `src/brr/envs/__init__.py`), and nothing in
`_queue_spawn_request` overrides that for a spawned child. Mid-run, `git
branch --show-current` in this thought's own shell flipped from `main` to
the spawned child's `brr/worktree-hygiene-report-2026-07-07` — the child
had run `git checkout -b` in the *same* working directory this thought
was mid-edit in. Recovered without loss only because the child had made
zero commits (`git checkout main` was a no-op content-wise); had it
committed first, this thought's own uncommitted kb edit would have ridden
along into the child's branch, or a genuine checkout conflict could have
surfaced. `spawn:` is the one primitive that deliberately breaks
single-flight; `environment=host` giving it zero working-directory
isolation is a real, not theoretical, collision surface — worth a
scoped fix (e.g. `spawn:` always launching under `WorktreeEnv` regardless
of the repo's own `environment=` config) before this is exercised again
outside a lucky zero-commit race.

**Addendum 2026-07-07 (run-260707-1033-jyzb) — "no reply" root-caused: a
bare `done` stub, not a delivery failure.** The maintainer reported both
run-260707-0911-rdw4 (11:37 CEST) and the #263 review self-wake above as
having "not produced any reply," read as a possible harness regression
worth checking alongside re-testing `spawn:`. It wasn't a crash and
nothing was lost: both runs shipped their real content via outbox interim
messages mid-thought (confirmed live in the conversation-log JSONL —
substantive `interim_response` artifacts are there, timestamps line up),
but each then closed its *terminal* stdout with a bare `done`/`done.`
stub. The multi-response contract (`kb/design-multi-response.md` §Streaming
delivery) already has a clean path for this — a genuinely empty terminal
stdout skips the closeout message entirely once outbox already delivered
the substance — but a trivial *non-empty* stdout isn't that path:
`deliver_stream` still ships it as one more real, separate message
(`protocol.read_response` returns non-None, `deliver_terminal` fires). So
the literal last thing to land in the thread, both times, was an empty
word — reading as "nothing happened" from the reader's seat even though
real work had, and specifically explains why the maintainer's own
timestamp match (11:37 CEST) landed exactly on the terminal artifact, not
the substantive interim ones sent minutes earlier.

Root cause is prompt discipline, not code: `daemon-substrate.md`'s
next-move contract already banned "a bare status word" as an ending, but
didn't name this specific split-the-difference trap (non-empty-but-
contentless close, after the substance already shipped elsewhere) —
apparently a real enough gap to hit twice in one day across two different
resident thoughts. Fixed at the source: `src/brr/prompts/daemon-
substrate.md` §next-move now names the failure mode explicitly and states
the two clean options (genuinely empty stdout, or a real one-line
receipt) — nothing in between. This run's own closing reply follows that
rule rather than repeating the pattern it just diagnosed.

Also acted on directly, same run: the maintainer's "own the sub runs,
proper workers interface" ask (asked because this exact silence read as
the mechanism failing) — dispatched another `spawn:` (codex, #259
PR-review-queue dashboard lane, mirroring the Activity/Plans/Quota/Live-
runs publish shape a fifth time), *claiming* the resident would linger
in-run to review and fold in per the wait-and-review contract.

**Correction (2026-07-07, run-260707-1158-alaq) — that claim was false,
caught the same day.** jyzb did not linger: it ended ~4 minutes after
dispatching, terminal stdout a bare `-`, having never polled
`inbox.json`/`portal-state.json` for the spawn's completion at all — the
exact "linger claimed, not exercised" gap the review self-wake it left
behind (`schedule.md`, since retired) named as its own reason for
existing. Leaving the incorrect "convention exercised, not just
re-stated" sentence uncorrected above would have been the same failure
this whole addendum chain is about: a false "this works now" statement
sitting in durable memory for a later wake to trust without re-checking.
See the addendum immediately below for the resolution and the deeper
bug it led to.

**Addendum 2026-07-07 (run-260707-1158-alaq) — the deeper bug: a crashed
spawn never notified its parent at all.** Reviewed #259 as the review
self-wake asked: diff read directly, backend suite (1346) and frontend
build/lint/check all independently green, matched the brief, merged as
PR #264. But *why* did jyzb's dispatched spawn (`run-260707-1053-sx2c`)
need a manually-authored self-wake as a safety net in the first place,
given `_notify_spawn_parent` is supposed to land an ordinary pending
event in the parent's conversation regardless of whether the parent is
still running? Traced it: `sx2c` never reached a clean finish (context.md
still says `status: running`; no response file, no outbox — consistent
with the maintainer's own "I killed all the active runners" a few
messages later). Reading `start()`'s reap loop
(`src/brr/daemon.py` ~4602) found the actual gap: `_notify_spawn_parent`
was only called in the *success* branch of `current_spawn.result()` — a
worker future that raises (crash, kill, launch failure) hit the `except`
branch, which only `print()`s to the daemon console. No event, no
signal, nothing lands in the parent's thread. The design's own promise
("Concurrent sub-spawns" item 4 above: "a child-completion notification
delivered into the still-running parent thought") says *completion*, and
a crash is a form of completion; the code only handled the happy path.
Fixed in `_notify_spawn_parent_of_crash` (PR #266), built from the raw
inbox event dict rather than a `Run` object (a crashed worker never
produces one) — wired into the same reap branch. This is the one
"spawn: doesn't behave as agreed" gap this day's reports actually
resolve to: not a design/prompt drift, a real one-branch code bug, now
closed and regression-tested
(`test_notify_spawn_parent_of_crash_lands_pending_event`).

Same run also closed the companion "why didn't you remember this" thread
(`kb/log.md` §2026-07-07 "recent-turns crowding bug") — the wake-prompt's
inline "recent turns" snippet was, separately, capable of showing *zero*
real recent dialogue on a busy thread, which is the more likely
explanation for why an already-settled design point needed re-surfacing by the
maintainer instead of being remembered. Both fixes landed the same run;
neither alone was the whole story.

**Addendum 2026-07-08 — the `reload_requested`-vs-spawn-gate question
(Finding 2/3, ~line 445 above) decided, not left open a third time.**
This section's own verdict ("value-laden enough to name back rather than
decide unilaterally") held through the 2026-07-08 Gap-1 closure run,
which fixed the worktree-isolation collision but explicitly parked this
gate coupling as a fork again. Same-thread follow-up asked directly for
the "reload_requested silently stalls spawn dispatch until next wake"
gap to be rethought and delegated the actual call: *"whatever a run
spawns it should wait on, to own and complete the work, but it should
not be crippled, or blocked by a daemon. the daemon should do [the]
little possible work there, we just need to make sure the runs don't
step on each other's toes."* Decided: unconditional decoupling — the
concurrent-spawn slot no longer reads `reload_requested` at all; the
resident slot and re-exec itself still do, since a fresh resident thought
genuinely runs inside the soon-to-be-replaced process image and a spawn's
real work (a separate `claude`/`codex` subprocess) never does. This
supersedes the two-shapes framing above (B1's narrower flag-split vs. B2's
status-quo) with a third option neither named: remove the coupling
entirely, since Gap 1's `environment: worktree` force already delivers
the actual toe-stepping protection the vision asked for, and the standing
review-before-close contract plus the crash-notify path (PR #266) already
bound the narrow staleness risk B1 was designed to catch. Full reasoning,
the decoupling's own reasoning about why B1's "considered narrow
carve-out" framing was itself answering the wrong question, and the
regression test that fails against the pre-fix gating and passes against
the fix: `kb/plan-spawn-gap-closure.md` §"Addendum (2026-07-08) — decided:
unconditional decoupling, not B1". Not re-duplicated here — this entry is
the pointer, that page is the receipt.

## Hot-idle residency and quota-aware pacing (maintainer, 2026-07-02)

Follow-up sharpening the stingy-director economics: if the wake already
spawned in a strong core, downshifting mid-conversation buys nothing — the
paid asset is the assembled context. The proposal: a wake that, instead of
terminating, idles hot (`while n < 100: sleep 30; check portal; act if
input; n++`) — near-free residency because the conversation is already
paid for — plus proactive loops paced by *observed* quota/allowance data
rather than fixed intervals.

Scrutiny, held against the current machinery:

- **The cache economics are real but have a 5-minute cliff.** Provider
  prompt caches (~5m TTL) make a 30s poll loop genuinely cheap: each
  iteration pays only new tokens. Past the TTL, every iteration re-reads
  the full context uncached. So hot-idle is a *short-horizon* instrument —
  minutes, not hours — exactly matching the maintainer's own "it should
  occasionally terminate" caveat (context drift, cost accumulation).
- **The slot is the scarcer resource.** Under single-flight, a hot-idle
  wake occupies the run slot; a queued unrelated event waits behind a loop
  that is mostly sleeping. Hot-idle should yield when `portal-state.json`
  shows unrelated pending work — the fold-in contract already reads at
  plan boundaries; an idle loop must too.
- **Quota visibility exists as data.** `claude_usage` / `claude_status` /
  `codex_status` already extract shell-reported usage and limits. What's
  missing is the *policy seam*: feeding those data points into wake pacing
  (schedule intervals, proactive-loop budgets, core selection) instead of
  only into runner availability. That is the concrete follow-up — a
  consumption-aware input to `schedule.md` cadence and respawn class,
  tied to the co-maintainer workstream's standing-loop idea.
- **Partially built already:** `.keepalive` extends a run past budget, and
  the daemon re-invokes on tracked completions; what does not exist is a
  sanctioned in-run idle-poll pattern. If adopted, it should be a named
  contract (max iterations, TTL-aware sleep step, yield-on-unrelated-work)
  rather than each wake improvising a `while` loop.

Direction: agree in principle as a *short* post-delivery linger (catch the
follow-up that arrives 40 seconds after the reply — today that spawns a
cold run), not as long residency. Shipped shape has two layers: runner-owned
linger for true same-thought fold-in, and daemon-owned `attending` for the
post-return safety net/card truth. The quota-aware pacing piece deserves
its own design pass; it is policy on existing telemetry, not new
infrastructure.

Telemetry update (2026-07-03): the Claude `/usage` PTY probe is down from
~18s to ~3.5s, its cache TTL now 10s (maintainer's call — under the 30s
heartbeat any TTL means "probe every beat", so 10s is the freshest the
beat can deliver; `BRR_CLAUDE_USAGE_TTL` to override), and the parser now
keeps per-model weekly buckets separate — the TUI added a `Current week
(Fable)` line that previously clobbered the all-models number. Pacing
policy can now read a per-Core weekly constraint (the binding one for a
Fable-cored director), fresh to one beat, without new collection work.

**Maintainer reaffirmed the target shape (2026-07-04):** restated the
end-state directly — trigger (a message, or a tag on a ticket/PR) starts a
session; the resident does the work cost-permitting; the human's loop is
review/clarify/merge, "the self-hosted co-maintainer"; the session "stays
open for a long time... reset or restart occasionally," bounded mainly by
context window, not per-message termination. Checked against what's built:
the trigger half is already there (forge issue/PR events spawn a full
resident run with no dispatcher hop, per
`decision-account-centered-daemon.md` §3's routing table) and the
review/merge loop is already the `gate: forge` PR handoff. The *residency*
half is still deliberately short of this vision — B5 shipped a ~10–15m
linger plus a 90s post-return attending floor, not the long-session-with-
occasional-reset shape described here, precisely because of this section's
own cache-cliff economics (past ~5m every idle iteration re-reads full
context uncached). That scoping-down was correct as a v1 guardrail, not
necessarily as the destination — the maintainer's restatement reads as
"the short linger is a stepping stone, not the target." Revisiting it
productively needs B6's data (can the quota afford longer residency?) and an
explicit reset policy (context-window pressure or a scheduled cadence,
not "the runner returned"). Not re-scoped yet — named here so the next pass
on hot-idle residency starts from "this is still the standing ask," not
from a stale "short linger settled it."

Execution tickets for this design:
[`plan-director-execution.md`](plan-director-execution.md).

## B1 — quota-aware pacing policy (decided 2026-07-04)

The policy half of [#214](https://github.com/Gurio/brr/issues/214), written
against the telemetry that landed 2026-07-03 (per-Core weekly buckets, 10s
TTL). Scrutiny while writing it: `_merge_level_snapshots`
(`daemon.py:2436`) currently forwards the `quota` key from a Shell's level
snapshot wholesale but the snapshot itself
(`claude_usage.parse_usage_text`, `codex_status.parse_token_count`) only
ever put a rendered *string* summary in that dict — the numeric
`used_percentage` fields computed a few lines earlier
(`session_used_percentage`, `week_used_percentage`, `week_models[label]`)
never made it past the parser function. So today there is genuinely no
programmatic access to "how low is the binding bucket" downstream of the
collector — only a human-readable line. B2 needs to close that gap before
any pacing decision can read a number instead of parsing prose.

**Binding bucket.** The lowest live remaining-percent among: session,
week (all-models), and any active per-model week bucket (Codex: primary +
secondary rate-limit windows). "Remaining" always means `100 -
used_percentage`; a shell with no collector for a slot contributes
nothing (never guessed).

**Two floors, account policy, not hardcoded** (mirrors the
`delivery.post_delivery_attend_seconds` convention — dotted key, sane
default, `.brr/config` overridable):

- `pacing.quota_low_floor_pct` (default `20.0`) — below this, `every:`
  schedule entries stretch: the due-check uses `interval *
  pacing.quota_stretch_factor` (default `3.0`) instead of the entry's
  stated interval, so a standing loop backs off without being silenced.
- `pacing.quota_critical_floor_pct` (default `8.0`) — below this, `every:`
  entries do not fire at all this beat (ambient loops pause). Recovery
  above the floor resumes normal cadence on the next beat; no separate
  "resume" bookkeeping needed since the check re-evaluates live each beat.

**What is never discretionary:** `at:` one-shot entries (deadlines,
reminders) and anything gate-addressed (a real user waiting on a reply).
Quota pressure bends *ambient* initiative, never a promise already made to
someone.

**Respawn core class.** Downshifting is resident policy (B3), not a new
daemon mechanism — B1 only supplies the number the resident's own
delegation judgment reads (the Mode block's `Quota:` line already carries
it). A daemon-side automatic override of a resident's explicit `shell:`/
`core:` respawn choice is out of scope here; it would second-guess a
judgment call the resident is better placed to make with the full picture
(task shape, not just quota).

**B2 scope (plumbing, delegable):** thread the buckets through
(`claude_usage`/`codex_status` → `quota` dict → `_merge_level_snapshots` →
`_fire_due_schedules`), add the floor/stretch config readers, apply the
stretched interval (or the pause) only to `kind == "every"` entries before
calling `schedule.due_entries`, and surface the binding percent + which
floor (if any) is active in `resources` so a mid-run boundary can see the
same number the scheduler used. Full spec: `plan-director-execution.md`
§B1–B2 depends-on note; exact touch points named in the B2 delegation
brief (kb/log.md, this date).

## Cache TTL vs compaction, and B6's data problem revisited (2026-07-04)

Maintainer question (telegram): does idle wall-clock time itself get billed
while a permission-gated session waits on a user reply, and is TTL-eviction
the same thing as compaction? Two separate mechanisms, worth naming apart:

- **Cache TTL eviction** (~5m Anthropic, similar order for Codex) is
  time-based: no request arrives within the window ⇒ the *next* request is a
  cache miss, priced as a full uncached input read. Idle time itself is not
  metered — nothing is billed while no call is made. The cost is deferred,
  not incurred, and it only lands as "more expensive," never as "charged for
  waiting."
- **Compaction** (context summarization when the window fills) is
  capacity-based, triggered by accumulated tokens, unrelated to how long the
  session sat idle. Conflating the two overstates the cost of a long
  permission-gated wait — the real tax is only the next-call cache miss, and
  only if the wait outlasted the TTL.

This confirms rather than revises §Hot-idle residency above (the 5-minute
cliff framing there was already right).

**B6 ("blocked on data... a week+ of observed per-runner burn"): partially
already unblocked.** Checked `$CODEX_HOME/sessions/**/rollout-*.jsonl` on
the operator's machine: 69 of 88 recent rollout files (2026-06-20 through
2026-07-04 — the actual dogfooding window, not a guess) have `cwd` under
this repo's worktrees, and every one carries `token_count` events with
`rate_limits.primary`/`secondary` (used_percent, window_minutes, resets_at)
timestamped per turn. That is a real ~2-week time series of Codex quota
burn already sitting on disk, retroactively minable — no forward waiting
period needed for the Codex half of B6. A one-off script over existing
rollout files, not new collection, not a bench.

Claude side has no equivalent: `claude_usage`'s PTY scrape of `/usage`
returns only the current snapshot, nothing persisted historically. Claude
session transcripts (`~/.claude/projects/**/*.jsonl`) do carry per-turn
token/cost usage, which could reconstruct relative burn *rate* but not
percent-of-weekly-cap (that arithmetic lives inside Anthropic's own
`/usage` rendering, not in the transcript). So: Codex's half of B6 can be
answered now from history; Claude's half still needs forward logging
(cheapest shape: persist the already-computed `claude_usage` snapshot to a
durable log on each heartbeat, starting now, rather than waiting on a new
collection mechanism).

**No new bench needed for this.** The maintainer's "do we need a bench?"
reads as a different question than [`design-bench-loop.md`](design-bench-loop.md)
answers — that bench measures prompt/protocol seam-following under a
lesser-light runner (card discipline, fold-in, next-move), not quota
economics. What B6 needs is data extraction (Codex: retroactive script over
rollout files; Claude: a forward log line) and then a policy pass over that
data — not a scenario harness that spends quota to observe behavior we can
already read off disk.

## Forks left to the maintainer

- None hard-blocking for phases 1–2. Phase 3's physical file location has a
  parked sub-fork (CS5) in the account-daemon decision — confirm on
  execution.
- ~~Whether option sets render as plain numbered text everywhere or as native
  buttons on gates that support them (Telegram inline keyboards)~~ —
  **settled 2026-07-03 (maintainer): plain numbered text everywhere.** A
  compact numbered closeout invites exactly the reply shape the loop wants —
  free-form, multi-part, composable ("1a 2a 3c and do x please") — while
  inline buttons collapse the exchange to one tap per option set and fight
  the mixed reply. The MUD instinct was right; the tech is ready now.
  No button rendering work is planned.
