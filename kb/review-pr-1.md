# Review: PR #1 (`feat/task-abstraction`)

Date: 2026-04-10

## Summary

Deep review of PR #1 found two concrete correctness issues in the implemented
task plumbing and one larger feature gap between the PR description/plan and
the code path actually executed by the daemon.

Two small fixes were applied directly in the working tree:

1. Event status now follows the task outcome (`done` / `needs_context` /
   `error`) instead of being forced to `done`.
2. `Task.from_event()` now honors explicit event `branch` and `env` fields
   instead of silently discarding them into `meta`.

## Follow-up

### 2026-04-14

The remaining triage gap from finding #1 has now been fixed in the working
tree. The daemon runs a real triage step before execution, parses the returned
task frontmatter/body into a persisted `Task`, and fails closed if the triage
output is malformed. The prompt-building duplication called out in PR comments
was also reduced, and the triage prompt now makes the `branch`/`env`
relationship explicit.

## Findings

### 1. Triage pipeline is described but not wired

The PR summary and plan documents describe a two-stage flow:

`event -> triage agent -> Task -> execution agent`

But the daemon still creates tasks directly via `Task.from_event()` and runs
them immediately. The new triage prompt builder exists, but nothing calls it.

Relevant code:
- `src/brr/daemon.py` creates the task directly in `_run_worker()`
- `src/brr/runner.py` defines `build_triage_prompt()` but it is currently dead code

Impact:
- The main advertised behavior ("triage agent decides branch/env") does not
  happen yet.
- `branch=auto` is not agent-decided in practice; it is only a stored field.
- `env` is similarly descriptive metadata today, not an execution decision.

### 2. Event status was being flattened to `done`

Before the local fix, `daemon.start()` always wrote `done` back to the event
file after `_run_worker()` returned, even if the task itself was
`needs_context` or `error`.

Impact:
- A gate or operator inspecting inbox state could not distinguish success from
  "needs more info" or failure.
- This also weakens any future retry/resume/cancellation logic built on event
  status.

Fixed locally in `src/brr/daemon.py`.

### 3. Explicit event branch/env overrides were ignored

The plans say event-level overrides should take precedence over defaults, but
`Task.from_event()` was always using config defaults for `branch` and `env`.
If an event arrived with those fields set, they were preserved only as opaque
metadata and never affected execution.

Impact:
- Existing-branch tasks cannot steer execution as designed.
- Any gate or script writing `branch:` / `env:` into the event file is ignored.

Fixed locally in `src/brr/task.py`.

## Verification

- Added daemon tests covering `needs_context` and `error` propagation.
- Added a task test covering event `branch` / `env` precedence over config.
- Full suite passes with `PYTHONPATH=src pytest`.

## Notes

The repo's current virtualenv imported `brr` from `.venv/site-packages`
instead of the source tree under `src/`, so plain `pytest` exercised the
installed package. Using `PYTHONPATH=src pytest` verified the working tree
changes correctly. If that becomes common, the developer setup should ensure an
editable install or otherwise force tests to import from `src/`.
