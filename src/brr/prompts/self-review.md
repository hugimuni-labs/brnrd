## Environment ergonomics (requested)

End your final stdout reply with a short **Ergonomics review:** footer (2–4
sentences, plain prose). This is separate from task self-review in
`AGENTS.md` — it is feedback on *how easy the runner environment was*, not
whether the code change is correct.

Cover what helped and what friction you hit:

- **Orientation** — Was the Task Context Bundle (and log extract) enough?
  Did you need the runtime recovery file or extra `.brr/` exploration?
- **Tooling** — Missing or broken commands in this environment (tests,
  editors, `gh`, git helpers, network). Note workarounds you used.
- **Branch metadata** — If the task involved rebasing or working on a
  branch other than your task branch, was bundle branch/publish metadata
  clear or confusing?

Be specific and honest; skip boilerplate. If nothing notable, say so briefly.
