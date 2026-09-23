"""boundary — the turn ended; what did it end *as*.

Classifies what came back (a parent's ``stop:``, a runner error, a clean
exit that still owes an artifact), probes for a fresh commit before any
teardown, decides delivery. A satisfied turn stages its terminal reply here
and then ends as ``completed`` — or as ``hold`` when the resident's own
``hold:`` or the seat's turn-end park asks for one. An unsatisfied turn ends
as ``hold`` on a confident quota exhaustion, ``retry`` within the shared
budget, ``fallback`` onto another Shell+Core (the Lane is rebuilt here), or
``exhausted``.

This is the *turn's* boundary. The per-tool-call boundary row
(``runs/<id>/boundaries.jsonl``) is written by the hook subprocess —
``hooks.record_boundary`` — not by the worker.

Lines on ``main`` (``3def7ad6``): ``daemon.py:5213–5477`` and ``5557–5726``.
"""

from __future__ import annotations

from .. import protocol
from .. import run_ledger
from .. import runner
from .. import runner_failures
from .. import runner_select
from .. import spending_plan
from .. import worktree

from dataclasses import replace

from .. import daemon
from .prepare import runner_runtime
from .shapes import Attempt, Boundary, Lane, Prepared, Streamed


def boundary(p: Prepared, s: Streamed) -> Boundary:
    event = p.event
    repo_root = p.place_root
    responses_dir = p.responses_dir
    cfg = p.cfg
    max_retries = p.max_retries
    account_context = p.account_context
    inbox_dir = p.inbox_dir
    eid = p.eid
    brr_dir = p.brr_dir
    runs_dir = p.runs_dir
    emit = p.emit
    task = p.task
    branch_plan = p.branch_plan
    env_ctx = p.env_ctx
    run_root = p.run_root
    execution_root = p.execution_root
    context_path = p.context_path
    resp_path = p.resp_path
    outbox_dir = p.outbox_dir
    output_stats = p.output_stats
    trace_dirs = p.trace_dirs
    result = s.result
    a = s.attempt
    attempt = a.n
    runner_choice = a.lane.choice
    runner_name = a.lane.name
    quality_escalation = a.lane.quality_escalation
    retries_used = a.retries_used
    attempted_runners = a.attempted_runners
    # This attempt's failure, and only this attempt's (move 3b): an earlier
    # attempt's failure is history (``a.failures``), never the reading a
    # clean — or differently failed — attempt ends the run on.
    last_failure: dict[str, object] | None = None
    if result.trace_dir:
        trace_dirs.append(str(result.trace_dir.relative_to(brr_dir)))
    stop_control = daemon._stopped_run_control(eid)
    if stop_control is not None:
        # The runner just died to (or survived past) a parent `stop:` —
        # a deliberate cancellation must not fall into the retry /
        # fallback machinery, which would relaunch the killed work.
        return Boundary(
            kind="stopped",
            attempt=replace(a, last_failure=None),
            stop_control=stop_control,
        )
    attempt_failure_kind: str | None = None
    try:
        result.raise_for_error()
    except RuntimeError as e:
        print(f"[brnrd] worker {eid}: runner error: {e}")
        detail = result.error_detail() or str(e)
        timed_out = result.timed_out
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
                # ``detail`` prefers stderr; the transport verdict was
                # taken over both streams, so hand it in rather than
                # letting a stdout-only signature read as a generic
                # runner error in the terminal note.
                transport=result.transport_failure,
            ),
            # The structured cause codex itself named for a died-in-
            # flight turn (runner.py's ``_extract_codex_task_error``),
            # or ``None`` for every other Shell/outcome. Carried
            # alongside the regex-derived ``failure_kind`` above rather
            # than folded into it: a resource hold needs the *exact*
            # kind codex stated, not "this text also matched a quota
            # pattern" — see ``_maybe_arm_resource_hold_on_failure``.
            "codex_task_error": result.codex_task_error,
        }
        attempt_failure_kind = str(last_failure["failure_kind"])
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
                    "codex_task_error": result.codex_task_error,
                }
                attempt_failure_kind = str(last_failure["failure_kind"])
    daemon._record_runner_auth_health(repo_root, runner_choice, attempt_failure_kind)
    failures = (
        [*a.failures, {"attempt": attempt, **last_failure}]
        if last_failure is not None else list(a.failures)
    )
    ended = replace(a, last_failure=last_failure, failures=failures)

    # Detect a fresh commit on the worktree branch before finalize runs
    # — finalize tears the worktree down on success, so this read has
    # to happen here. ``has_commits_beyond(seed_ref)`` follows HEAD,
    # which is what the agent ended on (initial branch or a switched
    # branch); both count as "the agent committed real work".
    try:
        has_new_commit = worktree.has_commits_beyond(
            run_root, branch_plan.seed_ref,
            base_oid=getattr(branch_plan, "seed_oid", None),
        )
    except worktree.BaseUnresolvable as exc:
        # #1298: git could not answer, which is not the same fact as "the
        # agent committed nothing" — and the old ``False`` here spoke the
        # second. Downstream, ``False`` un-gates ``_stray_host_write``
        # (the ``host-head-moved`` advisory the issue quoted), tells the
        # forge facet there is nothing to land, and joins the finalize
        # side in reporting an empty run. Erring the other way costs a
        # published branch that may equal its base; erring this way cost
        # three strands their commits in one evening.
        print(
            f"[brnrd] worker {eid}: base unresolvable ({exc}); assuming "
            "the run committed rather than assuming it did not"
        )
        has_new_commit = True
    except Exception as exc:  # noqa: BLE001 - same posture, wider net
        # An unreadable checkout (already torn down, permissions) is the
        # same epistemic state as an unresolvable base and gets the same
        # answer, for the same reason. Kept as its own arm only so the
        # line the operator reads names which of the two happened.
        print(
            f"[brnrd] worker {eid}: commit probe failed ({exc}); assuming "
            "the run committed rather than assuming it did not"
        )
        has_new_commit = True
    task.meta["has_new_commit"] = has_new_commit
    satisfied, signal = daemon._result_satisfied_delivery(
        result, output_stats, event, has_new_commit=has_new_commit,
    )
    if satisfied:
        print(f"[brnrd] worker {eid}: response ready ({signal})")
        task.meta["success_signal"] = signal
        if trace_dirs:
            task.meta["trace_dirs"] = ", ".join(trace_dirs)
        if daemon._response_has_body(resp_path):
            terminal_duplicate = daemon._terminal_stream_duplicates_delivered(task, resp_path)
            # A terminal reply reaches the world one of three ways: a gate
            # delivers it, the spawning parent collects it along the
            # dispatch edge, or nothing does. Only the third is a
            # suppression — and it is a property of the event source, not
            # of one hardcoded source name.
            source = str(event.get("source") or "")
            unowned = bool(source) and not daemon._terminal_reply_lands(
                source,
                spawn_parent_run_id=str(
                    task.meta.get("spawn_parent_run_id") or ""),
                runs_dir=runs_dir,
            )
            # #562's residual: "unowned" is not automatically "unheard".
            # `notify.gate` (explicit config key, or single-gate
            # inference — `_resolve_notify_gate`) names a configured
            # chat gate to carry the terminal text out-of-bound instead
            # of staging it undeliverable. Never attempted for a
            # duplicate (nothing new to say) or when the source is
            # already owned (the existing channel already carries it) —
            # the single-delivery invariant lives in that ordering, not
            # in a check inside this block.
            notify_gate = (
                daemon._resolve_notify_gate(
                    cfg, emit.brr_dir,
                    conversation_key=str(task.conversation_key or ""),
                )
                if unowned and not terminal_duplicate
                else ""
            )
            # brnrd#1798: a dispatch_message-sourced reply is "unowned"
            # here whenever the steer that woke this strand stamped no
            # spawn_parent_run_id (the steer event only ever carries
            # spawn_message_from_run/for_run/for_event —
            # _terminal_reply_lands has nothing else to land on) — so
            # the notify.gate net meant for a genuinely orphaned
            # schedule wake was catching a strand's reply to its own
            # dispatcher instead, and shipping it to the correspondent
            # raw, reading as if the seat itself had spoken. Required
            # shape (the maintainer's steer on the issue): still
            # deliver — never reroute away from the correspondent —
            # but label it as the strand's own note, and fan a copy to
            # the steering run so the seat knows what just went out.
            relay_label = ""
            relay_parent_run_id = ""
            relay_strand_run_id = ""
            if source == "dispatch_message":
                relay_strand_run_id = str(
                    task.meta.get("spawn_message_for_run") or ""
                )
                relay_parent_run_id, _relay_conv = daemon._child_owner_route(
                    str(task.meta.get("spawn_message_for_event") or ""),
                    fallback_parent_run_id=str(
                        task.meta.get("spawn_message_from_run") or ""
                    ),
                )
                relay_label = daemon._strand_reply_label(
                    emit.brr_dir, runs_dir, relay_strand_run_id or task.id,
                )
            gate_fallback_delivered = False
            if notify_gate:
                fallback_body = protocol.read_response(responses_dir, eid) or ""
                if source == "dispatch_message":
                    fallback_body = daemon._label_strand_relay_body(
                        relay_label, fallback_body,
                    )
                gate_fallback_delivered = daemon._deliver_out_of_bound(
                    emit, task, responses_dir, inbox_dir, eid,
                    notify_gate, {}, fallback_body, outbox_dir=outbox_dir,
                    account_context=account_context,
                )
                if gate_fallback_delivered:
                    output_stats["outbound"] = output_stats.get("outbound", 0) + 1
                    output_stats["delivered"] = output_stats.get("delivered", 0) + 1
                    if source == "dispatch_message" and relay_parent_run_id:
                        daemon._notify_parent_of_relayed_strand_message(
                            inbox_dir,
                            parent_run_id=relay_parent_run_id,
                            strand_run_id=relay_strand_run_id or task.id,
                            strand_event_id=str(
                                task.meta.get("spawn_message_for_event") or ""
                            ),
                            label=relay_label,
                            gate=notify_gate,
                            body=fallback_body,
                        )
            undeliverable = (
                unowned and not terminal_duplicate and not gate_fallback_delivered
            )
            suppression_reason = (
                "duplicate of a delivered reply"
                if terminal_duplicate
                else f"delivered out-of-bound via notify.gate={notify_gate!r}"
                if gate_fallback_delivered
                else f"no gate owns {source or 'unknown'} events"
                if unowned
                else ""
            )
            route = daemon._terminal_route(
                source,
                spawn_parent_run_id=str(
                    task.meta.get("spawn_parent_run_id") or ""),
                runs_dir=runs_dir,
                duplicate=terminal_duplicate,
                undeliverable=undeliverable,
                delivered_elsewhere=bool(output_stats.get("delivered", 0)),
                gate_fallback=gate_fallback_delivered,
            )
            task.meta["terminal_route"] = route
            daemon._stage_terminal_response(
                task,
                account_context,
                event,
                resp_path,
                suppressed_reason=suppression_reason,
                undeliverable=undeliverable,
            )
            if terminal_duplicate:
                # Static dispatch call: the terminal stream is an exact
                # duplicate of a reply this run already delivered to the
                # waking thread mid-run (outbox partial). Shipping it
                # again double-posts on the one surface the user watches;
                # the content is already in the conversation log, so the
                # durable message is stamped as already delivered.
                print(
                    f"[brnrd] worker {eid}: terminal stream suppressed "
                    "(duplicate of a delivered reply)"
                )
            elif gate_fallback_delivered:
                # Run-type-agnostic delivery (the point of this whole
                # block): nobody owned the waking event's source, but
                # `notify.gate` did, so the closing message still reaches
                # a chat instead of sitting readable only on the run
                # node. Printed like `gate-sole` — the net caught this
                # one too, just via a gate the event never addressed.
                print(
                    f"[brnrd] worker {eid}: terminal stream had no owning "
                    f"gate — delivered via notify.gate={notify_gate!r} "
                    "instead of staging undeliverable"
                )
            elif not unowned:
                daemon._record_response_artifact(emit, task, resp_path)
                if route == daemon._TERMINAL_ROUTE_GATE_SOLE:
                    # #743: the net caught this one. Printed only here —
                    # ``gate-extra`` is the common shape and a line at
                    # every closeout would stop being read.
                    print(
                        f"[brnrd] worker {eid}: terminal stream is this "
                        "run's only delivery — the fallback net carried "
                        "it, not a route the run chose"
                    )
        # Keep an in-memory snapshot for closeout consumers; the response
        # carrier now stays on disk too, but synthetic/older gates can
        # still race the transition during deploy skew.
        terminal_reply = protocol.read_response(responses_dir, eid)
        pending_halt = task.meta.pop("pending_halt", None)
        if pending_halt is not None:
            # `halt:` outranks every park, including one this same turn
            # staged and including the daemon's own turn-end net below: a
            # seat that chose to end does not get parked instead. This is
            # the whole point of the verb (design-the-four-stops.md §The
            # two verbs) — every other exit was closed, so the seat could
            # not leave, and the cost of that was invisible.
            task.meta.pop("pending_resource_hold", None)
            return Boundary(
                kind="halt",
                attempt=ended,
                halt_spec=pending_halt,
                terminal_reply=terminal_reply,
                success_signal=signal,
                has_new_commit=has_new_commit,
            )
        pending_hold = task.meta.pop("pending_resource_hold", None)
        if pending_hold is None:
            pending_hold = daemon._park_seat_on_turn_end(task, cfg)
        if pending_hold is not None:
            # The resident's own `hold:` directive (outbox parse above)
            # — a clean turn that chose to park rather than one that
            # failed to. Whatever it said this turn was already staged
            # into `resp_path` by the ordinary success-path delivery
            # just above; `_finalize_resource_hold`'s own terminal-body
            # write is purely a fallback for a `hold:` with no
            # accompanying reply. Routed to the same hold-shaped
            # finalize the automatic-detection path uses instead of
            # the ordinary "done" sequence below — no double-finalize,
            # no double-publish.
            return Boundary(
                kind="hold",
                attempt=ended,
                hold_spec=pending_hold,
                terminal_reply=terminal_reply,
                success_signal=signal,
                has_new_commit=has_new_commit,
            )
        return Boundary(
            kind="completed",
            attempt=ended,
            terminal_reply=terminal_reply,
            success_signal=signal,
            has_new_commit=has_new_commit,
        )

    hold_spec = daemon._maybe_arm_resource_hold_on_failure(last_failure, task=task, cfg=cfg)
    if hold_spec is not None:
        # A confident, structured quota exhaustion pre-empts the
        # ordinary retry/fallback/give-up decision below entirely —
        # design-the-allowance.md: "no automatic provider switch" for
        # exactly this evidence. A regex-only QUOTA_EXHAUSTED guess
        # (no structured codex_task_error) still falls through
        # unchanged to AUTO_FALLBACK_FAILURES further down.
        return Boundary(
            kind="hold",
            attempt=ended,
            hold_spec=hold_spec,
        )
    if (
        last_failure is not None
        and last_failure.get("failure_kind") == runner_failures.CORE_REFUSAL
        and daemon._core_refusal_should_retry_fresh(task)
    ):
        # #2076's ladder, rung 1: the same Shell+Core, rebooted as a fresh
        # session. `retry_reason()` never fires for this text (it is not a
        # timeout/transport/host-suspend signature), so this intercepts
        # ahead of the ordinary retry/fallback logic below rather than
        # trying to make that machinery recognise a fourth retryable
        # shape. The "fresh session" half is free: `worker/prepare.py`
        # only ever resumes this run's native session at `attempt == 1`,
        # so attempt N+1 is a new session by construction. A *second*
        # refusal inside the window (`_core_refusal_should_retry_fresh`
        # returning False) falls through unchanged to the ordinary
        # `AUTO_FALLBACK_FAILURES` reroute just below, which now includes
        # this kind — same "no other Shell/quota ⇒ give up" ending every
        # other operational failure already gets.
        retries_used += 1
        daemon._announce_core_refusal_retry(responses_dir, eid, runner_name)
        print(
            f"[brnrd] worker {eid}: Core refusal on {runner_name}, "
            "rebooting as a fresh session..."
        )
        emit(
            "retrying", run_id=task.id, event_id=eid, attempt=attempt + 1,
            reason="core refusal: fresh-session reboot on the same Shell+Core",
        )
        return Boundary(
            kind="retry",
            attempt=ended,
            next_attempt=replace(
                a, n=attempt + 1, retries_used=retries_used,
                prompt_mode="core_refusal_retry", last_failure=None,
                failures=failures,
            ),
        )
    retry_reason = result.retry_reason()
    will_retry = bool(retry_reason and retries_used < max_retries)
    fallback_runner_name: str | None = None
    fallback_choice: runner_select.RunnerProfile | None = None
    failure_kind = (
        str(last_failure.get("failure_kind") or "")
        if last_failure and not retry_reason else ""
    )
    if failure_kind:
        # #1931: the substitute has to be able to *finish*. Every other
        # filter in `automatic_fallback_runner` asks whether a candidate
        # is legal; this reading asks whether it is alive. Measured
        # 2026-09-11: a claude 401 fell through to a codex profile whose
        # weekly window read 1%, and that same reading parked the seat
        # fourteen minutes later. One cached snapshot, no network.
        try:
            from ..gates import cloud_publisher as _cloud_pub
            fallback_quota = _cloud_pub.quota_shell_binding_pct(brr_dir)
        except Exception:
            # A quota collector that cannot answer must never be able to
            # block a dispatch — an empty map disables the rule.
            fallback_quota = {}
        fallback_choice = runner.fallback_runner_profile(
            repo_root,
            runner_choice,
            failure_kind,
            tried=attempted_runners,
            quota_pct=fallback_quota,
            starve_floor_pct=daemon._seat_starve_floor_pct(cfg),
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
        retries_used += 1
        prompt_mode = (
            "transport_retry" if result.transport_failure
            else "artifact_retry"
        )
        print(f"[brnrd] worker {eid}: {retry_reason}, retrying...")
        emit(
            "retrying",
            run_id=task.id,
            event_id=eid,
            attempt=attempt + 1,
            reason=retry_reason,
        )
        return Boundary(
            kind="retry",
            attempt=ended,
            next_attempt=replace(
                a,
                n=attempt + 1,
                retries_used=retries_used,
                prompt_mode=prompt_mode,
                last_failure=None,
                failures=failures,
            ),
        )
    if fallback_runner_name:
        previous_runner = runner_name
        assert fallback_choice is not None
        runner_choice = fallback_choice
        runner_name = runner_choice.name
        # A wake-request note would now be a lie: the requested body
        # failed and this is the fallback, not the tapped profile.
        runner_wake_note = None
        runtime = runner_runtime(
            runner_choice,
            task=task, eid=eid, env_ctx=env_ctx, context_path=context_path,
            outbox_dir=outbox_dir, brr_dir=brr_dir, cfg=cfg, run_root=run_root,
            repo_root=repo_root, emit=emit,
        )
        runner_meta = runtime.meta
        quota_summary = runtime.quota
        runner_env = runtime.env
        extra_runner_args = runtime.extra_args
        run_hooks_installed = runtime.hooks_installed
        runner_catalog = runner.available_runner_catalog(
            repo_root, selected=runner_name,
        )
        daemon._enrich_catalog_quota(runner_catalog, brr_dir)
        quality_escalation = daemon._quality_escalation_meta(repo_root, runner_name)
        daemon._record_task_runner(task, runner_choice)
        run_ledger.mark_run_started(task, runner_name, outbox_dir, execution_root)
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
        daemon._record_runner_substitution(
            task, previous_runner, runner_name, failure_kind,
        )
        daemon._announce_runner_substitution(
            responses_dir, eid, previous_runner, runner_name, failure_kind,
        )
        return Boundary(
            kind="fallback",
            attempt=ended,
            next_attempt=Attempt(
                n=attempt + 1,
                lane=Lane(
                    choice=runner_choice,
                    name=runner_name,
                    meta=runner_meta,
                    quota_summary=quota_summary,
                    env=runner_env,
                    extra_args=extra_runner_args,
                    hooks_installed=run_hooks_installed,
                    catalog=runner_catalog,
                    quality_escalation=quality_escalation,
                    wake_note=runner_wake_note,
                ),
                retries_used=retries_used,
                attempted_runners=attempted_runners,
                prompt_mode=prompt_mode,
                fallback_notice=fallback_notice,
                last_failure=None,
                failures=failures,
            ),
        )
    # Nothing left to try: a timeout, an unrecognised non-zero exit, or a
    # retryable class whose budget is spent. Give up now rather than
    # burning another expensive attempt; the give-up branch below carries
    # the captured error up to the gate.
    return Boundary(
        kind="exhausted",
        attempt=ended,
        relay_candidate=relay_candidate,
        relay_plan=relay_plan,
    )
