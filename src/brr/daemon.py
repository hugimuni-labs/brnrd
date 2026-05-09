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
from . import conversations
from . import envs
from . import gitops
from . import prompts
from . import protocol
from . import run_context
from . import runner
from . import updates
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


def _push_if_needed(
    repo_root: Path,
    *,
    conversation_key: str | None = None,
    task_id: str | None = None,
) -> None:
    """Push to origin if there are unpushed commits.

    When *conversation_key* is provided, emit ``push_started``/
    ``push_done`` update packets so gates can render delivery progress.
    """
    try:
        result = subprocess.run(
            ["git", "log", "@{u}..HEAD", "--oneline"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if not (result.returncode == 0 and result.stdout.strip()):
            return
        commits = [c for c in result.stdout.strip().splitlines() if c.strip()]
        commit_count = len(commits)
        brr_dir = gitops.shared_brr_dir(repo_root)
        push_payload: dict = {"commits": commit_count}
        if task_id:
            push_payload["task_id"] = task_id
        if conversation_key:
            updates.emit(brr_dir, updates.UpdatePacket(
                type="push_started",
                conversation_key=conversation_key,
                payload=push_payload,
            ))
        print("[brr] pushing changes...")
        push = subprocess.run(
            ["git", "push"], cwd=repo_root,
            capture_output=True, text=True, timeout=60,
        )
        if conversation_key:
            done_payload = dict(push_payload)
            done_payload["ok"] = push.returncode == 0
            if push.returncode != 0:
                detail = (push.stderr or push.stdout or "").strip()
                if detail:
                    done_payload["error"] = detail[:500]
            updates.emit(brr_dir, updates.UpdatePacket(
                type="push_done",
                conversation_key=conversation_key,
                payload=done_payload,
            ))
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
    derives the conversation key, and tracks status throughout
    execution. Returns the Task.
    """
    eid = event["id"]
    brr_dir = gitops.shared_brr_dir(repo_root)
    tasks_dir = brr_dir / "tasks"
    runner_name = runner.resolve_runner(repo_root)
    base_branch = gitops.current_branch(repo_root)

    conv_key = conversations.conversation_key_for_event(event) or ""
    if conv_key:
        conversations.append_event(brr_dir, conv_key, event)
        updates.emit(brr_dir, updates.UpdatePacket(
            type="event_received",
            conversation_key=conv_key,
            payload={"event_id": eid, "source": event.get("source", "")},
        ))

    task = Task.from_event(event, cfg)
    task.conversation_key = conv_key
    task.save(tasks_dir)

    updates.emit(brr_dir, updates.UpdatePacket(
        type="task_created",
        conversation_key=conv_key,
        payload={"task_id": task.id, "event_id": eid, "env": task.env},
    ))

    task.update_status("running", tasks_dir)
    resp_path = protocol.response_path(responses_dir, eid)

    print(f"[brr] task {task.id} (event {eid}): env={task.env}")

    task.meta["response_path"] = str(resp_path)
    task.meta["base_branch"] = base_branch

    event_body_for_prompt = event.get("body", "") or ""

    try:
        env_backend = envs.get_env(task.env)
        env_ctx = env_backend.prepare(
            task,
            repo_root,
            cfg,
            base_branch=base_branch,
            response_path=resp_path,
            debug=debug,
        )
    except RuntimeError as e:
        print(f"[brr] task {task.id}: env setup failed: {e}")
        task.update_status("error", tasks_dir)
        updates.emit(brr_dir, updates.UpdatePacket(
            type="failed",
            conversation_key=conv_key,
            payload={"task_id": task.id, "stage": "env", "error": str(e)},
        ))
        return task

    run_root = env_ctx.cwd
    branch_name = env_ctx.branch_name
    if branch_name:
        task.meta["branch_name"] = branch_name

    updates.emit(brr_dir, updates.UpdatePacket(
        type="env_prepared",
        conversation_key=conv_key,
        payload={
            "task_id": task.id,
            "env": task.env,
            "branch_name": branch_name,
        },
    ))

    if conv_key:
        conversations.append_task(
            brr_dir, conv_key,
            task_id=task.id, event_id=eid,
            env=task.env, status=task.status,
            base_branch=base_branch, branch_name=branch_name,
        )

    recent_conversation = (
        conversations.read_recent(brr_dir, conv_key, limit=10)
        if conv_key else []
    )

    context_path = run_context.write_context_file(
        brr_dir,
        task,
        event,
        env_ctx,
        recent_conversation=recent_conversation,
        event_body=event_body_for_prompt,
    )
    task.meta["context_path"] = str(context_path)
    task.save(tasks_dir)

    trace_dirs: list[str] = []
    updates.emit(brr_dir, updates.UpdatePacket(
        type="run_started",
        conversation_key=conv_key,
        payload={"task_id": task.id, "branch": branch_name, "env": task.env},
    ))
    seen_containers: set[str] = set()
    for attempt in range(1, max_retries + 2):
        if attempt == 1:
            prompt = prompts.build_daemon_prompt(
                task.body, eid, str(env_ctx.response_path_env), run_root,
                task_id=task.id,
                branch_name=branch_name,
                base_branch=base_branch,
                runtime_dir=str(env_ctx.runtime_dir),
                context_path=str(context_path),
                recent_conversation=recent_conversation,
                event_body=event_body_for_prompt,
            )
        else:
            prompt = prompts.build_daemon_prompt(
                f"Previous attempt printed no final reply on stdout. "
                f"Print your full response as the final stdout message.\n\n"
                f"Original task: {task.body}",
                eid, str(env_ctx.response_path_env), run_root,
                task_id=task.id,
                branch_name=branch_name,
                base_branch=base_branch,
                runtime_dir=str(env_ctx.runtime_dir),
                context_path=str(context_path),
                recent_conversation=recent_conversation,
                event_body=event_body_for_prompt,
            )

        print(f"[brr] worker {eid}: attempt {attempt}")
        updates.emit(brr_dir, updates.UpdatePacket(
            type="attempt_started",
            conversation_key=conv_key,
            payload={
                "task_id": task.id,
                "event_id": eid,
                "attempt": attempt,
            },
        ))
        result = env_backend.invoke(
            env_ctx,
            runner_name,
            runner.RunnerInvocation(
                kind="daemon-run",
                label=f"{eid}-attempt-{attempt}",
                prompt=prompt,
                cwd=run_root,
                repo_root=repo_root,
                response_path=str(env_ctx.response_path_host),
            ),
            cfg=cfg,
            trace=debug,
        )
        _emit_new_containers(
            brr_dir, conv_key, task.id, env_ctx, seen_containers,
        )
        if result.trace_dir:
            trace_dirs.append(str(result.trace_dir.relative_to(brr_dir)))
        try:
            result.raise_for_error()
        except RuntimeError as e:
            print(f"[brr] worker {eid}: runner error: {e}")

        if result.validation_ok:
            print(f"[brr] worker {eid}: response ready")
            if trace_dirs:
                task.meta["trace_dirs"] = ", ".join(trace_dirs)
            _record_response_artifact(brr_dir, conv_key, task, resp_path)
            task.update_status("done", tasks_dir)
            maintenance_trace = _maybe_kb_maintenance(
                run_root, repo_root, cfg, runner_name, trace=debug,
            )
            if maintenance_trace:
                trace_dirs.append(maintenance_trace)
                task.meta["trace_dirs"] = ", ".join(trace_dirs)
                task.save(tasks_dir)
            updates.emit(brr_dir, updates.UpdatePacket(
                type="finalizing",
                conversation_key=conv_key,
                payload={"task_id": task.id, "stage": "done"},
            ))
            task = env_backend.finalize(
                env_ctx, task, tasks_dir, debug=debug,
            )
            _emit_preserved_containers(brr_dir, conv_key, task)
            preserved_branch = task.meta.get("preserved_branch")
            if task.status == "conflict":
                updates.emit(brr_dir, updates.UpdatePacket(
                    type="conflict",
                    conversation_key=conv_key,
                    payload={
                        "task_id": task.id,
                        "branch": preserved_branch or branch_name,
                    },
                ))
            else:
                updates.emit(brr_dir, updates.UpdatePacket(
                    type="done",
                    conversation_key=conv_key,
                    payload={
                        "task_id": task.id,
                        "event_id": eid,
                        "preserved_branch": preserved_branch,
                    },
                ))
            return task

        retry_reason = result.retry_reason()
        will_retry = bool(retry_reason and attempt <= max_retries)
        updates.emit(brr_dir, updates.UpdatePacket(
            type="attempt_failed",
            conversation_key=conv_key,
            payload={
                "task_id": task.id,
                "event_id": eid,
                "attempt": attempt,
                "reason": retry_reason or "unknown",
                "will_retry": will_retry,
            },
        ))
        if will_retry:
            print(f"[brr] worker {eid}: {retry_reason}, retrying...")
            updates.emit(brr_dir, updates.UpdatePacket(
                type="retrying",
                conversation_key=conv_key,
                payload={
                    "task_id": task.id,
                    "event_id": eid,
                    "attempt": attempt + 1,
                    "reason": retry_reason,
                },
            ))

    print(f"[brr] worker {eid}: gave up after {max_retries + 1} attempts")
    if trace_dirs:
        task.meta["trace_dirs"] = ", ".join(trace_dirs)
    task.update_status("error", tasks_dir)
    updates.emit(brr_dir, updates.UpdatePacket(
        type="failed",
        conversation_key=conv_key,
        payload={"task_id": task.id, "event_id": eid, "stage": "run"},
    ))
    updates.emit(brr_dir, updates.UpdatePacket(
        type="finalizing",
        conversation_key=conv_key,
        payload={"task_id": task.id, "stage": "failed"},
    ))
    task = env_backend.finalize(env_ctx, task, tasks_dir, debug=debug)
    _emit_preserved_containers(brr_dir, conv_key, task)
    return task


def _emit_new_containers(
    brr_dir: Path,
    conversation_key: str,
    task_id: str,
    env_ctx: "envs.RunContext",
    seen: set[str],
) -> None:
    """Emit container_started packets for any newly-launched env containers."""
    state = env_ctx.env_state if isinstance(env_ctx.env_state, dict) else {}
    raw_list = state.get("docker_containers", [])
    if isinstance(raw_list, list):
        candidates = [str(c) for c in raw_list if c]
    elif raw_list:
        candidates = [str(raw_list)]
    else:
        current = state.get("docker_container")
        candidates = [str(current)] if current else []
    for cid in candidates:
        if cid in seen:
            continue
        seen.add(cid)
        updates.emit(brr_dir, updates.UpdatePacket(
            type="container_started",
            conversation_key=conversation_key,
            payload={
                "task_id": task_id,
                "env": env_ctx.name,
                "container": cid,
            },
        ))


def _emit_preserved_containers(
    brr_dir: Path,
    conversation_key: str,
    task: Task,
) -> None:
    """Emit container_preserved when finalize left containers behind."""
    raw = task.meta.get("docker_containers")
    if not raw:
        return
    if isinstance(raw, str):
        containers = [c.strip() for c in raw.split(",") if c.strip()]
    elif isinstance(raw, list):
        containers = [str(c) for c in raw if c]
    else:
        containers = [str(raw)]
    if not containers:
        return
    updates.emit(brr_dir, updates.UpdatePacket(
        type="container_preserved",
        conversation_key=conversation_key,
        payload={
            "task_id": task.id,
            "containers": containers,
        },
    ))


def _record_response_artifact(
    brr_dir: Path,
    conversation_key: str,
    task: Task,
    response_path: Path,
) -> None:
    """Index the response artifact on the conversation log."""
    label = f"response:{task.event_id}" if task.event_id else f"response:{task.id}"
    if conversation_key:
        conversations.append_artifact(
            brr_dir, conversation_key,
            kind="response",
            path=str(response_path),
            task_id=task.id,
            label=label,
        )
    updates.emit(brr_dir, updates.UpdatePacket(
        type="artifact_created",
        conversation_key=conversation_key,
        payload={
            "task_id": task.id,
            "kind": "response",
            "path": str(response_path),
        },
    ))


def _kb_changed(run_root: Path) -> bool:
    """Return True if the task modified any files under kb/."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--", "kb/"],
            cwd=run_root, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "--", "kb/"],
            cwd=run_root, capture_output=True, text=True, timeout=10,
        )
        return bool(untracked.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _maybe_kb_maintenance(
    run_root: Path,
    repo_root: Path,
    cfg: dict,
    runner_name: str,
    *,
    trace: bool = False,
) -> str | None:
    """Run KB maintenance if configured and KB was modified."""
    policy = str(cfg.get("kb_maintenance", "auto")).strip().lower()
    if policy == "never":
        return None
    if policy == "auto" and not _kb_changed(run_root):
        return None

    prompt = prompts.build_kb_maintenance_prompt(run_root)
    if not prompt:
        return None

    print("[brr] running kb maintenance...")
    result = runner.invoke_runner(
        runner_name,
        runner.RunnerInvocation(
            kind="kb-maintenance",
            label="kb-maintenance",
            prompt=prompt,
            cwd=run_root,
            repo_root=repo_root,
        ),
        cfg=cfg,
        trace=trace,
    )
    if result.ok:
        print("[brr] kb maintenance complete")
    else:
        print(f"[brr] kb maintenance failed (non-fatal): exit {result.returncode}")
    if result.trace_dir:
        return str(result.trace_dir.relative_to(gitops.shared_brr_dir(repo_root)))
    return None


# ── Main loop ────────────────────────────────────────────────────────


def start(repo_root: Path, *, debug: bool | None = None) -> None:
    """Run the daemon main loop (blocking, foreground).

    *debug* enables trace persistence and worktree retention.  When
    ``None``, falls back to the ``debug`` key in ``.brr/config``.
    """
    brr_dir = gitops.shared_brr_dir(repo_root)
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

                if task.status == "error":
                    print(f"[brr] task {task.id}: failed")
                elif task.status == "conflict":
                    print(f"[brr] task {task.id}: branch preserved (cannot fast-forward)")

                _push_if_needed(
                    repo_root,
                    conversation_key=task.conversation_key,
                    task_id=task.id,
                )
            else:
                time.sleep(_SCAN_INTERVAL)

            for t in gate_threads:
                if not t.is_alive():
                    print(f"[brr] warning: gate thread {t.name} died")

    finally:
        _clear_pid(brr_dir)
        print("[brr] daemon stopped")
