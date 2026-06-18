# Co-maintainer — one perceived continuity, many runner actors

Status: accepted on 2026-06-13 — north-star synthesis from a close-loop
design session, accepted with refinements folded back in (see §4.4, §5, §6,
§8, §11). Sequences the next milestone (GitHub milestone "Co-maintainer")
and is the umbrella for the tracking issues it names. Supersedes the
approach taken in the closed PR #107 (which tried to fit all gate history
into the wake context; see §4).

## 1. The entity we're building

brr already has a *self*: the resident agent, whose durable memory is the
forge-backed dominion and whose every wake is a runner woken by an event or
a self-scheduled cron (see [`design-agent-dominion.md`](design-agent-dominion.md)).
The next milestone turns that self into a **co-maintainer** — a cybernetic
autopilot a human works alongside on a project, across every channel the
project speaks through at once.

The organising metaphor, in the user's words: **one perceived continuity,
many runner actors — like a peer working on the same forge project.** You
don't track which process your human collaborator is; you see one
continuous colleague through the forge: issues, PRs, commits, comments,
chat. Each brr wake is an ephemeral actor, but the human should perceive a
single collaborator with memory, taste, and awareness of everything in
flight.

What that collaborator can do when it's whole:

- Hold **several conversations at once** (Telegram, Slack, GitHub
  issue/PR threads, brnrd-relayed cloud chats) and understand how they
  relate — that a PR comment and a Telegram message are about the same
  work.
- Be **aware of the forge**: open issues, open PRs, local commits, its own
  worktrees, what's landed and what's in flight — and cross-reference work
  in progress against all of it.
- **Resolve conflicts smoothly** — between channels, between its own
  branches, between a stale assumption and a fresh message.
- Carry **complete context** without that context being a firehose: enough
  to act well, retrievable in depth when it needs more.

Most of the substrate exists. This doc is the **connective tissue** plus
the specific gaps that, closed together, make the co-maintainer real. It is
a release-worthy milestone, not a single feature.

## 2. What we already stand on

This builds on shipped work; it does not re-design it. The pieces:

- **The resident self** — [`design-agent-dominion.md`](design-agent-dominion.md):
  durable dominion memory, single-flight reflex/deliberation, the playbook
  as the convergence point.
- **Three layers, one driver** — [`plan-playbook-generalization.md`](plan-playbook-generalization.md):
  the playbook is the resident (host-agnostic core); brr is one driver; and
  `brr agent inject` hands any wrapper brr's assembled wake-context. *The
  layer model in §3 is this work, taken to its logical end.*
- **Delivery that isn't one-shot** — [`design-multi-response.md`](design-multi-response.md):
  interim + multiple + interleaved responses via `.brr/outbox/<eid>/`.
- **Proactivity + ownership of sync** — [`design-self-scheduled-thoughts.md`](design-self-scheduled-thoughts.md):
  the resident owns its schedule and its `brr-home` sync + conflict
  resolution.
- **Conversations as routing + history, not identity** —
  [`decision-drop-streams.md`](decision-drop-streams.md) and the per-event-pipeline
  log in [`../src/brr/conversations.py`](../src/brr/conversations.py).
- **Managed delivery + cards** — [`design-managed-delivery.md`](design-managed-delivery.md):
  daemon-side card lifecycle, two transports (direct / brnrd relay), under
  brnrd's data-minimization stance.
- **The GitHub boundary** — [`design-github-gate-vs-brnrd-app.md`](design-github-gate-vs-brnrd-app.md)
  and **PR #106** (`brr/managed-github-routing`, open): managed GitHub
  comments route like addressed ingress — addressed issue/PR comments
  enqueue, responses return to the GitHub thread, and the cloud gate
  expands GitHub `reply_to` blobs into local inbox frontmatter carrying
  **repo, issue, comment, trigger, PR number, branch_target**. That
  metadata is the raw material for forge-awareness (§5).
- **Introspection** — [`design-context-introspection.md`](design-context-introspection.md):
  the opt-in "look at it" wake stance.

## 3. Three layers, and who owns each

The co-maintainer's context comes from three layers that must stay legible
and not bleed into each other. [`plan-playbook-generalization.md`](plan-playbook-generalization.md)
named them; the principle to hold:

1. **Resident core (the self).** The playbook, the dominion, taste,
   memory. Host-agnostic. Owned by the resident — it reshapes its own
   playbook. brr only injects it.
2. **brr driver (the substrate).** Wake, deliver, branch, sync, the Run
   Context Bundle, the capture net. Owned by brr. This is *how* the self is
   run here; it is not the self.
3. **Dev introspection (opt-in).** The "look at it" stance — a development
   aid layered on top, not a production wake stance.

Each injected block is tagged with where it came from; that provenance is
what introspection asks the resident to see whole. Today the layers bleed
in two known ways, both folded into the work below:

- Operational "how-to" (the driver's mechanics) is thin in the wake prompt,
  so the resident re-derives substrate facts it should be told once.
- `brr agent inject` — the natural "show me my context" tool — assembles a
  *different* set of blocks than a real wake (it omits the mode toggles,
  diffense and introspection; see §10).

## 4. Continuity & communications — the heart

### 4.1 What's broken today

The conversation log is **lossy at the data layer**, not just the render
layer. Anchored in [`../src/brr/conversations.py`](../src/brr/conversations.py):

- **User messages are truncated to one line.** `append_event` stores
  `summary = body.splitlines()[0]` — the rest of the message is never in
  the conversation memory.
- **The agent can't see its own words.** `append_artifact` stores a file
  `path` + `label`, no inline reply text. Past dialogue turns are
  references, not text — so the resident reconstructs a thread it half
  can't read.
- **Liveness pollutes memory.** `append_update` writes every lifecycle
  packet — including the 30-second `heartbeat` — into the *same* per-event
  jsonl as real turns. The heartbeat exists for hung-run detection and to
  bump the chat card's elapsed counter (see the daemon module header); it
  has no business in conversation memory.
- **The tail is kind-blind.** `read_recent(limit)` merges all kinds and
  takes the last N. A burst of heartbeats / lifecycle rows **evicts** the
  user and agent turns — the exact failure the closed PR #107 tried to
  band-aid by widening the budget.
- **One human reads as two threads.** `gate_thread_key` keys a local
  Telegram chat as `telegram:<chat>:<topic>` but the same chat relayed
  through brnrd as `cloud:telegram:<chat>:<topic>`. Same person, two
  conversation directories, split history.
- **Cross-gate FIFO vs per-gate keys.** The inbox is a global FIFO by
  mtime under single-flight, while conversation keys are per-gate. An
  unacknowledged event from gate A sits pending while gate B runs, and a
  reply can be attributed to the wrong thread when history is thin or
  wrong-keyed. (The delivery half of this is §6.)

### 4.2 The chosen shape — between firehose and full synthesis

The user framed two poles and asked for the middle:

- **Full unification** = the complete real picture injected as one context
  item (a firehose; burns the budget, drowns signal).
- **Hybrid** = the unified records are "processed" — the resident is asked
  to think them into a synthesis every wake (costs a deliberation step).

**We sit between them**, with three tiers:

1. **The communication snapshot (in-context, curated).** An *elegant,
   functional* cross-channel picture handed to the resident at wake: who is
   talking on which channel, what is pending and what relates to what, the
   recent turns woven as a real chat (user and agent turns interleaved,
   untruncated within a sane budget), and the forge state that frames it
   (§5). Curated for action — not the whole history, not a synthesis the
   resident had to generate.
2. **Deeper history as on-demand records, grouped by input type.** The full
   untruncated store behind the snapshot, **grouped into jsonl by input
   type** (per source / per gate / per forge thread) with clean file
   interfaces, so the resident can pull "all of GitHub PR #N" or "all of
   this Telegram chat" exactly when it needs more than the snapshot shows.
   Retrieval cost is paid only on demand.
3. **A resident-maintained thread of record (durable).** The
   project-level narrative the resident curates **in its dominion** —
   like a peer's running notes — the durable "what we're doing together"
   that survives across wakes and channels and is the resident's own, not
   brr's. Decided 2026-06-13: resident-curated working memory, not a
   human-facing forge artifact.

This honours the robustness=retrieval-cost hierarchy from
[`design-environment-shaping.md`](design-environment-shaping.md): cheap,
high-signal at wake; full fidelity one read away; durable synthesis where
the resident chose to write it.

Shipped 2026-06-14: the daemon now builds a structured
`CommunicationSnapshot` from the current thread plus sibling threads for the
same correspondent, writes untruncated per-gate/per-forge-thread JSONL files
under the run directory with a manifest, and points the prompt/context at the
resident's dominion `thread-of-record.md` slot. The snapshot's recent-turn
selection is still budgeted, but unanswered user events get a strong boost
over pure recency. The forge-state facet remains §5 / #113, not part of this
slice.

Extended 2026-06-14 (#131): the snapshot gained a **prior-failure facet**. When
the most recent terminal run outcome on the current thread was an *operational*
failure (runner crash / env setup / retry exhaustion — the daemon's `failed`
packet, never a normal noop or a push `conflict`), `prior_failure` carries its
structured reason (error detail, attempts, exit code, timeout flag, stage,
timestamp) and the bundle renders it as one prominent `⚠ Prior run on this
thread failed` line near the top — so a wake landing after an interruption opens
knowing it. No new persistence: the terminal `failed` update packet already lands
in the per-thread conversation jsonl; the builder walks back to the first
terminal outcome and surfaces it only when it was a failure, so a later success
clears a stale one.

**kb optional → collapse into the dominion.** In theme with this: the
shared `kb/` may become optional (a `brr init` toggle / setup choice — see
issue #105). When it's off, the semantic + decisional layer has nowhere
committed to live but the dominion, so it **collapses into the dominion** —
the thread of record and any durable synthesis become dominion-only, and
the wake snapshot draws from there. The tiers above are unchanged; only
*where the durable layer lives* moves.

### 4.3 Persistence refactor

To make the three tiers possible, history persistence changes (the
"refactor the persistence slightly, keep turn-taking clear, clean file
interfaces" the user asked for):

- **Store full message text** on inbound events, not a first-line summary.
- **Store agent reply text inline** on response artifacts (keep the path
  too), so turns are readable as a chat, agent turns woven between user
  turns.
- **Split liveness out of conversation memory.** Heartbeats and pure
  lifecycle packets stop being conversation records; they become daemon
  liveness/card state (§4.5). What stays in the log is the dialogue and the
  task milestones worth remembering.
- **Make the tail kind-aware.** The snapshot builder selects *dialogue
  turns* first and treats lifecycle separately (collapsed or dropped), so
  no eviction-by-noise.
- **Clean read interfaces.** One for "the snapshot," one for "the deep
  records by input type" — distinct from the append path, so callers don't
  re-derive shape.

### 4.4 Per-correspondent identity (multi-user) and channel redundancy

A conversation key answers "which thread," not "which person." The
co-maintainer must work on **multi-user projects**, so identity is a layer
*above* conversation keys: each turn carries a **correspondent identity**
(username / usertag / user id — for cloud users especially), so the resident
knows *who* it's talking to and several people can share one project. This
is daemon-side and respects brnrd's data-minimization stance — brnrd holds
the metadata graph, not the content (see
[`plan-conversation-id-propagation.md`](plan-conversation-id-propagation.md)
and [`subject-managed-mode.md`](subject-managed-mode.md)).

The same human reaching the project through *two* gates on one platform
(local Telegram **and** brnrd-relayed Telegram) is an unusual case. Rather
than forcibly canonicalize the keys, treat it as a **redundancy channel**:
recognise the duplicate correspondent, deliver once, and don't double-act —
one perceived continuity, regardless of how many pipes reach it. (Decided
2026-06-13: a correspondent-identity layer over silent key-canonicalization.)

Shipped 2026-06-14: event records now carry `correspondent_key` and, when
available, `origin_message_key`. The daemon prompt reads recent history across
sibling conversation directories for the same correspondent, and exact
same-source duplicates (local/cloud Telegram message or GitHub comment) finish
as deduplicated tasks instead of starting a second runner.

### 4.5 Heartbeats are daemon-only

The 30-second heartbeat reverts to what it is: a daemon mechanism to detect
a hung runner and keep the chat card's elapsed counter moving. It does not
write conversation memory. (Breadcrumb: heartbeats were persisted as
`update` records and competed with real turns in the tail until this
milestone moved them out; the user flagged "heart beating every 30 seconds
polluting the context" as the tell that it took a wrong turn.)

## 5. Forge awareness & cross-referencing

A co-maintainer must see the project the way a human peer does. The
snapshot (§4.2 tier 1) gains a **forge-state facet**:

- Open issues and PRs the resident is involved in, its own
  `.brr/worktrees/*` and their branches (brr already enumerates these — see
  `worktree.list_worktrees` in [`../src/brr/worktree.py`](../src/brr/worktree.py)),
  local commits not yet pushed, and what has landed.
- **Cross-references**: tie a chat thread to the PR/issue/branch it's
  about, using the cloud gate's GitHub metadata (repo / issue / comment /
  PR number / branch_target) that **PR #106** threads through. A Telegram
  "did that land?" resolves against the actual PR state.

Conflict resolution rides on existing ownership: the resident owns
`brr-home` sync + merge (from [`design-self-scheduled-thoughts.md`](design-self-scheduled-thoughts.md))
and branch publication via the publish kernel
([`design-publish-kernel.md`](design-publish-kernel.md)); forge-awareness
gives it the picture to do so without surprises.

Shipped 2026-06-14 (#113): the wake snapshot's `CommunicationSnapshot`
gains a `forge` facet, built **network-free** by a new
[`../src/brr/forge_state.py`](../src/brr/forge_state.py) and attached in
the daemon beside the snapshot. It carries two local views: **worktrees**
(every `.brr/worktrees/*` via `worktree.list_worktrees`, each with its
branch, an unpushed-commit count from `worktree.unpushed_commit_count` —
`git rev-list --count HEAD --not --remotes`, no upstream needed — a dirty
flag, the "this run" marker, and a `forges.view_branch_url` link) and
**threads** (the GitHub issues/PRs in play, parsed from the current and
sibling conversation keys into `repo`/`number`/clickable `forges.thread_url`
cross-references; the waking thread is enriched with the live event's
`github_kind` / `branch_target` / `github_pr_number` / `github_html_url`
from PR #106's metadata). Rendered in both the daemon prompt
(`_format_forge_state`) and the run-context file. **Live** PR/issue status
(open/closed/merged, behind-base, CI) is deliberately out of scope — it
needs a token-bearing API call on the hot wake path and is the input to
forge grooming (#117) below, not this observational facet.

**From awareness to action — forge grooming** (issue #117). Awareness is the
input; grooming is what a co-maintainer *does* with it, on its own
initiative (self-scheduled wakes are the natural trigger):

- **Detect PRs/MRs that need a rebase and do it** — behind the base,
  conflicting, or claiming a state the base has moved past — then resolve,
  validate, and force-with-lease, exactly as `AGENTS.md` → *Pushing,
  rebasing, and open PRs* already prescribes for a human-grade collaborator.
- **Clean up stale PRs** — close or refresh ones the work has overtaken;
  update titles/bodies that drifted from HEAD.
- **Produce a grooming digest** — a periodic summary / proposals to the user
  (what's open, what's stuck, what it suggests doing) on a chat thread.

This turns `AGENTS.md`'s open-PR judgement into real behaviour, fed by the
snapshot's forge facet. (PR #106 is a live example: it sits `CONFLICTING`
against main and wants exactly this rebase-and-validate treatment before it
can land.)

## 6. Delivery robustness & run↔reply decoupling

Today a run is effectively coupled to one terminal reply: terminal delivery
requires `status==done` plus non-empty stdout, so a failed or empty run
**delivers nothing** — a silent drop the user experienced. Combined with
the global-FIFO/per-gate-key mismatch (§4.1), a missed delivery can be
followed by a reply that reads the wrong queue.

The deeper version of this decoupling — retiring the per-event `task`
concept entirely so a run reads the whole inbox and decides what to tackle
/ fold / postpone — is its own design slice:
[`design-run-event-model.md`](design-run-event-model.md) (#128). It owns
the daemon's serial-re-spawn half of the "three wakes on #114" symptom
(the self-author-trigger half is #129); this section's success-signal
floor is the substrate it builds on.

Targets, extending the shipped partials path
([`design-multi-response.md`](design-multi-response.md)) and the open
push/reply-ownership thread in
[`review-daemon-coherence-2026-06.md`](review-daemon-coherence-2026-06.md):

- **The agent decides where, how, and how much to reply.** Reply
  composition is the resident's judgement — which thread(s), how long, and
  **formatted for the destination gate** (a Telegram message and a GitHub
  comment are not the same shape), surfacing the useful forge links and,
  when the input names a GitHub issue, referencing it in the PR so it
  auto-closes. (Absorbs the closed #104.)
- **A run's success signal is its output, not its stdout.** A run must
  produce **at least one output event (a delivery / gate event), or a new
  commit/push, or an explicit noop event** — or a combination. The daemon
  determines success/failure from the *presence* of one of these, replacing
  the brittle `status==done` + non-empty-stdout coupling. Silence is the
  failure signal.
- **What counts as failure, and what to surface.** An agent that *replied*
  has not failed, even if it didn't finish the task — that's a normal
  partial. The real failures are **operational / runner errors**: a token
  limit, a connection drop, a runner crash. The user owns the runner — a
  critical piece of infrastructure brr doesn't control — so these are
  surfaced to them **unambiguously**, distinct from "task incomplete but I
  spoke." (This is the back-channel's vantage rule;
  [`design-agent-ergonomics.md`](design-agent-ergonomics.md).)
- **Decouple thought from stdout.** A wake may produce zero, one, or many
  deliveries on possibly several threads; stdout is one path, not the
  definition of "the reply."
- **Inbox fairness / correct keying.** Don't let one pending gate block
  another's delivery, and never attribute a reply to the wrong thread.

## 7. Worktree / branch-collision fix

Concrete, high-pain, small. `WorktreeEnv.prepare`
([`../src/brr/envs/__init__.py`](../src/brr/envs/__init__.py)) creates the
collision-free `brr/<run-id>` branch, then **unconditionally**
`worktree.switch_to(target_branch)` when the event names one (e.g. a PR head
branch). `switch_to` ([`../src/brr/worktree.py`](../src/brr/worktree.py))
tries `git switch <branch>` then `git switch -c <branch>` — **both fail** if
that branch is already checked out in another worktree (a human's or a
Cursor dev checkout), raising `fatal: a branch named '…' already exists`.

`sync.refresh_before_run` already guards "skip branches checked out in
another worktree." `prepare` must apply the same guard: detect
checked-out-elsewhere and fall back to a unique branch at the target's tip
(or detached HEAD there), surfacing the choice — instead of failing the
run. See [`subject-runs-branching.md`](subject-runs-branching.md).

## 8. Status-card UX & agent-owned composition

Cards are daemon-rendered from `UpdatePacket`s via `run_progress`, under
brnrd's relay-not-store stance ([`design-managed-delivery.md`](design-managed-delivery.md)).
The co-maintainer should be able to **compose what its card says** — a
collaborator narrates its own progress.

**Composition seam (shipped 2026-06-14, slice #114).** The resident writes
its preferred narration into a `.card` control dotfile in the per-run
outbox (already mounted into every run env). The daemon drains it on
each heartbeat (and once more after the runner returns), emits a
`card_composed` packet only when the content changes, and the gates'
existing `CARD_PACKETS` rerender loop picks it up. The view gains
`agent_card_text` and the compact renderer surfaces it as a `note: …`
tail line; the daemon still owns the lifecycle scaffolding (header, sync
line, phase log, terminal state). brnrd remains a transient relay
holding only `message_id` — data-minimization intact.

**Re-align the card with the new arch.** Today the card binds to one
session and infers "delivered / done" from terminal stdout delivery — which
§6 dissolves. With agent-decided, possibly multi-thread delivery and a
success signal of "events / commit / noop," the card must take its success
and delivery state from *that* signal (not stdout-non-empty), reflect that a
single run may have answered on several threads, and show an **operational
failure** distinctly from a normal partial. The card isn't wrong, it's
coupled to assumptions §6 removes: agent-owned composition sits on top of a
daemon-owned lifecycle that tracks the new signal.

*Projection-layer re-alignment shipped 2026-06-14 (#126).* The
daemon-owned lifecycle now reads the §6 success signal instead of
stdout-non-empty, in three pieces:

- **Success from the signal.** `_result_satisfied_delivery` returns
  `(satisfied, signal)` where `signal` is one of `current_reply |
  other_reply | outbound | commit | internal` — a run is successful when
  it answered *any* thread, sent an out-of-bound `gate:` message, made a
  new commit on the worktree branch (detected via
  `worktree.has_commits_beyond(seed_ref)` *before* finalize tears the
  worktree down), or is an internal event needing no thread reply. Stdout
  is still the common `current_reply` path, no longer the only one. The
  signal rides the `done` packet onto `RunProgressView.success_signal`.
- **Operational failure renders distinctly.** The `failed` packet carries
  a `failure_kind` (`timed_out` / `runner_error` / `no_output`); the
  compact card renames the terminal `failed` entry to `timed out` /
  `runner failed` / `no reply` so an operational failure (the user owns
  the runner per §6) reads differently from a hypothetical agent partial.
- **Multi-thread delivery reflected.** The `done` packet carries
  `replies_current` / `replies_other` / `outbound_messages` / `committed`;
  `_delivery_summary` surfaces "delivered to N threads" / "sent N
  out-of-bound message(s)" / "committed; no reply" on the terminal line,
  so a wake that answered several threads isn't collapsed to the
  current-thread reply.

*Open piece — the per-thread rolling card (gate-side).* The folded-in
legibility fix (gate keeps one card `message_id` keyed on
`(thread, correspondent)` and edits it in place across runs, so three
failed runs no longer stack three dead cards) is brnrd/gate-side state,
not the daemon projection layer this slice re-aligned. It's the remaining
work on #126: preserve the relay-not-store invariant (brnrd still holds
only the `message_id`, now per-thread not per-run) while rolling prior
outcomes into a short status header rather than a fresh comment per run.

## 9. Daemon responsiveness — shipped 2026-06-14 (#115)

Lower priority than continuity, but the co-maintainer should feel present.
Both halves of this slice shipped 2026-06-14 (see
[`subject-daemon.md`](subject-daemon.md) → *Loop cadence & gate
responsiveness*):

- **Connection reuse.** Each gate module (telegram, slack, cloud, github)
  holds one `requests.Session` (`_SESSION`) used through its existing HTTP
  chokepoint, so keep-alive reuses the connection across polls instead of
  dialing fresh. One session per single loop thread → no locking. The
  brnrd backend keeps its own async `httpx` client.
- **Event-driven wakeup.** A process-local `threading.Event`
  (`protocol.inbox_wake()`) the daemon loop blocks on; `create_event`
  sets it for in-process `pending` writes (gate enqueue, schedule fire),
  so a fresh event is picked up at once. The 3s tick stays as the
  backstop for cross-process `brr run` writes and time-based schedules.

Single-flight is unchanged — this was idle latency, not concurrency, per
the accepted dependency stance
([`decision-runtime-dependencies.md`](decision-runtime-dependencies.md)).
The same dispatch loop is where
[`design-run-event-model.md`](design-run-event-model.md) (#128) changes
*what a run sees* (the whole inbox, not `pending[0]`) — the event-driven
wake here and the inbox-reading run there are the same loop from two angles.

## 10. Faithful "what this wake received"

The user suspected `introspection.md` wasn't injected. **It is** — verified
this session: `introspect.enabled` parses `True`; both `build_run_prompt`
and `build_daemon_prompt` route through `_join_prompt_parts`, which appends
the block; and worktree-root config resolution still finds the main `.brr`.
What hides it:

- `brr agent inject` assembles a *different* block set than a real wake
  (omits diffense + introspection) — the "show me my context" tool is not
  faithful to a wake.
- Successful runs' traces (the only place the full `prompt.md` is persisted,
  via `runner._write_trace`) are **cleaned up** by
  `_cleanup_traces_on_success`; only failures leave a prompt to inspect. So
  there's no retained record of what a successful wake actually received.

Target: a faithful per-wake context view — make `brr agent inject`
mode-aware (accept/honor the toggles) and/or retain the last assembled
prompt — so "what did this wake see?" has an honest answer. Folds into
[`design-context-introspection.md`](design-context-introspection.md) and
the inject tool in [`plan-playbook-generalization.md`](plan-playbook-generalization.md).

## 11. Execution order & decisions

The leverage order and the dependency order mostly agree. Recommended
sequence (each maps to a milestone issue):

1. **Worktree branch-collision guard** (§7, #112) — tiny, independent,
   removes a live run failure; ship first so dogfooding on topic branches
   is reliable.
2. **Conversation persistence refactor + heartbeat demotion** (§4.3 / §4.5,
   #108, with #93) — foundational; everything downstream reads cleaner
   history.
3. **Delivery robustness + the run success signal** (§6, #111) —
   co-foundational; the events/commit/noop signal is a prerequisite for the
   card re-alignment and for honest failure surfacing.
4. **Per-correspondent identity + redundancy channels** (§4.4, #109) —
   shipped 2026-06-14 for daemon-side identity tags, sibling-channel prompt
   history, and exact source-message deduplication.
5. **Communication snapshot + on-demand grouped history + thread of record**
   (§4.2, #110) — shipped 2026-06-14 for the structured wake snapshot,
   grouped run-directory JSONL history files, and dominion thread-of-record
   prompt/context hint.
6. **Card re-alignment + agent-owned composition** (§8, #114/#126) — needs #111.
   *Composition seam shipped 2026-06-14* (`.card` control dotfile +
   `card_composed` packet + `agent_card_text` on the projection).
   *Projection-layer re-alignment shipped 2026-06-14 (#126)* — success
   from the events/commit/noop signal, distinct operational-failure
   rendering, and multi-thread delivery on the terminal line. The
   per-thread rolling card (gate-side `message_id` keyed on
   `(thread, correspondent)`) remains the open piece of #126.
7. **Forge-awareness in the snapshot** (§5, #113) — *shipped 2026-06-14*
   (network-free `forge` facet on the snapshot: worktrees + unpushed work +
   issue/PR cross-references, via `forge_state.py`). **Forge grooming**
   (§5, #117) — the action layer on top, still open; needs the live PR
   status this facet deliberately leaves out.
8. **Daemon responsiveness** (§9, #115) — *shipped 2026-06-14*
   (per-gate `requests.Session` connection reuse + `inbox_wake`
   event-driven loop wakeup). **Faithful context view** (§10, #116) —
   independent; slot in opportunistically.
9. **Run / event model** (§6/§9, #128) — retire the per-event `task`; a
   run reads the whole inbox and decides. Design page proposed 2026-06-14
   ([`design-run-event-model.md`](design-run-event-model.md)); wants the
   user's nod on its open decisions (per-run claim + `defer_until`
   debounce, run-id keying, run-granularity billing coupled to #130, and
   phasing the rename) before code. Subsumes the narrower batch-events
   idea; substrate for the resumable-tasks work.

### Decisions (close-loop, 2026-06-13)

- **Identity (§4.4):** a correspondent-identity layer over silent
  key-canonicalization; carry per-user identity (username / usertag /
  userid) for multi-user projects; treat a same-platform local+cloud
  duplicate as a redundancy channel (deliver once).
- **Thread of record (§4.2):** resident-curated in the dominion, not a
  human-facing forge artifact. In theme, `kb/` may become optional (#105);
  when off, the durable layer collapses into the dominion.
- **Snapshot eviction (§4.2):** recency is the primary importance metric for
  a correspondent's events, with **unanswered** as a strong boost.
- **Delivery floor (§6):** an agent reply is not a failure; operational /
  runner errors are, and are surfaced to the user unambiguously (they own
  the runner — critical infra brr doesn't control).
