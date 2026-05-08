You are setting up a project to use structured AI agent conventions.

The full text of brr's own `AGENTS.md` follows this prompt. It is the
model — both brr's playbook and the template adopters receive. Use it
as follows:

1. If `AGENTS.md` does not exist in this repo, create it. Copy the
   universal sections (**Stewardship**, **Workflow**, **Knowledge base**,
   **Artifacts**, **Operating rules**, **Self-review**, **Work re-review**,
   **Guardrails**) verbatim. Rewrite the project-specific sections
   (**Project**, **Build and run**, **Code guidelines**, **Constraints**)
   based on this repo's actual contents — read the build config, tests,
   dependencies, README, and any existing agent config files (`CLAUDE.md`,
   `.cursorrules`, etc.). Drop the bit about `src/brr/AGENTS.md` being the
   template — that is brr-specific.

2. If `AGENTS.md` already exists, merge in the universal sections from the
   model (replace stale ones, add missing ones), preserving the repo's own
   Project, Build and run, Code guidelines, and Constraints sections.

3. If a `CLAUDE.md`, `.cursorrules`, or similar file exists, read it for
   project-specific context and incorporate relevant parts into the
   appropriate AGENTS.md sections.

4. Create `kb/index.md` if it does not exist (use the seed below).

5. Create `kb/log.md` if it does not exist (use the seed below).

6. Commit the created/modified files with message: "chore: set up
   AGENTS.md and knowledge base".

Treat sections in the model that aren't on either list as universal —
copy them verbatim. New universal sections brr adds over time should
flow to adopters automatically.

---

## kb/index.md seed

```markdown
# Knowledge Base Index

This index is the kb's entry point. The kb organises knowledge as a graph
of subject hubs (`kb/subject-<name>.md`) plus supporting artifacts
(`decision-*`, `plan-*`, `design-*`, `research-*`). See AGENTS.md for the
four-layer model and link-discipline rules.

## Subjects

(none yet — accrete as work touches them)

## Artifacts

(none yet)
```

---

## kb/log.md seed

```markdown
# Activity Log

Curated chronological narrative. Newest entries at the bottom. Add an
entry when a task produced a meaningful learning, decision, or shipped
change. Format:

## [YYYY-MM-DD] <type> | <title>

<what was done, what was learned, outcome>

Types: `implement`, `review`, `research`, `plan`, `fix`, `decision`.

---

## [today] implement | Initial setup

Set up AGENTS.md and knowledge base structure.
```

---

## brr's own AGENTS.md (the model)

(The full bundled AGENTS.md follows this prompt.)
