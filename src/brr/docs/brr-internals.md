# brr Internals

Orientation for an agent running under brr. This document ships with
the `brr` tool itself — it is not project-specific. To read it at
runtime, run `brr docs brr-internals`.

If you (the agent) are running and something about the environment is
confusing (unfamiliar folders, unexpected metadata in your prompt, a
per-task log file), consult this page or `brr docs execution-map`
before guessing.

## You might be running under brr

You can tell you are running under a brr-driven invocation by the
following signals in your prompt:

- An `Event:` and/or `Task ID:` line in the metadata block.
- An instruction to write a "final response" to a specific
  `.brr/responses/<event-id>.md` path.
- A `Write your log entry to kb/log-<task-id>.md` line (worktree mode).
- A `Shared runtime dir:` pointing at the main checkout's `.brr/`.

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
| `tasks/`     | Parsed task manifests, one per event (source of truth post-triage) |
| `responses/` | Agent final responses destined for gate replies                    |
| `streams/`   | Workstream manifests, append-only event/task/artifact records      |
| `traces/`    | Prompt + stdout + meta for every runner invocation (debug mode)    |
| `reviews/`   | Self-review notes the agent writes about its own runs              |
| `worktrees/` | Isolated git worktrees for concurrent tasks                        |
| `gates/`     | Per-gate auth/state JSON                                           |
| `prompts/`   | User overrides of bundled prompt templates (see below)             |
| `docs/`      | User overrides of bundled docs (see below)                         |
| `config`     | Key=value runtime config                                           |

## Commands the agent can use

These are always available inside a brr-driven task:

- `brr status` — active daemon + recent tasks + active worktrees +
  active streams.
- `brr inspect <task-id>` — cross-linked manifest for a task (event,
  branch, worktree, response, trace directories, stream link).
- `brr streams` — list workstreams.
- `brr stream show <id>` — manifest, recent tasks, artifacts.
- `brr docs` — list bundled documentation topics.
- `brr docs <topic>` — print a bundled doc (e.g. `execution-map`,
  `streams`, `brr-internals`).

The agent does not run `brr up`/`brr down`; the daemon is managed by
the human operator.

## Override model

brr ships a set of templates and docs with the package. Users can
override any of them by dropping a file with the same name into the
corresponding `.brr/` folder:

| Bundled at                     | Per-repo override         |
| ------------------------------ | ------------------------- |
| `src/brr/prompts/<name>.md`    | `.brr/prompts/<name>.md`  |
| `src/brr/docs/<name>.md`       | `.brr/docs/<name>.md`     |

The runner and `brr docs` both check the override path first, then
fall back to the bundled copy. `brr eject` copies all bundled prompts
into `.brr/prompts/` as a starting point for customisation.

Project-specific knowledge belongs in `kb/` (the knowledge base),
never in `.brr/`. The split is:

- `kb/` — permanent, project-specific, committed to the repo. Owned by
  agents working in this repo.
- `.brr/` — tool runtime. Ephemeral unless debug mode says otherwise.
- `src/brr/docs/` (bundled) + `.brr/docs/` (override) — tool
  documentation, same across all repos unless a user overrides.

## KB maintenance trigger

After every successful task, the daemon decides whether to run a
lightweight KB consistency pass (a second, short runner invocation
with `prompts/kb-maintenance.md`). The decision logic is:

```
policy = cfg["kb_maintenance"]  # default: "auto"

if policy == "never":   skip
if policy == "always":  run
if policy == "auto":    run only if this task actually touched kb/
```

The "did this task touch kb/" check is a git diff in the execution
root (the worktree for worktree tasks, or the main checkout for local
tasks):

```
git diff --name-only -- kb/          # tracked-file changes
git ls-files --others --exclude-standard -- kb/   # new untracked files
```

If either output is non-empty, the KB was touched and maintenance
runs. If the diff command fails (git missing, timeout) the check
returns False and maintenance is skipped. It's best-effort — a
failed maintenance pass is logged but never fails the parent task.

### Why this heuristic

Earlier drafts used task-body heuristics ("does the instruction look
big?") or triage-side classification. Those all require the triage
agent to guess ahead of time. The git-diff check is post-hoc: it
looks at what actually happened, not at what the agent was told to
do. A one-line fix that happened to add a kb entry gets maintained; a
big refactor that never touched kb/ doesn't pay the runner cost.

### Configuring it

In `.brr/config`:

- `kb_maintenance=auto` (default) — run only when kb/ was modified.
- `kb_maintenance=always` — run after every successful task.
- `kb_maintenance=never` — never run the maintenance step.

Set to `always` if you want stricter kb/ hygiene at the cost of one
extra runner invocation per task. Set to `never` if you prefer to do
kb maintenance manually.

## Concurrency model

The daemon processes events serially in v1. Worktree-based tasks
isolate the working directory and branch, but they do not yet run in
parallel — the worker pool is single-threaded. See
`kb/plan-concurrent-worktrees.md` for the roadmap in this repo.

When a worktree task finishes, the daemon merges the branch back (for
`auto`/`task` strategies) or preserves it (named branches), then
removes the worktree unless debug mode keeps it for inspection.

## Debug mode

`brr up --debug` (or `debug=true` in config) changes two behaviours:

- Traces are written for every runner invocation (prompt, stdout,
  stderr, meta, artifacts) under `.brr/traces/<kind>/...`.
- Worktrees are preserved after their task finishes instead of being
  removed — useful for post-mortem inspection.

Reviewers can correlate `brr inspect <task-id>` output with the trace
directories it lists.
