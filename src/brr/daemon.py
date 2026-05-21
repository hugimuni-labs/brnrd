"""Daemon — main loop that scans the inbox, runs workers, pushes results.

The daemon is a single foreground process (``brr up``).  It:

1. Starts configured gate threads (each gate polls its own channel).
2. Scans ``.brr/inbox/`` for pending events on a timer.
3. Dispatches pending events into a bounded worker pool.
4. Each worker owns the full pipeline for its event: runner invocation,
   retries, response capture, response release to gates, kb maintenance,
   env finalize, and branch push.
5. Workers don't share mutable state — conversation logs and gate
   progress cards are partitioned per event / per task. The only
   resources that need synchronisation are git refs at fast-forward and
   push, which take per-branch locks. See
   ``kb/design-concurrent-execution.md`` for the full contract.

No cancellation in v1 — runners run to completion. ``brr down`` /
SIGTERM flip the loop flag; the dispatch loop stops accepting new
events and the pool drains before the process exits.
"""

from __future__ import annotations

import collections
import concurrent.futures
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import branching
from . import config as conf
from . import conversations
from . import dev_reload as reload_mod
from . import envs
from . import forges
from . import gitops
from . import kb_health
from . import kb_preflight
from . import prompts
from . import protocol
from . import run_context
from . import runner
from . import sync
from . import updates
from .task import Task

_SCAN_INTERVAL = 3
_BUILTIN_GATES = ["telegram", "slack", "github"]
# Cadence for the run-time heartbeat packet. 30s is short enough that
# the chat card visibly bumps elapsed time during the long "running"
# phase, and far below Telegram's edit rate ceiling (~30/sec/chat).
_HEARTBEAT_INTERVAL = 30.0
# Default worker pool size. Four parallel tasks cover the usual burst
# (several channels or follow-ups while longer work is in flight) without
# most adopters needing to tune config. Forge API quota and runner
# subscription limits still apply — set ``max_workers=1`` for strictly
# serial behaviour, or lower/raise via ``.brr/config`` as needed.
_DEFAULT_MAX_WORKERS = 4
# How long to wait for in-flight workers to drain on shutdown. None
# means "wait forever"; the loop only exits the pool join when every
# worker is done. A long-running task killed mid-flight by an external
# signal still leaves trace dirs and the response file for forensics.
_SHUTDOWN_DRAIN_TIMEOUT: float | None = None
# Extra rows pulled from the conversation log on top of what the prompt
# actually renders. Absorbs the in-flight event + task records (and any
# pre-runner update packets for the same event) that
# ``_recent_conversation_for_prompt`` strips before formatting, so the
# rendered tail stays at ``prompts.RECENT_CONVERSATION_MAX``.
_RECENT_READ_HEADROOM = 12


# ── Per-branch locks ────────────────────────────────────────────────


# A keyed lock for git operations that genuinely share a resource:
# fast-forwarding into an auto-land target, and pushing a branch. Two
# workers acting on the same branch serialise on the same lock; two
# workers acting on different branches never contend. The map itself
# is guarded by a tiny lock so two concurrent first-uses of the same
# branch see the same Lock instance.
_BRANCH_LOCKS: dict[str, threading.Lock] = collections.defaultdict(threading.Lock)
_BRANCH_LOCKS_GUARD = threading.Lock()


def _branch_lock(name: str | None) -> threading.Lock:
    """Return the per-branch lock for *name*, creating it on first use."""
    if not name:
        # Unknown branch — return a fresh anonymous lock so the caller
        # still gets a context manager but doesn't serialise anything.
        return threading.Lock()
    with _BRANCH_LOCKS_GUARD:
        return _BRANCH_LOCKS[name]


# ── Packet emitter for one worker ───────────────────────────────────


@dataclass
class _WorkerEmit:
    """Closure-like emitter that carries (brr_dir, conv_key, event_id).

    Every packet from one worker shares these three values, so threading
    them through every emit call individually is just noise. Callers
    use ``emit("packet_type", **payload)`` — the conversation_key and
    event_id rides on the packet automatically so it lands in the
    right per-event jsonl file.
    """

    brr_dir: Path
    conversation_key: str
    event_id: str

    def __call__(self, packet_type: str, **payload: object) -> None:
        updates.emit(self.brr_dir, updates.UpdatePacket(
            type=packet_type,
            conversation_key=self.conversation_key,
            event_id=self.event_id,
            payload=payload,
        ))


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


# ── Git publish ──────────────────────────────────────────────────────


def publish(
    repo_root: Path,
    task: Task,
) -> None:
    """Publish the task's branch to its remote, if there are commits.

    The publish kernel:

    - The agent leaves work on a branch. ``task.meta["publish_branch"]``
      names it (set by ``WorktreeEnv.finalize``).
    - When the event named an ``expected_publish_branch`` and the
      agent stayed on the task branch ``brr/<task-id>``, publish to
      the expected branch via a refspec
      (``git push origin brr/<task-id>:refs/heads/<expected>``) so
      the daemon never has to update the local target ref.
    - When the resulting source ref equals the expected publish
      branch AND ``task.meta["expected_remote_oid"]`` is set AND the
      local source is not an ancestor of the remote target, push with
      ``--force-with-lease`` anchored to ``expected_remote_oid``. This
      is the PR-rebase case.
    - Otherwise plain push, with ``-u`` when the local branch has no
      upstream.

    A failed push flips ``publish_status`` to ``conflict`` and emits
    the ``conflict`` packet so gates render the delivery failure
    rather than reporting success.
    """
    push_branch = task.meta.get("publish_branch")
    if not push_branch:
        return

    brr_dir = gitops.shared_brr_dir(repo_root)
    emit = _WorkerEmit(
        brr_dir, task.conversation_key or "", task.event_id or "",
    )

    expected = task.meta.get("expected_publish_branch") or None
    expected_remote_oid = task.meta.get("expected_remote_oid") or None
    # Refspec push: agent kept the task branch (``brr/<task-id>``)
    # but the event named a different ``expected`` to publish under.
    # Push the local source to the named remote ref without touching
    # the local target ref first.
    remote_branch = expected if expected and expected != push_branch else push_branch

    try:
        upstream = gitops.branch_upstream(repo_root, push_branch)
        remote = gitops.branch_remote(repo_root, push_branch)
        set_upstream = False
        force_with_lease = False

        if upstream and remote_branch == push_branch:
            commits = _commits_between(repo_root, upstream, push_branch)
            if not commits:
                return
            if not remote:
                remote = upstream.split("/", 1)[0] if "/" in upstream else None
        else:
            remote = remote or gitops.default_remote(repo_root)
            if not remote:
                return
            remote_ref = f"{remote}/{remote_branch}"
            if gitops.rev_parse(repo_root, remote_ref):
                commits = _commits_between(repo_root, remote_ref, push_branch)
            else:
                commits = _commits_since_seed(repo_root, push_branch)
            if not commits:
                return
            # Set upstream only when pushing to a matching-named branch
            # for the first time; refspec pushes don't carry an
            # upstream because the local source name doesn't match the
            # remote target. Mirrors how a user would publish a new
            # branch with ``git push -u``.
            if remote_branch == push_branch:
                set_upstream = True

        if not remote:
            return

        # Lease decision: force-with-lease only when the local source
        # ref has rewritten history relative to the remote target and
        # the daemon captured the remote oid at task start.
        if (
            expected_remote_oid
            and remote_branch == expected
            and not gitops.is_ancestor(
                repo_root, f"{remote}/{remote_branch}", push_branch,
            )
        ):
            force_with_lease = True

        push_cmd = _push_command(
            remote,
            push_branch,
            remote_branch,
            set_upstream=set_upstream,
            lease_oid=(expected_remote_oid if force_with_lease else None),
        )
        push_payload: dict = {
            "commits": len(commits),
            "branch": push_branch,
            "set_upstream": set_upstream,
            "force_with_lease": force_with_lease,
            "task_id": task.id,
        }
        if task.conversation_key:
            emit("push_started", **push_payload)
        print(f"[brr] pushing {push_branch}...")
        with _branch_lock(remote_branch):
            push = subprocess.run(
                push_cmd, cwd=repo_root,
                capture_output=True, text=True, timeout=60,
            )
        if task.conversation_key:
            done_payload = dict(push_payload)
            done_payload["ok"] = push.returncode == 0
            if push.returncode == 0:
                view = _forge_view_url(repo_root, remote, remote_branch)
                if view:
                    done_payload["view_url"] = view
            else:
                detail = (push.stderr or push.stdout or "").strip()
                if detail:
                    done_payload["error"] = detail[:500]
            emit("push_done", **done_payload)
        if push.returncode != 0:
            task.meta["publish_status"] = "conflict"
            if task.conversation_key:
                emit(
                    "conflict",
                    task_id=task.id,
                    branch=push_branch,
                    publish_branch=push_branch,
                )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def _push_command(
    remote: str,
    source: str,
    remote_branch: str,
    *,
    set_upstream: bool,
    lease_oid: str | None,
) -> list[str]:
    """Build ``git push`` argv for the publish step.

    Three mutually exclusive arms:

    - ``lease_oid`` set → ``--force-with-lease`` push with explicit
      refspec ``<source>:refs/heads/<remote_branch>``.
    - ``set_upstream`` set → ``git push -u <remote> <source>``; only
      reachable when ``source == remote_branch``.
    - otherwise → ``git push <remote> <source>:<remote_branch>`` (or
      plain ``<source>`` when names match).
    """
    cmd = ["git", "push"]
    if lease_oid:
        remote_ref = f"refs/heads/{remote_branch}"
        cmd.append(f"--force-with-lease={remote_ref}:{lease_oid}")
        cmd.extend([remote, f"{source}:{remote_ref}"])
        return cmd
    if set_upstream:
        cmd.append("-u")
    if source == remote_branch:
        cmd.extend([remote, source])
    else:
        cmd.extend([remote, f"{source}:{remote_branch}"])
    return cmd


def _forge_view_url(
    repo_root: Path, remote: str, branch: str,
) -> str | None:
    """Compute the forge "view branch" URL or ``None`` when not derivable.

    The path is intentionally tolerant: any failure (missing remote,
    unparseable URL, unknown forge, missing config) returns ``None``
    and the caller emits a packet without the link. The push has
    already succeeded by the time we're here; a missing link is never
    worth failing the task over.
    """
    try:
        url = gitops.remote_url(repo_root, remote)
        if not url:
            return None
        cfg = conf.load_config(repo_root)
        return forges.view_branch_url(
            url,
            branch,
            override_kind=cfg.get("forge.kind") or None,
            override_url_base=cfg.get("forge.url_base") or None,
        )
    except Exception:
        return None


def _commits_between(repo_root: Path, base_ref: str, branch: str) -> list[str]:
    result = subprocess.run(
        ["git", "log", f"{base_ref}..{branch}", "--oneline"],
        cwd=repo_root, capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return []
    return [c for c in result.stdout.splitlines() if c.strip()]


def _commits_since_seed(repo_root: Path, branch: str) -> list[str]:
    seed = gitops.default_branch(repo_root) or "HEAD"
    merge_base = subprocess.run(
        ["git", "merge-base", seed, branch],
        cwd=repo_root, capture_output=True, text=True, timeout=10,
    )
    if merge_base.returncode == 0 and merge_base.stdout.strip():
        return _commits_between(repo_root, merge_base.stdout.strip(), branch)
    result = subprocess.run(
        ["git", "log", branch, "--oneline"],
        cwd=repo_root, capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return []
    return [c for c in result.stdout.splitlines() if c.strip()]


def _cleanup_traces_on_success(
    brr_dir: Path, tasks_dir: Path, task: Task,
) -> None:
    """Remove every trace dir the task accumulated on a clean ``done``.

    Symmetric with worktree and container cleanup: when the task
    finished cleanly, the durable artefacts (git commits, response
    file, kb updates) capture everything that matters. Traces only
    earn their disk footprint on ``error`` / ``conflict``, where the
    captured prompt/stdout/stderr is the only forensic handle left.
    """
    if task.status != "done":
        return
    raw = task.meta.get("trace_dirs", "")
    if not raw:
        return
    for rel in (item.strip() for item in raw.split(",") if item.strip()):
        path = brr_dir / rel
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    task.meta.pop("trace_dirs", None)
    task.save(tasks_dir)


# ── Sync hook helpers ────────────────────────────────────────────────


def _branches_to_refresh(repo_root: Path, event: dict) -> list[str]:
    """Return local branch names worth refreshing before this task.

    Always includes the local default branch (the typical seed for new
    work). Adds any structured event branch fields the daemon already
    knows about (``branch_target``, ``target_branch``, ``base_branch``,
    ``branch``), filtered through the same validation
    ``branching.resolve_publish_plan`` uses so we don't hand the sync
    layer "auto" / "current" sentinels.
    """
    out: list[str] = []
    default = gitops.default_branch(repo_root)
    if default and default != "HEAD":
        out.append(default)

    for key in branching.STRUCTURED_BRANCH_KEYS:
        candidate = branching._event_branch_candidate(
            repo_root, key, event.get(key),
        )
        if candidate and candidate not in out:
            out.append(candidate)
    return out


# ── Worker ───────────────────────────────────────────────────────────


def _run_worker(
    event: dict,
    repo_root: Path,
    responses_dir: Path,
    cfg: dict,
    max_retries: int,
) -> Task:
    """Run the runner for a single event, with retries.

    Creates a Task from the event, persists it to .brr/tasks/,
    derives the conversation key, and tracks status throughout
    execution. Returns the Task.

    Every update packet rides through the local ``emit`` closure so
    ``conversation_key`` and ``event_id`` are populated automatically.
    Per-event-pipeline conversation routing relies on that — see
    ``kb/design-concurrent-execution.md``.
    """
    eid = event["id"]
    brr_dir = gitops.shared_brr_dir(repo_root)
    tasks_dir = brr_dir / "tasks"
    runner_name = runner.resolve_runner(repo_root)

    conv_key = conversations.conversation_key_for_event(event) or ""
    emit = _WorkerEmit(brr_dir, conv_key, eid)

    # Refresh local refs before resolving the branch plan so the task
    # seeds from a current view of the world. Computing target_branches
    # off the raw event (rather than the resolved plan) avoids a chicken-
    # and-egg loop and lets a future github-gate event for a PR comment
    # name its head branch via ``branch_target`` for free.
    sync_targets = _branches_to_refresh(repo_root, event)
    sync_result = sync.refresh_before_task(
        repo_root, target_branches=sync_targets, cfg=cfg,
    )

    branch_plan = branching.resolve_publish_plan(repo_root, event, cfg)

    if conv_key:
        conversations.append_event(brr_dir, conv_key, event)
        emit("event_received", event_id=eid, source=event.get("source", ""))
        sync_summary = sync.render_summary(sync_result)
        if sync_summary or sync_result.error:
            emit(
                "synced",
                event_id=eid,
                summary=sync_summary,
                ff_branches=dict(sync_result.ff_branches),
                skipped=dict(sync_result.skipped),
                error=sync_result.error,
            )

    task = Task.from_event(event, cfg)
    task.conversation_key = conv_key
    task.save(tasks_dir)

    emit("task_created", task_id=task.id, event_id=eid, env=task.env)

    task.update_status("running", tasks_dir)
    resp_path = protocol.response_path(responses_dir, eid)

    print(f"[brr] task {task.id} (event {eid}): env={task.env}")

    task.meta["response_path"] = str(resp_path)
    task.meta.update(branch_plan.meta_items())

    event_body_for_prompt = event.get("body", "") or ""

    try:
        env_backend = envs.get_env(task.env)
        env_ctx = env_backend.prepare(
            task,
            repo_root,
            cfg,
            branch_plan=branch_plan,
            response_path=resp_path,
        )
    except RuntimeError as e:
        print(f"[brr] task {task.id}: env setup failed: {e}")
        task.update_status("error", tasks_dir)
        emit("failed", task_id=task.id, stage="env", error=str(e))
        return task

    run_root = env_ctx.cwd
    branch_name = env_ctx.branch_name
    if branch_name:
        task.meta["branch_name"] = branch_name

    # Pin the OID the task branch sprouted from so the post-task
    # maintenance pass can ask "which kb / AGENTS.md pages did the
    # preceding work touch?" — that's the concrete review target the
    # maintenance agent needs to spot historical-narrative leakage on
    # pages the task edited. Resolving the seed ref against the run
    # root catches the case where the host's view of the ref has
    # since moved.
    task_pre_head = (
        gitops.rev_parse(run_root, branch_plan.seed_ref)
        if branch_plan.seed_ref
        else None
    )

    emit(
        "env_prepared",
        task_id=task.id,
        env=task.env,
        branch_name=branch_name,
        seed_ref=branch_plan.seed_ref,
        expected_publish_branch=branch_plan.expected_publish_branch,
        branch_source=branch_plan.source,
    )

    if conv_key:
        conversations.append_task(
            brr_dir, conv_key,
            task_id=task.id, event_id=eid,
            env=task.env, status=task.status,
            branch_name=branch_name,
            seed_ref=branch_plan.seed_ref,
            expected_publish_branch=branch_plan.expected_publish_branch,
            branch_source=branch_plan.source,
            host_context_branch=branch_plan.host_context_branch,
        )

    # Read a window larger than the prompt renders so the in-flight
    # event/task records (stripped by _recent_conversation_for_prompt)
    # don't shrink the tail below RECENT_CONVERSATION_MAX.
    recent_read_limit = prompts.RECENT_CONVERSATION_MAX + _RECENT_READ_HEADROOM
    recent_conversation = (
        _recent_conversation_for_prompt(
            conversations.read_recent(brr_dir, conv_key, limit=recent_read_limit),
            event_id=eid,
            task_id=task.id,
        )
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
    emit(
        "run_started",
        task_id=task.id,
        branch=branch_name,
        seed_ref=branch_plan.seed_ref,
        expected_publish_branch=branch_plan.expected_publish_branch,
        env=task.env,
        runner=runner_name,
    )
    seen_containers: set[str] = set()
    last_failure: dict[str, object] | None = None
    for attempt in range(1, max_retries + 2):
        if attempt == 1:
            prompt = prompts.build_daemon_prompt(
                task.body, eid, str(env_ctx.response_path_env), run_root,
                task_id=task.id,
                source=task.source or event.get("source"),
                environment=task.env,
                branch_name=branch_name,
                seed_ref=branch_plan.seed_ref,
                expected_publish_branch=branch_plan.expected_publish_branch,
                branch_source=branch_plan.source,
                host_context_branch=branch_plan.host_context_branch,
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
                source=task.source or event.get("source"),
                environment=task.env,
                branch_name=branch_name,
                seed_ref=branch_plan.seed_ref,
                expected_publish_branch=branch_plan.expected_publish_branch,
                branch_source=branch_plan.source,
                host_context_branch=branch_plan.host_context_branch,
                runtime_dir=str(env_ctx.runtime_dir),
                context_path=str(context_path),
                recent_conversation=recent_conversation,
                event_body=event_body_for_prompt,
            )

        print(f"[brr] worker {eid}: attempt {attempt}")
        emit("attempt_started", task_id=task.id, event_id=eid, attempt=attempt)

        attempt_started_monotonic = time.monotonic()

        def _emit_heartbeat() -> None:
            elapsed = int(time.monotonic() - attempt_started_monotonic)
            emit(
                "heartbeat",
                task_id=task.id,
                attempt=attempt,
                elapsed_seconds=elapsed,
            )
            _emit_new_containers(emit, task.id, env_ctx, seen_containers)

        result = _invoke_with_heartbeat(
            env_backend,
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
            trace=True,
            on_heartbeat=_emit_heartbeat,
        )
        _emit_new_containers(emit, task.id, env_ctx, seen_containers)
        if result.trace_dir:
            trace_dirs.append(str(result.trace_dir.relative_to(brr_dir)))
        try:
            result.raise_for_error()
        except RuntimeError as e:
            print(f"[brr] worker {eid}: runner error: {e}")
            last_failure = {
                "exit_code": result.returncode,
                "error": result.error_detail() or str(e),
                "timed_out": result.returncode == 124,
            }
        else:
            if not result.validation_ok and not result.retry_reason():
                detail = result.error_detail()
                if detail:
                    last_failure = {
                        "exit_code": result.returncode,
                        "error": detail,
                        "timed_out": False,
                    }

        if result.validation_ok:
            print(f"[brr] worker {eid}: response ready")
            if trace_dirs:
                task.meta["trace_dirs"] = ", ".join(trace_dirs)
            _record_response_artifact(emit, task, resp_path)
            task.update_status("done", tasks_dir)
            _set_event_status_if_present(event, "done")
            maintenance_trace = _maybe_kb_maintenance(
                run_root, repo_root, cfg, runner_name,
                emit=emit,
                task_id=task.id,
                task_pre_head=task_pre_head,
                trace=True,
            )
            if maintenance_trace:
                trace_dirs.append(maintenance_trace)
                task.meta["trace_dirs"] = ", ".join(trace_dirs)
                task.save(tasks_dir)
            emit("finalizing", task_id=task.id, stage="done")
            # Per-branch lock around finalize: if this task's expected
            # publish branch overlaps another concurrent worker's, the
            # publish step must serialise on that name. Tasks
            # targeting different branches don't contend.
            with _branch_lock(branch_plan.expected_publish_branch):
                task = env_backend.finalize(env_ctx, task, tasks_dir)
            _cleanup_traces_on_success(brr_dir, tasks_dir, task)
            _emit_preserved_containers(emit, task)
            emit(
                "done",
                task_id=task.id,
                event_id=eid,
                publish_branch=task.meta.get("publish_branch"),
                publish_status=task.meta.get("publish_status"),
            )
            return task

        retry_reason = result.retry_reason()
        will_retry = bool(retry_reason and attempt <= max_retries)
        attempt_payload: dict[str, object] = {
            "task_id": task.id,
            "event_id": eid,
            "attempt": attempt,
            "reason": retry_reason or (
                last_failure.get("error") if last_failure else None
            ) or "unknown",
            "will_retry": will_retry,
        }
        if last_failure and not retry_reason:
            attempt_payload["exit_code"] = last_failure["exit_code"]
            if last_failure.get("timed_out"):
                attempt_payload["timed_out"] = True
        emit("attempt_failed", **attempt_payload)
        if will_retry:
            print(f"[brr] worker {eid}: {retry_reason}, retrying...")
            emit(
                "retrying",
                task_id=task.id,
                event_id=eid,
                attempt=attempt + 1,
                reason=retry_reason,
            )
            continue
        # Hard failure (timeout / non-zero exit) — no retry, give up now
        # rather than burning another expensive attempt. The give-up
        # branch below carries the captured error up to the gate.
        break

    if last_failure and last_failure.get("timed_out"):
        print(f"[brr] worker {eid}: timed out, giving up")
    else:
        print(f"[brr] worker {eid}: gave up after {attempt} attempt(s)")
    if trace_dirs:
        task.meta["trace_dirs"] = ", ".join(trace_dirs)
    task.update_status("error", tasks_dir)
    # finalize first so any preserved branches / containers are recorded
    # on the task before the failure packet renders — the failure packet
    # is what gates see last, so its payload must be the canonical
    # explanation.
    emit("finalizing", task_id=task.id, stage="failed")
    with _branch_lock(branch_plan.expected_publish_branch):
        task = env_backend.finalize(env_ctx, task, tasks_dir)
    _emit_preserved_containers(emit, task)
    failed_payload: dict[str, object] = {
        "task_id": task.id,
        "event_id": eid,
        "stage": "run",
        "attempts": attempt,
    }
    if last_failure:
        failed_payload["exit_code"] = last_failure["exit_code"]
        if last_failure.get("error"):
            failed_payload["error"] = last_failure["error"]
        if last_failure.get("timed_out"):
            failed_payload["timed_out"] = True
    emit("failed", **failed_payload)
    return task


def _invoke_with_heartbeat(
    env_backend,
    env_ctx,
    runner_name: str,
    invocation: "runner.RunnerInvocation",
    *,
    cfg: dict,
    trace: bool,
    on_heartbeat,
    interval: float = _HEARTBEAT_INTERVAL,
) -> "runner.RunnerResult":
    """Run *env_backend.invoke* in a thread, ticking *on_heartbeat* every
    *interval* seconds while it's alive.

    The runner subprocess can sit silent for many minutes — codex with
    xhigh reasoning routinely chews for 5-10 min without emitting any
    daemon-side packets. The heartbeat keeps the chat card alive: each
    tick prompts gates to re-render with a fresh elapsed counter.
    Heartbeat callbacks run on the daemon's main thread (synchronous
    with the loop), not in the runner thread, so a misbehaving
    callback can't corrupt the in-flight runner invocation.
    """
    import threading

    holder: list = []

    def _target() -> None:
        try:
            holder.append(env_backend.invoke(
                env_ctx, runner_name, invocation, cfg=cfg, trace=trace,
            ))
        except BaseException as exc:  # noqa: BLE001
            holder.append(exc)

    worker = threading.Thread(
        target=_target,
        daemon=True,
        name=f"runner-{invocation.label}",
    )
    worker.start()
    while worker.is_alive():
        worker.join(timeout=interval)
        if worker.is_alive():
            try:
                on_heartbeat()
            except Exception:
                # Heartbeat is best-effort; never let it break a real run.
                pass

    outcome = holder[0]
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


def _emit_new_containers(
    emit: _WorkerEmit,
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
        emit(
            "container_started",
            task_id=task_id,
            env=env_ctx.name,
            container=cid,
        )


def _recent_conversation_for_prompt(
    records: list[dict],
    *,
    event_id: str,
    task_id: str,
) -> list[dict]:
    """Return prior conversation records, excluding the in-flight task."""
    out: list[dict] = []
    for record in records:
        if record.get("event_id") == event_id:
            continue
        if record.get("task_id") == task_id:
            continue
        out.append(record)
    return out


def _emit_preserved_containers(
    emit: _WorkerEmit,
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
    emit("container_preserved", task_id=task.id, containers=containers)


def _record_response_artifact(
    emit: _WorkerEmit,
    task: Task,
    response_path: Path,
) -> None:
    """Index the response artifact on the conversation log."""
    label = f"response:{task.event_id}" if task.event_id else f"response:{task.id}"
    if emit.conversation_key:
        conversations.append_artifact(
            emit.brr_dir, emit.conversation_key,
            kind="response",
            path=str(response_path),
            task_id=task.id,
            event_id=emit.event_id,
            label=label,
        )
    emit(
        "artifact_created",
        task_id=task.id,
        kind="response",
        path=str(response_path),
    )


def _set_event_status_if_present(event: dict, status: str) -> bool:
    """Set an inbox event status, tolerating gate cleanup after delivery."""
    try:
        protocol.set_status(event, status)
    except FileNotFoundError:
        return False
    event["status"] = status
    return True


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


# Pathspecs the task-touched-pages query and the maintenance commit
# scope both respect. ``kb/`` covers the synthesis layer; the two
# ``AGENTS.md`` paths cover the universal schema (the symlink at the
# repo root and the canonical copy bundled with the package).
_KB_TOUCHED_PATHSPECS: tuple[str, ...] = (
    "kb",
    "AGENTS.md",
    "src/brr/AGENTS.md",
)


def _kb_pages_touched_since(
    run_root: Path, pre_head: str | None,
) -> list[str]:
    """Return kb/AGENTS.md files changed between *pre_head* and ``HEAD``.

    Returns an empty list when *pre_head* is missing or git refuses to
    diff (worktree without git, bare repos, etc.). Paths are relative
    to the repo root and sorted for deterministic output. The
    maintenance prompt injects the list so the agent has a concrete
    review target rather than "the whole kb".
    """
    if not pre_head:
        return []
    try:
        result = subprocess.run(
            [
                "git", "diff", "--name-only", "-z",
                f"{pre_head}..HEAD", "--", *_KB_TOUCHED_PATHSPECS,
            ],
            cwd=run_root, capture_output=True, text=True, timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []
    raw = result.stdout
    if not raw:
        return []
    paths = [p for p in raw.split("\0") if p]
    return sorted(set(paths))


def _format_touched_block(touched: list[str]) -> str:
    """Render *touched* as a Markdown block for the maintenance prompt.

    Returns ``""`` when the list is empty so callers can drop the
    block entirely. The header matches the cue the maintenance prompt
    references ("Task-touched kb pages") so the agent can recognise
    it deterministically.
    """
    if not touched:
        return ""
    lines = ["## Task-touched kb pages", ""]
    for path in touched:
        lines.append(f"- `{path}`")
    lines.append("")
    return "\n".join(lines)


def _maybe_kb_maintenance(
    run_root: Path,
    repo_root: Path,
    cfg: dict,
    runner_name: str,
    *,
    emit: _WorkerEmit | None = None,
    task_id: str | None = None,
    task_pre_head: str | None = None,
    trace: bool = False,
) -> str | None:
    """Run kb maintenance with deterministic preflight + LLM redundancy pass.

    The preflight is cheap and runs every time. When kb is unchanged
    *and* the preflight is clean, the LLM pass is skipped — kb
    maintenance becomes a true safety net rather than a tax on every
    task. When findings exist or kb has been touched, the LLM pass
    runs with the preflight findings injected into the prompt.

    When *task_pre_head* is supplied (typically the seed-ref OID the
    task branch sprouted from), the prompt also gets a
    ``Task-touched kb pages`` block listing the kb / AGENTS.md files
    the preceding task changed. The maintenance agent reviews those
    pages as its primary job; the deterministic findings are
    additional concrete targets. Without this list the agent was
    historically left to "scan the whole kb for anything weird",
    which it reliably skipped.

    Any leftover uncommitted kb edits after the runner exits get
    rolled into one ``brr maintenance`` commit on the task's current
    branch so cleanup lands in the same delivery (and PR, when one
    exists) as the work that triggered it. A ``kb_maintenance_done``
    packet is emitted when ``brr_dir`` and ``conv_key`` are provided,
    so gates surface the outcome on the response card; without it
    the pass historically dropped its edits silently.
    """
    policy = str(cfg.get("kb_maintenance", "auto")).strip().lower()
    if policy == "never":
        return None

    findings = kb_preflight.scan(run_root)
    kb_changed = _kb_changed(run_root)
    if policy == "auto" and not kb_changed and not findings:
        return None

    base_prompt = prompts.build_kb_maintenance_prompt(run_root)
    if not base_prompt:
        return None

    task_touched = _kb_pages_touched_since(run_root, task_pre_head)
    findings_block = kb_preflight.format_findings(findings)
    stats_block = kb_health.format_graph_stats(
        kb_health.compute_graph_stats(run_root, task_touched=task_touched),
    )
    touched_block = _format_touched_block(task_touched)
    extras = "\n\n".join(
        block
        for block in (touched_block, findings_block, stats_block)
        if block
    )
    prompt = (
        f"{base_prompt}\n\n{extras}".rstrip() + "\n"
        if extras
        else base_prompt
    )

    if findings:
        print(f"[brr] running kb maintenance ({len(findings)} preflight finding(s))...")
    else:
        print("[brr] running kb maintenance (kb changed; preflight clean)...")
    pre_head = gitops.rev_parse(run_root, "HEAD")
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

    commits, files = _commit_kb_maintenance_edits(run_root, pre_head)
    if emit is not None and emit.conversation_key:
        emit(
            "kb_maintenance_done",
            task_id=task_id,
            commits=commits,
            files=files,
            ok=bool(result.ok),
        )

    if result.trace_dir:
        return str(result.trace_dir.relative_to(gitops.shared_brr_dir(repo_root)))
    return None


# Files the maintenance pass is allowed to touch. Anything outside
# this set is left alone — if the pass strayed (e.g. modified runtime
# code or the daemon source), we don't paper over the contract
# violation with a commit. The salvage rule preserves the worktree
# on uncommitted state so the operator sees the stray edits.
_KB_MAINTENANCE_PATHSPECS: tuple[str, ...] = (
    "kb",
    "AGENTS.md",
    "src/brr/AGENTS.md",
)

# Author baked into automated commits made *for* the maintenance
# pass. The agent's own commits keep its configured git identity;
# this only stamps the daemon's roll-up of leftover edits so the
# git log can answer "did a human or automated cleanup write this?".
_KB_MAINTENANCE_AUTHOR_NAME = "brr maintenance"
_KB_MAINTENANCE_AUTHOR_EMAIL = "brr-maintenance@brr.local"


def _commit_kb_maintenance_edits(
    run_root: Path, pre_head: str | None,
) -> tuple[int, int]:
    """Stamp leftover kb edits and count what landed on the branch.

    Returns ``(commits, files)`` where ``commits`` is the number of
    commits added between *pre_head* and the function's exit, and
    ``files`` is the number of files touched across those commits.

    The maintenance prompt asks the agent to commit its own edits;
    this is the fallback for agents that don't. Anything outside
    :data:`_KB_MAINTENANCE_PATHSPECS` is left uncommitted so a stray
    edit surfaces via the worktree-salvage rule rather than being
    silently absorbed into a kb commit.
    """
    try:
        for pathspec in _KB_MAINTENANCE_PATHSPECS:
            target = run_root / pathspec
            if not target.exists():
                continue
            gitops._git(run_root, "add", "--", pathspec, check=False)
        staged = gitops._git(
            run_root, "diff", "--cached", "--name-only", check=False,
        )
        if staged.stdout.strip():
            env = os.environ.copy()
            env.update({
                "GIT_AUTHOR_NAME": _KB_MAINTENANCE_AUTHOR_NAME,
                "GIT_AUTHOR_EMAIL": _KB_MAINTENANCE_AUTHOR_EMAIL,
                "GIT_COMMITTER_NAME": _KB_MAINTENANCE_AUTHOR_NAME,
                "GIT_COMMITTER_EMAIL": _KB_MAINTENANCE_AUTHOR_EMAIL,
            })
            subprocess.run(
                [
                    "git", "commit",
                    "-m", "chore(kb): inline maintenance pass",
                ],
                cwd=run_root, env=env, check=False,
                capture_output=True,
            )
    except Exception:  # noqa: BLE001 — maintenance must not break delivery
        pass

    if pre_head is None:
        return (0, 0)
    head = gitops.rev_parse(run_root, "HEAD")
    if not head or head == pre_head:
        return (0, 0)
    try:
        count = gitops._git(
            run_root, "rev-list", "--count", f"{pre_head}..{head}",
            check=False,
        )
        commits = int((count.stdout or "0").strip() or "0")
        name_only = gitops._git(
            run_root, "diff", "--name-only", pre_head, head,
            check=False,
        )
        files = sum(
            1 for line in name_only.stdout.splitlines() if line.strip()
        )
        return (commits, files)
    except Exception:  # noqa: BLE001
        return (0, 0)


# ── Worker-tail housekeeping ────────────────────────────────────────


def _run_worker_and_finalize(
    event: dict,
    repo_root: Path,
    responses_dir: Path,
    cfg: dict,
    max_retries: int,
) -> Task:
    """Run one event end-to-end and return the resulting Task.

    Owns the full pipeline for one event: run the runner, capture
    response, perform post-response housekeeping, and push to remote.
    Lives as a separate function so each worker thread owns the whole
    pipeline (and so tests can drive it without spinning up the
    threaded loop).

    The dev-reload watcher is polled in the main loop only — calling
    it from worker threads would race on the watcher's internal
    snapshot.
    """
    task = _run_worker(event, repo_root, responses_dir, cfg, max_retries)
    if event.get("status") != "done":
        _set_event_status_if_present(event, task.status)
    if task.status == "error":
        print(f"[brr] task {task.id}: failed")

    publish(repo_root, task)
    return task


# ── Main loop ────────────────────────────────────────────────────────


def start(
    repo_root: Path,
    *,
    dev_reload: bool | None = None,
) -> None:
    """Run the daemon main loop (blocking, foreground).

    Tasks dispatch into a bounded ``ThreadPoolExecutor`` so unrelated
    events run in parallel. ``max_workers`` reads from ``.brr/config``
    (default ``4``); set ``max_workers=1`` to reproduce the previous
    serial behaviour exactly. Workers don't share mutable state — see
    ``kb/design-concurrent-execution.md`` and ``kb/subject-daemon.md``
    for the partitioning contract.

    Traces are always written and worktrees/containers are kept on
    failure (or when uncommitted files are left behind) but discarded
    on clean success — there is no operator-facing debug switch.

    *dev_reload* enables the brr-development re-exec watcher.  When
    ``None``, falls back to the ``dev_reload`` key in ``.brr/config``.
    Reload waits until the worker pool drains so no in-flight task
    has its process replaced underneath it.
    """
    brr_dir = gitops.shared_brr_dir(repo_root)
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"

    existing_pid = read_pid(brr_dir)
    if existing_pid and not reload_mod.is_reexec_for_current_process(existing_pid):
        raise SystemExit("[brr] daemon already running")
    reload_mod.clear_reexec_marker()
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
    max_workers = max(1, int(cfg.get("max_workers", _DEFAULT_MAX_WORKERS)))
    dev_reload_mode = (
        dev_reload if dev_reload is not None
        else bool(cfg.get("dev_reload", False))
    )
    reload_watcher = (
        reload_mod.DevReloadWatcher.for_repo(repo_root)
        if dev_reload_mode else None
    )

    gate_threads = _start_gates(brr_dir, inbox_dir, responses_dir)
    if not gate_threads:
        print("[brr] warning: no gates configured — inbox will only receive events from `brr run` or scripts")

    if reload_watcher is not None:
        print("[brr] developer reload enabled")
    print(
        f"[brr] daemon started (pid {os.getpid()}, "
        f"max_workers={max_workers})"
    )

    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="brr-worker",
    )
    # Maps event path → in-flight future, so we don't double-dispatch
    # an event the gate hasn't observed as "done" yet on its next
    # scan. The path is a stable identity for the inbox file.
    in_flight: dict[Path, concurrent.futures.Future] = {}
    reload_requested = False

    try:
        while running:
            # Top of loop: poll the dev-reload watcher exactly once.
            # The watcher mutates its own snapshot; the main thread is
            # its only caller so the changed() bookkeeping stays
            # consistent. Workers don't poll the watcher themselves.
            if reload_watcher is not None and reload_watcher.changed():
                reload_requested = True

            # Reap completed futures so capacity reflects reality on
            # this iteration's dispatch decisions.
            completed = [
                path for path, fut in in_flight.items() if fut.done()
            ]
            for path in completed:
                fut = in_flight.pop(path)
                try:
                    fut.result()
                except Exception as exc:  # noqa: BLE001
                    # Worker crashed before returning a Task. The task
                    # file may or may not exist; the operator sees the
                    # traceback in the daemon console.
                    print(f"[brr] worker crashed: {exc}")

            # Quiescent reload: only re-exec when no worker is in
            # flight, so an in-progress task can't have its process
            # replaced underneath it. The dev_reload design's
            # "between tasks" guarantee generalises to "between
            # batches" under the concurrent pool.
            if reload_requested and not in_flight:
                print("[brr] package files changed; re-execing daemon")
                pool.shutdown(wait=True)
                reload_mod.reexec()  # noreturn on success

            # Dispatch new events as capacity allows. Stop accepting
            # new events once reload is requested so the pool can
            # drain — bounds reload latency to "longest in-flight
            # task at flag time".
            if not reload_requested:
                events = protocol.list_pending(inbox_dir)
                for event in events:
                    if event["_path"] in in_flight:
                        continue
                    if len(in_flight) >= max_workers:
                        break
                    eid = event["id"]
                    print(f"[brr] processing: {eid}")
                    protocol.set_status(event, "processing")
                    fut = pool.submit(
                        _run_worker_and_finalize,
                        event, repo_root, responses_dir, cfg, max_retries,
                    )
                    in_flight[event["_path"]] = fut

            time.sleep(_SCAN_INTERVAL)

            for t in gate_threads:
                if not t.is_alive():
                    print(f"[brr] warning: gate thread {t.name} died")

    finally:
        # Drain in-flight workers before exiting. cancel_futures=False
        # because every submitted future has already started (we
        # throttle dispatch ourselves to max_workers); cancellation
        # would only cancel a queued task we don't have.
        pool.shutdown(wait=True, cancel_futures=False)
        _clear_pid(brr_dir)
        print("[brr] daemon stopped")
