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
| `<name>.md` with `gate: <name>` frontmatter | outbound ▸ a destination | A **send** to a destination with no waiting event — ping a chat, post out-of-band, deliver from a scheduled wake. `gate: forge` is the explicit PR handoff (`head`, `base`, `title` frontmatter; body is the PR body) and opens or refreshes the PR for that head branch when the GitHub gate can deliver. Diffense may generate the title/body when a checked review pack exists, but PR delivery is not diffense-owned. An unconfigured gate is dropped. The body is close-keyword checked before the PR is queued — see **Close keywords close on two channels** below. |
| `<name>.md` with `respawn: true` frontmatter | parked ⏸ runner handoff | Queue a fresh event for the same conversation and mark the current run satisfied by handoff. Use `shell:` / `core:` for an explicit target, or `quality: escalate` / `quality: strong` to let brnrd choose the stronger local Core exposed in `portal-state.json` (`resources.runner.quality_escalation`). Optional `reason:`, `at:`, `defer_until:`, and body/carry-forward text ride into the queued event. Paid relay is not selected here. |
| `<name>.md` with `spawn: true` frontmatter | concurrent ↗ worker dispatch | Queue a bounded worker-stack child in the configured concurrent pool (sized by `spawn.max_concurrent`); read live headroom from `portal-state.json` → `resources.coexisting_runs.spawn_pool` (`max_concurrent` / `active` / `available`) rather than assuming a cap. Name `shell:` / `core:`; optional `environment:` may opt the child *down* the isolation ladder (`docker` / `solitary` — both keep the child's own worktree, so the parent-collision guard holds); `worktree` is the default and the floor, and anything less isolated (`host`) is refused with a notice. The completion returns to the parent as a pending event. Use this for independent pending work when capacity and quota are healthy. The parent retains ownership of any original external event and answers it with `event: <id>` after reviewing the child; the spawn request alone does not clear that event. |
| `<name>.md` with `stop: <run-or-event-id>` frontmatter | concurrent ✕ worker stop | Stop a concurrent child **this run** dispatched, addressed by its spawn event id or child run id (wyrd §3: a run controls only its own dispatchees — the daemon enforces the ownership check and does the kill; nothing depends on the child reading anything). A child still queued is cancelled before it ever starts; a running child's runner process is killed, its partial branch work is salvaged, and it finalizes as `stopped` — the completion note (`status=stopped`) returns to this run as a pending event. Optional `reason:` (or the body) is recorded on the child. A refused stop (unknown id, not your dispatchee, already finished) lands in `portal-state.json` → `notices`. |
| `<name>.md` with `to: <run-or-event-id>` frontmatter | concurrent ▸ worker steer | Message a concurrent child **this run** dispatched (same ownership check as `stop:`). The body lands as a `dispatch_message` event that **only the addressed worker's** `inbox.json` / portal-state surfaces — it never dispatches a run of its own, other runs never see it, and whatever the child has not folded in is retired when the child ends. A steer, not a new contract: the child folds it into its existing work and should not `event:`-address it. Workers are thread-isolated (they get their contract and these edge messages, not the user thread's recent turns or pending events), so this verb is the *only* way words reach a running worker. Refusals land in `notices`. |
| `<name>.md` with `runner_policy: propose` frontmatter | parked ⏸ policy approval | Park a proposed runner-policy edit in the account dominion instead of mutating policy directly. The body is the proposed policy markdown. Optional `scope: account` applies account-wide; the default is repo-scoped, with optional `repo:` / `repo_label:` override. The daemon sends an approval prompt; a later `approve runner-policy <id>` reply applies it, while `reject runner-policy <id>` closes it unchanged. |
| `<name>.md` with `await: <condition> \| ...` + `timeout: <duration>` frontmatter | inbound ◂ armed hold | **A select, not a sleep** (#959). Declare what you're waiting on once; the daemon evaluates it on its own heartbeat, not on your say-so. See §`await:` below. |
| `.keepalive` | slot control | **Hold the single-flight slot** past your budget. First line is an ISO-8601 time ("busy until T") or `+<duration>` like `+30m`. Rewrite to extend. A control file, never delivered. (Not world-facing — it steers the slot, not a surface.) |
| `.card` | outbound ▸ desired-state | **Maintain the run body** — resident-owned Markdown, reconciled in place. Keep `## Now` current; only that section projects onto the compact live card. Preserve the arc, findings, and decisions in later sections. At closeout the daemon copies the full write-head to `runs/<repo>/<run>/body.md` beside its separately attested `state.md`; empty/delete leaves a frame-only run. |
| `menu.json` | outbound ▸ desired-state | **Maintain the thread's one live menu.** Write one JSON object atomically with `menu_id`, `thread`, and `options` (`handle`, `label`, optional `detail`, optional `rec: true`); `expires_at` is optional. The daemon validates and archives the generation, supersedes the prior one, and renders the same stored menu at gates and at the next resident boundary. Malformed menus land in `portal-state.json` → `notices`. A worker child has no v1 menu transport; its parent composes. |
| `.mood` | slot control | **Your own resident-authored mood** — an almost-free meta-channel from resident to user (#566 layer 2). First line only: an emote name or a free glyph string. The hook boundary re-reads it fresh and folds it into the live delta every boundary — a bar segment mid-run, a plain line at seed/stop. It **displays** every boundary and **asks** only on an edge: when a tool in the batch just came back wrong, the chip renders as `mood fo.cus ← Bash ✗`, setting the face you claimed beside the thing that broke. Transition-stamped, so a run debugging a red test is asked once, not at every pass. A control file, never delivered. |
| `.pr` | slot control | The PR number for a PR **this run created itself** — bare, `#`-prefixed, or a full URL. Not needed for a GitHub-sourced task that already arrived with one. `remote_scm` in the live portal is deliberately network-free (run metadata, never a live forge query), so without this file a self-created PR stays invisible to it and the facet keeps reading `absent`. A control file, never delivered. |
| `.relics.jsonl` | slot control | This run's **produce manifest** — one JSON object per line, append-only. See §The produce manifest below. A control file, never delivered. |
| `.promises.jsonl` | slot control | This run's **blueprint** — the produce manifest in the opposite tense: what you said you would make. Written through `brnrd promise <what>`; see §The blueprint below. Drives the `owed N` boundary chip and the closeout's plan-vs-progress line. A control file, never delivered. |
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

### `brnrd do` — the verdict rides the act

`brnrd do` is porcelain over the grammar above, not a new channel: every
write it makes lands as one of the same outbox files this manual already
describes, staged and drained exactly the way a hand-written `note.md` /
`event: <id>` reply would be. What it adds is the read-back — instead of
staging a file and separately remembering to poll `notices`, one call
stages the file, waits for the daemon's own drain to consume it, and diffs
`notices` from just before the stage to report `✓` / `✗ <notice>` / `?
still queued` in the same call.

```
brnrd do [--outbox DIR] [--timeout SECONDS] \
  [--mood <feeling-or-handle> [--mood-note "…"]] \
  [--note <event-id>]... \
  [--reply <event-id> --body-file FILE | --body "…"]... \
  [--gate <name> --body-file FILE]... \
  [--card FILE] \
  [-- <command> [args…]]
```

- `--note` / `--reply` / `--gate` each stage the canonical `note:` /
  `event:` / `gate:` fenced frontmatter this manual already specifies, wait
  up to `--timeout` (default 30s) for the drain to consume the file, and
  report the verdict: `✓` (consumed, no fresh matching notice), `✗
  <kind>: <text>` (a fresh notice named this directive), or `? still
  queued` (still sitting in the outbox at the timeout — never a hang).
  `--mood` / `--card` write the `.mood` / `.card` control files directly
  (never drained, so the verdict is just the write) — `--mood` resolves
  through the same `emotes.lookup` / `emotes.near_misses` this manual's
  `.mood` row already points at, so a near-miss reports candidates instead
  of writing nothing silently.
- Bare `brnrd do` (no verbs) prints a compact one-screen read of pending
  events, outbound counts, notices, the quota line, and spawn-pool
  headroom — the canonical replacement for hand-parsing
  `portal-state.json`.
- `-- <command> [args…]` (only recognised for `do`, split out of argv
  before anything else parses it) runs the given command via `execvp`
  after the verbs above are staged — argv passthrough, never shell
  interpretation, so it does no globbing/piping/env-expansion beyond what
  your own shell already did. Verdict lines move to stderr so the
  command's stdout stays pipeable, and the command's own stdout/stderr/
  exit code become `brnrd do`'s — the boundary carries both the speech
  acts and the real work in one call. Omit `--` for a pure update call,
  exactly as above.

**One correlation gap, named rather than patched around.** A notice
records its verb and target in the message text (`"note dropped: event
evt-… …"`, `"reply dropped: …"`) but never the staged filename that
produced it, so `brnrd do` matches a fresh notice to "was this about the
directive I just staged" by substring, not by identity. It works because
every refusal/drop/redirect notice in `daemon.py`'s `_drain_outbox` names
both its verb and its target — but a concurrent notice from unrelated
activity that happened to share both substrings would still misattribute.
The precise fix is daemon-side: give `_record_outbox_notice`
(`daemon.py`, the function starting ~L6135) an optional `source_file:`
field threaded from each call site's own `fpath.name`, so a reader could
join on identity instead of text. Not done here — no daemon-side changes
in this change — named so the gap has one place to live.

### `await:` — a select, not a sleep (#959)

The measurement this closes: a resident waiting on a dispatched worker or a
background gate wrote `until <condition>; do sleep 25; done` as one shell
call. By the older liveness contract that "survives the closeout" —
`.keepalive` was armed, the thought never ended — but it emitted **zero tool
boundaries** for the whole span, and the daemon only ever reaches a resident
*at* a tool boundary. Three of the maintainer's messages queued behind a
wait that was doing exactly what it was told. **A wait a correspondent
cannot interrupt is not a wait. It is a gap.**

```
---
await: <condition> [| <condition> ...]
timeout: <duration>          # required — a wait with no ceiling is a hang
---
```

**Conditions** (`|`-separated; the fenced form is required — the value has
spaces, so the unfenced lenient shortcut other verbs get doesn't apply):

- `file:<path>` — the path exists.
- `pid:<n>` — the process has exited.
- `spawn:<run-or-event-id>` — a concurrent child **this run** dispatched has
  finished (matched against either the child's eventual run id or the
  `spawn: true` dispatch's own event id — you may only have one of the two
  on hand, depending on when you wrote the `await:`).
- `event` — **a new pending event arrived.** Always a member of the set,
  whether or not you name it — parsing appends it structurally. You cannot
  construct a blind wait by forgetting to mention "did a message arrive",
  because forgetting is no longer an available state.

**The condition set is evaluated by the daemon on its own heartbeat tick**
(every ~10s, independent of whether you call anything at all) — that is
what makes this a *listening* wait rather than a *sleeping* one. The outcome
lands in `portal-state.json` → `await`:

```jsonc
{"armed": true, "conditions": ["file:/tmp/gate.log", "event"],
 "timeout_seconds": 1200, "deadline": "…", "capped": false,
 "resolved": true, "outcome": "condition", "which": "file:/tmp/gate.log"}
```

`resolved` flips to `true` on exactly one of three outcomes — never
silence:

- `"condition"` — a named `file:`/`pid:`/`spawn:` term fired; `which` names it.
- `"event"` — some other pending event arrived that no named condition
  already explains.
- `"timeout"` — the deadline passed with nothing else firing.

**`.keepalive` is extended for you**, to the timeout deadline (never
shortened below an existing one you wrote yourself) — you do not also have
to remember a separate keepalive write on top of the `await:`. If the
requested `timeout:` would outlive this run's remaining hard budget
ceiling, the hold is armed for the capped duration instead of the full
request — silently truncating a promised wait is worse than warning about
one, so the cap is announced once as an `advisory` notice, not swallowed.

**Poll it with `brnrd portal await`** (or read `portal-state.json` →
`await` yourself, e.g. from §linger's own poll loop). One call blocks for
at most **15 seconds** — a small, code-level bound, not a documentation
convention — before returning `{"outcome": "pending", ...}`; call it again.
Because the daemon's own evaluation tick runs independently of this call,
a resolution that lands mid-sleep is still reported the moment you wake up
and re-read, with no extra round trip. This is the structural fix for the
measured failure: a wait built from a sequence of short, separately
boundary-visible tool calls gives the daemon (and anything it wants to
inject) a seam at least every 15 seconds, for as long as the wait runs —
never the multi-minute blind stretch a hand-rolled shell loop can produce.

**v1 is the verb and the hold path only.** `await:` never ends this run to
service a wait — it holds the slot, the way `.keepalive` always has. A
`worker: true` (concurrent spawn) run may not arm one: the hold is a
resident-level cost decision, and a worker has no business spending its
parent's slot on its own wait. Malformed input (no `timeout:`, an
unrecognised condition) is dropped to `notices`, same as any other verb.

## The blueprint — `.promises.jsonl`

The produce manifest says what a run **made**. This says what it **said it
would make**, so a boundary can name what is still owed and the closeout
cannot quietly forget it (#1008).

    brnrd promise pr --count 2 --ref "the rollout split"
    brnrd promise kb --ref subject-x.md
    brnrd promise pr --release --why "superseded by #1042"

`<what>` is a produce kind — `commit`, `branch`, `pr`, `merge`, `kb`,
`issue`, `comment`, `message`, `file`. A promise names something the
manifest can actually attest; anything else would sit owed forever.

**Count decides; `--ref` speaks.** Matching is on *how many* of a kind
landed, never on the ref: a promise is made before the thing exists and
cannot name it, so keying on the ref would report a broken promise every
time you shipped the right work under a different name. The ref is the
label the owed line uses — and once part of a kind has landed it renders
as `one of: …`, because counting cannot say *which*.

**`--release` is the counter, and it needs `--why`.** Without a way out an
abandoned intent sits owed at every boundary forever, and a nag with no
counter stops being read. The reason rides the row, so the record of the
abandonment lives where the abandonment happened.

**A promise cannot be kept by its own past.** Each row records how much of
its kind the run had already produced when the claim was made, and only the
surplus over that counts toward it. Without that, a run that had opened one PR
and then promised two more would read *all kept* the moment nothing further
happened — the guard failing in the **optimistic** direction, which is the one
direction a guard may not fail in. A hand-written row with no baseline falls
back to counting everything, which is the lenient old behaviour.

What it still cannot do: two *unrelated* later PRs satisfy two promised PRs.
Counting cannot see intent, and matching on `--ref` would cry wolf every time
the right work shipped under another name.

**What it renders.**

- `owed N` — a bar chip, absent at zero, the same differential discipline
  as `!N`. Deliberately not a ratio against `⚒`: that count includes things
  nobody promised, so there is no shared denominator.
- `- still owed: 2 PRs — the rollout split` — a detail line, latched on the
  blueprint's *own* delta. It speaks when a promise is made, met, or
  released, and again at the closeout — not once per boundary, which is how
  an obligation turns into noise.
- At the closeout, and only there, a kept blueprint says so.

**An empty blueprint renders nothing at all.** `promised 3 · shipped 1` is
evidence a promise broke — you wrote the row, so you can be proven wrong
about it. `promised 0 · shipped 3` is not evidence of a kept run: it is a
run that wrote no rows, which is byte-identical to a run that had nothing to
promise. A guard may only assert something the run can be proven wrong
about.

## The produce manifest — `.relics.jsonl`

One JSON object per line, append-only, via `brr.relics.append(outbox_dir,
kind, **fields)` or by appending the line directly. It is what the run made,
in a form something other than prose can read.

**Auto-derived — write nothing for these.** Commits, **merges you
performed**, the pushed branch, a self-reported PR, **kb pages committed by
the knowledge capture**, and your **terminal reply** are collected at
closeout. Merges are their own block, separate from PRs made: a merge
commit in the run's scope is promoted to a `merge` relic (`Merge pull
request #N` subjects link the PR; `Merge branch 'x'` names the branch), and
a GitHub-committed squash landing (`… (#N)`, committer
`noreply@github.com`) counts too. The one merge git archaeology cannot see
is a pure-remote `gh pr merge` whose result never reaches the local
checkout — report that one yourself:
`{"kind": "merge", "pr": N, "url": "…"}`. Every outbound reply is born
under `runs/<repo>/<run-id>/messages/NNNNNN-<kind>.md` and reported as a reply
relic. Delivery changes its frontmatter from `pending` to one of three terminal
states, stamping the receipt when one exists: `delivered` (a gate carried it to
a platform), `collected` (a worker's terminal report, read by the parent run
along the dispatch edge — no gate owns `spawn`, and none should), or
`undeliverable` (nothing claims the target; the reason says what). The message
is never unlinked, so the run's full edge traffic remains durable.

A reply addressed to a dispatch-tree event — `spawn`, `spawn_completed`,
`dispatch_message`, `schedule` — is recorded `undeliverable` immediately, with
a notice in `notices`, **and still retires the event as handled**: the reply
is the clearing mechanism even though its text reaches no reader. Anything a
person must read belongs in a reply to the originating user event (when one
exists) or routed via `gate:`; a worker completion is a signal to fold in,
not an address whose reply anyone sees.

The built-in vocabulary is `summary`, `commit`, `branch`, `pr`, `merge`,
`issue`, `comment`, `kb`, `file`, `message`, `reply`, and `item`. Unknown kinds remain readable
through their first descriptive field, but use a built-in kind when it fits.

**`item` is ancestry, not produce** (#972, the weld): it names the warp item
(`<layer>#<slug>`, a resolver address into `surface/layers/`) this run
ignited from. The daemon writes it for you when the dispatching message
names the address; if you only learn mid-run which item you are serving,
record it with `brnrd relic item the-loom#gate-chips-row-on-repos` — the
slug is the item heading's anchor. At capture, the run's forge produce
lands back on that item's `refs:` row, so the item and the run reference
each other instead of re-listing each other.

**Worth a line of your own:**

```jsonl
{"kind": "issue",   "number": 317, "action": "closed"}
{"kind": "comment", "url": "…"}
{"kind": "message", "channel": "telegram", "note": "design fork answered"}
{"kind": "summary", "text": "…"}
```

**Issues have a command, and it is the one relic worth the habit.**

```
brnrd relic issue 686 --closed [--repo owner/name]
brnrd relic issue 764 --opened
```

Same appended line, none of the JSON. It exists because issue produce is the
only kind the daemon cannot see: commits, merges, the branch, the PR, kb
pages and your reply are all derived, but `gh issue create` and `gh issue
close` happen inside your shell and leave the daemon nothing to observe. So
an issue relic exists **only** if you write one — and the misses skew, because
filing feels like output while closing feels like tidying up.

The action flag is required, not defaulted. An issue relic with no `action`
is neither created nor completed — that is how every hand-written record
predating this command still reads, and it stays true rather than being
retro-fitted into a bucket nobody chose.

**Links are derived, not reported.** The daemon knows the run's forge and
`owner/repo`, so an `issue` / `pr` / `commit` / `branch` relic gets its forge
URL filled in at collection time from its `number` / `sha` / `name` alone —
write the bare record. Add `"url"` only to point somewhere the daemon cannot
derive (another forge, a comment permalink), or `"repo": "owner/name"` for a
thread in a different project. When the forge is unknown the relic renders
unlinked; the daemon never fabricates a URL.

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

Telegram renders a live menu as one inline-keyboard message per thread and
edits that message on supersession. A tap creates a pending
`kind: menu_answer` event whose JSON body carries `menu_id`, `option`, optional
`text`, and the resolution status (`live`, `stale`, `expired`, or `unknown`).
Expiry is decided authoritatively when the answer is ingested; fresh renders
and boot boundaries filter expired menus. A stale, expired, or pruned
generation therefore remains answerable instead of becoming a dropped tap.

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

### Close keywords close on two channels

A forge closes an issue on a closing keyword (`Closes` / `Fixes` /
`Resolves` + `#NNN`) in a **commit message** *and* in a **pull-request
body**, with equal authority. The rule is the same on both: the keyword only
means *close* at the start of a line, and after the ref only more refs may
follow — `, #MMM`, then end of line or `: subject`. Anything else is prose,
GitHub discards it, and the issue closes anyway. That is how #749 was shut by
the very clause written to keep it open (`Closes #749 move 5 (the ticket
stays open for moves 1–4).`, PR #838's body).

Where the check runs:

| channel | enforced by | when |
| --- | --- | --- |
| commit message | the `commit-msg` hook brnrd installs (`$BRR_RUN_ID` set ⇒ run commits only) | at `git commit` |
| PR body via `gate: forge` / `gate: github` + a PR action | the outbox drain — a failing body is refused to `notices` and the PR is **not** created | when the file is drained |
| PR body via a hand `gh pr create` | nothing — no brnrd code is on that path | **opt-in:** `brnrd close-check <body-file>` (exit 1 ⇒ findings; `--channel commit-msg`, `--json`) |

One predicate, one source: `src/brr/closekeyword.py`. The hook script is
rendered from it, so the two channels cannot drift apart.

**Quoting a line the rule refuses** — the case that bites hardest in a PR
body, because a PR body is where you would naturally show the example. Two
forms are quotable and neither needs a bypass:

- **mask the digits** — `Closes #NNN …` is not `#<digits>`, so nothing
  matches and nothing closes. This is why every remedy above is written with
  `#NNN`.
- **split the keyword from the ref** — the predicate only fires when the two
  are adjacent, so `#749 — closed by a keyword with a tail on line 30` is
  free to name the real issue.

There is no code-fence exemption. GitHub does not document ignoring closing
keywords inside fenced blocks, and #839 is a ticket about what assuming a
channel's behaviour costs.

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
  schedule entry may pin `shell:`, `core:`, or the legacy `runner:` profile;
  an unavailable pin fires on the config default instead of dropping the
  entry. A scheduled wake is a fresh thought, but an entry's firings thread
  together (shared `conversation_key:`, default `schedule:<id>`). This is how
  you defer, set reminders, decompose work across wakes, and keep your own
  clock.

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

**Waiting on a specific named condition** — a gate log file, a pid, a
concurrent `spawn:` child — is `await:`'s job (above), not this recipe's:
`await:` gives the daemon's own heartbeat the condition to evaluate and
bounds each of *your* poll calls at 15s, where the recipe below bounds one
call at up to 240s. Reach for linger's own backoff only for the generic
"tell me if *anything at all* changes" case with no specific condition to
name.

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
  "Prior wake" means the previous thought on *this resident's line*: runs whose
  score records `source: spawn` are concurrent workers this line dispatched, not
  predecessors, and the picker skips them. A `respawn:` handoff inherits the
  originating gate's source, so it still counts — it is the same thought
  continuing in a different body.
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
