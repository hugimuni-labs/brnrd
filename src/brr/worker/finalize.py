"""finalize — the throw's ending, written.

``completed`` releases the Shuttle, transitions ``running → done``
(``runner_completed``), finalizes the environment under the branch lock and
emits ``done``; ``hold`` and ``stopped`` hand to the hold-shaped and
stop-shaped finalizers the loop always used; ``exhausted`` writes the
terminal failure, defers the siblings, salvages the worktree and emits
``failed``. The publish itself stays with the caller
(``_run_worker_and_finalize``), strictly after these packets.

The dominion capture the manual lists under finalize stays in ``stream``:
it runs once per *attempt*, before the stop check, so a retry re-captures —
moving it here would reorder it past the boundary classification.

Lines on ``main`` (``3def7ad6``): ``daemon.py:5489–5555`` and ``5728–5790``
(with the stop finalizer from ``4513``/``5220``, the hold finalizer from
``5489``/``5565``).
"""

from __future__ import annotations

from .. import account
from .. import runner_failures
from .. import shuttle

from .. import daemon
from .shapes import Boundary, Finalized, Prepared


def finalize(p: Prepared, b: Boundary) -> Finalized:
    if b.kind == "completed":
        return _finalize_completed(p, b)
    if b.kind == "hold":
        return _finalize_hold(p, b)
    if b.kind == "halt":
        return _finalize_halt(p, b)
    if b.kind == "stopped":
        return _finalize_stopped(p, b)
    if b.kind == "exhausted":
        return _finalize_exhausted(p, b)
    raise ValueError(f"finalize: a {b.kind!r} boundary loops back to dispatch")


def _finalize_stopped(p: Prepared, b: Boundary) -> Finalized:
    event = p.event
    cfg = p.cfg
    eid = p.eid
    runs_dir = p.runs_dir
    emit = p.emit
    task = p.task
    branch_plan = p.branch_plan
    env_backend = p.env_backend
    env_ctx = p.env_ctx
    trace_dirs = p.trace_dirs
    attempt = b.attempt.n
    stop_control = b.stop_control
    return Finalized(
        daemon._finalize_stopped_run(
            emit, task, event, eid, runs_dir, env_backend, env_ctx,
            branch_plan, cfg, stop_control, attempt, trace_dirs,
        ),
        "stopped",
    )


def _finalize_hold(p: Prepared, b: Boundary) -> Finalized:
    event = p.event
    repo_root = p.place_root
    responses_dir = p.responses_dir
    cfg = p.cfg
    account_context = p.account_context
    inbox_dir = p.inbox_dir
    eid = p.eid
    brr_dir = p.brr_dir
    runs_dir = p.runs_dir
    emit = p.emit
    task = p.task
    branch_plan = p.branch_plan
    env_backend = p.env_backend
    env_ctx = p.env_ctx
    resp_path = p.resp_path
    pending_hold = b.hold_spec
    return Finalized(
        daemon._finalize_resource_hold(
            emit, task, event, eid, runs_dir, env_backend, env_ctx,
            branch_plan, cfg, inbox_dir, responses_dir, resp_path,
            pending_hold, conversation_key=task.conversation_key,
            account_home=(
                account.context_home_root(account_context)
                if account_context is not None else brr_dir
            ),
            repo_root=repo_root,
        ),
        "held",
    )


def _finalize_halt(p: Prepared, b: Boundary) -> Finalized:
    """``halt:`` — the seat's own ending (design-the-four-stops.md).

    Shaped like ``_finalize_hold`` and landing somewhere else entirely: the
    same worktree-preservation sequence, because in-flight edits must
    survive an ending exactly as they survive a park, and then a
    **terminal** status rather than ``held``. Nothing resumes a halted run;
    the next message mints a new seat, which is precisely what makes
    ending cheap enough to be allowed without an approval gate.
    """
    return Finalized(
        daemon._finalize_halt(
            p.emit, p.task, p.event, p.eid, p.runs_dir, p.env_backend, p.env_ctx,
            p.branch_plan, p.cfg, p.inbox_dir, p.responses_dir, p.resp_path,
            b.halt_spec or {}, conversation_key=p.task.conversation_key,
            account_context=p.account_context,
            account_home=(
                account.context_home_root(p.account_context)
                if p.account_context is not None else p.brr_dir
            ),
            repo_root=p.repo_root,
        ),
        "halted",
    )


def _finalize_completed(p: Prepared, b: Boundary) -> Finalized:
    event = p.event
    repo_root = p.place_root
    account_context = p.account_context
    eid = p.eid
    brr_dir = p.brr_dir
    runs_dir = p.runs_dir
    emit = p.emit
    task = p.task
    branch_plan = p.branch_plan
    env_backend = p.env_backend
    env_ctx = p.env_ctx
    output_stats = p.output_stats
    terminal_reply = b.terminal_reply
    signal = b.success_signal
    has_new_commit = b.has_new_commit
    entity = shuttle.Shuttle.load(
        account.context_home_root(account_context)
        if account_context is not None else brr_dir
    )
    if not daemon._is_strand(task.meta) and entity.state in ("awake", "listening"):
        entity.transition(
            "released", why="runner_completed", by="daemon",
            run_id=task.id, repo_root=str(repo_root),
            conversation_key=task.conversation_key,
        )
    task.transition("done", why="runner_completed")
    daemon._set_event_status_if_present(event, "done")
    emit("finalizing", run_id=task.id, stage="done")
    # Per-branch lock around finalize: serialises publish on a
    # branch name so two pushers can't race it. Under single-flight
    # one daemon never contends here; the lock stays as cheap
    # insurance and a seam for a future concurrency revisit (see
    # kb/review-daemon-coherence-2026-06.md §4).
    with daemon._branch_lock(branch_plan.target_branch):
        task = env_backend.finalize(env_ctx, task, runs_dir)
    task.terminal_reply = terminal_reply
    daemon._cleanup_traces_on_success(brr_dir, runs_dir, task)
    daemon._emit_preserved_containers(emit, task)
    # Payload carries the multi-thread delivery shape so the card
    # can reflect "delivered to N threads" / "sent N out-of-bound"
    # / "no reply — committed work" instead of collapsing
    # everything to a single current-thread reply (§8 re-alignment).
    #
    # #1192: ``env_backend.finalize`` (just above) only classifies
    # the *local* worktree state — "ready" means "has commits queued
    # to publish", not "was published". The actual push
    # (``daemon.publish``) is invoked by this run's caller
    # (``_run_worker_and_finalize``), strictly after this packet is
    # emitted — measured 18s late in the incident that opened #1192,
    # by which time the parent, the ledger row, and this very packet
    # had already asserted ``publish_status=ready``. Forward the
    # honest "not settled yet" word instead and let the later
    # ``push_done`` / ``conflict`` packets say what actually
    # happened; ``nothing``/``detached``/``conflict`` are already
    # accurate at this point and pass through unchanged.
    done_publish_status = task.meta.get("publish_status")
    if done_publish_status == "ready":
        done_publish_status = "pending"
    emit(
        "done",
        run_id=task.id,
        event_id=eid,
        publish_branch=task.meta.get("publish_branch"),
        publish_status=done_publish_status,
        success_signal=signal,
        replies_current=output_stats.get("current", 0),
        replies_other=output_stats.get("other", 0),
        outbound_messages=output_stats.get("outbound", 0),
        respawn_requests=output_stats.get("respawn", 0),
        committed=has_new_commit,
    )
    return Finalized(task, "done")


def _finalize_exhausted(p: Prepared, b: Boundary) -> Finalized:
    event = p.event
    responses_dir = p.responses_dir
    cfg = p.cfg
    inbox_dir = p.inbox_dir
    eid = p.eid
    runs_dir = p.runs_dir
    failure_defer_seconds = p.failure_defer_seconds
    emit = p.emit
    task = p.task
    branch_plan = p.branch_plan
    env_backend = p.env_backend
    env_ctx = p.env_ctx
    resp_path = p.resp_path
    trace_dirs = p.trace_dirs
    attempt = b.attempt.n
    last_failure = b.attempt.last_failure
    relay_candidate = b.relay_candidate
    relay_plan = b.relay_plan
    if last_failure and last_failure.get("timed_out"):
        print(f"[brnrd] worker {eid}: timed out, giving up")
    else:
        print(f"[brnrd] worker {eid}: gave up after {attempt} attempt(s)")
    if trace_dirs:
        task.meta["trace_dirs"] = ", ".join(trace_dirs)
    # Move 5c: a run that errs before its `.topic` settled carries
    # `topic: None` + `topic_unset: True`; the next wake on the thread sees
    # `predecessor_topic_unset` in its bundle and may assign it once.
    _mark_topic_unset(p, task)
    task.update_status("error", runs_dir)
    failure_reason = _cite_earlier_failure(
        daemon._failure_reason(last_failure, attempt),
        last_failure,
        b.attempt.failures,
    )
    daemon._write_terminal_failure_response(
        emit,
        task,
        event,
        responses_dir,
        resp_path,
        failure_reason,
        relay_candidate=relay_candidate.summary() if relay_candidate else None,
        relay_plan=relay_plan.to_dict() if relay_plan else None,
    )
    daemon._defer_pending_siblings_after_failure(
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
    daemon._capture_worktree(task, env_ctx, branch_plan, cfg, runs_dir)
    # finalize first so any preserved branches / containers are recorded
    # on the run before the failure packet renders — the failure packet
    # is what gates see last, so its payload must be the canonical
    # explanation.
    emit("finalizing", run_id=task.id, stage="failed")
    with daemon._branch_lock(branch_plan.target_branch):
        task = env_backend.finalize(env_ctx, task, runs_dir)
    daemon._emit_preserved_containers(emit, task)
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
    return Finalized(task, "failed")


def _cite_earlier_failure(
    reason: str,
    last_failure: dict[str, object] | None,
    failures: list[dict[str, object]],
) -> str:
    """Name the latest earlier failure when the ending attempt recorded none.

    Move 3b's one reader of the old survival: ``last_failure`` used to outlive
    a plain retry, so a run whose attempt 1 dropped mid-response and whose
    attempt 2 then missed its artifact finalized *as the drop* — the right
    detail under the wrong attribution. ``last_failure`` is now the ending
    attempt's, so the reason says what the ending attempt did, and the
    history says what came before it. Only when the ending attempt has no
    failure of its own: that is the one case the survival used to supply,
    and a failed ending attempt's reason already names its cause.
    """
    if last_failure is not None or not failures:
        return reason
    earlier = failures[-1]
    kind = str(earlier.get("failure_kind") or "")
    detail = str(earlier.get("error") or "").strip()
    cited = runner_failures.reason_prefix(kind) if kind else "runner failed"
    if detail and kind != runner_failures.INTERRUPTED:
        cited = f"{cited}: {detail}"
    return f"{reason}; attempt {earlier.get('attempt')} before it: {cited}"


def _mark_topic_unset(p: Prepared, task) -> None:
    from .. import run_topic

    try:
        home = (
            account.context_home_root(p.account_context)
            if p.account_context is not None else None
        )
        run_topic.settle(
            task, outbox_dir=p.outbox_dir, account_home=home,
            inbox_dir=p.inbox_dir, is_strand=daemon._is_strand(task.meta),
        )
        run_topic.mark_unset_on_error(task, p.outbox_dir)
    except Exception:  # noqa: BLE001 - never sink a failure finalize
        return

