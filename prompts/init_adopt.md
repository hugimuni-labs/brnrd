You are preparing a Git repository for AI‑assisted operation.  Your goal is
to extract the key aspects of the project and propose a new, normalised set
of instruction files.  Follow these steps:

1. **Identify purpose.** Summarise the project in one sentence.
2. **Enumerate commands.** List commands for build, test, verify, start,
   stop, status, logs and any other tasks you infer from existing scripts
   or documentation.  Use exact command strings when possible.
3. **Extract policies.** Note any constraints or operating rules (e.g.
   branch naming conventions, commit message format, testing
   requirements, code style, security policies).  Quote them briefly.
4. **Collect task sources.** Identify TODO files, roadmap documents or
   issue trackers that the agent should use for tasks.  Provide paths.
5. **Plan normalisation.** Decide whether existing instruction files
   should be kept as‑is, appended with a managed section or replaced.
6. **Emit structured proposal.** Produce a JSON object with fields:

```
{
  "project_purpose": "…",
  "commands": {
    "verify": "…",
    "status": "…",
    "start": "…",
    …
  },
  "policies": ["…", "…"],
  "task_sources": ["…", …],
  "keep_files": ["CLAUDE.md", …],
  "rewrite_files": ["AGENTS.md", …]
}
```

Return only this JSON.  Do not write files yourself.