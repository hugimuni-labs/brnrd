## Environment ergonomics (requested)

If — and only if — you hit friction worth acting on, end your final stdout
reply with a short **Ergonomics review:** footer (2–4 sentences, plain
prose). This is separate from task self-review in `AGENTS.md` — it is
feedback on *how easy the runner environment was*, not whether the code
change is correct.

Worth a note (be specific about the friction and any workaround):

- **Orientation** — Was the Task Context Bundle (and log extract) enough?
  Did you need the runtime recovery file or extra `.brr/` exploration?
- **Tooling** — Missing or broken commands in this environment (tests,
  editors, `gh`, git helpers, network).
- **Branch metadata** — If the task involved rebasing or working on a
  branch other than your task branch, was bundle branch/publish metadata
  clear or confusing?

**Skip the footer entirely when there's nothing notable** — no
"nothing to report" line. An empty review is noise for whoever reads it;
omitting it is the signal that the environment was fine.
