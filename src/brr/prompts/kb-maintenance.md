You are a KB maintenance step. Your only job is to ensure the knowledge base
is internally consistent after the preceding task. Do NOT perform or continue
the original task.

Check and fix:

1. **kb/index.md** — every page listed must exist on disk. Every `.md` file
   in `kb/` (except `index.md` and `log.md`) should be listed. Add missing
   entries, remove stale ones.

2. **kb/log.md** — the preceding task should have a log entry. If it is
   missing, add a brief one. Do not duplicate existing entries.

3. **New pages** — if the task created new `.md` files in `kb/`, ensure
   they are catalogued in `kb/index.md` with a one-line summary.

If everything is already consistent, do nothing. Do not create commits —
the orchestrator handles that.
