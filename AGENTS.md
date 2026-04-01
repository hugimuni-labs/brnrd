---
brr:
  version: 1
  mode: incubating
  default_executor: codex
  auto_approve: true
  commands:
    build: ""
    test: "pytest"
    verify: "pytest"
  task_sources: []
  state_file: .brr.local/state.md
  commit_policy: commit-at-end-if-material
---

# Project

brr is structured guardrails for AI agent work, managed from a chat.
It produces AGENTS.md — a universal instruction file that works with
any AI tool — and provides a remote execution layer via Telegram.
Python 3.10+, stdlib only, zero runtime dependencies.

## Build and run

```bash
pip install -e ".[dev]"
pytest
```

## Code guidelines

- Zero runtime dependencies. stdlib only.
- Keep the codebase small — every module earns its place.
- Use `executor` terminology consistently (not `runner`).
- The guardrails and workflow conventions belong in AGENTS.md (the
  universal file), not in run.md (the brr-specific glue).

## Workflow

**Branching.** Create a feature branch for code changes; commit with
a descriptive message when done.  For read-only tasks (review, verify,
research), report results without branching or committing.

**State file.** After each task, update `.brr.local/state.md`:
- Rewrite **Current Focus** to reflect where things stand.
- Add to **Conversation Topics** (one line: what was asked, what was
  done).  Keep the last ~10 entries; drop older ones.
- Update **Decisions**, **Discoveries**, **Next Steps**, **Open
  Questions** as needed.  Remove stale items — do not accumulate.

**Long output.** If your response would exceed a few hundred lines,
write it to a file or create a gist (`gh gist create`) and reference
the link.  The chat connector has a message size limit.

**Task types.** Adapt your approach to what is being asked:
- *Implement / fix* — branch, code, test, commit.
- *Review / verify / check* — read, analyse, report.  No branch.
- *Research / plan* — investigate, write findings to a file or gist.
- *Release / deploy* — follow the project's release process exactly.

## Guardrails

- **Dead ends.** Two failed attempts at the same approach → stop and
  report what you tried rather than retrying.
- **Scope drift.** If work expands beyond the original task, pause
  and note what you found.  Do not silently take on unbounded scope.
- **Proportionality.** Match effort to task size.  A one-line fix
  does not need a multi-file refactor.  A question does not need a
  prototype.
- **State tracking.** Always update the state file, even on failure
  or partial progress.  The next run depends on it.

When in doubt, write down what you know and what you are unsure about,
and let the user decide the next move.

## Constraints

- Do not add runtime dependencies.
- Do not commit secrets or `.brr.local/` contents.
- Do not modify the YAML frontmatter structure without discussion.
