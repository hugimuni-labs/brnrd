# Portals — the shape of a daemon run, and the seams you steer it through

How an average daemon run unfolds under the brnrd daemon, and the
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

This document ships with `brnrd`. Override it per-repo by dropping a file
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
| `<name>.md` with `respawn: true` frontmatter | parked ⏸ runner handoff | Queue a fresh event for the same conversation and mark the current run satisfied by handoff. Use `shell:` / `core:` for an explicit target, or `quality: escalate` / `quality: strong` to let brnrd choose the stronger local Core exposed in `portal-state.json` (`resources.runner.quality_escalation`). Optional `reason:`, `at:`, `defer_until:`, and body/carry-forward text ride into the queued event. Paid relay is not selected here. |
| `<name>.md` with `spawn: true` frontmatter | concurrent ↗ worker dispatch | Queue a bounded worker-stack child in the configured concurrent pool (sized by `spawn.max_concurrent`); read live headroom from `portal-state.json` → `resources.coexisting_runs.spawn_pool` (`max_concurrent` / `active` / `available`) rather than assuming a cap. Name `shell:` / `core:` and optionally `task_classification:`; the completion returns to the parent as a pending event. Use this for independent pending work when capacity and quota are healthy. The parent retains ownership of any original external event and answers it with `event: <id>` after reviewing the child; the spawn request alone does not clear that event. |
| `<name>.md` with `stop: <run-or-event-id>` frontmatter | concurrent ✕ worker stop | Stop a concurrent child **this run** dispatched, addressed by its spawn event id or child run id (wyrd §3: a run controls only its own dispatchees — the daemon enforces the ownership check and does the kill; nothing depends on the child reading anything). A child still queued is cancelled before it ever starts; a running child's runner process is killed, its partial branch work is salvaged, and it finalizes as `stopped` — the completion note (`status=stopped`) returns to this run as a pending event. Optional `reason:` (or the body) is recorded on the child. A refused stop (unknown id, not your dispatchee, already finished) lands in `portal-state.json` → `notices`. |
| `<name>.md` with `to: <run-or-event-id>` frontmatter | concurrent ▸ worker steer | Message a concurrent child **this run** dispatched (same ownership check as `stop:`). The body lands as a `dispatch_message` event that **only the addressed worker's** `inbox.json` / portal-state surfaces — it never dispatches a run of its own, other runs never see it, and whatever the child has not folded in is retired when the child ends. A steer, not a new contract: the child folds it into its existing work and should not `event:`-address it. Workers are thread-isolated (they get their contract and these edge messages, not the user thread's recent turns or pending events), so this verb is the *only* way words reach a running worker. Refusals land in `notices`. |
| `<name>.md` with `runner_policy: propose` frontmatter | parked ⏸ policy approval | Park a proposed runner-policy edit in the account dominion instead of mutating policy directly. The body is the proposed policy markdown. Optional `scope: account` applies account-wide; the default is repo-scoped, with optional `repo:` / `repo_label:` override. The daemon sends an approval prompt; a later `approve runner-policy <id>` reply applies it, while `reject runner-policy <id>` closes it unchanged. |
| `.keepalive` | slot control | **Hold the single-flight slot** past your budget. First line is an ISO-8601 time ("busy until T") or `+<duration>` like `+30m`. Rewrite to extend. A control file, never delivered. (Not world-facing — it steers the slot, not a surface.) |
| `.card` | outbound ▸ desired-state | **Maintain the run body** — resident-owned Markdown, reconciled in place. Keep `## Now` current; only that section projects onto the compact live card. Preserve the arc, findings, and decisions in later sections. At closeout the daemon copies the full write-head to `runs/<repo>/<run>/body.md` beside its separately attested `state.md`; empty/delete leaves a frame-only run. |
| `.task-classification` | slot control | One short slug naming this run's **shape** for the cost ledger (`dashboard-slice`, `kb-brainstorm`, `bugfix`, …). Write it any time before closeout; the `Stop` hook nudges if it is still missing at the boundary, but that is a last catch, not permission to wait. Left unwritten, that ledger row's `task_classification` is **null forever** — and it is the one join key the cost-estimate workstream rolls up on. `spawn:` / `respawn:` frontmatter takes a `task_classification:` key to tag a child at hand-off. A control file, never delivered. |
| `.pr` | slot control | The PR number for a PR **this run created itself** — bare, `#`-prefixed, or a full URL. Not needed for a GitHub-sourced task that already arrived with one. `remote_scm` in the live portal is deliberately network-free (run metadata, never a live forge query), so without this file a self-created PR stays invisible to it and the facet keeps reading `absent`. A control file, never delivered. |
| `.relics.jsonl` | slot control | This run's **produce manifest** — one JSON object per line, append-only. See §The produce manifest below. A control file, never delivered. |
| `inbox.json` | inbound ◂ | **Daemon-owned**, refreshed each heartbeat: the live list of other pending events. Read it at plan/todo boundaries and once more before terminal closeout; every event gets an inline, spawn, or explicit-defer disposition. Never edit or remove it. |
| `portal-state.json` | inbound ◂ | **Daemon-owned**, refreshed each heartbeat: the broader live daemon-state capsule for this run. It includes pending events, delivered/drained reply counts, pending outbox files, current card text, budget/keepalive posture, worker headroom (`resources.coexisting_runs.spawn_pool`), attested live produce (`produce`: counts plus the latest commit, branch, and PR), a stable `change_token` for attention-relevant changes, and **`notices`** — see below. The runner also receives `BRR_PORTAL_STATE` pointing at it. Inspect with `brnrd portal state`; never edit or remove it. |

### `notices` — the directives brnrd refused

An outbox file is deleted from the directory **whether it was accepted or
refused**. So a directive the daemon could not carry out — a `spawn:` it had
no pool capacity to queue, a reply addressed to an event that is no longer
pending — is *invisible from inside the run*: the file is gone, exactly as it
would be on success.

`portal-state.json` → `notices` is where those land, and it is the only place
they exist. **Read it after any `spawn:` / `respawn:` / `event:`-addressed
write.** A dropped directive that nobody reads is a request that silently
never happened.

## The produce manifest — `.relics.jsonl`

One JSON object per line, append-only, via `brr.relics.append(outbox_dir,
kind, **fields)` or by appending the line directly. It is what the run made,
in a form something other than prose can read.

**Auto-derived — write nothing for these.** Commits, the pushed branch, a
self-reported PR, **kb pages committed by the knowledge capture**, and your
**terminal reply** are collected at closeout. Every outbound reply is born
under `runs/<repo>/<run-id>/messages/NNNNNN-<kind>.md` and reported as a reply
relic. Delivery changes its frontmatter from `pending` to `delivered` or
`undeliverable`, stamping the platform receipt when one exists. The message is
never unlinked, so the run's full edge traffic remains durable.

The built-in vocabulary is `summary`, `commit`, `branch`, `pr`, `issue`,
`comment`, `kb`, `file`, `message`, and `reply`. Unknown kinds remain readable
through their first descriptive field, but use a built-in kind when it fits.

**Worth a line of your own:**

```jsonl
{"kind": "issue",   "number": 317, "action": "closed"}
{"kind": "comment", "url": "…"}
{"kind": "message", "channel": "telegram", "note": "design fork answered"}
{"kind": "summary", "text": "…"}
```

At most one `summary`, and it heads the receipt. The manifest feeds the
dashboard's collapsed run receipt; the chat card does not render it yet (a
named gap, not a bug in your run).

Treat produce you can name as **yours to compose with**: a summary may give
the receipt its spine, and a relevant issue, kb page, or message may be a
useful glint in the reply. Reinforce the work where that helps the reader.
Do not turn every receipt into ornament, and do not restate facts the daemon
already derives for you.

The daemon also injects runner environment variables for the live
surfaces it owns: `BRR_RUN_ID`, `BRR_EVENT_ID`, `BRR_OUTBOX_DIR`,
`BRR_INBOX_PATH`, `BRR_PORTAL_STATE`, `BRR_RESPONSE_PATH`, and
`BRR_CONTEXT_PATH` when those paths exist in the run environment. The
file remains the universal portal contract; the env vars are discovery
handles so a runner does not have to copy paths out of prose.

Fresh state reaches the runner two ways. A **Tier 2 runner with a
boundary back channel** gets it pushed automatically: at each runner
boundary brnrd flushes the outbox and `.card` immediately (no heartbeat
wait) and, when the runner supports live injection, weaves a compact
`portal-state` delta back into context, so the INBOUND-CHECK is automatic
rather than "remember to read `inbox.json`." That mechanism is the runner's
native lifecycle hooks calling `brnrd hook <phase>`: Claude registers a per-run
settings file (`PostToolBatch` / `Stop` / `SessionStart`), Codex takes the
same hook config as runner argv, and a `Stop` block folds a follow-up that is
already pending before runner exit into the same thought. A follow-up that
arrives after the runner has returned cannot be folded by a hook; the
daemon-owned attending floor below can keep the slot/card warm briefly, but
that follow-up becomes the next run. Any runner can also pull state
directly — read `portal-state.json` / `inbox.json`, or run `brnrd portal
state` for the text view. (The earlier `brnrd portal wrap -- <command>`
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
PR. Keep this as a portal/gate handoff rather than a broad public `brnrd`
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
  intended content, nothing else; progress and debug go to stderr. brnrd
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

## The next move — how an addressed reply ends

An addressed run's final reply — the stdout closeout, or the outbox
message that closes the thread — ends with **the next move**: one line
naming where the loop stands, so the human never has to infer it.

- `done — <receipt>` — the ask is complete; name the receipt (commit,
  PR, page, reply that holds).
- `continuing — <what's next>` — the work carries on under its own steam
  (a scheduled wake, the next chunk); name the next concrete move.
- `blocked — <what's needed>` — you can't proceed; name the one
  unblocking thing and who holds it.
- A **genuine fork** — 2–4 numbered options, your recommendation, and a
  one-line reason. A short reply ("2", "the second one") should be all
  it takes to set the work moving.

The most common value is *nothing to decide* — done or continuing.
**Manufacturing options is the failure mode**: options appear only at
genuine forks (product/values calls, costly or irreversible spends,
intent the code can't resolve) — never to look thorough, and never to
hand back a reversible call that was yours to take. A reviewer should
reject any habit that makes options the default shape of a reply.

This is structural, not a closing courtesy: **check the literal last line
before sending.** Two edges, both caught in live runs:

1. **The substance already shipped through outbox interims.** Then the
   terminal stdout is *either* genuinely empty (`deliver_stream` skips an
   empty closeout) *or* a real one-line receipt ending in the next-move
   shape. A bare `done` is neither — it still ships, it lands last, and to
   the reader it *is* the reply.
2. **A due self-wake firing inline at a run's tail** (a director tick, say)
   never replaces the primary task's closeout. The tick's own notify bar
   governs only the tick's content. The two are **additive** in one reply —
   primary receipt + next-move line, tick line appended — never a choice.

## The post-delivery linger — catching the follow-up warm

A follow-up often lands moments after your reply; spawning a cold run to
read one more sentence wastes the warm context you're still holding.
There are two layers:

- **Runner-owned linger** keeps the same thought alive. Use it when the
  conversation is clearly live — the user is mid-thread, or your reply
  invites a short answer — and you can afford the warm wait.
- **Daemon-owned attending** is the automatic safety net after a configured
  gate current-thread delivery. It emits an `attending` packet, renders the
  nonterminal card phase as `delivered · attending`, holds the single-flight
  slot briefly, and yields immediately when any pending event appears. It is
  intentionally weaker than runner linger: the runner has exited, so the
  follow-up becomes the next run rather than being answered inside the same
  thought. That next run is an **unblock, not a restart** — it reads the same
  conversation history, dominion, and kb the first run did; nothing resets,
  only the process does.

Runner-owned linger is a named contract, not an improvised while-loop:

1. **Deliver first.** Send the reply as a mid-thought outbox message —
   it is the satisfying signal, so the eventual final stdout can stay
   empty (an empty closeout after an outbox delivery is correct, not a
   failure).
2. **Hold the slot honestly.** Write `.keepalive` for the linger horizon
   and set `.card` to say you're lingering (e.g. "lingering for
   follow-ups; next check in ~2m").
3. **Back off exponentially.** Start around 30s, double per quiet poll,
   cap at ~240s — inside the ~5-minute provider cache window, so each
   poll rides warm context instead of paying a cold re-read.
4. **Poll, don't spin.** Each poll reads `portal-state.json`
   (`change_token` says whether anything moved). A same-thread follow-up
   ⇒ fold it in, reply, reset the backoff. **Any other pending event ends
   passive waiting**: dispatch bounded independent work through `spawn:`
   when capacity and quota are healthy; otherwise yield or explicitly defer
   it so the queue never starves.

   The mechanical shape — one bounded shell command per quiet interval,
   never an unbounded sleep loop:

   ```sh
   # One linger poll: waits up to $INTERVAL seconds, exits 0 the moment
   # attention-relevant state moves, 124 when the interval passes quiet.
   last=$(jq -r .change_token "$BRR_PORTAL_STATE")
   timeout "$INTERVAL" sh -c '
     while sleep 10; do
       [ "$(jq -r .change_token "$BRR_PORTAL_STATE")" != "'"$last"'" ] && exit 0
     done'
   ```

   Run it with `INTERVAL=30`, then `60`, `120`, `240`, `240`, … — the
   backoff lives in the *sequence of calls*, not inside one long-running
   command. After each call: exit 0 ⇒ read `portal-state.json` and
   `inbox.json`, decide fold-in vs spawn vs explicit defer/yield; exit 124 ⇒ double the interval
   and go again (or exit if the horizon passed). Keeping each poll a
   separate tool call matters beyond tidiness: on hook-capable Shells the
   portal update fires *between* calls, so pending events are pushed into
   your context at every poll boundary — the ownership rule gets checked even
   when you forget to check it.
5. **Bound the horizon.** Default: 10–15 minutes past the last delivery;
   extend only while the exchange is actually flowing. Multi-hour vigils
   are scheduled wakes' territory, or quota-aware pacing policy (#214) —
   a linger is hot idle and spends attention and quota even when each
   poll is cheap.
6. **Exit quietly.** When the horizon passes with nothing new: clear or
   settle the card, leave stdout empty, end. The reply already went out.

Daemon-owned attending is configured with
`delivery.post_delivery_attend_seconds` (default 90s; `false` disables;
`delivery.post_delivery_linger_seconds` is an alias for the older wording)
and `delivery.post_delivery_attend_poll_seconds` (default 1s). It only
applies after the successful `current_reply` path for a configured gate
thread. Terminal `done` still renders the final `delivered` state after the
attending floor ends or yields.

## The continuity line — the loop closing across wakes

The boot kernel opens with a `continuity:` line that names what changed
since the resident last stood in this checkout. This is the **world's
readout**, not the resident's prose — read from the prior wake's persisted
boot-score, the local git history, and the forge cache, never from the
dominion's authored memory or the shared work surface.

The point is a distinction the earlier frames missed. A resident perceives
many things about its own past — the authored work surface and Recent
Activity — and every line is prose the resident/user wrote. That is a message
in a bottle: exactly as good as last
wake's discipline, and free to drift from the world in silence. Authored
memory never brings bad news about itself.

The continuity line closes the loop — last wake's action → this wake's
perception — using **observed facts**: PRs that actually merged, commits the
dominion actually took, whether the memory that was supposed to be here is
readable. The `mount` field is three-state and the `✗` is load-bearing:

- `✓` — the prior wake's boot-score.json was found and parses (the mount holds).
- `✗ first wake` — no prior score exists; this is an ordinary and useful fact.
- `✗ unreachable` — the memory is *supposed* to be here and is not; act on this before trusting a single injected block.

A ✓ mount renders the rest of the line: `continuity: ✓ run-id age · shipped
#386 #387 · dominion +2`. These numbers are what actually happened:

- `run-id age` — e.g., `run-260713-2251-ropg 2h ago`; the prior wake's id and how long ago it ran.
- `shipped` — PRs that reached MERGED since the prior wake (only if any).
- `dominion +N` — commits the dominion actually committed since the prior wake (only if nonzero).

A second, indented `drift:` line renders only when the resident's account of
itself and the world's have come apart:

- `dominion has N uncommitted change(s) — the capture net did not close on a prior wake`
- `dominion push was rejected (…) — the remote diverged; reconcile before trusting injected memory`

Drift earns its own line precisely because it is the case a resident must not
skim past. It is the boot telling the wake that its own prose and its own
repository disagree about what it did. A drift line that cries wolf every
wake trains the resident to stop reading — and the value of this line is that
it is rare and true.

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
   otherwise re-read `portal-state.json` (or run `brnrd portal state`) at
   natural seams. `inbox.json` remains the focused
   pending-events view when you only need that list. Give every pending event
   a disposition: fold small/related work here; spawn bounded independent
   work when capacity and quota are healthy; defer only with an explicit
   resource, priority, dependency, or authority reason. Bound long commands; write
   `.keepalive` if the work will outlast your budget.

5. **Deliver.** Leave a satisfying operational signal for this situation.
   If the signal is meant to communicate, send it through stdout or an
   explicit portal; if the work is an artifact, commit it. Don't try to
   encode every possible completion shape as a chat reply. Immediately
   before a terminal closeout, re-read the live `portal-state.json` when
   the run has one (`inbox.json` is enough when you only need the pending
   event list); fold small/related work, spawn independent bounded work, or
   record the explicit reason it must remain queued. This cannot catch messages that arrive after the runner
   has already returned, but it prevents avoidable orphaned follow-ups. If
   you wrote files, commit them on the current branch — the diff is the
   receipt the work happened. Rename the run branch to something
   descriptive (keep the `brr/` prefix) before committing if the work has a
   clear theme.

6. **Decompose / defer the rest.** Can't finish it all in one wake, or
   the request is naturally several steps? Dispatch bounded independent
   pieces through `spawn:` and review their completions here. Use
   `schedule.md` for time-bound follow-ups. Defer a pending event only when
   resources, priority, dependency, or authority make dispatch unwise, and
   state that reason; unrelated is not itself a reason.

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
- `brnrd docs execution-map` — how an event flows through brnrd end to end.
- `brnrd docs internals` — the `.brr/` layout and internals.
- AGENTS.md — the repo contract every wake rests on.
