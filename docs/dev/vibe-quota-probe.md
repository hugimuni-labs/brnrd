# Vibe quota probe — receipt

Bounded diagnosis run 2026-09-29 (run-260929-1956-bwzt), the first live Vibe
strand. No product code changed; this file is the evidence receipt that the
strand could read the tree, commit, and publish.

## Finding, in one paragraph

The claude row in portal `quota.others` reads `stale=true, read_at=null` with
live numbers (`binding_remaining_pct=81`) because the merged claude level
snapshot never carries a top-level `updated_at`:
`_merge_level_snapshots` (src/brr/daemon.py:3896) whitelists only
`quota/spend/context_window/plan_type` and copies the stamp into the quota
*block* (daemon.py:3920), while `other_fuel.fuel_row`
(src/brr/other_fuel.py:118) reads it at top level only. Codex is immune
(`codex_usage.merge_levels` stamps top level, codex_usage.py:379). The
boundary chip therefore renders `claude ?` beside a dashboard that shows
S100/W81 — same reading, one surface can prove its age and the other cannot.

## Repair + test

- Smallest correct repair: carry the first non-empty top-level `updated_at`
  through `_merge_level_snapshots` onto the merged dict (~3 lines). A
  narrower `fuel_row` fallback to `quota.updated_at` exists but leaves every
  other top-level reader undated — the bug class daemon.py:3912's comment
  records as already fixed once, one layer down.
- Regression test: drive `daemon._other_shells_fuel` with a real
  `.claude-usage-levels.json` under a tmp shared dir, *without* patching
  `_collect_levels` (the existing test at tests/test_other_fuel.py:113
  patches it, which is why this path never ran). Assert `stale is False` and
  `read_at == stamp` — both fail on trunk.

Full trace, line coordinates, and named edges: the run's report at
`.brr/reports/vibe-quota-probe-1d0h.md` (not committed — runtime path).

## Limits

Diagnosis only. No live daemon, PTY scrape, or test run was performed in this
strand; conclusions are from source on trunk at 3b4f96ab.
