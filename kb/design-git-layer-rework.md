# Design: git layer rework

Status: active

This page covers brr's git layer in three phases: daemon-side
freshness, a real GitHub gate, and a prompt-level mitigation for
runner thoughtfulness on design-loaded tasks. Phase 1 and Phase 2
have shipped; Phase 3 is queued on the same plan and will amend
this page in place as it lands.

The page hangs off [`subject-daemon.md`](subject-daemon.md) and
[`subject-tasks-branching.md`](subject-tasks-branching.md). The
running plan lives outside the kb in
`.cursor/plans/daemon-sync-and-git-gate-cleanup_*.plan.md`.

## Why this exists

Until 2026-05-15 the built-in `git_gate.py` mixed three different needs
under one label, and the load-bearing one was silently broken:

1. **Daemon freshness** — every brr task seeds from the daemon's local
   `main`. Nothing ever fetched, so once a brr-produced branch was
   merged on the remote (typical PR-merge-on-GitHub flow) every
   subsequent task started from a stale main until the operator
   manually pulled. Universal failure mode, no signal to the operator.
2. **Forge events** — PRs, MRs, issues, comments. Provider-specific.
   The natural input shape brr was missing for ~everyone using a forge.
3. **Tasks-folder watcher** — drop a markdown file into `tasks/` on the
   default branch and the daemon picks it up. Genuinely niche. The
   protocol-level bash example in
   [`gates/README.md`](../src/brr/gates/README.md) covers the residual
   demand.

Plus a meta-issue this work surfaced: the unmerged
`brr/git-gate-defaults` branch flipped the niche watcher on by default
and wrote a defensive design doc — the path-of-least-resistance read of
the request, not engagement with whether the existing shape was the
right one. The stewardship section in
[`AGENTS.md`](../src/brr/AGENTS.md) calls for that engagement; the
prompt and self-review surface aren't strong enough to actually elicit
it.

The rework ships #1 and #2, deletes #3, and tightens the prompt +
self-review so the next loaded task gets genuine engagement instead of
mechanical compliance.

## Phase 1 — Daemon freshness

Status: shipped on 2026-05-15.

[`sync.py`](../src/brr/sync.py) exposes a single entry point:

```python
def refresh_before_task(
    repo_root: Path,
    *,
    target_branches: list[str],
    cfg: dict[str, Any] | None = None,
) -> SyncResult: ...
```

The daemon calls it in [`_run_worker`](../src/brr/daemon.py) just
before [`branching.resolve_branch_plan`](../src/brr/branching.py).
Target branches come from `_branches_to_refresh(repo_root, event)`,
which always includes the local default branch and adds any structured
branch named on the event (`branch_target`, `target_branch`,
`base_branch`, or the legacy `branch` key, validated through the same
helper the branch resolver already uses).

The contract is small:

- One `git fetch <default-remote>` per call when a remote exists.
- `--ff-only` advance of each named local branch against
  `<remote>/<branch>`. Already-up-to-date and dirty-tree cases skip
  silently with a recorded reason; diverged history records a
  non-fast-forward skip.
- Never raises. Any unexpected exception is captured as
  `SyncResult.error` so a flaky network never blocks a task.

Outcomes ride on the progress card via a new `synced` packet (see
[`updates.py`](../src/brr/updates.py) and
[`run_progress.py`](../src/brr/run_progress.py)). The card surfaces a
short `synced: ff main -> abc1234` line; the no-op path is quiet.

### Seed-ref invariant

After `refresh_before_task` returns, the daemon's local view of any
named target branch is at least as fresh as the remote was at the
start of task setup, *or* the result records why it isn't (dirty
tree, divergence, fetch failure, opt-out). Worker code can rely on
the seed ref reflecting that view rather than the operator's last
manual `git pull`.

### Config

Two opt-out knobs in `.brr/config`, both default-on:

- `sync.fetch_before_task=false` — never touch the network.
- `sync.fast_forward_default=false` — fetch but leave local refs
  alone (for users sharing the daemon's checkout with active dev
  work).

### Why no plan stage / extra surface here

The simpler shape (one helper called from one place in the daemon
loop) wins because:

- A separate sync thread on a timer would burn fetches even when no
  work is queued, and would race with the just-in-time fetch the worker
  already needs.
- Mutating the host checkout from a gate (the old `git_gate.use_pull`
  knob) hid daemon-wide behaviour inside an opt-in transport adapter.
- Operator-on-different-machine sync collapses, after this lands, to
  "you can `git fetch` on your laptop and you'll see what brr
  produced". A `brr ls --branches` or similar can be added later if
  there's a clear need; it's not what was broken.

### Tests

[`tests/test_sync.py`](../tests/test_sync.py) covers no-remote
no-op, happy-path fetch+ff, dirty-tree skip, divergence skip,
multi-target branches (default + structured), already-up-to-date
silence, branch-doesn't-exist-locally skip, fetch failure, exception
capture, and both config opt-outs.
[`tests/test_daemon.py`](../tests/test_daemon.py) pins the call
order (sync before resolve) and the soft-failure path (sync error
must not block the task).

## Phase 2 — GitHub gate

Status: shipped on 2026-05-15.

[`gates/github.py`](../src/brr/gates/github.py) is a built-in gate that
talks to `https://api.github.com` over stdlib `urllib`. Mirrors
slack/telegram in shape: `is_configured`, `run_loop`, `setup`, `auth`,
`bind`. State at `.brr/gates/github.json`: token (when stored),
`bot_login`, `repo`, `triggers`, polling cursors.

### Triggers

Two opt-in trigger types; both can run at once:

- **`label-on-issue`**: polls `GET /repos/{repo}/issues?state=open&
  labels={label}&since={cursor}`. New labelled issues become inbox
  events. PRs returned by this endpoint are deliberately filtered
  out; PR work belongs to the mention trigger because PRs almost
  always have ongoing back-and-forth.
- **`mention-in-comment`**: polls `GET /repos/{repo}/issues/comments?
  since={cursor}` (returns both issue and PR comments). Comments
  containing the configured mention string become events. The bot's
  own login is filtered so a reply doesn't re-trigger itself.

PR-comment events derive their `branch_target` by fetching
`/repos/{repo}/pulls/{number}` once per unique PR per loop tick. This
is the load-bearing seam to Phase 1: the daemon's
`_branches_to_refresh` already understands `branch_target`, so
"comment `@brr fix the failing test` on a PR" Just Works — the
worktree starts on a freshly fast-forwarded copy of the PR head.

`_POLL_INTERVAL = 60`. Authenticated REST quota is 5000/hr; this
consumes ~120/hr at most. Each trigger keeps both a `since` cursor
and a bounded set of seen IDs (`_SEEN_CAP = 500`) to dedupe across
overlapping windows.

### Auth

`resolve_token(state)` order: stored > `gh auth token` > `GITHUB_TOKEN`
or `GH_TOKEN` env > nothing. `auth(brr_dir)` runs the chain at setup,
calls `GET /user` to validate, and records the bot login plus a
`token_source` marker. gh CLI / env tokens are *not* persisted — the
chain re-resolves them on every run, so `gh auth refresh` flows just
work. An operator who pastes a token gets it stored under `.brr/`
(already gitignored).

`bind(brr_dir)` autodetects the repo from `git remote get-url origin`
(both HTTPS and SSH forms recognised; non-github.com hosts return
None) and prompts for label / mention configuration with sensible
defaults (`brr` and `@brr-bot`).

### Response delivery

On the daemon's `done` packet, the gate posts a comment via
`POST /repos/{repo}/issues/{issue_number}/comments` (PR-comment
events use the PR number, which is the issue number in GitHub's
API). Inbox event and response file are deleted on successful POST.

### Error handling

`_handle_api_error` interprets `Retry-After`,
`X-RateLimit-Reset`/`X-RateLimit-Remaining: 0` and 4xx vs. 5xx
distinctly:

- `Retry-After` wins when present.
- Else, exhausted rate limit sleeps until `X-RateLimit-Reset`.
- Else, 4xx (non-transient: bad token, missing repo) backs off the
  full `_BACKOFF_MAX = 120`s — surfaces the failure without spamming.
- Else, 5xx / network errors back off the poll interval.

### Tests

[`tests/test_github_gate.py`](../tests/test_github_gate.py) covers
the token resolution chain (stored / gh CLI / env / prompt), repo
autodetect from both HTTPS and SSH origin URLs, the label trigger
including the PR-skip rule, the mention trigger producing
`branch_target` for PR comments and not for issue comments, the bot
self-filter, comments without the mention being ignored, polling
cursor advancement, response posting, the rate-limit /
`Retry-After` / 4xx error matrix, and the no-op path when the gate
is unconfigured. All API calls are mocked at the
`_api_get` / `_api_post` boundary — the same pattern slack and
telegram use.

Other forges (`gitlab`, `gitea`, `bitbucket`) get their own modules
when each is genuinely wanted; the github gate establishes the
pattern — one module per provider, shared file-protocol contract,
no abstract base class.

## Phase 3 — Runner thoughtfulness

Status: planned, not yet shipped.

Three small sharpenings of the existing single-pass runner surface,
not a pre-task plan stage:

- Trigger-phrase guidance in [`prompts/run.md`](../src/brr/prompts/run.md)
  naming the failure mode and authorizing a chat-only response when
  the task body uses revisit/reconsider signals.
- "No commit" as a first-class outcome for revisit/discussion tasks
  (the diff-as-receipt rule does not apply when there is no clear edit
  to make yet).
- One new self-review bullet in [`AGENTS.md`](../src/brr/AGENTS.md)
  asking "if the task contained a contradiction, did you surface it
  before resolving it?" Maps directly to the existing Stewardship
  section.

Why not a stage:

- No latency cost for normal tasks. The 95% case (clear implement /
  fix / Q&A) is unchanged.
- A separate stage would split the design from the execution into
  different runs, making follow-through worse not better.
- Path-of-least-resistance shipping is a judgment failure, not a
  procedural one. Make the judgment explicit in the prompt rather
  than bolting on a stage that produces another judgment-shaped
  artifact.
- Post-task kb-maintenance is justified because it's mechanical
  (lint-style checks). A pre-task plan stage would not be mechanical,
  so the analogy doesn't carry over.

Guardrail tests in `tests/test_prompts.py` will pin the new bullets
so future prompt edits that drop them fail loudly.

## Boundary

- **Pure git refs and freshness** belong to the daemon. Owned by
  [`sync.py`](../src/brr/sync.py); the operator's only knobs are the
  two `sync.*` config keys.
- **Forge concepts** — PRs, issues, comments, labels, mentions —
  belong to per-provider gates. They share the file-protocol contract
  but everything else is provider-specific.
- **Internal task plumbing** — task construction, branch plan
  resolution, env preparation — stays in the daemon and is not
  exposed to gates.

## Lineage

Replaces the deleted tasks-folder git gate (commit will be in the
Phase 1 landing on `brr/daemon-sync`) which conflated event input
with daemon freshness, and the unmerged `brr/git-gate-defaults`
branch which mistook "on by default" for the real fix.

## Read next

- [`subject-daemon.md`](subject-daemon.md) for the daemon hub this
  page hangs off.
- [`subject-tasks-branching.md`](subject-tasks-branching.md) for the
  branch plan resolution that the seed-ref invariant feeds.
- [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md)
  for the structured-branch contract that Phase 1 reuses and Phase 2
  hooks into.
- [`gates/README.md`](../src/brr/gates/README.md) for the file
  protocol every gate (built-in or script) shares.
