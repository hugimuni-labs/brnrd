# Research: daemon runner context ergonomics, 2026-05-09

Point-in-time review of what it took for a daemon-launched agent to
recover task meaning, understand the environment, and decide whether
the prompt/runtime shape helped or got in the way.

This is not a new subject hub. It is a focused research artifact linked
from the kb index; broader source orientation remains in
[`repo-dive-in-map.md`](repo-dive-in-map.md), and the kb maintenance
pattern is synthesised in [`subject-kb.md`](subject-kb.md).

## Method

The task started from a brr daemon prompt with a Task Context Bundle and
an allowed generated run context file at `.brr/runs/<task-id>/context.md`.
Per the playbook, the orientation path was:

1. Read [`AGENTS.md`](../AGENTS.md), [`kb/index.md`](index.md), and the
   recent tail of [`kb/log.md`](log.md).
2. Read the generated run context file named in the bundle, but avoid
   exploring other `.brr/` runtime files.
3. Follow the repo's own source map in
   [`repo-dive-in-map.md`](repo-dive-in-map.md).
4. Inspect the runner-facing surfaces:
   [`src/brr/prompts.py`](../src/brr/prompts.py),
   [`src/brr/prompts/run.md`](../src/brr/prompts/run.md),
   [`src/brr/run_context.py`](../src/brr/run_context.py),
   [`src/brr/daemon.py`](../src/brr/daemon.py),
   [`src/brr/envs/__init__.py`](../src/brr/envs/__init__.py), and the
   bundled docs under [`src/brr/docs/`](../src/brr/docs/).

That path was mostly linear. The kb index and repo dive-in map were the
highest-leverage artifacts; without them the source search space would
have been much wider.

## Findings

### Context recovery mostly works

The current schema is strong. The Task Context Bundle gave task ID,
event ID, base/current branch, runtime dir, response path, and the run
context file. The run context file made the environment unambiguous:
this task was running as `Environment: docker` on branch
`brr/task-1778326643-33ug`, with image `brr-runner:dev` and the repo
mounted at the host path.

The kb shape also worked as intended. `kb/index.md` gave a subject-level
map; `kb/log.md` gave enough recent narrative to understand that this
task followed the completed kb-shape arc; `repo-dive-in-map.md` pointed
directly at the prompt, runner, env, daemon, and preflight modules.

### The daemon prompt was noisier than necessary

The same current event reached the agent in three forms:

- a current-event summary inside `Recent in this conversation`,
- the `Original event body` block,
- a trailing `Task: ...` block with the same body.

The comments in [`src/brr/prompts.py`](../src/brr/prompts.py) said the
recent conversation block represented history before the in-flight task,
but [`daemon._run_worker`](../src/brr/daemon.py) gathered recent
conversation records after appending the current event/task/update
records. For long Telegram requests, this duplication costs attention
and makes the "what exactly am I supposed to answer?" boundary less
clean.

This review fixed that directly: daemon prompt context now filters out
records for the in-flight event/task, and `build_daemon_prompt` no
longer appends a duplicate `Task:` block when the task body and original
event body are identical.

### Some bundled docs still contradicted the current contract

[`src/brr/docs/active-task.md`](../src/brr/docs/active-task.md) still
told agents to write the final response to the response path and to use
a per-task log file in worktree mode. That contradicted the current
stdout capture contract and the removal of `kb/log-<task-id>.md` files
recorded in [`decision-kb-shape.md`](decision-kb-shape.md).

[`src/brr/docs/execution-map.md`](../src/brr/docs/execution-map.md)
still listed `kb/log-<task-id>.md` under artifact locations and described
`traces/review` as part of the pipeline. The docs now say there are no
per-task kb log files, that `kb/log.md` is curated narrative only, and
that `.brr/reviews/` is reserved for explicit review artifacts rather
than part of the default lifecycle.

### Runtime `.brr/` boundaries are clear enough, but easy to overread

The delivery contract says not to explore or modify `.brr/` beyond the
run context file and explicitly required paths. The run context file
also lists event, task, response, and conversation-log paths. That is
useful recovery data, but it creates a small judgement call: does a path
listed in the generated context count as explicitly allowed to read?

For this task, the right behaviour was to read only the run context and
stay out of the raw conversation log. The prompt could be a little more
explicit: listed runtime paths are recovery pointers; read them only
when needed to resolve ambiguity, and never edit them.

### The Docker environment is not yet ergonomic for brr self-work

The run context said the task was in Docker with image
`brr-runner:dev`. Basic git and the three runner CLIs were available,
but at task start this live shell did not have `rg`, `python`,
`python3`, or `pytest` on PATH. That matters:

- the playbook tells agents to prefer `rg`;
- this repo is a Python project and its normal verification command is
  `pytest`;
- [`src/brr/Dockerfile`](../src/brr/Dockerfile) includes `ripgrep`, so
  the live image/session appears stale relative to the current bundled
  Dockerfile or is not exactly the same environment the Dockerfile
  describes;
- the previous Docker review in [`kb/log.md`](log.md) already flagged
  the lack of Python/project tooling, and this run reproduced that
  limitation.

The generic runner image is a reasonable base for AI CLI execution, but
it is not enough for brr development tasks that need tests. For brr
self-work, use `environment=worktree`/host when verification matters, or
run a project-layered Docker image that adds `python3`, `pip`, `pytest`,
and `rg` on top of the runner image.

### Extra tools or MCP were not the main gap

No external MCP capability was needed for this task. The useful tools
were filesystem reads, grep/find fallback, git, and the repo's own
source map. Missing local executables (`rg`, Python, pytest) were a
larger constraint than missing remote tools.

### Manual preflight exposed a small scanner robustness bug

Running `kb_preflight.scan(Path("."))` from the repo root initially
reported every kb page as missing from the index, even though the index
was correct. The daemon passes an absolute worktree path, so the live
daemon path was not broken; the helper itself mixed relative `kb/` paths
with resolved link targets. The scanner now resolves `repo_root` at the
start of `scan`, and a regression test covers relative repo roots.

## Changes made in this pass

- Filtered current event/task lifecycle records out of the recent
  conversation context sent to daemon prompts and run context files.
- Suppressed the duplicate trailing `Task:` block when it repeats the
  original event body.
- Updated prompt tests and daemon conversation tests for the cleaner
  context contract.
- Normalised `kb_preflight.scan`'s repo root handling so manual
  `Path(".")` calls do not produce false missing-index findings.
- Updated bundled active-task, execution-map, and internals docs to
  match the stdout response contract and the removed per-task-log
  mechanism.

## Follow-up recommendations

1. Add a snapshot-style test for a realistic full daemon prompt and run
   context file. The existing unit tests cover pieces, but a generated
   "what the agent actually sees" fixture would catch stale doc/prompt
   contradictions faster.
2. Add a lightweight environment/tooling preflight to the run context,
   especially for Docker: runner CLI, git, `rg`, Python, and configured
   test command availability. This should be informational, not a hard
   gate.
3. Build or configure a brr-specific development image for brr's own
   daemon dogfooding. Keep the generic runner image small, but do not
   use it for Python repo tasks that need tests.
4. Clarify in the run prompt that runtime paths listed in the generated
   context are read-only recovery pointers, not an invitation to browse
   `.brr/` broadly.
