"""prepare — the throw before its runner.

Resolves who is speaking and where the run lands (event, run record, runner
selection, trust, the environment, the branch plan, presence, the Shuttle's
``released → awake``), writes the wake's orientation (notices, history,
the communication snapshot, the context file) and the ``preparing`` portal,
and fixes the first attempt's :class:`Lane`.

Three endings happen here, before any runner exists, and keep their status
writes where their ordering pins them: an exact duplicate of an already
received origin message (``done``, ``duplicate_origin_message``), a source
the trust tier refuses (``done``, ``trust_refused``), and an environment
that fails to prepare (``error``).

Lines on ``main`` (``3def7ad6``): ``daemon.py:3234–4468``.
"""

from __future__ import annotations

from .. import await_verb
from .. import branching
from .. import config as conf
from .. import conversations
from .. import forge_state
from .. import gitops
from .. import hooks as hooks_mod
from .. import hud
from .. import knowledge
from .. import menus
from .. import pending_resume
from .. import presence
from .. import prompts
from .. import protocol
from .. import resource_hold
from .. import run_context
from .. import run_ledger
from .. import run_topic
from .. import runner
from .. import runner_quota
from .. import shuttle
from .. import stake as stake_mod
from .. import sync
import os
import time
from pathlib import Path

from .. import account, envs, runner_select
from ..run import Run

from .. import daemon
from .shapes import Finalized, Lane, Prepared


def prepare(
    event: dict,
    repo_root: Path,
    responses_dir: Path,
    cfg: dict,
    max_retries: int,
    *,
    account_context: account.AccountContext | None = None,
    inbox_dir: Path | None = None,
) -> Prepared | Finalized:
    eid = event["id"]
    brr_dir = gitops.shared_brr_dir(repo_root)
    runs_dir = brr_dir / "runs"
    repo_label = daemon._repo_label(repo_root, event, cfg)
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
    # profile". #733: this site no longer decides anything about one — the
    # claim happened at dispatch (`_apply_dashboard_wake_request`), against
    # the server that owns the row, and all that is left here is reading the
    # verdict it stamped on the event.
    #
    # The duplication this replaces is the bug, not an accident of it: the
    # same guard ladder ran at both sites, so a tap the dispatch-time rung
    # had already lapsed came back as `pending() is None` here and the run
    # reported "no tap was ever parked" — the miss made invisible by the
    # very code meant to make it visible.
    #
    # `dashboard_wake_request_reason` present ⇒ the server refused; the tap
    # existed and did not apply, and that distinction is exactly what
    # `resources.runner.wake_request` (facets.py) carries onto the run so a
    # human sees "you asked for X, you got Y, because Z". `None` when no tap
    # was in play at all, which is the common case and must read as absent
    # rather than as a miss.
    wake_request_report: dict[str, object] | None = None
    runner_wake_note: str | None = None
    if event.get("dashboard_wake_request_id"):
        wake_reason = str(
            event.get("dashboard_wake_request_reason") or ""
        ).strip() or None
        if wake_reason is None:
            runner_wake_note = "requested from the dashboard dispatch header"
        wake_request_report = {
            "requested_profile": str(
                event.get("dashboard_wake_request_profile")
                or event.get("runner")
                or ""
            ) or None,
            "applied": wake_reason is None,
            "reason": wake_reason,
        }
    elif event.get("dashboard_wake_sticky_profile"):
        # #932: the profile was inherited from the conversation-sticky
        # record, not claimed fresh. The note is what lets a wake (and the
        # human reading its bundle) tell tap-fresh from tap-inherited — and
        # names the expiry, because the auto-revert is the maintainer's
        # whole price for sticky existing at all.
        runner_wake_note = daemon._sticky_wake_note(event)
    runner_choice = runner.resolve_runner_profile(
        repo_root, runner_overrides or None,
    )
    if wake_request_report is not None:
        wake_request_report["resolved_profile"] = runner_choice.name
    runner_name = runner_choice.name
    runner_meta: dict[str, object] = runner_choice.portal_metadata()
    quality_escalation = daemon._quality_escalation_meta(repo_root, runner_name)
    failure_defer_seconds = float(
        cfg.get(
            "dispatch.failure_defer_seconds",
            daemon._FAILURE_DEFER_SECONDS_DEFAULT,
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
        "dispatch.dedup_window_seconds", daemon._DEDUP_WINDOW_SECONDS_DEFAULT
    )
    duplicate_event = (
        None if is_respawn_origin else
        conversations.find_event_by_origin_message(
            brr_dir, origin_message_key, exclude_event_id=eid,
            max_age_seconds=dedup_window,
        )
    )
    emit = daemon._WorkerEmit(brr_dir, conv_key, eid)

    if conv_key:
        conversations.append_event(brr_dir, conv_key, event)
        emit("event_received", event_id=eid, source=event.get("source", ""))

    if duplicate_event:
        task = Run.from_event(event, cfg)
        if is_home_root:
            task.meta["root_kind"] = "home"
            task.meta["forge_lane"] = False
        task.conversation_key = conv_key
        task.save(runs_dir)
        task.transition("done", why="duplicate_origin_message")
        if correspondent_key:
            task.meta["correspondent_key"] = correspondent_key
        task.meta["repo_label"] = repo_label
        if wake_request_report is not None:
            task.meta["wake_request"] = wake_request_report
        protocol.update_event_meta(event, run_id=task.id, repo_label=repo_label)
        daemon._persist_run_state_doc(
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
        body = daemon._deduplicated_event_body()
        resp_path = protocol.response_path(responses_dir, eid)
        task.terminal_reply = body
        protocol.write_response(responses_dir, eid, body)
        daemon._stage_terminal_response(
            task, account_context, event, resp_path,
        )
        daemon._record_response_artifact(emit, task, resp_path)
        daemon._set_event_status_if_present(event, "done")
        emit("finalizing", run_id=task.id, stage="deduplicated")
        emit("done", run_id=task.id, event_id=eid, publish_status="deduplicated")
        return Finalized(task, "deduplicated")

    # Refresh local refs before resolving the branch plan so the run
    # seeds from a current view of the world. Computing target_branches
    # off the raw event (rather than the resolved plan) avoids a chicken-
    # and-egg loop and lets a future github-gate event for a PR comment
    # name its head branch via ``branch_target`` for free.
    if is_home_root:
        sync_result = sync.SyncResult()
        # Same reasoning as branching.resolve_publish_plan's sibling probe
        # in the ``else`` branch below: this is the first thing
        # _run_worker does, nothing has run yet, and the outer crash
        # handler around _run_worker (see its "worker_crash" defer/backoff
        # path) turns an uncaught failure here into a cleanly deferred run
        # instead of a silent mis-seed off a host branch git couldn't
        # actually name.
        host_branch = gitops.current_branch(repo_root)  # current-branch: propagates
        seed = host_branch if host_branch != "HEAD" else "HEAD"
        branch_plan = branching.PublishPlan(
            seed_ref=seed,
            target_branch=None,
            source="home:host",
            host_context_branch=host_branch if host_branch != "HEAD" else None,
            seed_oid=gitops.rev_parse(repo_root, seed),
        )
    else:
        sync_targets = daemon._branches_to_refresh(repo_root, event)
        sync_result = sync.refresh_before_run(
            repo_root, target_branches=sync_targets, cfg=cfg,
        )
        branch_plan = branching.resolve_publish_plan(repo_root, event, cfg)

    task = Run.from_event(event, cfg)
    # brnrd#2023: the seat's scroll, claimed here and nowhere else. A release
    # arms it; this dispatch — the one that actually leads — takes it, by
    # rename, exactly once. `Run.from_event` can no longer carry it off the
    # event, so this is the only way a run wakes warm.
    seat_home = daemon._shuttle_home(
        account.context_home_root(account_context) if account_context else None,
        runs_dir,
    )
    if resource_hold.is_handover(event):
        # A handover asks for a *successor*. Its carry-forward body is the
        # whole inheritance, and the claim the parked seat armed is not its
        # to take — unmake it rather than leave it for the next dispatch.
        pending_resume.clear(seat_home, why=f"handover {eid}")
    else:
        claim = pending_resume.consume(seat_home, conversation_key=conv_key)
        if claim:
            task.meta["resume_native_session_id"] = claim["session_id"]
            task.meta["resume_native_provider"] = claim.get("provider") or ""
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
    _stamp_topic_proposal(event, task, account_context, brr_dir, conv_key)
    # Move 4b: a stake the waking event carries (frontmatter, or a chat
    # message's lead `stake:` line) arms at the run's first boundary
    # (`daemon._stake_facet`), where the seat's meter is read.
    stake_request = stake_mod.request_from(event, str(event.get("body") or ""))
    if stake_request is not None:
        stake_request["event_id"] = eid
        if event.get("stake_carried_spent") not in (None, ""):
            stake_request["carried_spent"] = str(event.get("stake_carried_spent"))
        task.meta["stake_request"] = stake_request

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
        task.save(runs_dir)
        task.transition("done", why="trust_refused")
        task.meta["publish_status"] = "refused"
        protocol.update_event_meta(event, run_id=task.id, repo_label=repo_label)
        daemon._persist_run_state_doc(
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
        body = daemon._trust_refused_event_body(reason)
        resp_path = protocol.response_path(responses_dir, eid)
        task.terminal_reply = body
        protocol.write_response(responses_dir, eid, body)
        daemon._stage_terminal_response(task, account_context, event, resp_path)
        daemon._record_response_artifact(emit, task, resp_path)
        daemon._set_event_status_if_present(event, "done")
        emit("finalizing", run_id=task.id, stage="refused")
        emit("done", run_id=task.id, event_id=eid, publish_status="refused")
        return Finalized(task, "refused")

    # Bind the stop control to the run id, so a stop can be addressed by
    # either handle from here on (wyrd §3). Unconditional since #476: a
    # resident thought is registered too, and the live-runs view a user taps
    # names runs by run id, not by the event that woke them.
    daemon._bind_run_control(eid, task.id)
    # Persist the comparison base and the current verdict once, on the run
    # manifest. User-facing readers can then suppress the mechanically-created
    # placeholder branch without paying a git probe on every dashboard tick.
    task.meta["seed_ref"] = branch_plan.seed_ref
    task.meta["has_new_commit"] = False
    # #703 arm-of-record: the shared host checkout's state as this run starts.
    # Taken here — after `sync.refresh_before_run`'s fetch+fast-forward, before
    # the runner exists — so a daemon-owned ff is never mistaken for a run's own
    # write. Read back in `_run_worker_and_finalize` by `_stray_host_write`.
    daemon._record_host_baseline(task, repo_root)
    # The boot janitor runs in a future daemon process. Persist this daemon's
    # pid so that future boot can prove the process which owned the run is
    # gone instead of treating an absent pid as equivalent evidence.
    task.meta["pid"] = os.getpid()
    daemon._record_task_runner(task, runner_choice)
    daemon._persist_run_state_doc(
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
        live_run_label = daemon._presence_label_for_event(event)
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
    shuttle_home = (
        account.context_home_root(account_context)
        if account_context is not None else brr_dir
    )
    entity = shuttle.Shuttle.load(shuttle_home)
    if not daemon._is_strand(task.meta) and entity.state == "released":
        entity.transition(
            "awake", why="event_dispatched", by="daemon", run_id=task.id,
            repo_root=str(repo_root), conversation_key=task.conversation_key,
        )
    resp_path = protocol.response_path(responses_dir, eid)
    # Per-event drop zone for interim responses the resident ships
    # mid-flight (the multi-response protocol, kb/design-multi-response.md).
    # Created up front so the agent can write to it the moment it wakes.
    outbox_dir = brr_dir / "outbox" / eid
    outbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir = inbox_dir or (brr_dir / "inbox")

    # THE WELD, ignition half (#972, narrowed #1383) — see
    # `_weld_ignition_body` / `_weld_ignite` above for the guard and its
    # reasoning. Best-effort — the weld must never block the run.
    daemon._weld_ignite(event, account_context, outbox_dir, task.id)

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
        daemon._record_outbox_notice(
            outbox_dir,
            "repo config tried to set security-defining key(s) "
            f"{ignored_security_keys} in .brr/config — ignored, not "
            "honoured (they load only from the daemon-owned "
            "security.config; run `brnrd config promote` to migrate them "
            "there).",
            kind="refused",
            lifetime="standing",
        )

    # #693: the *file* half of the same domain. A runner profile carries
    # `cmd:` — the argv this daemon execs — so a repo-side `runners.md` is
    # `runner_cmd` under another name and is ignored exactly like one.
    # Same channel as above deliberately: an operator learning that a
    # security-defining input was dropped should not have to learn a
    # second place to look depending on whether it was a key or a file.
    # Unlike the key case this can be either a spent mirror or load-bearing
    # customisation, so the notice must carry the classifier's remedy rather
    # than always naming the migration command.
    ignored_profile_files = conf.ignored_repo_profile_files(repo_root)
    for ignored_file in ignored_profile_files:
        display_path = f".brr/{ignored_file.relpath}"
        if ignored_file.classification == "mirror":
            remedy = (
                "This file defines only equivalent profiles already loaded; "
                "delete it."
            )
        elif ignored_file.classification == "divergent":
            details = []
            if ignored_file.new_profiles:
                details.append(
                    "New profile(s): "
                    + ", ".join(f"`{name}`" for name in ignored_file.new_profiles)
                    + "."
                )
            if ignored_file.differing_profiles:
                details.append(
                    "Differing profile(s): "
                    + ", ".join(
                        f"`{name}`" for name in ignored_file.differing_profiles
                    )
                    + "."
                )
            remedy = (
                " ".join(details)
                + " Run `brnrd config promote` to move this file there."
            )
        else:
            reason = ignored_file.reason or "comparison unavailable"
            remedy = (
                f"Comparison unavailable: {reason}. Inspect the file before "
                "choosing whether to delete or promote it."
            )
        print(
            f"[brnrd] WARNING: run {task.id} (event {eid}): repo-side runner "
            f"profile file(s) {display_path} — ignored, not loaded (profiles "
            "load only from the daemon-owned home or the bundled catalog). "
            f"{remedy}"
        )
        daemon._record_outbox_notice(
            outbox_dir,
            f"repo-side runner profile file(s) {display_path} — ignored, not "
            "loaded. A profile carries `cmd:`, the command brnrd executes, "
            "so profiles load only from the daemon-owned home or the "
            f"bundled catalog. {remedy}",
            kind="refused",
            lifetime="standing",
        )

    # #700: the *unreachable* half of the same domain. #693 (above) covers
    # a repo-side runners.md being ignored; this covers the account's own
    # runners.md being unreachable in the first place. home_profiles_path
    # derives from security_config_path, whose resolution isn't total
    # (#663): from a linked worktree of a --separate-git-dir repo, the
    # account home can't be found and resolution falls through to a
    # per-repo project home. That home has no runners.md, which looks
    # identical — from here and from every other surface — to "never
    # configured one". Same channel as the notices above, deliberately
    # (#693's reasoning: an operator learning a security-defining input
    # was dropped shouldn't have to learn a second place to look). This is
    # a symptom guard, not a fix — and #663 is already *closed*, so "retire
    # when #663 closes" would read as licence to delete a live notice. The
    # real condition is behavioural, and lives on
    # `config.home_profiles_unreachable`'s docstring: retire this the day a
    # stranded linked worktree can resolve its account home again.
    if conf.home_profiles_unreachable(repo_root):
        print(
            f"[brnrd] WARNING: run {task.id} (event {eid}): the profile "
            "catalog's home is unreachable from this worktree (likely a "
            "linked worktree under --separate-git-dir, #663) — custom "
            "runner profiles are not being read here; the bundled catalog "
            "ran instead"
        )
        daemon._record_outbox_notice(
            outbox_dir,
            "custom runner profiles are not being read in this worktree — "
            "the profile catalog's home is unreachable from here (likely a "
            "linked worktree under --separate-git-dir, #663), so brnrd ran "
            "the bundled catalog instead of your account's runners.md. Run "
            "brnrd from the main checkout (or resolve #663) to restore your "
            "custom profiles.",
            # Technical unreachability, not a policy call — nothing here
            # decided to ignore the account's profiles, the path just
            # couldn't be resolved from this worktree (#663).
            kind="dropped",
            lifetime="standing",
        )

    # #1472: the ingress twin of `spawn: repo:`'s refusal, and the same
    # sentence. `_repo_for_event` resolves an explicit repo label it does
    # not serve by seeding the run in the *default* repo's tree while
    # handing the unresolved label back — so `task.meta["repo_label"]`, the
    # card, the relics and the branch all report a project this run is not
    # in. On a single-repo account the fallback happens to be right, which
    # is why it reads as working; the two labels are derived independently
    # on the server (`gates/cloud.py::local_repo_identity`) and here
    # (`account.repo_label`) and for a remote-less checkout they cannot
    # agree, so the wrong case is reachable, not hypothetical.
    #
    # The seeding is left exactly as it was. Whether an unresolvable label
    # should *refuse* the dispatch rather than mis-seed it is a product
    # call #1472 leaves open (a run in the wrong tree can push a branch to
    # the wrong project); this only ends the silence around it. Visible the
    # same two ways as the notices above, and for the #524 reason: a portal
    # notice the run reads at its own first boundary (`hooks.py` renders
    # notice text in full on the seed and Stop boundaries, so this lands in
    # the resident's opening screen, not only in a file someone might open),
    # and a host-side WARNING for an operator tailing logs.
    unserved_label = daemon._unserved_repo_label(account_context, event)
    if unserved_label and account_context is not None:
        landed_label = next(
            (
                repo.label
                for repo in account_context.repos.values()
                if repo.root == repo_root
            ),
            repo_root.name,
        )
        served = ", ".join(sorted(account_context.repos.keys()))
        print(
            f"[brnrd] WARNING: run {task.id} (event {eid}): repo "
            f"{unserved_label!r} is not a served repo — the daemon serves: "
            f"{served}. Seeded in {landed_label!r} instead."
        )
        daemon._record_outbox_notice(
            outbox_dir,
            # Front-loaded on purpose. `hooks._NOTICE_TEXT_CAP` truncates a
            # notice at 220 characters in the seed block, so the two facts
            # that make this correctable — the label asked for and the set
            # served — are said before the consequence clause, and it is the
            # consequence that gets cut on a long-labelled account, never
            # the remedy.
            f"repo: {unserved_label!r} is not a served repo. The daemon "
            f"serves: {served}. The run seeded in the tree of "
            f"{landed_label!r} instead — its branch, relics and kb writes "
            f"land there, not in {unserved_label!r}.",
            # Dispatched, and *not where it was addressed* — the same
            # content-yes / addressing-no shape the redirected gate reply
            # below is classed as. Lifetime "run", not "standing": this is
            # one event's routing miss, fresh and worth the `!N` alarm, not
            # a permanent property of the environment that would re-fire
            # every wake and habituate.
            kind="redirected",
            lifetime="run",
        )

    print(f"[brnrd] run {task.id} (event {eid}): env={task.env}")

    task.meta["response_path"] = str(resp_path)
    task.meta["outbox_path"] = str(outbox_dir)
    task.meta.update(branch_plan.meta_items())

    # Wyrd §3, strand isolation: a strand-stack child talks to its
    # dispatcher and its dispatchees, nobody else. It gets its contract
    # (the event body) and any parent messages — not the user thread's
    # recent turns, history, or burst siblings. The agenda-lock pitfall
    # (a strand following the thread's hottest topic instead of its
    # contract, and once forging a receipt from a sibling's SHA riding
    # the decoration, both caught live 2026-07) retires at the daemon
    # instead of by prompt discipline.
    is_strand_run = daemon._is_strand(event)
    event_body_for_prompt = event.get("body", "") or ""
    woven_body, woven_sibling_ids = (
        (None, set()) if is_strand_run
        else daemon._weave_burst_siblings_into_body(
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

    # "The first run takes it from there" — by mechanism, not by hope. The
    # connect-time greeting (#1244 fork 2) covers repos with a door that can
    # *originate*; a cloud-only pairing has none, so its first-ever wake used
    # to arrive as a plain conversational run with no word about setup — and
    # did none, on the cheapest and the strongest core alike (measured live,
    # 2026-08-19: a fresh install answered "nothing appears to need
    # intervention" over a repo with no contract). While `AGENTS.md` is
    # absent, any owner-trusted run a human addressed IS the setup run: fold
    # the init playbook + adopter template around the message. The trigger
    # state ends the moment the contract is committed, and the greeting
    # event is excluded because its body already carries the same playbook.
    if daemon._uninitialized_first_wake_applies(
        event,
        repo_root,
        cfg,
        is_strand_run=is_strand_run,
        is_home_root=is_home_root,
        correspondent_key=correspondent_key,
    ):
        try:
            init_facts = prompts.collect_daemon_wake_init_facts(repo_root)
        except Exception:  # noqa: BLE001 — facts are best-effort, never a blocker
            init_facts = None
        task.body = prompts.build_uninitialized_wake_task(
            repo_root, facts=init_facts,
        )
        task.meta["uninitialized_repo_wake"] = True
        print(
            f"[brnrd] run {task.id} (event {eid}): AGENTS.md absent — "
            "init playbook folded into this wake (first run on an "
            "uninitialized repo)"
        )

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
        daemon._write_terminal_failure_response(
            emit,
            task,
            event,
            responses_dir,
            resp_path,
            f"environment setup failed: {e}",
        )
        daemon._defer_pending_siblings_after_failure(
            inbox_dir,
            lead_event_id=eid,
            run_id=task.id,
            seconds=failure_defer_seconds,
        )
        emit("failed", run_id=task.id, stage="env", error=str(e))
        return Finalized(task, "env")

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
        # A host run has no assigned branch — pin the checkout's HEAD now so
        # relics.collection_scope's branchless fallback has a start point.
        # See _stamp_host_start_oid for why a bare rev_parse isn't enough
        # (#1309 item 2).
        daemon._stamp_host_start_oid(task, repo_root)
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
        from .. import ergonomics
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
        if conv_key and not is_strand_run else []
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
        if conv_key and not is_strand_run else None
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
        # Lane liveness (w-71): `200` beside `set`. A pure cache read — the
        # probes ride the scan tick below, never prompt assembly. Always
        # attached (never gated on "has any lane"), because the facet's
        # `absent` verdict is itself the answer a wake must see: "nobody has
        # probed" must not render as silence, which reads as fine.
        if not is_home_root:
            daemon._attach_lane_liveness_facet(communication_snapshot, repo_root)
        # Reader fluency (#217): which language this thread's reader reads.
        # v1 reads the repo-level `fluency` config key (weave | prose);
        # per-correspondent declaration at the gate boundary stays the
        # eventual shape. Renamed from `user_commitment: full | profane`
        # 2026-07-23 — `full` read as an amount, which is the one thing this
        # field must never mean (identity-core → Voice And The Seam).
        fluency = str(cfg.get("fluency") or "").strip()
        if fluency:
            communication_snapshot["fluency"] = fluency
        # The next boundary reads the same validated generation gates render.
        # Expired menus are filtered here; ingestion remains authoritative for
        # taps on controls a transport may still display from an older message.
        related_menu_threads = conversations.conversation_keys_for_correspondent(
            brr_dir,
            correspondent_key,
            include_key=conv_key,
        )
        live_menu = menus.load_live_menu(
            brr_dir,
            conv_key,
            correspondent_key=correspondent_key,
            legacy_threads=related_menu_threads,
        )
        if live_menu is not None:
            communication_snapshot["live_menu"] = live_menu
    recent_conversation = (
        communication_snapshot.get("recent_turns", [])
        if communication_snapshot else []
    )

    # Snapshot of other waiting events so the resident has immediate
    # orientation at wake. A live copy is also refreshed in the outbox
    # below and on every heartbeat.
    # Strands get the same isolation here as the live inbox below: the
    # user thread's pending events belong to the dispatcher. Found live
    # 2026-07-18 — a strand's boot prompt listed two of the maintainer's
    # telegram messages while inbox.json correctly showed none.
    pending_events_snapshot = daemon._pending_events_for_agent(
        inbox_dir,
        eid,
        strand=is_strand_run,
        account_context=account_context,
        repo_label=repo_label,
        observer_run_id=task.id,
    )
    if woven_sibling_ids:
        pending_events_snapshot = [
            ev for ev in pending_events_snapshot
            if str(ev.get("id") or "") not in woven_sibling_ids
        ]
    daemon._write_live_inbox(
        outbox_dir,
        inbox_dir,
        eid,
        strand=is_strand_run,
        account_context=account_context,
        repo_label=repo_label,
        observer_run_id=task.id,
    )

    # Other thoughts awake right now (presence registry), excluding this
    # one — so the resident knows it may share the dominion with a
    # concurrent session and reconciles rather than fights (slice 5).
    # Account-wide (#1727): the dominion these thoughts share is the
    # account's, and a `spawn:` strand with `repo:` registers in the repo
    # it runs in — reading only this checkout reported the run's own
    # sibling as absent.
    present_snapshot = [
        e for e in presence.list_active_account(brr_dir)
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
    # ``delivered`` (#743) is the subset of the three above that actually put
    # text in front of a reader — see the increment site in ``_drain_outbox``.
    output_stats = {"current": 0, "other": 0, "outbound": 0, "delivered": 0}
    prompt_diffense = prompts.diffense_emit_enabled(cfg)
    # Compatibility window: the key remains readable, but elapsed clocks no
    # longer reap a run. The dashboard stop lane is the sole live-process kill.
    configured_timeout = runner.runner_timeout(cfg)
    if configured_timeout is not None:
        print(
            "[brnrd] runner.timeout_seconds no longer reaps; "
            "the user cancels from the dashboard"
        )
    card_path = outbox_dir / daemon._CARD_CONTROL_NAME
    menu_path = outbox_dir / daemon._LIVE_MENU_NAME
    # Runner boundary back-channel flush signal: a stream driver or native
    # hook touches this dotfile to ask the daemon to drain now. Same host dir
    # the runner writes BRR_OUTBOX_DIR into (bind-mounted for container envs),
    # so the daemon reads the signal the boundary mechanism wrote. The signal
    # only asks; the daemon stays the sole drainer (see _drain_outbox / the
    # design doc).
    flush_path = outbox_dir / hooks_mod.FLUSH_SIGNAL_NAME
    card_state: dict[str, object] = {}
    menu_state: dict[str, object] = {}
    codex_events_path = outbox_dir / ".codex-events.jsonl"
    run_started_monotonic = time.monotonic()

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
        daemon._record_outbox_notice(
            outbox_dir,
            f"runner selection changed between resolution and spawn: "
            f"{runner_choice.name} -> {reselected.name}. The run starts on "
            f"{reselected.name}, the currently-set profile.",
            # Nothing refused or dropped — the run adopts the new
            # selection and proceeds. FYI only.
            kind="advisory",
            lifetime="run",
        )
        runner_choice = reselected
        runner_name = runner_choice.name
        runner_meta = runner_choice.portal_metadata()

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
    runner_catalog = runner.available_runner_catalog(repo_root, selected=runner_name)
    daemon._enrich_catalog_quota(runner_catalog, brr_dir)
    daemon._record_task_runner(task, runner_choice)
    run_ledger.mark_run_started(task, runner_name, outbox_dir, run_root)
    task.save(runs_dir)
    hud.write_live(hud.HUDInputs(
        outbox_dir=outbox_dir,
        inbox_dir=inbox_dir,
        current_event_id=eid,
        task=task,
        phase="preparing",
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
    ))

    return Prepared(
        event=event,
        repo_root=repo_root,
        responses_dir=responses_dir,
        cfg=cfg,
        max_retries=max_retries,
        account_context=account_context,
        inbox_dir=inbox_dir,
        eid=eid,
        brr_dir=brr_dir,
        runs_dir=runs_dir,
        repo_label=repo_label,
        is_home_root=is_home_root,
        is_strand_run=is_strand_run,
        conv_key=conv_key,
        correspondent_key=correspondent_key,
        failure_defer_seconds=failure_defer_seconds,
        emit=emit,
        task=task,
        presence_id=presence_id,
        shuttle_home=shuttle_home,
        branch_plan=branch_plan,
        env_backend=env_backend,
        env_ctx=env_ctx,
        run_root=run_root,
        branch_name=branch_name,
        branch_setup_notice=branch_setup_notice,
        context_path=context_path,
        event_body_for_prompt=event_body_for_prompt,
        communication_snapshot=communication_snapshot,
        recent_conversation=recent_conversation,
        pending_events_snapshot=pending_events_snapshot,
        present_snapshot=present_snapshot,
        prompt_diffense=prompt_diffense,
        resp_path=resp_path,
        outbox_dir=outbox_dir,
        card_path=card_path,
        menu_path=menu_path,
        flush_path=flush_path,
        codex_events_path=codex_events_path,
        card_state=card_state,
        menu_state=menu_state,
        output_stats=output_stats,
        trace_dirs=trace_dirs,
        seen_containers=seen_containers,
        run_started_monotonic=run_started_monotonic,
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
    )


def runner_runtime(
    selected: runner_select.RunnerProfile,
    *,
    task: Run,
    eid: str,
    env_ctx: envs.RunContext,
    context_path: Path,
    outbox_dir: Path,
    brr_dir: Path,
    cfg: dict,
    run_root: Path,
    repo_root: Path,
    emit: daemon._WorkerEmit,
) -> daemon._RunnerRuntime:
    """Resolve one runner profile into this run's env, hooks and argv.

    Was the ``_runner_runtime`` closure; called for the first attempt here
    and again by ``boundary`` when an automatic fallback swaps the Lane.
    Every captured name it read is now a parameter — none of them was ever
    rebound between the two call sites, so binding them at call time is the
    closure's own behaviour.
    """
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
            (env_ctx.outbox_env or outbox_dir) / daemon._LIVE_PORTAL_STATE_NAME
        ),
        # The wake's persisted BootScore — arms the hook's orientation
        # ledger (#513 Slice 9): `orient x/y` is metered against the
        # score's `orientation_set`. Same host-path convention as
        # BRR_CONTEXT_PATH (both live in `.brr/runs/<run-id>/`, written
        # by run_context before the runner starts).
        "BRR_BOOT_SCORE": str(
            brr_dir / "runs" / task.id / "boot-score.json"
        ),
        # The account/repo-shared ``.brr`` dir, warm across every run —
        # lets claude_status durably persist its spend/context reading
        # somewhere the *next* run's portal assembly can still find it
        # after this run's own outbox is swept (#1027; see
        # claude_status._shared_dir).
        "BRR_SHARED_DIR": str(brr_dir),
    }
    # Conversation identity passthrough: lets the resident stamp its own
    # commits with the Brnrd-Conversation-Id trailer (see gitops.commit_all
    # and kb/plan-conversation-id-propagation.md). Absent when the task has
    # no conversation — never export an empty value.
    if task.conversation_key:
        env["BRR_CONVERSATION_ID"] = task.conversation_key

    # Move 2c: `brnrd await` holds a lease rather than returning every ten
    # minutes, and claude's Bash tool kills any call at BASH_MAX_TIMEOUT_MS
    # (default 600000). Widen it to cover a full lease; the pre-tool hook
    # sets the await call's own `timeout` to this and tells the CLI
    # (`hooks._await_lease_input`). An operator's own value is left alone —
    # the hook reads whatever the Shell actually has, so a narrower cap just
    # means the lease returns `pending` sooner. Other Bash calls keep
    # claude's 2-minute default; only a call that asks for more can use it.
    if hooks_flavour == "claude" and not os.environ.get("BASH_MAX_TIMEOUT_MS"):
        env["BASH_MAX_TIMEOUT_MS"] = str(await_verb.CLAUDE_BASH_MAX_TIMEOUT_MS)

    # #1135: pin brnrd's own commit identity for the runner's *own* shell,
    # not just brnrd's internal git calls. `gitops.bot_identity_env()`
    # already pins these four vars for brnrd-authored commits (dominion
    # capture, the founding deed) — but a `git commit` the agent itself
    # types (resident or strand) never goes through that helper, so its
    # author fell through to whatever identity happened to be live on
    # the host at that moment: observed live, a strand's own commit
    # authored as the human operator. Env outranks `.git/config` at every
    # level, so setting it here overrides an inherited or ambient
    # identity the same way `_child_git_pin` overrides an inherited cwd.
    # Both resident and strand runs build their env through this one
    # function (`_run_worker`'s two call sites), so no separate wiring is
    # needed for either — unlike the git pin above, this is not
    # strand-scoped: a resident's own themed-work commits are equally in
    # scope. No config knob exists yet for an operator to choose their
    # own name here (grepped `.brr/config`, `security.config`,
    # `config.py`); that is a product decision for the maintainer, not
    # this fix, so the bot identity is unconditional for now.
    env["GIT_AUTHOR_NAME"] = gitops.BOT_NAME
    env["GIT_AUTHOR_EMAIL"] = gitops.BOT_EMAIL
    env["GIT_COMMITTER_NAME"] = gitops.BOT_NAME
    env["GIT_COMMITTER_EMAIL"] = gitops.BOT_EMAIL

    # #703: pin this strand's git to the worktree it was given, so a shell
    # whose cwd drifted to the execution root cannot commit into the
    # *shared host checkout*. Live 2026-07-24: run-260724-2109-hqfz put 262
    # insertions of its deliverable on the maintainer's own `main`, twice,
    # while its own branch published empty — and an empty publish is
    # indistinguishable from a strand that correctly had nothing to commit,
    # so nothing refused it and nothing reported it. Worktree isolation is
    # the filesystem lane; git's notion of "which working tree am I in" is
    # just cwd, and cwd is not contained.
    env.update(daemon._child_git_pin(task, run_root))
    # #1184: the pin above closes the *git* half of the same hazard —
    # `Edit`/`Write` take a raw absolute path with no pin at all, and the
    # strand worktree is a *child directory* of this same host checkout
    # (`<repo_root>/.brr/worktrees/<run-id>/…`), so the host path is a
    # strict prefix of the strand's own and the shape a model completes
    # when it reaches for "the absolute path to X". `repo_root` is the
    # host checkout `_run_worker` was dispatched against — armed only
    # when the git pin actually pinned (``GIT_WORK_TREE`` present),
    # the same fact-based gating `_child_git_pin` itself uses: no readable
    # git dir ⇒ nothing to compare a write path against, same as the pin's
    # own degrade.
    #
    # `BRR_WORK_TREE` duplicates `GIT_WORK_TREE` under the `BRR_` namespace
    # deliberately, rather than the PreToolUse hook reading `GIT_WORK_TREE`
    # itself: every `brnrd hook <phase>` invocation runs through
    # `cli.main()`, and its first act — `_drop_inherited_git_pin` — pops
    # `GIT_DIR`/`GIT_WORK_TREE` from `os.environ` before anything else runs
    # (so brnrd's *own* git calls are never blinded by the pin they gave
    # the strand). That scrub is correct and load-bearing for every other
    # `brnrd` command, but it also means the hook subprocess this predicate
    # runs in can never see `GIT_WORK_TREE` — a `BRR_`-namespaced copy is
    # the only way `hooks._rooted_write_neutral` (see `HookContext`) gets
    # to know the boundary at all. Caught live driving this guard end to
    # end rather than only through `hooks.run_hook` unit tests, which
    # construct their env dict by hand and so never exercise the scrub.
    if "GIT_WORK_TREE" in env:
        env["BRR_HOST_ROOT"] = str(repo_root)
        env["BRR_WORK_TREE"] = env["GIT_WORK_TREE"]
    # The closeout guard (`hooks.next_move`, default off). Armed per-run via env
    # so the hook subprocess needs no config of its own. Default-off is the
    # control arm, not timidity: `next_move` failed 0/6 across *both* arms of the
    # drift bench, which makes it the cleanest baseline on the board — any
    # non-zero in the armed arm is signal. Measure, then default it on.
    #
    # Not armed for strands — and the reason has been restated, because the
    # one it used to give ("`strand.md` grants no chat seam") is no longer
    # true. A strand *can* reach a human: `gate:` carries no strand
    # predicate anywhere on its path, and `strand.md` now teaches that seam
    # explicitly. The skip survives the correction on a different footing:
    # `next_move` enforces the shape of a *chat turn* (a menu, or a bare
    # `done`/`continuing`/`blocked` state), and a strand's terminal stream
    # is not a chat turn — it is a return value collected on the dispatch
    # edge by whoever spawned it (`terminal_route: dispatch-edge`). Demanding
    # a menu of a return value would block a run for failing a contract it
    # was never given. A strand's *deliberate* `gate:` escalation is a
    # different artifact and is not what this guard reads.
    obligations: list[str] = []
    if cfg.get("hooks.next_move", False) and not daemon._is_strand(task.meta):
        env["BRR_NEXT_MOVE_GUARD"] = "1"
        # Same arming, same control-arm discipline: the guard also escalates
        # the clean artifact obligation (card) from format_delta's soft
        # `inject` mention to a hard block. A pure fresh-file existence check.
        obligations.append("card")
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
            if daemon._gate_can_deliver(brr_dir, "forge"):
                env["BRR_FORGE_GATE"] = "1"

    # The local CI-gate obligation, armed by a repo that declares what its
    # gate *is* (`hooks.gate_command`). brr ships no default: guessing a
    # stranger's build command is how a guard fires constantly for a
    # non-reason, and a project with no local gate owes nothing.
    #
    # Deliberately NOT behind `hooks.next_move`. That flag is a control arm
    # for an unmeasured reply-shape intervention; this is an explicit
    # per-repo declaration, which is already the opt-in. Chaining one to the
    # other would make a project that named its gate wonder why nothing
    # checks it.
    #
    # Not host-only either, unlike `scm`: that clause is about *publishing*,
    # which only the host environment fails to do for you, while this one is
    # about whether the code was checked — equally true in a worktree. It
    # needs the same fresh-git read, so it arms `BRR_REPO_DIR` itself when
    # the host branch above did not.
    #
    # Strands stay out for now, and the reason is cost, not principle: the
    # parent reviews the child's diff and runs the gate on the merged tree
    # itself (that is the standing rule, because a strand's own suite claim
    # is not evidence), so the tree that matters is already covered — while
    # arming every child would multiply full-gate minutes across a fleet, a
    # regression nobody has measured.
    gate_command = str(cfg.get("hooks.gate_command", "") or "").strip()
    if (
        gate_command
        and not daemon._is_strand(task.meta)
        and task.meta.get("root_kind") != "home"
    ):
        obligations.append("gate")
        env["BRR_GATE_COMMAND"] = gate_command
        env.setdefault("BRR_REPO_DIR", str(run_root))
        if env_ctx.branch_plan is not None:
            env.setdefault("BRR_SEED_REF", env_ctx.branch_plan.seed_ref)

    # The vigil obligation (#947): a terminal reply may claim a continuation
    # only if one is armed. Needs no repo declaration and no extra env — its
    # two artifacts are the run's own `.keepalive` and the presence
    # projection already in portal-state — so, like `gate`, it is not behind
    # the `hooks.next_move` control arm: this is not an unmeasured
    # reply-*shape* nudge, it is a claim that was false twice in one day,
    # each time costing the maintainer a wait on a run already `done`.
    #
    # Not for strands — same correction as `next_move` above: the old reason
    # ("`strand.md` grants no chat seam") is false; `gate:` was always open
    # to a strand and is now documented as open. What still holds is the
    # narrower half of the #779 reasoning: this guard reads the *terminal
    # reply*, and a strand's terminal reply is a return value the parent
    # collects, not a promise made to a waiting reader. "continuing" in a
    # return value is a sentence the dispatcher reads with full knowledge
    # that the strand is already dead — not a wait it was tricked into.
    # Nor does the skip open a hole on the new seam: `vigil` never read
    # outbox messages, so a strand's `gate:` escalation was outside its
    # scope before and after.
    if not daemon._is_strand(task.meta):
        obligations.append("vigil")

    # A conversation stays warm by default. Unlike `vigil`, which checks a
    # claim the reply made, `linger` checks the lifecycle itself: a user
    # should not pay for a cold wake merely because the resident forgot the
    # final wait.
    #
    # Armed for every non-strand seat, and deliberately NOT gated on
    # `source == "cloud"` any more (2026-09-12, `run-260912-0824-cicv`).
    # That gate read the *wake source* to decide a question about the
    # *room*: the run it let through was `source: schedule`, sent the
    # maintainer four chat replies between 08:29 and 08:48Z, and closed its
    # turn at 08:55 with no clause standing — `linger` was the only guard
    # that could have caught a turn ending on an honest `done`, and it was
    # the one guard not armed. A schedule tick that spends half an hour
    # talking to a live human *is* a live chat counterpart by the time its
    # turn ends, and nothing at dispatch time can know that yet.
    #
    # So the arming is now unconditional for a seat and the *clause* is
    # what reads the room, at Stop, from portal-state — where the answer
    # actually exists (`_linger_closeout_clause`). A run that never spoke
    # to anyone stays silent exactly as before.
    if not daemon._is_strand(task.meta):
        obligations.append("linger")

    # A strand that ends its turn with neither a submit nor a bolt has
    # not finished — it has stopped waiting by returning (three did on
    # 2026-09-06, each "still holding" with no tool call, each run
    # closed under it). The seat parks at turn end; a strand does not,
    # so the Stop hook blocks it once and names `brnrd await --file`.
    if daemon._is_strand(task.meta):
        obligations.append("hold")

    if obligations:
        env["BRR_CLOSEOUT_OBLIGATIONS"] = ",".join(obligations)

    if env_ctx.outbox_env:
        env["BRR_OUTBOX_DIR"] = str(env_ctx.outbox_env)
        env["BRR_INBOX_PATH"] = str(env_ctx.outbox_env / daemon._LIVE_INBOX_NAME)

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
    return daemon._RunnerRuntime(meta, quota, env, extra_args, hooks_installed)


def _stamp_topic_proposal(event, task, account_context, brr_dir, conv_key) -> None:
    """Move 5c: the waking event carries a topic proposal from dispatch.

    ``topic_proposed`` (+ ``topic_proposed_by`` ∈ ``signature`` · ``thread``)
    lands on the event file and the run's meta when the frame can propose
    one; the resident's ``.topic`` confirms or overrides it at its first
    boundary (``run_topic.settle``). When nothing proposes a live topic, the
    event carries ``topic_suggested`` instead (move 5d) — a slug minted from
    its text that `new` alone in ``.topic`` mints. An event already carrying ``topic:`` (a
    strand's dispatch) was assigned at entry and is not proposed for. The
    previous run on the thread ending unassigned rides the event dict (never
    the file) as ``predecessor_topic_unset`` for the bundle's one line.
    Best-effort: nothing here may sink a dispatch, and nothing is written
    when nothing is proposed.
    """
    try:
        home = account.context_home_root(account_context) if account_context is not None else None
        slug, why = run_topic.proposal(home, event, thread=conv_key)
        if slug and why == "suggested":
            # Move 5d: a candidate for a *new* heddle, not a live topic — its
            # own key, so nothing reading `topic_proposed` mistakes it.
            protocol.update_event_meta(event, topic_suggested=slug)
            task.meta[run_topic.META_SUGGESTED] = slug
        elif slug:
            protocol.update_event_meta(
                event, topic_proposed=slug, topic_proposed_by=why,
            )
            task.meta[run_topic.META_PROPOSED] = slug
            task.meta[run_topic.META_PROPOSED_WHY] = why
        predecessor = run_topic.predecessor_topic_unset(brr_dir, conv_key, task.id)
        if predecessor:
            event["predecessor_topic_unset"] = f"{predecessor['run']} {predecessor['event']}"
    except Exception:  # noqa: BLE001 - a proposal never sinks a dispatch
        return

