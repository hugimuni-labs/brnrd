# brr Internals

Orientation for an agent running under brr. This document ships with
the `brr` tool itself — it is not project-specific.

If you (the agent) are running and something about the environment is
confusing (unfamiliar folders, unexpected metadata in your prompt,
runtime paths you need to re-check), consult the generated run context
file before guessing.

## You might be running under brr

You can tell you are running under a brr-driven invocation by the
following signals in your prompt:

- An `Event:` and/or `Task ID:` line in the metadata block.
- A `### Delivery contract` block telling you stdout is the chat reply
  and pointing at a specific `.brr/responses/<event-id>.md` path.
- A `Shared runtime dir:` pointing at the main checkout's `.brr/`.
- A generated `.brr/runs/<task-id>/context.md` file named in the
  Task Context Bundle.

When you see these, you are not in a normal interactive session. You
are one step of a pipeline. Behave accordingly:

- Do your best-effort work within the task scope.
- Do not invent extra work to be "helpful".
- Do not explore or edit `.brr/` beyond what the task explicitly
  requires. It is runtime scratch space.
- Your exit / final message will be captured and forwarded to a human
  over a gate (Telegram, Slack, Git PR comment), so keep it focused.

## `.brr/` layout

All runtime state lives under `.brr/` at the repo root. It is
gitignored; do not commit its contents.

| Folder       | Purpose                                                            |
| ------------ | ------------------------------------------------------------------ |
| `inbox/`     | Incoming events from gates, one markdown file per event            |
| `tasks/`     | Task manifests, one per event (source of truth for the worker)     |
| `responses/` | Agent final responses destined for gate replies; per-event `<id>.partials/` hold queued interim replies |
| `outbox/`    | Per-event drop zone (`<id>/`) where the resident writes interim/interleaved replies mid-thought |
| `presence/`  | Who's awake right now — one JSON file per active thought/session, pruned on read |
| `dominion/`  | The resident's durable working memory (worktree on the `brr-home` branch); captured at sleep |
| `schedule/`  | Firing-state (`state.json`) for self-scheduled thoughts; specs live in the dominion's `schedule.md` |
| `runs/`      | Generated per-task context files for daemon runner invocations     |
| `conversations/` | Per-gate-thread append-only logs of events, tasks, artifacts, lifecycle updates |
| `traces/`    | Prompt + stdout + meta per runner invocation (cleaned on success)  |
| `reviews/`   | Reserved for explicit review artifacts; default tasks do not write here |
| `worktrees/` | Isolated git worktrees for concurrent tasks                        |
| `gates/`     | Per-gate auth/state JSON                                           |
| `prompts/`   | Legacy per-repo prompt overrides                                   |
| `docs/`      | User overrides of bundled docs (see below)                         |
| `config`     | Key=value runtime config                                           |

## Agent recovery surface

Agents should orient from the Task Context Bundle in the prompt. When
they need to re-check runtime details, they should read the generated
`.brr/runs/<task-id>/context.md` file named in the bundle. That file
replaces the old command cheat sheet for task/event recovery.

The agent does not run daemon lifecycle commands. `brr up` and
`brr down` are managed by the human operator.

## Developer reload

For brr self-development, use an editable install and start the
foreground daemon with:

```
brr up --dev-reload
```

This is an opt-in developer mode, not the default daemon lifecycle. It
watches brr's installed package files (`.py`, bundled markdown,
`Dockerfile`, and source-layout `pyproject.toml` when visible). When a
change is detected, the daemon re-execs the same Python command at a
safe boundary: before starting the next pending task, or after the
current task has produced its response, finalized, and attempted push.

The same mode can be enabled with `dev_reload=true` in `.brr/config`.
Normal `brr up` stays a stable foreground process; use an external
supervisor if you want restart policy outside local development.

## Override model

brr ships prompts and docs with the package. Lightweight runtime
choices belong in `.brr/config`, especially `runner`, `runner_cmd`,
and environment policy. Deep prompt or orchestration customization is
done by using a local checkout, editable install, or fork of brr.

Use `environment` for the user-facing execution policy:

- `environment=auto` — prefer configured Docker isolation, then fall
  back to worktree behavior.
- `environment=docker` — require Docker and `docker.image`.
- `environment=worktree` — run in a separate git worktree.
- `environment=host` — run directly in the main checkout (no isolation).

The legacy `env` and `default_env` config keys are still accepted, but
new config should use `environment`.

Branching is no longer carried on the task file. Before env prep the
daemon resolves a publish plan: seed ref, optional
`expected_publish_branch` (when the event named one), source string,
host checkout branch as context, and an optional `expected_remote_oid`
captured from the remote-tracking ref at task start for force-with-
lease pushes. Worktree and Docker runs always start on a fresh
`brr/<task-id>` branch sprouted from the seed ref. After the run,
finalize records `publish_branch` + `publish_status` on the task and
`daemon.publish` ships that branch — via a refspec push when the
agent kept the task branch but the event named a different expected
publish target, a leased force-push when the agent rewrote the
expected branch, or an ordinary push otherwise.
`branch.fallback` (or the legacy spelling `branch_fallback`) controls
the no-authority fallback. The only supported mode is `preserve`
(the default). Legacy values (`inbox`, `default`, `current`) warn once
on daemon start and downgrade to `preserve`.

Legacy per-repo override folders may still be read by the library, but
there is no public command to seed them:

| Bundled at                     | Per-repo override         |
| ------------------------------ | ------------------------- |
| `src/brr/prompts/<name>.md`    | `.brr/prompts/<name>.md`  |
| `src/brr/docs/<name>.md`       | `.brr/docs/<name>.md`     |

The runner checks prompt overrides first, then falls back to the
bundled copy. Docs helpers do the same for doc overrides when used
internally.

Project-specific knowledge belongs in `kb/` (the knowledge base),
never in `.brr/`. The split is:

- `kb/` — permanent, project-specific, committed to the repo. Owned by
  agents working in this repo.
- `.brr/` — tool runtime. Ephemeral by default; traces and any task
  failures/leftovers stay for inspection.
- `src/brr/docs/` (bundled) + `.brr/docs/` (override) — tool
  documentation, same across all repos unless a user overrides.

## KB maintenance: deterministic preflight, injected on wake

brr runs a deterministic kb consistency scan
(`brr.kb_preflight.scan(repo_root)`) over `kb/` as part of prompt
assembly (`prompts._build_kb_health_block`). When the scan finds
anything, the findings — plus a one-line graph-stats summary
(`brr.kb_health`) — ride into the resident's wake prompt as a
`kb health (deterministic preflight)` block, and the resident folds
fixes into its own thought. A clean scan injects nothing.

There is **no separate post-task kb-maintenance agent**. (Earlier
versions spawned a second LLM pass after every kb-touching task, with
its own `prompts/kb-maintenance.md`; removed 2026-06-08 — the resident
curates the shared kb as part of its single thought, with the
deterministic scan as the standing safety net. See
`kb/design-agent-dominion.md` and `kb/subject-daemon.md`.)

The preflight is cheap and structural — it only flags things a
deterministic scanner can be confident about:

- `missing-from-index` — a kb page exists on disk but isn't linked
  from `kb/index.md`.
- `stale-index-entry` — `kb/index.md` links to a path that doesn't
  exist on disk.
- `broken-link` — any kb page (other than `log.md`) links relatively
  to a path that doesn't exist.

Lifecycle-marker drift, contradictions with the log, and other
judgement calls aren't the scanner's job — they need synthesis the
resident does directly as it works.

### Why a deterministic safety net

Deterministic checks are cheap enough to run on every wake, so they
catch drift left by *previous* work too (say, a slashed page another
page still links to), surfaced where the resident is already working
rather than in a separate pass that has to be spawned and that
historically dropped its edits silently.

### Configuring it

In `.brr/config`:

- `kb_maintenance=auto` (default) — inject preflight findings on wake
  whenever the scan isn't clean.
- `kb_maintenance=never` — never inject; do kb hygiene by hand.

## Multi-response: interim + interleaved replies

The default delivery contract is one event → one final stdout → one
chat reply. On top of that, the resident can ship **interim** replies
mid-thought and **fold in** other pending events without waiting for
their own spawn. The mechanism is a file drop zone, mirroring the
diffense precedent (agent writes a known path, daemon picks up):

- **Drop zone** — `.brr/outbox/<event-id>/`. The resident writes a
  complete markdown reply per file (staging as `*.tmp` and renaming for
  an atomic write). The path rides the Task Context Bundle's delivery
  contract.
- **Drain** — on every heartbeat tick and once right after the runner
  returns, the daemon (`daemon._drain_outbox`) scans the drop zone
  oldest-first, promotes each file to a per-event partials queue
  (`protocol.write_partial` → `.brr/responses/<id>.partials/<seq>.md`),
  emits an `interim_response` packet, indexes the artifact on the
  conversation log, and removes the consumed file. A promoting drain is
  a positive liveness check-in.
- **Streaming delivery** — `runtime.deliver_stream` walks **active**
  events (`processing` *or* `done`): it delivers queued partials in
  order, deleting each after a successful send (so delivery is
  resumable), and only on `done` delivers the terminal `<id>.md` and
  cleans up the event, terminal file, and partials dir.
- **Interleaving** — an outbox file whose frontmatter names another
  pending event (`event: <id>`) is routed to *that* event's queue and
  that event is marked `done`, so its thread gets the reply and it never
  wakes as its own thought. The bundle lists other pending events so the
  resident knows what it can fold in. Unknown targets are dropped, not
  misrouted.

This is additive and backward compatible: a thought that prints one
final stdout and writes nothing to its outbox behaves exactly as before.
A finer *silence-based* idle-kill is *not* built on this — interim
check-ins are opportunistic, so their absence doesn't reliably mean
wedged. The liveness budget itself (`runner.timeout_seconds`) is now
heartbeat-enforced and agent-extensible: a long-running thought writes a
`.keepalive` control dotfile in its outbox (an ISO time or `+30m`-style
duration) to push the deadline out, capped at a hard ceiling, and
shutdown kills the in-flight runner to reclaim the slot. The full
protocol contract lives in `kb/design-multi-response.md`; the liveness
contract in `kb/review-daemon-coherence-2026-06.md` §2.

## Run progress UX

The daemon emits typed lifecycle packets through `brr.updates` for
every task: `task_created`, `env_prepared`, `container_started`,
`attempt_started`, `attempt_failed`, `retrying`, `run_started`,
`artifact_created`, `interim_response`, `finalizing`,
`container_preserved`, `push_started`, `push_done`, plus the terminal
`done` / `failed` / `conflict`.

Gates may opt in to a `render_update(brr_dir, packet)` hook to surface
progress to a human:

- The Telegram gate sends one progress message per task in the
  originating chat or topic on `task_created`, then edits the same
  message via `editMessageText` for later packets. Per-task state lives
  under `.brr/gates/telegram/progress/<task-id>.json` so concurrent
  workers never share a file.
- The Slack gate posts one threaded reply per task on `task_created`,
  then updates it with `chat.update`. Per-task state lives under
  `.brr/gates/slack/progress/<task-id>.json` on the same one-writer
  guarantee.
- Non-chat gates (script gates, future forge gates posting on issues
  or PRs) typically skip live progress and let the durable artifact —
  a commit, a comment, a delivered file — speak for the run.

Live progress is remote-first. There is no public local status or
inspect command; new lifecycle UX should flow through update packets,
`RunProgressView`, and gate renderers instead of reintroducing a
separate status module.

## Concurrency model

The daemon runs **single-flight**: one *thought* at a time, by design. A
resident agent's continuity lives in durable memory (the dominion), not in
throughput-parallel workers, so the local loop spawns one worker when idle
and lets new events wait. Per-task worktree/branch isolation and the
partitioned per-event/per-task state still hold — they let overlapping
thoughts (ad-hoc sessions, a second daemon) coexist without sharing a
mutable surface, coordinated by presence rather than a lock. See
`kb/subject-daemon.md` and `kb/design-agent-dominion.md` §4. Whether the
daemon should grow back toward owned concurrency is an open question — see
`kb/review-daemon-coherence-2026-06.md` §4.

When a worktree-backed task finishes, the daemon inspects the
worktree's git state. If the agent left commits on the original
`brr/<task-id>` branch and the branch plan has an auto-land target,
that target is fast-forwarded. If there is no target, or if the agent
created/checked out a different branch, the resulting branch is
preserved as-is. Conflicts preserve the task branch. The worktree is
removed only on a clean success with no uncommitted/untracked
leftovers; failures, conflicts, and dirty leftovers keep the worktree
for inspection. Docker tasks use the same worktree-backed branch
behavior with the same outcome-aware cleanup applied to the container
itself, and run the runner command inside the configured container
image.

The full env story — built-ins, configuration knobs, the docker
credential wiring, the durability contract, and the salvage rule —
lives in [`envs.md`](envs.md).

## Traces and forensics

Every runner invocation writes a trace directory under
`.brr/traces/<kind>/<label>-<timestamp>/` containing the prompt,
stdout, stderr, meta JSON, and any artifacts the runner produced.
Traces are always *written* — there is no operator switch — but
they're forensic-only: the daemon removes them when the task
finishes cleanly. Failures (`error`) and unmerged outcomes
(`conflict`) keep their traces so you can correlate the run-context
file with what the agent actually saw and said.

`.brr/` is gitignored, so traces stay local to whoever ran the
daemon. The durable record of a successful task is the git commit,
the response file at `.brr/responses/<event-id>.md`, and any kb
updates the agent committed — the trace would only repeat that
information.
