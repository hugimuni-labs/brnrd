# Models & quota

brnrd separates the CLI process from the model it runs:

- **Shell**: the CLI on `PATH` — `claude`, `codex`, or `gemini`.
- **Core**: the model and its cost, capability, and quota metadata.

Together they form the Runner for one wake. The resident remains the same when
the Runner changes.

Inspect the profiles available on this machine:

```bash
brnrd runners list
brnrd runners list --all
```

## Pin or let brnrd choose

Preferred project settings live in `.brr/config`:

```ini
shell=codex
core=default
runner_policy=fixed
```

Leave `shell` and `core` unset with `runner_policy=cost-aware` to let brnrd
choose the cheapest adequate available local Runner. Project-specific profiles
can live in `.brr/runners.md`.

## Escalate and downshift

A resident can hand a hard continuation to a stronger local Core after it has
read the repo, or pin an economy Core for bounded routine work. The handoff
keeps the conversation and prepared worktree. Quality escalation does not
silently opt into paid relay compute.

Quota is part of the live run posture and is shown to the resident before work
starts. Automatic fallback is deliberately narrow: classified local
quota/auth/provider failures may retry on another local Runner in the same or a
cheaper class. Paid relay remains behind explicit consent.
