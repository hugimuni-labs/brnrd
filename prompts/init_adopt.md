You are preparing a Git repository for managed AI operation. Analyse the
repository and produce two outputs.

## Step 1: Structured analysis

Extract the following by reading the repo's files, scripts, docs, and
any existing instruction files (CLAUDE.md, GEMINI.md, .cursor/rules, etc.):

1. **Purpose** — one sentence describing the project.
2. **Commands** — exact build, test, verify, start, lint commands found in
   Makefiles, package.json, justfiles, CI configs, etc.
3. **Policies** — constraints, code style rules, branch conventions, testing
   requirements. Quote them briefly.
4. **Task sources** — TODO files, roadmaps, issue trackers the agent should
   check for work. Provide paths.
5. **Existing instruction files** — list them and whether to keep or replace.

Emit this as JSON:

```json
{
  "project_purpose": "...",
  "commands": {"build": "...", "test": "...", "verify": "...", ...},
  "policies": ["...", "..."],
  "task_sources": ["...", ...],
  "keep_files": ["CLAUDE.md", ...],
  "rewrite_files": ["AGENTS.md", ...]
}
```

## Step 2: AGENTS.md content

Using the analysis above, write the Markdown body for AGENTS.md. This is
the section below the YAML frontmatter. It should contain:

1. A short project description (what it is, what stack).
2. Build and run instructions (exact commands).
3. Code guidelines (style, testing, commit conventions).
4. Constraints (things the agent must not do without approval).

Keep it under a page. Be specific and direct — this will be read by an AI
executor on every run, so every word should earn its place.

Emit the body as a Markdown block after the JSON, separated by `---`.

Return only the JSON and the Markdown body. Do not write files.
