You are setting up a repository for structured AI agent work.
Read the repo and fill in the AGENTS.md body sections.

Replace every HTML comment placeholder (<!-- ... -->) with content
specific to this repository.

## What to fill in

**# Project** — one paragraph: what this is, what stack, what it does.

**## Build and run** — exact commands from Makefile, package.json,
pyproject.toml, CI configs, justfile, scripts/, etc.  Use code blocks.
If no build system exists, say so.

**## Code guidelines** — language version, formatting tools, test
framework, commit message style, branch naming.  Be specific to what
you find in the repo, not generic advice.

**## Constraints** — sensitive directories, deployment commands, public
API surfaces, payment/auth code — anything an agent should not change
without explicit approval.

Also fill in the YAML `commands:` block (build, test, verify) with the
actual commands you found.

## What to leave as-is (unless the repo has specific conventions)

**## Workflow** and **## Guardrails** have sensible defaults.  Only
modify them if the repo has established conventions you can identify
(e.g. a specific branching strategy, required reviewers, CI gates).

## Result quality

Keep the whole body under a page.  Every line is read by an AI
executor on every single task — bloat directly hurts performance.
Be direct.  Use code blocks for commands.  No generic advice.
Do not modify the YAML frontmatter beyond the `commands:` block.
