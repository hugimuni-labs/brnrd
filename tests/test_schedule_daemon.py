"""Daemon-side self-scheduling: firing due thoughts + retiring them.

Covers `daemon._fire_due_schedules` (reflex firing of dominion schedule
specs into the inbox) and `daemon._retire_internal_event` (gateless
schedule events clean up after themselves). See
`kb/design-self-scheduled-thoughts.md`.
"""

from __future__ import annotations

import time

from brr import account, claude_usage, daemon, dominion, protocol, schedule

from _helpers import commit_files, init_git_repo


def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    init_git_repo(repo)
    commit_files(repo, {"README.md": "main\n"}, message="init main")
    (repo / ".brr").mkdir()
    return repo


def _write_schedule(dom, text):
    (dom / schedule.SCHEDULE_FILE).write_text(text, encoding="utf-8")


def test_fire_due_creates_event_for_past_at(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    _write_schedule(path, f"## Followup\nat: {past}\ncheck the CI run\n")

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    pending = protocol.list_pending(inbox)
    assert len(pending) == 1
    assert pending[0]["source"] == "schedule"
    assert pending[0]["schedule_id"] == "followup"
    assert "check the CI run" in pending[0]["body"]
    # Fired once: a second tick doesn't re-emit.
    daemon._fire_due_schedules(repo, brr_dir, inbox, {})
    assert len(protocol.list_pending(inbox)) == 1


def test_fire_due_reads_account_dominion_before_legacy(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    legacy = dominion.ensure_dominion(repo, push=False)
    _write_schedule(legacy, "")
    home = tmp_path / "account-home"
    cfg = {"home.path": str(home), "repo.label": "Gurio/brr"}
    ctx = account.resolve_context(repo, cfg)
    repo_dom = account.repo_dominion_path(ctx, "Gurio/brr")
    dominion.seed_account_dominion(repo_dom)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    _write_schedule(repo_dom, f"## Account Followup\nat: {past}\naccount task\n")

    daemon._fire_due_schedules(
        repo,
        brr_dir,
        inbox,
        cfg,
        account_context=ctx,
    )

    pending = protocol.list_pending(inbox)
    assert len(pending) == 1
    assert pending[0]["schedule_id"] == "account-followup"
    assert pending[0]["repo_label"] == "Gurio/brr"


def test_fire_due_threads_with_default_conversation_key(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    _write_schedule(path, f"## Daily Sweep\nat: {past}\nsweep\n")

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    ev = protocol.list_pending(inbox)[0]
    # Default per-entry thread so a recurring entry's firings share history.
    assert ev["conversation_key"] == "schedule:daily-sweep"


def test_fire_due_honors_explicit_conversation_key(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    _write_schedule(
        path, f"## Nudge\nat: {past}\nconversation_key: telegram:7:\nnudge\n")

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    ev = protocol.list_pending(inbox)[0]
    assert ev["conversation_key"] == "telegram:7:"


def test_fire_due_every_anchors_then_fires(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    _write_schedule(path, "## Upkeep\nevery: 60s\nrun upkeep\n")

    # First sight anchors without firing.
    daemon._fire_due_schedules(repo, brr_dir, inbox, {})
    assert protocol.list_pending(inbox) == []
    assert "upkeep" in schedule.load_state(brr_dir)

    # Backdate the anchor so the interval has elapsed, then it fires.
    schedule.save_state(brr_dir, {"upkeep": {"kind": "every", "last_fired": 0.0}})
    daemon._fire_due_schedules(repo, brr_dir, inbox, {})
    pending = protocol.list_pending(inbox)
    assert [e["schedule_id"] for e in pending] == ["upkeep"]


def test_fire_due_respects_disabled(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    _write_schedule(path, f"## Followup\nat: {past}\nx\n")

    daemon._fire_due_schedules(repo, brr_dir, inbox, {"schedule.enabled": False})
    assert protocol.list_pending(inbox) == []


def test_fire_due_noop_when_nothing_due(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    _write_schedule(path, f"## Later\nat: {future}\nnot yet\n")

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})
    assert protocol.list_pending(inbox) == []


def _write_quota_cache(brr_dir, remaining_pct):
    """Drop a levels cache where a recent run actually leaves one.

    claude_usage only ever caches into a *run's own* outbox dir
    (``.brr/outbox/<event-id>/``) — never ``brr_dir`` itself, since an
    account-wide scheduler tick has no "current run" of its own. Before the
    `runner_quota.latest_claude_usage_outbox_dir` fix, `_fire_due_schedules`
    read `brr_dir` directly and could never see a real cache in production;
    this fixture now writes to the same per-run location the fixed read
    actually searches (`kb/plan-director-execution.md` §B2).
    """
    outbox_dir = brr_dir / "outbox" / "evt-quota-cache"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    claude_usage.write_snapshot(outbox_dir, {
        "source": "claude /usage PTY",
        "quota": {
            "summary": f"week {remaining_pct}% left",
            "buckets": {"week": {"remaining_percentage": remaining_pct}},
        },
    })


def test_fire_due_pauses_every_entries_under_critical_quota_floor(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    _write_schedule(
        path,
        f"## Upkeep\nevery: 60s\nrun upkeep\n\n## Followup\nat: {past}\ncheck the CI run\n",
    )
    # Anchor the every: entry as already due under its stated interval.
    schedule.save_state(brr_dir, {"upkeep": {"kind": "every", "last_fired": 0.0}})
    # Below the default critical floor (8%) — `every:` entries must not fire.
    _write_quota_cache(brr_dir, 5.0)

    daemon._fire_due_schedules(repo, brr_dir, inbox, {"shell": "claude"})

    fired = {e["schedule_id"] for e in protocol.list_pending(inbox)}
    assert fired == {"followup"}  # every: paused; at: is a deadline, still fires
    assert schedule.load_state(brr_dir)["_pacing"] == {
        "mode": "quota-paused",
        "remaining_pct": 5.0,
    }


def test_fire_due_stretches_every_interval_under_low_quota_floor(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    _write_schedule(path, "## Upkeep\nevery: 100s\nrun upkeep\n")
    # 250s since last fire: due under the stated 100s interval, but not under
    # the pacing-stretched interval (default stretch factor 3x -> 300s).
    schedule.save_state(
        brr_dir, {"upkeep": {"kind": "every", "last_fired": time.time() - 250}}
    )
    # Between the default critical (8%) and low (20%) floors.
    _write_quota_cache(brr_dir, 15.0)

    daemon._fire_due_schedules(repo, brr_dir, inbox, {"shell": "claude"})

    assert protocol.list_pending(inbox) == []
    assert schedule.load_state(brr_dir)["_pacing"] == {
        "mode": "quota-paced",
        "factor": 3.0,
        "remaining_pct": 15.0,
    }


def _write_quota_cache_with_week_model(brr_dir, week_pct, model_label, model_pct):
    """Like `_write_quota_cache`, but with a per-model week bucket alongside
    the account-wide one — the #561 shape."""
    outbox_dir = brr_dir / "outbox" / "evt-quota-cache"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    claude_usage.write_snapshot(outbox_dir, {
        "source": "claude /usage PTY",
        "quota": {
            "summary": f"week {week_pct}% left; {model_label} week {model_pct}% left",
            "buckets": {
                "week": {"remaining_percentage": week_pct},
                "week_models": {model_label: {"remaining_percentage": model_pct}},
            },
        },
    })


def test_fire_due_ignores_other_core_week_model_bucket(tmp_path, monkeypatch):
    """#561: a schedule tick pinned to a Shell/Core whose own quota is
    healthy must not pause `every:` entries off a *different* Core's
    near-exhausted week_models bucket."""
    from brr import runner as runner_mod
    from brr.runner_select import RunnerProfile

    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    _write_schedule(path, "## Upkeep\nevery: 60s\nrun upkeep\n")
    schedule.save_state(brr_dir, {"upkeep": {"kind": "every", "last_fired": 0.0}})
    # Week is healthy (44%); Fable's week bucket is critical (4%) but this
    # tick is pinned to opus.
    _write_quota_cache_with_week_model(brr_dir, 44.0, "Fable", 4.0)
    monkeypatch.setattr(
        runner_mod, "runner_profile",
        lambda name, repo_root=None: RunnerProfile(
            name=name, profile=name, shell="claude", model="opus",
        ),
    )

    daemon._fire_due_schedules(repo, brr_dir, inbox, {"shell": "claude-opus"})

    fired = {e["schedule_id"] for e in protocol.list_pending(inbox)}
    assert fired == {"upkeep"}  # not paused — Fable's bucket doesn't bind opus
    assert schedule.load_state(brr_dir)["_pacing"] == {"mode": "normal"}


def test_fire_due_pauses_on_own_core_week_model_bucket(tmp_path, monkeypatch):
    """The same snapshot, this time pinned to the Core the thin bucket
    actually names — it must still bind and pause `every:` entries."""
    from brr import runner as runner_mod
    from brr.runner_select import RunnerProfile

    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    _write_schedule(path, "## Upkeep\nevery: 60s\nrun upkeep\n")
    schedule.save_state(brr_dir, {"upkeep": {"kind": "every", "last_fired": 0.0}})
    _write_quota_cache_with_week_model(brr_dir, 44.0, "Fable", 4.0)
    monkeypatch.setattr(
        runner_mod, "runner_profile",
        lambda name, repo_root=None: RunnerProfile(
            name=name, profile=name, shell="claude", model="fable",
        ),
    )

    daemon._fire_due_schedules(repo, brr_dir, inbox, {"shell": "claude-fable"})

    fired = {e["schedule_id"] for e in protocol.list_pending(inbox)}
    assert fired == set()  # paused — this tick's own Core is the thin bucket
    assert schedule.load_state(brr_dir)["_pacing"] == {
        "mode": "quota-paused",
        "remaining_pct": 4.0,
    }


def test_fire_due_ignores_quota_pacing_without_resolvable_runner(tmp_path):
    """No `shell=`/`runner=` pin resolvable → pacing is skipped, not guessed;
    entries fire exactly as they would with no quota awareness at all."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    _write_schedule(path, "## Upkeep\nevery: 60s\nrun upkeep\n")
    schedule.save_state(brr_dir, {"upkeep": {"kind": "every", "last_fired": 0.0}})
    _write_quota_cache(brr_dir, 1.0)  # would be critical, if it were ever read

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})  # no shell/runner pin

    fired = {e["schedule_id"] for e in protocol.list_pending(inbox)}
    assert fired == {"upkeep"}


def test_retire_internal_event_closes_schedule_source_in_place(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    path = protocol.create_event(inbox, "schedule", "do upkeep", schedule_id="upkeep")
    event = {"source": "schedule", "id": path.stem, "_path": path}
    protocol.write_response(responses, path.stem, "done")

    assert daemon._retire_internal_event(event, responses) is True
    assert protocol._read_event(path)["status"] == "delivered"
    assert protocol.response_exists(responses, path.stem)


def test_retire_internal_event_leaves_gate_events_alone(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    path = protocol.create_event(inbox, "telegram", "hi", chat_id="42")
    event = {"source": "telegram", "id": path.stem, "_path": path}

    assert daemon._retire_internal_event(event, responses) is False
    assert path.exists()  # the gate owns delivery + cleanup


# ── #616: spawn_completed self-retirement ───────────────────────────────────


def test_retire_internal_event_closes_spawn_completed_waking_source(tmp_path):
    """A spawn_completed waking event (parent died before observing) closes
    in place, the same way a schedule wake does.

    Drive red: comment out the `spawn_completed` branch in
    _retire_internal_event and confirm this fails; restore to keep.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    path = protocol.create_event(
        inbox, "spawn_completed", "child run-x done: status=done",
        spawn_parent_run_id="run-parent",
    )
    event = {"source": "spawn_completed", "id": path.stem, "_path": path}

    assert daemon._retire_internal_event(event, responses) is True
    assert protocol._read_event(path)["status"] == "delivered"


def test_retire_internal_event_retires_observed_spawn_completeds_for_parent(tmp_path):
    """Passing inbox_dir + run_id retires all spawn_completed events whose
    spawn_parent_run_id matches run_id, even when the waking event is a gate
    event (the common case: parent woke from user message, child finishes
    mid-run).

    Drive red: comment out the inbox-scan block in _retire_internal_event and
    confirm this fails; restore to keep.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"

    # Two spawn_completed events for this parent.
    c1 = protocol.create_event(
        inbox, "spawn_completed", "child-1 done",
        spawn_parent_run_id="run-parent-A",
    )
    c2 = protocol.create_event(
        inbox, "spawn_completed", "child-2 done",
        spawn_parent_run_id="run-parent-A",
    )
    # One spawn_completed for a *different* parent — must stay pending.
    c_other = protocol.create_event(
        inbox, "spawn_completed", "unrelated child done",
        spawn_parent_run_id="run-parent-B",
    )
    # The waking event is a normal gate event (telegram), not a spawn_completed.
    gate_path = protocol.create_event(inbox, "telegram", "hello", chat_id="42")
    gate_event = {"source": "telegram", "id": gate_path.stem, "_path": gate_path}

    result = daemon._retire_internal_event(
        gate_event, responses,
        inbox_dir=inbox,
        run_id="run-parent-A",
    )
    # Returns False because the waking event is a telegram event, not internal.
    assert result is False
    # But our two spawn_completed events are now delivered.
    assert protocol._read_event(c1)["status"] == "delivered"
    assert protocol._read_event(c2)["status"] == "delivered"
    # The unrelated spawn_completed is untouched.
    assert protocol._read_event(c_other)["status"] == "pending"
    # The gate event itself is untouched (gate owns it).
    assert protocol._read_event(gate_path)["status"] == "pending"


def test_spawn_completed_not_dispatched_after_parent_observed(tmp_path):
    """A spawn_completed event whose parent has ended is retired and must not
    appear in _dispatchable_targets after the parent run finishes.

    Drive red: remove the inbox-scan block in _retire_internal_event and
    confirm that spawn_completed events survive the run-end path and reappear
    in list_dispatchable; restore to keep.

    Behaviour test — does not grep source for any token.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"

    # Simulate: parent woke from telegram, child finished, completion note landed.
    gate_path = protocol.create_event(
        inbox, "telegram", "parent task", chat_id="99",
    )
    gate_event = {"source": "telegram", "id": gate_path.stem, "_path": gate_path}
    protocol.set_status(gate_event, "processing")

    completion = protocol.create_event(
        inbox, "spawn_completed", "child run-x done: status=done",
        spawn_parent_run_id="run-parent-X",
    )

    # Confirm the completion IS in list_pending before retirement.
    pending_before = [e for e in protocol.list_pending(inbox)]
    assert any(e.get("id") == completion.stem for e in pending_before), (
        "fixture must produce a pending spawn_completed before the fix runs"
    )

    # Parent run ends — retire_internal_event retires the completion.
    daemon._retire_internal_event(
        gate_event, responses,
        inbox_dir=inbox,
        run_id="run-parent-X",
    )

    # The spawn_completed is no longer dispatchable.
    remaining = [e for e in protocol.list_pending(inbox) if e.get("status") == "pending"]
    assert not any(e.get("source") == "spawn_completed" for e in remaining), (
        "spawn_completed must not remain pending after parent run's retire step"
    )


def test_spawn_completed_still_pending_until_parent_retires(tmp_path):
    """A spawn_completed event that has NOT been observed (parent not yet
    ended) stays pending and visible — constraint #1.

    The fixture must be one that production can actually emit, so we use the
    real _notify_spawn_parent notifier. A fixture the daemon never writes is
    not coverage.
    """
    from brr.run import Run

    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"

    task = Run(
        id="run-child-Y", event_id="evt-child-Y", body="", source="telegram",
        status="done",
        meta={
            "spawn_parent_run_id": "run-parent-Y",
            "spawn_parent_conversation_key": "telegram:42:",
        },
    )
    daemon._notify_spawn_parent(inbox, task)

    # Without calling _retire_internal_event, the completion is still pending.
    pending = protocol.list_pending(inbox)
    assert len(pending) == 1
    note = pending[0]
    assert note["source"] == "spawn_completed"
    assert note["spawn_parent_run_id"] == "run-parent-Y"
    assert note["status"] == "pending"
    # Stays dispatchable (a parent-died-before-observing case can wake a run).
    assert any(
        e.get("id") == note["id"]
        for e in protocol.list_dispatchable(inbox)
    )
