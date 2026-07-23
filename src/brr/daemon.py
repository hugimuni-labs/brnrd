"""Daemon — reflex loop that scans the inbox, wakes the agent, pushes results.

The daemon is a single foreground process (``brnrd up``) and a deliberately
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
   The one deliberate control-event exception is runner-policy approval:
   approval/rejection replies are handled by the daemon before dispatch so a
   resident cannot silently rewrite its own runner-selection policy.
4. The worker owns the full pipeline for its event: runner invocation,
   retries, response capture, response release to gates, env finalize,
   and branch push.

There is no general command layer: every event either wakes the agent or
waits for the living agent, except for the narrow daemon-owned approval
grammar that applies runner-policy proposals.
Liveness is enforced from the heartbeat: each tick checks an
agent-extensible budget (``runner.timeout_seconds``, pushed out by a
keepalive the agent writes) and kills a runner that outlives it via
``runner.kill_matching``; the runner's own ``communicate`` timeout is the
final backstop if the heartbeat path wedges. ``brnrd down`` / SIGTERM flip
the loop flag and kill the in-flight runner, so the single-flight slot is
reclaimed promptly rather than waiting out a long budget.
"""

from __future__ import annotations

import collections
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from . import account
from . import branching
from . import config as conf
from . import conversations
from . import dev_reload as reload_mod
from . import dominion
from . import envs
from . import facets
from . import forge_pr_cache
from . import forge_state
from . import forges
from . import claude_status
from . import claude_usage
from . import gitops
from . import hooks as hooks_mod
from . import knowledge
from . import message_store
from . import portals
from . import presence
from . import prompts
from . import codex_status
from . import codex_usage
from . import protocol
from . import relics
from . import run_context
from . import run_ledger
from . import runner
from . import runner_failures
from . import runner_quota
from . import wake_request as wake_request_mod
from . import runner_select
from . import schedule as schedule_mod
from . import spending_plan
from . import sync
from . import transcript
from . import updates
from . import release_availability
from . import usage_samples
from . import worktree
from .run import Run, list_runs, run_manifest_path

class _RunnerRuntime(NamedTuple):
    """What resolving a runner profile yields for one attempt.

    Named rather than a bare tuple on purpose: this is unpacked at two call
    sites — the initial resolve and the *fallback* resolve after a runner
    failure — and the fallback path has no test coverage.  A bare tuple grown
    by one field silently breaks the untested site with a ValueError that only
    surfaces in production, mid-failure, which is the worst possible moment.
    Attribute access makes new fields free and arity drift impossible.
    """

    meta: dict[str, object] | None
    quota: str | None
    env: dict[str, str]
    extra_args: list[str]
    hooks_installed: bool


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
# #577: a dashboard wake-tap's claim window and staleness backstop.
# Overridable via ``.brr/config`` (``dispatch.wake_request_claim_window_seconds``
# / ``dispatch.wake_request_ttl_seconds``). See ``wake_request.py`` for the
# mechanism these gate.
_WAKE_REQUEST_CLAIM_WINDOW_DEFAULT = wake_request_mod.DEFAULT_CLAIM_WINDOW_S
_WAKE_REQUEST_TTL_DEFAULT = wake_request_mod.DEFAULT_TTL_S
_RUN_STATE_REAP_AFTER_SECONDS = 24 * 3600.0
# How often a live daemon re-sweeps both run-truth stores for zombies. See
# ``_sweep_zombie_runs``: boot-only made a data repair the user had to
# schedule by restarting.
_ZOMBIE_SWEEP_INTERVAL_SECONDS = 30 * 60.0
# First retention sweep after boot waits this long, so a daemon restarted in
# a tight loop never GCs on every boot; steady-state cadence comes from
# ``retention.sweep_interval_hours`` (default daily, 0 disables).
_RETENTION_FIRST_SWEEP_DELAY_SECONDS = 60 * 60.0
# How far back the exact-duplicate scan looks. A genuine re-delivery (one
# external message fanned to two configured channels) is near-simultaneous;
# anything older sharing an origin key is a coincidental id collision, not a
# duplicate, and squashing the new message loses it. Six hours is generous
# for the real case and still excludes the day/month-old collisions that
# caused silent message loss (2026-07-15). Override: `dispatch.dedup_window_seconds`.
_DEDUP_WINDOW_SECONDS_DEFAULT = 6 * 3600.0
# Cadence for the run-time heartbeat packet. 10s keeps the chat card
# visibly alive and is well below Telegram's edit rate ceiling
# (~30/sec/chat). The Claude usage PTY scrape (which can block ~18s)
# is off the flush critical path since the latency fix; it only fires
# once per 300s (its TTL) on the heartbeat path — unaffected by
# shortening this interval.
_HEARTBEAT_INTERVAL = 10.0
# Sub-heartbeat poll cadence for the runner hooks back-channel flush signal
# (``.flush``, dropped by ``brnrd hook post-tool``). The heartbeat itself
# stays at 10s; this only governs how fast the daemon notices the signal
# and drains the outbox in response, so a mid-thought reply lands promptly
# instead of waiting out the tick. See kb/design-runner-back-channel.md.
_FLUSH_POLL_INTERVAL = 1.0
# Daemon-owned floor under the prompt-level post-delivery linger. The runner
# can still keep the same thought alive with outbox + .keepalive; this dwell
# covers the common failure where the runner exits after a reply. It holds the
# single-flight slot briefly, renders an explicit attending card phase, and
# yields the moment any pending event appears.
_POST_DELIVERY_ATTEND_SECONDS_DEFAULT = 90.0
_POST_DELIVERY_ATTEND_POLL_INTERVAL = 1.0
# Quota-aware pacing floors (kb/design-director-loop.md §B1, decided
# 2026-07-04): below the low floor, `every:` schedule entries stretch their
# interval; below the critical floor, they stop firing this beat entirely.
# `at:` entries and anything gate-addressed are never bent by this policy.
_QUOTA_LOW_FLOOR_PCT_DEFAULT = 20.0
_QUOTA_CRITICAL_FLOOR_PCT_DEFAULT = 8.0
_QUOTA_STRETCH_FACTOR_DEFAULT = 3.0
# Names owned by ``portals`` — the one place the driver-side file contract
# is spelled, now that ``init`` plays driver for its own single wake (#507).
_LIVE_INBOX_NAME = portals.LIVE_INBOX_NAME
_LIVE_PORTAL_STATE_NAME = portals.LIVE_PORTAL_STATE_NAME
# Agent-owned run body: the resident writes this control dotfile
# in its outbox; the daemon drains it on each heartbeat into a
# ``card_composed`` packet and the gate re-renders the live card. See
# ``kb/design-managed-delivery.md`` for the relay-not-store stance the
# seam preserves (the daemon stays the renderer; brnrd still only edits
# a card it does not author or store).
_CARD_CONTROL_NAME = ".card"
# Agent-declared PR handle: the resident writes the PR number (or a full
# GitHub PR URL) here right after `gh pr create` succeeds mid-run. The
# `remote_scm` facet is deliberately network-free (`brr.facets` docstring —
# "derived from run metadata", never a live `gh pr view`), so before this
# file existed a PR created by the resident itself (as opposed to one a
# GitHub-sourced task already carried in `task.meta['github_pr_number']`)
# was invisible to the live portal for the rest of that same run — reported
# live, 2026-07-07 (a same-thread follow-up naming the exact gap from a
# prior run's own ergonomics note). Same shape as `.card`/`.keepalive`: a
# small control dotfile the daemon reads on the heartbeat cadence, never
# delivered as a chat message.
_PR_CONTROL_NAME = ".pr"
# Soft cap on the live projection the daemon accepts from ``.card``. The full
# body is copied at closeout; only the ``## Now`` projection rides packets.
_CARD_CONTROL_MAX_BYTES = 64 * 1024
# Staleness bar for the ``.card`` narration: the maintainer's own estimate
# (2026-07-05) for how long a blank-or-unchanged card can sit on the one
# surface a watching user sees before it reads as neglect rather than quiet
# work. Mirrors the pending-event framing fix the same day — an
# attention-worthy silence needs to surface as a signal, not stay ambient.
_CARD_STALE_SECONDS = 240
_INTERNAL_EVENT_SOURCES = {"schedule"}
_RUNNER_POLICY_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_RUNNER_POLICY_REPLY_RE = re.compile(
    r"^\s*(approve|approved|yes|reject|rejected|deny|denied|no)\s+"
    r"(?:runner[-_ ]?policy|policy)\s+([A-Za-z0-9_.-]{1,96})\b",
    re.IGNORECASE,
)
# Loom envelope Phase 2 (kb/design-multi-workstream-concurrency.md §"Named
# forks — round 2"): unlike runner-policy above, this reply never comes
# from a chat-typed approval — it's synthesized by
# ``brnrd.routers.config_approval.decide_core`` once the account owner
# clicks approve/reject on the brnrd.dev confirm URL, and arrives over the
# same cloud events long-poll any other message does
# (``_dispatchable_targets`` below).
_CONFIG_CHANGE_PROPOSAL_ID_RE = _RUNNER_POLICY_PROPOSAL_ID_RE
_CONFIG_CHANGE_REPLY_RE = re.compile(
    r"^\s*(approve|approved|yes|reject|rejected|deny|denied|no)\s+"
    r"config[-_ ]?change\s+([A-Za-z0-9_.-]{1,96})\b",
    re.IGNORECASE,
)
# Sub-decision 1: start narrow. Keep in lockstep with
# ``src/brnrd/routers/config_approval.py::ALLOWED_CONFIG_KEYS`` — the two
# packages ship separately (local daemon vs. hosted server) so this can't
# be a shared import; a mismatch just means one side rejects a proposal
# the other would have allowed, never an unapproved config write.
_CONFIG_CHANGE_ALLOWED_KEYS = {
    "spawn.max_concurrent",
    # Wake-context budget knobs (2026-07-11 context-shape audit): the
    # resident tuning the standing cost of its own injected blocks is
    # exactly the resident-proposes / operator-approves shape this path
    # exists for. Defaults live in prompts.py / dominion.py.
    "dominion.inject_budget_bytes",
    "dominion.plan_inject_budget_bytes",
    "dominion.ledger_inject_budget_bytes",
}

# Every allowlisted key today is integer-valued; validate at proposal time
# so an approval can never write a value that later crashes prompt
# assembly (``int(cfg.get(...))`` at wake build).
_CONFIG_CHANGE_INT_KEYS = set(_CONFIG_CHANGE_ALLOWED_KEYS)


@dataclass(frozen=True)
class _DispatchTarget:
    """One pending event plus the repo/runtime surface it should use."""

    event: dict
    repo_root: Path
    inbox_dir: Path
    responses_dir: Path
    repo_label: str


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


def _start_gates(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
    excluded: frozenset[str] = frozenset(),
    included: frozenset[str] | None = None,
) -> list[threading.Thread]:
    threads = []
    for name in _BUILTIN_GATES:
        if name in excluded or (included is not None and name not in included):
            continue
        try:
            from .gates import import_gate
            mod = import_gate(name)
        except ImportError:
            continue
        if not hasattr(mod, "is_configured") or not mod.is_configured(brr_dir):
            continue
        print(f"[brnrd] starting gate: {name}")
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
    if task.meta.get("root_kind") == "home":
        # Home is committed and pushed by the account-home capture net. It has
        # no per-run forge lane, even if stale metadata names a branch.
        return
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
        print(f"[brnrd] pushing {push_branch}...")
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


def publish_default_branch(repo_root: Path, task: Run) -> None:
    """Best-effort fast-forward push of the default branch after a run.

    Runs merge reviewed work into the repo's default branch in the
    shared checkout, but ``publish`` only carries per-run branches
    (``publish_branch``, set by ``WorktreeEnv.finalize``) — a merge to
    the default branch arms nothing, so it never left the machine.
    Found live 2026-07-22: origin/main was 11 commits (~7 hours) behind
    local main, every announced merge unpushed, and the gap
    self-concealed because each run's scm line said "N unpushed" while
    nobody owned the push.

    Deliberately env-agnostic: the first cut fired only for ``host``
    runs, and the very next morning three ``solitary``-env continuation
    runs merged reviewed PRs into the shared checkout's main through
    the repo mount — five commits stranded again (found live
    2026-07-22, #327/#61). The env of the run says nothing about
    whether the default branch moved; the branch state does, and this
    function already reads it.

    Fires only when ALL hold:

    - the run's root is not the account home (home commits belong to
      the account capture net);
    - the local default branch has commits the remote lacks;
    - the remote default branch is an **ancestor** of the local one — a
      plain fast-forward. A diverged remote is the operator's to
      reconcile: skip and print a ``[brnrd]`` marker line, mirroring the
      capture net's needs-sync discipline. Never force, never
      ``--force-with-lease`` here.

    Pushes through the same lane as ``publish`` / ``gitops.push_branch``
    (a plain ``git push`` in ``repo_root``, so the daemon's managed
    credential setup applies). Best-effort like the capture net: every
    failure is a log line, never an exception into finalization.
    """
    if task.meta.get("root_kind") == "home":
        # Home is committed and pushed by the account-home capture net.
        return
    try:
        branch = gitops.default_branch(repo_root)
        if not branch or branch == "HEAD":
            return
        remote = (
            gitops.branch_remote(repo_root, branch)
            or gitops.default_remote(repo_root)
        )
        if not remote:
            return
        remote_ref = f"{remote}/{branch}"
        if not gitops.rev_parse(repo_root, remote_ref):
            # No remote-tracking ref to compare against; creating the
            # remote branch is not this lane's job.
            return
        commits = _commits_between(repo_root, remote_ref, branch)
        if not commits:
            return
        if not gitops.is_ancestor(repo_root, remote_ref, branch):
            print(
                f"[brnrd] default-branch publish: {remote_ref} has diverged from "
                f"local {branch}; skipping push — reconcile by hand "
                f"(fetch / merge / push) in {repo_root}"
            )
            return
        print(
            f"[brnrd] pushing {branch} ({len(commits)} commit(s)) after "
            f"run {task.id}..."
        )
        with _branch_lock(branch):
            pushed = gitops.push_branch(
                repo_root, remote, branch, set_upstream=False,
            )
        if not pushed:
            print(
                f"[brnrd] default-branch publish: push of {branch} to {remote} was "
                f"rejected; the remote is the operator's to reconcile"
            )
    except Exception as exc:  # noqa: BLE001 - best-effort, never fatal
        print(f"[brnrd] default-branch publish: skipped on error: {exc}")


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


def _record_task_runner(
    task: Run,
    selected: "runner_select.RunnerProfile",
) -> None:
    """Persist the currently selected Runner/Core on the run manifest."""
    task.meta["runner_name"] = selected.name
    for key in (
        "runner_shell", "runner_core", "runner_class",
        "core_requested", "core_observed",
    ):
        task.meta.pop(key, None)
    task.meta["runner_shell"] = selected.shell
    if selected.model:
        # ``runner_core`` remains the compatibility field consumed by existing
        # run-state readers. The explicit field is the truthful one: before
        # attestation this is a request, not an observation.
        task.meta["runner_core"] = selected.model
        task.meta["core_requested"] = selected.model
    if selected.cost_class:
        task.meta["runner_class"] = selected.cost_class


def _quality_escalation_meta(
    repo_root: Path,
    runner_name: str | None,
) -> dict[str, object] | None:
    """Metadata for the stronger local Runner a quality respawn would target."""
    if not runner_name:
        return None
    proposed = runner.quality_escalation_runner(repo_root, runner_name)
    if not proposed:
        return None
    meta = runner.profile_metadata(proposed, repo_root) or {}
    return {
        "status": "known",
        "name": proposed,
        "model": str(meta.get("model") or "").strip() or None,
        "class": str(meta.get("class") or "").strip() or None,
        "provider": str(meta.get("provider") or "").strip() or None,
        "owner": str(meta.get("owner") or "user").strip() or "user",
        "cost_rank": meta.get("cost_rank"),
        "capability_score": meta.get("capability_score"),
        "capability_source": str(
            meta.get("capability_source") or ""
        ).strip() or None,
        "capability_freshness": str(
            meta.get("capability_freshness") or ""
        ).strip() or None,
    }


def _repo_label(
    repo_root: Path,
    event: dict | None = None,
    cfg: dict | None = None,
) -> str:
    """Best-effort human label for the repo this run belongs to."""
    event = event or {}
    cfg = cfg or {}
    for key in ("github_repo", "repo_full_name", "repo", "repo_label"):
        value = str(event.get(key) or "").strip()
        if value:
            return value
    for key in ("repo.label", "repo_label"):
        value = str(cfg.get(key) or "").strip()
        if value:
            return value
    try:
        remote = gitops.default_remote(repo_root)
        if remote:
            url = gitops.remote_url(repo_root, remote)
            if url:
                from .gates.github.parse import parse_origin_url

                parsed = parse_origin_url(url)
                if parsed:
                    return parsed
    except Exception:
        pass
    value = str(event.get("repo_id") or "").strip()
    if value:
        return value
    return repo_root.name


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _frontmatter_doc(meta: dict[str, object], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if value is None:
            continue
        clean = str(value).replace("\n", " ").strip()
        lines.append(f"{key}: {clean}")
    lines.append("---")
    lines.append(body.strip())
    return "\n".join(lines).rstrip() + "\n"


def _runner_policy_proposal_requested(fm: dict) -> bool:
    raw = str(fm.get("runner_policy") or "").strip().lower()
    return raw in {
        "1", "true", "yes", "y", "on",
        "propose", "proposal", "runner", "policy", "runner-policy",
    }


def _runner_policy_scope(fm: dict) -> str:
    raw = str(fm.get("scope") or "").strip().lower()
    if raw in {"account", "account-wide", "global"}:
        return "account"
    return "repo"


def _runner_policy_target_path(
    ctx: account.AccountContext,
    *,
    scope: str,
    repo_label: str,
) -> Path:
    if scope == "account":
        return account.account_runner_policy_path(ctx)
    return account.runner_policy_path(ctx, repo_label)


def _relative_to_account(ctx: account.AccountContext, path: Path) -> str:
    try:
        return path.resolve().relative_to(ctx.dominion_repo.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def _runner_policy_proposal_id(task: Run, event_id: str, body: str) -> str:
    stamp = time.strftime("%y%m%d-%H%M%S", time.gmtime())
    digest = hashlib.sha1(
        f"{task.id}\0{event_id}\0{time.time()}\0{body}".encode("utf-8")
    ).hexdigest()[:8]
    return f"rpol-{stamp}-{digest}"


def _runner_policy_proposal_message(
    proposal_id: str,
    *,
    scope: str,
    repo_label: str,
    policy_rel: str,
    body: str,
) -> str:
    target = "account-wide policy" if scope == "account" else f"{repo_label} policy"
    excerpt = body.strip()
    if len(excerpt) > 1800:
        excerpt = excerpt[:1800].rstrip() + "\n\n[truncated for chat; full proposal is parked]"
    return (
        f"Runner-policy proposal `{proposal_id}` is parked for {target}.\n\n"
        f"Target: `{policy_rel}`\n\n"
        "Proposed policy:\n\n"
        f"```markdown\n{excerpt}\n```\n\n"
        f"Reply `approve runner-policy {proposal_id}` to apply it, or "
        f"`reject runner-policy {proposal_id}` to leave the current policy unchanged."
    )


def _read_runner_policy_proposal(
    ctx: account.AccountContext,
    proposal_id: str,
) -> dict[str, object] | None:
    if not _RUNNER_POLICY_PROPOSAL_ID_RE.match(proposal_id):
        return None
    path = account.runner_policy_proposals_path(ctx) / f"{proposal_id}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta = protocol.parse_frontmatter(text)
    if str(meta.get("id") or "") != proposal_id:
        return None
    meta["body"] = protocol.frontmatter_body(text).strip()
    meta["_path"] = path
    return meta


def _commit_account_policy_update(
    ctx: account.AccountContext,
    *,
    proposal_id: str,
    action: str,
) -> bool:
    if not gitops.worktree_dirty(ctx.dominion_repo):
        return False
    committed = gitops.commit_all(
        ctx.dominion_repo,
        f"runner-policy: {action} proposal {proposal_id}",
    )
    if not committed:
        return False
    try:
        remote = gitops.default_remote(ctx.dominion_repo)
        branch = gitops.current_branch(ctx.dominion_repo)
        if remote and branch and branch != "HEAD":
            gitops.push_branch(ctx.dominion_repo, remote, branch)
    except Exception:  # noqa: BLE001 - durability push is best-effort
        pass
    return True


def _queue_runner_policy_proposal(
    emit: _WorkerEmit,
    task: Run,
    responses_dir: Path,
    event_id: str,
    fm: dict,
    body: str,
    *,
    account_context: account.AccountContext | None,
) -> bool:
    if account_context is None or not account_context.enabled:
        print("[brnrd] outbox: runner-policy proposal had no account context; dropping")
        return False
    proposed = body.strip()
    if not proposed:
        print("[brnrd] outbox: runner-policy proposal had no policy body; dropping")
        return False

    scope = _runner_policy_scope(fm)
    repo_label = str(
        fm.get("repo")
        or fm.get("repo_label")
        or task.meta.get("repo_label")
        or account_context.default_repo.label
    ).strip()
    policy_path = _runner_policy_target_path(
        account_context,
        scope=scope,
        repo_label=repo_label,
    )
    proposal_id = _runner_policy_proposal_id(task, event_id, proposed)
    proposal_path = (
        account.runner_policy_proposals_path(account_context) / f"{proposal_id}.md"
    )
    policy_rel = _relative_to_account(account_context, policy_path)
    proposal_rel = _relative_to_account(account_context, proposal_path)
    meta = {
        "id": proposal_id,
        "status": "pending",
        "scope": scope,
        "repo_label": repo_label if scope == "repo" else "",
        "policy_path": policy_rel,
        "created": _utc_now(),
        "created_by_run": task.id,
        "created_from_event": event_id,
        "conversation_key": task.conversation_key or emit.conversation_key,
    }
    _write_text_atomic(proposal_path, _frontmatter_doc(meta, proposed))
    _commit_account_policy_update(
        account_context,
        proposal_id=proposal_id,
        action="park",
    )

    message = _runner_policy_proposal_message(
        proposal_id,
        scope=scope,
        repo_label=repo_label,
        policy_rel=policy_rel,
        body=proposed,
    )
    ppath = protocol.write_partial(responses_dir, event_id, message)
    if emit.conversation_key:
        conversations.append_artifact(
            emit.brr_dir,
            emit.conversation_key,
            kind="runner_policy_proposal",
            path=str(ppath),
            run_id=task.id,
            event_id=event_id,
            label=f"runner-policy:{proposal_id}",
            body=message,
        )
    emit(
        "runner_policy_proposed",
        run_id=task.id,
        event_id=event_id,
        proposal_id=proposal_id,
        scope=scope,
        repo_label=repo_label if scope == "repo" else None,
        policy_path=policy_rel,
        proposal_path=proposal_rel,
    )
    return True


def _runner_policy_reply(body: str) -> tuple[str, str] | None:
    match = _RUNNER_POLICY_REPLY_RE.match(body)
    if not match:
        return None
    verb = match.group(1).lower()
    action = "approve" if verb in {"approve", "approved", "yes"} else "reject"
    return action, match.group(2)


def _write_control_response(target: _DispatchTarget, body: str) -> None:
    protocol.write_response(target.responses_dir, target.event["id"], body)
    _set_event_status_if_present(target.event, "done")


def _handle_runner_policy_control_event(
    target: _DispatchTarget,
    account_context: account.AccountContext,
) -> bool:
    parsed = _runner_policy_reply(str(target.event.get("body") or ""))
    if parsed is None:
        return False
    action, proposal_id = parsed
    proposal = _read_runner_policy_proposal(account_context, proposal_id)
    if proposal is None:
        _write_control_response(
            target,
            f"I couldn't find runner-policy proposal `{proposal_id}`. No policy changed.",
        )
        return True

    event_conv = conversations.conversation_key_for_event(target.event) or ""
    proposal_conv = str(proposal.get("conversation_key") or "").strip()
    if proposal_conv and event_conv and proposal_conv != event_conv:
        _write_control_response(
            target,
            f"I did not apply runner-policy proposal `{proposal_id}` because "
            "the approval came from a different conversation.",
        )
        return True

    status = str(proposal.get("status") or "").strip()
    if status != "pending":
        _write_control_response(
            target,
            f"Runner-policy proposal `{proposal_id}` is already `{status or 'closed'}`. "
            "No policy changed.",
        )
        return True

    if not isinstance(proposal.get("_path"), Path):
        _write_control_response(
            target,
            f"Runner-policy proposal `{proposal_id}` could not be read. No policy changed.",
        )
        return True

    if action == "reject":
        protocol.update_event_meta(
            proposal,
            status="rejected",
            decided=_utc_now(),
            decided_by_event=target.event["id"],
        )
        _commit_account_policy_update(
            account_context,
            proposal_id=proposal_id,
            action="reject",
        )
        _write_control_response(
            target,
            f"Rejected runner-policy proposal `{proposal_id}`. No policy changed.",
        )
        return True

    scope = str(proposal.get("scope") or "repo").strip()
    repo_label = str(
        proposal.get("repo_label") or target.repo_label or account_context.default_repo.label
    ).strip()
    policy = str(proposal.get("body") or "").strip()
    if not policy:
        protocol.update_event_meta(
            proposal,
            status="invalid",
            decided=_utc_now(),
            decided_by_event=target.event["id"],
        )
        _commit_account_policy_update(
            account_context,
            proposal_id=proposal_id,
            action="invalidate",
        )
        _write_control_response(
            target,
            f"Runner-policy proposal `{proposal_id}` had an empty policy body. "
            "No policy changed.",
        )
        return True

    policy_path = _runner_policy_target_path(
        account_context,
        scope=scope,
        repo_label=repo_label,
    )
    _write_text_atomic(policy_path, policy.rstrip() + "\n")
    policy_rel = _relative_to_account(account_context, policy_path)
    protocol.update_event_meta(
        proposal,
        status="applied",
        decided=_utc_now(),
        decided_by_event=target.event["id"],
        applied_path=policy_rel,
    )
    _commit_account_policy_update(
        account_context,
        proposal_id=proposal_id,
        action="apply",
    )
    _write_control_response(
        target,
        f"Applied runner-policy proposal `{proposal_id}` to `{policy_rel}`.",
    )
    return True


# ── Loom envelope Phase 2 — config-change proposals ────────────────────
#
# A resident can ask for more of an allowlisted, user-tunable ceiling
# (``_CONFIG_CHANGE_ALLOWED_KEYS`` below) than ``.brr/config`` currently
# grants. Unlike CS6's runner-policy proposals above, this one is never
# applied on a chat-typed reply: the daemon mints a brnrd.dev approve/
# confirm URL (``gates/cloud.propose_config_change``) and only applies the
# change once that decision rides back over the account's existing
# ``/v1/daemons/inbox`` long-poll as an ``approve config-change <id>`` /
# ``reject config-change <id>`` event — the exact reply-body convention
# ``_config_change_reply`` below parses, deliberately mirroring
# ``_runner_policy_reply``'s shape so the dispatch machinery downstream
# stays uniform. kb/design-multi-workstream-concurrency.md §"Named forks —
# round 2".


def _config_change_requested(fm: dict) -> str | None:
    key = str(fm.get("config_change") or "").strip()
    return key or None


def _config_change_proposal_id(task: Run, event_id: str, key: str) -> str:
    stamp = time.strftime("%y%m%d-%H%M%S", time.gmtime())
    digest = hashlib.sha1(
        f"{task.id}\0{event_id}\0{time.time()}\0{key}".encode("utf-8")
    ).hexdigest()[:8]
    return f"cfgchg-{stamp}-{digest}"


def _read_config_change_proposal(
    ctx: account.AccountContext,
    proposal_id: str,
) -> dict[str, object] | None:
    if not _CONFIG_CHANGE_PROPOSAL_ID_RE.match(proposal_id):
        return None
    path = account.config_change_proposals_path(ctx) / f"{proposal_id}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta = protocol.parse_frontmatter(text)
    if str(meta.get("id") or "") != proposal_id:
        return None
    meta["body"] = protocol.frontmatter_body(text).strip()
    meta["_path"] = path
    return meta


def _commit_account_config_update(
    ctx: account.AccountContext,
    *,
    proposal_id: str,
    action: str,
) -> bool:
    if not gitops.worktree_dirty(ctx.dominion_repo):
        return False
    committed = gitops.commit_all(
        ctx.dominion_repo,
        f"config-change: {action} proposal {proposal_id}",
    )
    if not committed:
        return False
    try:
        remote = gitops.default_remote(ctx.dominion_repo)
        branch = gitops.current_branch(ctx.dominion_repo)
        if remote and branch and branch != "HEAD":
            gitops.push_branch(ctx.dominion_repo, remote, branch)
    except Exception:  # noqa: BLE001 - durability push is best-effort
        pass
    return True


def _queue_config_change_proposal(
    emit: _WorkerEmit,
    task: Run,
    repo_root: Path,
    responses_dir: Path,
    event_id: str,
    fm: dict,
    body: str,
    *,
    account_context: account.AccountContext | None,
) -> bool:
    key = _config_change_requested(fm)
    if not key:
        return False
    raw_value = fm.get("value")
    if raw_value is None or str(raw_value).strip() == "":
        message = f"Config-change proposal for `{key}` had no `value:` frontmatter; dropping."
        protocol.write_partial(responses_dir, event_id, message)
        return True
    requested_value = str(raw_value).strip()

    if account_context is None or not account_context.enabled:
        print("[brnrd] outbox: config-change proposal had no account context; dropping")
        message = (
            f"Config-change proposal for `{key}` needs an account context "
            "(cross-repo dominion) to park and escalate; this repo doesn't "
            "have one, so I left `.brr/config` untouched."
        )
        protocol.write_partial(responses_dir, event_id, message)
        return True

    if key not in _CONFIG_CHANGE_ALLOWED_KEYS:
        message = (
            f"`{key}` isn't on the agent-proposable config allowlist today "
            f"({sorted(_CONFIG_CHANGE_ALLOWED_KEYS)}). No change requested."
        )
        protocol.write_partial(responses_dir, event_id, message)
        return True

    if key in _CONFIG_CHANGE_INT_KEYS:
        try:
            parsed_value = int(requested_value)
        except ValueError:
            parsed_value = -1
        if parsed_value <= 0:
            message = (
                f"Config-change proposal for `{key}` needs a positive "
                f"integer value; got `{requested_value}`. No change requested."
            )
            protocol.write_partial(responses_dir, event_id, message)
            return True

    current_cfg = conf.load_config(repo_root)
    current_value = current_cfg.get(key)
    proposal_id = _config_change_proposal_id(task, event_id, key)
    proposal_path = account.config_change_proposals_path(account_context) / f"{proposal_id}.md"
    repo_label = str(
        fm.get("repo") or fm.get("repo_label") or task.meta.get("repo_label") or account_context.default_repo.label
    ).strip()
    meta = {
        "id": proposal_id,
        "status": "pending",
        "config_key": key,
        "current_value": "" if current_value is None else str(current_value),
        "requested_value": requested_value,
        "repo_label": repo_label,
        "created": _utc_now(),
        "created_by_run": task.id,
        "created_from_event": event_id,
        "conversation_key": task.conversation_key or emit.conversation_key,
    }
    _write_text_atomic(proposal_path, _frontmatter_doc(meta, body.strip()))
    _commit_account_config_update(account_context, proposal_id=proposal_id, action="park")

    # Gates normally talk to the daemon exclusively through the filesystem
    # (gates/README.md) — this is the one deliberate exception. Minting the
    # approve URL is a rare, resident-initiated action (not a dispatch-loop
    # tick), so a single short-timeout call here (see
    # ``_CONFIG_CHANGE_MINT_TIMEOUT_S``) buys one message with a working
    # link instead of a two-phase park/mint/notify dance whose async half
    # would leave a minting failure invisible until something polled for
    # it. Deferred import matches the existing `from .gates import cloud`
    # pattern in cli.py.
    from .gates import cloud
    minted = cloud.propose_config_change(
        emit.brr_dir,
        proposal_id=proposal_id,
        config_key=key,
        current_value=current_value,
        requested_value=requested_value,
        reason=body.strip(),
    )
    if minted and minted.get("approve_url"):
        message = (
            f"Config-change proposal `{proposal_id}` parked: `{key}` "
            f"`{current_value}` → `{requested_value}`.\n\n"
            f"Approve or reject at: {minted['approve_url']}\n\n"
            "No change applies until the account owner decides there."
        )
    elif minted and minted.get("error"):
        # Cloud-connected, but the mint call itself failed (server
        # allowlist mismatch, deploy-window 5xx, timeout ...). Surface the
        # detail — telling a connected account to run `brnrd account connect`
        # buries the actionable part (observed live 2026-07-11: a 422 from
        # an out-of-lockstep server allowlist read as "not connected").
        message = (
            f"Config-change proposal `{proposal_id}` parked locally (`{key}` "
            f"`{current_value}` → `{requested_value}`), but minting the "
            f"approve link failed: {minted['error']}\n\n"
            "Re-propose to retry, or apply the change by hand in "
            "`.brr/config`."
        )
    else:
        message = (
            f"Config-change proposal `{proposal_id}` parked locally (`{key}` "
            f"`{current_value}` → `{requested_value}`), but this repo isn't "
            "cloud-connected, so there's no approve link to send. Run "
            "`brnrd account connect` first, or apply the change by hand in "
            "`.brr/config`."
        )
    ppath = protocol.write_partial(responses_dir, event_id, message)
    if emit.conversation_key:
        conversations.append_artifact(
            emit.brr_dir,
            emit.conversation_key,
            kind="config_change_proposal",
            path=str(ppath),
            run_id=task.id,
            event_id=event_id,
            label=f"config-change:{proposal_id}",
            body=message,
        )
    emit(
        "config_change_proposed",
        run_id=task.id,
        event_id=event_id,
        proposal_id=proposal_id,
        config_key=key,
        current_value=current_value,
        requested_value=requested_value,
        approve_url=(minted or {}).get("approve_url"),
    )
    return True


def _config_change_reply(body: str) -> tuple[str, str] | None:
    match = _CONFIG_CHANGE_REPLY_RE.match(body)
    if not match:
        return None
    verb = match.group(1).lower()
    action = "approve" if verb in {"approve", "approved", "yes"} else "reject"
    return action, match.group(2)


def _handle_config_change_control_event(
    target: _DispatchTarget,
    account_context: account.AccountContext,
) -> bool:
    parsed = _config_change_reply(str(target.event.get("body") or ""))
    if parsed is None:
        return False
    action, proposal_id = parsed
    proposal = _read_config_change_proposal(account_context, proposal_id)
    if proposal is None:
        _write_control_response(
            target,
            f"I couldn't find config-change proposal `{proposal_id}`. No config changed.",
        )
        return True

    status = str(proposal.get("status") or "").strip()
    if status != "pending":
        _write_control_response(
            target,
            f"Config-change proposal `{proposal_id}` is already `{status or 'closed'}`. "
            "No config changed.",
        )
        return True

    if not isinstance(proposal.get("_path"), Path):
        _write_control_response(
            target,
            f"Config-change proposal `{proposal_id}` could not be read. No config changed.",
        )
        return True

    if action == "reject":
        protocol.update_event_meta(
            proposal,
            status="rejected",
            decided=_utc_now(),
            decided_by_event=target.event["id"],
        )
        _commit_account_config_update(account_context, proposal_id=proposal_id, action="reject")
        _write_control_response(
            target,
            f"Rejected config-change proposal `{proposal_id}`. No config changed.",
        )
        return True

    key = str(proposal.get("config_key") or "").strip()
    requested_value = str(proposal.get("requested_value") or "").strip()
    if not key or key not in _CONFIG_CHANGE_ALLOWED_KEYS or not requested_value:
        protocol.update_event_meta(
            proposal,
            status="invalid",
            decided=_utc_now(),
            decided_by_event=target.event["id"],
        )
        _commit_account_config_update(account_context, proposal_id=proposal_id, action="invalidate")
        _write_control_response(
            target,
            f"Config-change proposal `{proposal_id}` was no longer valid to apply "
            f"(`{key}` off the current allowlist, or missing a value). No config changed.",
        )
        return True

    cfg = conf.load_config(target.repo_root)
    cfg[key] = conf._parse_value(requested_value)
    conf.write_config(target.repo_root, cfg)
    protocol.update_event_meta(
        proposal,
        status="applied",
        decided=_utc_now(),
        decided_by_event=target.event["id"],
        applied_value=requested_value,
    )
    _commit_account_config_update(account_context, proposal_id=proposal_id, action="apply")
    _write_control_response(
        target,
        f"Applied config-change proposal `{proposal_id}`: `{key}` is now `{requested_value}`.",
    )
    return True


def _handle_daemon_control_events(
    targets: list[_DispatchTarget],
    account_context: account.AccountContext,
) -> list[_DispatchTarget]:
    remaining: list[_DispatchTarget] = []
    for target in targets:
        if _handle_runner_policy_control_event(target, account_context):
            continue
        if _handle_config_change_control_event(target, account_context):
            continue
        remaining.append(target)
    return remaining


def _event_files_present(inbox_dir: Path) -> bool:
    """Cheap guard before asking the protocol layer to parse an inbox."""
    try:
        return any(entry.name.endswith(".md") for entry in os.scandir(inbox_dir))
    except OSError:
        return False


def _repo_inbox(repo_root: Path) -> Path:
    return gitops.shared_brr_dir(repo_root) / "inbox"


def _repo_responses(repo_root: Path) -> Path:
    return gitops.shared_brr_dir(repo_root) / "responses"


def _repo_for_event(
    account_context: account.AccountContext,
    event: dict,
    *,
    fallback_repo_root: Path,
    fallback_label: str,
) -> tuple[Path, str]:
    """Resolve the repo dimension for one event.

    Message events in the account dispatch inbox carry an explicit ``repo`` /
    ``repo_label`` target.  Forge events can stay direct: if the event file
    lives in a registered repo's local inbox, that repo is the target even when
    the event body does not repeat its repo label.
    """
    explicit_label = account.event_repo_label(event)
    if explicit_label:
        if account.is_home_label(explicit_label):
            return account.context_home_root(account_context), account.HOME_ROOT_LABEL
        repo = account_context.repo_for_label(explicit_label)
        if repo is not None:
            return repo.root, repo.label
        return fallback_repo_root, explicit_label

    # Managed ingress is account-wide. A follow-up with no selector stays on
    # the repo most recently used by this gate thread, even when the account
    # default points elsewhere.
    if str(event.get("source") or "") == "cloud":
        conversation_key = conversations.conversation_key_for_event(event)
        if conversation_key:
            latest: tuple[str, account.AccountRepo] | None = None
            for registered in account_context.repos.values():
                brr_dir = gitops.shared_brr_dir(registered.root)
                records = conversations.read_recent(
                    brr_dir,
                    conversation_key,
                    limit=50,
                    include_lifecycle=True,
                )
                for record in reversed(records):
                    if record.get("kind") != "run":
                        continue
                    label = str(record.get("repo_label") or "").strip()
                    repo = account_context.repo_for_label(label)
                    if repo is None:
                        continue
                    candidate = (str(record.get("ts") or ""), repo)
                    if latest is None or candidate[0] > latest[0]:
                        latest = candidate
                    break
            if latest is not None:
                return latest[1].root, latest[1].label

    path = event.get("_path")
    if isinstance(path, Path):
        for repo in account_context.repos.values():
            try:
                if path.parent.resolve() == _repo_inbox(repo.root).resolve():
                    return repo.root, repo.label
            except OSError:
                continue
    return fallback_repo_root, fallback_label


def _dispatchable_targets(
    account_context: account.AccountContext,
    default_repo_root: Path,
    cfg: dict,
) -> list[_DispatchTarget]:
    """Return pending events across the account daemon's known inboxes.

    The existing single-repo path remains the hot path: the default repo inbox
    is always scanned. Additional repo inboxes and the account dispatch inbox
    are scanned only when they actually contain event files, avoiding extra work
    and preserving tests that monkeypatch the protocol scanner.
    """
    default_label = account_context.default_repo.label
    sources: list[tuple[Path, Path, Path, str, bool]] = []
    seen: set[Path] = set()

    def add_source(
        inbox_dir: Path,
        responses_dir: Path,
        repo_root: Path,
        repo_label: str,
        *,
        always: bool = False,
    ) -> None:
        try:
            key = inbox_dir.resolve()
        except OSError:
            key = inbox_dir
        if key in seen:
            return
        seen.add(key)
        if always or _event_files_present(inbox_dir):
            sources.append((inbox_dir, responses_dir, repo_root, repo_label, always))

    add_source(
        _repo_inbox(default_repo_root),
        _repo_responses(default_repo_root),
        default_repo_root,
        default_label,
        always=True,
    )
    if account_context.enabled:
        add_source(
            account_context.dispatch_inbox,
            account_context.responses_dir,
            account_context.default_repo.root,
            account_context.default_repo.label,
        )
        for repo in account_context.repos.values():
            add_source(
                _repo_inbox(repo.root),
                _repo_responses(repo.root),
                repo.root,
                repo.label,
            )

    targets: list[_DispatchTarget] = []
    for inbox_dir, responses_dir, source_repo, source_label, _always in sources:
        for event in protocol.list_dispatchable(inbox_dir):
            repo_root, repo_label = _repo_for_event(
                account_context,
                event,
                fallback_repo_root=source_repo,
                fallback_label=source_label,
            )
            if not account.event_repo_label(event):
                event.setdefault("repo_label", repo_label)
            targets.append(
                _DispatchTarget(
                    event=event,
                    repo_root=repo_root,
                    inbox_dir=inbox_dir,
                    responses_dir=responses_dir,
                    repo_label=repo_label,
                )
            )
    return sorted(targets, key=lambda target: _event_mtime(target.event))


def _apply_dashboard_wake_request(
    target: _DispatchTarget,
    account_context: account.AccountContext,
    default_repo_root: Path,
    cfg: dict | None = None,
) -> _DispatchTarget:
    """Bind a parked dispatch-header choice to the lead event atomically.

    The request lives beside the account daemon's default repo. Applying it
    here, before dispatch, is what lets ``repo_label`` choose a different
    registered execution root; ``_run_worker`` is too late because its root
    has already been selected. An event-level runner pin keeps outranking the
    one-shot dashboard choice, preserving the rack's existing semantics.

    #564: consumption used to be source-blind — a ``schedule``-source wake
    (a director tick, an ``every:``/``at:`` firing, nobody watching) could
    reach the pending tick's ``pending()`` first and eat a request the
    maintainer parked from the dashboard while composing his actual
    message, leaving that message to dispatch on the default profile with
    no receipt anywhere. The tap is a promise to *the next wake the
    maintainer is about to cause* — a schedule firing isn't that wake, so it
    must not spend the tap, whether or not it would otherwise apply.

    #577: a tap read here can still be for *some other* wake — the mirror
    file is repopulated every publish tick and this daemon has no way to
    tell "still the same tap the maintainer meant a minute ago" from
    "already stale, meant for whatever fired before this one" without a
    timestamp. ``claimable_for_event`` judges that from the tap's
    server-stamped ``parked_at`` against this event's own ``created``; a
    tap that fails the check is not applied here, but also must not sit
    armed for an unrelated later wake — see the lapse call below.
    """
    if any(target.event.get(key) for key in ("shell", "core", "runner")):
        return target
    if str(target.event.get("source") or "") == "schedule":
        return target
    original_target = target
    brr_dir = gitops.shared_brr_dir(default_repo_root)
    ttl_seconds = float(
        (cfg or {}).get(
            "dispatch.wake_request_ttl_seconds", _WAKE_REQUEST_TTL_DEFAULT
        )
    )
    request = wake_request_mod.pending(brr_dir, ttl_seconds=ttl_seconds)
    if request is None:
        return target

    repo_label = str(request.get("repo_label") or "").strip()
    if repo_label:
        repo = account_context.repo_for_label(repo_label)
        if repo is None:
            return target
        target = _DispatchTarget(
            event=target.event,
            repo_root=repo.root,
            inbox_dir=target.inbox_dir,
            responses_dir=target.responses_dir,
            repo_label=repo.label,
        )

    requested_profile = request["profile"]
    if runner.profile_metadata(requested_profile, target.repo_root) is None:
        print(
            f"[brnrd] wake request {request['request_id']} names unknown "
            f"profile '{requested_profile}' for {target.repo_label}; dropping it"
        )
        # #564: a drop is not a spend — leave the tap pending. Consuming
        # here was the second bug: an unknown profile (stale rack, another
        # daemon's catalog) burned the request without ever applying it,
        # so the maintainer never got what he asked for *and* the tap was
        # gone. The server-side cancel path (no longer returning the
        # request) still reclaims it if it's simply wrong.
        return original_target

    claim_window = float(
        (cfg or {}).get(
            "dispatch.wake_request_claim_window_seconds",
            _WAKE_REQUEST_CLAIM_WINDOW_DEFAULT,
        )
    )
    if not wake_request_mod.claimable_for_event(
        request, str(target.event.get("created") or ""),
        window_seconds=claim_window,
    ):
        # #577: this dispatch was otherwise eligible (no pin, not a
        # schedule wake, a profile this daemon knows) but the tap's
        # server-stamped park time is outside the claim window for *this*
        # event — it belongs to a wake that already came and went. Lapse
        # rather than leave it armed to ambush whatever fires next.
        wake_request_mod.lapse(
            brr_dir, request["request_id"],
            source=str(target.event.get("source") or ""),
            event_id=str(target.event.get("id") or "") or None,
            reason="tap parked outside the claim window for this wake",
        )
        return original_target

    updates: dict[str, object] = {
        "runner": requested_profile,
        "dashboard_wake_request_id": request["request_id"],
    }
    if repo_label:
        updates["repo_label"] = repo_label
    environment = str(request.get("environment") or "").strip()
    if environment:
        updates["environment"] = environment
    protocol.update_event_meta(target.event, **updates)
    target.event.update(updates)
    wake_request_mod.consume(brr_dir, request["request_id"])
    wake_request_mod.record_receipt(
        brr_dir,
        request["request_id"],
        source=str(target.event.get("source") or ""),
        event_id=str(target.event.get("id") or "") or None,
        profile=requested_profile,
    )
    return target


def _start_account_gates(
    account_context: account.AccountContext,
    default_repo_root: Path,
) -> list[threading.Thread]:
    """Start configured gates for the account store and registered repos."""

    threads: list[threading.Thread] = []
    seen: set[Path] = set()

    def start_for(
        brr_dir: Path,
        inbox_dir: Path,
        responses_dir: Path,
        excluded: frozenset[str] = frozenset(),
    ) -> None:
        try:
            key = brr_dir.resolve()
        except OSError:
            key = brr_dir
        if key in seen:
            return
        seen.add(key)
        threads.extend(_start_gates(brr_dir, inbox_dir, responses_dir, excluded))

    if account_context.enabled:
        start_for(
            account_context.dominion_repo,
            account_context.dispatch_inbox,
            account_context.responses_dir,
            frozenset({"cloud"}),
        )
        for repo in account_context.repos.values():
            brr_dir = gitops.shared_brr_dir(repo.root)
            start_for(
                brr_dir,
                brr_dir / "inbox",
                brr_dir / "responses",
                frozenset({"cloud"}),
            )
        # The cloud connection is account-owned and drains all account repos,
        # but its collectors still need a real repo runtime as their local
        # execution anchor. Run exactly one cloud loop from the default repo
        # and land its inbound files in the account dispatch inbox.
        default_brr_dir = gitops.shared_brr_dir(account_context.default_repo.root)
        threads.extend(
            _start_gates(
                default_brr_dir,
                account_context.dispatch_inbox,
                account_context.responses_dir,
                frozenset(),
                frozenset({"cloud"}),
            )
        )
    else:
        brr_dir = gitops.shared_brr_dir(default_repo_root)
        start_for(brr_dir, brr_dir / "inbox", brr_dir / "responses")
    return threads


def _build_continuity_facet(
    brr_dir: Path,
    *,
    repo_root: Path,
    run_id: str,
    forge_facet: Any | None = None,
) -> Any:
    """Assemble the boot score's continuity facet — Slice 3.

    Never raises and never blocks the wake.  Continuity is an orientation aid;
    a resident that cannot boot because its own memory was awkward to read is a
    worse outcome than one that boots to an honest ``✗ unreachable``.
    """
    from . import continuity as continuity_mod

    try:
        cfg = conf.load_config(repo_root)
        candidates = dominion.resident_dominion_candidates(repo_root, cfg)
        # The account home repo — the git root the capture net commits into.
        dominion_repo = candidates[0].capture_root if candidates else None

        prs: list[Any] = []
        if isinstance(forge_facet, dict):
            pr_state = forge_facet.get("pr_state")
            if isinstance(pr_state, dict):
                rows = pr_state.get("standalone")
                if isinstance(rows, list):
                    prs = rows

        return continuity_mod.build_continuity(
            brr_dir,
            current_run_id=run_id,
            dominion_repo=dominion_repo,
            prs=prs,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[brnrd] continuity facet unavailable: {exc}")
        from .bootscore import BootContinuity

        return BootContinuity(mount="✗ unreachable")


def _presence_label_for_event(event: dict) -> str:
    """Derive a run's presence ``label`` — dashboard chrome, not content.

    Read back by every *other* concurrently-active run and injected
    straight into that sibling's own hook context (``facets.py``'s
    ``coexisting_runs`` facet -> ``hooks.py`` ``additionalContext``). The
    pre-#585 shape fell back to a run's own ``task.body`` — its full,
    verbatim task spec — whenever ``event.get("summary")`` was empty, which
    it always was: no writer populates ``summary`` on this creation-time
    ``event`` dict before a run exists (audited for #585:
    ``_pending_event_record`` and ``conversations.append_event`` each build
    a *different*, display-only ``summary`` on a copy of the event/log
    record, never on this one). That made every spawn worker's presence
    label its own task prose, leaked into every sibling's model context at
    every tool-call boundary — the mechanism behind #574's real incident (a
    worker read a sibling's leaked spec as a directive and shipped the
    wrong issue).

    Do not resurrect a ``task.body`` (or any other free-text) fallback
    here. An empty label degrades gracefully: ``facets.py`` already falls
    back ``name -> label -> stream -> run_id``, and ``stream`` (the
    conversation key) is itself a handle. ``facets._sibling_handle`` is the
    second guard — a hard length cap at the facet boundary — for when a
    future writer forgets this one.
    """
    return " ".join(str(event.get("summary") or "").split())[:120]


def _run_worker(
    event: dict,
    repo_root: Path,
    responses_dir: Path,
    cfg: dict,
    max_retries: int,
    *,
    account_context: account.AccountContext | None = None,
    inbox_dir: Path | None = None,
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
    repo_label = _repo_label(repo_root, event, cfg)
    is_home_root = account.is_home_label(repo_label)
    if is_home_root:
        # Home is one shared, capture-net-owned checkout. It cannot sprout a
        # per-run worktree, and environment overrides must not route it through
        # repository isolation machinery.
        event["environment"] = "host"
    runner_overrides = {
        key: event.get(key)
        for key in ("shell", "core", "runner", "runner_policy")
        if event.get(key) not in (None, "")
    }
    # #328 tap-to-request: a spool-rack tap parked "next wake on this
    # profile" (mirrored into .brr/wake-request.json by the cloud gate's
    # publish tick). One-shot: apply it to this wake and spend it. An
    # event-level pin (respawn shell:/core:, quality: escalate) is a
    # deliberate per-run choice and wins — the tap then stays pending for
    # the next unpinned wake rather than being silently swallowed.
    # #564: this is the worker-level fallback for the same tap
    # `_apply_dashboard_wake_request` already tried to bind at dispatch
    # time — same blindness applied here too, so it needs the same two
    # guards: a `schedule`-source wake (director tick, `every:`/`at:`
    # firing) never spends a tap parked for the interactive wake the
    # maintainer was about to cause, and a drop (unknown profile) is not a
    # spend — only an applied profile burns the tap.
    runner_wake_note: str | None = (
        "requested from the dashboard dispatch header"
        if event.get("dashboard_wake_request_id")
        else None
    )
    # #577: "you asked for X, you got Y, because Z" — surfaced on
    # `resources.runner.wake_request` (facets.py) whenever a tap existed for
    # this wake, whether or not it applied. `None` when no tap was ever in
    # play, which is the common case and must read as absent, not as a miss.
    wake_request_report: dict[str, object] | None = None
    if event.get("dashboard_wake_request_id"):
        wake_request_report = {
            "requested_profile": str(event.get("runner") or "") or None,
            "applied": True,
            "reason": None,
        }
    wake_req_ttl = float(
        cfg.get("dispatch.wake_request_ttl_seconds", _WAKE_REQUEST_TTL_DEFAULT)
    )
    wake_req = wake_request_mod.pending(brr_dir, ttl_seconds=wake_req_ttl)
    wake_req_source = str(event.get("source") or "")
    wake_req_has_override = any(
        runner_overrides.get(key) for key in ("shell", "core", "runner")
    )
    if wake_req and wake_request_report is None:
        # Not already bound at dispatch time (`_apply_dashboard_wake_request`
        # saw nothing pending yet, or this daemon dispatches with no account
        # context). #577: this is the second, later read the claim window
        # is for — whatever the mirror has picked up since dispatch first
        # looked is visible now, judged against this event's own timestamp
        # rather than applied blind.
        requested_profile = wake_req["profile"]
        reason: str | None = None
        if wake_req_has_override:
            reason = "an event-level runner pin outranks the dashboard tap"
        elif wake_req_source == "schedule":
            reason = "a schedule-source wake never spends a dashboard tap"
        elif runner.profile_metadata(requested_profile, repo_root) is None:
            print(
                f"[brnrd] wake request {wake_req['request_id']} names unknown "
                f"profile '{requested_profile}'; dropping it"
            )
            # #564: don't consume on drop — see _apply_dashboard_wake_request.
            reason = f"profile '{requested_profile}' is unknown to this daemon"
        elif not wake_request_mod.claimable_for_event(
            wake_req, str(event.get("created") or ""),
            window_seconds=float(
                cfg.get(
                    "dispatch.wake_request_claim_window_seconds",
                    _WAKE_REQUEST_CLAIM_WINDOW_DEFAULT,
                )
            ),
        ):
            reason = "tap parked outside the claim window for this wake"
            # #577: this wake *was* otherwise eligible (no pin, not a
            # schedule firing, a known profile) and still didn't fit — the
            # tap belongs to a wake that already came and went. Lapse it
            # rather than leave it armed to ambush whatever fires next.
            wake_request_mod.lapse(
                brr_dir, wake_req["request_id"],
                source=wake_req_source, event_id=eid, reason=reason,
            )
        else:
            runner_overrides["runner"] = requested_profile
            runner_wake_note = "requested from the dashboard spool rack"
            wake_request_mod.consume(brr_dir, wake_req["request_id"])
            wake_request_mod.record_receipt(
                brr_dir,
                wake_req["request_id"],
                source=wake_req_source,
                event_id=eid,
                profile=requested_profile,
            )
        wake_request_report = {
            "requested_profile": requested_profile,
            "applied": reason is None,
            "reason": reason,
        }
    runner_choice = runner.resolve_runner_profile(
        repo_root, runner_overrides or None,
    )
    if wake_request_report is not None:
        wake_request_report["resolved_profile"] = runner_choice.name
    runner_name = runner_choice.name
    runner_meta: dict[str, object] = runner_choice.portal_metadata()
    quality_escalation = _quality_escalation_meta(repo_root, runner_name)
    failure_defer_seconds = float(
        cfg.get(
            "dispatch.failure_defer_seconds",
            _FAILURE_DEFER_SECONDS_DEFAULT,
        )
    )

    conv_key = conversations.conversation_key_for_event(event) or ""
    correspondent_key = conversations.correspondent_key_for_event(event) or ""
    origin_message_key = conversations.origin_message_key_for_event(event) or ""
    # A respawn-origin event carries its parent's telegram_chat_id /
    # telegram_message_id / telegram_topic_id forward so its eventual
    # reply lands in the same thread (see _queue_respawn_request). That
    # means it recomputes to the *same* origin_message_key as the
    # message that triggered the run which queued it. The exact-duplicate
    # check below exists to catch a genuinely re-delivered external
    # message (the same webhook payload landing on two configured
    # channels) — a daemon-dispatched respawn is never that, so it must
    # never be flagged against its own parent. Found live (2026-07-06):
    # a codex-shell respawn was silently squashed with "I already
    # received this source message on another configured channel" the
    # moment it started, because it looked like a duplicate of the
    # message that had queued it hours earlier.
    is_respawn_origin = bool(
        event.get("respawned_from_event") or event.get("respawned_by_run")
    )
    dedup_window = cfg.get(
        "dispatch.dedup_window_seconds", _DEDUP_WINDOW_SECONDS_DEFAULT
    )
    duplicate_event = (
        None if is_respawn_origin else
        conversations.find_event_by_origin_message(
            brr_dir, origin_message_key, exclude_event_id=eid,
            max_age_seconds=dedup_window,
        )
    )
    emit = _WorkerEmit(brr_dir, conv_key, eid)

    if conv_key:
        conversations.append_event(brr_dir, conv_key, event)
        emit("event_received", event_id=eid, source=event.get("source", ""))

    if duplicate_event:
        task = Run.from_event(event, cfg)
        if is_home_root:
            task.meta["root_kind"] = "home"
            task.meta["forge_lane"] = False
        task.conversation_key = conv_key
        task.status = "done"
        if correspondent_key:
            task.meta["correspondent_key"] = correspondent_key
        task.meta["repo_label"] = repo_label
        if wake_request_report is not None:
            task.meta["wake_request"] = wake_request_report
        protocol.update_event_meta(event, run_id=task.id, repo_label=repo_label)
        _persist_run_state_doc(
            account_context, task, repo_label=repo_label,
            stage="deduplicated", cfg=cfg,
        )
        task.meta["deduplicated_origin_message_key"] = origin_message_key
        prior_event_id = str(duplicate_event.get("event_id") or "").strip()
        prior_conversation = str(duplicate_event.get("conversation_key") or "").strip()
        if prior_event_id:
            task.meta["deduplicated_by_event_id"] = prior_event_id
        if prior_conversation:
            task.meta["deduplicated_by_conversation_key"] = prior_conversation
        task.save(runs_dir)
        emit(
            "run_created", run_id=task.id, event_id=eid,
            env=task.env, repo_label=repo_label,
            run_state_path=task.meta.get("run_state_path"),
            run_state_url=task.meta.get("run_state_url"),
        )
        if conv_key:
            conversations.append_run(
                brr_dir, conv_key,
                run_id=task.id, event_id=eid,
                env=task.env, status=task.status, repo_label=repo_label,
            )
        body = _deduplicated_event_body()
        resp_path = protocol.response_path(responses_dir, eid)
        task.terminal_reply = body
        protocol.write_response(responses_dir, eid, body)
        _stage_terminal_response(
            task, account_context, event, resp_path,
        )
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
    if is_home_root:
        sync_result = sync.SyncResult()
        host_branch = gitops.current_branch(repo_root)
        branch_plan = branching.PublishPlan(
            seed_ref=host_branch if host_branch != "HEAD" else "HEAD",
            target_branch=None,
            source="home:host",
            host_context_branch=host_branch if host_branch != "HEAD" else None,
        )
    else:
        sync_targets = _branches_to_refresh(repo_root, event)
        sync_result = sync.refresh_before_run(
            repo_root, target_branches=sync_targets, cfg=cfg,
        )
        branch_plan = branching.resolve_publish_plan(repo_root, event, cfg)

    task = Run.from_event(event, cfg)
    if is_home_root:
        task.meta["root_kind"] = "home"
        task.meta["forge_lane"] = False
        if task.meta.get("trust_tier") != "owner" and not task.meta.get("trust_refused"):
            task.meta["trust_refused"] = (
                "the account home requires an owner-trusted event"
            )
    task.conversation_key = conv_key
    if correspondent_key:
        task.meta["correspondent_key"] = correspondent_key
    task.meta["repo_label"] = repo_label
    if wake_request_report is not None:
        # #577: "you asked for X, you got Y, because Z" — carried onto the
        # run so `_resources_facet` can surface it on `resources.runner`
        # without the resident having to notice the miss on its own.
        task.meta["wake_request"] = wake_request_report
    protocol.update_event_meta(event, run_id=task.id, repo_label=repo_label)

    # Source-trust tiering (#517): an untrusted event that no isolated
    # environment can hold (solitary unavailable, or trust.untrusted=refuse)
    # is refused *before* any runner is prepared — fail closed. Visible, not
    # silent: a structured WARNING audit line (mirroring the gates' #408/#409
    # drops) plus a neutral refusal recorded on the run state and the event's
    # response, so the operator can see it without a valid principal being
    # confirmed to the sender.
    if task.meta.get("trust_refused"):
        reason = str(task.meta.get("trust_refused") or "")
        print(
            f"[brnrd] trust refuse run={task.id} event={eid} "
            f"source={event.get('source', '')} "
            f"tier={task.meta.get('trust_tier', '')} reason={reason}"
        )
        task.status = "done"
        task.meta["publish_status"] = "refused"
        protocol.update_event_meta(event, run_id=task.id, repo_label=repo_label)
        _persist_run_state_doc(
            account_context, task, repo_label=repo_label,
            stage="refused", cfg=cfg,
        )
        task.save(runs_dir)
        emit(
            "run_created", run_id=task.id, event_id=eid,
            env=task.env, repo_label=repo_label,
            run_state_path=task.meta.get("run_state_path"),
            run_state_url=task.meta.get("run_state_url"),
        )
        if conv_key:
            conversations.append_run(
                brr_dir, conv_key,
                run_id=task.id, event_id=eid,
                env=task.env, status=task.status, repo_label=repo_label,
            )
        body = _trust_refused_event_body(reason)
        resp_path = protocol.response_path(responses_dir, eid)
        task.terminal_reply = body
        protocol.write_response(responses_dir, eid, body)
        _stage_terminal_response(task, account_context, event, resp_path)
        _record_response_artifact(emit, task, resp_path)
        _set_event_status_if_present(event, "done")
        emit("finalizing", run_id=task.id, stage="refused")
        emit("done", run_id=task.id, event_id=eid, publish_status="refused")
        return task

    # Bind the stop control to the run id, so a stop can be addressed by
    # either handle from here on (wyrd §3). Unconditional since #476: a
    # resident thought is registered too, and the live-runs view a user taps
    # names runs by run id, not by the event that woke them.
    _bind_run_control(eid, task.id)
    # Persist the comparison base and the current verdict once, on the run
    # manifest. User-facing readers can then suppress the mechanically-created
    # placeholder branch without paying a git probe on every dashboard tick.
    task.meta["seed_ref"] = branch_plan.seed_ref
    task.meta["has_new_commit"] = False
    # The boot janitor runs in a future daemon process. Persist this daemon's
    # pid so that future boot can prove the process which owned the run is
    # gone instead of treating an absent pid as equivalent evidence.
    task.meta["pid"] = os.getpid()
    _record_task_runner(task, runner_choice)
    _persist_run_state_doc(
        account_context, task, repo_label=repo_label, stage="created", cfg=cfg,
    )
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

    emit(
        "run_created", run_id=task.id, event_id=eid,
        env=task.env, repo_label=repo_label,
        run_state_path=task.meta.get("run_state_path"),
        run_state_url=task.meta.get("run_state_url"),
    )

    # Record this thought in the presence registry so overlapping thoughts
    # (ad-hoc sessions, a second daemon) can see who's on which stream and
    # avoid colliding on the same work (kb/design-agent-dominion.md §4).
    # Best-effort: presence is a hint, never a gate. Deregistered in
    # _run_worker_and_finalize's finally; the heartbeat closure refreshes it.
    presence_id: str | None = None
    try:
        live_run_label = _presence_label_for_event(event)
        # Same fields, same derivation as the closed-run ledger row
        # (run_ledger.py::_ledger_row) — `spawn_immediate` is set only on a
        # concurrent `spawn:` child's own event (_queue_spawn_request), so
        # it (not the parent-id truthiness alone) is the ledger's own
        # is_subspawn source of truth; mirrored here rather than
        # re-derived differently.
        presence_id = presence.register(
            brr_dir, kind="daemon", stream=conv_key, run_id=task.id,
            repo_label=repo_label, label=live_run_label,
            parent_run_id=task.meta.get("spawn_parent_run_id") or None,
            is_subspawn=bool(task.meta.get("spawn_immediate")),
            # Same Shell+Core fields `_record_task_runner` (above) just
            # persisted on the run manifest — carried into presence too so
            # the *live* dashboard view can name which Runner a running
            # thought is on, not only the closed-run ledger.
            runner_name=task.meta.get("runner_name") or None,
            runner_shell=task.meta.get("runner_shell") or None,
            runner_core=task.meta.get("runner_core") or None,
            runner_class=task.meta.get("runner_class") or None,
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
    inbox_dir = inbox_dir or (brr_dir / "inbox")

    # #533: a security-defining key (`runner_cmd`, `trust.*`, `docker.*`,
    # `solitary.*`, `environment`/`env`/`default_env`) set in the
    # repo-writable `.brr/config` is silently *dropped* by
    # `conf.load_config` — it never reaches `cfg` above. "Never honoured"
    # must not also mean "never seen": recomputed here (cheap — one file
    # read) so this run's own outbox carries the notice even though `cfg`
    # itself no longer distinguishes an ignored key from one that was
    # never set. Visible two ways, matching the #524 discipline: a portal
    # notice this run can read, and a host-side WARNING an operator
    # tailing logs sees regardless of whether anyone reads the notice.
    ignored_security_keys = conf.load_config_report(repo_root)[1]
    if ignored_security_keys:
        print(
            f"[brnrd] WARNING: run {task.id} (event {eid}): .brr/config set "
            f"security-defining key(s) {ignored_security_keys} — ignored, "
            "not honoured (they load only from the daemon-owned "
            "security.config; run `brnrd config promote` to migrate them)"
        )
        _record_outbox_notice(
            outbox_dir,
            "repo config tried to set security-defining key(s) "
            f"{ignored_security_keys} in .brr/config — ignored, not "
            "honoured (they load only from the daemon-owned "
            "security.config; run `brnrd config promote` to migrate them "
            "there).",
        )

    print(f"[brnrd] run {task.id} (event {eid}): env={task.env}")

    task.meta["response_path"] = str(resp_path)
    task.meta["outbox_path"] = str(outbox_dir)
    task.meta.update(branch_plan.meta_items())

    # Wyrd §3, worker thread isolation: a worker-stack child talks to its
    # dispatcher and its dispatchees, nobody else. It gets its contract
    # (the event body) and any parent messages — not the user thread's
    # recent turns, history, or burst siblings. The agenda-lock pitfall
    # (a worker following the thread's hottest topic instead of its
    # contract, and once forging a receipt from a sibling's SHA riding
    # the decoration, both caught live 2026-07) retires at the daemon
    # instead of by prompt discipline.
    is_worker_run = bool(event.get("worker"))
    event_body_for_prompt = event.get("body", "") or ""
    woven_body, woven_sibling_ids = (
        (None, set()) if is_worker_run
        else _weave_burst_siblings_into_body(
            inbox_dir,
            event,
            cfg,
            correspondent_key=correspondent_key,
            conversation_key=conv_key,
        )
    )
    if woven_body:
        event_body_for_prompt = woven_body
        task.body = woven_body

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
        print(f"[brnrd] run {task.id}: env setup failed: {e}")
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

    # A resident commits into the project checkout directly, mid-run, in a
    # shell — same shape as the account-knowledge hand-commit gap #565
    # closed. ``.git/hooks`` is the *common* dir shared by the checkout and
    # every worktree spawned from it, so one install here (against
    # ``repo_root``, never a run's own worktree) covers every env backend
    # (#575). Idempotent and best-effort — see gitops.ensure_run_id_hook.
    gitops.ensure_run_id_hook(repo_root)

    run_root = env_ctx.cwd
    branch_name = env_ctx.branch_name
    if branch_name:
        task.meta["branch_name"] = branch_name
    else:
        # A host run has no assigned branch, so relic derivation cannot
        # measure "what this run committed" from branch-vs-seed — the usual
        # host flow merges back into the seed branch, which erases the range.
        # Pin the checkout's HEAD now; commits that appear beyond this OID
        # during the run are this run's produce (relics.collection_scope).
        start_oid = gitops.rev_parse(repo_root, "HEAD")
        if start_oid:
            task.meta["host_start_oid"] = start_oid
    branch_setup_notice = task.meta.get("branch_setup_notice") or None
    # Resolve once during run assembly.  ``portal-state.json`` refreshes every
    # heartbeat, so carrying this avoids turning a stable URL into repeated git
    # archaeology on a hot path.
    task.meta["kb_base_url"] = knowledge.kb_base_url(run_root, cfg)
    # Stamp the knowledge repo's HEAD for this run (#538). Residents commit
    # kb pages themselves mid-run — the majority path — and closeout's
    # dirty-vs-HEAD capture diff cannot see an already-committed page. The
    # ``start..HEAD`` window derived from this OID can.
    kb_start_oid = knowledge.head_oid(repo_root, cfg)
    if kb_start_oid:
        task.meta["kb_start_oid"] = kb_start_oid

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
        repo_label=repo_label,
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
            repo_label=repo_label,
        )

    history_groups = (
        conversations.write_grouped_history_files(
            brr_dir, brr_dir / "runs" / task.id / "history",
            conv_key, correspondent_key,
        )
        if conv_key and not is_worker_run else []
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
        if conv_key and not is_worker_run else None
    )
    if communication_snapshot is not None:
        # Forge-state facet (co-maintainer §5, #113): the resident's
        # in-flight worktrees/branches and the issues/PRs in play, built
        # network-free from local git + conversation keys.
        forge_facet = (
            None if is_home_root else forge_state.build_forge_state(
                repo_root,
                related_threads=communication_snapshot.get("related_threads"),
                current_thread=conv_key,
                current_run_id=task.id,
                current_event_meta=event,
            )
        )
        if forge_facet:
            communication_snapshot["forge"] = forge_facet
        # Reader fluency (#217): which language this thread's reader reads.
        # v1 reads the repo-level `fluency` config key (weave | prose);
        # per-correspondent declaration at the gate boundary stays the
        # eventual shape. Renamed from `user_commitment: full | profane`
        # 2026-07-23 — `full` read as an amount, which is the one thing this
        # field must never mean (identity-core → Voice And The Seam).
        fluency = str(cfg.get("fluency") or "").strip()
        if fluency:
            communication_snapshot["fluency"] = fluency
    recent_conversation = (
        communication_snapshot.get("recent_turns", [])
        if communication_snapshot else []
    )

    # Snapshot of other waiting events so the resident has immediate
    # orientation at wake. A live copy is also refreshed in the outbox
    # below and on every heartbeat.
    # Workers get the same isolation here as the live inbox below: the
    # user thread's pending events belong to the dispatcher. Found live
    # 2026-07-18 — a worker's boot prompt listed two of the maintainer's
    # telegram messages while inbox.json correctly showed none.
    pending_events_snapshot = _pending_events_for_agent(
        inbox_dir, eid, worker=is_worker_run,
    )
    if woven_sibling_ids:
        pending_events_snapshot = [
            ev for ev in pending_events_snapshot
            if str(ev.get("id") or "") not in woven_sibling_ids
        ]
    _write_live_inbox(outbox_dir, inbox_dir, eid, worker=is_worker_run)

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
    card_state: dict[str, object] = {}
    codex_events_path = outbox_dir / ".codex-events.jsonl"
    run_started_monotonic = time.monotonic()

    def _runner_runtime(selected: "runner_select.RunnerProfile") -> _RunnerRuntime:
        meta = selected.portal_metadata()
        name = selected.name
        quota = runner_quota.describe_runner_quota(name, cfg, brr_dir)
        # Native hook config is opt-in through a profile's explicit ``hooks:``
        # field — brr never infers hooks from the runner name. A profile with no
        # ``hooks:`` field uses the heartbeat-polled fallback (outbound flush, no
        # inbound injection).
        declared_hooks_flavour = selected.hooks
        hooks_flavour = declared_hooks_flavour or name
        env = {
            "BRR_RUN_ID": task.id,
            "BRR_EVENT_ID": eid,
            "BRR_RUNNER": hooks_flavour,
            "BRR_RESPONSE_PATH": str(env_ctx.response_path_env),
            "BRR_CONTEXT_PATH": str(context_path),
            "BRR_PORTAL_STATE": str(
                (env_ctx.outbox_env or outbox_dir) / _LIVE_PORTAL_STATE_NAME
            ),
            # The wake's persisted BootScore — arms the hook's orientation
            # ledger (#513 Slice 9): `orient x/y` is metered against the
            # score's `orientation_set`. Same host-path convention as
            # BRR_CONTEXT_PATH (both live in `.brr/runs/<run-id>/`, written
            # by run_context before the runner starts).
            "BRR_BOOT_SCORE": str(
                brr_dir / "runs" / task.id / "boot-score.json"
            ),
        }
        # Conversation identity passthrough: lets the resident stamp its own
        # commits with the Brnrd-Conversation-Id trailer (see gitops.commit_all
        # and kb/plan-conversation-id-propagation.md). Absent when the task has
        # no conversation — never export an empty value.
        if task.conversation_key:
            env["BRR_CONVERSATION_ID"] = task.conversation_key
        # The closeout guard (`hooks.next_move`, default off). Armed per-run via env
        # so the hook subprocess needs no config of its own. Default-off is the
        # control arm, not timidity: `next_move` failed 0/6 across *both* arms of the
        # drift bench, which makes it the cleanest baseline on the board — any
        # non-zero in the armed arm is signal. Measure, then default it on.
        #
        # Not armed for workers: `worker.md` grants no chat seam, so a worker owes no
        # closeout, and a guard demanding one would block a run for failing to keep a
        # contract it was never given.
        if cfg.get("hooks.next_move", False) and not task.meta.get("worker"):
            env["BRR_NEXT_MOVE_GUARD"] = "1"
            # Same arming, same control-arm discipline: the guard also escalates
            # the clean artifact obligation (card) from format_delta's soft
            # `inject` mention to a hard block. A pure fresh-file existence check.
            obligations = ["card"]
            # The SCM obligation, now armed (product call made 2026-07-15). It
            # is NOT a file check but a fresh-git read at Stop, so the hook needs
            # the checkout + seed ref. Armed only for `host`: that is the one
            # environment where finalization does not publish the end branch, so
            # uncommitted / unpushed work is genuinely lost. In a worktree the
            # daemon publishes, so the same block would nag about work that will
            # leave the machine on its own. (Missing-PR is deliberately NOT part
            # of this block — see `hooks._scm_closeout_clause`.)
            if task.env == "host" and task.meta.get("root_kind") != "home":
                obligations.append("scm")
                env["BRR_REPO_DIR"] = str(run_root)
                if env_ctx.branch_plan is not None:
                    env["BRR_SEED_REF"] = env_ctx.branch_plan.seed_ref
                # Arms the `scm` clause's escape route: it may only name
                # `gate: forge` when this account can actually deliver it
                # (see hooks._scm_closeout_clause). HookContext cannot probe
                # gate config itself, so the daemon hands it the fact.
                # Absent ⇒ the hook treats it as off (see there).
                if _gate_can_deliver(brr_dir, "forge"):
                    env["BRR_FORGE_GATE"] = "1"
            env["BRR_CLOSEOUT_OBLIGATIONS"] = ",".join(obligations)

        if env_ctx.outbox_env:
            env["BRR_OUTBOX_DIR"] = str(env_ctx.outbox_env)
            env["BRR_INBOX_PATH"] = str(env_ctx.outbox_env / _LIVE_INBOX_NAME)

        # Tier 2 native hooks: install per-run hook config only for profiles that
        # explicitly declare a hook flavour. Two mechanisms by flavour — a
        # settings file written into the worktree (claude), or config-override
        # argv injected into the runner command (codex).
        extra_args: list[str] = []
        # The hook decision is a *fact this run knows* — returned explicitly so
        # the BootScore reports what was actually wired, rather than re-probing
        # from the daemon process (where the runner's own env does not exist).
        # Not stashed on `meta`: that can be None for an unknown profile, and
        # its None-ness is meaningful.
        hooks_installed = False
        if declared_hooks_flavour == "codex":
            if hooks_mod.codex_hook_capability():
                extra_args = hooks_mod.codex_hook_args()
                hooks_installed = True
                emit(
                    "hooks_installed",
                    run_id=task.id,
                    event_id=eid,
                    flavour=declared_hooks_flavour,
                    path="<argv -c hooks.*>",
                )
                print(f"[brnrd] worker {eid}: installed codex hook config via argv")
        elif (
            declared_hooks_flavour
            and hooks_mod.hook_capability(declared_hooks_flavour, run_root)
        ):
            hook_config_path = hooks_mod.install_hook_config(
                declared_hooks_flavour, run_root
            )
            if hook_config_path is not None:
                hooks_installed = True
                emit(
                    "hooks_installed",
                    run_id=task.id,
                    event_id=eid,
                    flavour=declared_hooks_flavour,
                    path=str(hook_config_path),
                )
                print(
                    f"[brnrd] worker {eid}: installed "
                    f"{declared_hooks_flavour} hook config at {hook_config_path}"
                )
        if hooks_installed:
            # Native boundaries are synchronous with portal acceptance. The
            # hook writes a token to `.flush`; `_invoke_with_heartbeat` drains
            # and acknowledges that exact token before the hook returns. This
            # makes Stop, not runner-return housekeeping, the final delivery
            # boundary. Tier-0/1 profiles leave this unset and keep the polled
            # compatibility path.
            env["BRR_FLUSH_SYNC"] = "1"
        return _RunnerRuntime(meta, quota, env, extra_args, hooks_installed)

    # Re-read the selected runner immediately before building the runtime.
    # `resolve_runner_profile` ran ~600 lines back, before the trust/env
    # setup, the worktree build, and the prompt assembly — a real window in
    # which an operator can change the pin from the dashboard or `.brr/config`
    # and watch the run start on the *old* one, with nothing saying why.
    # Maintainer ask, 2026-07-23.
    #
    # Deliberately a re-resolution with the *same* overrides rather than a
    # raw config read: an override in force (a dashboard wake request,
    # `quality: escalate`) must keep winning, and passing the same overrides
    # makes that true by construction instead of by a special case. This is
    # also the last point where adopting a change is safe — every consumer
    # of `runner_choice` that shapes the actual run (`_runner_runtime`, the
    # catalog, `_record_task_runner`, the `expected_core` attestation) sits
    # at or below this line.
    reselected = runner.resolve_runner_profile(repo_root, runner_overrides or None)
    if reselected.name != runner_choice.name:
        print(
            f"[brnrd] run {task.id} (event {eid}): selected runner changed "
            f"{runner_choice.name} -> {reselected.name} between resolution and "
            "spawn — adopting the current selection"
        )
        _record_outbox_notice(
            outbox_dir,
            f"runner selection changed between resolution and spawn: "
            f"{runner_choice.name} -> {reselected.name}. The run starts on "
            f"{reselected.name}, the currently-set profile.",
        )
        runner_choice = reselected
        runner_name = runner_choice.name
        runner_meta = runner_choice.portal_metadata()

    runtime = _runner_runtime(runner_choice)
    runner_meta = runtime.meta
    quota_summary = runtime.quota
    runner_env = runtime.env
    extra_runner_args = runtime.extra_args
    run_hooks_installed = runtime.hooks_installed
    runner_catalog = runner.available_runner_catalog(repo_root, selected=runner_name)
    _record_task_runner(task, runner_choice)
    run_ledger.mark_run_started(task, runner_name, outbox_dir, run_root)
    task.save(runs_dir)
    _write_live_portal_state(
        outbox_dir,
        inbox_dir,
        eid,
        task,
        phase="preparing",
        runner_name=runner_name,
        runner_meta=runner_meta,
        runner_catalog=runner_catalog,
        quality_escalation=quality_escalation,
        budget_seconds=budget_seconds,
        hard_cap_seconds=hard_cap_seconds,
        keepalive_path=keepalive_path,
        card_state=card_state,
        output_stats=output_stats,
        start_monotonic=run_started_monotonic,
        work_dir=run_root,
        quota_summary=quota_summary,
        cfg=cfg,
        brr_dir=brr_dir,
    )

    attempt = 0
    clean_retries_used = 0
    attempted_runners: list[str] = []
    prompt_mode = "normal"
    fallback_notice: str | None = None
    while True:
        attempt += 1
        # An id proves one completed Codex invocation, not the task forever.
        # A retry is a new invocation; retaining the previous attempt's id
        # would make every running heartbeat report the old thread until the
        # replacement process returned and overwrote it.
        task.meta.pop("codex_thread_id", None)
        try:
            codex_events_path.unlink()
        except FileNotFoundError:
            pass
        stop_control = _stopped_run_control(eid)
        if stop_control is not None:
            # The parent stopped this child before (or between) attempts —
            # never launch a runner for cancelled work.
            return _finalize_stopped_run(
                emit, task, event, eid, runs_dir, env_backend, env_ctx,
                branch_plan, cfg, stop_control, attempt, trace_dirs,
            )
        if runner_name not in attempted_runners:
            attempted_runners.append(runner_name)
        if prompt_mode == "artifact_retry":
            prompt_instruction = (
                "Previous attempt exited cleanly but did not produce the "
                "required output file(s). Produce them this time.\n\n"
                f"Original run instruction: {task.body}"
            )
        elif prompt_mode == "fallback" and fallback_notice:
            prompt_instruction = (
                f"{fallback_notice}\n\n"
                "Continue from the current worktree state and finish the "
                "original run instruction. Do not restart work that is already "
                "present in the files unless it is wrong.\n\n"
                f"Original run instruction: {task.body}"
            )
        else:
            prompt_instruction = task.body

        if attempt == 1:
            run_levels, _ = _collect_levels(
                runner_name, outbox_dir, run_root,
                refresh=False, shared_dir=brr_dir,
            )
            level_quota = runner_quota.summary_from_levels(run_levels)
            quota_summary = level_quota or quota_summary

        # ── Boot mount (`boot.mount`, default ON) ────────────────────────
        # On: the file-backed contracts leave the prose and are seeded as `Read`
        # calls and their results in a session the Shell resumes — the same bytes,
        # in tool-result position, fenced by `transcript.SNAPSHOT_SEAM`.
        # Off: byte-identical to the prose boot every wake had before 2026-07-14.
        # `kb/design-boot-transcript.md`.
        #
        # The default flipped because the experiment ran (3 rounds × 2 arms,
        # `bench --scenario drift`, arms attested from the `prompt.md` each core
        # actually woke into). What it found is *not* what the flag was built to
        # look for, and the distinction is the whole reason this is now on:
        #
        #   obligation RECALL    — dead even. .card ✓✓✓ / classification ✓✓✓ /
        #                          commit ✓✓✓ in BOTH arms. The drift hypothesis
        #                          as originally stated is NOT supported.
        #   obligation ENACTMENT — separates 3/3. The prose arm `cd`'d out of the
        #                          worktree it woke in and committed onto `main`,
        #                          every round. The mounted arm stayed, every round.
        #                          Both bundles named the identical `Execution root:`.
        #
        # Both cores were *told*; only one of them *was somewhere*. A prose contract
        # describes a place. A mounted one is a wake that already acted from it. The
        # failure it prevents — a run committing to the default branch of a shared
        # checkout — is unrecoverable in a way its cost is not.
        #
        # The flag survives, and it is not vestigial: it is the control arm. Every
        # future claim about the boot is measured against `boot.mount=false`,
        # which is also why the prose path must keep working, byte for byte.
        boot_mount = bool(cfg.get("boot.mount", True))
        mount_shell = str(task.meta.get("runner_shell") or "")
        mount_sink: dict[str, str] | None = (
            {} if boot_mount and mount_shell in transcript.MOUNTED_SHELLS else None
        )

        # Built once, so the fail-closed rebuild below cannot drift from the
        # prompt it is replacing.
        update_observation = release_availability.observation(repo_root)
        _prompt_kwargs: dict[str, Any] = dict(
            outbox_path=str(env_ctx.outbox_env) if env_ctx.outbox_env else None,
            run_id=task.id,
            source=task.source or event.get("source"),
            environment=task.env,
            branch_name=branch_name,
            repo_label=repo_label,
            seed_ref=branch_plan.seed_ref,
            branch_source=branch_plan.source,
            branch_setup_notice=branch_setup_notice,
            host_context_branch=branch_plan.host_context_branch,
            runtime_dir=str(env_ctx.runtime_dir),
            context_path=str(context_path),
            recent_conversation=recent_conversation,
            communication_snapshot=communication_snapshot,
            kb_base_url=task.meta.get("kb_base_url"),
            pending_events=pending_events_snapshot,
            present=present_snapshot,
            event_body=event_body_for_prompt,
            event_attachments=protocol.event_attachment_paths(event),
            budget_seconds=budget_seconds,
            runner_medium=(
                f"{runner_name} ({runner_wake_note})"
                if runner_wake_note
                else runner_name
            ),
            # The score gets the *resolved* body, not the display label above.
            # We already wrote these three into run.md (see ``runner_shell`` /
            # ``runner_core`` in task.meta); a boot score that reports
            # ``core: null`` while run.md names the core in the same directory,
            # in the same second, is not an inspection of anything.
            runner_name=runner_name,
            runner_shell=task.meta.get("runner_shell") or None,
            runner_core=task.meta.get("runner_core") or None,
            # Why this body. NOT where the attention came from — those were one
            # field until 2026-07-13, and the kernel confidently told its first
            # live reader that its attention had arrived "from the dashboard
            # spool rack" when the user had in fact typed it into telegram.
            body_provenance=runner_wake_note or None,
            # Who is speaking. The one thing the attention line exists to say.
            source_gate=str(event.get("source") or "") or None,
            continuity=_build_continuity_facet(
                brr_dir,
                repo_root=repo_root,
                run_id=task.id,
                forge_facet=(
                    communication_snapshot.get("forge")
                    if communication_snapshot
                    else None
                ),
            ),
            runner_quota=quota_summary,
            update_available=(
                update_observation.render() if update_observation else None
            ),
            runner_catalog=runner_catalog,
            diffense=prompt_diffense,
            worker=bool(task.meta.get("worker")),
            hooks_installed=run_hooks_installed,
        )

        prompt, boot_score = prompts.build_daemon_prompt_with_score(
            prompt_instruction,
            eid,
            str(env_ctx.response_path_env),
            run_root,
            _mount_sink=mount_sink,
            **_prompt_kwargs,
        )

        if mount_sink:
            try:
                session_id = transcript.mount_claude_session(
                    boot_score,
                    block_text=mount_sink,
                    cwd=str(run_root),
                    git_branch=branch_name or "",
                    model=str(task.meta.get("runner_core") or ""),
                )
                extra_runner_args = [
                    *transcript.resume_argv(session_id),
                    *extra_runner_args,
                ]
                print(f"[brnrd] boot mounted as transcript: session {session_id}")
            except Exception as exc:  # noqa: BLE001 — fail closed, never silently
                # The mounted blocks have already left the prose. If the mount did
                # not happen, this wake would run with its contracts removed from
                # the prompt and seeded nowhere — silently, and *caused by the
                # boot*. Rebuild the prose prompt. A boot that cannot mount must
                # degrade to the boot that always worked, out loud.
                print(f"[brnrd] boot transcript mount failed ({exc}) — prose boot")
                prompt, boot_score = prompts.build_daemon_prompt_with_score(
                    prompt_instruction,
                    eid,
                    str(env_ctx.response_path_env),
                    run_root,
                    **_prompt_kwargs,
                )

        if attempt == 1:
            # Persist the assembled prompt so "what did this wake see?" has
            # an honest answer even on successful runs (traces are cleaned up
            # on success; the run directory persists).  The BootScore lands
            # beside it: same question, structured answer — which blocks
            # entered, who owns them, which were silent.
            run_context.write_prompt_file(brr_dir, task, prompt)
            run_context.write_boot_score(brr_dir, task, boot_score)
        prompt_mode = "normal"
        fallback_notice = None

        print(f"[brnrd] worker {eid}: attempt {attempt}")
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
            runner_meta=runner_meta,
            runner_catalog=runner_catalog,
            quality_escalation=quality_escalation,
            budget_seconds=budget_seconds,
            hard_cap_seconds=hard_cap_seconds,
            keepalive_path=keepalive_path,
            card_state=card_state,
            output_stats=output_stats,
            start_monotonic=run_started_monotonic,
            work_dir=run_root,
            quota_summary=quota_summary,
            cfg=cfg,
            brr_dir=brr_dir,
        )

        def _emit_heartbeat() -> None:
            _refresh_codex_thread_id(task, codex_events_path)
            # Drain first: promoting an interim response is the resident's
            # mid-run check-in, and the partial should reach the gate as
            # promptly as the heartbeat that observed the agent is alive.
            _drain_outbox(
                emit, task, responses_dir, eid, outbox_dir, inbox_dir,
                repo_root=repo_root,
                account_context=account_context,
                stats=output_stats,
            )
            _drain_agent_card(
                emit, task, eid, card_path, card_state,
                account_context=account_context,
                repo_label=task.meta.get("repo_label"),
            )
            _emit_mirror_cards(emit, task, eid, inbox_dir, card_state)
            # Advance the node's frame out of "created" the first time we can
            # prove the agent is alive. Once, not per heartbeat: the frame is a
            # lifecycle attestation, and rewriting it every 30s would churn the
            # corpus fingerprint (and its full republish) for no new fact.
            #
            # Produce is the one thing on the frame that legitimately moves
            # mid-run, so it gets the same treatment the card drain got in
            # #480: rewrite on a real change, never on the clock. Collecting
            # the manifest is cheap (a bounded `git log` plus two control
            # files); republishing the corpus is not, so the fingerprint is
            # the gate.
            produce_moved = False
            if task.meta.get("run_state_running_recorded"):
                produce_moved = _run_state_produce_changed(
                    task, work_dir=run_root, outbox_dir=outbox_dir,
                )
            if not task.meta.get("run_state_running_recorded") or produce_moved:
                task.meta["run_state_running_recorded"] = True
                _persist_run_state_doc(
                    account_context, task,
                    repo_label=str(task.meta.get("repo_label") or ""),
                    stage="running", cfg=cfg,
                    work_dir=run_root, outbox_dir=outbox_dir,
                )
            _write_live_inbox(outbox_dir, inbox_dir, eid, worker=is_worker_run)
            _write_live_portal_state(
                outbox_dir,
                inbox_dir,
                eid,
                task,
                phase="running",
                attempt=attempt,
                runner_name=runner_name,
                runner_meta=runner_meta,
                runner_catalog=runner_catalog,
                quality_escalation=quality_escalation,
                budget_seconds=budget_seconds,
                hard_cap_seconds=hard_cap_seconds,
                keepalive_path=keepalive_path,
                card_state=card_state,
                output_stats=output_stats,
                start_monotonic=run_started_monotonic,
                work_dir=run_root,
                quota_summary=quota_summary,
                cfg=cfg,
                brr_dir=brr_dir,
            )
            if presence_id:
                presence.heartbeat(
                    brr_dir, presence_id,
                    name=run_ledger.read_run_name_control(outbox_dir) or "",
                    mood=run_ledger.read_run_mood_control(outbox_dir) or "",
                )
            elapsed = int(time.monotonic() - attempt_started_monotonic)
            emit(
                "heartbeat",
                run_id=task.id,
                attempt=attempt,
                elapsed_seconds=elapsed,
            )
            _emit_new_containers(emit, task.id, env_ctx, seen_containers)

        def _emit_flush() -> None:
            _refresh_codex_thread_id(task, codex_events_path)
            # Event-driven drain fired by the boundary back channel's .flush signal
            # (chunk 3 of the back channel): push the just-written outbox
            # file / card to the gate promptly, then refresh the live inbox
            # + portal-state the next boundary reads back for injection. Lighter
            # than _emit_heartbeat — no heartbeat packet / presence ping, so
            # a tool-boundary flush doesn't spam the chat card.
            # refresh_levels=False: the event-driven flush must never block on the
            # ~18s PTY scrape for Claude usage. The heartbeat (every 30s) owns
            # the refresh; the flush only reads the on-disk cached snapshot.
            _drain_outbox(
                emit, task, responses_dir, eid, outbox_dir, inbox_dir,
                repo_root=repo_root,
                account_context=account_context,
                stats=output_stats,
            )
            _drain_agent_card(
                emit, task, eid, card_path, card_state,
                account_context=account_context,
                repo_label=task.meta.get("repo_label"),
            )
            _emit_mirror_cards(emit, task, eid, inbox_dir, card_state)
            _write_live_inbox(outbox_dir, inbox_dir, eid, worker=is_worker_run)
            _write_live_portal_state(
                outbox_dir,
                inbox_dir,
                eid,
                task,
                phase="running",
                attempt=attempt,
                runner_name=runner_name,
                runner_meta=runner_meta,
                runner_catalog=runner_catalog,
                quality_escalation=quality_escalation,
                budget_seconds=budget_seconds,
                hard_cap_seconds=hard_cap_seconds,
                keepalive_path=keepalive_path,
                card_state=card_state,
                output_stats=output_stats,
                start_monotonic=run_started_monotonic,
                work_dir=run_root,
                quota_summary=quota_summary,
                cfg=cfg,
                brr_dir=brr_dir,
                refresh_levels=False,
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
                expected_core=runner_choice.model,
                selected_runner=runner_choice,
                codex_events_path=codex_events_path,
            ),
            cfg=cfg,
            trace=True,
            on_heartbeat=_emit_heartbeat,
            on_flush=_emit_flush,
            flush_path=flush_path,
            budget_seconds=budget_seconds,
            hard_cap_seconds=hard_cap_seconds,
            keepalive_path=keepalive_path,
            # Every dispatched run is stoppable since #476, not just a
            # spawned child: the user-side affordance exists precisely for
            # the resident thought no parent run can reach.
            should_abort=(lambda: _stopped_run_control(eid) is not None),
        )
        if result.observed_core:
            task.meta["core_observed"] = result.observed_core
            runner_meta = {
                **runner_meta,
                "model_observed": result.observed_core,
                "core_mismatch": result.core_mismatch,
            }
        if result.codex_thread_id:
            # Per-run state, not a global (issue #195 multi-run safety): this
            # attempt's proven thread id, stashed on the task so the
            # "finalizing" portal-state write just below — and a later retry
            # attempt's own "running" write before its *own* runner call
            # returns — can correlate the rollout exactly instead of
            # guessing newest-mtime across every Codex Shell alive right now.
            task.meta["codex_thread_id"] = result.codex_thread_id
        _emit_new_containers(emit, task.id, env_ctx, seen_containers)
        # Tier-2 Stop is a synchronous portal boundary: the runner cannot
        # return until the matching flush token has been accepted. A normal
        # hooked run therefore has no special "post-return drain" lifecycle.
        # Keep one recovery check for Tier-0/1 runners and a broken/old hook:
        # correctness degrades to the old path instead of losing a message.
        if not run_hooks_installed or _outbox_message_files(outbox_dir):
            if run_hooks_installed:
                print(
                    f"[brnrd] worker {eid}: recovering outbox files left "
                    "after synchronous Stop"
                )
            _drain_outbox(
                emit, task, responses_dir, eid, outbox_dir, inbox_dir,
                repo_root=repo_root,
                account_context=account_context,
                stats=output_stats,
            )
        _drain_agent_card(
            emit, task, eid, card_path, card_state,
            account_context=account_context,
            repo_label=task.meta.get("repo_label"),
        )
        _emit_mirror_cards(emit, task, eid, inbox_dir, card_state, final=True)
        _write_live_inbox(outbox_dir, inbox_dir, eid, worker=is_worker_run)
        _write_live_portal_state(
            outbox_dir,
            inbox_dir,
            eid,
            task,
            phase="finalizing",
            attempt=attempt,
            runner_name=runner_name,
            runner_meta=runner_meta,
            runner_catalog=runner_catalog,
            quality_escalation=quality_escalation,
            budget_seconds=budget_seconds,
            hard_cap_seconds=hard_cap_seconds,
            keepalive_path=keepalive_path,
            card_state=card_state,
            output_stats=output_stats,
            start_monotonic=run_started_monotonic,
            work_dir=run_root,
            quota_summary=quota_summary,
            cfg=cfg,
            brr_dir=brr_dir,
        )
        # Capture the resident's dominion edits before any branch/exit. One
        # call site covers success, retry, and hard failure: a clean
        # dominion no-ops, and on retry the next pass just re-captures any
        # new writes (idempotent — see _capture_dominion).
        _capture_dominion(
            repo_root,
            cfg,
            task,
            account_context=account_context,
        )
        if result.trace_dir:
            trace_dirs.append(str(result.trace_dir.relative_to(brr_dir)))
        stop_control = _stopped_run_control(eid)
        if stop_control is not None:
            # The runner just died to (or survived past) a parent `stop:` —
            # a deliberate cancellation must not fall into the retry /
            # fallback machinery, which would relaunch the killed work.
            return _finalize_stopped_run(
                emit, task, event, eid, runs_dir, env_backend, env_ctx,
                branch_plan, cfg, stop_control, attempt, trace_dirs,
            )
        try:
            result.raise_for_error()
        except RuntimeError as e:
            print(f"[brnrd] worker {eid}: runner error: {e}")
            detail = result.error_detail() or str(e)
            timed_out = result.returncode == 124
            last_failure = {
                "exit_code": result.returncode,
                "error": detail,
                "timed_out": timed_out,
                # A Core mismatch no longer reaches here: it is a provenance
                # annotation on a run that completed, not a failure. Whatever
                # raised is the real cause, so classify on that alone rather
                # than letting the alarm bit shadow it.
                "failure_kind": runner_failures.classify_failure(
                    timed_out=timed_out,
                    exit_code=result.returncode,
                    detail=detail,
                ),
            }
        else:
            if not result.validation_ok and not result.retry_reason():
                detail = result.error_detail()
                if detail:
                    last_failure = {
                        "exit_code": result.returncode,
                        "error": detail,
                        "timed_out": False,
                        "failure_kind": runner_failures.classify_failure(
                            exit_code=result.returncode,
                            detail=detail,
                        ),
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
        task.meta["has_new_commit"] = has_new_commit
        satisfied, signal = _result_satisfied_delivery(
            result, output_stats, event, has_new_commit=has_new_commit,
        )
        if satisfied:
            print(f"[brnrd] worker {eid}: response ready ({signal})")
            task.meta["success_signal"] = signal
            if trace_dirs:
                task.meta["trace_dirs"] = ", ".join(trace_dirs)
            if _response_has_body(resp_path):
                terminal_duplicate = _terminal_stream_duplicates_delivered(task, resp_path)
                # A terminal reply reaches the world one of three ways: a gate
                # delivers it, the spawning parent collects it along the
                # dispatch edge, or nothing does. Only the third is a
                # suppression — and it is a property of the event source, not
                # of one hardcoded source name.
                source = str(event.get("source") or "")
                unowned = bool(source) and not _terminal_reply_lands(
                    source,
                    spawn_parent_run_id=str(
                        task.meta.get("spawn_parent_run_id") or ""),
                )
                suppression_reason = (
                    "duplicate of a delivered reply"
                    if terminal_duplicate
                    else f"no gate owns {source or 'unknown'} events"
                    if unowned
                    else ""
                )
                _stage_terminal_response(
                    task,
                    account_context,
                    event,
                    resp_path,
                    suppressed_reason=suppression_reason,
                    undeliverable=unowned and not terminal_duplicate,
                )
                if terminal_duplicate:
                    # Static dispatch call: the terminal stream is an exact
                    # duplicate of a reply this run already delivered to the
                    # waking thread mid-run (outbox partial). Shipping it
                    # again double-posts on the one surface the user watches;
                    # the content is already in the conversation log, so the
                    # durable message is stamped as already delivered.
                    task.meta["terminal_stream_suppressed"] = True
                    print(
                        f"[brnrd] worker {eid}: terminal stream suppressed "
                        "(duplicate of a delivered reply)"
                    )
                elif not unowned:
                    _record_response_artifact(emit, task, resp_path)
            # Keep an in-memory snapshot for closeout consumers; the response
            # carrier now stays on disk too, but synthetic/older gates can
            # still race the transition during deploy skew.
            terminal_reply = protocol.read_response(responses_dir, eid)
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
            task.terminal_reply = terminal_reply
            _cleanup_traces_on_success(brr_dir, runs_dir, task)
            _emit_preserved_containers(emit, task)
            attend_result = _post_delivery_attend(
                emit,
                task,
                event,
                inbox_dir,
                cfg,
                signal=signal,
                attempt=attempt,
            )
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
                respawn_requests=output_stats.get("respawn", 0),
                committed=has_new_commit,
                post_delivery_attend=attend_result,
            )
            return task

        retry_reason = result.retry_reason()
        will_retry = bool(retry_reason and clean_retries_used < max_retries)
        fallback_runner_name: str | None = None
        fallback_choice: runner_select.RunnerProfile | None = None
        failure_kind = (
            str(last_failure.get("failure_kind") or "")
            if last_failure and not retry_reason else ""
        )
        if failure_kind:
            fallback_choice = runner.fallback_runner_profile(
                repo_root,
                runner_choice,
                failure_kind,
                tried=attempted_runners,
            )
            fallback_runner_name = fallback_choice.name if fallback_choice else None
        attempt_payload: dict[str, object] = {
            "run_id": task.id,
            "event_id": eid,
            "attempt": attempt,
            "reason": retry_reason or (
                last_failure.get("error") if last_failure else None
            ) or "unknown",
            "will_retry": will_retry,
        }
        if fallback_runner_name:
            attempt_payload["will_fallback"] = True
            attempt_payload["fallback_runner"] = fallback_runner_name
        if last_failure and not retry_reason:
            attempt_payload["exit_code"] = last_failure["exit_code"]
            attempt_payload["failure_kind"] = last_failure.get("failure_kind")
            if last_failure.get("timed_out"):
                attempt_payload["timed_out"] = True
        # Check for relay fallback before emitting the attempt failure. The
        # packet is the live feedback surface for cards and future portal
        # readers, so relay availability has to ride the first emission.
        relay_candidate = None
        relay_plan = None
        if (
            failure_kind
            and failure_kind in runner_select.AUTO_FALLBACK_FAILURES
            and not fallback_runner_name
        ):
            try:
                runners = runner_select.available_runners(repo_root)
                relay_candidate = runner_select.best_relay_runner(runners)
                if relay_candidate:
                    # Emit spending plan request. The resident/user approval
                    # consumer is a later slice; this feedback slice makes the
                    # available relay path visible without spending tokens.
                    relay_plan = spending_plan.SpendingPlan(
                        reason=failure_kind,
                        model=relay_candidate.model or relay_candidate.name,
                        provider=relay_candidate.provider or "unknown",
                        estimated_input_tokens=0,
                        estimated_output_tokens=0,
                        consent_state="pending",
                    )
                    attempt_payload["needs_relay_consent"] = True
                    attempt_payload["relay_candidate"] = relay_candidate.summary()
                    attempt_payload["relay_plan"] = relay_plan.to_dict()
            except Exception:
                # Relay check failed; don't block hard failure.
                relay_candidate = None
                relay_plan = None
        emit("attempt_failed", **attempt_payload)
        if will_retry:
            clean_retries_used += 1
            prompt_mode = "artifact_retry"
            print(f"[brnrd] worker {eid}: {retry_reason}, retrying...")
            emit(
                "retrying",
                run_id=task.id,
                event_id=eid,
                attempt=attempt + 1,
                reason=retry_reason,
            )
            continue
        if fallback_runner_name:
            previous_runner = runner_name
            assert fallback_choice is not None
            runner_choice = fallback_choice
            runner_name = runner_choice.name
            # A wake-request note would now be a lie: the requested body
            # failed and this is the fallback, not the tapped profile.
            runner_wake_note = None
            runtime = _runner_runtime(runner_choice)
            runner_meta = runtime.meta
            quota_summary = runtime.quota
            runner_env = runtime.env
            extra_runner_args = runtime.extra_args
            run_hooks_installed = runtime.hooks_installed
            runner_catalog = runner.available_runner_catalog(
                repo_root, selected=runner_name,
            )
            quality_escalation = _quality_escalation_meta(repo_root, runner_name)
            _record_task_runner(task, runner_choice)
            run_ledger.mark_run_started(task, runner_name, outbox_dir, run_root)
            task.save(runs_dir)
            reason = f"fallback after {failure_kind}"
            fallback_notice = (
                f"Previous runner {previous_runner} failed operationally "
                f"({failure_kind}). brr automatically fell back to "
                f"{runner_name}."
            )
            prompt_mode = "fallback"
            print(
                f"[brnrd] worker {eid}: {previous_runner} failed "
                f"({failure_kind}); falling back to {runner_name}"
            )
            emit(
                "retrying",
                run_id=task.id,
                event_id=eid,
                attempt=attempt + 1,
                reason=reason,
                runner=runner_name,
                from_runner=previous_runner,
                failure_kind=failure_kind,
            )
            last_failure = None
            continue
        # Hard failure (timeout / non-zero exit) — no retry, give up now
        # rather than burning another expensive attempt. The give-up
        # branch below carries the captured error up to the gate.
        break

    if last_failure and last_failure.get("timed_out"):
        print(f"[brnrd] worker {eid}: timed out, giving up")
    else:
        print(f"[brnrd] worker {eid}: gave up after {attempt} attempt(s)")
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
        relay_candidate=relay_candidate.summary() if relay_candidate else None,
        relay_plan=relay_plan.to_dict() if relay_plan else None,
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
    _emit_preserved_containers(emit, task)
    # Classify the failure for the card. The no-output case is the clean-exit-
    # but-no-signal path: the runner never recorded a hard failure, yet the run
    # produced no reply on any thread and no commit.
    failure_kind = str(
        (last_failure or {}).get("failure_kind") or runner_failures.NO_OUTPUT
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


def _refresh_codex_thread_id(task: Run, events_path: Path) -> str | None:
    """Publish the thread id from an in-flight Codex JSONL stream.

    ``thread.started`` is the first Codex event. Reading this runtime surface
    from the heartbeat makes rollout correlation live; waiting for the final
    ``RunnerResult`` would leave every long-running snapshot on the old
    newest-mtime guess.
    """
    try:
        raw = events_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    thread_id = codex_status._safe_thread_id(
        runner._extract_codex_thread_id(raw)
    )
    if thread_id:
        task.meta["codex_thread_id"] = thread_id
    return thread_id


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
    should_abort=None,
) -> "runner.RunnerResult":
    """Run *env_backend.invoke* in a thread, ticking *on_heartbeat* every
    *interval* seconds while it's alive, and enforce the liveness budget.

    *should_abort* (optional callable → bool) is polled at the same flush
    cadence: when it turns true the invocation's own subprocess is killed
    via :func:`runner.kill_matching` — the enforcement backstop for the
    ``stop:`` dispatch verb, covering the race where the stop lands before
    the child's subprocess registers for a direct kill.

    The runner subprocess can sit silent for many minutes — codex with
    xhigh reasoning routinely chews for 5-10 min without emitting any
    daemon-side packets. The heartbeat keeps the chat card alive: each
    tick prompts gates to re-render with a fresh elapsed counter. The
    callbacks run on the thought thread driving this invocation (the same
    stack that called here), not on the runner's inner thread, so a
    misbehaving callback can't corrupt the in-flight runner.

    When *budget_seconds* is set, the same tick is the liveness authority:
    past ``start + budget`` the runner is killed via
    :func:`runner.kill_matching` to reclaim the single-flight slot — unless
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
    if should_abort is not None:
        poll = min(poll, flush_interval)
    deadline_killed = False
    while worker.is_alive():
        worker.join(timeout=poll)
        if not worker.is_alive():
            break
        if should_abort is not None:
            aborted = False
            try:
                aborted = bool(should_abort())
            except Exception:
                aborted = False
            if aborted and runner.kill_matching(invocation.label):
                worker.join()  # let the killed proc surface its result
                break
            # Abort requested but no subprocess registered yet: keep
            # polling — the kill lands on a later pass once it exists.
        # Event-driven flush: the runner boundary wrote a request token. Drain
        # first, then acknowledge that exact token. A Tier-2 Stop hook waits on
        # the ack, so deleting the signal *before* the callback (the old shape)
        # would falsely claim acceptance while delivery was still racing.
        if flush_path is not None and flush_path.exists():
            try:
                token = flush_path.read_text(encoding="utf-8").strip()
            except OSError:
                token = ""
            flushed = False
            if on_flush is not None:
                try:
                    on_flush()
                    flushed = True
                except Exception:
                    pass
            if flushed and token:
                ack_path = flush_path.parent / hooks_mod.FLUSH_ACK_NAME
                try:
                    _write_text_atomic(ack_path, token + "\n")
                    # Do not erase a newer request that arrived while the
                    # callback ran (unlikely, but the file is cross-process).
                    if flush_path.read_text(encoding="utf-8").strip() == token:
                        flush_path.unlink()
                except OSError:
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
            # Kill only *this* invocation's subprocess (exact-label match) —
            # with concurrent spawns live, the old kill-whatever-registered-
            # last shape could terminate a sibling run's process instead.
            if runner.kill_matching(invocation.label):
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
    *,
    worker: bool = False,
) -> list[dict[str, object]]:
    """Return other waiting events the resident may fold in.

    Excludes respawn-origin events (``respawned_by_run`` / ``respawned_from_event``
    set by :func:`_queue_respawn_request`): those are a system-to-system handoff
    destined to become a *different* run once this one frees the single-flight
    slot, not a follow-up any resident-wake can "fold in." Counting them here
    made the Stop-hook attention gate un-clearable from inside the very run
    that queued the respawn — pending_event_count could never reach zero, so
    the closeout hint kept firing every phase even after the resident
    correctly explained (on ``.card``) that the event was queued on purpose
    (found live, 2026-07-06: a codex-shell respawn stuck a run in a
    fold-in-or-explain loop it had already resolved).
    """
    events: list[dict[str, object]] = []
    for ev in protocol.list_pending(inbox_dir):
        if ev.get("id") == current_event_id:
            continue
        if ev.get("status") != "pending":
            continue
        if ev.get("respawned_by_run") or ev.get("respawned_from_event"):
            continue
        # Dispatch-edge traffic (wyrd §3): a `to:` message is visible only
        # to the child it addresses — never to the resident's own view.
        edge_target = str(ev.get("spawn_message_for_event") or "")
        if edge_target and edge_target != current_event_id:
            continue
        # A worker-stack run sees only its own edge traffic — the user
        # thread's pending events belong to its dispatcher, not to it.
        if worker and not edge_target:
            continue
        events.append(_pending_event_record(ev))
    return events


def _write_live_inbox(
    outbox_dir: Path | None,
    inbox_dir: Path,
    current_event_id: str,
    *,
    worker: bool = False,
) -> Path | None:
    """Refresh the live inbox view exposed to the running resident.

    The file sits in the run outbox because that directory is already
    mounted into every daemon-run environment. It is daemon-owned control
    state, not a deliverable outbox message.

    The *writing* moved to :mod:`portals` (#507 L3) so init can produce the
    same file without a daemon; what stays here is the daemon's own
    visibility rule — which pending events this wake is allowed to see.
    """
    return portals.write_live_inbox(
        outbox_dir,
        current_event_id,
        _pending_events_for_agent(inbox_dir, current_event_id, worker=worker),
    )


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
            portals.is_staging_name(path.name)
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


def _merge_level_snapshots(
    *snapshots: dict[str, object] | None,
) -> dict[str, object] | None:
    merged: dict[str, object] = {}
    sources: list[str] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        source = snapshot.get("source")
        if isinstance(source, str) and source.strip():
            sources.append(source.strip())
        for key in ("quota", "spend", "context_window", "plan_type"):
            if key in snapshot:
                merged[key] = snapshot[key]
    if sources:
        merged["source"] = " + ".join(dict.fromkeys(sources))
    return merged or None


def _collect_levels(
    runner_name: str | None,
    outbox_dir: Path | None,
    work_dir: Path | None = None,
    *,
    refresh: bool = True,
    shared_dir: Path | None = None,
    codex_thread_id: str | None = None,
) -> tuple[dict[str, object] | None, "frozenset[str] | bool"]:
    """Pick the level snapshot + wired-slot set for *runner_name*'s Shell.

    *codex_thread_id*, when the caller has one (a codex invocation that
    already returned and proved its ``thread.started`` id — see
    ``RunnerResult.codex_thread_id``), is passed straight through to
    :func:`codex_status.load_levels` for exact rollout correlation (issue
    #195). ``None`` is the correct value whenever no run has completed yet
    (a pre-dispatch read, a schedule-pacing tick with no live run) — it
    reproduces :func:`codex_status.load_levels`'s previous newest-mtime
    fallback exactly, never a crash or a silently-wrong id.

    Each Shell exposes its quota/context (and, for Claude, spend) through a
    different head-less seam, so the level *source* is per-Shell:

    - **codex** — two seams, merged. Passively, the session rollout file carries
      ``rate_limits`` (5h + weekly subscription quota) and
      ``model_context_window`` on every ``token_count`` event
      (:mod:`codex_status`) — exact while a run is live, frozen the moment it
      ends. Actively, a cached ``codex app-server`` JSON-RPC probe reads the
      account's rate limits with no run at all and no quota spent
      (:mod:`codex_usage`), which is what keeps the panel true between runs
      (#315). No dollar-spend gauge either way, so ``spend`` is deliberately not
      collected.
    - **claude** — a cached, daemon-side PTY scrape of interactive ``/usage``
      carries subscription quota windows, and the final ``--output-format json``
      result normalized by :mod:`claude_status` carries spend + context
      accounting. The TUI scrape is intentionally throttled; hooks read the
      portal-state snapshot, they do not run the scrape themselves.

    When *refresh* is ``False`` only the on-disk cache is read — the blocking
    probe (Claude's PTY scrape, Codex's app-server spawn) is skipped entirely.
    The heartbeat path passes ``refresh=True`` (the default) so the cache stays
    current on the 30s cadence; the event-driven flush path passes
    ``refresh=False`` so it never stalls a boundary reply waiting for a
    stale-cache refresh.

    *shared_dir* is the account's ``.brr`` dir. Codex's quota is **account**
    state, not run state, so its probe cache lives there: one cache every reader
    shares, warm across runs and daemon restarts, instead of the per-run
    snapshot Claude's collectors write (which forces the
    ``latest_claude_usage_outbox_dir`` hunt on any reader without a "current
    run"). Falls back to *outbox_dir* when no shared dir is supplied.

    Returns ``(levels, wired_slots)`` for :func:`facets.build`. ``wired_slots``
    is the set of level slots whose collector exists (so an empty slot reads
    ``absent`` not ``unimplemented``); Shells with no collector return ``False``.
    """
    if codex_status.supported(runner_name):
        cache_dir = shared_dir or outbox_dir
        probe = (
            codex_usage.load_or_refresh_snapshot(cache_dir)
            if refresh else codex_usage.load_snapshot(cache_dir)
        )
        merged = codex_usage.merge_levels(
            probe, codex_status.load_levels(thread_id=codex_thread_id),
        )
        # Give the point reading a memory: this is the heartbeat cadence, so it
        # is the series trailing burn is measured from (`usage_samples`). A
        # side effect of a read that already happened — never its own poll.
        usage_samples.record(cache_dir, "codex", merged)
        return merged, frozenset(
            codex_usage.COLLECTED_SLOTS | codex_status.COLLECTED_SLOTS
        )
    if claude_status.supported(runner_name):
        if refresh:
            usage_levels = claude_usage.load_or_refresh_snapshot(
                outbox_dir, cwd=work_dir
            )
        else:
            usage_levels = claude_usage.load_snapshot(outbox_dir)
        result_levels = claude_status.load_snapshot(outbox_dir)
        merged = _merge_level_snapshots(usage_levels, result_levels)
        # The seam that makes burn shell-agnostic. Claude's `/usage` scrape is a
        # *point* reading that forgets itself; sampling it here — into the
        # account-shared dir, not the per-run outbox — is what turns it into the
        # series `usage_samples.recent_burn` needs, on the Shell that does most
        # of the spending.
        usage_samples.record(shared_dir or outbox_dir, "claude", merged)
        return merged, frozenset(
            claude_usage.COLLECTED_SLOTS | claude_status.COLLECTED_SLOTS
        )
    return None, False


def _resources_facet(
    quota_summary: str | None,
    *,
    levels: dict[str, object] | None = None,
    levels_collector: "bool | frozenset[str]" = False,
    branch: str | None = None,
    pr_number: str | None = None,
    runner_name: str | None = None,
    runner_meta: "dict[str, object] | None" = None,
    runner_catalog: "list[dict[str, object]] | None" = None,
    quality_escalation: "dict[str, object] | None" = None,
    relay_consent: "dict[str, object] | None" = None,
    pacing_status: "dict[str, object] | None" = None,
    coexisting: "list[dict[str, object]] | None" = None,
    wake_request: "dict[str, object] | None" = None,
) -> dict[str, object]:
    """Operator-facing 'work status' the running resident can read.

    Thin wrapper over :func:`facets.build`, the single definition of the facet
    schema (``kb/design-resident-boundary.md`` §1 — "by schema, not by
    convention"). The schema, the three-state honesty, and the per-Shell level
    asymmetry all live in ``facets``; this keeps the daemon's construction call
    in one place so the JSON snapshot, the woven hook line, and ``brnrd portal
    state`` can never drift on which facets they carry.

    ``coexisting`` is ``None`` unless the call site has a presence snapshot to
    give (see ``_write_live_portal_state``'s ``brr_dir`` param) — passing
    ``None`` reproduces the previous always-``unimplemented`` behaviour
    exactly.

    ``wake_request`` (#577) is ``task.meta["wake_request"]`` when this wake
    had a dashboard tap in play: ``requested_profile`` / ``resolved_profile``
    / ``applied`` / ``reason``, so "you asked for X, you got Y, because Z"
    is readable without the resident having to notice a miss on its own.
    ``None`` when no tap was ever pending for this wake.
    """
    return facets.build(
        quota_summary=quota_summary,
        levels=levels,
        levels_collector=levels_collector,
        branch=branch,
        pr_number=pr_number,
        runner_name=runner_name,
        runner_meta=runner_meta,
        runner_catalog=runner_catalog,
        quality_escalation=quality_escalation,
        relay_consent=relay_consent,
        pacing_status=pacing_status,
        coexisting=coexisting,
        wake_request=wake_request,
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


def _read_pr_control(pr_path: Path) -> str | None:
    """Best-effort PR number from the ``.pr`` control file, or ``None``.

    Accepts a bare number (``274``), a ``#``-prefixed one, or a full PR URL
    (``.../pull/274``) — whatever's quickest for the resident to write right
    after ``gh pr create`` prints its result. Never raises; a malformed or
    missing file just means ``remote_scm`` falls back to ``task.meta``.
    """
    try:
        text = pr_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    return forges.parse_pull_request_number(text)


def _note_run_state_movement(
    task: Run,
    *,
    scm: dict[str, object] | None,
    produce: dict[str, object] | None,
    stats: dict[str, object] | None,
    events: list[dict[str, object]] | None,
) -> float | None:
    """Stamp and return when this run's observable state last moved.

    "Moved" means something a run card would legitimately report changed:
    the working tree / branch (``scm``), the produce manifest, what the run
    has delivered, or which events are waiting on it. Deliberately *not*
    elapsed time, tool calls, or token spend — a run can burn twenty minutes
    inside one test suite without a single fact about it changing, and that
    is precisely the case the old timer nagged about.

    Returns the monotonic instant of the last movement, or ``None`` before
    the first observation (where there is nothing to be behind).
    """
    try:
        digest = json.dumps(
            {
                "scm": scm,
                "produce": produce,
                "delivered": {
                    key: stats.get(key) for key in ("current", "other", "outbound")
                } if stats else None,
                "events": sorted(
                    str(event.get("event_id") or "") for event in (events or [])
                ),
            },
            sort_keys=True,
            default=str,
        )
    except Exception:
        return task.meta.get("run_state_moved_monotonic")
    now = time.monotonic()
    if task.meta.get("run_state_digest") != digest:
        task.meta["run_state_digest"] = digest
        # The first observation establishes a baseline rather than counting as
        # movement: otherwise every run would open one stale-card timer at
        # wake, which is the timer this whole change is replacing.
        if task.meta.get("run_state_moved_monotonic") is None and "run_state_digest_seen" not in task.meta:
            task.meta["run_state_digest_seen"] = True
        else:
            task.meta["run_state_moved_monotonic"] = now
    return task.meta.get("run_state_moved_monotonic")


def _card_is_stale(
    *,
    card_written_monotonic: float | None,
    state_moved_monotonic: float | None,
    card_active: bool,
) -> bool:
    """Is the run's card behind the run?

    Three conditions, all required: the run has moved, the card has not been
    rewritten since it moved, and enough time has passed that the resident
    has plainly moved on rather than being mid-action.

    A run that has never written a card at all keeps the old unconditional
    clock — the card is the surface the user watches, and its *first* write
    is owed early regardless of whether anything has happened yet.
    """
    if not card_active:
        return bool(
            card_written_monotonic is not None
            and time.monotonic() - card_written_monotonic > _CARD_STALE_SECONDS
        )
    if state_moved_monotonic is None or card_written_monotonic is None:
        return False
    if card_written_monotonic >= state_moved_monotonic:
        return False
    return time.monotonic() - state_moved_monotonic > _CARD_STALE_SECONDS


def _change_token(payload: dict[str, object]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        # ``scm`` and ``produce`` are excluded like ``elapsed_seconds`` below:
        # ordinary editing/committing should not bump the token and trip a
        # post-tool injection. Both ride a delta already rendering for another
        # reason; SCM additionally renders at the seed/stop boundaries.
        if key not in {"generated_at", "change_token", "scm", "produce"}
    }
    budget = stable.get("budget")
    if isinstance(budget, dict):
        stable["budget"] = {
            key: value for key, value in budget.items()
            if key != "elapsed_seconds"
        }
    # ``card.age_seconds`` ticks every heartbeat like ``elapsed_seconds``
    # does; only the ``stale`` flip (like ``long_running``) should count as
    # attention-worthy change.
    card = stable.get("card")
    if isinstance(card, dict):
        stable["card"] = {
            key: value for key, value in card.items()
            if key not in {"age_seconds", "state_moved_seconds"}
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
    runner_meta: "dict[str, object] | None" = None,
    runner_catalog: "list[dict[str, object]] | None" = None,
    quality_escalation: "dict[str, object] | None" = None,
    relay_consent: "dict[str, object] | None" = None,
    budget_seconds: float | None = None,
    hard_cap_seconds: float | None = None,
    keepalive_path: Path | None = None,
    card_state: dict[str, object] | None = None,
    output_stats: dict[str, int] | None = None,
    start_monotonic: float | None = None,
    work_dir: Path | None = None,
    quota_summary: str | None = None,
    refresh_levels: bool = True,
    cfg: dict | None = None,
    brr_dir: Path | None = None,
) -> Path | None:
    """Refresh the runner-visible daemon-state portal.

    ``inbox.json`` answers only which events are pending. This broader
    capsule answers "what needs my attention now?" for the running
    resident: input, delivery/card posture, budget state, local SCM posture
    (unpushed commits / modified files), and compiled produce in one
    daemon-owned file refreshed on the heartbeat cadence.

    *refresh_levels* controls whether the Claude usage scrape may run:
    ``True`` (default, heartbeat path) allows a cache-miss to trigger the
    blocking PTY probe; ``False`` (flush path) only reads the on-disk
    cache, keeping the event-driven flush cheap.

    *brr_dir*, when given, wires the ``coexisting_runs`` facet to a live
    presence-registry read (self excluded) — the same query already used to
    build the wake-time-only ``present_snapshot`` injected into
    ``context.md`` (``_run_worker``, "Other thoughts awake right now"), now
    refreshed on every heartbeat/flush instead of frozen at wake time. A
    caller that omits it gets the previous ``unimplemented`` behaviour
    unchanged.
    """
    if not outbox_dir:
        return None
    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
        events = _pending_events_for_agent(
            inbox_dir, current_event_id,
            worker=bool(task.meta.get("worker")) if hasattr(task, "meta") else False,
        )
        stats = output_stats or {}
        card_text = (card_state or {}).get("last", "")
        pending_files = _outbox_message_files(outbox_dir)
        elapsed = (
            int(time.monotonic() - start_monotonic)
            if start_monotonic is not None else None
        )
        # Card age: seconds since the note last changed (write *or*
        # withdrawal), falling back to the run's own start when the card
        # has never been touched — a wake that never writes a note is, from
        # the watching user's side, indistinguishable from one whose note
        # went stale the moment it woke up.
        card_written_monotonic = (
            (card_state or {}).get("written_monotonic") or start_monotonic
        )
        card_age = (
            time.monotonic() - card_written_monotonic
            if isinstance(card_written_monotonic, (int, float)) else None
        )
        scm_facet = _scm_facet(work_dir, task.meta.get("branch_name"))
        live_branch, live_seed = (
            relics.collection_scope(task.meta, Path(work_dir))
            if work_dir else (None, None)
        )
        # A host run's scope is the shared checkout (relics.collection_scope's
        # host_start_oid fallback); gate it by run identity so the live
        # facet never flashes a concurrent sibling's commits as this run's
        # produce (#575). A worktree run's own branch needs no filter.
        live_commit_run_id = (
            task.id if not task.meta.get("branch_name") else None
        )
        produce_facet = (
            relics.live_summary(
                work_dir,
                branch=live_branch,
                seed_ref=live_seed,
                outbox_dir=outbox_dir,
                commit_run_id=live_commit_run_id,
            )
            if work_dir else {"known": False}
        )
        # Staleness is measured against the run's *movement*, not the wall
        # clock (maintainer, 2026-07-19, agreeing with the run that raised it:
        # "tied to elapsed-since-last-state-changing-action, it'd keep the
        # pressure where it belongs"). A timer-only rule fires on an accurate
        # card during a long test suite, and the cheapest way to satisfy it is
        # a cosmetic edit — which trains writing to the file to quiet the
        # nudge rather than because the surface moved. Here the nudge can only
        # fire when something a card would actually report has changed since
        # the card was last written.
        state_moved_monotonic = _note_run_state_movement(
            task, scm=scm_facet, produce=produce_facet, stats=stats, events=events,
        )
        card_stale = _card_is_stale(
            card_written_monotonic=card_written_monotonic,
            state_moved_monotonic=state_moved_monotonic,
            card_active=bool(card_text),
        )
        run_levels, run_level_slots = _collect_levels(
            runner_name, outbox_dir, work_dir,
            refresh=refresh_levels, shared_dir=brr_dir,
            codex_thread_id=(
                task.meta.get("codex_thread_id")
                if hasattr(task, "meta") else None
            ),
        )
        # The run boundary knows its own Core (the resolved profile's
        # `model`, e.g. "opus"/"fable") — pass it so a thin week_models
        # bucket for a *different* Core doesn't bind this run's pacing (#561).
        binding_model = str((runner_meta or {}).get("model") or "").strip() or None
        pacing_status = _quota_pacing_status(cfg or {}, run_levels, model=binding_model)
        coexisting_snapshot: list[dict[str, object]] | None = None
        if brr_dir is not None:
            try:
                coexisting_snapshot = [
                    e for e in presence.list_active(brr_dir)
                    if e.get("run_id") != task.id
                ]
            except OSError:
                coexisting_snapshot = None
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
                "repo": task.meta.get("repo_label"),
                "branch": task.meta.get("branch_name"),
            },
            "attention": {
                "pending_event_count": len(events),
                "pending_outbox_file_count": len(pending_files),
                "needs_attention": bool(events or pending_files),
            },
            # Directives brr refused or dropped this run (a spawn it could
            # not queue, a reply addressed to an event that no longer
            # exists). Silence here used to be indistinguishable from
            # success — the file is deleted either way.
            "notices": _read_outbox_notices(outbox_dir),
            "inbound": {
                "current_event": current_event_id,
                # Mechanical fact, not a hint: can a reply addressed to the
                # waking event actually be delivered? Same predicate the
                # dispatch path uses to decide terminal-stream suppression,
                # so the Stop-hook delivery warning cannot nag about a
                # reply the router would refuse (#562).
                "current_event_replyable": _terminal_reply_lands(
                    str(getattr(task, "source", "") or ""),
                    spawn_parent_run_id=str(
                        task.meta.get("spawn_parent_run_id") or ""),
                ),
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
                "age_seconds": (
                    int(card_age) if card_age is not None else None
                ),
                "stale": card_stale,
                # What the staleness verdict is measured against, so the
                # briefing can say *why* the card is behind rather than only
                # that it is old.
                "state_moved_seconds": (
                    int(time.monotonic() - state_moved_monotonic)
                    if state_moved_monotonic is not None else None
                ),
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
            "scm": scm_facet,
            "produce": produce_facet,
            "knowledge": {"kb_base_url": task.meta.get("kb_base_url")},
            "name": {"written": bool(run_ledger.read_run_name_control(outbox_dir))},
            "resources": _resources_facet(
                quota_summary,
                # Per-Shell level source (see _collect_levels): Codex reads its
                # subscription quota + context window live from the session
                # rollout file; Claude gets terminal spend/context accounting
                # from result JSON. The wired-slot set decides whether an empty
                # slot reads 'absent' vs 'unimplemented'.
                levels=run_levels,
                levels_collector=run_level_slots,
                branch=task.meta.get("branch_name"),
                # A resident-declared `.pr` control file wins over task.meta:
                # it's this run's own live evidence (written the moment `gh pr
                # create` succeeds), whereas `github_pr_number` in task.meta
                # only exists for tasks that originated *from* a GitHub
                # issue/PR — a run that creates its own PR mid-thought had no
                # way to update that field before this file existed.
                pr_number=(
                    _read_pr_control(outbox_dir / _PR_CONTROL_NAME)
                    or task.meta.get("github_pr_number")
                ),
                runner_name=runner_name,
                runner_meta=runner_meta,
                runner_catalog=runner_catalog,
                quality_escalation=quality_escalation,
                relay_consent=relay_consent,
                pacing_status=pacing_status,
                coexisting=coexisting_snapshot,
                wake_request=task.meta.get("wake_request"),
            ),
        }
        if task.meta.get("root_kind") == "home":
            remote_scm = payload["resources"]["remote_scm"]
            remote_scm.update(
                {
                    "status": "absent",
                    "branch": None,
                    "pr_number": None,
                    "pr_state": "none",
                    "summary": None,
                    "note": "account-home runs have no forge lane",
                }
            )
        # Spawn ownership is only actionable when the resident can see pool
        # headroom. Presence already tells us which siblings are sub-spawns;
        # expose that count beside the coexisting-runs facet rather than
        # making the prompt infer capacity from prose or a hidden config.
        coexisting_facet = payload["resources"]["coexisting_runs"]
        spawn_limit = _max_concurrent_spawns(cfg or {})
        if coexisting_snapshot is None:
            spawn_active = None
            spawn_available = None
        else:
            spawn_active = sum(
                1 for entry in coexisting_snapshot
                if bool(entry.get("is_subspawn"))
            )
            spawn_available = max(0, spawn_limit - spawn_active)
        coexisting_facet["spawn_pool"] = {
            "max_concurrent": spawn_limit,
            "active": spawn_active,
            "available": spawn_available,
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


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _respawn_defer_until(fm: dict) -> str | None:
    raw = str(fm.get("defer_until") or fm.get("at") or "").strip()
    if not raw:
        return None
    if raw.startswith("+"):
        seconds = schedule_mod.parse_duration(raw[1:].strip())
        return _format_utc_after(seconds) if seconds is not None else None
    return raw if schedule_mod.parse_iso(raw) is not None else None


def _respawn_quality_target(fm: dict) -> str | None:
    """Return the requested local quality target class for a respawn frontmatter."""
    raw = str(
        fm.get("quality")
        or fm.get("quality_escalation")
        or fm.get("escalation")
        or ""
    ).strip()
    if not raw:
        return None
    value = raw.lower()
    if value in {"0", "false", "no", "n", "off", "none"}:
        return None
    if value in {"balanced", "strong"}:
        return value
    if value in {"1", "true", "yes", "y", "on", "escalate", "stronger", "higher"}:
        return "strong"
    return None


def _queue_respawn_request(
    emit: _WorkerEmit,
    task: Run,
    repo_root: Path | None,
    inbox_dir: Path | None,
    event_id: str,
    fm: dict,
    body: str,
    outbox_dir: Path | None = None,
) -> bool:
    if inbox_dir is None:
        _record_outbox_notice(outbox_dir, "respawn dropped: no inbox to queue into")
        return False
    proposed = str(
        fm.get("proposed_runner")
        or fm.get("runner")
        or fm.get("shell")
        or ""
    ).strip()
    core = str(fm.get("core") or "").strip()
    quality_target = _respawn_quality_target(fm)
    if not proposed and not core and quality_target and repo_root is not None:
        current_runner = str(task.meta.get("runner_name") or "").strip()
        proposed = runner.quality_escalation_runner(
            repo_root, current_runner, target_class=quality_target
        ) or ""
    if not proposed and not core:
        print(
            "[brnrd] outbox: respawn request had no runner/core/"
            "quality target; dropping"
        )
        return False
    current = _find_pending_event(inbox_dir, event_id) or {}
    source = str(fm.get("source") or current.get("source") or task.source or "respawn")
    carry = str(fm.get("carry_forward") or "").strip()
    new_body = body.strip() or carry or task.body
    if not new_body.strip():
        _record_outbox_notice(outbox_dir, "respawn dropped: the request had no body")
        return False
    worker = _truthy(fm.get("worker"))
    reserved = {
        "_path", "id", "body", "status", "created", "source",
        "origin_message_key", "respawn", "event", "gate",
        "runner", "proposed_runner", "shell", "core", "at", "defer_until",
        "carry_forward", "quality", "quality_escalation", "escalation",
        "worker",
    }
    meta = {
        k: v for k, v in current.items()
        if k not in reserved and not str(k).startswith("_")
    }
    explicit_repo = str(
        fm.get("repo") or fm.get("repo_label") or fm.get("repo_id") or ""
    ).strip()
    if explicit_repo:
        meta["repo"] = explicit_repo
        meta["repo_label"] = explicit_repo
    elif task.meta.get("repo_label"):
        meta["repo_label"] = task.meta["repo_label"]
    if proposed:
        meta["shell"] = proposed
    if core:
        meta["core"] = core
    defer_until = _respawn_defer_until(fm)
    if defer_until:
        meta["defer_until"] = defer_until
    if worker:
        meta["worker"] = True
    reason = str(fm.get("reason") or "").strip()
    meta["respawned_from_event"] = event_id
    meta["respawned_by_run"] = task.id
    # Source-trust tiering (#517): a respawn continues the parent run's
    # work, so it inherits the parent's tier authoritatively — a respawn
    # can never escalate an untrusted run into a more-trusted env, and an
    # owner continuation is never accidentally demoted to untrusted just
    # because its origin pending event is gone (source alone would fail
    # closed). The parent tier overrides any tier copied from ``current``.
    if task.meta.get("trust_tier"):
        meta["trust_tier"] = task.meta["trust_tier"]
    if reason:
        meta["respawn_reason"] = reason
    if quality_target:
        meta["respawn_quality"] = quality_target
    new_path = protocol.create_event(inbox_dir, source, new_body, **meta)
    print(f"[brnrd] outbox: queued respawn request ({new_path.stem})")
    if emit.conversation_key:
        conversations.append_artifact(
            emit.brr_dir, emit.conversation_key,
            kind="respawn_request",
            path=str(new_path),
            run_id=task.id,
            event_id=event_id,
            label=f"respawn:{new_path.stem}",
            body=reason or new_body,
        )
    emit(
        "respawn_requested",
        run_id=task.id,
        event_id=event_id,
        respawn_event_id=new_path.stem,
        proposed_runner=proposed or None,
        core=core or None,
        quality=quality_target,
        defer_until=defer_until,
        reason=reason or None,
    )
    return True


NOTICES_FILE = ".notices.jsonl"
_MAX_NOTICES = 12


def _record_outbox_notice(outbox_dir: Path | None, text: str) -> None:
    """Tell the *running resident* that brr refused or dropped its directive.

    Every drop path in the outbox drain used to end at a ``print()`` — and
    the daemon's stdout is captured nowhere. From inside the run, a spawn
    that was refused and a spawn that is quietly working look identical:
    the file vanishes either way. That is the same silent-failure shape as
    a 401 that kills a gate thread while the daemon reports healthy; the
    fix is the same too — put the failure where the reader already looks.

    Notices land in the run's own outbox dir and are surfaced in
    ``portal-state.json`` (``notices``), which residents re-read at plan
    boundaries and before closeout. Control file, never delivered.
    """
    print(f"[brnrd] outbox: {text}")
    if outbox_dir is None:
        return
    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "text": text},
            ensure_ascii=False,
        )
        with (outbox_dir / NOTICES_FILE).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _retire_outbox_staging(path: Path) -> None:
    """Move an accepted staging file aside without deleting message content."""

    try:
        target_dir = path.parent / ".processed"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if target.exists():
            target = target_dir / f"{time.time_ns()}-{path.name}"
        path.replace(target)
    except OSError:
        pass


def _stage_outbound(
    task: Run,
    account_context: account.AccountContext | None,
    *,
    body: str,
    kind: str,
    target_event: str = "",
    target_gate: str = "",
    target_thread: str = "",
    source_ref: str = "",
    status: str = message_store.PENDING,
    reason: str = "",
) -> Path | None:
    if account_context is None or not account_context.enabled:
        return None
    label = str(task.meta.get("repo_label") or account_context.default_repo.label)
    return message_store.stage(
        account_context,
        repo_label=label,
        run_id=task.id,
        body=body,
        kind=kind,
        target_event=target_event,
        target_gate=target_gate,
        target_thread=target_thread,
        source_ref=source_ref,
        status=status,
        reason=reason,
    )


def _stage_terminal_response(
    task: Run,
    account_context: account.AccountContext | None,
    event: dict,
    response_path: Path,
    *,
    suppressed_reason: str = "",
    undeliverable: bool = False,
) -> Path | None:
    # The terminal stream is always *captured* here, deliverability aside.
    # When *undeliverable* (no gate owns the source — a ``schedule`` wake
    # with no spawning parent), the capture is the whole story: the text is
    # kept as the run's body / message-store record and is dispatched
    # nowhere. That is deliberate — there is no fallback gate to invent one
    # for (#562). A schedule-woken run with something to say must route it
    # to a configured user gate itself (`gate: telegram`).
    body = protocol.read_response(response_path.parent, str(event.get("id") or "")) or ""
    status = (
        message_store.UNDELIVERABLE if undeliverable else message_store.PENDING
    )
    path = _stage_outbound(
        task,
        account_context,
        body=body,
        kind="terminal",
        target_event=str(event.get("id") or ""),
        target_gate=str(event.get("source") or ""),
        target_thread=str(event.get("conversation_key") or task.conversation_key),
        source_ref=str(response_path),
        status=status,
        reason=suppressed_reason,
    )
    if path is not None:
        protocol.attach_message_path(response_path, path)
        if suppressed_reason and status == message_store.PENDING:
            message_store.transition(
                path,
                message_store.DELIVERED,
                gate="deduplicated",
                platform_message_id="already-delivered",
            )
    if suppressed_reason:
        protocol.update_event_meta(event, terminal_suppressed=True)
    return path


def _read_outbox_notices(outbox_dir: Path | None) -> list[dict[str, str]]:
    if outbox_dir is None:
        return []
    try:
        lines = (outbox_dir / NOTICES_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, str]] = []
    for line in lines[-_MAX_NOTICES:]:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("text"):
            out.append(record)
    return out


# Live spawn-control registry — the daemon-side half of the wyrd's
# The stop registry: one entry per *dispatched run*, the handle every kill
# in this daemon goes through. Entries are registered at dispatch time
# (`spawn:` queue time for a child, submit time for the resident thought),
# bound to a run id once the worker thread creates its Run, and retired when
# the main loop reaps the future. In-memory only: a daemon restart kills
# every live runner anyway, so there is nothing durable to stop.
#
# ``parent_run_id`` is the dispatch edge, and it is what encodes *authority*,
# not mere parentage:
#
# - a spawned child carries its dispatcher's run id. The dispatch-edge
#   ownership rule (`kb/design-wyrd.md` §3) reads it so the ``stop:`` verb
#   can refuse a run reaching sideways to kill a sibling it never started.
# - a resident thought carries ``None`` — *no run* dispatched it, so no run
#   may stop it. It is not unowned, though: it is owned by the human whose
#   account it burns, and the user-side stop path (#476) is scoped to that
#   account rather than to a dispatch edge. Registering it here rather than
#   in a second registry is the whole point — one kill path, one place that
#   knows which processes are live (this repo has shipped two production
#   bugs from a fact stored twice).
_run_controls_lock = threading.Lock()
_run_controls: dict[str, dict] = {}


def _register_run_control(spawn_event_id: str, parent_run_id: str | None) -> None:
    with _run_controls_lock:
        _run_controls[spawn_event_id] = {
            "event_id": spawn_event_id,
            "parent_run_id": parent_run_id,
            "run_id": None,
            "stopped": False,
        }


def _bind_run_control(spawn_event_id: str, run_id: str) -> None:
    with _run_controls_lock:
        control = _run_controls.get(spawn_event_id)
        if control is not None:
            control["run_id"] = run_id


def _find_run_control(target: str) -> dict | None:
    """Look a control up by spawn event id or child run id (exact match)."""
    with _run_controls_lock:
        control = _run_controls.get(target)
        if control is not None:
            return control
        for control in _run_controls.values():
            if control.get("run_id") == target:
                return control
    return None


def _stopped_run_control(spawn_event_id: str) -> dict | None:
    with _run_controls_lock:
        control = _run_controls.get(spawn_event_id)
        if control is not None and control.get("stopped"):
            return control
    return None


def _retire_run_control(spawn_event_id: str) -> None:
    with _run_controls_lock:
        _run_controls.pop(spawn_event_id, None)


def _retire_child_messages(inbox_dir: Path | None, spawn_event_id: str) -> None:
    """Retire unconsumed parent→child messages once the child is over.

    A ``to:`` message exists only for the addressed child's lifetime — it
    is edge traffic, not a dispatchable event. Whatever the child did not
    fold in dies with it; leaving it pending would leak a permanently
    invisible event (every view filters it to a run that no longer exists).
    """
    if inbox_dir is None or not spawn_event_id:
        return
    for ev in protocol.list_pending(inbox_dir):
        if str(ev.get("spawn_message_for_event") or "") == spawn_event_id:
            protocol.set_status(ev, "done")


def _queue_child_message(
    emit: _WorkerEmit,
    task: Run,
    inbox_dir: Path | None,
    event_id: str,
    fm: dict,
    body: str,
    outbox_dir: Path | None = None,
) -> bool:
    """Handle a ``to: <id>`` outbox directive (wyrd §3, the message verb).

    Parent→child traffic along the dispatch edge: an inbox event only the
    addressed worker sees (its ``inbox.json`` / portal-state; every other
    view filters it out, and it never dispatches a run of its own). The
    child folds it into its work — it is a steer, not a new contract, and
    not an event the child should ``event:``-address. Ownership-checked
    like ``stop:``; refusals land in ``portal-state.json → notices``.
    """
    target = str(fm.get("to") or "").strip()
    if not target:
        _record_outbox_notice(outbox_dir, "message dropped: no target run/event id")
        return False
    if not body.strip():
        _record_outbox_notice(outbox_dir, f"message to {target!r} dropped: empty body")
        return False
    control = _find_run_control(target)
    if control is None:
        _record_outbox_notice(
            outbox_dir,
            f"message refused: {target!r} matches no live concurrent spawn "
            "(already finished, never dispatched here, or the id is wrong)",
        )
        return False
    if str(control.get("parent_run_id")) != task.id:
        _record_outbox_notice(
            outbox_dir,
            f"message refused: {target!r} was not dispatched by this run — "
            "a run messages only its own dispatchees (kb/design-wyrd.md §3)",
        )
        return False
    if control.get("stopped"):
        _record_outbox_notice(
            outbox_dir,
            f"message refused: {target!r} is being stopped",
        )
        return False
    if inbox_dir is None:
        _record_outbox_notice(outbox_dir, "message dropped: no inbox to queue into")
        return False
    spawn_event_id = str(control["event_id"])
    new_path = protocol.create_event(
        inbox_dir,
        "dispatch_message",
        body.strip(),
        spawn_message_for_event=spawn_event_id,
        spawn_message_for_run=str(control.get("run_id") or ""),
        spawn_message_from_run=task.id,
    )
    print(f"[brnrd] outbox: message to {target} ({new_path.stem}) by {task.id}")
    emit(
        "spawn_message",
        run_id=task.id,
        event_id=event_id,
        spawn_event_id=spawn_event_id,
        message_event_id=new_path.stem,
        target=target,
    )
    return True


def _apply_run_stop(
    control: dict,
    inbox_dir: Path | None,
    *,
    stopped_by: str,
    reason: str = "",
    conversation_key: str = "",
) -> str:
    """Dispatch the kill for an already-authorized stop. Returns the stage.

    **The** kill path — the one both the ``stop:`` outbox verb (a run
    stopping its own dispatchee) and the user-side stop affordance (#476,
    the account owner stopping anything burning on their daemon) reach.
    Authorization is deliberately *not* here: the two callers answer
    different questions about who may stop what, and answering them in one
    place would either weaken the dispatch-edge rule or refuse the human.
    What must not fork is the mechanism below, so it doesn't.

    Two shapes, by whether the run ever started. Already dispatched (or
    mid-launch): kill its current attempt by invocation-label prefix and let
    the worker's own loop observe the stopped flag — that covers the sliver
    where the flag lands before the subprocess registers. Never dispatched:
    cancel the inbox event so it never starts and post the completion note
    right here, since no future will ever exist to reap.
    """
    with _run_controls_lock:
        already_stopped = bool(control.get("stopped"))
        control["stopped"] = True
        control["stopped_by"] = stopped_by
        if reason:
            control["stop_reason"] = reason
    spawn_event_id = str(control["event_id"])
    child_run_id = control.get("run_id")
    if child_run_id is None and inbox_dir is not None:
        pending = _find_pending_event(inbox_dir, spawn_event_id)
        if pending is not None:
            protocol.set_status(pending, "cancelled")
            _retire_child_messages(inbox_dir, spawn_event_id)
            if not already_stopped and control.get("parent_run_id"):
                # Only a dispatched child has a parent thread waiting on a
                # completion note; a resident thought cancelled before it
                # started has nobody to notify.
                try:
                    protocol.create_event(
                        inbox_dir,
                        "spawn_completed",
                        f"concurrent spawn {spawn_event_id} stopped before "
                        f"it started (cancelled by {stopped_by})",
                        conversation_key=conversation_key or f"run:{stopped_by}",
                        spawned_by_event=spawn_event_id,
                        spawn_parent_run_id=stopped_by,
                        spawn_stopped=True,
                    )
                except OSError as exc:
                    print(f"[brnrd] stop notify failed for {spawn_event_id}: {exc}")
            return "cancelled-before-start"
    runner.kill_matching(f"{spawn_event_id}-attempt-")
    return "running"


def _queue_stop_request(
    emit: _WorkerEmit,
    task: Run,
    inbox_dir: Path | None,
    event_id: str,
    fm: dict,
    body: str,
    outbox_dir: Path | None = None,
) -> bool:
    """Handle a ``stop: <id>`` outbox directive (wyrd §3, the stop verb).

    Ownership-checked and attested: the target must be a concurrent child
    *this* run dispatched (matched by spawn event id or child run id), and
    the kill is the daemon's own — it does not depend on the worker reading
    anything. A running child's runner process is killed immediately (the
    worker's attempt loop then finalizes it as ``stopped`` and the normal
    reap path notifies this parent); a child still queued in the inbox is
    cancelled before it ever dispatches, with the completion note posted
    right here since no future will ever exist for it. Refusals land in
    ``portal-state.json → notices`` like every other dropped directive.
    """
    target = str(fm.get("stop") or "").strip()
    if not target:
        _record_outbox_notice(outbox_dir, "stop dropped: no target run/event id")
        return False
    control = _find_run_control(target)
    if control is None:
        _record_outbox_notice(
            outbox_dir,
            f"stop refused: {target!r} matches no live concurrent spawn "
            "(already finished, never dispatched here, or the id is wrong)",
        )
        return False
    if str(control.get("parent_run_id")) != task.id:
        _record_outbox_notice(
            outbox_dir,
            f"stop refused: {target!r} was not dispatched by this run — "
            "a run may stop only its own dispatchees (kb/design-wyrd.md §3)",
        )
        return False
    reason = str(fm.get("reason") or "").strip() or body.strip()
    spawn_event_id = str(control["event_id"])
    stage = _apply_run_stop(
        control,
        inbox_dir,
        stopped_by=task.id,
        reason=reason,
        conversation_key=task.conversation_key or f"run:{task.id}",
    )
    print(f"[brnrd] outbox: stop {target} ({stage}) by {task.id}")
    emit(
        "spawn_stop_requested",
        run_id=task.id,
        event_id=event_id,
        spawn_event_id=spawn_event_id,
        target=target,
        stage=stage,
        reason=reason or None,
    )
    return True


def _queue_spawn_request(
    emit: _WorkerEmit,
    task: Run,
    inbox_dir: Path | None,
    event_id: str,
    fm: dict,
    body: str,
    outbox_dir: Path | None = None,
) -> bool:
    """Queue a concurrent worker-stack child (``spawn:``, slice 1).

    Sibling to :func:`_queue_respawn_request`, with one structural
    difference: a respawn only ever starts once *this* run ends (queued
    into the ordinary inbox, dispatched by the next idle tick); a spawn is
    picked up by the main loop's *second* dispatch slot immediately,
    alongside this still-running thought (see ``active_spawns`` and
    ``_max_concurrent_spawns`` in the daemon loop). Always ``worker: true`` — never the
    resident stack — a concurrent child does not get dominion write, kb
    governance, or scheduling authority any more than a sequential
    worker-stack respawn does (`kb/design-director-loop.md` §"Concurrent
    sub-spawns": that's exactly why it doesn't reopen the dominion-
    coherence problem single-flight exists to close).

    A worker-stack run spawning *its own* child is refused — nesting was
    never part of the slice-1 shape (cap=1, one level), and a worker has
    no business creating further daemon-dispatched work anyway.
    """
    if task.meta.get("root_kind") == "home":
        _record_outbox_notice(
            outbox_dir,
            "spawn refused: the account home is a shared host tree and cannot "
            "provide concurrent worktree isolation.",
        )
        return False
    if bool(task.meta.get("worker")):
        _record_outbox_notice(
            outbox_dir,
            "spawn refused: a worker-stack run cannot spawn (no nested spawns). "
            "Do the work inline, or hand it back to the resident.",
        )
        return False
    if inbox_dir is None:
        _record_outbox_notice(outbox_dir, "spawn dropped: no inbox to queue into")
        return False
    # ``shell:``/``core:`` are optional — absent, the child dispatches on the
    # account's configured default, exactly like any fresh event (nothing
    # below needs them; ``meta`` simply omits the keys). Until 2026-07-12 a
    # spawn without them was *dropped*, with the only trace a print to the
    # daemon's uncaptured stdout: the prompt contract said the keys were
    # optional, the code required them, and a resident who believed the
    # contract sat waiting for a worker that never existed. Caught by living
    # it — this run's own setup-assist spawn vanished that way.
    proposed = str(fm.get("shell") or fm.get("runner") or "").strip()
    core = str(fm.get("core") or "").strip()
    new_body = body.strip()
    if not new_body:
        _record_outbox_notice(outbox_dir, "spawn dropped: the request had no body")
        return False
    source = str(fm.get("source") or "spawn")
    meta: dict = {"worker": True}
    # A spawn is the one primitive that deliberately runs concurrently
    # with its still-running parent in the *same* daemon process — every
    # other dispatch path (respawn:, a fresh event) only ever starts once
    # whatever came before it has ended, so sharing the repo's own
    # `environment=host` working directory has never been a collision
    # risk for them. For a spawn it is: confirmed live 2026-07-07
    # (run-260707-1321-auhp, kb/design-director-loop.md §"Concurrent
    # sub-spawns" addendum) a spawned child's `git checkout -b` executed
    # in the same cwd as the parent's own mid-edit shell, flipping the
    # parent's branch out from under it. Worktree isolation is therefore
    # the *floor*, regardless of the repo's own env policy — an event's
    # own `environment` key outranks the repo config default
    # (`run.py::_event_environment_policy`).
    #
    # The frontmatter may opt *down* into a more-isolated env (#515):
    # `docker` and `solitary` are WorktreeEnv subclasses, so the child
    # still gets its own worktree and the collision guard holds by
    # construction — isolation only increases. Anything else (`host`
    # above all) is refused outright rather than silently rewritten:
    # a parent that asked for less isolation should hear "no", and a
    # parent that asked for *more* (say, solitary on a repo with no
    # docker.image) must never be quietly downgraded — the env backend
    # fails that run loudly instead (`docker env requires docker.image`).
    requested_env = str(fm.get("environment") or fm.get("env") or "").strip().casefold()
    if requested_env in ("docker", "solitary"):
        meta["environment"] = requested_env
    elif requested_env and requested_env != "worktree":
        _record_outbox_notice(
            outbox_dir,
            f"spawn refused: environment {requested_env!r} is not spawnable — "
            "worktree is the floor; opt-down to docker/solitary only.",
        )
        return False
    else:
        meta["environment"] = "worktree"
    if proposed:
        meta["shell"] = proposed
    if core:
        meta["core"] = core
    if task.meta.get("repo_label"):
        meta["repo_label"] = task.meta["repo_label"]
    reason = str(fm.get("reason") or "").strip()
    # Reuses the exact meta keys _pending_events_for_agent already excludes
    # on: a spawn dispatch is the same "system-to-system handoff, not a
    # fold-in-able follow-up" shape a respawn is, just concurrent instead
    # of sequential.
    meta["respawned_from_event"] = event_id
    meta["respawned_by_run"] = task.id
    meta["spawn_immediate"] = True
    meta["spawn_parent_run_id"] = task.id
    meta["spawn_parent_conversation_key"] = task.conversation_key or ""
    # Source-trust tiering (#517): a spawned child inherits the parent's
    # tier so an untrusted (isolated) parent cannot escalate work into a
    # more-trusted env by spawning it. Without this the child's source
    # ("spawn") would resolve to owner. The forced worktree isolation above
    # is a concurrency guard, not a trust boundary — the tier still governs.
    if task.meta.get("trust_tier"):
        meta["trust_tier"] = task.meta["trust_tier"]
    if reason:
        meta["spawn_reason"] = reason
    new_path = protocol.create_event(inbox_dir, source, new_body, **meta)
    # Dispatch-edge ownership (wyrd §3): record who dispatched this child so
    # the `stop:` verb can enforce parent-only control from the first moment
    # the spawn exists — before it has a run id, before it has a process.
    _register_run_control(new_path.stem, task.id)
    print(f"[brnrd] outbox: queued concurrent spawn ({new_path.stem})")
    # A schedule entry can opt in (`reset_on: spawn`) to treat this dispatch
    # as if it had just fired itself, rather than firing redundantly right
    # after related event-driven work already happened — see
    # schedule.apply_reset_signals, read back on the next scheduling tick.
    schedule_mod.record_signal(emit.brr_dir, "spawn")
    if emit.conversation_key:
        conversations.append_artifact(
            emit.brr_dir, emit.conversation_key,
            kind="spawn_request",
            path=str(new_path),
            run_id=task.id,
            event_id=event_id,
            label=f"spawn:{new_path.stem}",
            body=reason or new_body,
        )
    emit(
        "spawn_requested",
        run_id=task.id,
        event_id=event_id,
        spawn_event_id=new_path.stem,
        proposed_runner=proposed or None,
        core=core or None,
        reason=reason or None,
    )
    return True


def _short_event_summary(event: dict, *, limit: int = 80) -> str:
    """One clipped line describing *event*'s own body, for a redirect prefix.

    Best-effort only: an event with no body (or none readable) yields an
    empty string, and the caller falls back to a gate-only mention rather
    than manufacture a summary that isn't there.
    """
    text = " ".join(str(event.get("body") or "").split())
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _drain_outbox(
    emit: _WorkerEmit,
    task: Run,
    responses_dir: Path,
    event_id: str,
    outbox_dir: Path | None,
    inbox_dir: Path | None = None,
    *,
    repo_root: Path | None = None,
    account_context: account.AccountContext | None = None,
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
      event ``done`` so the gate delivers the reply to its thread. A target
      that isn't live becomes an ``undeliverable`` run message (never
      misrouted or silently dropped).
    - **A gate destination** (``gate: <name>`` + target metadata): an
      agent-initiated message with no waiting event (a scheduled ping, an
      out-of-bound note). ``_deliver_out_of_bound`` synthesizes an
      already-``done`` event the gate delivers. ``event:``
      is "reply to a waiting thread"; ``gate:`` is "send to a
      destination".

    Each promotion is persisted in the run message store, indexed on the
    conversation log, and emits an ``interim_response`` packet. The accepted
    staging file moves under ``.processed``; ``.tmp`` files are skipped so the
    agent has an atomic-write staging name.
    Returns the count promoted — a promoting drain is also a liveness
    check-in. Errors are swallowed: a drain bug must never break a run.

    Called by runner boundary flushes and the heartbeat fallback while the
    runner is alive. Tier-0/1 runs and a broken Tier-2 handshake get one
    post-return recovery check; post-return is not the normal lifecycle seam.
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
        # ``.tmp`` anywhere in the suffix chain is the agent's atomic-write
        # staging name (``portals.is_staging_name`` — a bare ``.suffix``
        # check missed ``note.md.tmp.<pid>.<rand>`` and delivered a message
        # mid-write, #590); dotfiles are reserved as control channels
        # (e.g. ``.keepalive`` for the liveness budget), and the live JSON
        # files are daemon-owned control state. None are deliverable.
        if (
            portals.is_staging_name(fpath.name)
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
        if _runner_policy_proposal_requested(fm):
            if _queue_runner_policy_proposal(
                emit,
                task,
                responses_dir,
                event_id,
                fm,
                body,
                account_context=account_context,
            ):
                promoted += 1
                if stats is not None:
                    stats["current"] = stats.get("current", 0) + 1
                    stats["runner_policy"] = stats.get("runner_policy", 0) + 1
            _retire_outbox_staging(fpath)
            continue
        if _config_change_requested(fm):
            if _queue_config_change_proposal(
                emit,
                task,
                repo_root,
                responses_dir,
                event_id,
                fm,
                body,
                account_context=account_context,
            ):
                promoted += 1
                if stats is not None:
                    stats["current"] = stats.get("current", 0) + 1
                    stats["config_change"] = stats.get("config_change", 0) + 1
            _retire_outbox_staging(fpath)
            continue
        if _truthy(fm.get("respawn")):
            dispatched = _queue_respawn_request(
                emit, task, repo_root, inbox_dir, event_id, fm, body, outbox_dir,
            )
            if dispatched:
                promoted += 1
                message_path = _stage_outbound(
                    task, account_context,
                    body=body or task.body,
                    kind="dispatch",
                    target_gate="respawn",
                    source_ref=str(fpath),
                )
                if message_path:
                    message_store.transition(
                        message_path, message_store.DELIVERED,
                        gate="dispatch", platform_message_id="respawn-event",
                    )
                if stats is not None:
                    stats["respawn"] = stats.get("respawn", 0) + 1
            _retire_outbox_staging(fpath)
            continue
        if _truthy(fm.get("spawn")):
            dispatched = _queue_spawn_request(
                emit, task, inbox_dir, event_id, fm, body, outbox_dir,
            )
            if dispatched:
                promoted += 1
                message_path = _stage_outbound(
                    task, account_context,
                    body=body,
                    kind="dispatch",
                    target_gate="spawn",
                    source_ref=str(fpath),
                )
                if message_path:
                    message_store.transition(
                        message_path, message_store.DELIVERED,
                        gate="dispatch", platform_message_id="spawn-event",
                    )
                if stats is not None:
                    stats["spawn"] = stats.get("spawn", 0) + 1
            _retire_outbox_staging(fpath)
            continue
        if str(fm.get("to") or "").strip():
            handled = _queue_child_message(
                emit, task, inbox_dir, event_id, fm, body, outbox_dir,
            )
            if handled:
                promoted += 1
                message_path = _stage_outbound(
                    task, account_context,
                    body=body,
                    kind="dispatch",
                    target_gate="spawn-message",
                    source_ref=str(fpath),
                )
                if message_path:
                    message_store.transition(
                        message_path, message_store.DELIVERED,
                        gate="dispatch", platform_message_id="spawn-message-event",
                    )
                if stats is not None:
                    stats["spawn_message"] = stats.get("spawn_message", 0) + 1
            _retire_outbox_staging(fpath)
            continue
        if str(fm.get("stop") or "").strip():
            handled = _queue_stop_request(
                emit, task, inbox_dir, event_id, fm, body, outbox_dir,
            )
            if handled:
                promoted += 1
                message_path = _stage_outbound(
                    task, account_context,
                    body=body or f"stop {fm.get('stop')}",
                    kind="dispatch",
                    target_gate="stop",
                    source_ref=str(fpath),
                )
                if message_path:
                    message_store.transition(
                        message_path, message_store.DELIVERED,
                        gate="dispatch", platform_message_id="stop-request",
                    )
                if stats is not None:
                    stats["stop"] = stats.get("stop", 0) + 1
            _retire_outbox_staging(fpath)
            continue
        gate = str(fm.get("gate") or "").strip()
        if gate:
            # Gate-addressed: an agent-initiated message to a destination
            # with no waiting event (a scheduled ping, an out-of-bound
            # note). Synthesize an already-`done` event the gate delivers
            # and cleans up; it never wakes a thought.
            gate_available = _gate_can_deliver(emit.brr_dir, gate)
            message_path = _stage_outbound(
                task,
                account_context,
                body=body,
                kind="outbound",
                target_gate=gate,
                target_thread=str(fm.get("thread") or ""),
                source_ref=str(fpath),
                status=(
                    message_store.PENDING
                    if gate_available else message_store.UNDELIVERABLE
                ),
                reason=(
                    "" if gate_available
                    else f"gate {gate!r} is not deliverable on this account"
                ),
            )
            if _deliver_out_of_bound(
                emit, task, responses_dir, inbox_dir, event_id, gate, fm, body,
                outbox_dir, message_path=message_path,
            ):
                promoted += 1
                if stats is not None:
                    stats["outbound"] = stats.get("outbound", 0) + 1
            _retire_outbox_staging(fpath)
            continue
        target = str(fm.get("event") or "").strip()
        target = target or event_id
        cross = target != event_id
        target_event = _find_pending_event(inbox_dir, target) if cross else None
        if cross and target_event is None:
            # Unknown or already-handled target — don't deliver to the
            # wrong thread; drop with a console note.
            _record_outbox_notice(
                outbox_dir,
                f"reply dropped: event {target} is not pending (already handled, "
                f"or the id is wrong) — the message was NOT delivered",
            )
            _stage_outbound(
                task,
                account_context,
                body=body,
                kind="interim",
                target_event=target,
                source_ref=str(fpath),
                status=message_store.UNDELIVERABLE,
                reason=f"event {target} has no live gate owner",
            )
            _retire_outbox_staging(fpath)
            continue
        target_source = str(
            (target_event or {}).get("source") or getattr(task, "source", "")
        )
        own_source = str(getattr(task, "source", "") or "")
        redirected = False
        if (
            cross
            and target_event is not None
            and target_source
            and target_source != own_source
            and _gate_owns_source(target_source)
            and not _gate_can_deliver(emit.brr_dir, target_source)
        ):
            # Cross-gate, not reachable from this run (#578): a real gate
            # owns the target event, but it's neither this run's own gate
            # nor configured/running here — staging a reply for it would
            # sit ``pending`` in a store nobody polls. Record the foreign
            # target as explicitly undeliverable, retire it exactly like
            # any other cross reply, and redirect the body onto this run's
            # own live gate instead, prefixed with its origin, so the
            # answer still reaches someone rather than nobody.
            _stage_outbound(
                task,
                account_context,
                body=body,
                kind="interim",
                target_event=target,
                target_gate=target_source,
                target_thread=str(target_event.get("conversation_key") or ""),
                source_ref=str(fpath),
                status=message_store.UNDELIVERABLE,
                reason=(
                    f"gate {target_source!r} is not reachable from this run "
                    "— redirected to the run's own gate"
                ),
            )
            _set_event_status_if_present(target_event, "done")
            _record_outbox_notice(
                outbox_dir,
                f"reply redirected: event {target} is owned by gate "
                f"{target_source!r}, not reachable from this run — delivered "
                "on this run's own gate instead, prefixed with its origin",
            )
            summary = _short_event_summary(target_event)
            origin_note = (
                f"re: {summary} — originally on {target_source}\n\n"
                if summary else f"re: an event on {target_source}\n\n"
            )
            body = origin_note + body
            target = event_id
            cross = False
            target_event = None
            target_source = own_source
            redirected = True
        # Interim replies ride the target event's own gate. Dispatch-tree
        # sources (spawn, spawn_completed, dispatch_message) have no gate and
        # no collector for interims — only a worker's *terminal* report is
        # collected — so a partial written here would orphan and the record
        # would sit pending forever. Say so at staging time instead — but
        # only about a source we can actually see: an absent one is unknown,
        # not impossible.
        deliverable = not target_source or _gate_owns_source(target_source)
        message_path = _stage_outbound(
            task,
            account_context,
            body=body,
            kind="interim",
            target_event=target,
            target_gate=target_source,
            target_thread=str(
                (target_event or {}).get("conversation_key")
                or getattr(task, "conversation_key", "")
            ),
            # A redirect already staged one durable row for the foreign
            # target above (``source_ref=str(fpath)``); this second row is
            # the redirected delivery itself and needs its own identity, or
            # ``stage``'s idempotency-by-source_ref would just hand back
            # that first (undeliverable) row instead of creating this one.
            source_ref=str(fpath) + ("#redirect" if redirected else ""),
            status=(
                message_store.PENDING if deliverable
                else message_store.UNDELIVERABLE
            ),
            reason=(
                "" if deliverable else
                f"no gate owns {target_source or 'unknown'} events — answer the "
                "originating user event instead"
            ),
        )
        if not deliverable:
            _record_outbox_notice(
                outbox_dir,
                f"reply NOT delivered: no gate owns {target_source or 'unknown'} "
                f"events (target {target}) — address the originating user event "
                f"instead; the text is kept in the run's message store",
            )
        ppath = (
            protocol.write_partial(
                responses_dir, target, body, message_path=message_path,
            )
            if body and deliverable else None
        )
        _retire_outbox_staging(fpath)
        if cross and target_event is not None and not deliverable:
            # Nothing will deliver this, but the resident *did* answer it:
            # retire the event so the unowned-source inbox stops growing
            # (#454), with the text preserved as an undeliverable record.
            _set_event_status_if_present(target_event, "done")
        if not ppath:
            continue
        promoted += 1
        if stats is not None:
            key = "other" if cross else "current"
            stats[key] = stats.get(key, 0) + 1
        if not cross:
            # Remember what was already delivered to the waking thread so the
            # terminal-stream dispatch can skip an exact duplicate — the
            # "deliver via outbox *and* restate on stdout" double-post the old
            # required-terminal-reply contract used to push residents into.
            # In-process only (a dynamic attribute, never serialized).
            digests = getattr(task, "_delivered_current_digests", None)
            if digests is None:
                digests = set()
                task._delivered_current_digests = digests  # type: ignore[attr-defined]
            digests.add(hashlib.sha256(body.encode("utf-8")).hexdigest())
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


def _gate_is_configured(brr_dir: Path, delivery_gate: str) -> bool:
    """True when the delivery-loop gate *delivery_gate* is configured here.

    The one probe behind both :func:`_gate_can_deliver` (a single gate,
    already resolved through its alias) and :func:`_configured_gate_names`
    (the full deliverable set) — same source, so a refusal notice built
    from the set can never disagree with the single-gate decision it
    explains.
    """
    if delivery_gate not in _BUILTIN_GATES:
        return False
    try:
        from .gates import import_gate
        mod = import_gate(delivery_gate)
    except ImportError:
        return False
    is_configured = getattr(mod, "is_configured", None)
    return bool(is_configured) and bool(is_configured(brr_dir))


def _gate_can_deliver(brr_dir: Path, gate: str) -> bool:
    """True when *gate* or its delivery alias is configured here.

    Guards out-of-bound delivery against typos and unconfigured gates: a
    synthesized event for a gate no thread is polling would sit undelivered
    forever, so an unknown/unconfigured target is dropped with a note
    instead.
    """
    return _gate_is_configured(brr_dir, _delivery_source_for_gate(gate))


def _configured_gate_names(brr_dir: Path) -> list[str]:
    """Delivery-loop gate names deliverable on this account.

    Catalog order (:data:`_BUILTIN_GATES`), built from the same
    :func:`_gate_is_configured` probe :func:`_gate_can_deliver` uses — a
    refusal notice quoting this list can never name a set that disagrees
    with the single-gate decision it explains.

    These are delivery-loop names, and every one of them is also addressable
    as-is in an agent's ``gate:`` key, so the list is always actionable. It is
    not the *complete* set of accepted spellings: :func:`_delivery_source_for_gate`
    additionally accepts ``forge`` as an alias for ``github``, which this list
    does not render. Harmless where it is read — the notice only fires for a
    gate that was refused, and an account with ``github`` configured never
    refuses ``forge`` in the first place. Revisit if a second alias appears,
    or if the alias map ever stops being one-way.
    """
    return [name for name in _BUILTIN_GATES if _gate_is_configured(brr_dir, name)]


def _gate_owns_source(source: str) -> bool:
    """True when some gate's delivery loop claims events of *source*.

    Deliberately weaker than :func:`_gate_can_deliver`: this asks whether
    the source is a gate's at all, not whether that gate is configured on
    this host. Dispatch-tree sources (``spawn``, ``spawn_completed``,
    ``dispatch_message``, ``schedule``) are owned by nobody, and a reply
    addressed to one can never arrive — that is a property of the
    protocol. A *configured-gate* source with the gate switched off is a
    different, recoverable condition, and must not be recorded as an
    impossible delivery.
    """
    return _delivery_source_for_gate(source) in _BUILTIN_GATES


def _terminal_reply_lands(
    source: str, *, spawn_parent_run_id: str = "",
) -> bool:
    """True when a run's terminal reply has somewhere to arrive.

    Exactly two things carry a terminal stream out of the run: a gate that
    owns *source* (:func:`_gate_owns_source`), or a spawning parent that
    collects the child's report along the dispatch edge. Anything else —
    a ``schedule`` wake most of all — leaves the final stdout captured to
    the response path as the run's own body/message store and nowhere
    else, and a reply addressed at that event can never be delivered.

    An *absent* source is unknown, not impossible: treated as landing, so
    a missing field never manufactures a false "nobody will see this".

    Shared by the dispatch path (terminal-stream suppression) and the
    portal state (``inbound.current_event_replyable``, which the Stop-hook
    delivery warning reads) so both speak from one fact rather than from
    a source-name string match.
    """
    if not source:
        return True
    return bool(spawn_parent_run_id) or _gate_owns_source(source)


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
    outbox_dir: Path | None = None,
    message_path: Path | None = None,
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
        _record_outbox_notice(
            outbox_dir, f"gate message dropped: gate {gate!r} had no body/inbox",
        )
        return False
    if not _gate_can_deliver(emit.brr_dir, gate):
        if ":" in gate:
            # Looks like a thread/conversation-key string (the
            # ``schedule:...`` / ``telegram:...`` shape), not a bare gate
            # name — this is the one case that fixed-string diagnosis is
            # actually true for.
            _record_outbox_notice(
                outbox_dir,
                f"gate message dropped: {gate!r} is not a configured gate — "
                f"the `gate:` key takes a bare gate name (e.g. `telegram`), "
                f"not a thread string; the message was NOT delivered",
            )
        else:
            configured = _configured_gate_names(emit.brr_dir)
            _record_outbox_notice(
                outbox_dir,
                f"gate message dropped: {gate!r} is not deliverable on this "
                f"account (configured gates: "
                f"{', '.join(configured) if configured else 'none'}); the "
                f"message was NOT delivered",
            )
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
        inbox_dir,
        event_source,
        "",
        status="done",
        run_id=task.id,
        repo_label=str(getattr(task, "meta", {}).get("repo_label") or ""),
        **target_meta,
    )
    new_eid = new_path.stem
    protocol.write_response(
        responses_dir, new_eid, body, message_path=message_path,
    )
    if gate == "forge" and outbox_dir is not None:
        # Acceptance receipt, not a claim that the asynchronous forge gate has
        # already created the PR. The synchronous Stop flush makes this marker
        # visible to the closeout guard before it decides whether handoff intent
        # is missing.
        _write_text_atomic(
            outbox_dir / hooks_mod.FORGE_HANDOFF_NAME,
            f"event: {new_eid}\nhead: {target_meta.get('head', '')}\n",
        )
    print(f"[brnrd] outbox: queued out-of-bound message to gate {gate!r} ({new_eid})")
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
    state: dict[str, object],
    *,
    account_context: account.AccountContext | None = None,
    repo_label: str | None = None,
) -> bool:
    """Promote the agent-composed card narration into a ``card_composed`` packet.

    The resident owns its progress card's body via a single control file
    (``outbox/<eid>/.card``) — a dotfile, so it never enters the outbox
    drain as a deliverable message. On each heartbeat/boundary tick (plus a
    post-return recovery check) the daemon reads the file; when its content
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
        state["projection"] = ""
        state["written_monotonic"] = time.monotonic()
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
    projection = _card_now_projection(body)
    state["projection"] = projection
    state["written_monotonic"] = time.monotonic()
    emit(
        "card_composed",
        run_id=task.id,
        event_id=event_id,
        text=projection,
    )
    # A running run's node used to carry a frame and its traffic but no body,
    # because the body was only captured at closeout — so the one run a reader
    # is most likely to open, the one happening now, read as the emptiest.
    # The card is already on disk and already changed; mirroring it here costs
    # one write per actual card edit and makes the live node whole. Closeout
    # still captures the final card, so the durable body is unchanged.
    if account_context is not None and repo_label:
        try:
            _persist_run_body(
                account_context, task, repo_label=repo_label, card_path=card_path,
            )
        except Exception:  # noqa: BLE001 - a card-control bug must not break a run
            pass
    return True


def _card_now_projection(body: str) -> str:
    """Project a sectioned run body onto the compact live card.

    Existing one-note cards remain valid. A sectioned body exposes only its
    ``## Now`` section; the rest belongs to the permanent runfile, not the
    cramped live status card.
    """

    lines = body.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip().casefold() == "## now":
            start = index + 1
            break
    if start is None:
        return body
    projected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        projected.append(line)
    return "\n".join(projected).strip()


_MIRROR_CARD_GATES = ("telegram",)


def _emit_mirror_cards(
    emit: _WorkerEmit,
    task: Run,
    current_event_id: str,
    inbox_dir: Path,
    card_state: dict[str, object],
    *,
    final: bool = False,
) -> None:
    """Mirror the live card into waiting correspondents' own threads (#341).

    Card routing follows a run's *origin* thread, so when a chat message
    folds into (say) a schedule-sourced run as a pending event, its author
    watches a silent chat while their message is actively being worked.
    This emits one ``mirror_card`` packet per foreign-thread pending chat
    event so the gate can render a small "folded into a running thought"
    stub under the correspondent's own message. Events in the run's own
    thread are skipped — that chat already has the real card.

    State rides in ``card_state["mirrors"]`` for the life of the attempt.
    Packets fire on first sight, on narration change, and once on
    resolution: an event leaving the pending set mid-run was folded in
    ("answered"); one still pending at the final boundary/recovery drain stays
    "queued" for the next thought (*final*). Errors are swallowed — a
    mirror must never break a run.
    """
    try:
        if bool(task.meta.get("worker")):
            # A worker-stack run owns no thread and folds nothing (live
            # incident 2026-07-16: two spawn children stamped "folded into a
            # running thought" under the whole backlog of a chat that their
            # parent was actively answering). Workers stay silent here.
            return
        run_conv = task.conversation_key or ""
        mirrors = card_state.setdefault("mirrors", {})
        if not isinstance(mirrors, dict):  # pragma: no cover - state abuse
            return
        narration = str(card_state.get("projection") or card_state.get("last") or "")
        seen: set[str] = set()
        for ev in protocol.list_pending(inbox_dir):
            eid = str(ev.get("id") or "")
            if not eid or eid == current_event_id:
                continue
            if ev.get("status") != "pending":
                continue
            if ev.get("respawned_by_run") or ev.get("respawned_from_event"):
                continue
            source = str(ev.get("source") or "")
            if source not in _MIRROR_CARD_GATES:
                continue
            conv = conversations.conversation_key_for_event(ev) or ""
            if not conv or conv == run_conv:
                continue
            seen.add(eid)
            entry = mirrors.get(eid)
            if (
                isinstance(entry, dict)
                and entry.get("last") == narration
                and not final
            ):
                continue
            payload: dict[str, object] = {
                "run_id": task.id,
                "origin_conversation_key": run_conv,
                "origin_source": task.source,
                "source": source,
                "status": "queued" if final else "active",
                "agent_card_text": narration,
                "event_meta": {
                    k: v for k, v in ev.items()
                    if k.startswith(f"{source}_")
                },
            }
            updates.emit(emit.brr_dir, updates.UpdatePacket(
                type="mirror_card",
                conversation_key=conv,
                event_id=eid,
                payload=payload,
            ))
            mirrors[eid] = {"conv": conv, "last": narration, "payload": payload}
        # Resolution: a tracked mirror whose event left the pending set was
        # folded into this run — close its stub as answered.
        for eid in [k for k in mirrors if k not in seen]:
            entry = mirrors.pop(eid)
            if not isinstance(entry, dict):
                continue
            payload = dict(entry.get("payload") or {})
            payload["status"] = "answered"
            payload["agent_card_text"] = ""
            updates.emit(emit.brr_dir, updates.UpdatePacket(
                type="mirror_card",
                conversation_key=str(entry.get("conv") or ""),
                event_id=eid,
                payload=payload,
            ))
    except Exception:
        return


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
    *,
    account_context: account.AccountContext | None = None,
) -> None:
    """Emit inbox events for any self-scheduled thoughts that are now due.

    The reflex half of self-invocation: the resident owns ``schedule.md`` in
    its account-scoped dominion (legacy repo-local fallback supported); this
    reads it against daemon-owned firing-state and the clock, and fires due
    entries as ordinary ``schedule``-source events. Specs live in the
    dominion; firing-state lives in the runtime dir — the daemon never writes
    the agent's ``schedule.md``. Best-effort: any failure is swallowed so
    scheduling never wedges the loop. See ``kb/design-self-scheduled-
    thoughts.md``.
    """
    if not _schedule_enabled(cfg):
        return
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return
    try:
        candidates = dominion.resident_dominion_candidates(repo_root, cfg)
        dom = None
        entries = []
        for candidate in candidates:
            if not candidate.path.is_dir():
                continue
            entries = schedule_mod.parse_schedule(candidate.path)
            if entries:
                dom = candidate.path
                break
        if dom is None or not entries:
            return
        now = time.time()
        loaded_state = schedule_mod.load_state(brr_dir)
        signals = schedule_mod.load_signals(brr_dir)
        state = schedule_mod.apply_reset_signals(entries, loaded_state, signals, now)
        grace = float(
            cfg.get(
                "schedule.stale_grace_seconds",
                cfg.get("schedule_stale_grace_seconds", schedule_mod.DEFAULT_STALE_GRACE_S),
            )
        )

        # Quota-aware pacing (kb/design-director-loop.md §B1, decided
        # 2026-07-04): bend ambient `every:` cadence by observed quota —
        # never `at:` one-shots, those are deadlines. `schedule.due_entries`
        # stays pure; the bending happens here, to the entry list passed in.
        # There is no single "current run" outbox for an account-wide
        # scheduler tick, so this reads whatever cache sits at *brr_dir*
        # directly (shared, not per-run) for whichever runner is explicitly
        # pinned in config (`shell=` or legacy `runner=`, excluding `auto` —
        # `core=` names a model class, not a Shell, so it can't key the
        # per-Shell level collector). An unresolved runner name or a cold
        # cache just means no pacing signal this beat; the try/except this
        # function already wraps everything in covers that, so no separate
        # defensive layer is added here.
        scheduled_entries = entries
        dropped_ids: set[str] = set()
        pacing_snapshot: dict[str, object] = {"mode": "normal"}
        shell_pin = str(cfg.get("shell") or "").strip()
        runner_cfg = str(cfg.get("runner") or "").strip()
        runner_name = shell_pin or (
            runner_cfg if runner_cfg and runner_cfg != "auto" else ""
        )
        if runner_name:
            # claude_usage/claude_status only ever cache into a *run's*
            # outbox dir, never brr_dir itself — brr_dir has no "current
            # run" of its own here, so go find the freshest one a recent
            # run left behind (previously always missed, since brr_dir was
            # passed straight through and never held the cache file).
            levels_dir = brr_dir
            if claude_status.supported(runner_name):
                levels_dir = runner_quota.latest_claude_usage_outbox_dir(brr_dir) or brr_dir
            # Codex needs none of that hunt: its probe cache is account-scoped and
            # lives at brr_dir (`shared_dir`), warm across runs — which is what
            # makes *this* read meaningful at all. Pacing decides between runs,
            # exactly when the rollout file is frozen; before the app-server probe
            # existed, an idle daemon paced its schedule off whatever quota the
            # last Codex turn happened to leave behind, however old.
            sched_levels, _ = _collect_levels(
                runner_name, levels_dir, None, refresh=False, shared_dir=brr_dir,
            )
            # A scheduling tick has a pinned Shell/runner name, not
            # necessarily a committed Core — resolve it to a profile only to
            # read the Core it names, and fall back to no model (excluding
            # every per-model bucket, per binding_quota_remaining_pct's own
            # rule) rather than let an unresolvable pin wedge this tick.
            sched_model: str | None = None
            try:
                sched_model = runner.runner_profile(runner_name, repo_root).model
            except Exception:  # noqa: BLE001 - best-effort, never blocks pacing
                sched_model = None
            binding_pct = runner_quota.binding_quota_remaining_pct(
                sched_levels, model=sched_model,
            )
            if binding_pct is not None:
                if binding_pct < _quota_critical_floor_pct(cfg):
                    scheduled_entries = [e for e in entries if e.kind != "every"]
                    dropped_ids = {e.id for e in entries if e.kind == "every"}
                    pacing_snapshot = {
                        "mode": "quota-paused",
                        "remaining_pct": round(binding_pct, 1),
                    }
                elif binding_pct < _quota_low_floor_pct(cfg):
                    stretch = _quota_stretch_factor(cfg)
                    pacing_snapshot = {
                        "mode": "quota-paced",
                        "factor": stretch,
                        "remaining_pct": round(binding_pct, 1),
                    }
                    scheduled_entries = [
                        replace(e, interval=(e.interval or 0) * stretch)
                        if e.kind == "every" else e
                        for e in entries
                    ]

        due, new_state = schedule_mod.due_entries(
            scheduled_entries, state, now, stale_grace=grace,
        )
        # due_entries prunes new_state to the ids it was actually handed —
        # carry forward the anchor/last-fired record for entries the
        # critical-floor pause dropped this beat, so a recovering quota
        # resumes cadence instead of re-anchoring from zero.
        for did in dropped_ids:
            if did in state and did not in new_state:
                new_state[did] = state[did]
        # The dashboard must render the same effective schedule this tick
        # used. Without this receipt it applies the authored interval and can
        # call a deliberately stretched/paused wake "overdue".
        new_state["_pacing"] = pacing_snapshot
        for entry in due:
            body = entry.body or f"(self-scheduled thought: {entry.id})"
            # Thread the firing so a recurring entry's wakes share a
            # readable history; default per-entry, overridable to an
            # existing gate conversation.
            conv = entry.conversation_key or f"schedule:{entry.id}"
            protocol.create_event(
                inbox_dir, "schedule", body,
                schedule_id=entry.id, conversation_key=conv,
                repo_label=(
                    account_context.default_repo.label
                    if account_context is not None and account_context.enabled
                    else _repo_label(repo_root, {}, cfg)
                ),
            )
            print(f"[brnrd] schedule: fired {entry.id}")
        if new_state != loaded_state:
            schedule_mod.save_state(brr_dir, new_state)
    except Exception as exc:  # noqa: BLE001 - scheduling must never wedge the loop
        print(f"[brnrd] schedule: skipped tick ({exc})")


def _retire_internal_event(event: dict, responses_dir: Path) -> bool:
    """Retire a gateless (``schedule``-source) event after it completes.

    A self-scheduled thought has no delivery gate. Its terminal message is
    retained as ``undeliverable`` in the run store and the dispatch event is
    closed in place, preserving the same audit shape as every other target.
    """
    if event.get("source") != "schedule" or not event.get("_path"):
        return False
    protocol.set_status(event, "delivered")
    return True


_SPAWN_NOTIFY_RESPONSE_MAX_CHARS = 2000

# #574: the first `brr/<slug>` token in a spec is the branch it commits the
# child to — the convention every dispatch already uses (`_queue_spawn_
# request`'s own docstring). The lookbehind excludes a `brr/` reached via a
# preceding word char, `/`, or `.` so nothing in a spec *but* a branch name
# can masquerade as the branch commitment. All three are load-bearing and
# each names a token this repo's own spec bodies carry as a matter of course:
# `src/brr/daemon.py` (source anchors, `/`), and — the one the first cut of
# this check missed — `.brr/worktrees/<run-id>` and `.brr/outbox/…`, which
# every *host*-environment spawn spec names in its working rules, and which
# yielded a confident `brr/worktrees` as the "spec branch". A guard that
# false-positives on a worker that did everything right is worse than no
# guard: it teaches the reader to skip the flag, and it is gone the one time
# it is right (`hooks.py:441-444`, the #562 lesson, one module over).
_SPAWN_CONTRACT_BRANCH_RE = re.compile(r"(?<![\w/.])brr/[A-Za-z0-9][A-Za-z0-9_.-]*")
# The first `/tmp/brr-*.md` token is the report path convention every
# dispatch spec uses; the `/tmp/brr-` prefix is distinctive enough that no
# lookbehind is needed.
_SPAWN_CONTRACT_REPORT_RE = re.compile(r"/tmp/brr-[A-Za-z0-9][A-Za-z0-9_.-]*\.md")


def _extract_spawn_contract(spec_text: str) -> tuple[str | None, str | None]:
    """Pull the (branch, report) contract a spawn spec commits its child to.

    Mechanical, no model in the loop — the *first* match of each pattern,
    nothing smarter. Deliberately does **not** attempt an issue-number
    extraction: prose like "related: #565" would false-positive, and #574's
    own live case is a worker *inventing* a justifying reference to a wrong
    issue — a noisy heuristic there would be one more thing to explain away.
    Branch + report path are the load-bearing two.
    """
    branch_match = _SPAWN_CONTRACT_BRANCH_RE.search(spec_text)
    report_match = _SPAWN_CONTRACT_REPORT_RE.search(spec_text)
    return (
        branch_match.group(0) if branch_match else None,
        report_match.group(0) if report_match else None,
    )


def _spawn_contract_check(spec_text: str, publish_branch: str | None) -> dict | None:
    """Compare a spawn spec's contract against what the child actually did.

    Returns ``None`` when there is nothing to check — no ``brr/<slug>``
    branch named in the spec at all. Absence of a contract is not a
    mismatch (#574's own constraint): a spawn with no branch commitment
    behaves exactly as it always has.

    Otherwise returns a dict describing the comparison: ``mismatch`` is
    True when the published branch differs from the spec's, or a spec-
    named report path was never written. Pure string/filesystem-existence
    logic, no model call — a caller wraps this in try/except per the
    fail-open posture (`_notify_spawn_parent`'s docstring): a bug in this
    check must never itself read as a worker-run failure.

    Live case, 2026-07-22: run-260722-2337-pqav was specced for #564 on
    `brr/wake-request-source-gate`, delivered #565 on
    `brr/stopped-run-kb-credit`, and the completion event still said
    `status=done` — the parent believed it. This is the check that would
    have caught it.
    """
    spec_branch, spec_report = _extract_spawn_contract(spec_text or "")
    if not spec_branch:
        return None
    published = str(publish_branch or "").strip() or None
    branch_ok = published == spec_branch
    report_found: bool | None = None
    if spec_report:
        report_found = Path(spec_report).exists()
    report_ok = report_found is not False
    return {
        "mismatch": (not branch_ok) or (not report_ok),
        "spec_branch": spec_branch,
        "published_branch": published,
        "spec_report": spec_report,
        "report_found": report_found,
    }


def _notify_spawn_parent(inbox_dir: Path | None, task: Run) -> None:
    """Land a completion note in the spawning parent's own thread.

    kb/design-director-loop.md §"Concurrent sub-spawns" item 4: a live
    in-run notification, not the guessed-time review self-wake convention
    — reuses the exact mechanism a mid-run user event already rides
    (an ordinary pending inbox event sharing the parent's
    ``conversation_key``), so a parent still running sees it on its next
    ``inbox.json``/plan-boundary read via the existing
    ``_pending_events_for_agent`` path. Unlike the spawn-dispatch event
    itself, this one is *not* tagged with ``respawned_from_event``/
    ``respawned_by_run`` — the parent should react to it, not skip it.

    If the parent has already ended, this is simply the next ordinary
    dispatchable event — the daemon picks it up ``in the normal course,
    which is a reasonable stand-in for "someone should look at this."
    Best-effort: a spawn without parent linkage (or no inbox) is silently
    skipped rather than raising — a notification bug must never surface
    as a worker-run failure. Same posture governs the #574 contract check
    below: extraction/comparison failures are logged and swallowed, never
    allowed to turn a notify bug into an apparent worker failure.
    """
    if inbox_dir is None:
        return
    parent_run_id = task.meta.get("spawn_parent_run_id")
    if not parent_run_id:
        return
    conv = str(task.meta.get("spawn_parent_conversation_key") or "").strip()
    conv = conv or f"run:{parent_run_id}"

    # #574: does what the child actually published match the spec it was
    # given? ``task.body`` is the spec text as the child saw it — never
    # rewoven for a worker run (the burst-sibling weave at the top of the
    # dispatch loop is gated `if not is_worker_run`, and a spawned child is
    # always `worker: true`) — so it is safe to read straight off the
    # reaped ``Run`` here, no ``prompt.md`` reread needed.
    status_label = task.status
    contract_kwargs: dict = {}
    contract_block = ""
    try:
        contract = _spawn_contract_check(task.body, task.meta.get("publish_branch"))
    except Exception as exc:  # noqa: BLE001 - fail open, see docstring above
        print(f"[brnrd] spawn contract check failed for {task.id}: {exc}")
        contract = None
    if contract is not None and contract["mismatch"]:
        status_label = "contract-mismatch"
        contract_kwargs = {
            "spawn_contract_mismatch": True,
            "spawn_contract_spec_branch": contract["spec_branch"],
            "spawn_contract_published_branch": contract["published_branch"] or "",
        }
        if contract["spec_report"]:
            report_line = (
                f"spec report:       {contract['spec_report']} "
                f"({'found' if contract['report_found'] else 'MISSING'})"
            )
            contract_kwargs["spawn_contract_spec_report"] = contract["spec_report"]
            contract_kwargs["spawn_contract_report_found"] = bool(
                contract["report_found"]
            )
        else:
            report_line = "spec report:       (none named)"
        contract_block = (
            "\n\ncontract mismatch — spec vs published:\n"
            f"spec branch:       {contract['spec_branch']}\n"
            f"published branch:  {contract['published_branch'] or '(none)'}\n"
            f"{report_line}"
        )

    summary = f"concurrent spawn {task.id} finished: status={status_label}{contract_block}"
    response_path = task.meta.get("response_path")
    if response_path:
        try:
            text = Path(str(response_path)).read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            if len(text) > _SPAWN_NOTIFY_RESPONSE_MAX_CHARS:
                text = text[:_SPAWN_NOTIFY_RESPONSE_MAX_CHARS] + "\n…(truncated)"
            summary = f"{summary}\n\n{text}"
    try:
        completion = protocol.create_event(
            inbox_dir,
            "spawn_completed",
            summary,
            conversation_key=conv,
            spawned_by_run=task.id,
            spawn_parent_run_id=parent_run_id,
            **contract_kwargs,
        )
    except OSError as exc:
        print(f"[brnrd] spawn-completion notify failed for {task.id}: {exc}")
        return
    _mark_report_collected(task, completion)


def _mark_report_collected(task: Run, completion: Path) -> None:
    """Stamp a worker's terminal report as collected on the dispatch edge.

    No gate owns ``spawn``, so the report would otherwise sit ``pending``
    forever in the run's message store — the residue #454 named. It is not
    undeliverable either: the parent run reads it via the completion event's
    ``message_path``. That is a real receipt, so it gets stamped like one.
    """
    response_path = task.meta.get("response_path")
    if not response_path:
        return
    message_path = message_store.message_path_from_queue(Path(str(response_path)))
    if message_path is None:
        return
    message_store.transition(
        message_path,
        message_store.COLLECTED,
        gate="dispatch-edge",
        platform_message_id=completion.stem,
    )


def _notify_spawn_parent_of_crash(
    inbox_dir: Path | None, event: dict, error: BaseException,
) -> None:
    """Land a failure note for a concurrent spawn that crashed before finishing.

    Bug found live 2026-07-07 (issue: a spawned run's completion silently
    never reached its parent thread): the main loop's reap step called
    ``_notify_spawn_parent`` only in the success branch — when the worker
    future raised instead of returning a ``Run`` (a runner-launch failure,
    an unhandled exception mid-thought), the parent got no signal at all,
    silently contradicting design-director-loop.md's "Concurrent
    sub-spawns" promise that *completion* — not just success — lands back
    as a pending event. The only reason this hasn't landed as a genuinely
    lost result before is the dispatching run defensively writing its own
    guessed-time review self-wake as insurance; that's the degraded
    fallback, not something a crash should have to rely on every time.

    Built from the raw inbox *event* dict rather than a ``Run``/task
    object, since a worker that crashed before returning one never
    produces the richer object ``_notify_spawn_parent`` reads from.
    Best-effort, mirroring that function's own failure posture.
    """
    if inbox_dir is None:
        return
    parent_run_id = event.get("spawn_parent_run_id")
    if not parent_run_id:
        return
    conv = str(event.get("spawn_parent_conversation_key") or "").strip()
    conv = conv or f"run:{parent_run_id}"
    spawn_event_id = str(event.get("id") or "?")
    summary = f"concurrent spawn {spawn_event_id} crashed before finishing: {error}"
    try:
        protocol.create_event(
            inbox_dir,
            "spawn_completed",
            summary,
            conversation_key=conv,
            spawned_by_event=spawn_event_id,
            spawn_parent_run_id=parent_run_id,
            spawn_failed=True,
        )
    except OSError as exc:
        print(f"[brnrd] spawn-crash notify failed for {spawn_event_id}: {exc}")


def _finalize_stopped_run(
    emit: _WorkerEmit,
    task: Run,
    event: dict,
    eid: str,
    runs_dir: Path,
    env_backend,
    env_ctx,
    branch_plan,
    cfg: dict,
    control: dict,
    attempt: int,
    trace_dirs: list[str],
) -> Run:
    """Close out a run that was stopped — by its parent (wyrd §3 ``stop:``)
    or by the account owner from the dashboard (#476).

    A deliberate cancellation, not a failure: no retries, no runner
    fallback, no failure notices — those paths would relaunch the very
    work someone just killed. Partial work on the run's branch is salvaged
    exactly like a failed run's (`_capture_worktree`), the run ends as
    ``stopped``, and — for a spawned child — the ordinary reap path carries
    the completion note (``status=stopped``) back to the parent's thread.
    A stopped resident thought simply ends; the user watching the cell it
    came from is the one who asked.
    """
    stopped_by = str(control.get("stopped_by") or "")
    print(f"[brnrd] worker {eid}: stopped by parent {stopped_by or '?'}")
    if trace_dirs:
        task.meta["trace_dirs"] = ", ".join(trace_dirs)
    task.meta["stopped_by"] = stopped_by
    if control.get("stop_reason"):
        task.meta["stop_reason"] = str(control["stop_reason"])
    task.update_status("stopped", runs_dir)
    _set_event_status_if_present(event, "cancelled")
    _capture_worktree(task, env_ctx, branch_plan, cfg, runs_dir)
    emit("finalizing", run_id=task.id, stage="stopped")
    with _branch_lock(branch_plan.target_branch):
        task = env_backend.finalize(env_ctx, task, runs_dir)
    _emit_preserved_containers(emit, task)
    emit(
        "stopped",
        run_id=task.id,
        event_id=eid,
        stopped_by=stopped_by,
        attempts=attempt,
    )
    return task


def _capture_knowledge(
    repo_root: Path,
    cfg: dict,
    task: Run,
    *,
    event: dict | None = None,
    responses_dir: Path | None = None,
    outbox_dir: Path | None = None,
    terminal_reply: str | None = None,
) -> None:
    """Commit + push knowledge edits; replies now belong to the home run store."""
    if not bool(cfg.get("knowledge.capture", True)):
        return
    if task.status == "stopped":
        # The account-knowledge checkout is shared by every concurrent run,
        # live or stopped. A stopped run has no trustworthy ownership
        # boundary left in it: the dirty-tree sweep below would commit a
        # still-running sibling's uncommitted edits under *this* run's
        # identity, and the mid-run commit-window read would credit that
        # sibling's already-committed pages to this run's dashboard node
        # (#575, the other half of #565). The owning run's own capture net
        # still runs this same path when *it* finishes, so nothing here is
        # lost — only deferred to whoever is actually still working.
        # ``_capture_worktree`` (this run's own branch/worktree salvage,
        # already run by ``_finalize_stopped_run`` before this point) is
        # unaffected: it is not the shared checkout.
        return

    captured_pages: list[str] = []
    moved = knowledge.capture(
        repo_root, f"brnrd-kb: capture knowledge after run {task.id}", cfg=cfg,
        captured_pages=captured_pages,
        conversation_id=task.conversation_key or None,
        run_id=task.id,
    )
    if moved:
        print(f"[brnrd] knowledge: captured kb after {task.id}")
    # Union in pages the resident committed mid-run (#538): everything the
    # knowledge repo took between the run-start stamp and now, scoped to
    # this repo's pages *and* this run's own commits (#565) — a sibling's
    # commit inside the same shared-checkout window is filtered out by the
    # ``Brnrd-Run-Id`` trailer, not credited by proximity in time. The
    # capture commit above lands inside the window too, so dedupe against
    # the dirty-diff manifest before appending.
    seen_pages = set(captured_pages)
    for page in knowledge.committed_pages_in_window(
        repo_root, str(task.meta.get("kb_start_oid") or "") or None, cfg=cfg,
        run_id=task.id,
    ):
        if page not in seen_pages:
            captured_pages.append(page)
            seen_pages.add(page)
    reported_kb_paths = {
        str(record.get("path") or "").removeprefix("kb/")
        for record in relics.read_reported(outbox_dir)
        if record.get("kind") == "kb"
    }
    for page in captured_pages:
        if page in reported_kb_paths:
            continue
        url = knowledge.kb_page_url(repo_root, page, cfg)
        relics.append(
            outbox_dir, "kb", path=page, **({"url": url} if url else {}),
        )


def _capture_dominion(
    repo_root: Path,
    cfg: dict,
    task: Run,
    *,
    account_context: account.AccountContext | None = None,
) -> None:
    """Commit whatever the resident wrote into its dominion this thought.

    The persistence step of the agent-as-memory model: the resident edits its
    account-scoped dominion freely during a thought; brr captures those edits
    at sleep so they survive to the next wake without the agent running a
    commit dance. The legacy repo-local dominion is still captured when present
    so partially migrated installs do not lose notes.
    """
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return
    push = bool(
        cfg.get("dominion.push_on_capture", cfg.get("dominion_push_on_capture", True))
    )
    candidates = dominion.resident_dominion_candidates(repo_root, cfg)
    if account_context is not None and account_context.enabled:
        candidates.insert(
            0,
            dominion.ResidentDominion(
                path=account.repo_dominion_path(
                    account_context,
                    str(task.meta.get("repo_label") or account_context.default_repo.label),
                ),
                capture_root=account_context.dominion_repo,
                label="account",
            ),
        )
    seen_roots: set[Path] = set()
    for candidate in candidates:
        root = candidate.capture_root
        if not root.is_dir():
            continue
        try:
            key = root.resolve()
        except OSError:
            key = root
        if key in seen_roots:
            continue
        seen_roots.add(key)
        remote = gitops.default_remote(root)
        branch = gitops.current_branch(root)
        if branch == "HEAD":
            branch = None
        message = (
            f"brnrd-home: capture account memory after run {task.id}"
            if not candidate.legacy
            else f"brr-home: capture working memory after run {task.id}"
        )
        committed = dominion.commit(
            root,
            message,
            remote=remote,
            branch=branch,
            push=push and bool(remote),
            conversation_id=task.conversation_key or None,
        )
        if committed:
            print(f"[brnrd] dominion: captured working memory after {task.id}")


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
    if task.meta.get("root_kind") == "home":
        # Account-home capture owns commit and push after the run. Worktree
        # salvage would invent a second, forge-shaped publish lane here.
        return
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
                conversation_id=task.conversation_key or None,
                run_id=task.id,
            ):
                print(f"[brnrd] salvage: committed in-flight work for {task.id}")
        seed_ref = getattr(branch_plan, "seed_ref", None)
        if seed_ref and not worktree.has_commits_beyond(run_root, seed_ref):
            return
        task.meta["has_new_commit"] = True
        task.meta["publish_branch"] = branch
        task.meta["branch_name"] = branch
        task.save(runs_dir)
        print(f"[brnrd] salvage: arming publish of {branch} for failed {task.id}")
    except Exception as e:  # best-effort — never let salvage break the give-up path
        print(f"[brnrd] salvage: skipped for {task.id} ({e})")


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


def _backfill_dispatch_edges(account_context: account.AccountContext) -> int:
    """Recover dispatch edges for run nodes written before the field existed.

    The edge was never lost, only unrecorded on the node: ``run_ledger`` has
    carried ``parent_run_id`` on every spawned run since the ledger existed.
    This replays those rows onto the durable documents so the tree is
    navigable through its whole history rather than only forward from the
    first daemon that knew about the field.

    Idempotent, and it only ever writes an edge whose *child* document is
    really there — a ledger row for a run that left no node is a row about a
    run, not evidence of a node to link.
    """
    if not account_context.enabled or not account_context.runs_dir.is_dir():
        return 0
    recorded = 0
    for registered in account_context.repos.values():
        try:
            lines = run_ledger.ledger_path(registered.root).read_text(
                encoding="utf-8"
            ).splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            child_id = str(row.get("run_id") or "").strip()
            parent_id = str(row.get("parent_run_id") or "").strip()
            if not child_id or not parent_id:
                continue
            label = str(row.get("repo_label") or registered.label)
            child_path = account.run_dir(account_context, label, child_id) / "state.md"
            try:
                text = child_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if protocol.parse_frontmatter(text).get("parent_run_id") != parent_id:
                lines_out = text.splitlines()
                if not lines_out or lines_out[0] != "---":
                    continue
                try:
                    fm_end = lines_out.index("---", 1)
                except ValueError:
                    continue
                lines_out.insert(fm_end, f"parent_run_id: {parent_id}")
                protocol._atomic_write(child_path, "\n".join(lines_out) + "\n")
                recorded += 1
            _record_dispatch_edge(
                account_context,
                repo_label=label,
                parent_run_id=parent_id,
                child_run_id=child_id,
            )
    return recorded


def _existing_child_run_ids(path: Path) -> list[str]:
    """Read the accreted ``child_run_ids`` list off an existing state document."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    value = protocol.parse_frontmatter(text).get("child_run_ids")
    if not isinstance(value, str):
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _existing_produce_lines(path: Path) -> list[str]:
    """Recover the ``## Produce`` section already on a state document.

    Same accretion discipline as ``child_run_ids``: a rewrite that cannot
    re-derive produce (no work dir in scope at that call site) must not
    silently delete what an earlier write proved. Absence of evidence is not
    a manifest of nothing.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    marker = "\n## Produce\n"
    index = text.find(marker)
    if index < 0:
        return []
    section = text[index + 1:]
    # The section runs to the next heading of the same level or to EOF.
    for offset, line in enumerate(section.splitlines()):
        if offset and line.startswith("## "):
            section = "\n".join(section.splitlines()[:offset])
            break
    return ["", *section.rstrip().splitlines()]


def _run_state_produce_changed(
    task: Run,
    *,
    work_dir: Path | None,
    outbox_dir: Path | None,
) -> bool:
    """True when the run's produce has moved since the node was last written.

    Read-only: the fingerprint is only *stored* by the writer, so a probe
    that decides "no change" can never make the next write believe it
    already published something it did not.
    """
    if work_dir is None:
        return False
    try:
        branch, seed = relics.collection_scope(task.meta, Path(work_dir))
        # See run_ledger.build_closed_run_row: the shared-checkout fallback
        # scope needs a run-identity filter so a sibling's commit never
        # counts as "this node's produce changed" (#575).
        commit_run_id = task.id if not task.meta.get("branch_name") else None
        records = relics.collect(
            Path(work_dir),
            branch=branch,
            seed_ref=seed,
            outbox_dir=outbox_dir,
            commit_run_id=commit_run_id,
        )
    except Exception:
        return False
    return relics.fingerprint(records) != task.meta.get("run_state_produce_fingerprint")


def _run_state_produce_lines(
    path: Path,
    task: Run,
    *,
    work_dir: Path | None,
    outbox_dir: Path | None,
) -> list[str]:
    """The node's produce section: freshly collected, or preserved.

    Collection is the same path the ledger uses at closeout
    (``relics.collect``) rather than a second accounting, so the node and the
    receipt can never disagree about what a run made. Every failure degrades
    to the previously written section — produce is a convenience on a
    lifecycle attestation, and must never be able to fail a state write.
    """
    if work_dir is None:
        return _existing_produce_lines(path)
    try:
        branch, seed = relics.collection_scope(task.meta, Path(work_dir))
        commit_run_id = task.id if not task.meta.get("branch_name") else None
        records = relics.collect(
            Path(work_dir),
            branch=branch,
            seed_ref=seed,
            outbox_dir=outbox_dir,
            commit_run_id=commit_run_id,
        )
    except Exception:
        return _existing_produce_lines(path)
    task.meta["run_state_produce_fingerprint"] = relics.fingerprint(records)
    rendered = relics.render_markdown(records)
    return rendered if rendered else _existing_produce_lines(path)


def _record_dispatch_edge(
    account_context: account.AccountContext | None,
    *,
    repo_label: str,
    parent_run_id: str,
    child_run_id: str,
) -> Path | None:
    """Record the parent→child half of a Wyrd dispatch edge.

    The child half is one frontmatter field on the child's own document
    (``parent_run_id``), written by its state-doc writer from meta it already
    carries. The reverse half cannot be: a parent usually writes its final
    state document before, or concurrently with, the children it dispatched,
    so nothing in the parent's own closeout knows the child's run id.

    So the child stamps its parent, surgically — an append to the parent's
    existing ``child_run_ids`` list, leaving every other line of the daemon's
    attestation untouched. The list is order-preserving and deduplicated, and
    a parent whose document does not exist (different account, pruned, never
    written) is a no-op rather than a fabricated file: an edge is only ever
    recorded between two runs that both really happened.

    A fleet's children can close simultaneously, so the read-modify-write is
    held under a cross-process lock — kept in the system temp dir, since a
    lock file inside home would land in the account repo's own history. A
    lock that cannot be taken degrades to the unguarded write rather than
    dropping the edge: the boot backfill replays the ledger and repairs any
    edge a race did lose, so this path is self-healing either way.
    """
    if account_context is None or not account_context.enabled:
        return None
    if not parent_run_id or not child_run_id:
        return None
    path = account.run_dir(account_context, repo_label, parent_run_id) / "state.md"
    lock_name = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    with gitops.file_lock(Path(tempfile.gettempdir()) / f"brnrd-edge-{lock_name}.lock"):
        return _write_dispatch_edge(path, child_run_id)


def _write_dispatch_edge(path: Path, child_run_id: str) -> Path | None:
    """The locked read-modify-write half of :func:`_record_dispatch_edge`."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    try:
        fm_end = lines.index("---", 1)
    except ValueError:
        return None
    children: list[str] = []
    field_index: int | None = None
    for index in range(1, fm_end):
        key, separator, value = lines[index].partition(":")
        if separator and key.strip() == "child_run_ids":
            field_index = index
            children = [item.strip() for item in value.split(",") if item.strip()]
            break
    if child_run_id in children:
        return path
    children.append(child_run_id)
    rendered = f"child_run_ids: {', '.join(children)}"
    if field_index is None:
        lines.insert(fm_end, rendered)
    else:
        lines[field_index] = rendered
    protocol._atomic_write(path, "\n".join(lines) + "\n")
    return path


def _persist_run_state_doc(
    account_context: account.AccountContext | None,
    task: Run,
    *,
    repo_label: str,
    stage: str,
    cfg: dict | None = None,
    work_dir: Path | None = None,
    outbox_dir: Path | None = None,
) -> Path | None:
    """Write the durable account-level run-state document for *task*.

    This is the CS2/CS4 bridge: the live card remains a compact projection,
    while the account dominion repo gets a durable, git-mirrorable status
    object.  The document is intentionally simple markdown for the first slice;
    a richer renderer can project the same fields later.

    Records both ``run_state_path`` (the local store path, a dev breadcrumb)
    and, when the account dominion tracks a forge-hosted remote,
    ``run_state_url`` (the web-visible link) into ``task.meta`` so run surfaces
    can link the durable object rather than leak a host-local path to a remote
    reader.
    """
    if account_context is None or not account_context.enabled:
        return None
    root = account.run_dir(account_context, repo_label, task.id)
    path = root / "state.md"
    root.mkdir(parents=True, exist_ok=True)
    # ``Run.status`` has no *running* member in practice — the daemon moves a
    # task from "pending" straight to its terminal value, and execution lives
    # in the separate presence/run_progress lane. That left every mid-flight
    # node reporting "pending" for its whole life, and every run that died off
    # the clean closeout path frozen there forever (280 of 602 nodes on the
    # live account, 2026-07-19). The frame reports *execution* instead, which
    # is the question a reader inspecting a live run is actually asking.
    status = "running" if stage == "running" and task.status == "pending" else task.status
    lines = [
        "---",
        f"run_id: {task.id}",
        f"event_id: {task.event_id}",
        f"status: {status}",
        f"stage: {stage}",
        f"repo_label: {repo_label}",
        f"source: {task.source}",
    ]
    if task.conversation_key:
        lines.append(f"conversation_key: {task.conversation_key}")
    # Dispatch edge, child half (wyrd §1): who dispatched this run. `source`
    # already names the *kind* of dispatcher (user gate, schedule, spawn);
    # this names the identity, which is the only half a render cannot infer.
    parent_run_id = str(task.meta.get("spawn_parent_run_id") or "").strip()
    if parent_run_id:
        lines.append(f"parent_run_id: {parent_run_id}")
    # Preserve the reverse half stamped onto this document by children that
    # finished after it was last written. The writer owns every other line;
    # this one field is accreted from outside and must survive a rewrite.
    existing_children = _existing_child_run_ids(path)
    if existing_children:
        lines.append(f"child_run_ids: {', '.join(existing_children)}")
    has_new_commit = task.meta.get("has_new_commit") is True
    for key in (
        "runner_name",
        "runner_shell",
        "runner_core",
        "runner_class",
        "target_branch",
        "publish_status",
        "reply_archive",
        "success_signal",
        "pid",
    ):
        value = task.meta.get(key)
        if value not in (None, ""):
            lines.append(f"{key}: {value}")
    branch = (
        task.meta.get("branch_name") or task.meta.get("publish_branch")
    ) if has_new_commit else None
    if branch:
        for key in ("branch_name", "publish_branch"):
            value = task.meta.get(key)
            if value not in (None, ""):
                lines.append(f"{key}: {value}")
    # The resident-authored mood (`.mood` first line, #566 slice 0). Read
    # fresh at every persist so the running-stage frame tracks the live
    # face and the finished-stage frame keeps the last one the run wore.
    # Absent file → absent key: an unset mood is not a fact to invent.
    mood = run_ledger.read_run_mood_control(outbox_dir)
    if mood:
        lines.append(f"mood: {mood}")
    # The body used to restate status/stage/repo/source/event/runner as a
    # bullet list — every fact a verbatim copy of the frontmatter one screen
    # up, and the node renderer showed both (maintainer, 2026-07-19: the ask
    # is an information-dense, *non-repetitive* surface). The frontmatter is
    # the attested record; the body carries only what it alone knows: the
    # request excerpt and the produce manifest.
    lines.extend([
        "---",
        f"# Run {task.id}",
    ])
    if task.body:
        summary = " ".join(task.body.split())
        if len(summary) > 240:
            summary = summary[:239].rstrip() + "..."
        lines.extend(["", "## Request", "", summary])
    # Produce, on the node itself (maintainer, 2026-07-19: "the idea of the run
    # weld was that you maintain the relics as the run goes, and then at stop
    # the run file *with relics* is presented as the main summarized inspection
    # point"). Until now relics were collected only by
    # ``run_ledger.append_closed_run`` and rendered only from the ledger API,
    # whose window reaches back seven days — so a run's own permanent document
    # could never say what the run made, and a *live* run had no manifest at
    # all. Collecting here puts produce where the rest of the run's truth lives
    # and makes it accrue while the run is still working.
    produce_lines = _run_state_produce_lines(
        path, task, work_dir=work_dir, outbox_dir=outbox_dir,
    )
    lines.extend(produce_lines)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    task.meta["run_state_path"] = str(path)
    if parent_run_id:
        _record_dispatch_edge(
            account_context,
            repo_label=repo_label,
            parent_run_id=parent_run_id,
            child_run_id=task.id,
        )
    url = account.run_state_blob_url(account_context, path, cfg=cfg)
    if url:
        task.meta["run_state_url"] = url
    return path


def _persist_run_body(
    account_context: account.AccountContext | None,
    task: Run,
    *,
    repo_label: str,
    card_path: Path | None,
) -> Path | None:
    """Capture the resident-owned ``.card`` write-head as ``body.md``.

    The daemon composes none of this text. It copies the resident's final
    Markdown at closeout, keeping live writes separate from the attested
    ``state.md`` writer.
    """

    if account_context is None or not account_context.enabled or card_path is None:
        return None
    try:
        body = card_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not body:
        return None
    path = account.run_dir(account_context, repo_label, task.id) / "body.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    protocol._atomic_write(path, body + "\n")
    task.meta["run_body_path"] = str(path)
    return path


def _closed_ledger_run_ids(account_context: account.AccountContext) -> set[str]:
    """Return run ids proved closed by any repo ledger in this account."""
    closed: set[str] = set()
    for registered in account_context.repos.values():
        path = run_ledger.ledger_path(registered.root)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            run_id = str(row.get("run_id") or "") if isinstance(row, dict) else ""
            if run_id:
                closed.add(run_id)
    return closed


# A node's frame is unfinished in two shapes, and until 2026-07-19 the janitor
# only knew one of them. ``Run.status`` never actually reaches "running" — the
# daemon moves a task from "pending" straight to its terminal value — so a run
# that died off the clean closeout path froze at "pending", and the janitor,
# looking only for "running", walked past every one of them: 280 of 602 nodes
# on the live account, permanently claiming they had never started. The
# guardrail was guarding a state the writer no longer produced.
_UNFINISHED_RUN_STATUSES = frozenset({"running", "pending"})


def _reaped_run_state_text(text: str, *, reaped_at: str, reason: str) -> str:
    """Rewrite a RUNNING state document as a retained, explicit failure."""
    lines = text.splitlines()
    try:
        fm_end = lines.index("---", 1)
    except ValueError:
        return text
    replacements = {
        "status": "error",
        "stage": "reaped",
        "reaped_at": reaped_at,
        "reap_reason": reason,
    }
    seen: set[str] = set()
    for index in range(1, fm_end):
        key, separator, _value = lines[index].partition(":")
        if separator and key in replacements:
            lines[index] = f"{key}: {replacements[key]}"
            seen.add(key)
    for key, value in replacements.items():
        if key not in seen:
            lines.insert(fm_end, f"{key}: {value}")
            fm_end += 1
    for index in range(fm_end + 1, len(lines)):
        if lines[index].startswith("- status:"):
            lines[index] = "- status: error"
        elif lines[index].startswith("- stage:"):
            lines[index] = "- stage: reaped"
    return "\n".join(lines) + "\n"


def _retention_sweep(
    repo_root: Path,
    account_context: account.AccountContext | None,
) -> float:
    """Run one retention GC pass (#501); returns the configured cadence.

    Shares :mod:`brr.retention` with ``brnrd gc`` so the daemon never
    deletes anything the CLI's dry run would not have named. Config is
    read fresh each pass — window and cadence edits apply without a
    daemon restart. Returns the sweep interval in seconds (0 when the
    periodic pass is disabled); the caller re-arms with its own floor.
    Never raises: a janitor must not take the daemon down.
    """
    from . import retention

    try:
        cfg = conf.load_config(repo_root)
        interval_h = float(
            cfg.get(retention.SWEEP_INTERVAL_KEY,
                    retention.SWEEP_INTERVAL_DEFAULT_HOURS))
    except Exception:
        return 0.0
    if interval_h <= 0:
        return 0.0
    try:
        windows = retention.Windows.from_config(cfg)
        _plan, reports = retention.gc(
            repo_root, account_context, windows, dry_run=False)
        items = sum(r.items for r in reports.values())
        size = sum(r.bytes for r in reports.values())
        if items:
            print(
                f"[brnrd] retention sweep: deleted {items} item(s), "
                f"{retention.format_bytes(size)}"
            )
    except Exception as exc:  # noqa: BLE001 - janitor must never block the daemon
        print(f"[brnrd] retention sweep skipped: {exc}")
    return interval_h * 3600.0


def _sweep_zombie_runs(
    account_context: account.AccountContext | None,
) -> dict[str, int]:
    """Reap both run-truth stores of runs that are provably no longer running.

    Ran at boot only until 2026-07-19, which made a data repair something the
    *user* had to schedule: 279 phantom manifests sat in the published
    activity feed for as long as it took someone to be at a keyboard and
    restart the daemon. A janitor that can only run at boot is a janitor
    that runs when the mess is least likely to be noticed, so this is now a
    named sweep the daemon also performs on an interval.

    Both stores, always: the state doc feeds the Wyrd node and the manifest
    feeds the cloud activity publisher, and reaping one without the other is
    the exact split that produced those phantoms.
    """
    swept = {"state_docs": 0, "manifests": 0}
    if account_context is None:
        return swept
    try:
        reaped_state_docs = _reap_zombie_run_state_docs(account_context)
        swept["state_docs"] = len(reaped_state_docs)
        if reaped_state_docs:
            print(f"[brnrd] run-state janitor: reaped {len(reaped_state_docs)} zombie run(s)")
    except Exception as exc:  # noqa: BLE001 - janitor must never block the daemon
        print(f"[brnrd] run-state janitor skipped: {exc}")
    try:
        reaped_manifests = _reap_zombie_run_manifests(account_context)
        swept["manifests"] = len(reaped_manifests)
        if reaped_manifests:
            print(f"[brnrd] run-manifest janitor: reaped {len(reaped_manifests)} zombie run(s)")
    except Exception as exc:  # noqa: BLE001 - janitor must never block the daemon
        print(f"[brnrd] run-manifest janitor skipped: {exc}")
    return swept


def _reap_zombie_run_state_docs(
    account_context: account.AccountContext,
    *,
    now: float | None = None,
    ancient_after_seconds: float = _RUN_STATE_REAP_AFTER_SECONDS,
) -> list[Path]:
    """Reap account run-state docs that are provably no longer running.

    Presence is the live authority. With no matching presence (and no
    optional live pid recorded on the document), a closed ledger row proves
    the run ended; otherwise age supplies a conservative crash-recovery
    backstop. Documents are rewritten, never deleted.
    """
    if not account_context.enabled or not account_context.runs_dir.is_dir():
        return []
    timestamp = time.time() if now is None else now
    live_run_ids: set[str] = set()
    for registered in account_context.repos.values():
        for entry in presence.list_active(gitops.shared_brr_dir(registered.root), now=timestamp):
            run_id = str(entry.get("run_id") or "")
            if run_id:
                live_run_ids.add(run_id)
    closed_run_ids = _closed_ledger_run_ids(account_context)
    reaped: list[Path] = []
    reaped_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))
    for path in sorted(account_context.runs_dir.rglob("state.md")):
        try:
            text = path.read_text(encoding="utf-8")
            fields = protocol.parse_frontmatter(text)
            modified_at = path.stat().st_mtime
        except OSError:
            continue
        if str(fields.get("status") or "").casefold() not in _UNFINISHED_RUN_STATUSES:
            continue
        run_id = str(fields.get("run_id") or "")
        if not run_id or run_id in live_run_ids:
            continue
        try:
            pid = int(fields.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid and presence.pid_alive(pid):
            continue
        ledger_closed = run_id in closed_run_ids
        ancient = timestamp - modified_at >= ancient_after_seconds
        if not (ledger_closed or ancient):
            continue
        proof = "closed ledger row" if ledger_closed else "state document exceeded the boot safety horizon"
        reason = f"boot janitor: no live presence or pid; {proof}"
        protocol._atomic_write(
            path,
            _reaped_run_state_text(text, reaped_at=reaped_at, reason=reason),
        )
        reaped.append(path)
    return reaped


def _reap_zombie_run_manifests(
    account_context: account.AccountContext,
    *,
    now: float | None = None,
    ancient_after_seconds: float = _RUN_STATE_REAP_AFTER_SECONDS,
) -> list[Path]:
    """Reap local run manifests left unfinished by a killed daemon.

    The account run-state document and the repo's ``.brr/runs/<id>/run.md``
    manifest are two stores of the same fact, and until 2026-07-19 only the
    first one was ever reaped (see ``_reap_zombie_run_state_docs``, #481).
    That mattered because the manifest is what the cloud activity publisher
    reads: ``_run_activity_records`` reports exactly the pending/running
    manifests, so every run the daemon was killed out from under stayed in
    the account's ``/activity`` feed forever, claiming to be running. Live
    measurement on 2026-07-19: 279 of 281 published rows were phantoms, the
    oldest from 2026-06-21, against 279 stuck manifests on disk.

    Same proof discipline as the state-doc janitor — presence is the live
    authority, a closed ledger row proves the end, age is the conservative
    crash-recovery backstop — and the same retention stance: the manifest is
    rewritten as an explicit ``error``, never deleted.
    """
    if not account_context.enabled:
        return []
    timestamp = time.time() if now is None else now
    closed_run_ids = _closed_ledger_run_ids(account_context)
    reaped_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))
    reaped: list[Path] = []
    for registered in account_context.repos.values():
        brr_dir = gitops.shared_brr_dir(registered.root)
        runs_dir = brr_dir / "runs"
        if not runs_dir.is_dir():
            continue
        live_run_ids = {
            str(entry.get("run_id") or "")
            for entry in presence.list_active(brr_dir, now=timestamp)
        }
        for task in list_runs(runs_dir):
            if task.status.casefold() not in _UNFINISHED_RUN_STATUSES:
                continue
            if task.id in live_run_ids:
                continue
            manifest = run_manifest_path(runs_dir, task.id)
            try:
                modified_at = manifest.stat().st_mtime
            except OSError:
                continue
            ledger_closed = task.id in closed_run_ids
            ancient = timestamp - modified_at >= ancient_after_seconds
            if not (ledger_closed or ancient):
                continue
            proof = (
                "closed ledger row"
                if ledger_closed
                else "manifest exceeded the boot safety horizon"
            )
            task.meta["reaped_at"] = reaped_at
            task.meta["reap_reason"] = f"boot janitor: no live presence; {proof}"
            task.update_status("error", runs_dir)
            reaped.append(manifest)
    return reaped


def _repo_ledger_run_ids(repo_root: Path) -> set[str]:
    """Run ids proved closed by *repo_root*'s own ledger.

    Per-repo sibling of ``_closed_ledger_run_ids`` (which walks the account's
    registered repos): the spawn reconciliation sweep resolves each candidate
    event against the repo it would dispatch into, and must work in
    legacy repo-local mode too, where the account registry can be empty.
    """
    closed: set[str] = set()
    try:
        lines = run_ledger.ledger_path(repo_root).read_text(
            encoding="utf-8").splitlines()
    except OSError:
        return closed
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        run_id = str(row.get("run_id") or "") if isinstance(row, dict) else ""
        if run_id:
            closed.add(run_id)
    return closed


def _recorded_pid_alive(task: Run) -> bool:
    """True when the pid persisted on a run manifest is still running.

    The manifest records the *dispatching daemon's* pid (see the
    ``task.meta["pid"]`` write in ``_run_worker``): the runner subprocess is
    that process's child, so a live recorded pid means the dispatch may
    still be in flight and the sweep must not touch it.
    """
    try:
        pid = int(task.meta.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return bool(pid and presence.pid_alive(pid))


def _mark_interrupted_runs(
    account_context: account.AccountContext,
    repo_root: Path,
    cfg: dict,
    *,
    now: float | None = None,
    stale_after_seconds: float = _RUN_STATE_REAP_AFTER_SECONDS,
) -> int:
    """Boot-time marker for runs a dead daemon left frozen mid-flight (#316).

    A host suspend/crash/OOM kills the daemon between a run's dispatch and
    its terminal packet, so the chat status card freezes on its last
    rendered text ("running · 1m 03s") with no signal that anything went
    wrong. The event-retry mechanism already recovers the *work* — the
    still-``processing`` event is re-dispatched as a new run — but nothing
    ever tells the old run's card its story ended. This sweep closes
    exactly that gap: it marks the orphaned run record with a dedicated
    ``failure_kind`` (``host_interrupted``) and emits the terminal
    ``failed`` packet the dead daemon never got to send, so the frozen
    card re-renders as "interrupted" (plus a retry note when the event is
    still dispatchable). It deliberately touches **no event state** — the
    retry path stays exactly as it was.

    Proof discipline mirrors ``_reconcile_orphaned_spawn_dispatches``
    below, conservative by construction:

    - **presence is the live authority**: a live presence entry for the
      run ⇒ leave it alone;
    - **the recorded dispatcher pid**: the manifest's ``pid`` still alive
      ⇒ the dispatching process may still be mid-flight ⇒ leave it alone
      (this also protects dev-reload re-execs, which keep the same pid);
    - **proof of death**: a recorded pid that is *dead* is affirmative
      proof here — ``_run_worker`` persists the dispatching daemon's pid
      on the manifest precisely "so that future boot can prove the
      process which owned the run is gone". Unlike the spawn-event sweep
      (where an event may have no run record or pid at all), every
      manifest this sweep judges documents its owner. Manifests with no
      recorded pid fall back to the janitors' conservative staleness
      horizon (*stale_after_seconds*, default 24h) — the timestamp is
      the fallback, never the preferred evidence.

    Idempotent by the status transition itself: the sweep moves the
    manifest to ``error``, and a terminal manifest never matches the
    unfinished-status filter again, so a second boot cannot double-emit.
    Runs before the zombie janitors in ``start()`` so a manifest they
    would silently reap gets its card told first.
    """
    timestamp = time.time() if now is None else now
    marked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))
    # Events still eligible for the main loop's crash-recovery re-dispatch:
    # the honest basis for the card's "retrying" tail. Read, not asserted —
    # the sweep never changes event state.
    retry_eligible: set[str] = set()
    try:
        for target in _dispatchable_targets(account_context, repo_root, cfg):
            eid = str(target.event.get("id") or "")
            if eid:
                retry_eligible.add(eid)
    except Exception:  # noqa: BLE001 - retry hint is advisory, never blocking
        pass
    roots: dict[Path, Path] = {}
    for candidate in [repo_root] + [
        registered.root for registered in account_context.repos.values()
    ]:
        try:
            key = candidate.resolve()
        except OSError:
            key = candidate
        roots.setdefault(key, candidate)
    marked = 0
    for root in roots.values():
        brr_dir = gitops.shared_brr_dir(root)
        runs_dir = brr_dir / "runs"
        if not runs_dir.is_dir():
            continue
        live_run_ids = {
            str(entry.get("run_id") or "")
            for entry in presence.list_active(brr_dir, now=timestamp)
        }
        for task in list_runs(runs_dir):
            if task.status.casefold() not in _UNFINISHED_RUN_STATUSES:
                continue
            if task.id in live_run_ids:
                continue
            if _recorded_pid_alive(task):
                continue
            try:
                pid = int(task.meta.get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid:
                proof = f"its dispatching daemon (pid {pid}) is gone"
            else:
                try:
                    modified_at = run_manifest_path(
                        runs_dir, task.id).stat().st_mtime
                except OSError:
                    continue
                if timestamp - modified_at < stale_after_seconds:
                    # No pid recorded and still inside the safety horizon:
                    # "not provably alive" is not "provably dead".
                    continue
                proof = (
                    "no recorded pid and no manifest write inside the "
                    "safety horizon"
                )
            will_retry = bool(task.event_id and task.event_id in retry_eligible)
            # Status first, packet after — same order as the spawn
            # reconciliation sweep: a crash between the two loses one card
            # update; the reverse order would re-emit on every restart
            # that hits the window.
            task.meta["failure_kind"] = runner_failures.HOST_INTERRUPTED
            task.meta["interrupted_at"] = marked_at
            task.meta["interrupt_reason"] = (
                f"boot interrupted-run marker: {proof}"
            )
            task.update_status("error", runs_dir)
            error_text = (
                "the daemon was interrupted (host suspend/crash) while "
                "this run was in flight"
            )
            if will_retry:
                error_text += "; retrying the event on a fresh run"
            _WorkerEmit(brr_dir, task.conversation_key, task.event_id)(
                "failed",
                run_id=task.id,
                event_id=task.event_id,
                stage="interrupted",
                failure_kind=runner_failures.HOST_INTERRUPTED,
                error=error_text,
            )
            marked += 1
            print(
                f"[brnrd] interrupted-run marker: {task.id} marked "
                f"host_interrupted ({proof})"
            )
    return marked


def _reconcile_orphaned_spawn_dispatches(
    account_context: account.AccountContext,
    repo_root: Path,
    cfg: dict,
    *,
    now: float | None = None,
    stale_after_seconds: float = _RUN_STATE_REAP_AFTER_SECONDS,
) -> int:
    """Boot-time reconciliation for spawn dispatches orphaned by a daemon death.

    Issue #311, option (2) — no new persisted state. ``active_spawns`` (the
    in-memory link between a dispatched ``spawn:`` child and the parent
    conversation waiting on its completion note) dies with the daemon
    process; everything needed to reconstruct the notification is already
    durable on the spawned event itself (``spawn_parent_run_id`` /
    ``spawn_parent_conversation_key``, written by ``_queue_spawn_request``
    and never stripped by ``Run.from_event`` — #268's hand-traced seam). So
    on startup, sweep for spawn events stuck in ``processing`` whose worker
    is provably no longer running and deliver the crash notification the
    reap block would have delivered.

    Proof discipline mirrors the zombie-run janitors above, conservative by
    construction — a daemon death can leave an *orphaned runner subprocess*
    still writing, so "not in my in-memory table" is never evidence:

    - **presence is the live authority**: any live presence entry for a run
      dispatched from this event ⇒ leave it alone;
    - **the recorded dispatcher pid**: the manifest's ``pid`` still alive ⇒
      the dispatching process (the runner's parent) may still be mid-flight
      ⇒ leave it alone;
    - **proof of death**: a closed ledger row for the event's run (the
      worker finished; only the reap-notify was lost), or no write to the
      event file / run manifest for longer than *stale_after_seconds*
      (defaults to the janitors' 24h crash-recovery horizon — beyond any
      runner budget, keepalive-extended or not).

    Idempotent by the status transition itself: the sweep resolves the
    event to ``error``, and a resolved event never appears in
    ``list_pending`` again, so a second restart cannot double-notify.
    Resolving the status also stops the main loop's crash-recovery
    re-dispatch (``list_dispatchable`` treats ``processing`` as eligible)
    from re-running work whose parent has just been told it died.
    """
    timestamp = time.time() if now is None else now
    notified = 0
    runs_cache: dict[Path, list[Run]] = {}
    presence_cache: dict[Path, set[str]] = {}
    ledger_cache: dict[Path, set[str]] = {}
    for target in _dispatchable_targets(account_context, repo_root, cfg):
        event = target.event
        if str(event.get("status") or "") != "processing":
            continue
        if not event.get("spawn_immediate") or event.get("spawn_message_for_event"):
            continue
        if not event.get("spawn_parent_run_id"):
            continue
        eid = str(event.get("id") or "")
        if not eid:
            continue
        brr_dir = gitops.shared_brr_dir(target.repo_root)
        runs_dir = brr_dir / "runs"
        if runs_dir not in runs_cache:
            runs_cache[runs_dir] = list_runs(runs_dir)
        event_runs = [t for t in runs_cache[runs_dir] if t.event_id == eid]
        if brr_dir not in presence_cache:
            presence_cache[brr_dir] = {
                str(entry.get("run_id") or "")
                for entry in presence.list_active(brr_dir, now=timestamp)
            }
        if any(t.id in presence_cache[brr_dir] for t in event_runs):
            continue
        if any(_recorded_pid_alive(t) for t in event_runs):
            continue
        newest_write = _event_mtime(event)
        for t in event_runs:
            try:
                newest_write = max(
                    newest_write,
                    run_manifest_path(runs_dir, t.id).stat().st_mtime,
                )
            except OSError:
                pass
        if target.repo_root not in ledger_cache:
            ledger_cache[target.repo_root] = _repo_ledger_run_ids(
                target.repo_root)
        ledger_closed = any(
            t.id in ledger_cache[target.repo_root] for t in event_runs)
        ancient = timestamp - newest_write >= stale_after_seconds
        if not (ledger_closed or ancient):
            continue
        proof = (
            "its run closed in the ledger but the completion was never delivered"
            if ledger_closed
            else "no write for longer than the reconciliation safety horizon"
        )
        # Status first, notify after — same order as the live path (the
        # worker thread resolves the event before the reap block notifies).
        # A crash between the two loses one notification; the reverse order
        # would double-notify on every restart that hits the window.
        protocol.set_status(event, "error")
        try:
            protocol.update_event_meta(
                event,
                reconciled_at=time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp)),
                reconcile_reason=f"boot spawn reconciliation: {proof}",
            )
        except OSError:
            pass
        _notify_spawn_parent_of_crash(
            target.inbox_dir,
            event,
            RuntimeError(
                f"the daemon restarted while this spawn was in flight ({proof})"
            ),
        )
        notified += 1
        print(
            f"[brnrd] spawn reconciliation: notified parent of orphaned "
            f"spawn {eid} ({proof})"
        )
    return notified


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


def _terminal_stream_duplicates_delivered(task: Run, resp_path: Path) -> bool:
    """True when the captured terminal stream is byte-identical (modulo
    surrounding whitespace) to a reply this run already delivered to the
    waking thread via the outbox.

    The static-dispatch dedupe: with terminal delivery no longer *required*,
    a resident that already answered the thread mid-run may still end on the
    same text (the old contract trained exactly that). Exact match only — a
    terminal stream that says anything new still ships. Digests live on a
    dynamic run attribute (``_delivered_current_digests``), populated by
    ``_drain_outbox``; never serialized.
    """
    digests = getattr(task, "_delivered_current_digests", None)
    if not digests:
        return False
    try:
        body = protocol.frontmatter_body(
            resp_path.read_text(encoding="utf-8"),
        ).strip()
    except OSError:
        return False
    if not body:
        return False
    return hashlib.sha256(body.encode("utf-8")).hexdigest() in digests


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
    out-of-bound gate send), queued a respawn, made a new commit on the worktree
    branch, or the event is internal (schedule fire / dedup retire) and no
    thread reply is required. Stdout remains the common ``current_reply`` path,
    but it is no longer the *only* success signal — a run that committed work,
    answered a sibling thread, or parked a respawn is a successful run too.

    *signal* is one of ``current_reply | other_reply | outbound | respawn |
    commit | internal | ""`` (empty when not satisfied). The string surfaces on
    the ``done`` packet so renderers can name what the success was.
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
    if output_stats.get("respawn", 0) > 0:
        return True, "respawn"
    if has_new_commit:
        return True, "commit"
    if not _event_requires_thread_delivery(event):
        return True, "internal"
    return False, ""


def _seconds_config(
    cfg: dict,
    *keys: str,
    default: float,
) -> float:
    for key in keys:
        if key not in cfg:
            continue
        raw = cfg.get(key)
        if isinstance(raw, bool):
            return default if raw else 0.0
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            return default
        return max(0.0, seconds)
    return default


_MAX_CONCURRENT_SPAWNS_DEFAULT = 4


def _max_concurrent_spawns(cfg: dict) -> int:
    """Configured worker-stack ``spawn:`` pool width.

    Slice 1 (kb/design-director-loop.md §"Concurrent sub-spawns") shipped
    this hardcoded at a cap of 1. Generalized to a small configurable pool
    per kb/design-multi-workstream-concurrency.md "Ranked moves" #1 and the
    maintainer's 2026-07-08 call ("set the concurrency to 4 or something
    already"). ``spawn.max_concurrent`` in ``.brr/config``; clamped to at
    least 1 so a misconfigured 0/negative value can't silently wedge every
    ``spawn:`` request back into the ordinary sequential queue.
    """
    raw = cfg.get("spawn.max_concurrent", _MAX_CONCURRENT_SPAWNS_DEFAULT)
    if isinstance(raw, bool):
        return _MAX_CONCURRENT_SPAWNS_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _MAX_CONCURRENT_SPAWNS_DEFAULT
    return max(1, value)


def _post_delivery_attend_seconds(cfg: dict) -> float:
    """Configured daemon-owned dwell after a current-thread delivery.

    ``delivery.post_delivery_linger_seconds`` is accepted as an alias because
    the docs and older discussion called the whole behavior "linger". The code
    uses "attend" for the daemon floor so it does not overclaim same-thought
    runner residency.
    """
    return _seconds_config(
        cfg,
        "delivery.post_delivery_attend_seconds",
        "delivery.post_delivery_linger_seconds",
        default=_POST_DELIVERY_ATTEND_SECONDS_DEFAULT,
    )


def _post_delivery_attend_poll_interval(cfg: dict) -> float:
    return _seconds_config(
        cfg,
        "delivery.post_delivery_attend_poll_seconds",
        default=_POST_DELIVERY_ATTEND_POLL_INTERVAL,
    )


def _quota_low_floor_pct(cfg: dict) -> float:
    """Below this remaining-percent, `every:` entries stretch their interval."""
    return _seconds_config(
        cfg, "pacing.quota_low_floor_pct", default=_QUOTA_LOW_FLOOR_PCT_DEFAULT,
    )


def _quota_critical_floor_pct(cfg: dict) -> float:
    """Below this remaining-percent, `every:` entries don't fire this beat."""
    return _seconds_config(
        cfg, "pacing.quota_critical_floor_pct",
        default=_QUOTA_CRITICAL_FLOOR_PCT_DEFAULT,
    )


def _quota_stretch_factor(cfg: dict) -> float:
    """Multiplier applied to an `every:` entry's interval under the low floor."""
    return _seconds_config(
        cfg, "pacing.quota_stretch_factor", default=_QUOTA_STRETCH_FACTOR_DEFAULT,
    )


def _quota_pacing_status(
    cfg: dict,
    levels: "dict[str, object] | None",
    *,
    model: str | None = None,
) -> "dict[str, object] | None":
    """Binding quota remaining-percent + which pacing floor (if any) is live.

    Mirrors the check ``_fire_due_schedules`` uses to bend ``every:`` schedule
    cadence (kb/design-director-loop.md §B1), surfaced here so a mid-run
    boundary (``resources.quota.pacing``) sees the same number the scheduler
    used. ``None`` when the binding percent can't be proven this heartbeat
    (no collector, no numeric buckets) — never a fabricated read.

    *model* is the Core actually spending this bucket (the run's resolved
    ``core=``, or ``None`` at a scheduling tick that hasn't committed to a
    runner) — see :func:`runner_quota.binding_quota_remaining_pct` for the
    binding rule this passes through. A per-model bucket excluded from the
    floor because it names a different Core is still real information the
    user wants, so a thin one (below the low floor) rides along as
    ``excluded_thin`` — visible, non-binding.
    """
    pct = runner_quota.binding_quota_remaining_pct(levels, model=model)
    if pct is None:
        return None
    low_floor = _quota_low_floor_pct(cfg)
    floor = None
    if pct < _quota_critical_floor_pct(cfg):
        floor = "critical"
    elif pct < low_floor:
        floor = "low"
    status: "dict[str, object]" = {"binding_remaining_pct": pct, "floor": floor}
    excluded = runner_quota.excluded_week_model_buckets(levels, model)
    thin = sorted(
        label for label, remaining in excluded.items() if remaining < low_floor
    )
    if thin:
        status["excluded_thin"] = thin
    return status


def _should_post_delivery_attend(
    brr_dir: Path,
    task: Run,
    event: dict,
    *,
    signal: str,
    seconds: float,
) -> bool:
    if seconds <= 0:
        return False
    if signal != "current_reply":
        return False
    if not _event_requires_thread_delivery(event):
        return False
    if not task.conversation_key:
        return False
    source = str(event.get("source") or task.source or "").strip()
    if not source:
        return False
    return _gate_can_deliver(brr_dir, source)


def _post_delivery_attend(
    emit: _WorkerEmit,
    task: Run,
    event: dict,
    inbox_dir: Path,
    cfg: dict,
    *,
    signal: str,
    attempt: int,
) -> str:
    """Hold the daemon slot briefly after a delivered current-thread reply.

    This is deliberately weaker than runner-owned linger: the runner process has
    already returned, so a same-thread follow-up will become the next run rather
    than being answered inside the same thought. The value is still real: the
    card says "delivered · attending", the slot stays warm-ish for provider
    cache reuse, and unrelated work is not starved because any pending event
    ends the dwell immediately.

    Returns ``skipped | pending | quiet`` for tests and packet metadata.
    """
    seconds = _post_delivery_attend_seconds(cfg)
    if not _should_post_delivery_attend(
        emit.brr_dir, task, event, signal=signal, seconds=seconds,
    ):
        return "skipped"

    poll = _post_delivery_attend_poll_interval(cfg)
    if poll <= 0:
        poll = _POST_DELIVERY_ATTEND_POLL_INTERVAL
    poll = min(poll, max(seconds, 0.001))
    event_id = str(event.get("id") or task.event_id or emit.event_id)
    emit(
        "attending",
        run_id=task.id,
        event_id=event_id,
        seconds=int(seconds),
        reason="watching for follow-up after delivery",
    )
    started = time.monotonic()
    deadline = started + seconds
    next_heartbeat = started + _HEARTBEAT_INTERVAL
    while True:
        if _pending_events_for_agent(inbox_dir, event_id):
            # #351 interim guarantee: a follow-up that lands *during* the
            # attendance dwell must reach the normal dispatch path — never be
            # polled here and then silently dropped (the loss the maintainer
            # hit live: a same-thread message eaten on the one channel he
            # watches). The runner process has already exited, so this event
            # cannot fold into the current thought; it becomes the next run.
            #
            # The subtle seam this closes: ``create_event`` sets the inbox
            # wake when the follow-up arrives, but the main loop clears that
            # wake at the top of every iteration (protocol.inbox_wake()), and
            # while the attending run is still the in-flight ``current`` the
            # loop can't dispatch it (single-flight). By the time attendance
            # ends the wake can already be consumed, so nothing guarantees the
            # follow-up is re-scanned promptly. Re-arm it here so detecting the
            # event during attendance *is* its enqueue: the loop rescans and
            # dispatches on the next tick rather than waiting out the poll.
            protocol.inbox_wake().set()
            return "pending"
        now = time.monotonic()
        if now >= deadline:
            return "quiet"
        if now >= next_heartbeat:
            emit(
                "heartbeat",
                run_id=task.id,
                attempt=attempt,
                elapsed_seconds=int(now - started),
            )
            next_heartbeat = now + _HEARTBEAT_INTERVAL
        time.sleep(min(poll, max(0.0, deadline - now)))


def _failure_reason(
    last_failure: dict[str, object] | None,
    attempts: int,
) -> str:
    if last_failure:
        detail = str(last_failure.get("error") or "").strip()
        exit_code = last_failure.get("exit_code")
        kind = str(
            last_failure.get("failure_kind")
            or runner_failures.classify_failure(
                timed_out=bool(last_failure.get("timed_out")),
                exit_code=exit_code,
                detail=detail,
            )
        )
        prefix = runner_failures.reason_prefix(kind)
        if kind == runner_failures.INTERRUPTED:
            return f"{prefix} after {attempts} attempt(s)"
        if detail:
            return f"{prefix} after {attempts} attempt(s): {detail}"
        if exit_code is not None:
            return f"{prefix} after {attempts} attempt(s) with exit code {exit_code}"
    return f"runner produced no reply after {attempts} attempt(s)"


def _terminal_failure_body(
    reason: str,
    *,
    relay_candidate: str | None = None,
    relay_plan: dict[str, object] | None = None,
) -> str:
    body = (
        "I couldn't complete this run.\n\n"
        f"brr is surfacing this because {reason}."
    )
    if relay_candidate:
        plan = relay_plan if isinstance(relay_plan, dict) else {}
        model = str(plan.get("model") or "").strip()
        provider = str(plan.get("provider") or "").strip()
        total = str(plan.get("total_estimated_cost_usd") or "").strip()
        details = [relay_candidate]
        if model:
            details.append(f"model {model}")
        if provider:
            details.append(f"provider {provider}")
        if total and total.lower() != "none":
            details.append(f"estimated total ${total}")
        body += (
            "\n\nRelay fallback: brr found "
            + ", ".join(details)
            + " as a brnrd relay candidate. It did not spend relay tokens "
            "automatically; the approval/resume loop is a separate slice."
        )
    return body


def _deduplicated_event_body() -> str:
    return (
        "I already received this source message on another configured channel. "
        "No second run was started."
    )


def _trust_refused_event_body(reason: str) -> str:
    # Source-trust tiering (#517): a neutral, non-probing refusal. It states
    # policy without confirming any valid principal, mirroring the gates'
    # default-closed "don't help an attacker enumerate" posture.
    detail = f" ({reason})" if reason else ""
    return (
        "This request came from an untrusted source and no isolated "
        f"environment is available to run it safely{detail}. No run was "
        "started. An operator can configure `trust.untrusted_env` (e.g. "
        "`solitary` with a `docker.image`) to enable isolated runs for "
        "untrusted sources."
    )


def _crash_requires_notice(event: dict) -> bool:
    """True when a *hard failure* on this event should still surface.

    ``_event_requires_thread_delivery`` says "schedule" is internal — right
    for the success path, where a director tick that re-derived nothing new
    is correctly silent (the notify-bar logic, untouched by this). But a
    crash is never "did its job quietly": found live 2026-07-07
    (run-260707-1154-kem3, the 11:54 director tick) — killed mid-run
    (returncode 143, empty stdout/stderr), yet ``_event_requires_thread_delivery``
    made ``_write_terminal_failure_response`` return before writing anything,
    so the crash left zero trace anywhere the maintainer could see it.
    Silence-because-crashed and silence-because-nothing-changed rendered
    identically — indistinguishable from the one surface (chat) the
    maintainer actually watches. The gate can already route a schedule
    event's response via ``last_chat_id`` (PR #244), so this only needed
    the early-return relaxed for the failure path specifically.
    """
    return str(event.get("source") or "") == "schedule"


def _write_terminal_failure_response(
    emit: _WorkerEmit,
    task: Run,
    event: dict,
    responses_dir: Path,
    response_path: Path,
    reason: str,
    *,
    relay_candidate: str | None = None,
    relay_plan: dict[str, object] | None = None,
) -> bool:
    """Queue a terminal failure note for addressed events.

    The run record still stays ``error``; only the inbox event moves to
    ``done`` so the gate has a message to deliver and a cleanup signal.
    """
    if not _event_requires_thread_delivery(event) and not _crash_requires_notice(event):
        return False
    if _response_has_body(response_path):
        return False
    body = _terminal_failure_body(
        reason,
        relay_candidate=relay_candidate,
        relay_plan=relay_plan,
    )
    task.terminal_reply = body
    protocol.write_response(responses_dir, event["id"], body)
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
    *,
    account_context: account.AccountContext | None = None,
    inbox_dir: Path | None = None,
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
    eid = event.get("id", "?")
    failure_defer_seconds = float(
        cfg.get("dispatch.failure_defer_seconds", _FAILURE_DEFER_SECONDS_DEFAULT)
    )
    task = None
    try:
        try:
            task = _run_worker(
                event,
                repo_root,
                responses_dir,
                cfg,
                max_retries,
                account_context=account_context,
                inbox_dir=inbox_dir,
            )
        except Exception:
            # A crash here left ``task`` unset, so the event's own status
            # never advances past "processing" — and list_dispatchable
            # treats "processing" as still-eligible (crash-recovery after a
            # daemon restart). With nothing marking this attempt done, the
            # very next main-loop tick re-dispatches the identical event,
            # crashes again, and repeats with no backoff: an infinite
            # crash-restart loop, one fresh run-id every attempt. Found live
            # 2026-07-06 — a director-tick event (schedule-sourced, "host"
            # env, no per-run worktree isolation) looped 26+ times over
            # ~50 minutes, each attempt leaking an underegistered presence
            # entry, before being manually marked "error" mid-incident. The
            # underlying crash cause wasn't recoverable from this pass (the
            # daemon's own stdout isn't captured to a file — a real gap
            # named back separately) but no future crash of *any* cause
            # should be able to reproduce the loop, so this is a structural
            # backstop, not a fix for one bug: always retire the event
            # rather than leave it "processing" forever.
            print(
                f"[brnrd] run for {eid}: crashed before producing a Run:\n"
                f"{traceback.format_exc()}"
            )
            _set_event_status_if_present(event, "error")
            try:
                protocol.update_event_meta(
                    event,
                    defer_until=_format_utc_after(failure_defer_seconds),
                    defer_reason="worker_crash",
                )
            except OSError:
                pass
            raise
        if event.get("status") != "done":
            _set_event_status_if_present(event, task.status)
        if task.status == "error":
            print(f"[brnrd] run {task.id}: failed")

        outbox_path = (
            Path(str(task.meta["outbox_path"]))
            if task.meta.get("outbox_path") else None
        )
        # Before the ledger: the reply archive reports a ``reply`` relic, and
        # ``append_closed_run`` is what collects relics.
        _capture_knowledge(
            repo_root,
            cfg,
            task,
            event=event,
            responses_dir=responses_dir,
            outbox_dir=outbox_path,
            terminal_reply=task.terminal_reply,
        )
        try:
            run_ledger.append_closed_run(
                repo_root,
                task,
                cfg,
                outbox_dir=outbox_path,
                work_dir=repo_root,
            )
        except Exception as exc:  # noqa: BLE001 - ledger must not block delivery
            print(f"[brnrd] run {task.id}: run-ledger append failed: {exc}")

        publish(repo_root, task)
        publish_default_branch(repo_root, task)
        repo_label = str(task.meta.get("repo_label") or _repo_label(repo_root, event, cfg))
        _persist_run_body(
            account_context,
            task,
            repo_label=repo_label,
            card_path=outbox_path / _CARD_CONTROL_NAME if outbox_path else None,
        )
        _persist_run_state_doc(
            account_context,
            task,
            repo_label=repo_label,
            stage="finished",
            cfg=cfg,
            work_dir=repo_root,
            outbox_dir=outbox_path,
        )
        _capture_dominion(
            repo_root,
            cfg,
            task,
            account_context=account_context,
        )
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
        if task is not None and task.meta.get("outbox_path"):
            _remove_outbox(Path(str(task.meta["outbox_path"])))


# ── Burst coalescing (dispatch debounce) ────────────────────────────


def _events_share_thread(
    lead_event: dict,
    other: dict,
    *,
    correspondent_key: str,
    conversation_key: str,
) -> bool:
    """True when *other* belongs to the same human/thread as *lead_event*."""
    lead_conv = (
        conversation_key
        or conversations.conversation_key_for_event(lead_event)
        or ""
    )
    other_conv = conversations.conversation_key_for_event(other) or ""
    if lead_conv and other_conv:
        return lead_conv == other_conv
    lead_corr = (
        correspondent_key
        or conversations.correspondent_key_for_event(lead_event)
        or ""
    )
    other_corr = conversations.correspondent_key_for_event(other) or ""
    if lead_corr and other_corr:
        return lead_corr == other_corr
    return False


def _weave_burst_siblings_into_body(
    inbox_dir: Path,
    lead_event: dict,
    cfg: dict,
    *,
    correspondent_key: str,
    conversation_key: str,
) -> tuple[str | None, set[str]]:
    """Merge same-thread burst siblings into one wake task body.

    Burst coalescing already lands rapid fragments in one wake, but only
    the lead event's body reached the task text — siblings sat in the inbox
    capsule until the resident read it. Same-thread events that arrived
    within the burst window are woven here so actionable follow-ups are
    visible without relying on an early ``inbox.json`` read.
    """
    burst_window = float(
        cfg.get("dispatch.burst_window_seconds", _BURST_WINDOW_DEFAULT)
    )
    burst_max_wait = float(
        cfg.get("dispatch.burst_max_wait_seconds", _BURST_MAX_WAIT_DEFAULT)
    )
    if burst_window <= 0:
        return None, set()
    max_spread = max(burst_window, burst_max_wait)

    lead_id = str(lead_event.get("id") or "").strip()
    lead_body = str(lead_event.get("body") or "").strip()
    if not lead_id or not lead_body:
        return None, set()

    lead_mtime = _event_mtime(lead_event)
    siblings: list[tuple[float, dict]] = []
    for ev in protocol.list_pending(inbox_dir):
        eid = str(ev.get("id") or "").strip()
        if not eid or eid == lead_id or ev.get("status") != "pending":
            continue
        body = str(ev.get("body") or "").strip()
        if not body:
            continue
        if not _events_share_thread(
            lead_event,
            ev,
            correspondent_key=correspondent_key,
            conversation_key=conversation_key,
        ):
            continue
        mtime = _event_mtime(ev)
        if lead_mtime > 0 and mtime > 0 and abs(mtime - lead_mtime) > max_spread:
            continue
        siblings.append((mtime, ev))

    if not siblings:
        return None, set()

    siblings.sort(key=lambda item: (item[0], str(item[1].get("id") or "")))
    parts = [lead_body]
    woven_ids: set[str] = set()
    for _mtime, ev in siblings:
        parts.append(str(ev.get("body") or "").strip())
        woven_ids.add(str(ev["id"]))

    woven = parts[0]
    for idx, ev in enumerate((item[1] for item in siblings), start=1):
        sibling_body = str(ev.get("body") or "").strip()
        woven += (
            f"\n\n---\n\n"
            f"Follow-up {idx} (event {ev['id']}):\n"
            f"{sibling_body}"
        )
    return woven, woven_ids


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
        raise SystemExit("[brnrd] daemon already running")
    reload_mod.clear_reexec_marker()
    # Stamp what this process image was actually built from, before anything can
    # edit the checkout under us.  Runs again in the fresh process after every
    # re-exec, so the fingerprint always describes the code *currently
    # executing* — which is the only thing a spawn's boot can honestly claim.
    reload_mod.capture_image_fingerprint()
    if not (repo_root / "AGENTS.md").exists():
        raise SystemExit("[brnrd] run `brnrd init` first")

    _write_pid(brr_dir)
    running = True

    def _handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    cfg = conf.load_config(repo_root)

    def _report_update(observation: release_availability.Availability | None) -> None:
        if observation and (fact := observation.render()):
            print(f"[brnrd] {fact}")

    release_availability.refresh_if_stale_async(repo_root, on_complete=_report_update)
    account_context = account.resolve_context(repo_root, cfg)
    # #316: mark runs the previous daemon process left frozen mid-flight
    # so their chat cards read "interrupted" instead of stale running
    # text. Must run before the zombie janitors (which would silently
    # move the same manifests to terminal without telling the card) and
    # before the first dispatch scan (which re-dispatches the event as a
    # fresh run — the marker is what makes that card story truthful).
    try:
        interrupted = _mark_interrupted_runs(account_context, repo_root, cfg)
        if interrupted:
            print(
                "[brnrd] interrupted-run marker: marked "
                f"{interrupted} interrupted run(s)"
            )
    except Exception as exc:  # noqa: BLE001 - marker must not block boot
        print(f"[brnrd] interrupted-run marker skipped: {exc}")
    _sweep_zombie_runs(account_context)
    try:
        backfilled = _backfill_dispatch_edges(account_context)
        if backfilled:
            print(f"[brnrd] wyrd: recovered dispatch edges for {backfilled} run(s)")
    except Exception as exc:  # noqa: BLE001 - backfill must not block boot
        print(f"[brnrd] dispatch-edge backfill skipped: {exc}")
    # #311: reconcile spawn dispatches orphaned by a daemon death — must run
    # before the main loop's first dispatch scan, because list_dispatchable
    # treats "processing" as still-eligible and the spawn slot would
    # otherwise re-dispatch a stuck event before the sweep can judge it.
    try:
        reconciled = _reconcile_orphaned_spawn_dispatches(
            account_context, repo_root, cfg)
        if reconciled:
            print(
                "[brnrd] spawn reconciliation: notified "
                f"{reconciled} orphaned spawn dispatch(es)"
            )
    except Exception as exc:  # noqa: BLE001 - reconciliation must not block boot
        print(f"[brnrd] spawn reconciliation skipped: {exc}")
    for registered in account_context.repos.values():
        try:
            repo_brr_dir = gitops.shared_brr_dir(registered.root)
            migrated = message_store.migrate_legacy(
                account_context,
                repo_root=registered.root,
                repo_label=registered.label,
                brr_dir=repo_brr_dir,
            )
            if any(migrated.values()):
                print(
                    "[brnrd] message store: migrated "
                    f"{migrated['partials']} orphan partials and "
                    f"{migrated['replies']} archived replies for {registered.label}"
                )
            resolved = message_store.resolve_stranded(
                account_context,
                repo_label=registered.label,
                gate_owned=_gate_owns_source,
            )
            if any(resolved.values()):
                print(
                    "[brnrd] message store: resolved "
                    f"{resolved['collected']} collected and "
                    f"{resolved['undeliverable']} undeliverable stranded "
                    f"records for {registered.label}"
                )
        except Exception as exc:  # noqa: BLE001 - migration must not block boot
            print(f"[brnrd] message-store migration skipped for {registered.label}: {exc}")
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

    if (
        bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True)))
        and (not account_context.enabled or bool(cfg.get("dominion.legacy_repo_local", False)))
    ):
        try:
            dpath = dominion.ensure_dominion(
                repo_root,
                branch=str(cfg.get(
                    "dominion.branch",
                    cfg.get("dominion_branch", dominion.DEFAULT_BRANCH),
                )),
            )
            print(f"[brnrd] dominion ready: {dpath}")
        except Exception as exc:  # noqa: BLE001
            print(f"[brnrd] dominion bootstrap skipped: {exc}")

    if account_context.enabled and account_context.dominion_repo.exists():
        try:
            repo_dominion = account.repo_dominion_path(
                account_context,
                account.repo_label(repo_root, cfg),
            )
            dominion.seed_account_dominion(repo_dominion)
        except Exception as exc:  # noqa: BLE001
            print(f"[brnrd] account dominion seed skipped: {exc}")
        print(f"[brnrd] account dominion ready: {account_context.dominion_repo}")

    gate_threads = _start_account_gates(account_context, repo_root)
    if not gate_threads:
        print("[brnrd] warning: no gates configured — inbox will only receive events from `brnrd run` or scripts")

    if reload_watcher is not None:
        print("[brnrd] developer reload enabled")
    print(f"[brnrd] daemon started (pid {os.getpid()}, single-flight)")

    # Single-flight: one thought off the main thread at a time. A
    # one-slot executor keeps the clean future lifecycle (done / result /
    # drain-on-shutdown) while the loop stays responsive to dev-reload,
    # gate liveness, and signals during a long thought. The runner's own
    # wall-clock timeout (runner.timeout_seconds) is the liveness
    # backstop that reclaims the slot if the CLI subprocess wedges.
    # Second pool (kb/design-director-loop.md §"Concurrent sub-spawns";
    # generalized past cap-of-1 in kb/design-multi-workstream-concurrency.md
    # "slice 1"): `current` remains the one resident-stack thought
    # single-flight protects (dominion write, kb governance, scheduling).
    # `active_spawns` holds up to `_max_concurrent_spawns(cfg)` *worker-
    # stack-only* concurrent children a running thought can dispatch via
    # `spawn:` outbox frontmatter — none of them touch the surface
    # single-flight exists to protect, so they share a pool of their own
    # rather than waiting behind the resident's slot.
    max_spawns = _max_concurrent_spawns(cfg)
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1 + max_spawns,
        thread_name_prefix="brr-thought",
    )
    current: concurrent.futures.Future | None = None
    # The event id backing `current`, held so the run's stop control can be
    # retired when the thought is reaped (#476).
    current_eid: str | None = None
    # Each entry: {"future", "inbox_dir", "event"}. `event` is captured at
    # submission time so a crashed spawn (which never returns a Run/task
    # object) can still be notified back to its parent — see
    # _notify_spawn_parent_of_crash.
    active_spawns: list[dict] = []
    reload_requested = False
    # Accumulated changed-file keys since reload was first requested, bounded
    # at emit time by format_dev_reload_breadcrumb. A run may keep the reload
    # pending across several watcher polls, so keep every trigger until the
    # quiescent re-exec boundary.
    reload_changed_files: list[str] = []
    # The zombie sweep runs on a slow interval as well as at boot, so a
    # long-lived daemon repairs its own stores instead of waiting for the
    # next restart to notice. Slow on purpose: the only thing it can find
    # mid-life is a run killed out from under the daemon, which is rare and
    # never urgent — the cost of noticing it an hour late is nil, the cost of
    # rescanning both stores every tick is not.
    next_zombie_sweep = time.monotonic() + _ZOMBIE_SWEEP_INTERVAL_SECONDS
    # Retention GC (#501): the same code path as `brnrd gc`, on a slow clock.
    # Config is re-read at each sweep so window edits apply without a
    # restart; with no windows configured the sweep is a cheap no-op.
    next_retention_sweep = time.monotonic() + _RETENTION_FIRST_SWEEP_DELAY_SECONDS

    wake = protocol.inbox_wake()
    try:
        while running:
            if time.monotonic() >= next_zombie_sweep:
                next_zombie_sweep = time.monotonic() + _ZOMBIE_SWEEP_INTERVAL_SECONDS
                _sweep_zombie_runs(account_context)
            if time.monotonic() >= next_retention_sweep:
                interval_s = _retention_sweep(repo_root, account_context)
                next_retention_sweep = time.monotonic() + max(
                    interval_s, _RETENTION_FIRST_SWEEP_DELAY_SECONDS)
            # Consume any pending wake signal up front, before we read the
            # inbox below: a set that lands after this clear (a gate
            # enqueuing mid-iteration) keeps the flag set, so the wait at
            # the bottom returns promptly and the next pass picks it up —
            # no event is missed, and a busy iteration can't spin.
            wake.clear()

            # Poll the dev-reload watcher exactly once per iteration —
            # the main thread is its only caller, so the changed()
            # bookkeeping stays consistent.  Accumulate changed-file keys
            # so the pre-exec breadcrumb can name what actually changed.
            if reload_watcher is not None and reload_watcher.changed():
                reload_changed_files.extend(reload_watcher.last_changed)
                reload_requested = True

            # Reap the in-flight thought once it finishes.
            if current is not None and current.done():
                try:
                    current.result()
                except Exception as exc:  # noqa: BLE001
                    # The thought crashed before returning a Run; the
                    # operator sees the traceback in the daemon console.
                    print(f"[brnrd] thought crashed: {exc}")
                # The thought is over; its stop control retires with it, so a
                # stop parked against a finished run can never reach a later
                # one that reuses the handle.
                if current_eid:
                    _retire_run_control(current_eid)
                    current_eid = None
                current = None

            # Reap any concurrent worker-stack children that have finished,
            # and notify each one's still-running parent thought (or leave a
            # normal pending event behind if the parent already ended —
            # the next dispatch tick picks it up like any other follow-up,
            # standing in for the guessed-time review self-wake convention
            # this replaces for the common case).
            if active_spawns:
                still_running = []
                for spawn in active_spawns:
                    future = spawn["future"]
                    if not future.done():
                        still_running.append(spawn)
                        continue
                    try:
                        spawn_task = future.result()
                    except Exception as exc:  # noqa: BLE001
                        print(f"[brnrd] spawned child crashed: {exc}")
                        if spawn["event"] is not None:
                            _notify_spawn_parent_of_crash(
                                spawn["inbox_dir"], spawn["event"], exc,
                            )
                    else:
                        _notify_spawn_parent(spawn["inbox_dir"], spawn_task)
                    if spawn["event"] is not None:
                        # The child is over; its dispatch-edge control (and
                        # with it, stoppability) retires with it, as does any
                        # unconsumed parent→child message traffic.
                        spawn_eid = str(spawn["event"].get("id") or "")
                        _retire_run_control(spawn_eid)
                        _retire_child_messages(spawn["inbox_dir"], spawn_eid)
                active_spawns = still_running

            # Quiescent reload: only re-exec between thoughts, so a
            # running run can't have its process replaced underneath it.
            if reload_requested and current is None:
                # Emit a deliberate breadcrumb *before* exec so an operator
                # watching the terminal can tell this restart was intentional
                # (not a crash) and see what changed.
                print(
                    f"[brnrd] {reload_mod.format_dev_reload_breadcrumb(reload_changed_files)}"
                )
                pool.shutdown(wait=True)
                reload_mod.reexec()  # noreturn on success

            # Fire any self-scheduled thoughts that have come due — they
            # land in the inbox as ordinary events and queue behind a
            # running thought like any other (kb/design-self-scheduled-
            # thoughts.md). Runs every tick, busy or idle.
            _fire_due_schedules(
                repo_root,
                brr_dir,
                inbox_dir,
                cfg,
                account_context=account_context,
            )

            # Keep the local PR-state cache warm, off the loop thread, so the
            # wake's Forge block can render `#382 MERGED` beside a branch while
            # prompt assembly itself stays network-free (forge_pr_cache's whole
            # reason to exist). TTL-guarded: at most one `gh` round-trip every
            # few minutes, and never two at once.
            forge_pr_cache.refresh_if_stale_async(repo_root)

            # This is a daily, background observation; a release endpoint can
            # never delay dispatch or make the daemon unhealthy.
            release_availability.refresh_if_stale_async(repo_root)

            # One scan feeds both dispatch decisions below — a spawn-
            # marked event is never a resident-lead candidate and vice
            # versa, so splitting one pass avoids a second full inbox scan
            # (and a second round of ``list_pending`` I/O) every tick.
            # Scanning is gated on an open slot existing at all, not on
            # ``reload_requested`` — a pending package-file reload no
            # longer holds the spawn slot shut (see below); it still holds
            # the *resident* slot shut, so a scan purely for a fresh
            # resident dispatch would be wasted while reload is pending.
            scanned: list[_DispatchTarget] | None = None
            if len(active_spawns) < max_spawns or (
                current is None and not reload_requested
            ):
                scanned = _dispatchable_targets(account_context, repo_root, cfg)

            # Concurrent worker-stack children (slice 1, generalized past
            # cap-of-1): dispatched independently of the resident's own
            # `current` slot — that's the entire point, a spawn runs
            # *alongside* the still-live parent thought rather than after it
            # ends. Capped at `max_spawns` (`len(active_spawns) <
            # max_spawns`), scanned every tick regardless of whether the
            # resident slot is busy. No burst-settling here —
            # unlike a fresh external message, a `spawn:` request is
            # already one deliberate, already-complete dispatch decision
            # the parent made; nothing to debounce it against.
            #
            # Deliberately NOT gated on ``reload_requested`` (resolved
            # 2026-07-08) — and the reason matters, because the *original*
            # reason was falsified on 2026-07-13 and nearly took the
            # decision down with it.
            #
            # What that reason said: a spawn is a separate subprocess that
            # "does its own work by reading the checkout fresh off disk,
            # never by calling back into this process's in-memory daemon
            # module", so staleness could only touch "the few lines of
            # daemon orchestration code that submit/reap/notify it".
            #
            # **That is not true, and #386 is what made it untrue.** The
            # spawn is ``pool.submit(_run_worker_and_finalize, ...)`` — a
            # thread in *this* process — and it assembles the child's
            # entire boot prompt in *this* image. Prompt prose survives
            # that (``prompts.py`` ``read_text()``s ``*.md`` fresh on every
            # assembly, so an edit lands in the very next wake). Prompt
            # *code* does not: #386 moved the boot's opening from data into
            # code, and a running daemon renders the kernel and the
            # orientation list from the Python it imported at start. Blast
            # radius is not "a few lines of orchestration" — it is the
            # child's whole wake. Measured: on 2026-07-13 two spawned
            # children rendered the pre-#388 kernel, complete with the
            # worker-queue bug #388 had already fixed in the tree.
            #
            # And yet the decision stands, because **gating here is
            # incoherent**, not merely costly. Re-exec fires only on
            # ``reload_requested and current is None``: it waits for the
            # resident thread to finish. But the resident is the thing
            # doing the editing *and* the thing doing the spawning. A
            # reload triggered by the resident's own edit can never land
            # while that resident is still running — so gating spawn on it
            # would not delay spawn-after-your-own-edit, it would make it
            # impossible, permanently, by construction. The 2026-07-08 call
            # was more right than its stated reason, which is precisely why
            # nobody re-examined it when the reason died.
            #
            # So: dispatch, and **say so**. ``BootHost.image_stale`` puts a
            # ``stale: ⚠`` line in the child's kernel whenever this image
            # has been superseded by the checkout (``dev_reload``, ``.py``
            # only — ``.md`` is read fresh and is never stale). It does not
            # fix the staleness; it converts a silent false negative into a
            # loud one, which is the whole difference between a measurement
            # that is wrong and a measurement that is wrong and *believed*.
            # The structural fix — runs as subprocesses, so re-exec is free
            # and never waits on a thread — is a separate, larger slice.
            #
            # The vision this closes against
            # (2026-07-08, same-thread): "the daemon should do [the]
            # little possible work there, we just need to make sure the
            # runs don't step on each other's toes" — and toe-stepping is
            # what `environment: worktree` (unconditionally forced onto
            # every spawned event, Gap 1, shipped 2026-07-08) actually
            # guards, not process-image freshness. Holding the spawn slot
            # shut for an unrelated package edit crippled the primitive
            # for close to "most substantive resident turns" on this
            # repo's own dev-reload daemon (design-director-loop.md
            # "Finding 2/3") — and that cost is real whatever the
            # staleness risk turns out to be, which is why the fix is to
            # *report* the risk rather than to re-close the slot.
            # Re-exec itself is still safe: ``pool.shutdown(wait=True)``
            # below blocks on any in-flight spawn future exactly as it does
            # on ``current``, so a reload never kills a spawn mid-flight,
            # only defers replacing the process image until every active
            # spawn (and the resident thought) is done.
            open_spawn_slots = max_spawns - len(active_spawns)
            if open_spawn_slots > 0:
                # Dedup against events already claimed this tick or a prior
                # one: `list_dispatchable`/`list_pending` deliberately keep
                # returning "processing" events too (so a still-running
                # resident event stays visible for follow-up-folding), but
                # that means a spawn-marked event survives its own
                # `set_status(..., "processing")` write and reappears as a
                # "candidate" on every subsequent tick. The resident
                # dispatch path (above) is guarded by `current is None`
                # in-memory, which incidentally also blocks this
                # re-selection; the spawn pool has no equivalent single
                # in-flight flag to check once `max_spawns` > 1, so nothing
                # stopped the same event from filling every remaining open
                # slot in one tick, or across ticks before the pool filled.
                # Root-caused live 2026-07-08 (run-260708-2010-5sor): a
                # single `spawn:` outbox dispatch produced 4 concurrent
                # duplicate children (run-260708-2017-{zzc1,tgvx,a2kn,i8x6}),
                # each its own worktree, all working the same event —
                # exactly bounded by `max_spawns`, which is what gave the
                # bug away. Filter by event id against both the active pool
                # and events already selected earlier in this same tick.
                active_spawn_ids = {
                    spawn["event"].get("id") for spawn in active_spawns
                }
                spawn_candidates: list[_DispatchTarget] = []
                for t in (scanned or []):
                    if not t.event.get("spawn_immediate"):
                        continue
                    eid = t.event.get("id")
                    if eid in active_spawn_ids:
                        continue
                    active_spawn_ids.add(eid)
                    spawn_candidates.append(t)
                    if len(spawn_candidates) >= open_spawn_slots:
                        break
                for target in spawn_candidates:
                    event = target.event
                    eid = event["id"]
                    print(f"[brnrd] processing (concurrent spawn): {eid}")
                    protocol.set_status(event, "processing")
                    future = pool.submit(
                        _run_worker_and_finalize,
                        event,
                        target.repo_root,
                        target.responses_dir,
                        cfg,
                        max_retries,
                        account_context=account_context,
                        inbox_dir=target.inbox_dir,
                    )
                    active_spawns.append(
                        {
                            "future": future,
                            "inbox_dir": target.inbox_dir,
                            "event": event,
                        }
                    )

            # Spawn one thought when idle and work is pending. Events that
            # arrive while a thought runs stay pending — the living agent
            # picks them up at a plan boundary (multi-response), or the
            # next spawn handles them. Reload holds *this* (resident)
            # dispatch so the slot can drain and re-exec can proceed — a
            # fresh resident thought must not start on soon-to-be-stale
            # code. The concurrent-spawn slot above is no longer gated the
            # same way; see its own comment.
            burst_hold = 0.0
            if current is None and not reload_requested:
                pending = [
                    t for t in (scanned or [])
                    if not t.event.get("spawn_immediate")
                    # Edge traffic (`to:` messages) is injected into a live
                    # child's views, never dispatched as its own thought.
                    and not t.event.get("spawn_message_for_event")
                ]
                if pending:
                    pending = _handle_daemon_control_events(
                        pending,
                        account_context,
                    )
                if pending:
                    burst_hold = _burst_settle_delay(
                        [target.event for target in pending],
                        burst_window,
                        burst_max_wait,
                        time.time(),
                    )
                    if burst_hold <= 0:
                        # Dispatch the oldest as lead; the wake reads the
                        # whole settled burst and folds the rest in (the
                        # multi-response ``event:`` path), so a burst becomes
                        # one thought, not one spawn per fragment.
                        target = _apply_dashboard_wake_request(
                            pending[0], account_context, repo_root, cfg,
                        )
                        event = target.event
                        eid = event["id"]
                        extra = len(pending) - 1
                        suffix = f" (+{extra} pending)" if extra else ""
                        repo_suffix = (
                            f" [{target.repo_label}]" if target.repo_label else ""
                        )
                        print(f"[brnrd] processing: {eid}{repo_suffix}{suffix}")
                        protocol.update_event_meta(
                            event,
                            defer_until=None,
                            deferred_by_run=None,
                            defer_reason=None,
                        )
                        protocol.set_status(event, "processing")
                        # Register the resident thought's stop control before
                        # it is airborne (#476). `parent_run_id=None`: no run
                        # dispatched this, so no run may stop it — only the
                        # account owner, through the dashboard.
                        _register_run_control(eid, None)
                        current_eid = eid
                        current = pool.submit(
                            _run_worker_and_finalize,
                            event,
                            target.repo_root,
                            target.responses_dir,
                            cfg,
                            max_retries,
                            account_context=account_context,
                            inbox_dir=target.inbox_dir,
                        )

            # Event-driven idle wait: block until a fresh in-process event
            # wakes us or the poll tick elapses, whichever comes first.
            # The tick still bounds latency for cross-process writers (the
            # ``brnrd run`` CLI) and time-based work (due schedules), which
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
                    print(f"[brnrd] warning: gate thread {t.name} died")

    finally:
        # Shutdown requested (signal): kill every in-flight runner — the
        # resident's own thought *and* any live concurrent spawns — so the
        # pool drains promptly instead of waiting out their (long, possibly
        # extended) budgets. ``runner.kill_active`` became the small
        # per-invocation registry the old slice-1 note here asked for
        # (2026-07-18, the wyrd §3 stop-verb slice).
        if runner.kill_active():
            print("[brnrd] shutdown: terminated in-flight runner(s)")
        pool.shutdown(wait=True, cancel_futures=False)
        _clear_pid(brr_dir)
        print("[brnrd] daemon stopped")
