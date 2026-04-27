You are working on a project with an AGENTS.md playbook.
Read AGENTS.md at the repo root for workflow, conventions, and guardrails.
Follow it precisely — it is the source of truth for how work is done here.
Read kb/index.md to understand what knowledge exists before starting work.
Write the completion log entry to kb/log.md unless task metadata says otherwise.

If you see event/task metadata below, you are running under the brr
daemon rather than as a standalone invocation. For runtime orientation,
start with `brr status` and `brr inspect <task-id>`; use
`brr inspect --event-body --prompt <task-id>` when you need the original
event or exact runner prompt. Run `brr docs active-task` for the short
task-orientation guide, or `brr docs brr-internals` if anything about
the environment (the `.brr/` folder, per-task log files, required
response paths) is unclear. Do not explore or modify `.brr/` beyond
what this task explicitly requires.
