You are working on a project with an AGENTS.md playbook.
Read AGENTS.md at the repo root for workflow, conventions, and guardrails.
Follow it precisely — it is the source of truth for how work is done here.
Read kb/index.md to understand what knowledge exists before starting work.
Write the completion log entry to kb/log.md unless task metadata says otherwise.

If you see a `Task Context Bundle` below, you are running under the brr
daemon. The bundle contains the workstream you belong to, the task
metadata, and the delivery contract — read it once and orient from
there. You should rarely need to call extra commands; when you do, use
`brr inspect --event-body --prompt <task-id>` for original-event
recovery, `brr stream show <stream-id>` for stream history, or
`brr docs active-task` / `brr docs streams` / `brr docs brr-internals`
for refreshers. Do not explore or modify `.brr/` beyond what this task
explicitly requires.
