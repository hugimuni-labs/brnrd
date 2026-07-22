# Pitfalls — trigger-indexed failure memory
#
# The *remember* step of the environment-shaping loop. When you hit
# friction worth recording but not yet worth a forcing function, write it
# here: brr surfaces a pitfall in your wake prompt when one of its
# triggers appears in the task at hand — the lesson placed in your path,
# not prose you must remember to re-read.
#
# Format: a `## ` heading (the lesson's name), a `trigger:` line
# (comma-separated keywords or loci that tend to appear when the failure
# is about to recur), then the lesson. Slash a pitfall once a lint, test,
# or baked tool guards the failure — the forcing function is the better
# memory, and a stale pitfall is just orientation tax.
#
# Example (delete once you have real ones):
#
# ## Blind 5xx retry masks caller bugs
# trigger: retry, 5xx, http client
# The HTTP client surfaces 5xx to the caller without retrying. If you add
# a retry, gate it behind idempotency — a blind retry hid a caller bug
# here before.

## diffense `--relay` needs `requests` + network — use the embedded-pack fallback
trigger: brr review, --relay, diffense, pr-body, review pack, ModuleNotFoundError requests
Publishing a diffense PR from a sandboxed runner: `brr review <pack> --pr-body --relay`
imports the GitHub gate (for repo detection + gist publication), which imports
`requests` — absent in the sandbox — so `--relay` crashes with
`ModuleNotFoundError: No module named 'requests'`. Don't fight it: drop `--relay`.
`brr review <pack> --pr-body` (no relay) projects the same body and embeds the full
pack JSON in a `<!-- diffense:pack:v1 ... -->` HTML comment, which is the renderer's
fallback when no gist URL exists. Then push the branch and publish via a `gate: forge`
outbox file (`head`/`base`/`title` frontmatter, body = the projected PR body). The
`--pr-title` projection works without network. Validated on #128 (task-260614-1637).

## Full pytest run shows 5 collection ERRORs for gate tests (requests missing)
trigger: pytest, ModuleNotFoundError requests, collection error, telegram_gate, github_gate
`requests` is absent in the sandbox, and `src/brr/gates/{telegram,github,slack}.py`
import it at module load. A bare `python -m pytest` aborts collection with 5 errors
(test_gate_setup, test_github_gate, test_slack_render_update, test_telegram_gate,
test_telegram_render_update) — *before running anything*. This is env friction, not
your change. Run the rest with `--ignore=tests/test_gate_setup.py
--ignore=tests/test_github_gate.py --ignore=tests/test_slack_render_update.py
--ignore=tests/test_telegram_gate.py --ignore=tests/test_telegram_render_update.py`
(or `pip install requests` if network is up). Confirm pre-existing by stashing your
diff and re-collecting one gate test. Seen on task-260614-1644 (#131).

## Editing the host checkout instead of the task worktree
trigger: Edit absolute path, /home/gurio/src/misc/brr/src, /home/gurio/src/misc/brr/kb, wrong branch, worktree, execution root, host checkout
The bundle's Execution root is a *worktree* at
`/home/gurio/src/misc/brr/.brr/worktrees/<task-id>/`, but the obvious repo path
`/home/gurio/src/misc/brr/src/...` is the **host checkout** (on `main`), a different
tree. Read/Edit calls with that host absolute path silently land in the host
checkout, not your task branch — your worktree stays clean and the work is on the
wrong tree (and mixed with whatever the host had uncommitted). Either `cd` into the
worktree and use relative paths, or build absolute paths under
`.brr/worktrees/<task-id>/`. Recovery if you already edited the host tree:
`git -C <host> diff -- <your files> > /tmp/p.patch && git -C <host> checkout -- <your files>`,
then `git apply /tmp/p.patch` inside the worktree (exclude any pre-existing host
changes you didn't make). Seen on task-260614-1903 (#115). **Recurred
task-260616-2151** editing `kb/` (not just `src/`) — and the trigger model
*missed it*: the failure is structural to **every** worktree task that edits
files, but this pitfall only injects when the task body happens to mention the
trigger words, which a "discuss the cockpit" task never will. That's the gap:
the right fix is a forcing function (the Edit tool / runner refusing absolute
paths outside the worktree, or the wake context not advertising the host path),
not a memory I trip over by luck. Surfaced to the maintainer 2026-06-16.

## Emitting a diffense review pack — schema gotchas that fail `--check`
trigger: review pack, pack.json, brr review --check, diffense emit, uncertainty card, lore.headline
Two snags cost a re-validate cycle when hand-emitting a pack (task-260617-2104):
(1) An **uncertainty card's gloss must be `lore.descriptive` or a *top-level*
`headline`** — `lore.headline` is NOT read by `_gloss`, so it fails
`card.gloss missing`. Use `lore.descriptive` for every card, uncertainty
included. (2) **`kind` must be from `KNOWN_KINDS`**, not the id namespace — a
card with `"id": "item:foo"` still needs a real kind like `code-fn-edit`,
`kb-page-new`, `kb-page-edit`, `test-add`, or `custom` (prose/prompt/doc files →
`custom`). A bare `"kind": "item"` only warns (renders generic) but is sloppy.
The clean-pack recipe: summary card first (exactly one), every card carries
`identity.label` + `lore.descriptive` + `provenance`; any card with
`identity.file` needs a resolvable `locator.local` of `path:line`; uncertainty
cards add `subkind` (assumption/concern/dilemma/out-of-scope-flag/follow-up/meta)
+ `severity` (low/med/high/blocking/blocking-for-merge); list every card id in
`reading_order`. `brr review --check <path>` must report 0 errors AND 0 warnings.

## Recovery wake after a mid-flight runner failure starts cold — the failed run's reasoning evaporates
trigger: prior run failed, connection closed mid-response, pick up your work, recovery wake, resume failed run, runner error
When a run dies operationally (API flake, quota), the recovery wake's Run
Context Bundle flags *that* a prior run failed but carries **nothing about
what it was doing**. The failed run leaves no commit, no branch, no
dominion scratch — its in-flight reasoning is gone. The ONLY durable trace
of its mid-flight state is what it already *pushed to the user*: the
`.card` `[update]` notes and `[artifact]` outbox messages, preserved in
the gate-thread history JSONL (`.brr/runs/<this-run>/history/gate_thread-*.jsonl`).
The Runtime-recovery pointer in the bundle is for *this* run's context, not
the failed one. So to pick up: read the gate history JSONL, find the last
`[update]`/`[artifact]` turns from the failed run, and the last user turn
(it usually carries the decision/fork the run was acting on). The Bundle's
"Recent turns" woven view gives short *event bodies* only — not the run's
own emitted artifacts — so you must grep the full JSONL for the plan.
Lesson for next time you're the one at risk: on any non-trivial run, push
your plan/intent to a durable surface *early* (a `.card` + an outbox
artifact, or a dominion scratch note) so a recovery wake reads it off a
live surface instead of reconstructing from history. The daemon-side fix
is a "prior run was working on" recovery handle in the bundle — see
thread-of-record / the note to the maintainer 2026-06-19.

## woven "Recent turns" block carries thread-openers, not the recent tail
trigger: merged and updated, what's that about, thank you closeout, woven thread stale, recent turns oldest first

Observed evt 6q1m (2026-06-23): the Run Context Bundle's "Recent turns (woven,
oldest first)" listed 8 turns all dated 2026-06-10..06-12 — the *opening* turns
of a 415-dialogue thread — while today's event was 06-23. The exchanges that
actually established current state (#171/#175 hook wiring, the salvage net) were
absent from the woven block. I could only tell what "Merged and updated" referred
to by tailing the gate_thread JSONL in the run's history/ dir.

Lesson: on a long-running thread, don't trust the woven "Recent turns" to carry
the *recent* tail — it appears to select salient thread-openers, not recency. When
a closeout/ack references something you can't place, tail
`.brr/runs/<run>/history/gate_thread-*.jsonl` before answering. Candidate to raise
with the maintainer: either rename the block (it isn't "recent") or window it to
the tail, because right now it half-promises continuity it doesn't deliver.

## Closely-spaced message fragments race separate wakes — answer the burst, not just the first line
trigger: let's do medium, you stopped before answering, stopped before answering, burst fragments, orphaned follow-up, double-answer, coalesce window, debounce, #128, why didn't you reply, fragment burst

Observed evt-ratx (2026-06-27): the maintainer sent "let's do medium" then two
trailing fragments seconds apart. A wake planned/ended on an earlier slice before
the trailing fragment was folded into its inbox, so "let's do medium" sat
**unanswered** until a later wake tripped over it — and the maintainer rightly
flagged the stop as a problem. Each burst pays a re-orientation wake plus a
no-answer / double-answer risk.

Lesson — two altitudes:
1. **Cheap, already live:** the delivery contract re-reads `inbox.json` /
   `portal-state.json` immediately before closeout. *Honour it* — re-read the
   live inbox before terminal delivery and fold a related trailing fragment into
   this wake (one `event: <id>` reply per folded event). This is the only thing
   that stops a second orphaning; it caught evt-ratx.
2. **Durable fix (carry forward):** daemon-side burst-coalescing at the event
   seam — a per-run claim + short debounce/coalesce window so fragments from one
   correspondent gather into one wake's inbox *before* it plans. Squarely #128.
   When that ships and a burst can no longer split across wakes, slash this
   pitfall — the seam fix is the better memory. Synthesis: dogfooding-plan-loop.md.

## Firing-test runner hooks from a clean env; stale stream conclusions are retired
trigger: hooks back channel, brr hook, post-tool, PostToolUse, PostToolBatch, Stop, boundary injection, inbound injection, --safe-mode, CLAUDE_CODE_SAFE_MODE, settings.local.json, hooks not firing, hooks don't fire, runner hooks, runner profile, Tier 2, --print, stream-json, runner_stream, stop-control
Do not trust config-present / endpoint-works as proof that a runner hook channel
is live. Trust end-to-end firing evidence: `.hook-state.json` / `.flush` written
by the hook, or a visible injected `[brr portal update]` / `additionalContext`
that the model actually reads.

Current truth after evt o538 (2026-06-27): Claude Code `PostToolUse`,
`PostToolBatch`, and `Stop` hooks **do** fire under headless `claude --print`
when the environment is clean; `Stop` block continues the turn, and
`additionalContext` injection lands. Codex native `PostToolUse` fires via inline
hook config too. The earlier dominion notes claiming "Claude hooks do not fire
under `--print`", "`--print` is single-turn with no stop-control", and "drop
`--print` for persistent stream mode" were false conclusions from contaminated
tests and are retired.

The contaminant was usually the parent agent session: `CLAUDE_CODE_SAFE_MODE=1`
(plus `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `AI_AGENT`, etc.) silently disables
settings-file hooks while logging reassuring hook text. For live runner tests,
spawn with those vars stripped, or use `runner.clean_runner_environ()`, which now
does this in production. If future tests prove that helper makes this impossible
end-to-end, slash this pitfall; the code guard is the better memory.

## Claude statusLine never fires headless; Claude /usage is PTY-scrapable; Codex quota is on-disk
trigger: statusLine, statusline, cost awareness, cost-data, quota, subscription quota, spend, context_window, facets, levels collector, claude /usage, /usage, codex /status, /status, rate_limits, token_count, codex sessions, rollout, codex_status, claude_usage, runner-media, design-resident-boundary §8
Fire-verified 2026-06-28 (this run), overturning the evt-e1gl finding the prior
wake built `statusline.py` on:

- **Claude `statusLine` is a TUI footer — it does NOT fire under `claude --print`**
  (the mode brr's daemon runs). Probe: a statusLine command in
  `.claude/settings.local.json` never fired under `--print`, while settings-file
  *hooks* fired the same run (clean env). So `statusline.py` is dead in
  production — it only collects in an interactive TUI a human wires manually.
  The head-less Claude spend/context source is `claude --print --output-format json`,
  whose result carries `total_cost_usd` (spend) + `modelUsage.contextWindow`
  (context) but **NOT** subscription reset windows. That result-JSON path is
  wired by `claude_status.py`.
- **Claude `/usage` can still be reached programmatically, but only as a TUI
  scrape.** Start interactive Claude in `--safe-mode`, type `/usage` through a
  PTY, capture the terminal screen, and parse Claude's own `Current session` +
  `Current week` buckets. That is wired by `claude_usage.py` with a cache/TTL.
  Do **not** put this inside a hook: it is ~15s and spawns a nested TUI. The hook
  should read the daemon's cached portal-state projection.
- **Codex DOES expose subscription quota head-less — the opposite of the earlier
  "edge-only" note.** Every `token_count` event in
  `$CODEX_HOME/sessions/.../rollout-*.jsonl` carries `rate_limits.primary` (5h)
  + `secondary` (weekly) with `used_percent`/`window_minutes`/`resets_at`, plus
  `model_context_window`. That's exactly what `/status` prints — on disk, no
  `/status` call, no credits. `codex_status.py` reads the newest rollout's last
  `token_count` (wired into facets). Note: `codex exec --json` *stdout* does NOT
  carry rate_limits (only `turn.completed` usage) — the quota is in the rollout
  file only.

Lesson that generalizes: a CLI feature being TUI-rendered (`/status`,
`statusLine`, `/usage`) does not by itself settle the data path. Check on-disk
session logs first; if none exist, a PTY scrape may still be viable, but cache it
and label the source quality honestly. Always fire the collector in the actual
run mode (`--print`/`exec`/interactive PTY) before trusting it — "the UI has the
field" ≠ "brr receives the field cheaply."

## A run's final reply kills its watchers — don't close out while waiting on a verdict
trigger: watcher armed, still waiting on the backend suite, re-invokes this run, background suite, spawn finished status=done uncommitted, stranded worktree, linger to review
Seen 2026-07-22 (run-260722-0753-slsr, #327 jinja-connect-port): the worker armed a
background watcher for the backend suite, then emitted its terminal reply — brr
finalized the run on that reply and the watcher died with the process. Result:
status=done, publish nothing, all work stranded uncommitted; its message_path and
/tmp report were never written. The "background task re-invokes you" contract holds
only *within* a live thought — it does not survive closeout. If a verdict is still
pending: extend `.keepalive`, wait in-thought (Monitor/background task, no terminal
reply), or commit first and let a reviewer re-run the suite. The parent/reviewer
wake should always check the child's worktree for uncommitted work before trusting
status=done.

## A merge with no receipts is not a merge — announce + review evidence at merge time
trigger: spawn_completed review, merge to main, whole-diff review, unreviewed diff, empty reply, no announce, review-before-merge
Seen 2026-07-22 (run-260722-0804-01mn → run-260722-0815-w9zk, #61 conversation-id):
the prior thought merged `brr/conversation-id` into local main at 08:14:05Z — 51s
after its #327 announce, *before* the worker's terminal message, then finalized
with an empty reply, no announce, no review or suite receipt anywhere. The next
wake (me) could not distinguish that from an unreviewed merge, so the review had
to be redone from scratch — and it found a real gap (migration added the
conversation_id column but not the model's index; fresh installs indexed, migrated
installs not). Lessons: (1) the announce at merge time IS the review receipt —
a silent merge forces full re-review; (2) never start a second merge inside a
closeout window you can't finish the choreography in — leave it for the
spawn_completed wake that's already coming; (3) a reviewer landing on an
unexplained merge should re-review rather than trust the commit's existence.
