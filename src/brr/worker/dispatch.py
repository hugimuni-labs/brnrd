"""dispatch — one attempt, up to the moment the runner starts.

Clears the last attempt's observations, refuses to launch cancelled work,
builds the prompt (boot mount, the fail-closed prose rebuild, the attempt-1
prompt/boot-score/wake-manifest files), announces ``attempt_started`` and
writes the ``running`` portal.

Lines on ``main`` (``3def7ad6``): ``daemon.py:4485–4825`` (less the
auth-proof reset at ``4794–4803``, which moved to ``stream`` beside the
closure that flips it).
"""

from __future__ import annotations

from .. import presence
from .. import prompts
from .. import protocol
from .. import release_availability
from .. import run_context
from .. import runner_auth_health
from .. import runner_quota
from .. import transcript
import time
from dataclasses import replace
from typing import Any

from .. import daemon
from .shapes import Attempt, Boundary, Dispatched, Prepared


def dispatch(p: Prepared, a: Attempt) -> Dispatched | Boundary:
    event = p.event
    repo_root = p.repo_root
    cfg = p.cfg
    account_context = p.account_context
    inbox_dir = p.inbox_dir
    eid = p.eid
    brr_dir = p.brr_dir
    repo_label = p.repo_label
    emit = p.emit
    task = p.task
    presence_id = p.presence_id
    shuttle_home = p.shuttle_home
    branch_plan = p.branch_plan
    env_backend = p.env_backend
    env_ctx = p.env_ctx
    run_root = p.run_root
    branch_name = p.branch_name
    branch_setup_notice = p.branch_setup_notice
    context_path = p.context_path
    event_body_for_prompt = p.event_body_for_prompt
    communication_snapshot = p.communication_snapshot
    recent_conversation = p.recent_conversation
    pending_events_snapshot = p.pending_events_snapshot
    present_snapshot = p.present_snapshot
    prompt_diffense = p.prompt_diffense
    outbox_dir = p.outbox_dir
    codex_events_path = p.codex_events_path
    card_state = p.card_state
    output_stats = p.output_stats
    run_started_monotonic = p.run_started_monotonic
    attempt = a.n
    attempted_runners = a.attempted_runners
    prompt_mode = a.prompt_mode
    fallback_notice = a.fallback_notice
    runner_choice = a.lane.choice
    runner_name = a.lane.name
    runner_meta = a.lane.meta
    quota_summary = a.lane.quota_summary
    extra_runner_args = a.lane.extra_args
    run_hooks_installed = a.lane.hooks_installed
    runner_catalog = a.lane.catalog
    quality_escalation = a.lane.quality_escalation
    runner_wake_note = a.lane.wake_note
    # An id proves one completed Codex invocation, not the task forever.
    # A retry is a new invocation; retaining the previous attempt's id
    # would make every running heartbeat report the old thread until the
    # replacement process returned and overwrote it.
    task.meta.pop("codex_thread_id", None)
    task.meta.pop("claude_session_id", None)
    # Same reasoning as the thread id above, for the model a previous
    # attempt observed: a retry that escalates to a different runner
    # must not leave attempt 1's `model_observed` reading in place, on
    # either surface that carries it — a stale value here would show as
    # attempt 2 running attempt 1's model, in both task.meta-derived
    # payloads and the live presence entry.
    task.meta.pop("core_observed", None)
    runner_meta = {k: v for k, v in runner_meta.items() if k not in ("model_observed", "core_mismatch")}
    if presence_id:
        try:
            presence.heartbeat(brr_dir, presence_id, runner_model_observed="")
        except OSError:
            pass
    try:
        codex_events_path.unlink()
    except FileNotFoundError:
        pass
    stop_control = daemon._stopped_run_control(eid)
    if stop_control is not None:
        # The parent stopped this child before (or between) attempts —
        # never launch a runner for cancelled work.
        return Boundary(kind="stopped", attempt=a, stop_control=stop_control)
    if runner_name not in attempted_runners:
        attempted_runners.append(runner_name)
    if prompt_mode == "artifact_retry":
        prompt_instruction = (
            "Previous attempt exited cleanly but did not produce the "
            "required output file(s). Produce them this time.\n\n"
            f"Original run instruction: {task.body}"
        )
    elif prompt_mode == "transport_retry":
        # A transport death is not a failed attempt at the work, it is a
        # dropped call: the turn may have gotten arbitrarily far before
        # the connection went, and whatever it committed or wrote is
        # still on disk. Telling it "you produced nothing" (the
        # artifact_retry wording) would be false and would invite it to
        # redo work that is already there.
        prompt_instruction = (
            "The previous attempt's connection to the model dropped "
            "mid-response, so that turn never finished. Any files, "
            "commits, or outbox messages it had already produced are "
            "still present. Continue from the current worktree state "
            "and finish the original run instruction; do not restart "
            "work that is already present in the files unless it is "
            "wrong.\n\n"
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
        run_levels, _ = daemon._collect_levels(
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
    if task.meta.get("resume_native_session_id"):
        # A native resume *is* the transcript: the Shell reopens the
        # parked session itself (`runner._insert_claude_resume`), so
        # forging and `--fork-session`-mounting a second one would
        # hand claude two `--resume`s. Prose wake on top of the live
        # transcript instead.
        mount_sink = None
    # Every present block's exact rendered text, mounted or not — a
    # strict superset of `mount_sink` (#1830). Unconditional (unlike
    # `mount_sink`, gated on the mount toggle actually applying): a
    # prose-only wake still has home-originated blocks with no other
    # honest record of their bytes. See `run_context.write_wake_blocks`.
    block_text_sink: dict[str, str] = {}

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
        # #1491: the waking event's own age and retry history — read off
        # the event dict, never recomputed. ``created`` is stamped once
        # by ``protocol.create_event`` and never rewritten; ``retry_of``/
        # ``retry_failure_kind`` are ``_mark_interrupted_runs``'s
        # additive stamp, absent on a first attempt. Named
        # ``retry_failure_kind``, not ``retry_reason`` — that name is
        # ``RunnerResult.retry_reason()``'s already, for an unrelated
        # fact (see ``_record_retry_provenance``'s docstring).
        event_created=event.get("created"),
        event_retry_of=event.get("retry_of"),
        event_retry_failure_kind=event.get("retry_failure_kind"),
        # The waking event's own raw record (correspondent/thread
        # fields) — #128 step 3: lets the bundle recognise still-pending
        # burst siblings in `pending_events_snapshot` and list them
        # oldest-first under "Original event body" instead of only this
        # run's own body. Render-only: no new dispatch delay, and a
        # strand's isolated view (no correspondent's own pending events
        # ever reach it) naturally never finds a sibling here.
        event_meta=event,
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
        continuity=daemon._build_continuity_facet(
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
        strand=daemon._is_strand(task.meta),
        hooks_installed=run_hooks_installed,
    )

    prompt, boot_score = prompts.build_daemon_prompt_with_score(
        prompt_instruction,
        eid,
        str(env_ctx.response_path_env),
        run_root,
        _mount_sink=mount_sink,
        _block_text_sink=block_text_sink,
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
                # None for every backend but sandbox (see
                # `EnvBackend.session_seed_home`); `SandboxEnv` relocates
                # the seed into the VM's real HOME at invoke time.
                home=env_backend.session_seed_home(env_ctx),
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
            # Same `block_text_sink` object, deliberately: this rebuild
            # re-runs every `_take` call with `_mount_sink=None`, so every
            # key it touches overwrites the first pass's mounted-text
            # entry with the fresh prose that actually shipped — the
            # sink ends up describing this final `boot_score`, never the
            # discarded mounted one, with no separate reconciliation step.
            prompt, boot_score = prompts.build_daemon_prompt_with_score(
                prompt_instruction,
                eid,
                str(env_ctx.response_path_env),
                run_root,
                _block_text_sink=block_text_sink,
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
        run_context.write_wake_manifest(brr_dir, task, boot_score, wake_blocks=block_text_sink)
        # Every present block's exact rendered text (#1830) — a home-
        # originated block (dominion self-inject, work surface, pitfalls,
        # knowledge slices, the plan page) has no file on disk that ever
        # carried its bytes, mounted or not, so this is the only durable
        # record of "what did this wake actually read here?" for those
        # blocks. Unconditional, unlike the mounted-only sidecar below.
        run_context.write_wake_blocks(brr_dir, task, block_text_sink)
        # A mounted wake's prompt.md is missing exactly the blocks
        # `boot_score.body.mounted` says left the prose — persist the diverted
        # text this run actually built (never re-derived later from
        # current prompt files, which may have changed) so `brnrd prompts
        # replay` has a complete input to reconstruct (#1753). Gated on
        # the *final* `boot_score` (not the bare truthiness of
        # `mount_sink`): the fail-closed rebuild above may have discarded
        # a stale, populated `mount_sink` in favor of a fresh unmounted
        # prompt+score, and a sidecar written for that stale dict would
        # describe a wake nobody had.
        #
        # `body.mounted`, not `mounted`: the flag lives on `BootBody`
        # (`bootscore.py:314`), which is also where `replay` reads it
        # from (`boot-score.json` -> `body.mounted`). The first version
        # of this line reached for `boot_score.mounted` and raised
        # AttributeError on every mounted daemon wake — one fact, two
        # accessors, and only the reader's was exercised by a test.
        if boot_score.body.mounted and mount_sink:
            run_context.write_mounted_blocks(brr_dir, task, mount_sink)

    print(f"[brnrd] worker {eid}: attempt {attempt}")
    emit("attempt_started", run_id=task.id, event_id=eid, attempt=attempt)
    try:
        runner_auth_health.record_credential_reading(
            repo_root, getattr(runner_choice, "shell", "") or "",
            event="attempt", detail=f"{task.id} attempt {attempt} on {runner_name}",
        )
    except Exception:  # noqa: BLE001 — an instrument must never block a dispatch
        pass

    attempt_started_monotonic = time.monotonic()
    # Wall-clock twin of the line above: the pause machinery's "since"
    # fence needs epoch seconds (it compares against `ps`'s `lstart` and
    # `boundaries.jsonl`'s `at`), and `time.monotonic()` has no fixed
    # epoch to convert from.
    attempt_started_wall = time.time()
    daemon._write_live_portal_state(
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
        card_state=card_state,
        output_stats=output_stats,
        start_monotonic=run_started_monotonic,
        work_dir=run_root,
        quota_summary=quota_summary,
        cfg=cfg,
        brr_dir=brr_dir,
        account_context=account_context,
        repo_label=repo_label,
        shuttle_home=shuttle_home,
    )

    return Dispatched(
        attempt=replace(
            a,
            lane=replace(
                a.lane,
                meta=runner_meta,
                quota_summary=quota_summary,
                extra_args=extra_runner_args,
            ),
            prompt_mode="normal",
            fallback_notice=None,
        ),
        prompt=prompt,
        started_monotonic=attempt_started_monotonic,
        started_wall=attempt_started_wall,
    )
