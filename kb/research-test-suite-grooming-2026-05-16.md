# Test suite grooming — 2026-05-16

Status: active

Investigation of the `tests/` suite to find bloat, duplication, and
stale tests, and to weigh each against the *test-as-intent* discipline
freshly added to AGENTS.md.

Scope: review only. No deletions or restructuring committed in this
pass — this page is a proposal. The follow-up plan is the prioritised
list of moves at the bottom.

## Current shape

- 29 files, 358 test functions, **406 collected tests** (parametrize
  expands), passing in **~3 s**.
- ~7,970 LOC of tests against ~8,226 LOC of source (≈ 1 : 1).
- Largest sources of mass: `test_daemon.py` (1,303 LOC, 35 tests),
  `test_run_progress.py` (652), `test_envs.py` (637), `test_kb_preflight.py`
  (546), `test_github_gate.py` (515).
- No `tests/conftest.py`. Every helper is inlined per file.

The suite is **not broken** and **not slow**. The case for grooming is
clarity and intent quality, not correctness or wall-clock cost.

## What's healthy — leave alone

These files exemplify the discipline AGENTS.md just adopted; every
test names *why* it exists and would fail on intent drift.

- `test_kb_health.py` — every test docstring states the principle.
- `test_kb_preflight.py` — parametrised, focused, docstringed.
- `test_conversations.py` — narrow scope, intent-named tests.
- `test_protocol.py` — frontmatter / event CRUD, no scaffolding waste.
- `test_runner.py` — clean class structure, parametrised where helpful,
  docstrings on the non-obvious tests.
- `test_forges.py` — heavy parametrize, full URL-family coverage, no
  leftover scaffolding.
- `test_dev_reload.py` — small and focused.
- `test_dockerfile.py` — string-grep on Dockerfile, but each test
  docstring explains *why* the string matters (e.g., "gh is part of
  the runner toolbox"). Acceptable.

## Where the bloat lives

### `test_daemon.py` — 1,303 LOC, the biggest target

Five issues, in priority order.

**1. Inline `StubEnv` duplicated five times across the
`test_kb_maintenance_*` tests** (lines 594–877). Each test redefines
the same 20-line `StubEnv` class with identical `prepare` /
`invoke` / `finalize` shapes. A shared fixture (or a single
helper module) would cut ~300 LOC without losing a single
assertion.

**2. `test_forge_view_url_*` (4 tests, ~80 LOC, lines 1162–1229)
duplicate `test_forges.py` coverage with much heavier setup.** The
daemon-side wrapper `_forge_view_url` only adds: read remote URL via
`gitops.remote_url`, read config via `conf.load_config`, swallow
exceptions. Each of these 4 tests builds a real git repo + sets
remotes + writes `.brr/config` just to retest URL templating that
`test_forges.py` already covers exhaustively with pure-input
parametrization.

Proposal: replace with 2 small tests (no real-git): one that asserts
the wrapper feeds the right inputs to `forges.view_branch_url`, and
one that asserts exceptions return `None`. Cut ~60 LOC, keep the
intent (the wrapper is tolerant).

**3. Several near-identical `_run_worker` happy/sad-path tests share
the same `StubEnv` skeleton.** Specifically:

- `test_run_worker_constructs_task_without_triage` (75)
- `test_run_worker_retries_on_empty_stdout` (152)
- `test_run_worker_calls_sync_before_resolving_branch_plan` (205)
- `test_run_worker_proceeds_when_sync_fails` (266)

Each one inlines a similar `StubEnv` and a fake `invoke`. With a
shared `_stub_env_isolated` helper (already exists at line 43, used
in some tests but not all) plus a parametrize-able `_invoke_*` factory,
this collapses cleanly.

**4. `test_run_worker_constructs_task_without_triage` (line 75) has
the docstring-equivalent intent embedded in the test name** ("after
triage was removed") — fine — but the asserts on line 115–116
(`assert "triage" not in invocations`, `assert invocations ==
["daemon-run"]`) are now redundant with each other and largely
redundant against the module docstring on line 1. Once the inline
`_kb_maintenance_*` cluster is consolidated, the explicit
"no-triage" assertion is also obsolete (no test in the suite expects
triage anywhere). Worth cutting the explicit `not in invocations`
line.

**5. `test_start_*` (4 tests, PID / re-exec lifecycle, lines 332–490)
each stub the same ~7 daemon attributes.** A small fixture that
yields a stubbed daemon would let each test focus on its one
intent. This is style, not bug — keep as-is if the helper is more
ceremony than win. Marginal.

### `test_integration.py` — 111 LOC, 4 tests, **mostly duplicate**

Direct overlap with `test_adopt.py`:

| `test_integration.py`                            | `test_adopt.py` equivalent          |
| ------------------------------------------------ | ----------------------------------- |
| `TestEmptyRepo::test_creates_structure`          | `test_creates_brr_dir`              |
| `TestEmptyRepo::test_gitignore_has_brr`          | `test_gitignore_updated`            |
| `TestNoGitRepo::test_auto_git_init`              | `test_git_init_if_needed`           |

`TestRepoWithExistingAgentsMd::test_runner_still_called` is the only
unique test in the file — but all it asserts is that the runner is
invoked once even when `AGENTS.md` already exists. The behaviour it
checks (`adopt.init_repo` always calls the runner so the agent can
merge or rewrite) is already tested implicitly by every other adopt
test.

Proposal: delete `test_integration.py` outright. The 3 overlapping
tests are weaker versions of their `test_adopt.py` counterparts (use a
heavier mock runner that creates kb/ files), and the 4th is trivial.

### `test_envs.py` — 637 LOC, two small ergonomic moves

**1. Two near-twin tests for the same parameter** (lines 544 and 560):

- `test_docker_invoke_skips_credential_mounts_when_disabled` (config:
  `False`)
- `test_docker_invoke_skips_credential_mounts_when_disabled_string`
  (config: `"false"`)

Parametrize-or-merge. The boolean-vs-string handling is a single
behaviour: config coercion.

**2. The first three or four docker-invoke tests** (lines 177–323)
build their own `_invoke` plumbing instead of using the
`_build_docker_invoke` helper defined later in the file (line 418).
The helper exists; older tests didn't backfill onto it. Either:
backfill the helper everywhere, or accept the unevenness.

The rest of the file is genuinely 21 distinct intents (docker prepare
/ invoke / finalize, worktree finalize, credential mounts, env
passthrough) and each test has a clear docstring. Leave alone.

### Slack vs Telegram render-update tests — 503 LOC combined

Both files share the shape:

- `_seed_task` helper (different chat-id metadata per gate).
- `_save_token` helper (different state shape per gate).
- `_emit` helper (identical).
- A test that asserts the gate posts on `task_created`.
- A test that asserts the gate updates on subsequent packets.
- A test that asserts the gate falls back on update failure.

Proposal: a `tests/_render_update.py` (or `conftest.py`) module
exposing `emit_packet(brr_dir, key, type, **payload)` and a
parametrisable seed task. Cut ~80 LOC across the two files; keep all
intent.

This is **medium-leverage** — not critical, but the gates are likely
to acquire siblings (GitHub gate already has its own large render
suite), and a shared scaffold now keeps each new gate's tests focused
on what's gate-specific.

## Cross-file duplication map

These helpers are inlined in multiple files with identical or
near-identical shapes; perfect conftest candidates.

| Helper                                  | Copies                                                                                                                                                     |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_write_repo_scaffold(repo)`            | `test_daemon.py:13`, `test_daemon_progress_packets.py:16`, `test_daemon_conversations.py:11`                                                               |
| `_make_event(...)`                      | `test_daemon.py:23`, `test_daemon_progress_packets.py:22`, `test_daemon_conversations.py:17`                                                               |
| Stub worktree env                       | `test_daemon.py` (inline ×5+), `test_daemon_progress_packets.py:48` (`_StubWorktreeEnv`)                                                                   |
| `_init_repo(repo)` (git init + commit)  | `test_branching.py:9`, `test_envs.py:104`, `test_gitops.py:15` (returns oid), `test_sync.py:23`, `test_daemon.py:896` (`_init_real_repo`, returns oid)     |
| `_write(path, contents)`                | `test_kb_health.py:8`, `test_kb_preflight.py:8`                                                                                                            |
| `_emit(brr_dir, key, type, **payload)`  | `test_run_progress.py:11`, `test_slack_render_update.py:41`, `test_telegram_render_update.py:40`, `test_daemon_progress_packets.py` (via `updates.emit`)   |

A modest `tests/conftest.py` (or `tests/_helpers.py`) exposing five
to seven fixtures / functions can absorb every entry above without
touching any test's *intent*.

## Stale references

After scanning for removed concepts (triage, streams, workstream):

- **Triage** is named only in places where it's deliberately the
  absence being asserted (`test_run_worker_constructs_task_without_triage`,
  `assert "triage" not in invocations`, etc.). These are *regression*
  tests guarding against the concept silently reappearing. Keep —
  but consolidate. Once the StubEnv duplication is fixed, the
  "triage no longer runs" assertion can move to one canonical place
  and the rest can drop the assertion.
- **Streams** is named in `test_run_progress.py:627`
  (`test_render_text_compact_does_not_inject_conversation_identity`) as
  a one-sentence regression docstring explaining *why* the test
  exists. Genuine intent-encoded test; keep.
- **`docs` / `streams` / `stream` / `eject`** in
  `test_cli.py::test_removed_diagnostic_commands_are_not_public` —
  asserts these are not registered as CLI subcommands. Real removal
  guard; keep.

No tests appear to exercise dead code paths.

## Intent gaps (tests that test "what" not "why")

A handful of tests assert behaviour without encoding intent. With the
AGENTS.md rule fresh, these are the candidates to either strengthen
or drop.

- `test_integration.py::TestRepoWithExistingAgentsMd::test_runner_still_called`
  — asserts `calls == 1`. The intent (the agent gets a chance to
  merge into an existing AGENTS.md) is invisible; the assertion would
  pass even if the runner did nothing. Either drop or strengthen to
  assert what was *passed* to the runner.
- `test_daemon.py::test_run_worker_constructs_task_without_triage`
  asserts a list shape (`invocations == ["daemon-run"]`) but the
  *intent* is "the worker runs the prompt exactly once on the happy
  path." The list-shape assertion would falsely pass if the worker
  mis-labelled the invocation but still ran once. Worth tightening
  to `len(invocations) == 1`.

Beyond these, the suite's intent quality is genuinely good. Most
tests do encode the *why* either in the docstring or the name.

## Recommended moves, by leverage

Order is rough-priority — biggest payoff in clarity per LOC removed.

**High leverage (recommended)**

1. Delete `test_integration.py` (~110 LOC, 4 tests, three are direct
   dupes of `test_adopt.py`, one is trivial). Net: cleaner adopt
   coverage, one fewer file.
2. Extract a small `tests/conftest.py` with `_write_repo_scaffold`,
   `_make_event`, a single `make_worktree_env_stub(invoke_fn=)`
   factory, and `_init_repo` (in both `-> None` and `-> str` flavours
   if needed). Then collapse the daemon-test inline duplicates onto
   these fixtures. Saves ~400 LOC across `test_daemon.py`,
   `test_daemon_progress_packets.py`, and `test_daemon_conversations.py`
   without touching any assertion.
3. Replace the 4 real-git `test_forge_view_url_*` tests in
   `test_daemon.py` with 2 small stub-based tests (or 1 parametrize)
   for the daemon-side wrapper. Saves ~60 LOC; `test_forges.py`
   already covers the URL templating.
4. Parametrise the `disabled` / `"false"` twin in `test_envs.py`.
   Trivial, but reads better.

**Medium leverage (consider)**

5. Share a `_seed_task` / `_emit` helper between `test_slack_render_update.py`
   and `test_telegram_render_update.py`. Pays off if the gate fleet
   keeps growing; otherwise marginal.
6. Backfill the older docker-invoke tests onto the existing
   `_build_docker_invoke` helper in `test_envs.py`. Style, not
   semantics.

**Low leverage / skip**

7. The 4 `test_start_*` PID tests each stub similar attributes; the
   ceremony to share would roughly equal the duplication. Leave.
8. `test_dockerfile.py` is fragile-by-nature (string-grep) but each
   test docstring states intent. Don't refactor.

## Open questions for the operator

- **Delete `test_integration.py`?** It's the cleanest win but it
  *looks* important (the name says "integration"); confirm before
  removing.
- **`tests/conftest.py` vs `tests/_helpers.py`?** conftest gives
  automatic fixture discovery (no imports); `_helpers.py` is more
  explicit. Both work. Recommendation: `conftest.py` for the truly
  shared helpers, `_helpers.py` only if some helpers belong only to
  a subset.
- **Should the daemon-test split (`test_daemon.py` vs
  `_progress_packets` vs `_conversations` vs `_heartbeat`) stay?**
  After consolidation, the four files together would be ~1,200 LOC
  instead of ~2,100; one merged `test_daemon.py` may be readable
  again. Or the split-by-concern (worker / packets / heartbeat /
  conversations) may still be the right shape. Subjective.

## Scope guardrails

- This proposal touches *test code only*. No source changes.
- The suite passes today; every move must keep that true.
- The cuts above don't remove a single distinct intent — only
  duplicates, trivial assertions, and inline scaffolding.
