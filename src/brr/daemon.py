"""Daemon — main loop that scans the inbox, runs workers, pushes results.

The daemon is a single foreground process (``brr up``).  It:

1. Starts configured gate threads (each gate polls its own channel).
2. Scans ``.brr/inbox/`` for pending events on a timer.
3. Spawns workers (runner subprocesses) one at a time (serial v1).
4. Checks for response files after each worker finishes.
5. Retries the runner if no response file was created.
6. Pushes git commits after a worker makes changes.

No cancellation in v1 — the runner runs to completion.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import config as conf
from . import gitops
from . import protocol
from . import runner
from . import worktree
from .task import Task

_SCAN_INTERVAL = 3
_BUILTIN_GATES = ["telegram", "slack", "git_gate"]


# ── PID file ─────────────────────────────────────────────────────────


def _pid_path(brr_dir: Path) -> Path:
    return brr_dir / "daemon.pid"


def _write_pid(brr_dir: Path) -> None:
    _pid_path(brr_dir).write_text(str(os.getpid()) + "\n")


def _clear_pid(brr_dir: Path) -> None:
    _pid_path(brr_dir).unlink(missing_ok=True)


def read_pid(brr_dir: Path) -> int | None:
    """Read the daemon PID, or None if not running."""
    path = _pid_path(brr_dir)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        path.unlink(missing_ok=True)
        return None


def stop(brr_dir: Path) -> bool:
    """Send SIGTERM to the running daemon. Returns True if one was running."""
    pid = read_pid(brr_dir)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        _clear_pid(brr_dir)
        return False


# ── Gate threads ─────────────────────────────────────────────────────


def _start_gates(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> list[threading.Thread]:
    threads = []
    for name in _BUILTIN_GATES:
        try:
            from .gates import import_gate
            mod = import_gate(name)
        except ImportError:
            continue
        if not hasattr(mod, "is_configured") or not mod.is_configured(brr_dir):
            continue
        print(f"[brr] starting gate: {name}")
        t = threading.Thread(
            target=mod.run_loop,
            args=(brr_dir, inbox_dir, responses_dir),
            daemon=True,
            name=f"gate-{name}",
        )
        t.start()
        threads.append(t)
    return threads


# ── Git push ─────────────────────────────────────────────────────────


def _push_if_needed(repo_root: Path) -> None:
    """Push to origin if there are unpushed commits."""
    try:
        result = subprocess.run(
            ["git", "log", "@{u}..HEAD", "--oneline"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            print("[brr] pushing changes...")
            subprocess.run(
                ["git", "push"], cwd=repo_root,
                capture_output=True, text=True, timeout=60,
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


# ── Worker ───────────────────────────────────────────────────────────


def _run_worker(
    event: dict,
    repo_root: Path,
    responses_dir: Path,
    cfg: dict,
    max_retries: int,
    *,
    debug: bool = False,
) -> Task:
    """Run the runner for a single event, with retries.

    Creates a Task from the event, persists it to .brr/tasks/,
    and tracks status throughout execution.  Returns the Task.
    """
    eid = event["id"]
    tasks_dir = repo_root / ".brr" / "tasks"
    runner_name = runner.resolve_runner(repo_root)
    try:
        task = _triage_task(event, repo_root, cfg, runner_name, trace=debug)
    except RuntimeError as e:
        print(f"[brr] task {eid}: triage error: {e}")
        task = Task.from_event(event, cfg)
        task.update_status("error", tasks_dir)
        return task

    task.update_status("running", tasks_dir)
    resp_path = protocol.response_path(responses_dir, eid)

    print(f"[brr] task {task.id} (event {eid}): branch={task.branch} env={task.env}")

    run_root = repo_root
    branch_name = task.resolve_branch_name()
    uses_worktree = task.needs_worktree and branch_name is not None
    if uses_worktree:
        try:
            run_root = worktree.create(
                repo_root,
                task.id,
                branch_name,
                create_branch=not gitops.branch_exists(repo_root, branch_name),
            )
        except RuntimeError as e:
            print(f"[brr] task {task.id}: worktree setup failed: {e}")
            task.update_status("error", tasks_dir)
            return task

    for attempt in range(1, max_retries + 2):
        if attempt == 1:
            prompt = runner.build_daemon_prompt(
                task.body, eid, str(resp_path), run_root,
                task_id=task.id,
                branch_name=branch_name,
                runtime_dir=str(repo_root / ".brr"),
            )
        else:
            prompt = runner.build_daemon_prompt(
                f"Previous attempt did not produce a response file. "
                f"Please complete the task and write your response to {resp_path}.\n\n"
                f"Original task: {task.body}",
                eid, str(resp_path), run_root,
                task_id=task.id,
                branch_name=branch_name,
                runtime_dir=str(repo_root / ".brr"),
            )

        print(f"[brr] worker {eid}: attempt {attempt}")
        result = runner.invoke_runner(
            runner_name,
            runner.RunnerInvocation(
                kind="daemon-run",
                label=f"{eid}-attempt-{attempt}",
                prompt=prompt,
                cwd=run_root,
                repo_root=repo_root,
                response_path=str(resp_path),
                required_artifacts=[
                    runner.RunnerArtifactSpec(resp_path, f"response:{eid}"),
                ],
            ),
            cfg=cfg,
            trace=debug,
        )
        try:
            result.raise_for_error()
        except RuntimeError as e:
            print(f"[brr] worker {eid}: runner error: {e}")

        if result.validation_ok:
            print(f"[brr] worker {eid}: response ready")
            # Check for needs_context status in response
            resp_text = (responses_dir / f"{eid}.md").read_text(encoding="utf-8")
            resp_fm = protocol.parse_frontmatter(resp_text)
            if resp_fm.get("status") == "needs_context":
                task.update_status("needs_context", tasks_dir)
                if uses_worktree and not debug:
                    worktree.remove(repo_root, task.id, branch=branch_name, force=True)
            else:
                task.update_status("done", tasks_dir)
                if uses_worktree:
                    task = _finalize_worktree_task(
                        task, repo_root, tasks_dir, branch_name, keep_worktree=debug,
                    )
            return task

        retry_reason = result.retry_reason()
        if retry_reason and attempt <= max_retries:
            print(f"[brr] worker {eid}: {retry_reason}, retrying...")

    print(f"[brr] worker {eid}: gave up after {max_retries + 1} attempts")
    task.update_status("error", tasks_dir)
    if uses_worktree and not debug:
        worktree.remove(repo_root, task.id, branch=branch_name, force=True)
    return task


def _finalize_worktree_task(
    task: Task,
    repo_root: Path,
    tasks_dir: Path,
    branch_name: str,
    *,
    keep_worktree: bool = False,
) -> Task:
    """Merge or clean up a completed worktree task."""
    if task.branch in ("auto", "task"):
        result = gitops.merge_branch(
            repo_root, branch_name, f"merge {branch_name} for {task.id}",
        )
        if not result.success:
            print(f"[brr] task {task.id}: merge conflict on {branch_name}")
            task.update_status("conflict", tasks_dir)
            return task
        if keep_worktree:
            print(f"[brr] debug: keeping worktree for {task.id}")
        else:
            worktree.remove(repo_root, task.id, branch=branch_name, delete_branch=True, force=True)
        return task

    if keep_worktree:
        print(f"[brr] debug: keeping worktree for {task.id}")
    else:
        worktree.remove(repo_root, task.id, branch=branch_name, force=True)
    return task


def _triage_task(
    event: dict,
    repo_root: Path,
    cfg: dict,
    runner_name: str,
    *,
    trace: bool = False,
) -> Task:
    """Run the triage agent and parse its task output."""
    prompt = runner.build_triage_prompt(event.get("body", ""), event["id"], repo_root)
    result = runner.invoke_runner(
        runner_name,
        runner.RunnerInvocation(
            kind="triage",
            label=event["id"],
            prompt=prompt,
            cwd=repo_root,
            repo_root=repo_root,
        ),
        cfg=cfg,
        trace=trace,
    )
    result.raise_for_error()
    try:
        task = Task.from_triage_output(result.output, event, cfg)
    except ValueError as e:
        raise RuntimeError(f"invalid triage output: {e}") from e

    task.save(repo_root / ".brr" / "tasks")
    return task


# ── Main loop ────────────────────────────────────────────────────────


def start(repo_root: Path, *, debug: bool | None = None) -> None:
    """Run the daemon main loop (blocking, foreground).

    *debug* enables trace persistence and worktree retention.  When
    ``None``, falls back to the ``debug`` key in ``.brr/config``.
    """
    brr_dir = repo_root / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"

    if read_pid(brr_dir):
        raise SystemExit("[brr] daemon already running")
    if not (repo_root / "AGENTS.md").exists():
        raise SystemExit("[brr] run `brr init` first")

    _write_pid(brr_dir)
    running = True

    def _handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    cfg = conf.load_config(repo_root)
    max_retries = int(cfg.get("response_retries", 1))
    debug_mode = debug if debug is not None else bool(cfg.get("debug", False))

    gate_threads = _start_gates(brr_dir, inbox_dir, responses_dir)
    if not gate_threads:
        print("[brr] warning: no gates configured — inbox will only receive events from `brr run` or scripts")

    if debug_mode:
        print("[brr] debug mode enabled (traces + worktree retention)")
    print(f"[brr] daemon started (pid {os.getpid()})")

    try:
        while running:
            events = protocol.list_pending(inbox_dir)
            if events:
                event = events[0]
                eid = event["id"]
                print(f"[brr] processing: {eid}")
                protocol.set_status(event, "processing")

                task = _run_worker(
                    event, repo_root, responses_dir, cfg, max_retries,
                    debug=debug_mode,
                )
                protocol.set_status(event, task.status)

                if task.status == "needs_context":
                    print(f"[brr] task {task.id}: needs more context")
                elif task.status == "error":
                    print(f"[brr] task {task.id}: failed")

                _push_if_needed(repo_root)
            else:
                time.sleep(_SCAN_INTERVAL)

            for t in gate_threads:
                if not t.is_alive():
                    print(f"[brr] warning: gate thread {t.name} died")

    finally:
        _clear_pid(brr_dir)
        print("[brr] daemon stopped")
