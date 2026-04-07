You are setting up a project to use structured AI agent conventions.

Your job:

1. If `AGENTS.md` does not exist, create it using the template below.
   Fill in the Project, Build and run, Code guidelines, and Constraints
   sections by reading the repository — look at the existing files,
   build config, tests, dependencies, README, and any existing
   agent config files (CLAUDE.md, .cursorrules, etc.).

2. If `AGENTS.md` already exists, merge the template's Workflow,
   Knowledge base, Artifacts, Operating rules, Self-review, Work
   re-review, and Guardrails sections into the existing file.  Do not
   overwrite the existing Project, Build and run, Code guidelines,
   or Constraints sections — those are user-authored.

3. If a `CLAUDE.md`, `.cursorrules`, or similar file exists, read it
   for project-specific context and incorporate relevant parts into
   the appropriate AGENTS.md sections.

4. Create `kb/index.md` if it does not exist (use the seed below).

5. Create `kb/log.md` if it does not exist (use the seed below).

6. Commit the created/modified files with message: "chore: set up
   AGENTS.md and knowledge base".

---

## AGENTS.md template

(The full template follows this prompt.)

---

## kb/index.md seed

```markdown
# Knowledge Base Index

Pages are organized by category. Update this file whenever you create
or remove a page.

## Architecture

(none yet)

## Decisions

(none yet)

## Research

(none yet)
```

---

## kb/log.md seed

```markdown
# Activity Log

Newest entries at the bottom. Format:

## [YYYY-MM-DD] <type> | <title>

<description>

---

## [today] implement | Initial setup

Set up AGENTS.md and knowledge base structure.
```
