# Decision: remove the triage stage

Status: accepted, 2026-05-06.
Supersedes: the `triage` step in `kb/repo-dive-in-map.md`, the
triage prompt template at `src/brr/prompts/triage.md`, and the
LLM-driven branching/env paths in
[`plan-branch-modes.md`](plan-branch-modes.md).
Sibling decisions in the same "drop the noisy abstraction" pattern:
[`decision-drop-streams.md`](decision-drop-streams.md) (workstreams
were the next layer to come off) and
[`decision-kb-shape.md`](decision-kb-shape.md) (per-task log files
came off after that).

## Context

Until now brr ran every event through a dedicated triage runner
invocation. The triage agent read the event body, decided a `branch`
strategy and an `environment` policy, and emitted a YAML-ish
frontmatter document that brr parsed back into a `Task`. The downstream
worker then prepared an env (host / worktree / docker), invoked the run
prompt, and captured the response.

Two failure shapes drove the rethink:

1. **Frontmatter-as-stdout-contract is brittle.** Real runners
   occasionally produce a preamble, a code fence, an explanation
   sentence, or a perfectly valid response that simply doesn't begin
   with `---`. brr rejected those with `invalid triage output: triage
   output is missing frontmatter` and never executed the task. The
   custom YAML-like parser in `protocol.parse_frontmatter` made this
   worse: it accepts only a narrow shape and silently discards
   anything that doesn't match the precise `^---\n...\n---` regex.
2. **Triage was solving the wrong problem.** Its main job was the
   branch / env routing decision. Branching is fundamentally a
   *post-hoc* property of the work — you can only really tell whether
   a change deserves its own branch after seeing the change. Asking
   an LLM to predict it ahead of time is a classification step that
   adds latency, cost, and a brittle parse failure mode for marginal
   gain.

## Decision

Remove the triage stage entirely. Replace the pipeline

```
event → triage (LLM, frontmatter) → task → env prep → run → response
```

with

```
event → Task.from_event (mechanical) → env prep → run → response
```

Concretely:

- **No triage runner invocation.** The daemon constructs the `Task`
  in pure Python from the event and `.brr/config`. There is no
  `_triage_task` function, no `triage.md` prompt, no
  `Task.from_triage_output`, no parsing of triage stdout.
- **Default env is isolated.** `environment=auto` resolves to:
  1. `docker` when `docker.image` is set and Docker is on PATH,
  2. `worktree` otherwise,
  3. `host` only when explicitly configured.
  The "trivial Q&A → host" routing that triage used to do is gone;
  the small env-prep cost of an always-isolated worker is the price
  for removing the LLM-based router. Users who want host execution
  for fast iteration set `environment=host` explicitly.
- **Worktree creation is unconditional.** `worktree.create(repo_root,
  task_id)` always creates `.brr/worktrees/<task-id>/` on a fresh
  `brr/<task-id>` branch from the current HEAD. This avoids the
  "branch already checked out" failure mode that triage's named-branch
  output could trigger, and it gives the agent a clean sandbox.
- **The agent owns branching at runtime.** The run prompt explains
  that the worktree starts on `brr/<task-id>`. The agent decides
  whether to:
  - leave commits on `brr/<task-id>` (brr will fast-forward into the
    base branch on cleanup — this is the "land it on the current
    branch" pattern),
  - `git switch -c <meaningful-name>` to keep a feature branch (brr
    preserves it, no auto-merge), or
  - `git switch <existing>` to continue work on a known branch (brr
    preserves it; if the branch is checked out elsewhere, git refuses
    and the agent surfaces the conflict in its response).
- **Finalize reads git state.** The cleanup logic looks at the
  worktree's HEAD: if it's still `brr/<task-id>` and fast-forwardable
  to the base branch, brr merges and deletes the branch. Otherwise it
  preserves whatever the agent ended up on.
- **Plain-text responses.** The runner's stdout is the response, full
  stop. brr no longer parses response frontmatter and no longer
  treats `status: needs_context` specially. If the agent can't
  complete the task — needs more info, hit a missing dep, ran into
  ambiguity — it just says so in its response. The operator reads
  the response in the gate (Telegram chat, Slack thread, etc.) and
  replies with another event. The conversation log already captures
  this loop.
- **Frontmatter stays where it earns its keep.** Event files
  (`.brr/inbox/<id>.md`) and task files (`.brr/tasks/<id>.md`) keep
  their human-readable `---` frontmatter. They are durable
  filesystem artefacts written and read by brr itself, not LLM
  outputs, so the parser is operating on text it controls.

## Consequences

- One fewer LLM call per event. Lower latency and lower cost on every
  task, including read-only ones.
- The entire `triage output is missing frontmatter` failure class
  disappears.
- The `branch` field on `Task` becomes vestigial. We drop it from the
  dataclass; `Task` carries `env`, `status`, `meta`, etc., and the
  concrete branch name (when there is one) lives in `meta` /
  conversation log records.
- `STATUSES` no longer includes `needs_context`. The lifecycle is
  `pending → running → done | error | conflict`. The progress
  projection stops emitting a `needs_context` phase.
- Run progress packet types shrink: no more `triage_done`. The
  `task_created` packet still fires immediately after `Task.from_event`
  so gates can show "task accepted" without waiting for env prep.
- Tests that mocked triage stdout disappear. New tests assert the
  mechanical event-to-task path and the agent-owns-branching cleanup
  logic.

## What we are *not* changing

- Conversation logs and run-progress projection still exist. They
  just have one fewer phase.
- Docker credential wiring, the durability contract, the salvage
  rule on error/conflict, and the trace layout under `.brr/traces/`
  are unaffected.
- Frontmatter on event and task files stays. Gates write it; brr
  reads it; both sides are mechanical.

## What this leaves room for

A future optional planning-validation phase is orthogonal. When we
add one it will be a deliberate stage with its own prompt, its own
structured output contract (probably much narrower than the freeform
triage frontmatter), and its own approval flow. The triage abstraction
would not have been the right shape for it; we're not preserving it
as a placeholder.

A later follow-up, [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md),
keeps the "agent owns branching at runtime" decision but removes the
daemon's ambient dependency on whichever branch the host checkout has
selected when a remote task lands commits.

## Lineage

- 2026-05-06 chat: live Telegram triage failure (`evt-1778094865223203321-axoa`)
  surfaced the brittleness; we walked through the design and agreed to
  remove the stage.
- Earlier groundwork: `kb/decision-drop-streams.md` already cut a
  layer that turned out to be doing too much; this is the same
  pattern at a different layer.
