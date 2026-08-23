---
gate: telegram
---
`brr/the-drift-that-had-two-causes` · `ab69aecc` · report `/tmp/brr-report-drift.md`

**What the system already knew:** nothing about workflow-run health. No deploy-pipeline cache existed. The fix is new infrastructure.

**What was built:**

`forge_workflow_cache.py` (new) — mirrors `forge_pr_cache` pattern: daemon warms `.brr/forge-workflow-state.json` via `gh run list`, prompt reads it network-free. `runs=None` = unknown, never "no failures."

`forge_state.py` — four new functions, `render_prod_line()` extended. The four rendered cases:

```
behind + healthy:  … · 36 commits behind origin/main (…) · deploy lane: healthy
behind + failing:  … · 36 commits behind origin/main (…) · DEPLOY LANE FAILING (12 consecutive)
behind + 1 fail:   … · 36 commits behind origin/main (…) · deploy lane: last run failed
behind + unknown:  … · 36 commits behind origin/main (…) · deploy lane: unknown
matching + healthy: … · matches origin/main
```

Unknown renders as unknown, never as healthy. 14 renderer-driven tests, all pass (244 forge total).

**Open edge — outside my files:** `daemon.py:15605` needs:
```python
forge_workflow_cache.refresh_if_stale_async(
    repo_root,
    workflow_file=cfg.get("deploy.workflow_file") or None,
)
```
alongside the existing `forge_pr_cache.refresh_if_stale_async` call. Until wired, every wake shows "deploy lane: unknown" — honest, not the original silence.
