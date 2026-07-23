You are setting up a project to use structured AI agent conventions.

The **adopter template** (`constitution.md`) follows this prompt. It is a
host-agnostic repository contract, split apart from brnrd's own internal
playbook — so it carries no brnrd-specific truth and no volatile daemon
machinery. Your job is to tailor it into this repo's `AGENTS.md`.

## What the template contains

- **Universal blocks** — sections wrapped in
  `<!-- brnrd:block id=… v=… hash=… -->` … `<!-- /brnrd:block -->` markers
  (How to read this playbook, Stewardship, Workflow, Knowledge base,
  Artifacts, Operating rules, Self-review, Guardrails). These are shared
  across every adopter.
- **Project placeholders** — the Project, Build and run, Code guidelines,
  and Constraints sections, each marked with a
  `<!-- brnrd:project id=… -->` comment describing what to write.

## Steps

1. **If `AGENTS.md` does not exist**, create it from the template:
   - Copy every universal block **verbatim, including its
     `<!-- brnrd:block … -->` marker lines and their `hash=` values**. Do
     not edit inside the markers, and do not recompute the hashes — brnrd
     verifies them and updates them on upgrade. The markers are what let a
     future `brnrd` upgrade refresh universal guidance by block identity
     without disturbing your tailoring.
   - Replace each `<!-- brnrd:project id=… -->` placeholder with real
     content for **this** repo: read the build config, tests,
     dependencies, README, and any existing agent-specific config files,
     and write the Project, Build and run, Code guidelines, and Constraints
     sections accordingly. Remove the placeholder comment and the italic
     stub line once written.

2. **If `AGENTS.md` already exists**, merge: replace any stale universal
   blocks with the template's current version (keep the markers), add
   missing ones, and preserve the repo's own Project, Build and run, Code
   guidelines, and Constraints sections.

3. If an agent-specific configuration file exists, read it for
   project-specific context and fold relevant parts into the appropriate
   sections.

4. **Knowledge base.** Follow the *Knowledge shape for this adopter*
   directive appended at the very end of this prompt — it says whether to
   scaffold a committed `kb/` (using the seeds below) or leave knowledge to
   the account home. Only create `kb/` files when that directive says so.

5. **Do not create `CLAUDE.md` yourself** — brnrd writes the shell bridge
   after setup and verifies each detected shell can reach `AGENTS.md`.

6. Commit the created/modified files with message: "chore: set up
   AGENTS.md and knowledge base".

---

## kb/index.md seed (committed-`kb/` shape only)

```markdown
# Knowledge Base Index

This index is the kb's entry point. The kb organises knowledge as a graph
of subject hubs (`kb/subject-<name>.md`) plus supporting artifacts
(`decision-*`, `plan-*`, `design-*`, `research-*`). See AGENTS.md for the
memory layers and link-discipline rules.

## Subjects

(none yet — accrete as work touches them)

## Artifacts

(none yet)
```

---

## kb/log.md seed (committed-`kb/` shape only)

```markdown
# Activity Log

Curated chronological narrative. Newest entries at the bottom. Add an
entry when a task produced a meaningful learning, decision, or shipped
change. Keep `.gitattributes` line `kb/log.md merge=union` so parallel Git
merges usually combine appended entries cleanly (append-only; avoid
concurrent edits to the same lines). Format:

## [YYYY-MM-DD] <type> | <title>

<what was done, what was learned, outcome>

Types: `implement`, `review`, `research`, `plan`, `fix`, `decision`.

---

## [today] implement | Initial setup

Set up AGENTS.md and knowledge base structure.
```

---

## The adopter template (tailor this into `AGENTS.md`)

(The full template follows this prompt.)
