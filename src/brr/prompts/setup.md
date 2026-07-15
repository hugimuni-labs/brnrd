You are setting up a project to use structured AI agent conventions.

The full text of brnrd's own `AGENTS.md` follows this prompt. It is the
model — both brnrd's playbook and the template adopters receive. Use it
as follows:

1. If `AGENTS.md` does not exist in this repo, create it. Copy the
   universal sections (**How to read this playbook**, **Stewardship**,
   **Workflow**, **Knowledge base**, **Artifacts**, **Operating rules**,
   **Self-review**, **Guardrails**) verbatim. Rewrite the
   project-specific sections (**Project**, **Build and run**, **Code
   guidelines**, **Constraints**) based on this repo's actual contents —
   read the build config, tests, dependencies, README, and any existing
   agent-specific config files. Drop the bit
   about `src/brr/AGENTS.md` being the template — that is brnrd-specific.

2. If `AGENTS.md` already exists, merge in the universal sections from the
   model (replace stale ones, add missing ones), preserving the repo's own
   Project, Build and run, Code guidelines, and Constraints sections.

3. If an agent-specific configuration file exists, read it for
   project-specific context and incorporate relevant parts into the
   appropriate AGENTS.md sections.

4. Create `kb/index.md` if it does not exist (use the seed below).

5. Create `kb/log.md` if it does not exist (use the seed below).

6. If this repo uses Git, add or update **`.gitattributes`** at the repo root
   so it contains `kb/log.md merge=union` (one line; merge with any existing
   rules). That nudges Git to union-merge the episodic log when parallel
   branches each append entries — still best practice to **append** new log
   sections only, not rewrite the same lines on concurrent branches.

7. Commit the created/modified files with message: "chore: set up
   AGENTS.md and knowledge base".

Treat sections in the model that aren't on either list as universal —
copy them verbatim. New universal sections brnrd adds over time should
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

## brnrd's own AGENTS.md (the model)

(The full bundled AGENTS.md follows this prompt.)
