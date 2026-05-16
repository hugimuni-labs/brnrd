# Test suite grooming — 2026-05-16

Status: shipped on 2026-05-16

This page records the shipped rationale for the 2026-05-16 test-suite
grooming pass. It began as review-only research, then the high-leverage
cuts landed in the same pass; the current shape is summarized here and
the chronological account lives in [`kb/log.md`](log.md).

## Current Shape

- The suite keeps focused `test_*.py` modules plus
  [`tests/_helpers.py`](../tests/_helpers.py) for shared scaffolding;
  live test counts belong in pytest output, not this historical
  rationale page.
- Shared scaffolding now lives in `tests/_helpers.py`:
  `init_git_repo`, `commit_files`, `write_repo_scaffold`,
  `make_event`, `StubWorktreeEnv`, and `succeed_invoke`.
- `tests/test_integration.py` is gone. Its useful coverage was weaker
  than `tests/test_adopt.py`; the one unique assertion only proved the
  runner was invoked, not that the existing-`AGENTS.md` merge contract
  was preserved.
- The daemon tests stay split by concern (`test_daemon.py`,
  `test_daemon_progress_packets.py`, `test_daemon_conversations.py`,
  `test_daemon_heartbeat.py`). After helper extraction, merging them
  would make a broad file without improving intent.
- Render-update tests and some older Docker invoke tests still have
  local helper shapes. They are acceptable until another gate or env
  makes another shared helper clearly worthwhile.

## Why These Cuts Landed

The grooming pass preserved test intent and removed scaffolding that
made intent harder to read:

- **Delete `test_integration.py`.** Three tests duplicated
  `test_adopt.py`; the fourth asserted only that the runner was called.
- **Extract explicit helpers.** A plain `tests/_helpers.py` module keeps
  setup visible at each call site and avoids implicit fixture state
  while removing repeated git/event/env stubs.
- **Stub `_forge_view_url` at the daemon boundary.** The daemon wrapper
  needs to read git/config inputs and swallow exceptions; URL templating
  belongs to `test_forges.py`.
- **Parametrize the Docker credential toggle.** Boolean `False` and
  string `"false"` exercise the same config-coercion behavior.
- **Tighten the no-triage assertion.** The important invariant is one
  daemon-run invocation on the happy path; the removed triage stage is
  already guarded elsewhere.

## Healthy Areas

These files already encode why they exist and should stay focused:

- `test_kb_health.py`
- `test_kb_preflight.py`
- `test_conversations.py`
- `test_protocol.py`
- `test_runner.py`
- `test_forges.py`
- `test_dev_reload.py`
- `test_dockerfile.py`

## Residual Candidates

Do not groom these just for symmetry:

- Share `_seed_task` / `_emit` helpers between
  `test_slack_render_update.py` and `test_telegram_render_update.py`
  when another live-progress gate makes the pattern repeat again.
- Backfill older Docker invoke tests onto the existing
  `_build_docker_invoke` helper in `test_envs.py` only if those tests
  are being edited for another reason.
- Leave the PID / re-exec daemon tests alone; the helper ceremony would
  be about as large as the duplication.

## Read Next

- [`repo-dive-in-map.md`](repo-dive-in-map.md) for the current test
  reading path.
- [`kb/log.md`](log.md) for the shipped pass narrative and commit-level
  outcomes.
