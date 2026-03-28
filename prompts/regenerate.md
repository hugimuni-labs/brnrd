You are updating a repository that is already brr‑managed.  You have
access to the current `AGENTS.md` and `agent_state.md` files.  Your task
is to propose updates while preserving user‑owned sections.

1. **Analyse current instructions.** Read the YAML header and the body
   of `AGENTS.md`.  Note the current commands, policies, task sources and
   operating model.
2. **Review memory.** Read `agent_state.md` and extract the current
   focus, key decisions, discoveries, next steps and open questions.
3. **Identify drift.** Check whether commands have changed in scripts or
   CI configuration.  Look for new TODO sources or removed ones.
4. **Propose updates.** If new commands or policies are detected,
   include them.  If some commands no longer exist, mark them for
   removal.  If the mode should change (e.g. from incubating to live),
   suggest it.
5. **Emit JSON diff.** Produce a JSON object describing:

```
{
  "update_commands": {"verify": "…", …},
  "remove_commands": ["start", …],
  "add_policies": ["…"],
  "remove_policies": ["…"],
  "add_task_sources": ["…"],
  "remove_task_sources": ["…"],
  "mode_change": "paused" | "incubating" | "live" | null
}
```

Return only this JSON.  Do not write files yourself.