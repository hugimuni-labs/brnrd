# Project

brr is a structured AI agent playbook tool with remote execution.  It produces
`AGENTS.md` — a playbook encoding project conventions, workflow, and guardrails
that any AI tool can read.  A daemon layer adds remote execution via gates
(Telegram, Slack, Git).  Pure stdlib Python (>=3.10), zero runtime dependencies.

## Build and run

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Or with uv
uv pip install -e ".[dev]"

# Run CLI
brr --help
python -m brr --help

# Run tests
pytest
```

## Code guidelines

- Python >=3.10, stdlib only — no runtime dependencies.
- Dev dependency: `pytest>=7.0`.  Tests live in `tests/`.
- Build system: setuptools (pyproject.toml).
- No formatter/linter configured yet — follow existing code style.
- Commit messages: conventional style (`fix:`, `feat:`, `chore:`, `refactor:`),
  explain *why* in the body.

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
not *what* — the diff shows the what.  Include the task summary in the
first line.

Only commit material changes.  If the task was read-only (review,
research, verify), do not commit.

### Task types

Adapt your approach:

- **Implement / fix** — code, test, commit.
- **Review / verify / check** — read, analyse, report.  No commit.
- **Research / plan** — investigate, write findings to `kb/` or a gist.
- **Release / deploy** — follow the project's release process exactly.

## Knowledge base

The `kb/` directory is a persistent, LLM-maintained knowledge base
committed to the repo.  It compounds across sessions — every task
should leave the kb richer than it found it.

### Structure

- `kb/index.md` — master catalog of all kb pages.  One line per page
  with a link and a brief summary, organized by category.  Update
  whenever you create or remove a page.
- `kb/log.md` — chronological activity log.  Append an entry for
  every task.

All other pages are created organically as needed: decisions,
architecture notes, research, discovered patterns, gotchas.

### Log format

Each entry in `kb/log.md` uses this format for parseability:

```
## [YYYY-MM-DD] <type> | <title>

<what was done, what was learned, outcome>
```

Types: `implement`, `review`, `research`, `plan`, `fix`.

This format lets anyone run `grep "^##" kb/log.md | tail -5` to see
recent activity at a glance.

### What to persist

- **Decisions** — create a dedicated page when a meaningful decision is
  made (architecture, library choice, trade-off).  Include the context,
  alternatives considered, and why this option was chosen.
- **Discoveries** — if you find something non-obvious during work (a
  gotcha, an undocumented dependency, a pattern that would save time
  next run), create a kb page.  The next session benefits from what
  you learned today.
- **Research** — investigation results, comparisons, analysis.
- **Architecture** — system overviews, data flows, component maps.

### What not to persist

- Transient task status (the log covers this).
- Verbose debug output.
- Anything that duplicates what's already in the codebase (don't
  copy code into kb — reference it).

### Contradiction handling

If new work contradicts a previous decision recorded in kb/, update the
decision page — note what changed, why, and when.  Don't silently
overwrite; the history of why decisions evolved is as valuable as the
decisions themselves.

### Health checks

When resuming work or between tasks, scan kb/ for:

- Stale log entries that newer work has superseded.
- Pages referenced in index.md that no longer exist.
- Decisions that have been reversed by later work without updating
  the decision page.
- Important concepts mentioned but lacking their own page.

Clean up as you go.  A healthy kb is a useful kb.

## Artifacts

### Long output

If your response would exceed a few hundred lines, write it to a file
or create a gist (`gh gist create`) and reference the link.  Chat
connectors have message size limits.

### Rich artifacts

When the task warrants it, produce artifacts a human would want to share:

- **Mermaid diagrams** for architecture, data flows, state machines.
- **Markdown tables** for comparisons and structured data.
- **Marp slide decks** for presentations and executive summaries.
- **Charts** (matplotlib, etc.) for data analysis.

Match the artifact to the task — a one-line fix does not need a slide
deck, but a research task or architecture review deserves a well-structured,
readable output.  When in doubt, err toward producing something visual.

### Filing artifacts

Research results, analysis, and reusable artifacts go into `kb/`.
One-off answers and short summaries go directly in the response.

## Operating rules

**Proportionality.** Match effort to task size.  A one-line fix does not
need a multi-file refactor.  A question does not need a prototype.

**Scope drift.** If work expands beyond the original task, pause and
note what you found.  Do not silently take on unbounded scope.

**Dead ends.** Two failed attempts at the same approach — stop and
report what you tried rather than retrying.  Suggest alternatives.

**Dependencies.** If the task requires something outside your reach
(credentials, external service, human decision), note it clearly and
move on to what you can do.

## Self-review

Before marking a task complete:

1. Re-read the original task.  Does your work actually address it?
2. Review every changed file.  Look for leftover debug code, TODOs
   you forgot to address, commented-out code.
3. Run tests if available and applicable.
4. Check that kb/log.md has been updated with this task.
5. Check that kb/index.md is current if you created or removed pages.

## Work re-review

When resuming a session (new conversation, fresh context):

1. Read `kb/index.md` first — understand what knowledge exists.
2. Read `kb/log.md` — understand what happened recently.
3. If continuing previous work, read the relevant kb pages for
   context before making changes.
4. If the previous session left TODOs or open questions in the log,
   address them.

## Guardrails

- Do not commit files containing secrets (`.env`, credentials, tokens).
- Two failed attempts at the same approach → stop and report.
- Do not delete or overwrite files outside the project scope.
- When in doubt, write down what you know and what you're unsure
  about, and let the user decide the next move.

## Constraints

- `.brr/` is a runtime directory (gitignored) — do not commit its contents.
- `src/brr/prompts/` contains bundled prompt templates — changes affect all users.
- Gate implementations (`src/brr/gates/`) follow the file protocol spec in
  `src/brr/gates/README.md` — maintain protocol compatibility.
- Zero runtime dependencies is a hard constraint — stdlib Python only.
