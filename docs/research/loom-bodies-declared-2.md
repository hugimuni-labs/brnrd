# Loom body table — second-build diagnostic

Status: blocked at the regression prerequisite; no runtime implementation changed.

Branch: `brr/loom-bodies-declared-2`. Seed: `619e53909e56d333a0db1e44bf8d1ad73f08130e` (`origin/brr/loom-bodies-declared`, fetched 2026-10-13). Main control: `9a084b92f1f086713968a0f06fd37f9038cb2b91`.

## Regression read

**I could not name a causal row or executor step.** The `running` × `alive, open` row still writes the same boundary; the jack owns the 0.8-second hold and does not read the body table. The first build changes the path that supplies that boundary: `_advance_bodies` gathers per-body state/config/gauge, executes boundary commands, then calls `_execute_body` a second time after routing. It also puts the new table on every jack subprocess's eager import path via `verbs_loom` → `loom` → `bodies`.

Those are additional costs, not a demonstrated cause. A provisional report sentence attributing the failure to them was too strong; this report replaces it. Before any runtime edit, the isolated seed failed at `test_holding.py:132`, `assert 1 >= 2`. An unchanged archive of fetched `origin/main` also failed the identical assertion with the specified repo interpreter. Thus this run did not reproduce the supplied branch-fails/main-passes distinction. Neither test was edited.

| check | result |
|---|---|
| Seed, isolated holding test | `1 failed in 7.58s` |
| Unmodified main archive, same isolated test | `1 failed in 7.08s` |
| Tentative removal of split dispatch and eager jack import, holding file | `1 failed, 1 passed in 20.94s` |

The last check falsifies the attempted repair in this run. All tentative runtime changes were removed. It was premature to attempt them before establishing the requested cause. No runtime change is being handed off as a fix.

Seven import-only subprocesses per tree measured median `verbs_loom` launch/import time of 0.1694 seconds on the seed and 0.1577 seconds on main. A single import trace assigned 25.7 ms to `bodies`; these observations do not account for two holds missing the 2.2-second assertion window and do not establish causality. The failure timing includes process startup and host load; the earlier first-build main pass remains a valid receipt for that earlier run, not a result reproduced here.

Failed holding assertions leave their gauge bodies running after the loom stops. The three exact fixtures created here were cleaned only after `_proven_body` verified their recorded PID/start time and room lock. No broad process cleanup was performed.

## Contract disagreements found by reading

| named row/test | disagreement to return to the maintainer |
|---|---|
| Startable × closed | First-build `test_closed_deferred_start_keeps_the_existing_gauge_path` requires `prepare, shown, spawn, started` for a closed pending start. The second-build contract requires `ended`. If “every existing test unchanged” includes spike tests on the seed, these requirements are mutually exclusive. Tests inherited from main and first-build-only spike tests need distinct treatment. |
| Exited → ended and crash recovery | A crash after `attempt.ended` but before the second drain/release leaves an unreleased holder in state `ended`. Adding only an `exited` row does not cover that prefix. The first-build generated test explicitly interrupts there and requires recovery. Either a terminal cleanup rule beside the table or an `ended` cleanup row is needed; silently retaining compatibility rows would miss the bar. |
| Table rendering | The first-build rendering test compares the renderer to `TABLE` itself, not the knowledge page. It does not meet the independent page-comparison bar. |

These are reported findings, not decisions settling the contract.

## Quantitative bar

Final tree keeps the seed's runtime and tests unchanged. This is not a successful second build.

| measure | final value | bar |
|---|---:|---|
| Table rows | 27 | Page's 16 plus `exited`: 17; no compatibility rows |
| Lifecycle lines in `loom.py` | 275 | Less than half of main's 267: at most 133 |
| `bodies.py` | 186 lines; 8,603 bytes | Report size |
| Existing tests modified | 0 | 0 |
| Holding regression | Failing | Green |
| Generated sequence | Seed's 20 × 350 steps; retained unchanged | All six rules at every step; no closed-start exception |
| Independent page comparison | Absent on seed | Required |
| Whole `tests/loom` final run | Not run | Required for a completed implementation |

Lifecycle count uses complete AST definition spans, including docstrings and internal blank lines, for `Body`, `_start`, `_refuse_author`, `pending_bodies`, `BodyContext`, `_observe_body`, `_body_fuel`, `_boundary`, `_execute_body`, `_advance_body`, and `_advance_bodies`, matching the first-build report's method.

## Open and unverified

1. **The holding cause remains unknown.** A controlled branch/main comparison that passes on main, or per-jack timings showing where the second hold falls outside the assertion, would resolve it. I did not establish a changed lifecycle rule by reading.
2. **The second-build bar was not met.** No full suite was run after abandoning the tentative edits; no green-suite claim is made. The diagnostic is the produce, not a rewritten lifecycle.
3. **`unidentified` finality remains the maintainer's.** No retry while closed and return to running on reopen was the dispatched prior. “Final” would remove the reopen transition and leave that process unidentified until a person ends it. No such policy change was implemented.

No PR opened. Publication receipt is maintained in the declared runtime report.
