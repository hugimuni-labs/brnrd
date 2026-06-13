# Co-maintainer — one perceived continuity, many runner actors

Status: accepted (2026-06-13) — north-star synthesis from a close-loop
design session. Not yet accepted. Sequences the next milestone and is the
umbrella for the tracking issues it names. Supersedes the approach taken in
the closed PR #107 (which tried to fit all gate history into the wake
context; see §4).

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
2. **brr driver (the substrate).** Wake, deliver, branch, sync, the Task
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
3. **A resident-maintained thread of record (optional, durable).** The
   project-level narrative the resident curates in its dominion — like a
   peer's running notes or a forge thread — anchored to git/forge. The
   durable "what we're doing together" that survives across wakes and
   channels and is the resident's own, not brr's.

This honours the robustness=retrieval-cost hierarchy from
[`design-environment-shaping.md`](design-environment-shaping.md): cheap,
high-signal at wake; full fidelity one read away; durable synthesis where
the resident chose to write it.

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

### 4.4 One perceived continuity across gates

Unify identity so the same human/forge-thread is one continuity regardless
of which gate carried a given message: either canonicalize keys (map
`cloud:<platform>:…` to its native `<platform>:…` equivalent) or layer a
**correspondent identity** above conversation keys. This is daemon-side and
respects brnrd's data-minimization stance — brnrd holds the metadata graph,
not the content (see [`plan-conversation-id-propagation.md`](plan-conversation-id-propagation.md)
and [`subject-managed-mode.md`](subject-managed-mode.md)). Open question in
§11 on how aggressively to merge.

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

## 6. Delivery robustness & run↔reply decoupling

Today a run is effectively coupled to one terminal reply: terminal delivery
requires `status==done` plus non-empty stdout, so a failed or empty run
**delivers nothing** — a silent drop the user experienced. Combined with
the global-FIFO/per-gate-key mismatch (§4.1), a missed delivery can be
followed by a reply that reads the wrong queue.

Targets, extending the shipped partials path
([`design-multi-response.md`](design-multi-response.md)) and the open
push/reply-ownership thread in
[`review-daemon-coherence-2026-06.md`](review-daemon-coherence-2026-06.md):

- **No silent drop.** Every addressed event ends with *something* on its
  thread — an answer, an interim, or an honest failure note — even on an
  empty/failed run.
- **Decouple thought from stdout.** A wake may produce zero, one, or many
  deliveries on possibly several threads; stdout is one path, not the
  definition of "the reply."
- **Inbox fairness / correct keying.** Don't let one pending gate block
  another's delivery, and never attribute a reply to the wrong thread.

## 7. Worktree / branch-collision fix

Concrete, high-pain, small. `WorktreeEnv.prepare`
([`../src/brr/envs/__init__.py`](../src/brr/envs/__init__.py)) creates the
collision-free `brr/<task-id>` branch, then **unconditionally**
`worktree.switch_to(target_branch)` when the event names one (e.g. a PR head
branch). `switch_to` ([`../src/brr/worktree.py`](../src/brr/worktree.py))
tries `git switch <branch>` then `git switch -c <branch>` — **both fail** if
that branch is already checked out in another worktree (a human's or a
Cursor dev checkout), raising `fatal: a branch named '…' already exists`.

`sync.refresh_before_task` already guards "skip branches checked out in
another worktree." `prepare` must apply the same guard: detect
checked-out-elsewhere and fall back to a unique branch at the target's tip
(or detached HEAD there), surfacing the choice — instead of failing the
task. See [`subject-tasks-branching.md`](subject-tasks-branching.md).

## 8. Status-card UX & agent-owned composition

Cards are daemon-rendered from `UpdatePacket`s via `run_progress`, under
brnrd's relay-not-store stance ([`design-managed-delivery.md`](design-managed-delivery.md)).
The co-maintainer should be able to **compose what its card says** — a
collaborator narrates its own progress. The seam: agent-owned card content
via new packet types or a control file in the task outbox (already mounted
into every run env), with the daemon still the sender and brnrd still a
transient relay — data-minimization intact. Additive to the existing card
lifecycle.

## 9. Daemon responsiveness

Lower priority than continuity, but the co-maintainer should feel present.
Today (see [`subject-daemon.md`](subject-daemon.md)):

- Gates make **fresh `requests` calls** per poll (no connection reuse).
- The loop is **pure sleep-polling** (`_SCAN_INTERVAL = 3s`); a new local
  event waits up to a scan tick with no event-driven wakeup.

Clean improvements within the accepted dependency stance
([`decision-runtime-dependencies.md`](decision-runtime-dependencies.md);
`httpx` is already an optional dep): connection reuse (`requests.Session`
or an `httpx.Client`), and a `threading.Event` to wake the loop promptly on
a fresh local event. Keep single-flight as the resident-identity reflex —
this is about idle latency, not concurrency.

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

## 11. Sequencing & open questions

Ordered by leverage; each maps to a tracking issue under this doc:

1. **Continuity persistence refactor** (§4.3) + **heartbeat demotion**
   (§4.5) — foundational; everything else reads cleaner history.
2. **One perceived continuity across gates** (§4.4).
3. **Communication snapshot + on-demand grouped history + thread of
   record** (§4.2).
4. **Delivery robustness & run↔reply decoupling** (§6).
5. **Worktree branch-collision guard** (§7) — small, ship early.
6. **Forge-awareness in the snapshot** (§5) — builds on PR #106.
7. **Agent-owned card composition** (§8).
8. **Daemon responsiveness** (§9).
9. **Faithful context view** (§10).

Open questions for the close-loop session:

- **Identity merge aggressiveness (§4.4):** canonicalize cloud↔native keys
  silently, or keep them distinct with a correspondent-identity layer above
  (safer for "is this really the same person?")?
- **Thread of record placement (§4.2 tier 3):** purely resident-curated in
  the dominion (private working memory), or also surfaced to the human as a
  forge artifact (a pinned issue / a project log)?
- **Snapshot budget:** what token budget does the snapshot get, and what is
  the eviction order when channels are busy (recency, unanswered-first,
  active-thread-first)?
- **Delivery floor (§6):** what is the minimal "no silent drop" message on
  a failed run — a fixed daemon note, or a resident-authored one?
