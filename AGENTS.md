# Project

brr is a structured AI agent playbook with persistent knowledge base and
remote execution.  It produces `AGENTS.md` (this file) — a playbook that
any AI tool reads — and adds a daemon layer for remote task execution via
gates (Telegram, Slack, Git).

Stack: Python 3.10+, stdlib only (no runtime deps).

## Build and run

```bash
pip install -e ".[dev]"
pytest
brr init
brr run "describe the project"
```

## Code guidelines

- Python 3.10+, stdlib only for core.  No third-party runtime deps.
- Format: default style, no enforced formatter.  Keep it readable.
- Tests: pytest.  Run with `pytest` from repo root.
- Commits: one logical commit per task.  Message explains *why*.
- Naming: runner (not executor), gate (not connector), kb (not state).

## Workflow

### Session startup

1. Read `kb/index.md` to understand what knowledge exists.
2. Read `kb/log.md` for recent activity — the last 5-10 entries give you
   context on what happened before this session.
3. If a task is provided, proceed.  If resuming, continue where the last
   session left off based on the log.

### Commits

Commit directly on the current branch.  Do not create feature branches —
the orchestrator manages branching when needed.

One logical commit per task.  The commit message should explain *why*,
not *what* — the diff shows the what.

Only commit material changes.  If the task was read-only (review,
research, verify), do not commit.

### Task types

- **Implement / fix** — code, test, commit.
- **Review / verify / check** — read, analyse, report.  No commit.
- **Research / plan** — investigate, write findings to `kb/` or a gist.
- **Release / deploy** — follow the release process exactly.

## Knowledge base

The `kb/` directory is a persistent, LLM-maintained knowledge base
committed to the repo.  It compounds across sessions.

- `kb/index.md` — master catalog.  One line per page with link + summary.
- `kb/log.md` — chronological activity log.

Log format: `## [YYYY-MM-DD] <type> | <title>` followed by description.

Persist decisions, discoveries, research, architecture notes.  Don't
duplicate code — reference it.  Update decision pages when they change.

## Artifacts

If output exceeds a few hundred lines, write to a file or gist.
Produce rich artifacts when applicable: mermaid diagrams, markdown
tables, Marp slides, charts.  Match artifact to task scope.

## Operating rules

- Match effort to task size.
- Two failed attempts at the same approach → stop and report.
- If scope expands beyond the original task, note it and pause.
- If something is out of reach (credentials, external service), note it.

## Self-review

Before marking complete: re-read the task, review changed files, run
tests, update kb/log.md and kb/index.md as needed.

## Guardrails

- Do not commit secrets (.env, tokens, credentials).
- Do not modify files outside the project scope.
- Do not explore or modify `.brr/` internal files.

## Constraints

- `src/brr/prompts/` — bundled prompt files.  These are the product.
  Changes here affect every user.
- Gate credentials live in `.brr/gates/` — never commit them.
- Keep zero runtime dependencies.  stdlib only.
