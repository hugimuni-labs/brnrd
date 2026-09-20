"""stream — the runner's life, boundary by boundary.

The four callbacks ``_invoke_with_heartbeat`` drives while the Shell runs —
the heartbeat (pause-cap sweep, drains, the ``running`` frame, presence),
the flush a native hook's ``.flush`` token fires (auth proof, drains, the
portal the next boundary reads back), pause-on-message, and the abort check
— then what the runner handed back: the observed core and session ids, the
recovery drain for Tier-0/1 Shells, the ``finalizing`` portal, the dominion
capture.

Lines on ``main`` (``3def7ad6``): ``daemon.py:4794–4803`` and ``4827–5212``.
"""

from __future__ import annotations

from .. import hooks as hooks_mod
from .. import hud
from .. import pause
from .. import presence
from .. import run_ledger
from .. import runner
from .. import runner_auth_health
import time
from dataclasses import replace

from .. import daemon
from .shapes import Dispatched, Prepared, Streamed


def stream(p: Prepared, dx: Dispatched) -> Streamed:
    repo_root = p.place_root
    responses_dir = p.responses_dir
    cfg = p.cfg
    account_context = p.account_context
    inbox_dir = p.inbox_dir
    eid = p.eid
    brr_dir = p.brr_dir
    repo_label = p.place_label
    is_strand_run = p.is_strand_run
    emit = p.emit
    task = p.task
    presence_id = p.presence_id
    shuttle_home = p.shuttle_home
    env_backend = p.env_backend
    env_ctx = p.env_ctx
    run_root = p.run_root
    execution_root = p.execution_root
    outbox_dir = p.outbox_dir
    card_path = p.card_path
    menu_path = p.menu_path
    flush_path = p.flush_path
    codex_events_path = p.codex_events_path
    card_state = p.card_state
    menu_state = p.menu_state
    output_stats = p.output_stats
    seen_containers = p.seen_containers
    run_started_monotonic = p.run_started_monotonic
    a = dx.attempt
    attempt = a.n
    runner_choice = a.lane.choice
    runner_name = a.lane.name
    runner_meta = a.lane.meta
    quota_summary = a.lane.quota_summary
    runner_env = a.lane.env
    extra_runner_args = a.lane.runner_args()
    run_hooks_installed = a.lane.hooks_installed
    runner_catalog = a.lane.catalog
    quality_escalation = a.lane.quality_escalation
    prompt = dx.prompt
    attempt_started_monotonic = dx.started_monotonic
    attempt_started_wall = dx.started_wall
    # Proof-of-auth for this attempt's runner domain: a live tool
    # boundary means the Shell authenticated, which is a stronger fact
    # than a clean process exit (`_record_runner_auth_health` below,
    # which only sees the *end* of the attempt). Reset every attempt so
    # a fallback runner earns its own proof rather than inheriting the
    # previous runner's. `_emit_flush` clears on the first boundary it
    # sees; a runner with no native hooks (Tier-0/1) or one that dies
    # before its first tool call never flips this, and the post-attempt
    # clear in `_record_runner_auth_health` still covers it.
    auth_health_cleared = False

    def _sweep_pause_cap() -> None:
        """Force-resume any pause past `seat.pause_cap_seconds` (spec
        step 5): a stopped process is a leak past its cap, not a pause —
        the correspondent it was frozen for may have moved on ages ago.
        Runs every heartbeat (10s), cheap when nothing is paused (one
        file read).
        """
        overdue = pause.overdue_records(outbox_dir)
        if not overdue:
            return
        pause.resume_pids(outbox_dir)
        daemon._record_outbox_notice(
            outbox_dir,
            "paused process(es) force-resumed after "
            f"{daemon._seat_pause_cap_seconds(cfg):.0f}s cap: "
            f"{pause.describe_paused(overdue)}",
            kind="advisory", lifetime="run",
        )

    def _emit_heartbeat() -> None:
        _sweep_pause_cap()
        daemon._refresh_codex_thread_id(task, codex_events_path)
        # Drain first: promoting an interim response is the resident's
        # mid-run check-in, and the partial should reach the gate as
        # promptly as the heartbeat that observed the agent is alive.
        daemon._drain_outbox(
            emit, task, responses_dir, eid, outbox_dir, inbox_dir,
            repo_root=repo_root,
            account_context=account_context,
            stats=output_stats,
        )
        daemon._drain_agent_card(
            emit, task, eid, card_path, card_state,
            account_context=account_context,
            repo_label=task.meta.get("repo_label"),
        )
        daemon._drain_live_menu(
            emit, task, menu_path, menu_state, outbox_dir=outbox_dir,
        )
        daemon._emit_mirror_cards(emit, task, eid, inbox_dir, card_state)
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
            produce_moved = daemon._run_state_produce_changed(
                task, work_dir=run_root, outbox_dir=outbox_dir,
            )
        if not task.meta.get("run_state_running_recorded") or produce_moved:
            task.meta["run_state_running_recorded"] = True
            daemon._persist_run_state_doc(
                account_context, task,
                repo_label=str(task.meta.get("repo_label") or ""),
                stage="running", cfg=cfg,
                work_dir=run_root, outbox_dir=outbox_dir,
            )
        daemon._write_live_inbox(
            outbox_dir,
            inbox_dir,
            eid,
            strand=is_strand_run,
            account_context=account_context,
            repo_label=repo_label,
            observer_run_id=task.id,
        )
        daemon._frame_heartbeat(
            task,
            outbox_dir=outbox_dir,
            card_state=card_state,
            output_stats=output_stats,
            brr_dir=brr_dir,
            account_context=account_context,
            repo_label=repo_label,
            work_dir=run_root,
            repo_root=repo_root,
            inbox_dir=inbox_dir,
        )
        hud.write_live(hud.HUDInputs(
            outbox_dir=outbox_dir,
            inbox_dir=inbox_dir,
            current_event_id=eid,
            task=task,
            phase="running",
            attempt=attempt,
            runner_name=runner_name,
            runner_meta=runner_meta,
            runner_catalog=runner_catalog,
            quality_escalation=quality_escalation,
            card_state=card_state,
            output_stats=output_stats,
            start_monotonic=run_started_monotonic,
            work_dir=execution_root,
            place_root=run_root,
            quota_summary=quota_summary,
            cfg=cfg,
            brr_dir=brr_dir,
            account_context=account_context,
            repo_label=repo_label,
            shuttle_home=shuttle_home,
        ))
        if presence_id:
            presence.heartbeat(
                brr_dir, presence_id,
                name=run_ledger.read_run_name_control(outbox_dir) or "",
                mood=run_ledger.read_run_mood_control(outbox_dir) or "",
                topics=run_ledger.read_run_topics_control(outbox_dir) or [],
            )
        elapsed = int(time.monotonic() - attempt_started_monotonic)
        emit(
            "heartbeat",
            run_id=task.id,
            attempt=attempt,
            elapsed_seconds=elapsed,
        )
        daemon._emit_new_containers(emit, task.id, env_ctx, seen_containers)

    def _emit_flush() -> None:
        nonlocal auth_health_cleared
        if not auth_health_cleared:
            # This attempt's Shell just proved it authenticated (it
            # reached a live tool boundary) — clear a stale auth-error
            # mark for its domain now rather than waiting for the
            # process to exit. A long-lived seat (`await` / `hold:`)
            # can run for hours after a relogin while the catalog still
            # read the domain dead for the whole span (the defect this
            # closes).
            runner_auth_health.clear_success(repo_root, runner_choice)
            auth_health_cleared = True
        daemon._refresh_codex_thread_id(task, codex_events_path)
        # Event-driven drain fired by the boundary back channel's .flush signal
        # (chunk 3 of the back channel): push the just-written outbox
        # file / card to the gate promptly, then refresh the live inbox
        # + portal-state the next boundary reads back for injection. Lighter
        # than _emit_heartbeat — no heartbeat packet / presence ping, so
        # a tool-boundary flush doesn't spam the chat card.
        # refresh_levels=False: the event-driven flush must never block on the
        # ~18s PTY scrape for Claude usage. The heartbeat (every 30s) owns
        # the refresh; the flush only reads the on-disk cached snapshot.
        daemon._drain_outbox(
            emit, task, responses_dir, eid, outbox_dir, inbox_dir,
            repo_root=repo_root,
            account_context=account_context,
            stats=output_stats,
        )
        daemon._drain_agent_card(
            emit, task, eid, card_path, card_state,
            account_context=account_context,
            repo_label=task.meta.get("repo_label"),
        )
        daemon._drain_live_menu(
            emit, task, menu_path, menu_state, outbox_dir=outbox_dir,
        )
        daemon._emit_mirror_cards(emit, task, eid, inbox_dir, card_state)
        daemon._write_live_inbox(
            outbox_dir,
            inbox_dir,
            eid,
            strand=is_strand_run,
            account_context=account_context,
            repo_label=repo_label,
            observer_run_id=task.id,
        )
        hud.write_live(hud.HUDInputs(
            outbox_dir=outbox_dir,
            inbox_dir=inbox_dir,
            current_event_id=eid,
            task=task,
            phase="running",
            attempt=attempt,
            runner_name=runner_name,
            runner_meta=runner_meta,
            runner_catalog=runner_catalog,
            quality_escalation=quality_escalation,
            card_state=card_state,
            output_stats=output_stats,
            start_monotonic=run_started_monotonic,
            work_dir=execution_root,
            place_root=run_root,
            quota_summary=quota_summary,
            cfg=cfg,
            brr_dir=brr_dir,
            account_context=account_context,
            repo_label=repo_label,
            refresh_levels=False,
            shuttle_home=shuttle_home,
        ))

    # Pause-not-kill detection (spec step 1): a correspondent message
    # landing mid-call is a *new* id in this run's own pending-event
    # view, not merely a nonzero one — an already-known pending event
    # was there when this attempt started and the resident already knows
    # to fold it in at the next natural boundary. Seeded on the first
    # poll of each attempt (never pauses on backlog that predates it),
    # then any newly-seen id triggers a pause. `claude`-only (codex's
    # per-call cap behaviour is an unmeasured, named non-goal); silent
    # no-op otherwise except one log line so the scope is visible rather
    # than assumed.
    _pause_seen_ids: set[str] | None = None
    if runner_name != "claude" and attempt == 1:
        print(
            f"[brnrd] pause-on-message: {eid} attempt {attempt} runner "
            f"{runner_name!r} is not claude — no-op (unmeasured cap "
            "behaviour, named non-goal)"
        )

    def _maybe_pause() -> None:
        nonlocal _pause_seen_ids
        if runner_name != "claude" or not daemon._seat_pause_on_message(cfg):
            return
        if pause.read_paused_record(outbox_dir):
            return  # a pause is already in effect; wait for its release
        try:
            current_ids = daemon._correspondent_event_ids(
                daemon._pending_events_for_agent(
                    inbox_dir, eid, strand=is_strand_run,
                    account_context=account_context,
                    repo_label=repo_label, observer_run_id=task.id,
                ),
                daemon._task_correspondent_key(task),
            )
        except Exception:
            return
        if _pause_seen_ids is None:
            _pause_seen_ids = current_ids
            return
        new_ids = current_ids - _pause_seen_ids
        _pause_seen_ids = current_ids
        if not new_ids:
            return
        runner_pid = runner.live_pid_for_label(f"{eid}-")
        if runner_pid is None:
            return
        since_ts = (
            hooks_mod.last_boundary_epoch(brr_dir / "runs" / task.id)
            or attempt_started_wall
        )
        records = pause.pause_run_children(
            runner_pid=runner_pid, since_ts=since_ts,
            outbox_dir=outbox_dir,
            cap_seconds=daemon._seat_pause_cap_seconds(cfg),
        )
        if records:
            print(
                f"[brnrd] {eid}: paused {len(records)} live tool "
                f"child(ren) — correspondent message(s) {sorted(new_ids)} "
                "arrived mid-call"
            )

    result = daemon._invoke_with_heartbeat(
        env_backend,
        env_ctx,
        runner_name,
        runner.RunnerInvocation(
            kind="daemon-run",
            label=f"{eid}-attempt-{attempt}",
            prompt=prompt,
            cwd=execution_root,
            repo_root=repo_root,
            publishing_brr_dir=repo_root / ".brr",
            repo_full_name=repo_label,
            response_path=str(env_ctx.response_path_host),
            env=runner_env,
            extra_runner_args=extra_runner_args,
            expected_core=runner_choice.model,
            selected_runner=runner_choice,
            codex_events_path=codex_events_path,
            # design-the-allowance.md's resource hold: a fresh dispatch
            # resuming a held conversation carries the preserved
            # native session id (stamped onto the triggering event by
            # `_apply_resource_hold_resume`, copied onto `task.meta`
            # by `Run.from_event` for free). First attempt only — a
            # retry of *this same* dispatch must not re-resume the
            # native session a second time with the same prompt, which
            # `codex exec resume` would read as a genuinely new turn
            # rather than a retry.
            resume_native_session_id=(
                daemon._resume_session_for_runner(task, runner_choice)
                if attempt == 1 else None
            ),
        ),
        cfg=cfg,
        trace=True,
        on_heartbeat=_emit_heartbeat,
        on_flush=_emit_flush,
        flush_path=flush_path,
        # Every dispatched run is stoppable since #476, not just a
        # spawned child: the user-side affordance exists precisely for
        # the resident thought no parent run can reach.
        should_abort=(lambda: daemon._stopped_run_control(eid) is not None),
        pause_check=_maybe_pause,
    )
    if result.observed_core:
        task.meta["core_observed"] = result.observed_core
        runner_meta = {
            **runner_meta,
            "model_observed": result.observed_core,
            "core_mismatch": result.core_mismatch,
        }
        # Presence only ever heard the *requested* core, at registration
        # time (before the runner ran, let alone reported what it
        # actually used). This is the one point where the observed fact
        # exists and the entry is (usually) still live — best-effort,
        # same as registration: a dashboard reading presence mid-run
        # should be able to tell "riding this thread" apart from "and
        # it's actually running X", not just repeat the pin.
        if presence_id:
            try:
                presence.heartbeat(
                    brr_dir, presence_id,
                    runner_model_observed=result.observed_core,
                )
            except OSError:
                pass
    if result.codex_thread_id:
        # Per-run state, not a global (issue #195 multi-run safety): this
        # attempt's proven thread id, stashed on the task so the
        # "finalizing" portal-state write just below — and a later retry
        # attempt's own "running" write before its *own* runner call
        # returns — can correlate the rollout exactly instead of
        # guessing newest-mtime across every Codex Shell alive right now.
        task.meta["codex_thread_id"] = result.codex_thread_id
    if getattr(result, "claude_session_id", None):
        # The claude half of the same fact, same per-run home.
        task.meta["claude_session_id"] = result.claude_session_id
    daemon._emit_new_containers(emit, task.id, env_ctx, seen_containers)
    # Tier-2 Stop is a synchronous portal boundary: the runner cannot
    # return until the matching flush token has been accepted. A normal
    # hooked run therefore has no special "post-return drain" lifecycle.
    # Keep one recovery check for Tier-0/1 runners and a broken/old hook:
    # correctness degrades to the old path instead of losing a message.
    if not run_hooks_installed or daemon._outbox_message_files(outbox_dir):
        if run_hooks_installed:
            print(
                f"[brnrd] worker {eid}: recovering outbox files left "
                "after synchronous Stop"
            )
        daemon._drain_outbox(
            emit, task, responses_dir, eid, outbox_dir, inbox_dir,
            repo_root=repo_root,
            account_context=account_context,
            stats=output_stats,
        )
    daemon._drain_agent_card(
        emit, task, eid, card_path, card_state,
        account_context=account_context,
        repo_label=task.meta.get("repo_label"),
    )
    daemon._drain_live_menu(
        emit, task, menu_path, menu_state, outbox_dir=outbox_dir,
    )
    daemon._emit_mirror_cards(emit, task, eid, inbox_dir, card_state, final=True)
    daemon._write_live_inbox(
        outbox_dir,
        inbox_dir,
        eid,
        strand=is_strand_run,
        account_context=account_context,
        repo_label=repo_label,
        observer_run_id=task.id,
    )
    hud.write_live(hud.HUDInputs(
        outbox_dir=outbox_dir,
        inbox_dir=inbox_dir,
        current_event_id=eid,
        task=task,
        phase="finalizing",
        attempt=attempt,
        runner_name=runner_name,
        runner_meta=runner_meta,
        runner_catalog=runner_catalog,
        quality_escalation=quality_escalation,
        card_state=card_state,
        output_stats=output_stats,
        start_monotonic=run_started_monotonic,
        work_dir=execution_root,
        place_root=run_root,
        quota_summary=quota_summary,
        cfg=cfg,
        brr_dir=brr_dir,
        account_context=account_context,
        repo_label=repo_label,
        shuttle_home=shuttle_home,
    ))
    # Capture the resident's dominion edits before any branch/exit. One
    # call site covers success, retry, and hard failure: a clean
    # dominion no-ops, and on retry the next pass just re-captures any
    # new writes (idempotent — see _capture_dominion).
    daemon._capture_dominion(
        repo_root,
        cfg,
        task,
        account_context=account_context,
    )

    return Streamed(
        dispatched=dx,
        attempt=replace(a, lane=replace(a.lane, meta=runner_meta)),
        result=result,
    )
