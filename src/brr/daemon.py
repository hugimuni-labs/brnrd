"""Daemon — reflex loop that scans the inbox, wakes the agent, pushes results.

The daemon is a single foreground process (``brr up``) and a deliberately
thin **reflex** layer: it does as little orchestration as possible and
leaves judgement to the agent it wakes. It:

1. Starts configured gate threads (each gate polls its own channel).
2. Scans ``.brr/inbox/`` for pending events on a timer.
3. Runs **single-flight** — one *thought* at a time. When idle and work
   is pending it spawns one worker; new events that arrive mid-thought are
   surfaced to the living agent through ``outbox/<event>/inbox.json`` and
   either get folded in at plan boundaries or wait for the next spawn.
   Concurrency within one resident is cooperative, not parallel across
   workers. See ``kb/design-agent-dominion.md`` §4 and
   ``kb/subject-daemon.md``.
4. The worker owns the full pipeline for its event: runner invocation,
   retries, response capture, response release to gates, env finalize,
   and branch push.

There is no command layer: every event either wakes the agent or waits
for the living agent — the daemon never parses ``/cancel`` or the like.
Liveness is enforced from the heartbeat: each tick checks an
agent-extensible budget (``runner.timeout_seconds``, pushed out by a
keepalive the agent writes) and kills a runner that outlives it via
``runner.kill_active``; the runner's own ``communicate`` timeout is the
final backstop if the heartbeat path wedges. ``brr down`` / SIGTERM flip
the loop flag and kill the in-flight runner, so the single-flight slot is
reclaimed promptly rather than waiting out a long budget.
"""

from __future__ import annotations

import collections
import concurrent.futures
import json
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
from . import dominion
from . import envs
from . import forges
from . import gitops
from . import presence
from . import prompts
from . import protocol
from . import run_context
from . import runner
from . import schedule as schedule_mod
from . import sync
from . import updates
from .task import Task

_SCAN_INTERVAL = 3
_BUILTIN_GATES = ["telegram", "slack", "github", "cloud"]
# Cadence for the run-time heartbeat packet. 30s is short enough that
# the chat card visibly bumps elapsed time during the long "running"
# phase, and far below Telegram's edit rate ceiling (~30/sec/chat).
_HEARTBEAT_INTERVAL = 30.0
# Extra rows pulled from the conversation log on top of what the prompt
# actually renders. Absorbs the in-flight event + task records (and any
# pre-runner update packets for the same event) that
# ``_recent_conversation_for_prompt`` strips before formatting, so the
# rendered tail stays at ``prompts.RECENT_CONVERSATION_MAX``.
_RECENT_READ_HEADROOM = 12
_LIVE_INBOX_NAME = "inbox.json"


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
    - Normally the agent starts on ``target_branch`` (set up by
      ``WorktreeEnv.prepare``) and commits there, so ``publish_branch``
      and ``target_branch`` are the same and this is a plain push.
    - If the agent switched to a different branch, that branch is
      published as-is.
    - Refspec fallback: if ``publish_branch`` still diverges from
      ``target_branch`` (e.g. the agent left the worktree on the
      ``brr/<task-id>`` placeholder), push via a refspec
      ``brr/<task-id>:refs/heads/<target>`` so the daemon never has
      to update the local target ref.
    - When the source ref equals ``target_branch`` AND
      ``task.meta["expected_remote_oid"]`` is set AND the local source
      is not an ancestor of the remote target, push with
      ``--force-with-lease`` anchored to that oid. This is the
      PR-rebase case.
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

    # Compat: tasks created before the rename still carry the old key.
    expected = (
        task.meta.get("target_branch")
        or task.meta.get("expected_publish_branch")
        or None
    )
    expected_remote_oid = task.meta.get("expected_remote_oid") or None
    # Refspec fallback: agent ended on a different branch than target.
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
    ``kb/subject-daemon.md``.
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

    # Record this thought in the presence registry so overlapping thoughts
    # (ad-hoc sessions, a second daemon) can see who's on which stream and
    # avoid colliding on the same work (kb/design-agent-dominion.md §4).
    # Best-effort: presence is a hint, never a gate. Deregistered in
    # _run_worker_and_finalize's finally; the heartbeat closure refreshes it.
    presence_id: str | None = None
    try:
        presence_id = presence.register(
            brr_dir, kind="daemon", stream=conv_key, task_id=task.id,
        )["id"]
        task.meta["presence_id"] = presence_id
    except OSError:
        presence_id = None

    task.update_status("running", tasks_dir)
    resp_path = protocol.response_path(responses_dir, eid)
    # Per-event drop zone for interim responses the resident ships
    # mid-flight (the multi-response protocol, kb/design-multi-response.md).
    # Created up front so the agent can write to it the moment it wakes.
    outbox_dir = brr_dir / "outbox" / eid
    outbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir = brr_dir / "inbox"

    print(f"[brr] task {task.id} (event {eid}): env={task.env}")

    task.meta["response_path"] = str(resp_path)
    task.meta["outbox_path"] = str(outbox_dir)
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
            outbox_path=outbox_dir,
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
    branch_setup_notice = task.meta.get("branch_setup_notice") or None

    # Deterministic ergonomics probes run once the env is prepared (so
    # the resolved image/token/worktree state is visible). Routing is
    # owner-aware (env_ctx.owner): user-owned runs default to a quiet
    # daemon log, operator-owned runs and ergonomics=off resolve to the
    # null proxy and short-circuit. Never gates the task — every failure
    # mode is swallowed here so a probe bug can't fail a run.
    try:
        from . import ergonomics
        ergonomics.probe_task_prep(
            task=task,
            repo_root=repo_root,
            brr_dir=brr_dir,
            cfg=cfg,
            ctx=env_ctx,
        )
    except Exception:
        pass

    emit(
        "env_prepared",
        task_id=task.id,
        env=task.env,
        branch_name=branch_name,
        seed_ref=branch_plan.seed_ref,
        target_branch=branch_plan.target_branch,
        branch_source=branch_plan.source,
    )

    if conv_key:
        conversations.append_task(
            brr_dir, conv_key,
            task_id=task.id, event_id=eid,
            env=task.env, status=task.status,
            branch_name=branch_name,
            seed_ref=branch_plan.seed_ref,
            target_branch=branch_plan.target_branch,
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

    # Snapshot of other waiting events so the resident has immediate
    # orientation at wake. A live copy is also refreshed in the outbox
    # below and on every heartbeat.
    pending_events_snapshot = _pending_events_for_agent(inbox_dir, eid)
    _write_live_inbox(outbox_dir, inbox_dir, eid)

    # Other thoughts awake right now (presence registry), excluding this
    # one — so the resident knows it may share the dominion with a
    # concurrent session and reconciles rather than fights (slice 5).
    present_snapshot = [
        e for e in presence.list_active(brr_dir)
        if e.get("task_id") != task.id
    ]

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
        target_branch=branch_plan.target_branch,
        env=task.env,
        runner=runner_name,
    )
    seen_containers: set[str] = set()
    last_failure: dict[str, object] | None = None
    prompt_diffense = prompts.diffense_emit_enabled(cfg)
    # Liveness budget: the heartbeat enforces this soft, agent-extensible
    # deadline; the runner's communicate() backstops at the hard cap. The
    # agent extends it by writing the keepalive control dotfile in its
    # outbox (skipped by the drain — see _drain_outbox).
    budget_seconds = runner.runner_timeout(cfg)
    hard_cap_seconds = max(budget_seconds * 4, budget_seconds + 3600)
    keepalive_path = outbox_dir / ".keepalive"
    for attempt in range(1, max_retries + 2):
        if attempt == 1:
            prompt = prompts.build_daemon_prompt(
                task.body, eid, str(env_ctx.response_path_env), run_root,
                outbox_path=str(env_ctx.outbox_env) if env_ctx.outbox_env else None,
                task_id=task.id,
                source=task.source or event.get("source"),
                environment=task.env,
                branch_name=branch_name,
                seed_ref=branch_plan.seed_ref,
                branch_source=branch_plan.source,
                branch_setup_notice=branch_setup_notice,
                host_context_branch=branch_plan.host_context_branch,
                runtime_dir=str(env_ctx.runtime_dir),
                context_path=str(context_path),
                recent_conversation=recent_conversation,
                pending_events=pending_events_snapshot,
                present=present_snapshot,
                event_body=event_body_for_prompt,
                budget_seconds=budget_seconds,
                diffense=prompt_diffense,
            )
        else:
            prompt = prompts.build_daemon_prompt(
                f"Previous attempt printed no final reply on stdout. "
                f"Print your full response as the final stdout message.\n\n"
                f"Original task: {task.body}",
                eid, str(env_ctx.response_path_env), run_root,
                outbox_path=str(env_ctx.outbox_env) if env_ctx.outbox_env else None,
                task_id=task.id,
                source=task.source or event.get("source"),
                environment=task.env,
                branch_name=branch_name,
                seed_ref=branch_plan.seed_ref,
                branch_source=branch_plan.source,
                branch_setup_notice=branch_setup_notice,
                host_context_branch=branch_plan.host_context_branch,
                runtime_dir=str(env_ctx.runtime_dir),
                context_path=str(context_path),
                recent_conversation=recent_conversation,
                pending_events=pending_events_snapshot,
                present=present_snapshot,
                event_body=event_body_for_prompt,
                budget_seconds=budget_seconds,
                diffense=prompt_diffense,
            )

        print(f"[brr] worker {eid}: attempt {attempt}")
        emit("attempt_started", task_id=task.id, event_id=eid, attempt=attempt)

        attempt_started_monotonic = time.monotonic()

        def _emit_heartbeat() -> None:
            # Drain first: promoting an interim response is the resident's
            # mid-run check-in, and the partial should reach the gate as
            # promptly as the heartbeat that observed the agent is alive.
            _drain_outbox(emit, task, responses_dir, eid, outbox_dir, inbox_dir)
            _write_live_inbox(outbox_dir, inbox_dir, eid)
            if presence_id:
                presence.heartbeat(brr_dir, presence_id)
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
                timeout_seconds=hard_cap_seconds,
            ),
            cfg=cfg,
            trace=True,
            on_heartbeat=_emit_heartbeat,
            budget_seconds=budget_seconds,
            hard_cap_seconds=hard_cap_seconds,
            keepalive_path=keepalive_path,
        )
        _emit_new_containers(emit, task.id, env_ctx, seen_containers)
        # Final drain after the runner returns: catch interim responses
        # written between the last heartbeat and exit, before finalize.
        _drain_outbox(emit, task, responses_dir, eid, outbox_dir, inbox_dir)
        # Capture the resident's dominion edits before any branch/exit. One
        # call site covers success, retry, and hard failure: a clean
        # dominion no-ops, and on retry the next pass just re-captures any
        # new writes (idempotent — see _capture_dominion).
        _capture_dominion(repo_root, cfg, task)
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
            emit("finalizing", task_id=task.id, stage="done")
            # Per-branch lock around finalize: serialises publish on a
            # branch name so two pushers can't race it. Under single-flight
            # one daemon never contends here; the lock stays as cheap
            # insurance and a seam for a future concurrency revisit (see
            # kb/review-daemon-coherence-2026-06.md §4).
            with _branch_lock(branch_plan.target_branch):
                task = env_backend.finalize(env_ctx, task, tasks_dir)
            _cleanup_traces_on_success(brr_dir, tasks_dir, task)
            _remove_outbox(outbox_dir)
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
    with _branch_lock(branch_plan.target_branch):
        task = env_backend.finalize(env_ctx, task, tasks_dir)
    _remove_outbox(outbox_dir)
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


def _keepalive_until(keepalive_path: Path | None) -> float | None:
    """Read an agent-written keepalive into an absolute epoch deadline.

    The file is a control dotfile in the task outbox carrying one line:
    an ISO-8601 timestamp ("busy until T"), or ``+<duration>`` (e.g.
    ``+30m``) interpreted from the file's mtime ("busy for N from when I
    wrote this", so re-reads don't slide). Returns epoch seconds, or
    ``None`` when the file is absent, empty, or unparseable.
    """
    if keepalive_path is None or not keepalive_path.exists():
        return None
    try:
        raw = keepalive_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    first = raw.splitlines()[0].strip()
    if first.startswith("+"):
        secs = schedule_mod.parse_duration(first[1:].strip())
        if secs is None:
            return None
        try:
            mtime = keepalive_path.stat().st_mtime
        except OSError:
            return None
        return mtime + secs
    return schedule_mod.parse_iso(first)


def _budget_exceeded(
    start_mono: float,
    budget_seconds: float,
    hard_cap_seconds: float | None,
    keepalive_path: Path | None,
) -> bool:
    """True when the runner has outlived its extensible, capped budget."""
    now_mono = time.monotonic()
    deadline = start_mono + budget_seconds
    until = _keepalive_until(keepalive_path)
    if until is not None:
        # Translate the wall-clock extension into the monotonic clock the
        # loop measures against.
        deadline = max(deadline, now_mono + (until - time.time()))
    if hard_cap_seconds is not None:
        deadline = min(deadline, start_mono + hard_cap_seconds)
    return now_mono >= deadline


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
    budget_seconds: float | None = None,
    hard_cap_seconds: float | None = None,
    keepalive_path: Path | None = None,
) -> "runner.RunnerResult":
    """Run *env_backend.invoke* in a thread, ticking *on_heartbeat* every
    *interval* seconds while it's alive, and enforce the liveness budget.

    The runner subprocess can sit silent for many minutes — codex with
    xhigh reasoning routinely chews for 5-10 min without emitting any
    daemon-side packets. The heartbeat keeps the chat card alive: each
    tick prompts gates to re-render with a fresh elapsed counter. The
    callbacks run on the thought thread driving this invocation (the same
    stack that called here), not on the runner's inner thread, so a
    misbehaving callback can't corrupt the in-flight runner.

    When *budget_seconds* is set, the same tick is the liveness authority:
    past ``start + budget`` the runner is killed via
    :func:`runner.kill_active` to reclaim the single-flight slot — unless
    the agent extended its deadline by writing *keepalive_path*.
    Extensions are capped at *hard_cap_seconds* so a forgotten keepalive
    can't pin the daemon forever, and the runner's own ``communicate``
    timeout (set to the hard cap) is the final backstop.
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
    start = time.monotonic()
    deadline_killed = False
    while worker.is_alive():
        worker.join(timeout=interval)
        if not worker.is_alive():
            break
        try:
            on_heartbeat()
        except Exception:
            # Heartbeat is best-effort; never let it break a real run.
            pass
        if budget_seconds is not None and _budget_exceeded(
            start, budget_seconds, hard_cap_seconds, keepalive_path,
        ):
            if runner.kill_active():
                deadline_killed = True
                worker.join()  # let the killed proc surface its result
            break

    outcome = holder[0]
    if isinstance(outcome, BaseException):
        raise outcome
    if deadline_killed and isinstance(outcome, runner.RunnerResult):
        # Present a budget kill like the wall-clock timeout (124) so the
        # retry/finalize path and the operator read it the same way.
        outcome.returncode = 124
        note = f"runner exceeded its {int(budget_seconds)}s liveness budget"
        outcome.stderr = (outcome.stderr + "\n" if outcome.stderr else "") + note
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


def _pending_event_record(ev: dict) -> dict[str, object]:
    """Return the agent-facing JSON shape for one pending event."""
    body = str(ev.get("body") or "")
    summary = " ".join(body.split())
    if len(summary) > 240:
        summary = summary[:237].rstrip() + "..."
    out: dict[str, object] = {}
    for key, value in ev.items():
        if key.startswith("_") or key == "body":
            continue
        out[key] = value
    out["summary"] = summary
    out["body"] = body
    return out


def _pending_events_for_agent(
    inbox_dir: Path,
    current_event_id: str,
) -> list[dict[str, object]]:
    """Return other waiting events the resident may fold in."""
    events: list[dict[str, object]] = []
    for ev in protocol.list_pending(inbox_dir):
        if ev.get("id") == current_event_id:
            continue
        if ev.get("status") != "pending":
            continue
        events.append(_pending_event_record(ev))
    return events


def _write_live_inbox(
    outbox_dir: Path | None,
    inbox_dir: Path,
    current_event_id: str,
) -> Path | None:
    """Refresh the live inbox view exposed to the running resident.

    The file sits in the task outbox because that directory is already
    mounted into every daemon-run environment. It is daemon-owned control
    state, not a deliverable outbox message.
    """
    if not outbox_dir:
        return None
    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "generated_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "current_event": current_event_id,
            "events": _pending_events_for_agent(inbox_dir, current_event_id),
        }
        path = outbox_dir / _LIVE_INBOX_NAME
        protocol._atomic_write(
            path,
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        )
        return path
    except OSError:
        return None


def _find_pending_event(inbox_dir: Path | None, event_id: str) -> dict | None:
    """Return the inbox event with *event_id* if it's still pending/processing."""
    if not inbox_dir:
        return None
    for ev in protocol.list_pending(inbox_dir):
        if ev.get("id") == event_id:
            return ev
    return None


def _drain_outbox(
    emit: _WorkerEmit,
    task: Task,
    responses_dir: Path,
    event_id: str,
    outbox_dir: Path | None,
    inbox_dir: Path | None = None,
) -> int:
    """Promote interim/interleaved responses the resident dropped in its outbox.

    The producer half of the multi-response protocol
    (``kb/design-multi-response.md``). Scans the per-event drop zone
    oldest-first; for each complete file it reads an optional
    ``event: <id>`` frontmatter target:

    - **Current event** (no target, or the target is this event): promote
      to this event's partials queue as an interim reply, streamed ahead
      of the terminal stdout.
    - **Another pending event** (``event: <id>``, interleaving): the
      resident folded a quick request in without waiting for its own
      spawn — promote the body to *that* event's queue and mark *that*
      event ``done`` so the gate delivers the reply to its thread and
      cleans it up. A target that isn't a live pending event is dropped
      (don't misroute).
    - **A gate destination** (``gate: <name>`` + target metadata): an
      agent-initiated message with no waiting event (a scheduled ping, an
      out-of-bound note). ``_deliver_out_of_bound`` synthesizes an
      already-``done`` event the gate delivers and cleans up. ``event:``
      is "reply to a waiting thread"; ``gate:`` is "send to a
      destination".

    Each promotion is indexed on the conversation log and emits an
    ``interim_response`` packet; the consumed file is removed. ``.tmp``
    files are skipped so the agent has an atomic-write staging name.
    Returns the count promoted — a promoting drain is also a liveness
    check-in. Errors are swallowed: a drain bug must never break a run.

    Called from the heartbeat tick (so it drains while the runner is
    alive) and once more right after the runner returns.
    """
    if not outbox_dir or not outbox_dir.exists():
        return 0
    try:
        entries = sorted(
            (p for p in outbox_dir.iterdir() if p.is_file()),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )
    except OSError:
        return 0
    promoted = 0
    for fpath in entries:
        # ``.tmp`` is the agent's atomic-write staging name; dotfiles are
        # reserved as control channels (e.g. ``.keepalive`` for the
        # liveness budget), and inbox.json is the daemon-owned live-inbox
        # view. None are deliverable messages.
        if (
            fpath.suffix == ".tmp"
            or fpath.name.startswith(".")
            or fpath.name == _LIVE_INBOX_NAME
        ):
            continue
        try:
            text = fpath.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = protocol.parse_frontmatter(text)
        body = protocol.frontmatter_body(text).strip()
        gate = str(fm.get("gate") or "").strip()
        if gate:
            # Gate-addressed: an agent-initiated message to a destination
            # with no waiting event (a scheduled ping, an out-of-bound
            # note). Synthesize an already-`done` event the gate delivers
            # and cleans up; it never wakes a thought.
            if _deliver_out_of_bound(
                emit, task, responses_dir, inbox_dir, event_id, gate, fm, body,
            ):
                promoted += 1
            fpath.unlink(missing_ok=True)
            continue
        target = str(fm.get("event") or "").strip()
        target = target or event_id
        cross = target != event_id
        target_event = _find_pending_event(inbox_dir, target) if cross else None
        if cross and target_event is None:
            # Unknown or already-handled target — don't deliver to the
            # wrong thread; drop with a console note.
            print(f"[brr] outbox: no deliverable event {target!r}; dropping reply")
            fpath.unlink(missing_ok=True)
            continue
        ppath = protocol.write_partial(responses_dir, target, body) if body else None
        fpath.unlink(missing_ok=True)
        if not ppath:
            continue
        promoted += 1
        if cross and target_event is not None:
            _set_event_status_if_present(target_event, "done")
        if emit.conversation_key:
            conversations.append_artifact(
                emit.brr_dir, emit.conversation_key,
                kind="interim_response",
                path=str(ppath),
                task_id=task.id,
                event_id=event_id,
                label=(f"reply:{target}" if cross else f"interim:{event_id}"),
            )
        emit(
            "interim_response",
            task_id=task.id,
            event_id=event_id,
            path=str(ppath),
            target_event=(target if cross else None),
        )
    return promoted


def _gate_can_deliver(brr_dir: Path, gate: str) -> bool:
    """True when *gate* or its delivery alias is configured here.

    Guards out-of-bound delivery against typos and unconfigured gates: a
    synthesized event for a gate no thread is polling would sit undelivered
    forever, so an unknown/unconfigured target is dropped with a note
    instead.
    """
    delivery_gate = _delivery_source_for_gate(gate)
    if delivery_gate not in _BUILTIN_GATES:
        return False
    try:
        from .gates import import_gate
        mod = import_gate(delivery_gate)
    except ImportError:
        return False
    is_configured = getattr(mod, "is_configured", None)
    return bool(is_configured) and bool(is_configured(brr_dir))


def _delivery_source_for_gate(gate: str) -> str:
    """Map agent-facing gate aliases to their delivery-loop source."""
    return "github" if gate == "forge" else gate


def _deliver_out_of_bound(
    emit: _WorkerEmit,
    task: Task,
    responses_dir: Path,
    inbox_dir: Path | None,
    event_id: str,
    gate: str,
    fm: dict,
    body: str,
) -> bool:
    """Queue an agent-initiated message to a gate destination.

    Synthesizes an already-`done` event for *gate* (or its delivery
    source, e.g. `forge` -> `github`) carrying the target
    metadata the agent named (chat id, channel, thread — whatever that
    gate's deliver closure reads, falling back to its configured default),
    with *body* as the response. The gate's normal deliver loop sends it
    and cleans it up; being `done` it never spawns a thought. This is the
    one core behind both out-of-bound pings and scheduled delivery.
    Returns True when queued.
    """
    if inbox_dir is None or not body:
        print(f"[brr] outbox: gate {gate!r} message had no body/inbox; dropping")
        return False
    if not _gate_can_deliver(emit.brr_dir, gate):
        print(f"[brr] outbox: gate {gate!r} is not a configured gate; dropping message")
        return False
    # Never let agent-written frontmatter override the reserved event keys
    # (a stray `status:` would resurrect the event as pending and spawn a
    # stray thought).
    reserved = {"gate", "event", "id", "source", "status", "created"}
    target_meta = {k: v for k, v in fm.items() if k not in reserved}
    event_source = _delivery_source_for_gate(gate)
    if gate == "forge":
        target_meta.setdefault("github_action", "pull_request")
    new_path = protocol.create_event(
        inbox_dir, event_source, "", status="done", **target_meta,
    )
    new_eid = new_path.stem
    protocol.write_response(responses_dir, new_eid, body)
    print(f"[brr] outbox: queued out-of-bound message to gate {gate!r} ({new_eid})")
    if emit.conversation_key:
        conversations.append_artifact(
            emit.brr_dir, emit.conversation_key,
            kind="outbound_message",
            path=str(protocol.response_path(responses_dir, new_eid)),
            task_id=task.id,
            event_id=event_id,
            label=f"outbound:{gate}",
        )
    return True


def _remove_outbox(outbox_dir: Path | None) -> None:
    """Best-effort removal of a drained per-event outbox drop zone."""
    if outbox_dir:
        shutil.rmtree(outbox_dir, ignore_errors=True)


def _schedule_enabled(cfg: dict) -> bool:
    return bool(cfg.get("schedule.enabled", cfg.get("schedule_enabled", True)))


def _fire_due_schedules(
    repo_root: Path,
    brr_dir: Path,
    inbox_dir: Path,
    cfg: dict,
) -> None:
    """Emit inbox events for any self-scheduled thoughts that are now due.

    The reflex half of self-invocation: the resident owns its
    ``.brr/dominion/schedule.md`` specs; this reads them against the
    daemon-owned firing-state and the clock, and fires due entries as
    ordinary ``schedule``-source events (picked up by the normal
    spawn-one-when-idle path). Specs live in the dominion; firing-state
    lives in the runtime dir — the daemon never writes the agent's
    ``schedule.md``. Best-effort: any failure is swallowed so scheduling
    never wedges the loop. See ``kb/design-self-scheduled-thoughts.md``.
    """
    if not _schedule_enabled(cfg):
        return
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return
    dom = dominion.dominion_path(repo_root)
    if not dom.is_dir():
        return
    try:
        entries = schedule_mod.parse_schedule(dom)
        if not entries:
            return
        state = schedule_mod.load_state(brr_dir)
        grace = float(
            cfg.get(
                "schedule.stale_grace_seconds",
                cfg.get("schedule_stale_grace_seconds", schedule_mod.DEFAULT_STALE_GRACE_S),
            )
        )
        due, new_state = schedule_mod.due_entries(
            entries, state, time.time(), stale_grace=grace,
        )
        for entry in due:
            body = entry.body or f"(self-scheduled thought: {entry.id})"
            # Thread the firing so a recurring entry's wakes share a
            # readable history; default per-entry, overridable to an
            # existing gate conversation.
            conv = entry.conversation_key or f"schedule:{entry.id}"
            protocol.create_event(
                inbox_dir, "schedule", body,
                schedule_id=entry.id, conversation_key=conv,
            )
            print(f"[brr] schedule: fired {entry.id}")
        if new_state != state:
            schedule_mod.save_state(brr_dir, new_state)
    except Exception as exc:  # noqa: BLE001 - scheduling must never wedge the loop
        print(f"[brr] schedule: skipped tick ({exc})")


def _retire_internal_event(event: dict, responses_dir: Path) -> bool:
    """Retire a gateless (``schedule``-source) event after it completes.

    A self-scheduled thought has no gate to deliver its response to or
    clean up after it — its effect is the work it did, not a chat reply —
    so the daemon deletes the event and any response/partials itself.
    Returns True when it cleaned up.
    """
    if event.get("source") != "schedule" or not event.get("_path"):
        return False
    eid = event.get("id", "")
    protocol.cleanup(
        event["_path"],
        protocol.response_path(responses_dir, eid),
        protocol.partials_dir(responses_dir, eid),
    )
    return True


def _capture_dominion(
    repo_root: Path,
    cfg: dict,
    task: Task,
) -> None:
    """Commit whatever the resident wrote into its dominion this thought.

    The persistence step of the agent-as-memory model: the resident edits
    ``.brr/dominion/`` freely during a thought; brr captures those edits
    at sleep so they survive to the next wake without the agent running a
    commit dance. Serialized + best-effort (see
    :func:`dominion.commit`) — a clean dominion is a silent no-op, and any
    failure is swallowed so capturing memory never breaks the run. Runs on
    both the success and failure exits (a failed thought may still have
    recorded the pain that caused it).
    """
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return
    branch = str(
        cfg.get("dominion.branch", cfg.get("dominion_branch", dominion.DEFAULT_BRANCH))
    )
    remote = gitops.default_remote(repo_root)
    push = bool(
        cfg.get("dominion.push_on_capture", cfg.get("dominion_push_on_capture", True))
    )
    committed = dominion.commit(
        dominion.dominion_path(repo_root),
        f"brr-home: capture working memory after task {task.id}",
        remote=remote,
        branch=branch,
        push=push and bool(remote),
    )
    if committed:
        print(f"[brr] dominion: captured working memory after {task.id}")


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
    task = None
    try:
        task = _run_worker(event, repo_root, responses_dir, cfg, max_retries)
        if event.get("status") != "done":
            _set_event_status_if_present(event, task.status)
        if task.status == "error":
            print(f"[brr] task {task.id}: failed")

        publish(repo_root, task)
        _retire_internal_event(event, responses_dir)
        return task
    finally:
        # Leave the presence registry — the thought is no longer awake.
        # The registry self-prunes on read too, but an explicit deregister
        # keeps it tidy and immediate.
        if task is not None and task.meta.get("presence_id"):
            presence.deregister(
                gitops.shared_brr_dir(repo_root), task.meta["presence_id"],
            )


# ── Main loop ────────────────────────────────────────────────────────


def start(
    repo_root: Path,
    *,
    dev_reload: bool | None = None,
) -> None:
    """Run the daemon main loop (blocking, foreground).

    **Single-flight**: one *thought* runs at a time. When idle and work
    is pending the loop spawns one worker; events that arrive mid-thought
    wait their turn (the living agent reconsiders its inbox at plan
    boundaries, or the next spawn picks them up). The worker still runs
    off the main thread, so the loop stays responsive to dev-reload,
    gate-thread liveness, and shutdown while a long thought runs. The
    per-task worktree/branch isolation and partitioned state survive from
    the former parallel design — see ``kb/subject-daemon.md`` and
    ``kb/design-agent-dominion.md`` §4.

    Traces are always written and worktrees/containers are kept on
    failure (or when uncommitted files are left behind) but discarded
    on clean success — there is no operator-facing debug switch.

    *dev_reload* enables the brr-development re-exec watcher.  When
    ``None``, falls back to the ``dev_reload`` key in ``.brr/config``.
    Reload waits until the in-flight thought drains so no running task
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
    dev_reload_mode = (
        dev_reload if dev_reload is not None
        else bool(cfg.get("dev_reload", False))
    )
    reload_watcher = (
        reload_mod.DevReloadWatcher.for_repo(repo_root)
        if dev_reload_mode else None
    )

    if bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        try:
            dpath = dominion.ensure_dominion(
                repo_root,
                branch=str(cfg.get(
                    "dominion.branch",
                    cfg.get("dominion_branch", dominion.DEFAULT_BRANCH),
                )),
            )
            print(f"[brr] dominion ready: {dpath}")
        except Exception as exc:  # noqa: BLE001
            print(f"[brr] dominion bootstrap skipped: {exc}")

    gate_threads = _start_gates(brr_dir, inbox_dir, responses_dir)
    if not gate_threads:
        print("[brr] warning: no gates configured — inbox will only receive events from `brr run` or scripts")

    if reload_watcher is not None:
        print("[brr] developer reload enabled")
    print(f"[brr] daemon started (pid {os.getpid()}, single-flight)")

    # Single-flight: one thought off the main thread at a time. A
    # one-slot executor keeps the clean future lifecycle (done / result /
    # drain-on-shutdown) while the loop stays responsive to dev-reload,
    # gate liveness, and signals during a long thought. The runner's own
    # wall-clock timeout (runner.timeout_seconds) is the liveness
    # backstop that reclaims the slot if the CLI subprocess wedges.
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="brr-thought",
    )
    current: concurrent.futures.Future | None = None
    reload_requested = False

    try:
        while running:
            # Poll the dev-reload watcher exactly once per iteration —
            # the main thread is its only caller, so the changed()
            # bookkeeping stays consistent.
            if reload_watcher is not None and reload_watcher.changed():
                reload_requested = True

            # Reap the in-flight thought once it finishes.
            if current is not None and current.done():
                try:
                    current.result()
                except Exception as exc:  # noqa: BLE001
                    # The thought crashed before returning a Task; the
                    # operator sees the traceback in the daemon console.
                    print(f"[brr] thought crashed: {exc}")
                current = None

            # Quiescent reload: only re-exec between thoughts, so a
            # running task can't have its process replaced underneath it.
            if reload_requested and current is None:
                print("[brr] package files changed; re-execing daemon")
                pool.shutdown(wait=True)
                reload_mod.reexec()  # noreturn on success

            # Fire any self-scheduled thoughts that have come due — they
            # land in the inbox as ordinary events and queue behind a
            # running thought like any other (kb/design-self-scheduled-
            # thoughts.md). Runs every tick, busy or idle.
            _fire_due_schedules(repo_root, brr_dir, inbox_dir, cfg)

            # Spawn one thought when idle and work is pending. Events that
            # arrive while a thought runs stay pending — the living agent
            # picks them up at a plan boundary (multi-response), or the
            # next spawn handles them. Reload also holds dispatch so the
            # slot can drain.
            if current is None and not reload_requested:
                pending = protocol.list_pending(inbox_dir)
                if pending:
                    event = pending[0]
                    eid = event["id"]
                    print(f"[brr] processing: {eid}")
                    protocol.set_status(event, "processing")
                    current = pool.submit(
                        _run_worker_and_finalize,
                        event, repo_root, responses_dir, cfg, max_retries,
                    )

            time.sleep(_SCAN_INTERVAL)

            for t in gate_threads:
                if not t.is_alive():
                    print(f"[brr] warning: gate thread {t.name} died")

    finally:
        # Shutdown requested (signal): kill the in-flight runner so we
        # reclaim the slot promptly instead of waiting out its (long,
        # possibly extended) budget, then drain the thought.
        if current is not None and not current.done():
            if runner.kill_active():
                print("[brr] shutdown: terminated in-flight runner")
        pool.shutdown(wait=True, cancel_futures=False)
        _clear_pid(brr_dir)
        print("[brr] daemon stopped")
