"""Daemon — reflex loop that scans the inbox, wakes the agent, pushes results.

The daemon is a single foreground process (``brr up``) and a deliberately
thin **reflex** layer: it does as little orchestration as possible and
leaves judgement to the agent it wakes. It:

1. Starts configured gate threads (each gate polls its own channel).
2. Scans ``.brr/inbox/`` for pending events on a timer.
3. Runs **single-flight** — one *thought* at a time. When idle and work
   is pending it spawns one worker; new events that arrive mid-thought are
   surfaced to the living agent through
   ``outbox/<event>/portal-state.json`` / ``inbox.json`` and either get
   folded in at plan boundaries or wait for the next spawn.
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
import hashlib
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
from . import facets
from . import forge_state
from . import forges
from . import claude_status
from . import gitops
from . import hooks as hooks_mod
from . import presence
from . import prompts
from . import codex_status
from . import protocol
from . import run_context
from . import runner
from . import runner_quota
from . import schedule as schedule_mod
from . import sync
from . import updates
from . import worktree
from .run import Run

_SCAN_INTERVAL = 3
_BUILTIN_GATES = ["telegram", "slack", "github", "cloud"]
# Burst coalescing. When a burst is already queued (≥2 pending events) and
# the slot is idle, hold dispatch briefly so the whole burst lands in one
# wake instead of spawning a fresh thought per fragment. A lone pending
# event never waits — debounce only spends latency where coalescing repays
# it. ``burst_window`` is the quiet gap that ends a burst; ``burst_max_wait``
# caps how long the oldest event waits so a steady trickle can't starve.
# Overridable via ``.brr/config`` (``dispatch.burst_window_seconds`` /
# ``dispatch.burst_max_wait_seconds``); a 0 window disables coalescing.
# See kb/design-run-event-model.md Q2 (re-wake debounce) and #128.
_BURST_WINDOW_DEFAULT = 1.5
_BURST_MAX_WAIT_DEFAULT = 12.0
# When a run fails before it can fold a settled burst, the lead event gets
# the terminal failure note and the siblings would otherwise become one
# failure wake each. Defer those siblings briefly; a fresh event can still
# wake the resident and show them in the live inbox.
_FAILURE_DEFER_SECONDS_DEFAULT = 300.0
# Cadence for the run-time heartbeat packet. 30s is short enough that
# the chat card visibly bumps elapsed time during the long "running"
# phase, and far below Telegram's edit rate ceiling (~30/sec/chat).
_HEARTBEAT_INTERVAL = 30.0
# Sub-heartbeat poll cadence for the runner hooks back-channel flush signal
# (``.flush``, dropped by ``brr hook post-tool``). The heartbeat itself
# stays at 30s; this only governs how fast the daemon notices the signal
# and drains the outbox in response, so a mid-thought reply lands promptly
# instead of waiting out the tick. See kb/design-runner-back-channel.md.
_FLUSH_POLL_INTERVAL = 1.0
_LIVE_INBOX_NAME = "inbox.json"
_LIVE_PORTAL_STATE_NAME = "portal-state.json"
# Agent-owned card narration: the resident writes this control dotfile
# in its outbox; the daemon drains it on each heartbeat into a
# ``card_composed`` packet and the gate re-renders the live card. See
# ``kb/design-managed-delivery.md`` for the relay-not-store stance the
# seam preserves (the daemon stays the renderer; brnrd still only edits
# a card it does not author or store).
_CARD_CONTROL_NAME = ".card"
# Soft cap on the agent narration the daemon will accept from ``.card``.
# Same intent as ``_format_agent_note``'s render cap: keep a runaway
# resident from flooding a thread. Excess bytes are truncated.
_CARD_CONTROL_MAX_BYTES = 4096
_INTERNAL_EVENT_SOURCES = {"schedule"}


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
    task: Run,
) -> None:
    """Publish the run's branch to its remote, if there are commits.

    The publish kernel:

    - The agent leaves work on a branch. ``run.meta["publish_branch"]``
      names it (set by ``WorktreeEnv.finalize``).
    - Normally the agent starts on ``target_branch`` (set up by
      ``WorktreeEnv.prepare``) and commits there, so ``publish_branch``
      and ``target_branch`` are the same and this is a plain push.
    - If the agent switched to a different branch, that branch is
      published as-is.
    - Refspec fallback: if ``publish_branch`` still diverges from
      ``target_branch`` (e.g. the agent left the worktree on the
      ``brr/<run-id>`` placeholder), push via a refspec
      ``brr/<run-id>:refs/heads/<target>`` so the daemon never has
      to update the local target ref.
    - When the source ref equals ``target_branch`` AND
      ``run.meta["expected_remote_oid"]`` is set AND the local source
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

    expected = task.meta.get("target_branch") or None
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
            "run_id": task.id,
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
                    run_id=task.id,
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
    worth failing the run over.
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
    brr_dir: Path, runs_dir: Path, task: Run,
) -> None:
    """Remove every trace dir the run accumulated on a clean ``done``.

    Symmetric with worktree and container cleanup: when the run
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
    task.save(runs_dir)


# ── Sync hook helpers ────────────────────────────────────────────────


def _branches_to_refresh(repo_root: Path, event: dict) -> list[str]:
    """Return local branch names worth refreshing before this run.

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
) -> Run:
    """Run the runner for a single event, with retries.

    Creates a Run from the event, persists it to .brr/runs/<run-id>/run.md,
    derives the conversation key, and tracks status throughout
    execution. Returns the Run.

    Every update packet rides through the local ``emit`` closure so
    ``conversation_key`` and ``event_id`` are populated automatically.
    Per-event-pipeline conversation routing relies on that — see
    ``kb/subject-daemon.md``.
    """
    eid = event["id"]
    brr_dir = gitops.shared_brr_dir(repo_root)
    runs_dir = brr_dir / "runs"
    runner_name = runner.resolve_runner(repo_root)
    failure_defer_seconds = float(
        cfg.get(
            "dispatch.failure_defer_seconds",
            _FAILURE_DEFER_SECONDS_DEFAULT,
        )
    )

    conv_key = conversations.conversation_key_for_event(event) or ""
    correspondent_key = conversations.correspondent_key_for_event(event) or ""
    origin_message_key = conversations.origin_message_key_for_event(event) or ""
    duplicate_event = conversations.find_event_by_origin_message(
        brr_dir, origin_message_key, exclude_event_id=eid,
    )
    emit = _WorkerEmit(brr_dir, conv_key, eid)

    if conv_key:
        conversations.append_event(brr_dir, conv_key, event)
        emit("event_received", event_id=eid, source=event.get("source", ""))

    if duplicate_event:
        task = Run.from_event(event, cfg)
        task.conversation_key = conv_key
        task.status = "done"
        if correspondent_key:
            task.meta["correspondent_key"] = correspondent_key
        task.meta["deduplicated_origin_message_key"] = origin_message_key
        prior_event_id = str(duplicate_event.get("event_id") or "").strip()
        prior_conversation = str(duplicate_event.get("conversation_key") or "").strip()
        if prior_event_id:
            task.meta["deduplicated_by_event_id"] = prior_event_id
        if prior_conversation:
            task.meta["deduplicated_by_conversation_key"] = prior_conversation
        task.save(runs_dir)
        emit("run_created", run_id=task.id, event_id=eid, env=task.env)
        if conv_key:
            conversations.append_run(
                brr_dir, conv_key,
                run_id=task.id, event_id=eid,
                env=task.env, status=task.status,
            )
        body = _deduplicated_event_body()
        resp_path = protocol.response_path(responses_dir, eid)
        protocol.write_response(responses_dir, eid, body)
        _record_response_artifact(emit, task, resp_path)
        _set_event_status_if_present(event, "done")
        emit("finalizing", run_id=task.id, stage="deduplicated")
        emit("done", run_id=task.id, event_id=eid, publish_status="deduplicated")
        return task

    # Refresh local refs before resolving the branch plan so the run
    # seeds from a current view of the world. Computing target_branches
    # off the raw event (rather than the resolved plan) avoids a chicken-
    # and-egg loop and lets a future github-gate event for a PR comment
    # name its head branch via ``branch_target`` for free.
    sync_targets = _branches_to_refresh(repo_root, event)
    sync_result = sync.refresh_before_run(
        repo_root, target_branches=sync_targets, cfg=cfg,
    )

    branch_plan = branching.resolve_publish_plan(repo_root, event, cfg)

    task = Run.from_event(event, cfg)
    task.conversation_key = conv_key
    if correspondent_key:
        task.meta["correspondent_key"] = correspondent_key
    task.save(runs_dir)

    if conv_key:
        sync_summary = sync.render_summary(sync_result)
        if sync_summary or sync_result.error:
            emit(
                "synced",
                run_id=task.id,
                event_id=eid,
                summary=sync_summary,
                ff_branches=dict(sync_result.ff_branches),
                skipped=dict(sync_result.skipped),
                error=sync_result.error,
            )

    emit("run_created", run_id=task.id, event_id=eid, env=task.env)

    # Record this thought in the presence registry so overlapping thoughts
    # (ad-hoc sessions, a second daemon) can see who's on which stream and
    # avoid colliding on the same work (kb/design-agent-dominion.md §4).
    # Best-effort: presence is a hint, never a gate. Deregistered in
    # _run_worker_and_finalize's finally; the heartbeat closure refreshes it.
    presence_id: str | None = None
    try:
        presence_id = presence.register(
            brr_dir, kind="daemon", stream=conv_key, run_id=task.id,
        )["id"]
        task.meta["presence_id"] = presence_id
    except OSError:
        presence_id = None

    task.update_status("running", runs_dir)
    resp_path = protocol.response_path(responses_dir, eid)
    # Per-event drop zone for interim responses the resident ships
    # mid-flight (the multi-response protocol, kb/design-multi-response.md).
    # Created up front so the agent can write to it the moment it wakes.
    outbox_dir = brr_dir / "outbox" / eid
    outbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir = brr_dir / "inbox"

    print(f"[brr] run {task.id} (event {eid}): env={task.env}")

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
        print(f"[brr] run {task.id}: env setup failed: {e}")
        task.update_status("error", runs_dir)
        _write_terminal_failure_response(
            emit,
            task,
            event,
            responses_dir,
            resp_path,
            f"environment setup failed: {e}",
        )
        _defer_pending_siblings_after_failure(
            inbox_dir,
            lead_event_id=eid,
            run_id=task.id,
            seconds=failure_defer_seconds,
        )
        emit("failed", run_id=task.id, stage="env", error=str(e))
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
    # null proxy and short-circuit. Never gates the run — every failure
    # mode is swallowed here so a probe bug can't fail a run.
    try:
        from . import ergonomics
        ergonomics.probe_run_prep(
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
        run_id=task.id,
        env=task.env,
        branch_name=branch_name,
        seed_ref=branch_plan.seed_ref,
        target_branch=branch_plan.target_branch,
        branch_source=branch_plan.source,
    )

    if conv_key:
        conversations.append_run(
            brr_dir, conv_key,
            run_id=task.id, event_id=eid,
            env=task.env, status=task.status,
            branch_name=branch_name,
            seed_ref=branch_plan.seed_ref,
            target_branch=branch_plan.target_branch,
            branch_source=branch_plan.source,
            host_context_branch=branch_plan.host_context_branch,
        )

    history_groups = (
        conversations.write_grouped_history_files(
            brr_dir, brr_dir / "runs" / task.id / "history",
            conv_key, correspondent_key,
        )
        if conv_key else []
    )
    communication_snapshot = (
        conversations.build_communication_snapshot(
            brr_dir,
            conv_key,
            correspondent_key,
            event_id=eid,
            run_id=task.id,
            recent_limit=prompts.RECENT_CONVERSATION_MAX,
            history_groups=history_groups,
        )
        if conv_key else None
    )
    if communication_snapshot is not None:
        # Forge-state facet (co-maintainer §5, #113): the resident's
        # in-flight worktrees/branches and the issues/PRs in play, built
        # network-free from local git + conversation keys.
        forge_facet = forge_state.build_forge_state(
            repo_root,
            related_threads=communication_snapshot.get("related_threads"),
            current_thread=conv_key,
            current_run_id=task.id,
            current_event_meta=event,
        )
        if forge_facet:
            communication_snapshot["forge"] = forge_facet
    recent_conversation = (
        communication_snapshot.get("recent_turns", [])
        if communication_snapshot else []
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
        if e.get("run_id") != task.id
    ]

    context_path = run_context.write_context_file(
        brr_dir,
        task,
        event,
        env_ctx,
        recent_conversation=recent_conversation,
        communication_snapshot=communication_snapshot,
        history_groups=history_groups,
        event_body=event_body_for_prompt,
    )
    task.meta["context_path"] = str(context_path)
    task.save(runs_dir)

    trace_dirs: list[str] = []
    emit(
        "run_started",
        run_id=task.id,
        branch=branch_name,
        seed_ref=branch_plan.seed_ref,
        target_branch=branch_plan.target_branch,
        env=task.env,
        runner=runner_name,
    )
    seen_containers: set[str] = set()
    last_failure: dict[str, object] | None = None
    output_stats = {"current": 0, "other": 0, "outbound": 0}
    prompt_diffense = prompts.diffense_emit_enabled(cfg)
    quota_summary = runner_quota.describe_runner_quota(runner_name, cfg, brr_dir)
    # Liveness budget: the heartbeat enforces this soft, agent-extensible
    # deadline; the runner's communicate() backstops at the hard cap. The
    # agent extends it by writing the keepalive control dotfile in its
    # outbox (skipped by the drain — see _drain_outbox).
    budget_seconds = runner.runner_timeout(cfg)
    hard_cap_seconds = max(budget_seconds * 4, budget_seconds + 3600)
    keepalive_path = outbox_dir / ".keepalive"
    card_path = outbox_dir / _CARD_CONTROL_NAME
    # Runner boundary back-channel flush signal: a stream driver or native
    # hook touches this dotfile to ask the daemon to drain now. Same host dir
    # the runner writes BRR_OUTBOX_DIR into (bind-mounted for container envs),
    # so the daemon reads the signal the boundary mechanism wrote. The signal
    # only asks; the daemon stays the sole drainer (see _drain_outbox / the
    # design doc).
    flush_path = outbox_dir / hooks_mod.FLUSH_SIGNAL_NAME
    card_state: dict[str, str] = {}
    run_started_monotonic = time.monotonic()
    _write_live_portal_state(
        outbox_dir,
        inbox_dir,
        eid,
        task,
        phase="preparing",
        runner_name=runner_name,
        budget_seconds=budget_seconds,
        hard_cap_seconds=hard_cap_seconds,
        keepalive_path=keepalive_path,
        card_state=card_state,
        output_stats=output_stats,
        start_monotonic=run_started_monotonic,
        work_dir=run_root,
        quota_summary=quota_summary,
    )
    # Native hook config is opt-in through a profile's explicit ``hooks:``
    # field — brr never infers hooks from the runner name. A profile with no
    # ``hooks:`` field uses the heartbeat-polled fallback (outbound flush, no
    # inbound injection).
    declared_hooks_flavour = runner.profile_hooks_flavour(runner_name, repo_root)
    hooks_flavour = declared_hooks_flavour or runner_name
    runner_env = {
        "BRR_RUN_ID": task.id,
        "BRR_EVENT_ID": eid,
        "BRR_RUNNER": hooks_flavour,
        "BRR_RESPONSE_PATH": str(env_ctx.response_path_env),
        "BRR_CONTEXT_PATH": str(context_path),
        "BRR_PORTAL_STATE": str(
            (env_ctx.outbox_env or outbox_dir) / _LIVE_PORTAL_STATE_NAME
        ),
    }
    if env_ctx.outbox_env:
        runner_env["BRR_OUTBOX_DIR"] = str(env_ctx.outbox_env)
        runner_env["BRR_INBOX_PATH"] = str(env_ctx.outbox_env / _LIVE_INBOX_NAME)

    # Tier 2 native hooks: install per-run hook config only for profiles that
    # explicitly declare a hook flavour. Two mechanisms by flavour — a settings
    # file written into the worktree (claude), or config-override argv injected
    # into the runner command (codex). Runners with no ``hooks:`` field degrade
    # to the heartbeat-polled portal model.
    extra_runner_args: list[str] = []
    if declared_hooks_flavour == "codex":
        if hooks_mod.codex_hook_capability():
            extra_runner_args = hooks_mod.codex_hook_args()
            emit(
                "hooks_installed",
                run_id=task.id,
                event_id=eid,
                flavour=declared_hooks_flavour,
                path="<argv -c hooks.*>",
            )
            print(f"[brr] worker {eid}: installed codex hook config via argv")
    elif (
        declared_hooks_flavour
        and hooks_mod.hook_capability(declared_hooks_flavour, run_root)
    ):
        hook_config_path = hooks_mod.install_hook_config(
            declared_hooks_flavour, run_root
        )
        if hook_config_path is not None:
            emit(
                "hooks_installed",
                run_id=task.id,
                event_id=eid,
                flavour=declared_hooks_flavour,
                path=str(hook_config_path),
            )
            print(
                f"[brr] worker {eid}: installed "
                f"{declared_hooks_flavour} hook config at {hook_config_path}"
            )
    for attempt in range(1, max_retries + 2):
        if attempt == 1:
            prompt = prompts.build_daemon_prompt(
                task.body, eid, str(env_ctx.response_path_env), run_root,
                outbox_path=str(env_ctx.outbox_env) if env_ctx.outbox_env else None,
                run_id=task.id,
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
                communication_snapshot=communication_snapshot,
                pending_events=pending_events_snapshot,
                present=present_snapshot,
                event_body=event_body_for_prompt,
                budget_seconds=budget_seconds,
                runner_medium=runner_name,
                runner_quota=quota_summary,
                diffense=prompt_diffense,
            )
            # Persist the assembled prompt so "what did this wake see?" has
            # an honest answer even on successful runs (traces are cleaned up
            # on success; the run directory persists).
            run_context.write_prompt_file(brr_dir, task, prompt)
        else:
            prompt = prompts.build_daemon_prompt(
                f"Previous attempt printed no final reply on stdout. "
                f"Print your full response as the final stdout message.\n\n"
                f"Original run instruction: {task.body}",
                eid, str(env_ctx.response_path_env), run_root,
                outbox_path=str(env_ctx.outbox_env) if env_ctx.outbox_env else None,
                run_id=task.id,
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
                communication_snapshot=communication_snapshot,
                pending_events=pending_events_snapshot,
                present=present_snapshot,
                event_body=event_body_for_prompt,
                budget_seconds=budget_seconds,
                runner_medium=runner_name,
                runner_quota=quota_summary,
                diffense=prompt_diffense,
            )

        print(f"[brr] worker {eid}: attempt {attempt}")
        emit("attempt_started", run_id=task.id, event_id=eid, attempt=attempt)

        attempt_started_monotonic = time.monotonic()
        _write_live_portal_state(
            outbox_dir,
            inbox_dir,
            eid,
            task,
            phase="running",
            attempt=attempt,
            runner_name=runner_name,
            budget_seconds=budget_seconds,
            hard_cap_seconds=hard_cap_seconds,
            keepalive_path=keepalive_path,
            card_state=card_state,
            output_stats=output_stats,
            start_monotonic=run_started_monotonic,
            work_dir=run_root,
            quota_summary=quota_summary,
        )

        def _emit_heartbeat() -> None:
            # Drain first: promoting an interim response is the resident's
            # mid-run check-in, and the partial should reach the gate as
            # promptly as the heartbeat that observed the agent is alive.
            _drain_outbox(
                emit, task, responses_dir, eid, outbox_dir, inbox_dir,
                stats=output_stats,
            )
            _drain_agent_card(emit, task, eid, card_path, card_state)
            _write_live_inbox(outbox_dir, inbox_dir, eid)
            _write_live_portal_state(
                outbox_dir,
                inbox_dir,
                eid,
                task,
                phase="running",
                attempt=attempt,
                runner_name=runner_name,
                budget_seconds=budget_seconds,
                hard_cap_seconds=hard_cap_seconds,
                keepalive_path=keepalive_path,
                card_state=card_state,
                output_stats=output_stats,
                start_monotonic=run_started_monotonic,
                work_dir=run_root,
                quota_summary=quota_summary,
            )
            if presence_id:
                presence.heartbeat(brr_dir, presence_id)
            elapsed = int(time.monotonic() - attempt_started_monotonic)
            emit(
                "heartbeat",
                run_id=task.id,
                attempt=attempt,
                elapsed_seconds=elapsed,
            )
            _emit_new_containers(emit, task.id, env_ctx, seen_containers)

        def _emit_flush() -> None:
            # Event-driven drain fired by the boundary back channel's .flush signal
            # (chunk 3 of the back channel): push the just-written outbox
            # file / card to the gate promptly, then refresh the live inbox
            # + portal-state the next boundary reads back for injection. Lighter
            # than _emit_heartbeat — no heartbeat packet / presence ping, so
            # a tool-boundary flush doesn't spam the chat card.
            _drain_outbox(
                emit, task, responses_dir, eid, outbox_dir, inbox_dir,
                stats=output_stats,
            )
            _drain_agent_card(emit, task, eid, card_path, card_state)
            _write_live_inbox(outbox_dir, inbox_dir, eid)
            _write_live_portal_state(
                outbox_dir,
                inbox_dir,
                eid,
                task,
                phase="running",
                attempt=attempt,
                runner_name=runner_name,
                budget_seconds=budget_seconds,
                hard_cap_seconds=hard_cap_seconds,
                keepalive_path=keepalive_path,
                card_state=card_state,
                output_stats=output_stats,
                start_monotonic=run_started_monotonic,
                work_dir=run_root,
                quota_summary=quota_summary,
            )

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
                env=runner_env,
                extra_runner_args=extra_runner_args,
            ),
            cfg=cfg,
            trace=True,
            on_heartbeat=_emit_heartbeat,
            on_flush=_emit_flush,
            flush_path=flush_path,
            budget_seconds=budget_seconds,
            hard_cap_seconds=hard_cap_seconds,
            keepalive_path=keepalive_path,
        )
        _emit_new_containers(emit, task.id, env_ctx, seen_containers)
        # Final drain after the runner returns: catch interim responses
        # written between the last heartbeat and exit, before finalize.
        _drain_outbox(
            emit, task, responses_dir, eid, outbox_dir, inbox_dir,
            stats=output_stats,
        )
        _drain_agent_card(emit, task, eid, card_path, card_state)
        _write_live_inbox(outbox_dir, inbox_dir, eid)
        _write_live_portal_state(
            outbox_dir,
            inbox_dir,
            eid,
            task,
            phase="finalizing",
            attempt=attempt,
            runner_name=runner_name,
            budget_seconds=budget_seconds,
            hard_cap_seconds=hard_cap_seconds,
            keepalive_path=keepalive_path,
            card_state=card_state,
            output_stats=output_stats,
            start_monotonic=run_started_monotonic,
            work_dir=run_root,
            quota_summary=quota_summary,
        )
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

        # Detect a fresh commit on the worktree branch before finalize runs
        # — finalize tears the worktree down on success, so this read has
        # to happen here. ``has_commits_beyond(seed_ref)`` follows HEAD,
        # which is what the agent ended on (initial branch or a switched
        # branch); both count as "the agent committed real work".
        try:
            has_new_commit = worktree.has_commits_beyond(
                run_root, branch_plan.seed_ref,
            )
        except Exception:
            has_new_commit = False
        satisfied, signal = _result_satisfied_delivery(
            result, output_stats, event, has_new_commit=has_new_commit,
        )
        if satisfied:
            print(f"[brr] worker {eid}: response ready ({signal})")
            if trace_dirs:
                task.meta["trace_dirs"] = ", ".join(trace_dirs)
            if _response_has_body(resp_path):
                _record_response_artifact(emit, task, resp_path)
            task.update_status("done", runs_dir)
            _set_event_status_if_present(event, "done")
            emit("finalizing", run_id=task.id, stage="done")
            # Per-branch lock around finalize: serialises publish on a
            # branch name so two pushers can't race it. Under single-flight
            # one daemon never contends here; the lock stays as cheap
            # insurance and a seam for a future concurrency revisit (see
            # kb/review-daemon-coherence-2026-06.md §4).
            with _branch_lock(branch_plan.target_branch):
                task = env_backend.finalize(env_ctx, task, runs_dir)
            _cleanup_traces_on_success(brr_dir, runs_dir, task)
            _remove_outbox(outbox_dir)
            _emit_preserved_containers(emit, task)
            # Payload carries the multi-thread delivery shape so the card
            # can reflect "delivered to N threads" / "sent N out-of-bound"
            # / "no reply — committed work" instead of collapsing
            # everything to a single current-thread reply (§8 re-alignment).
            emit(
                "done",
                run_id=task.id,
                event_id=eid,
                publish_branch=task.meta.get("publish_branch"),
                publish_status=task.meta.get("publish_status"),
                success_signal=signal,
                replies_current=output_stats.get("current", 0),
                replies_other=output_stats.get("other", 0),
                outbound_messages=output_stats.get("outbound", 0),
                committed=has_new_commit,
            )
            return task

        retry_reason = result.retry_reason()
        will_retry = bool(retry_reason and attempt <= max_retries)
        attempt_payload: dict[str, object] = {
            "run_id": task.id,
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
                run_id=task.id,
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
    task.update_status("error", runs_dir)
    failure_reason = _failure_reason(last_failure, attempt)
    _write_terminal_failure_response(
        emit,
        task,
        event,
        responses_dir,
        resp_path,
        failure_reason,
    )
    _defer_pending_siblings_after_failure(
        inbox_dir,
        lead_event_id=eid,
        run_id=task.id,
        seconds=failure_defer_seconds,
    )
    # Safety net: salvage whatever the failed run left on its branch. On a
    # clean failure exit (timeout / runner error / quota exhaustion) the
    # agent often never reached its own commit+push, and WorktreeEnv.finalize
    # deliberately skips publish-outcome resolution for a non-done run — so
    # without this the branch is never pushed and in-flight edits sit
    # uncommitted in a preserved worktree, visible only on the host (the
    # 2026-06-22 quota incident). Commit leftovers and arm publish_branch so
    # the publish() tail carries the work to the remote. Best-effort; runs
    # before finalize so the publish_branch it sets survives finalize's save.
    _capture_worktree(task, env_ctx, branch_plan, cfg, runs_dir)
    # finalize first so any preserved branches / containers are recorded
    # on the run before the failure packet renders — the failure packet
    # is what gates see last, so its payload must be the canonical
    # explanation.
    emit("finalizing", run_id=task.id, stage="failed")
    with _branch_lock(branch_plan.target_branch):
        task = env_backend.finalize(env_ctx, task, runs_dir)
    _remove_outbox(outbox_dir)
    _emit_preserved_containers(emit, task)
    # Classify the failure for the card: §6 says the user owns the runner
    # (critical infra brr doesn't control) so an operational failure
    # — runner timeout, non-zero exit, env/setup error — renders distinctly
    # from a "I ran out of attempts" silent partial. ``failure_kind`` reads:
    #   timed_out → "timed_out"  (runner.communicate hit the hard cap)
    #   exit_code → "runner_error" (subprocess returned non-zero)
    #   ok but no signal → "no_output" (runner ran clean but emitted
    #     nothing on any thread, didn't commit, can't declare noop —
    #     should be rare once #126's full noop affordance lands)
    #   no_output is the clean-exit-but-no-signal case: ``last_failure`` is
    #   None (the runner never recorded a failure), yet the run produced no
    #   reply on any thread and no commit. Any recorded ``last_failure``
    #   that isn't a timeout is a non-zero / artifact-missing runner error.
    failure_kind = "no_output"
    if last_failure:
        failure_kind = (
            "timed_out" if last_failure.get("timed_out") else "runner_error"
        )
    failed_payload: dict[str, object] = {
        "run_id": task.id,
        "event_id": eid,
        "stage": "run",
        "attempts": attempt,
        "failure_kind": failure_kind,
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

    The file is a control dotfile in the run outbox carrying one line:
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
    on_flush=None,
    flush_path: Path | None = None,
    flush_interval: float = _FLUSH_POLL_INTERVAL,
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
    last_heartbeat = start
    # When a flush signal is in play, poll at the faster cadence so the
    # boundary-triggered drain lands promptly; the heartbeat itself still
    # fires only every *interval*. With no flush_path the loop keeps its
    # original single-cadence shape.
    poll = min(interval, flush_interval) if flush_path is not None else interval
    deadline_killed = False
    while worker.is_alive():
        worker.join(timeout=poll)
        if not worker.is_alive():
            break
        # Event-driven flush: the runner boundary mechanism touched the
        # signal file; consume it and drain now instead of at the next tick.
        if flush_path is not None and flush_path.exists():
            try:
                flush_path.unlink()
            except OSError:
                pass
            if on_flush is not None:
                try:
                    on_flush()
                except Exception:
                    pass
        if time.monotonic() - last_heartbeat < interval:
            continue
        last_heartbeat = time.monotonic()
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
    run_id: str,
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
            run_id=run_id,
            env=env_ctx.name,
            container=cid,
        )


def _emit_preserved_containers(
    emit: _WorkerEmit,
    task: Run,
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
    emit("container_preserved", run_id=task.id, containers=containers)


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

    The file sits in the run outbox because that directory is already
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


def _iso_utc(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    except (OverflowError, OSError, ValueError):
        return None


def _outbox_message_files(outbox_dir: Path | None) -> list[str]:
    if not outbox_dir or not outbox_dir.exists():
        return []
    control_names = {_LIVE_INBOX_NAME, _LIVE_PORTAL_STATE_NAME}
    try:
        entries = sorted(
            (p for p in outbox_dir.iterdir() if p.is_file()),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )
    except OSError:
        return []
    names: list[str] = []
    for path in entries:
        if (
            path.suffix == ".tmp"
            or path.name.startswith(".")
            or path.name in control_names
        ):
            continue
        names.append(path.name)
    return names


def _keepalive_state(keepalive_path: Path | None) -> dict[str, object]:
    exists = bool(keepalive_path and keepalive_path.exists())
    until = _keepalive_until(keepalive_path)
    status = "absent"
    if exists and until is None:
        status = "unparseable"
    elif until is not None:
        status = "active" if until > time.time() else "expired"
    return {"status": status, "until": _iso_utc(until)}


def _collect_levels(
    runner_name: str | None, outbox_dir: Path | None
) -> tuple[dict[str, object] | None, "frozenset[str] | bool"]:
    """Pick the level snapshot + wired-slot set for *runner_name*'s vessel.

    Each medium exposes its quota/context (and, for Claude, spend) through a
    different head-less seam, so the level *source* is per-vessel:

    - **codex** — the session rollout file carries ``rate_limits`` (5h + weekly
      subscription quota) and ``model_context_window`` on every ``token_count``
      event, read live by :mod:`codex_status`. No dollar-spend gauge, so
      ``spend`` is deliberately not collected.
    - **claude** — the final ``--output-format json`` result, normalized by
      :mod:`claude_status` after the runner exits. It carries spend + context
      accounting but no subscription quota/reset windows, so this is a terminal
      accounting source, not mid-thought quota guidance.

    Returns ``(levels, wired_slots)`` for :func:`facets.build`. ``wired_slots``
    is the set of level slots whose collector exists (so an empty slot reads
    ``absent`` not ``unimplemented``); media with no collector return ``False``.
    """
    if codex_status.supported(runner_name):
        return codex_status.load_levels(), frozenset(codex_status.COLLECTED_SLOTS)
    if claude_status.supported(runner_name):
        return claude_status.load_snapshot(outbox_dir), frozenset(
            claude_status.COLLECTED_SLOTS
        )
    return None, False


def _resources_facet(
    quota_summary: str | None,
    *,
    levels: dict[str, object] | None = None,
    levels_collector: "bool | frozenset[str]" = False,
    branch: str | None = None,
    pr_number: str | None = None,
) -> dict[str, object]:
    """Operator-facing 'work status' the running resident can read.

    Thin wrapper over :func:`facets.build`, the single definition of the facet
    schema (``kb/design-resident-boundary.md`` §1 — "by schema, not by
    convention"). The schema, the three-state honesty, and the per-vessel level
    asymmetry all live in ``facets``; this keeps the daemon's construction call
    in one place so the JSON snapshot, the woven hook line, and ``brr portal
    state`` can never drift on which facets they carry.
    """
    return facets.build(
        quota_summary=quota_summary,
        levels=levels,
        levels_collector=levels_collector,
        branch=branch,
        pr_number=pr_number,
    )


def _scm_facet(
    work_dir: Path | None, branch: str | None
) -> dict[str, object]:
    """Local SCM posture for the run worktree: unpushed + modified counts.

    Cheap, local, failure-safe (the underlying helpers yield 0 on any git
    error). Surfaced by the boundary back channel at the closeout boundary so a
    wake that forgot to commit/push sees "N commit(s) not pushed, M modified
    file(s)" before it ends — the lived gap that motivated this (a wake closed
    out leaving its branch unpushed). ``known`` is False when there is no
    readable worktree, so the channel can stay silent rather than claim a
    clean tree it never inspected.
    """
    if not work_dir or not Path(work_dir).is_dir():
        return {"known": False, "branch": branch, "unpushed_commits": 0,
                "modified_files": 0}
    return {
        "known": True,
        "branch": branch,
        "unpushed_commits": worktree.unpushed_commit_count(Path(work_dir)),
        "modified_files": worktree.uncommitted_file_count(Path(work_dir)),
    }


def _change_token(payload: dict[str, object]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        # ``scm`` is excluded like ``elapsed_seconds`` below: modified-file
        # churn during normal editing should not bump the token and trip a
        # post-tool injection on every edit. Git posture is a boundary
        # signal — the seed (session-start) and stop hooks render it
        # unconditionally; mid-run it stays quiet.
        if key not in {"generated_at", "change_token", "scm"}
    }
    budget = stable.get("budget")
    if isinstance(budget, dict):
        stable["budget"] = {
            key: value for key, value in budget.items()
            if key != "elapsed_seconds"
        }
    encoded = json.dumps(
        stable,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _write_live_portal_state(
    outbox_dir: Path | None,
    inbox_dir: Path,
    current_event_id: str,
    task: Run,
    *,
    phase: str,
    attempt: int | None = None,
    runner_name: str | None = None,
    budget_seconds: float | None = None,
    hard_cap_seconds: float | None = None,
    keepalive_path: Path | None = None,
    card_state: dict[str, str] | None = None,
    output_stats: dict[str, int] | None = None,
    start_monotonic: float | None = None,
    work_dir: Path | None = None,
    quota_summary: str | None = None,
) -> Path | None:
    """Refresh the runner-visible daemon-state portal.

    ``inbox.json`` answers only which events are pending. This broader
    capsule answers "what needs my attention now?" for the running
    resident: input, delivery/card posture, budget state, and local SCM
    posture (unpushed commits / modified files) in one daemon-owned file
    refreshed on the heartbeat cadence.
    """
    if not outbox_dir:
        return None
    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
        events = _pending_events_for_agent(inbox_dir, current_event_id)
        stats = output_stats or {}
        card_text = (card_state or {}).get("last", "")
        pending_files = _outbox_message_files(outbox_dir)
        elapsed = (
            int(time.monotonic() - start_monotonic)
            if start_monotonic is not None else None
        )
        run_levels, run_level_slots = _collect_levels(runner_name, outbox_dir)
        payload: dict[str, object] = {
            "version": 1,
            "generated_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run": {
                "id": task.id,
                "event_id": current_event_id,
                "status": task.status,
                "phase": phase,
                "attempt": attempt,
                "env": task.env,
                "runner": runner_name,
                "branch": task.meta.get("branch_name"),
            },
            "attention": {
                "pending_event_count": len(events),
                "pending_outbox_file_count": len(pending_files),
                "needs_attention": bool(events or pending_files),
            },
            "inbound": {
                "current_event": current_event_id,
                "events": events,
            },
            "outbound": {
                "replies_current": int(stats.get("current", 0)),
                "replies_other": int(stats.get("other", 0)),
                "outbound_messages": int(stats.get("outbound", 0)),
                "any_sent": bool(
                    stats.get("current")
                    or stats.get("other")
                    or stats.get("outbound")
                ),
                "pending_outbox_files": pending_files,
            },
            "card": {
                "active": bool(card_text),
                "text": card_text,
            },
            "budget": {
                "elapsed_seconds": elapsed,
                "budget_seconds": budget_seconds,
                "hard_cap_seconds": hard_cap_seconds,
                "long_running": bool(
                    elapsed is not None
                    and budget_seconds is not None
                    and elapsed > budget_seconds
                ),
                "keepalive": _keepalive_state(keepalive_path),
            },
            "scm": _scm_facet(work_dir, task.meta.get("branch_name")),
            "resources": _resources_facet(
                quota_summary,
                # Per-vessel level source (see _collect_levels): Codex reads its
                # subscription quota + context window live from the session
                # rollout file; Claude gets terminal spend/context accounting
                # from result JSON. The wired-slot set decides whether an empty
                # slot reads 'absent' vs 'unimplemented'.
                levels=run_levels,
                levels_collector=run_level_slots,
                branch=task.meta.get("branch_name"),
                pr_number=task.meta.get("github_pr_number"),
            ),
        }
        payload["change_token"] = _change_token(payload)
        path = outbox_dir / _LIVE_PORTAL_STATE_NAME
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
    task: Run,
    responses_dir: Path,
    event_id: str,
    outbox_dir: Path | None,
    inbox_dir: Path | None = None,
    *,
    stats: dict[str, int] | None = None,
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
        # liveness budget), and the live JSON files are daemon-owned
        # control state. None are deliverable messages.
        if (
            fpath.suffix == ".tmp"
            or fpath.name.startswith(".")
            or fpath.name in {_LIVE_INBOX_NAME, _LIVE_PORTAL_STATE_NAME}
        ):
            continue
        try:
            text = fpath.read_text(encoding="utf-8")
        except OSError:
            continue
        # Tolerant parse: accept both a ``---``-fenced block and the common
        # resident slip of a leading ``event:`` / ``gate:`` line + ``---``
        # with no opening fence. The strict parser silently misrouted the
        # latter (leaked selector text, reply on the lead event); see
        # ``protocol.parse_outbox_message``.
        fm, body = protocol.parse_outbox_message(text)
        body = body.strip()
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
                if stats is not None:
                    stats["outbound"] = stats.get("outbound", 0) + 1
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
        if stats is not None:
            key = "other" if cross else "current"
            stats[key] = stats.get(key, 0) + 1
        if cross and target_event is not None:
            _set_event_status_if_present(target_event, "done")
        artifact_key = emit.conversation_key
        artifact_event_id = event_id
        if cross and target_event is not None:
            artifact_key = conversations.conversation_key_for_event(target_event) or ""
            artifact_event_id = target
            if artifact_key:
                conversations.append_event(emit.brr_dir, artifact_key, target_event)
        if artifact_key:
            conversations.append_artifact(
                emit.brr_dir, artifact_key,
                kind="interim_response",
                path=str(ppath),
                run_id=task.id,
                event_id=artifact_event_id,
                label=(f"reply:{target}" if cross else f"interim:{event_id}"),
                body=body,
            )
        emit(
            "interim_response",
            run_id=task.id,
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
    task: Run,
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
            run_id=task.id,
            event_id=event_id,
            label=f"outbound:{gate}",
            body=body,
        )
    return True


def _drain_agent_card(
    emit: _WorkerEmit,
    task: Run,
    event_id: str,
    card_path: Path | None,
    state: dict[str, str],
) -> bool:
    """Promote the agent-composed card narration into a ``card_composed`` packet.

    The resident owns its progress card's body via a single control file
    (``outbox/<eid>/.card``) — a dotfile, so it never enters the outbox
    drain as a deliverable message. On each heartbeat tick (and once more
    after the runner returns) the daemon reads the file; when its content
    has changed since the last emit, a ``card_composed`` packet is sent so
    the gate re-renders the live card with the agent's text. Removing or
    emptying the file emits one final empty packet so the narration
    cleanly withdraws.

    *state* is a tiny dict the worker owns for the life of the attempt; we
    stash the last-seen text under ``"last"`` so re-reading the same body
    is a no-op (no packet spam every 30s). Returns True when a packet was
    emitted, False on a no-op or unreadable file.

    The cap (``_CARD_CONTROL_MAX_BYTES``) bounds the daemon-side read;
    the gate's renderer applies a render-time cap of its own. Errors are
    swallowed — a card-control bug must never break a run.
    """
    if card_path is None:
        return False
    has_last = "last" in state
    if not card_path.exists():
        if not has_last or state["last"] == "":
            return False
        state["last"] = ""
        emit(
            "card_composed",
            run_id=task.id,
            event_id=event_id,
            text="",
        )
        return True
    try:
        raw = card_path.read_bytes()
    except OSError:
        return False
    if len(raw) > _CARD_CONTROL_MAX_BYTES:
        raw = raw[:_CARD_CONTROL_MAX_BYTES]
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return False
    body = text.strip()
    if has_last and state["last"] == body:
        return False
    state["last"] = body
    emit(
        "card_composed",
        run_id=task.id,
        event_id=event_id,
        text=body,
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
    task: Run,
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
        f"brr-home: capture working memory after run {task.id}",
        remote=remote,
        branch=branch,
        push=push and bool(remote),
    )
    if committed:
        print(f"[brr] dominion: captured working memory after {task.id}")


def _capture_worktree(
    task: Run,
    ctx,
    branch_plan,
    cfg: dict,
    runs_dir: Path,
) -> None:
    """Salvage a failed run's branch so its work isn't stranded locally.

    The work-branch counterpart to :func:`_capture_dominion`, fired on the
    give-up path. A killed/timed-out/quota-exhausted run usually never ran
    the agent's own commit+push, and :meth:`WorktreeEnv.finalize` resolves a
    publish outcome only for a ``done`` run — so on failure the branch never
    publishes and any uncommitted edits sit in a preserved worktree, visible
    only on the host. This:

    1. commits any uncommitted changes on the work branch (the "at least
       locally" floor), and
    2. arms ``task.meta["publish_branch"]`` so the publish() tail pushes the
       branch to the remote — but only when the branch carries real commits
       beyond the seed, so a run that failed before doing anything stays
       silent.

    Best-effort and gated by ``salvage.enabled`` (default on); a detached
    HEAD or unreadable tree is skipped. Runs before finalize so the
    publish_branch it sets survives finalize's ``task.save``.
    """
    if not bool(cfg.get("salvage.enabled", cfg.get("salvage_enabled", True))):
        return
    run_root = getattr(ctx, "cwd", None)
    if run_root is None:
        return
    run_root = Path(run_root)
    branch = worktree.current_branch(run_root)
    if not branch:
        # Detached HEAD — no branch to publish; finalize keeps the worktree
        # for forensic inspection.
        return
    try:
        if gitops.worktree_dirty(run_root):
            if gitops.commit_all(
                run_root,
                f"brr salvage: in-flight work from interrupted run {task.id}",
            ):
                print(f"[brr] salvage: committed in-flight work for {task.id}")
        seed_ref = getattr(branch_plan, "seed_ref", None)
        if seed_ref and not worktree.has_commits_beyond(run_root, seed_ref):
            return
        task.meta["publish_branch"] = branch
        task.meta["branch_name"] = branch
        task.save(runs_dir)
        print(f"[brr] salvage: arming publish of {branch} for failed {task.id}")
    except Exception as e:  # best-effort — never let salvage break the give-up path
        print(f"[brr] salvage: skipped for {task.id} ({e})")


def _record_response_artifact(
    emit: _WorkerEmit,
    task: Run,
    response_path: Path,
) -> None:
    """Index the response artifact on the conversation log."""
    label = f"response:{task.event_id}" if task.event_id else f"response:{task.id}"
    try:
        body = protocol.frontmatter_body(
            response_path.read_text(encoding="utf-8"),
        ).strip()
    except OSError:
        body = None
    if emit.conversation_key:
        conversations.append_artifact(
            emit.brr_dir, emit.conversation_key,
            kind="response",
            path=str(response_path),
            run_id=task.id,
            event_id=emit.event_id,
            label=label,
            body=body,
        )
    emit(
        "artifact_created",
        run_id=task.id,
        kind="response",
        path=str(response_path),
    )


def _event_requires_thread_delivery(event: dict) -> bool:
    """True when the originating event has a user-facing thread to close."""
    return str(event.get("source") or "") not in _INTERNAL_EVENT_SOURCES


def _response_has_body(path: Path) -> bool:
    try:
        return bool(protocol.frontmatter_body(
            path.read_text(encoding="utf-8"),
        ).strip())
    except OSError:
        return False


def _result_satisfied_delivery(
    result: "runner.RunnerResult",
    output_stats: dict[str, int],
    event: dict,
    *,
    has_new_commit: bool = False,
) -> tuple[bool, str]:
    """Return ``(satisfied, signal)`` — the success-signal kind that caught it.

    Aligns with the §6 co-maintainer model: a run succeeds when it produced
    an output event (a current-thread reply, a folded-in reply, or an
    out-of-bound gate send), or a new commit on the worktree branch, or the
    event is internal (schedule fire / dedup retire) and no thread reply is
    required. Stdout remains the common ``current_reply`` path, but it is no
    longer the *only* success signal — a run that committed work or
    answered a sibling thread is a successful run too.

    *signal* is one of ``current_reply | other_reply | outbound | commit |
    internal | ""`` (empty when not satisfied). The string surfaces on the
    ``done`` packet so renderers can name what the success was.
    """
    if not result.ok or result.missing_artifacts:
        return False, ""
    if result.has_response:
        return True, "current_reply"
    if output_stats.get("current", 0) > 0:
        return True, "current_reply"
    if output_stats.get("other", 0) > 0:
        return True, "other_reply"
    if output_stats.get("outbound", 0) > 0:
        return True, "outbound"
    if has_new_commit:
        return True, "commit"
    if not _event_requires_thread_delivery(event):
        return True, "internal"
    return False, ""


def _failure_reason(
    last_failure: dict[str, object] | None,
    attempts: int,
) -> str:
    if last_failure:
        detail = str(last_failure.get("error") or "").strip()
        exit_code = last_failure.get("exit_code")
        if last_failure.get("timed_out"):
            if detail:
                return f"runner timed out after {attempts} attempt(s): {detail}"
            return f"runner timed out after {attempts} attempt(s)"
        if detail:
            return f"runner failed after {attempts} attempt(s): {detail}"
        if exit_code is not None:
            return f"runner failed after {attempts} attempt(s) with exit code {exit_code}"
    return f"runner produced no reply after {attempts} attempt(s)"


def _terminal_failure_body(reason: str) -> str:
    return (
        "I couldn't complete this run.\n\n"
        f"brr is surfacing this because {reason}."
    )


def _deduplicated_event_body() -> str:
    return (
        "I already received this source message on another configured channel. "
        "No second run was started."
    )


def _write_terminal_failure_response(
    emit: _WorkerEmit,
    task: Run,
    event: dict,
    responses_dir: Path,
    response_path: Path,
    reason: str,
) -> bool:
    """Queue a terminal failure note for addressed events.

    The run record still stays ``error``; only the inbox event moves to
    ``done`` so the gate has a message to deliver and a cleanup signal.
    """
    if not _event_requires_thread_delivery(event):
        return False
    if _response_has_body(response_path):
        return False
    protocol.write_response(responses_dir, event["id"], _terminal_failure_body(reason))
    _record_response_artifact(emit, task, response_path)
    _set_event_status_if_present(event, "done")
    return True


def _format_utc_after(seconds: float) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + max(0.0, seconds)),
    )


def _defer_pending_siblings_after_failure(
    inbox_dir: Path,
    *,
    lead_event_id: str,
    run_id: str,
    seconds: float,
) -> int:
    """Brake sibling events after a terminal run failure.

    The current lead event receives the explicit failure note. Other
    pending events stay pending and visible to future wakes, but they are
    not eligible to become the next lead until ``defer_until`` passes.
    This is the first Q2-shaped failure brake for #128; per-run claim and
    run-keyed primary outbox remain separate slices.
    """
    if seconds <= 0:
        return 0
    defer_until = _format_utc_after(seconds)
    changed = 0
    for pending in protocol.list_pending(inbox_dir):
        if pending.get("id") == lead_event_id:
            continue
        if pending.get("status") != "pending":
            continue
        try:
            protocol.update_event_meta(
                pending,
                defer_until=defer_until,
                deferred_by_run=run_id,
                defer_reason="operational_failure",
            )
        except OSError:
            continue
        changed += 1
    return changed


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
) -> Run:
    """Run one event end-to-end and return the resulting Run.

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
            print(f"[brr] run {task.id}: failed")

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


# ── Burst coalescing (dispatch debounce) ────────────────────────────


def _event_mtime(event: dict) -> float:
    """File mtime of a pending event ≈ its arrival time. 0.0 when unknown,
    so an unstattable event reads as old and never holds the burst window."""
    path = event.get("_path")
    try:
        return path.stat().st_mtime
    except (OSError, AttributeError):
        return 0.0


def _burst_settle_delay(
    pending: list[dict],
    window: float,
    max_wait: float,
    now: float,
) -> float:
    """Seconds to hold dispatch so an arriving burst coalesces into one
    wake; ``0.0`` means dispatch now.

    Holds only when a burst is *already* forming (≥2 pending events), so a
    lone message never pays debounce latency. While events keep arriving
    less than *window* apart the burst is still landing and we wait — until
    either the inbox goes quiet for *window* or the oldest event has waited
    *max_wait* (the anti-starvation cap). A *window* of 0 disables
    coalescing. See kb/design-run-event-model.md Q2 (re-wake debounce) and
    #128: this is the first behavioural slice of the run/event model — one
    wake reads the settled burst and folds it, instead of the daemon
    spawning one thought per fragment.
    """
    if window <= 0 or len(pending) < 2:
        return 0.0
    mtimes = [_event_mtime(ev) for ev in pending]
    quiet_for = now - max(mtimes)
    waited = now - min(mtimes)
    if quiet_for >= window or waited >= max_wait:
        return 0.0
    return min(window - quiet_for, max_wait - waited)


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
    per-run worktree/branch isolation and partitioned state survive from
    the former parallel design — see ``kb/subject-daemon.md`` and
    ``kb/design-agent-dominion.md`` §4.

    Traces are always written and worktrees/containers are kept on
    failure (or when uncommitted files are left behind) but discarded
    on clean success — there is no operator-facing debug switch.

    *dev_reload* enables the brr-development re-exec watcher.  When
    ``None``, falls back to the ``dev_reload`` key in ``.brr/config``.
    Reload waits until the in-flight thought drains so no running run
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
    burst_window = float(
        cfg.get("dispatch.burst_window_seconds", _BURST_WINDOW_DEFAULT))
    burst_max_wait = float(
        cfg.get("dispatch.burst_max_wait_seconds", _BURST_MAX_WAIT_DEFAULT))
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

    wake = protocol.inbox_wake()
    try:
        while running:
            # Consume any pending wake signal up front, before we read the
            # inbox below: a set that lands after this clear (a gate
            # enqueuing mid-iteration) keeps the flag set, so the wait at
            # the bottom returns promptly and the next pass picks it up —
            # no event is missed, and a busy iteration can't spin.
            wake.clear()

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
                    # The thought crashed before returning a Run; the
                    # operator sees the traceback in the daemon console.
                    print(f"[brr] thought crashed: {exc}")
                current = None

            # Quiescent reload: only re-exec between thoughts, so a
            # running run can't have its process replaced underneath it.
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
            burst_hold = 0.0
            if current is None and not reload_requested:
                pending = protocol.list_dispatchable(inbox_dir)
                if pending:
                    burst_hold = _burst_settle_delay(
                        pending, burst_window, burst_max_wait, time.time())
                    if burst_hold <= 0:
                        # Dispatch the oldest as lead; the wake reads the
                        # whole settled burst and folds the rest in (the
                        # multi-response ``event:`` path), so a burst becomes
                        # one thought, not one spawn per fragment.
                        event = pending[0]
                        eid = event["id"]
                        extra = len(pending) - 1
                        suffix = f" (+{extra} pending)" if extra else ""
                        print(f"[brr] processing: {eid}{suffix}")
                        protocol.update_event_meta(
                            event,
                            defer_until=None,
                            deferred_by_run=None,
                            defer_reason=None,
                        )
                        protocol.set_status(event, "processing")
                        current = pool.submit(
                            _run_worker_and_finalize,
                            event, repo_root, responses_dir, cfg, max_retries,
                        )

            # Event-driven idle wait: block until a fresh in-process event
            # wakes us or the poll tick elapses, whichever comes first.
            # The tick still bounds latency for cross-process writers (the
            # ``brr run`` CLI) and time-based work (due schedules), which
            # don't set the signal. While holding a burst to settle, shorten
            # the wait to the remaining window so dispatch fires promptly
            # once it goes quiet — a fresh event also wakes us early and
            # extends the window on the next pass.
            wait_timeout = _SCAN_INTERVAL
            if burst_hold > 0:
                wait_timeout = min(wait_timeout, burst_hold)
            wake.wait(wait_timeout)

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
