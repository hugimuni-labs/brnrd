You are preparing a Git repository for managed AI operation.  Extract the
key aspects of the project and propose a normalised set of instruction
files.

1. **Purpose.** Summarise the project in one sentence.
2. **Commands.** List commands for build, test, verify, start, stop,
   status, logs and anything else you find in scripts or docs.  Use exact
   command strings.
3. **Policies.** Note constraints and rules: branch conventions, commit
   format, testing requirements, code style, security policies.  Be brief.
4. **Task sources.** Identify TODO files, roadmaps, issue trackers the
   agent should use.  Provide paths.
5. **File plan.** Decide whether existing instruction files should be kept,
   appended with a managed section, or replaced.
6. **Emit JSON.**

```json
{
  "project_purpose": "...",
  "commands": {"verify": "...", "status": "...", ...},
  "policies": ["...", "..."],
  "task_sources": ["...", ...],
  "keep_files": ["CLAUDE.md", ...],
  "rewrite_files": ["AGENTS.md", ...]
}
```

Return only this JSON.
