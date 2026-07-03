# Portals — the shape of a daemon run, and the seams you steer it through

How an average daemon run unfolds under the brr daemon, and the
control-file protocol — the **portals** — you steer it through. This is
the *manual* — read it when a run's shape is unfamiliar or you need to
look up a control file. It is **inspected, not injected**: a wake carries
the live per-run *values* (paths, ids, budget) in its Run Context Bundle
and a one-line pointer here; the choreography and the cheatsheet live in
this one place so a wake doesn't pay for them in tokens every time.

A **portal** is a seam where you turn to the world — somewhere the daemon
fills *in* (input arrives) or drains *out* (a message, a card, a PR
goes). Today each portal is a file you write into the outbox; the table
below is that grammar. (Where the grammar is headed — portals as marked
regions *in the generated stream itself*, so turning-to-the-world is how
the stream advances rather than a filename you must remember — lives in
`kb/design-portal-grammar.md`. This manual describes what ships now.)

This document ships with `brr`. Override it per-repo by dropping a file
at `.brr/docs/portals.md`.

## The grammar — control files as portals

Everything you steer happens by writing files into the **outbox
directory** named in your bundle (`.brr/outbox/<event-id>/`). One file is
one action. The daemon watches the directory and acts on its next
heartbeat. The bundle carries the concrete paths; this section is what
each one does, and which **portal form** it is:

- **inbound** ◂ — input flows in; you read (`portal-state.json` /
  `inbox.json`).
- **outbound** ▸ — you emit to a surface: a chat message, the card, a PR.
- **parked** ⏸ — you emit *and park the continuation*, resuming when
  something refluxes back (the PLAN→approve handoff).

Your wake never reads this table cold to act: the **delivery contract**
in the Run Context Bundle carries an *injected summary* of these three
forms, so the model rides hot while this manual stays the pull-only
reference for the full grammar. The two are a matched pair — the contract
names the forms, this manual defines them; change one and reconcile the
other so they don't drift.

| File | Portal | What it does |
| --- | --- | --- |
| `<name>.md` | outbound ▸ append-log | A **chat message**, delivered in filename order while you keep working. The body is the message. Stage as `*.tmp` and rename for an atomic write. |
| `<name>.md` with `event: <id>` frontmatter | outbound ▸ another thread | Same, but delivered to a **different pending event's** thread and marks that event handled, so it won't wake again. One complete reply per folded-in event. |
| `<name>.md` with `gate: <name>` frontmatter | outbound ▸ a destination | A **send** to a destination with no waiting event — ping a chat, post out-of-band, deliver from a scheduled wake. `gate: forge` is the explicit PR handoff (`head`, `base`, `title` frontmatter; body is the PR body) and opens or refreshes the PR for that head branch when the GitHub gate can deliver. Diffense may generate the title/body when a checked review pack exists, but PR delivery is not diffense-owned. An unconfigured gate is dropped. |
| `<name>.md` with `respawn: true` frontmatter | parked ⏸ runner handoff | Queue a fresh event for the same conversation and mark the current run satisfied by handoff. Use `shell:` / `core:` for an explicit target, or `quality: escalate` / `quality: strong` to let brr choose the stronger local Core exposed in `portal-state.json` (`resources.runner.quality_escalation`). Optional `reason:`, `at:`, `defer_until:`, and body/carry-forward text ride into the queued event. Paid relay is not selected here. |
| `<name>.md` with `runner_policy: propose` frontmatter | parked ⏸ policy approval | Park a proposed runner-policy edit in the account dominion instead of mutating policy directly. The body is the proposed policy markdown. Optional `scope: account` applies account-wide; the default is repo-scoped, with optional `repo:` / `repo_label:` override. The daemon sends an approval prompt; a later `approve runner-policy <id>` reply applies it, while `reject runner-policy <id>` closes it unchanged. |
| `.keepalive` | slot control | **Hold the single-flight slot** past your budget. First line is an ISO-8601 time ("busy until T") or `+<duration>` like `+30m`. Rewrite to extend. A control file, never delivered. (Not world-facing — it steers the slot, not a surface.) |
| `.card` | outbound ▸ desired-state | **Narrate the live progress card** — reconciled in place, not appended. Write only the note body; the daemon adds the `note:` label when it renders the live phase. Rewrite as context shifts; empty/delete to withdraw. The daemon owns the rest of the card; this is your seam to say what's actually happening. |
| `inbox.json` | inbound ◂ | **Daemon-owned**, refreshed each heartbeat: the live list of other pending events. Read it at plan/todo boundaries and once more before terminal closeout to fold in waiting work or explicitly leave it queued; never edit or remove it. |
| `portal-state.json` | inbound ◂ | **Daemon-owned**, refreshed each heartbeat: the broader live daemon-state capsule for this run. It includes pending events, delivered/drained reply counts, pending outbox files, current card text, budget/keepalive posture, and a stable `change_token` for attention-relevant changes. The runner also receives `BRR_PORTAL_STATE` pointing at it. Inspect with `brr portal state`; never edit or remove it. |

The daemon also injects runner environment variables for the live
surfaces it owns: `BRR_RUN_ID`, `BRR_EVENT_ID`, `BRR_OUTBOX_DIR`,
`BRR_INBOX_PATH`, `BRR_PORTAL_STATE`, `BRR_RESPONSE_PATH`, and
`BRR_CONTEXT_PATH` when those paths exist in the run environment. The
file remains the universal portal contract; the env vars are discovery
handles so a runner does not have to copy paths out of prose.

Fresh state reaches the runner two ways. A **Tier 2 runner with a
boundary back channel** gets it pushed automatically: at each runner
boundary brr flushes the outbox and `.card` immediately (no heartbeat
wait) and, when the runner supports live injection, weaves a compact
`portal-state` delta back into context, so the INBOUND-CHECK is automatic
rather than "remember to read `inbox.json`." That mechanism is the runner's
native lifecycle hooks calling `brnrd hook <phase>`: Claude registers a per-run
settings file (`PostToolBatch` / `Stop` / `SessionStart`), Codex takes the
same hook config as runner argv, and a `Stop` block folds a still-pending
follow-up into the same thought. Any runner can also pull state
directly — read `portal-state.json` / `inbox.json`, or run `brr portal
state` for the text view. (The earlier `brr portal wrap -- <command>`
shell wrapper was retired when the boundary back channel landed — it
only fired around shell calls the resident remembered to prefix, was
opt-in per command, and was one-directional; the back channel strictly
dominates it.)

The two reconcile semantics in the *Portal* column — append-log
(ordered, additive) and desired-state (one surface reconciled in place,
terraform-shaped) — are orthogonal to the transport underneath; the gate
is a dumb pipe under both. The PLAN→approve handoff is the canonical
*parked* portal: emit, park the continuation, resume when approval
refluxes (today via a follow-up event). Its message shape is below.

Code-changing runs have the lean PR handoff today through `gate: forge`.
What remains future portal work is a richer branch-keyed desired-state
surface — draft/review posture, issue links, labels, refresh policy, and
delivery acknowledgements — not the basic ability to ask the forge for a
PR. Keep this as a portal/gate handoff rather than a broad public `brr`
subcommand; diffense is optional review enrichment, not a requirement for
publishing a branch.

## The PLAN message — the parked portal's shape

When a request is large, multi-step, costly, or where you'd rather get a
nod before committing the compute, **don't execute on reflex and don't
just reply with vague intent** — emit a PLAN: a structured outbound
message (an ordinary outbox `<name>.md`, append-log) the human can
approve or edit with a short reply. Emitting it *parks* this run; the
approval reply is a fresh event whose wake carries the plan back in
(today via the woven conversation turns + gate-thread history + your
dominion — so the approval wake resumes from the plan, it does not
rebuild it cold).

A PLAN carries five things, no more:

1. **The decomposition** — the request broken into the concrete steps or
   chunks you'd actually run, in order. If it's one step, it isn't a PLAN
   — just do it.
2. **The chosen approach / medium per chunk** — where it matters: which
   runner medium, whether a chunk is its own wake (`schedule.md`) or a
   child event, what you'd branch vs. fold.
3. **Historical cost framing, never a projected promise** — ground the
   weight in *comparable past runs* ("a review of this size has
   historically run ~N wakes / ~$X"), drawn from what actually happened.
   **Never** invent a forward dollar figure or guarantee a cost; the
   honest frame is the past, not a quote.
4. **What parks and what resumes it** — say plainly that you're parking
   until they reply, and what their reply sets in motion.
5. **An explicit approve / edit affordance** — one line: "reply to
   approve, or edit any step and I'll re-plan." Make the seam obvious so
   the human knows a short word is all it takes.

Keep it scannable — a PLAN the human won't read is worse than executing.
After emitting it, stop: a parked plan is a complete, healthy turn. The
approval (or edit) arrives as its own event and starts the execution
wake.

Two more run surfaces live outside the outbox:

- **stdout** — the compatibility/current-thread fallback. When the
  situation calls for one plain current-thread reply, print the exact
  intended content, nothing else; progress and debug go to stderr. brr
  captures stdout to the response path in your bundle. It is one satisfying
  signal, not the definition of delivery. The daemon only needs an
  operational receipt that the run did not disappear; when something is
  intended for a human or forge surface, use an explicit communication
  portal.
- **`schedule.md`** in your dominion — each entry becomes a **future
  wake**. `at: <ISO-8601>` fires once; `every: <duration>` repeats. A
  scheduled wake is a fresh thought, but an entry's firings thread
  together (shared `conversation_key:`, default `schedule:<id>`). This is
  how you defer, set reminders, decompose work across wakes, and keep
  your own clock.

## The choreography — an average daemon run

1. **Receive.** A wake lands with a Run Context Bundle: the lead event, the
   delivery contract (paths, budget), recent conversation, the original
   event body. Read it once and orient from there.

2. **Orient.** Read `kb/index.md` and the injected recent-log tail; pull
   the subject/design pages the work touches. The dominion digest,
   matched pitfalls, and kb-health findings already rode in — let them
   steer you.

3. **Decide: plan or execute.** Small, clear, in-reach → execute. A
   contradiction with the current shape isn't a stop sign: reconcile it
   against the live state and act on the healthiest resolution in this
   same thought, narrating what you reconciled so the user can redirect.
   Reserve a parked **PLAN** message (the parked-portal shape above) for a
   build whose *spend or scope* genuinely wants a nod first, and a
   chat-only direction-set for a genuine fork (a product/values call, or
   intent you can't read from the code) — those are the cases that stop
   and wait for the follow-up event, not every reconsideration. (See
   AGENTS.md → Stewardship and run.md → "When the task asks you to
   reconsider".)

4. **Stay in the conversation.** For anything beyond a quick reply,
   compose `.card` so the human sees a live, self-authored status instead
   of bare daemon scaffolding. Name the phase, the runner medium / quota
   posture when the bundle gives one, and whether you are chunking for
   cost or resilience. Do not prefix the content with `note:` — the gate
   renderer supplies that label. Send an outbox trajectory note before a
   long stretch or at a fork. A Tier 2 boundary-back-channel runner gets
   fresh `portal-state` surfaced automatically at its supported seams;
   otherwise re-read `portal-state.json` (or run `brr portal state`) at
   natural seams. `inbox.json` remains the focused
   pending-events view when you only need that list. A related follow-up
   that appears while you are still thinking should fold into this wake
   when that is the healthiest path. Bound long commands; write
   `.keepalive` if the work will outlast your budget.

5. **Deliver.** Leave a satisfying operational signal for this situation.
   If the signal is meant to communicate, send it through stdout or an
   explicit portal; if the work is an artifact, commit it. Don't try to
   encode every possible completion shape as a chat reply. Immediately
   before a terminal closeout, re-read the live `portal-state.json` when
   the run has one (`inbox.json` is enough when you only need the pending
   event list); fold a quick related follow-up, or say why it should
   remain queued. This cannot catch messages that arrive after the runner
   has already returned, but it prevents avoidable orphaned follow-ups. If
   you wrote files, commit them on the current branch — the diff is the
   receipt the work happened. Rename the run branch to something
   descriptive (keep the `brr/` prefix) before committing if the work has a
   clear theme.

6. **Decompose / defer the rest.** Can't finish it all in one wake, or
   the request is naturally several steps? Write `schedule.md` entries
   for the follow-ups instead of cramming or dropping them. Fold a quick,
   related pending event in via `event: <id>`; leave anything that wants
   its own branch for its own wake.

7. **Persist what's worth keeping.** Durable decision, discovery, or
   shipped change → a `kb/log.md` entry and, when it's general, a `kb/`
   page. Raw friction, half-formed views, personal habits → your
   dominion. Friction worth tripping over next time → a `pitfalls.md`
   entry with a `trigger:` line.

## The robustness ladder

Live state is **injected** (the medium, the budget, this run's paths and
ids) — you can't miss it. The manual is **inspected** (`brnrd docs
portals`) — one glance away, not memorized, not re-paid every wake. A
failure the environment makes impossible (a lint, a test, a baked-in
tool) is stronger still than either. When you move a recurring failure
all the way down that ladder, retire the pitfall that stood in for it.

## See also

- `brnrd docs active-task` — the shorter orientation refresher.
- `brnrd docs execution-map` — how an event flows through brr end to end.
- `brnrd docs brr-internals` — the `.brr/` layout and internals.
- AGENTS.md — the repo contract every wake rests on.
