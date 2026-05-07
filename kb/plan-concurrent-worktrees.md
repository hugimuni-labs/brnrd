# Plan: Multi-Topic Concurrent Task Execution via Git Worktrees

## Revision History

- v1 (2026-04-07): Initial plan — worktree manager, worker pool, daemon v2
- v2 (2026-04-08): Updated to reflect task abstraction, per-task log files,
  execution environment abstraction, needs-context status. See
  plan-branch-modes.md for the unified design.

## Problem

brr currently executes tasks serially (daemon.py, "serial v1"). When multiple
events arrive — potentially across unrelated topics (e.g., "add logging" and
"fix auth bug") — they queue and run one at a time. This wastes time since
unrelated tasks don't conflict.

## Core Idea

Use `git worktree add` to give each concurrent task its own isolated working
copy. Each worker runs in its own worktree on a task-specific branch. When done,
the daemon merges results back to the main branch.

## Architecture

```
                          ┌─────────────┐
                          │   daemon    │
                          │  main loop  │
                          └──────┬──────┘
                                 │ scans inbox, creates tasks
                    ┌────────────┼────────────────┐
                    ▼            ▼                 ▼
              ┌──────────┐ ┌──────────┐     ┌──────────┐
              │ worker-1 │ │ worker-2 │ ... │ worker-N │
              │ env: wt  │ │ env: wt  │     │ env: dock│
              │ branch-1 │ │ branch-2 │     │ branch-N │
              └─────┬────┘ └─────┬────┘     └─────┬────┘
                    │            │                 │
                    ▼            ▼                 ▼
              ┌──────────────────────────────────────────┐
              │         merge coordinator                │
              │  sequential merge back to main branch    │
              │  + log squash (kb/log-*.md → log.md)     │
              └──────────────────────────────────────────┘
```

## Implementation Plan

### Phase 1: Task Abstraction (`src/brr/task.py`) — new module

See plan-branch-modes.md § "Phase 1: Task Abstraction" for the Task dataclass.
The task sits between event and execution — it carries branch strategy, env
type, and status (including `needs_context`).

### Phase 2: Worktree Manager (`src/brr/worktree.py`) — new module

Encapsulate all git-worktree operations. Pure stdlib, wraps `git` CLI.

**Functions:**

```python
def create(repo_root: Path, task_id: str, branch: str,
           create_branch: bool = True) -> Path:
    """Create a worktree at .brr/worktrees/<task_id>.
    
    If create_branch=True:
        git worktree add .brr/worktrees/<task_id> -b <branch> HEAD
    Else (existing branch):
        git worktree add .brr/worktrees/<task_id> <branch>
    
    Returns the worktree path.
    """

def remove(repo_root: Path, task_id: str) -> None:
    """Remove a worktree and optionally delete its branch."""

def merge_back(repo_root: Path, task_id: str, branch: str) -> MergeResult:
    """Merge the worktree branch back to the current branch.
    
    1. In main repo: git merge <branch> --no-ff -m "..."
    2. On conflict: abort merge, return MergeResult with conflict info.
    3. On success: squash per-task log files, remove worktree + branch.
    Returns MergeResult(success, conflicts, commit_sha).
    """

def list_active(repo_root: Path) -> list[WorktreeInfo]:
    """List active brr worktrees. Parses `git worktree list --porcelain`."""

def cleanup_stale(repo_root: Path) -> int:
    """Remove worktrees whose tasks are done. Returns count."""
```

**Key decisions:**
- Worktrees live under `.brr/worktrees/` (already gitignored via `.brr/`).
- Branch naming: determined by task spec (see plan-branch-modes.md).
- Each worktree shares the same `.git` — object store is shared, disk is cheap.
- Branch creation is supported — if the target branch doesn't exist, create it.

### Phase 3: Per-Task Log Files

Instead of agents writing to `kb/log.md` (merge conflict magnet), each agent
writes to `kb/log-<task-id>.md`. The orchestrator squashes these into
`kb/log.md` after merge.

```python
def squash_logs(repo_root: Path) -> int:
    """Merge all kb/log-*.md files into kb/log.md, delete originals.
    
    Returns count of entries merged.
    """
    log_path = repo_root / "kb" / "log.md"
    main_log = log_path.read_text()
    count = 0
    for f in sorted((repo_root / "kb").glob("log-*.md")):
        entries = f.read_text().strip()
        if entries:
            main_log += "\n\n" + entries
            count += 1
        f.unlink()
    log_path.write_text(main_log)
    return count
```

The prompt tells the agent:
```
Write your log entry to kb/log-<task-id>.md instead of kb/log.md.
Use the same format as kb/log.md entries.
```

### Phase 4: Worker Pool (`src/brr/pool.py`) — new module

Manages concurrent worker threads, each pinned to a worktree.

```python
@dataclass
class WorkerSlot:
    task: Task
    worktree_path: Path
    thread: threading.Thread
    started: float

class WorkerPool:
    def __init__(self, repo_root: Path, max_workers: int = 4):
        self._slots: dict[str, WorkerSlot] = {}   # task_id -> slot
        self._merge_lock = threading.Lock()        # serialize merges
    
    def submit(self, task: Task, cfg: dict) -> bool:
        """Set up environment and start a worker thread for the task."""
    
    def poll_completed(self) -> list[WorkerSlot]:
        """Return slots where the worker thread has finished."""
    
    def merge_completed(self) -> list[MergeResult]:
        """Merge all completed tasks, squash logs, clean up."""
    
    @property
    def active_count(self) -> int: ...
    
    @property
    def has_capacity(self) -> bool: ...
    
    def shutdown(self, timeout: float = 30) -> None:
        """Wait for all workers, merge, clean up."""
```

**Concurrency rules:**
- Worker threads run independently (no shared mutable state except their slot).
- Merges are serialized via `_merge_lock` — only one merge at a time.
- Per-branch locking for existing branches (two tasks on same branch = serial).
- `max_workers` is configurable via `.brr/config` (`max_workers=4`).

### Phase 5: Daemon v2 — modify `daemon.py`

Replace the serial loop with pool-based dispatch.

```python
# Current (serial v1):
events = protocol.list_pending(inbox_dir)
if events:
    event = events[0]
    _run_worker(event, ...)

# New (concurrent v2):
events = protocol.list_pending(inbox_dir)
for event in events:
    if pool.has_capacity:
        task = Task.from_event(event, cfg)
        protocol.set_status(event, "processing")
        pool.submit(task, cfg)

# After dispatch, check for completions:
for result in pool.merge_completed():
    if result.task.status == "needs_context":
        _notify_needs_context(result)
    protocol.set_status(result.event, result.task.status)
    _push_if_needed(repo_root)
```

### Phase 6: Prompt Assembly & Execution Context

See plan-branch-modes.md § "Prompt Assembly" and "Execution Context in Prompt".

Key additions to the prompt:
- Execution environment (worktree/docker/local)
- Branch ownership and naming
- Per-task log file path
- Explicit "need more context" permission
- Concurrency awareness

### Phase 7: Execution Environment Abstraction

```python
class ExecutionEnvironment(Protocol):
    def setup(self, task: Task, repo_root: Path) -> Path:
        """Set up the environment, return working directory."""
    
    def teardown(self, task: Task) -> None:
        """Clean up after task completion."""
    
    def collect_artifacts(self, task: Task) -> dict:
        """Collect response, log files, etc. from the environment."""

class LocalEnv: ...      # No-op setup, run in repo root
class WorktreeEnv: ...   # git worktree add/remove
class DockerEnv: ...     # Future: container lifecycle
```

Docker support is future work but the abstraction should be in place.
For Docker, the per-task log file approach works well: mount or copy out
`kb/log-<task-id>.md` from the container after execution.

## Edge Cases & Failure Modes

| Scenario | Handling |
|----------|----------|
| Merge conflict | Abort merge, mark task as `conflict`, log details. Branch preserved. |
| Worker crashes/times out | Clean up env, mark task `error`. Retry per config. |
| Agent needs more context | Mark task `needs_context`, notify user via gate. Not a retry. |
| Daemon killed mid-work | On restart, `cleanup_stale()` handles orphans. Tasks in `running` re-evaluated. |
| Same file edited by two workers | Caught at merge time. Second merge fails. |
| Log squash during active workers | Only squash logs from completed+merged tasks. Active tasks' logs untouched. |
| Branch doesn't exist for `auto` | Create it. This is a supported flow. |
| Two tasks on same existing branch | Serialize via per-branch lock in pool. |

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `src/brr/task.py` | **Create** | Task model (event→task, branch strategy, status) |
| `src/brr/worktree.py` | **Create** | Git worktree lifecycle management |
| `src/brr/pool.py` | **Create** | Concurrent worker pool with merge coordinator |
| `src/brr/env.py` | **Create** | Execution environment abstraction |
| `src/brr/daemon.py` | **Modify** | Replace serial loop with pool dispatch |
| `src/brr/runner.py` | **Modify** | Task-aware prompt building, per-task log paths |
| `src/brr/gitops.py` | **Modify** | Add merge/branch helpers |
| `src/brr/status.py` | **Modify** | Show task/pool/worktree status |
| `prompts/run.md` | **Modify** | Add "need context" permission |
| `prompts/run-worktree.md` | **Create** | Worktree mode overrides |
| `prompts/run-branch.md` | **Create** | Branch mode overrides |
| `tests/test_task.py` | **Create** | Task model tests |
| `tests/test_worktree.py` | **Create** | Worktree manager tests |
| `tests/test_pool.py` | **Create** | Worker pool tests |

## Implementation Order

1. **`task.py`** — foundation, no dependencies
2. **`worktree.py`** — depends on task model, testable in isolation
3. **Per-task log squash** — small, can be in worktree.py or separate
4. **`env.py`** — environment abstraction, LocalEnv + WorktreeEnv
5. **`pool.py`** — depends on task + worktree + env
6. **`daemon.py` changes** — integration, swap serial for pool
7. **Prompt updates** — mode-specific templates, context injection
8. **`status.py` updates** — cosmetic
9. **Tests** — throughout, but especially after steps 1, 2, and 5

## Open Questions

1. **Should `brr run` support task creation?** Currently it runs inline.
   Could create a task (with branch/env settings) for local concurrent runs.

2. **Branch cleanup policy:** Auto-delete merged branches? Keep conflict
   branches for N days? Configurable.

3. **Docker specifics:** Mount strategy, image selection, resource limits.
   Deferred to when Docker support is implemented.

4. ~~**Task persistence:** Should tasks be files in `.brr/tasks/`?~~
   **Resolved (2026-04-08):** Yes. Implemented in `src/brr/task.py`.
   Events were already persisted as files — Task extends this pattern
   to the event→task→execution pipeline.

5. **Agent branch override:** In `auto` mode, how does the agent communicate
   its chosen branch name? Response frontmatter is cleanest.

6. **Log squash agent:** For complex merges (concurrent runs that both
   touch overlapping topics), should an agent do the log merge? The
   structured format should make automated squash sufficient for most
   cases, but having an agent-assisted merge phase as a fallback is worth
   considering for the concurrent runner use case.
