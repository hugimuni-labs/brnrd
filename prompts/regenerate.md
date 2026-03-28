You are updating a repository that is already brr-managed.  You have
access to the current `AGENTS.md` and `agent_state.md`.

1. **Read current instructions.** Note commands, policies, task sources
   and operating model from the YAML header and body.
2. **Read state.** Extract current focus, conversation topics, decisions,
   discoveries, next steps and open questions.
3. **Check for drift.** Compare commands against scripts and CI config.
   Look for added or removed task sources.
4. **Propose updates.** Include new commands or policies.  Mark removed
   ones.  Suggest mode changes if appropriate.
5. **Emit JSON.**

```json
{
  "update_commands": {"verify": "...", ...},
  "remove_commands": ["start", ...],
  "add_policies": ["..."],
  "remove_policies": ["..."],
  "add_task_sources": ["..."],
  "remove_task_sources": ["..."],
  "mode_change": "paused" | "incubating" | "live" | null
}
```

Return only this JSON.
