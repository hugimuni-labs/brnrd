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


def test_schedule_attribution_finds_account_dominion_via_forwarded_context(tmp_path):
    """#1409: schedule attribution must resolve the *account*-scoped schedule.md.

    ``_attribute_schedule_entries`` -> ``_schedule_entries_touched_by_run`` ->
    ``_primary_dominion_candidate`` used to hand-build a duplicate candidate
    for this. Now it forwards ``account_context`` straight into
    ``resident_dominion_candidates`` instead — this test is the guard: it
    drives the real attribution seam end to end against an account-scoped
    (not legacy) dominion, so a broken forward (wrong path, wrong
    capture_root) shows up as a missing tier record, not a passing test that
    never touched the account path at all.
    """
    from brr.run import Run

    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    home = tmp_path / "account-home"
    cfg = {"home.path": str(home), "repo.label": "Gurio/brr"}
    ctx = account.resolve_context(repo, cfg)
    repo_dom = account.repo_dominion_path(ctx, "Gurio/brr")
    dominion.seed_account_dominion(repo_dom)
    _write_schedule(repo_dom, "## Nightly Sweep\nevery: 6h\nsweep\n")
    dominion.commit(
        ctx.dominion_repo,
        "brnrd-home: capture account memory after run run-attrib",
    )
    task = Run(
        id="run-attrib",
        event_id="evt-attrib",
        body="sweep",
        source="telegram",
        status="done",
        meta={"repo_label": "Gurio/brr", "trust_tier": "operator"},
    )

    daemon._attribute_schedule_entries(task, brr_dir, repo, cfg, ctx)

    tier_map = schedule.load_state(brr_dir).get(schedule._TIER_BY_ENTRY_KEY) or {}
    assert tier_map.get("nightly-sweep", {}).get("tier") == "operator"


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


def test_fire_due_carries_valid_runner_pins_only(tmp_path, monkeypatch):
    from brr.runner_select import RunnerProfile

    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    _write_schedule(
        path,
        f"## Cheap\nat: {past}\nshell: codex\ncore: luna\ncheap task\n\n"
        f"## Default\nat: {past}\ndefault task\n",
    )
    monkeypatch.setattr(
        daemon.runner,
        "resolve_runner_profile",
        lambda _root, pins: RunnerProfile(
            name="codex-mini", profile="codex-mini", shell="codex", model="luna"
        ),
    )

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    events = {event["schedule_id"]: event for event in protocol.list_pending(inbox)}
    assert events["cheap"]["shell"] == "codex"
    assert events["cheap"]["core"] == "luna"
    assert "runner" not in events["cheap"]
    assert all(
        key not in events["default"] for key in ("shell", "core", "runner")
    )


def test_fire_due_bad_runner_pin_falls_back_to_config_default(
    tmp_path, monkeypatch, capsys,
):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    _write_schedule(
        path,
        f"## Typo\nat: {past}\nshell: definitely-missing\nstill run\n",
    )

    def unavailable(_root, _pins):
        raise RuntimeError("not found on PATH")

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", unavailable)

    daemon._fire_due_schedules(repo, brr_dir, inbox, {"shell": "claude"})

    (event,) = protocol.list_pending(inbox)
    assert event["schedule_id"] == "typo"
    assert all(key not in event for key in ("shell", "core", "runner"))
    log = capsys.readouterr().out
    assert "typo Runner pin unavailable" in log
    assert "shell='definitely-missing'" in log
    assert "firing on config default" in log


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


# ── armed dated-letters snapshot (#904) ───────────────────────────────


def test_fire_due_snapshots_armed_letter_with_premise(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    future = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    _write_schedule(
        path,
        f"## Ship the thing\nat: {future}\n"
        "premise: the release branch is still green\ndeploy it\n",
    )

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    # Not due yet — no event fired, but it's armed and projected.
    assert protocol.list_pending(inbox) == []
    armed = schedule.load_armed_letters(brr_dir)
    assert len(armed) == 1
    row = armed[0]
    assert row["id"] == "ship-the-thing"
    assert row["heading"] == "Ship the thing"
    assert row["when"] == future
    assert row["premise"] == "the release branch is still green"


def test_fire_due_snapshots_armed_letter_without_premise(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    future = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    _write_schedule(path, f"## Followup\nat: {future}\ncheck the CI run\n")

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    (row,) = schedule.load_armed_letters(brr_dir)
    assert row["id"] == "followup"
    assert row["premise"] is None


def test_fire_due_armed_letters_drop_once_fired(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    _write_schedule(path, f"## Followup\nat: {past}\ncheck the CI run\n")

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    # Fired this very tick — no longer armed, and its own firing was
    # projected as pending only up to (not through) the moment it went off.
    assert len(protocol.list_pending(inbox)) == 1
    assert schedule.load_armed_letters(brr_dir) == []


def test_fire_due_armed_letters_excludes_every_entries(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    _write_schedule(path, "## Upkeep\nevery: 60s\nrun upkeep\n")

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    assert schedule.load_armed_letters(brr_dir) == []


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


def test_fire_due_paces_but_does_not_silence_critical_quota_floor(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    _write_schedule(
        path,
        f"## Upkeep\nevery: 60s\nrun upkeep\n\n"
        "## Soon\nevery: 100s\nnot yet under paced cadence\n\n"
        f"## Followup\nat: {past}\ncheck the CI run\n",
    )
    # One recurring entry is due even under 3x pacing; the other is 250s into
    # a paced 300s interval and must wait.
    schedule.save_state(
        brr_dir,
        {
            "upkeep": {"kind": "every", "last_fired": 0.0},
            "soon": {"kind": "every", "last_fired": time.time() - 250},
        },
    )
    # Below the default critical floor (8%): the datum stays actionable. The
    # recurring entry stretches, but a due one still reaches the resident.
    _write_quota_cache(brr_dir, 5.0)

    daemon._fire_due_schedules(repo, brr_dir, inbox, {"shell": "claude"})

    fired = {e["schedule_id"] for e in protocol.list_pending(inbox)}
    assert fired == {"followup", "upkeep"}  # `soon` was paced, not silenced
    assert schedule.load_state(brr_dir)["_pacing"] == {
        "mode": "quota-paced",
        "factor": 3.0,
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


def test_fire_due_paces_own_core_week_model_bucket(tmp_path, monkeypatch):
    """The same snapshot, this time pinned to the Core the thin bucket
    actually names — it must still bind and pace `every:` entries."""
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
    assert fired == {"upkeep"}
    assert schedule.load_state(brr_dir)["_pacing"] == {
        "mode": "quota-paced",
        "factor": 3.0,
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


def test_fire_due_paces_each_entry_against_its_runner_and_caches_levels(
    tmp_path, monkeypatch,
):
    from brr.runner_select import RunnerProfile

    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    path = dominion.ensure_dominion(repo, push=False)
    _write_schedule(
        path,
        "## Cheap one\nevery: 60s\nshell: codex-mini\none\n\n"
        "## Cheap two\nevery: 60s\nshell: codex-mini\ntwo\n\n"
        "## Default\nevery: 60s\ndefault\n",
    )
    schedule.save_state(
        brr_dir,
        {
            entry_id: {"kind": "every", "last_fired": 0.0}
            for entry_id in ("cheap-one", "cheap-two", "default")
        },
    )
    monkeypatch.setattr(
        daemon.runner,
        "resolve_runner_profile",
        lambda _root, _pins: RunnerProfile(
            name="codex-mini", profile="codex-mini", shell="codex", model="luna"
        ),
    )
    monkeypatch.setattr(
        daemon.runner,
        "runner_profile",
        lambda _name, _root: RunnerProfile(
            name="claude", profile="claude", shell="claude", model="opus"
        ),
    )
    level_reads = []

    def collect_levels(runner_name, *_args, **_kwargs):
        level_reads.append(runner_name)
        remaining = 80.0 if runner_name == "codex-mini" else 1.0
        return (
            {
                "quota": {
                    "buckets": {"week": {"remaining_percentage": remaining}}
                }
            },
            True,
        )

    monkeypatch.setattr(daemon, "_collect_levels", collect_levels)

    daemon._fire_due_schedules(repo, brr_dir, inbox, {"shell": "claude"})

    fired = {event["schedule_id"] for event in protocol.list_pending(inbox)}
    assert fired == {"cheap-one", "cheap-two", "default"}
    assert level_reads.count("codex-mini") == 1
    assert level_reads.count("claude") == 1
    pacing = schedule.load_state(brr_dir)["_pacing"]
    assert pacing["mode"] == "quota-paced"
    assert pacing["factor"] == 3.0
    assert pacing["entries"]["cheap-one"]["mode"] == "normal"


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
    """Passing inbox_dir + run_id retires every spawn_completed event whose
    spawn_parent_run_id matches run_id *and* was actually rendered to that
    parent's wake surface — even when the waking event is a gate event (the
    common case: parent woke from user message, child finishes mid-run).

    Rewritten 2026-08-07 (#1146): the original fixture retired c1/c2 without
    ever simulating a render, matching only on spawn_parent_run_id — the
    exact "pending-on-disk is not evidence of observation" bug the issue
    closes. This version renders the parent's own view
    (`_pending_events_for_agent`) once, as a real run's heartbeat would,
    before retiring — c1/c2 pick up the `observed_by` stamp and retire;
    c_other belongs to a different parent, is never rendered to this one,
    and stays pending on both counts.

    Drive red: comment out the `observed_by` check in `_retire_internal_event`
    (or the stamp in `_pending_events_for_agent`) and confirm this fails;
    restore to keep.
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

    # Parent A renders its own view at least once during its run — the
    # observation point (#1146). Parent B never does, in this fixture.
    daemon._pending_events_for_agent(
        inbox, gate_event["id"], observer_run_id="run-parent-A",
    )

    result = daemon._retire_internal_event(
        gate_event, responses,
        inbox_dir=inbox,
        run_id="run-parent-A",
    )
    # Returns False because the waking event is a telegram event, not internal.
    assert result is False
    # Our two observed spawn_completed events are now delivered.
    assert protocol._read_event(c1)["status"] == "delivered"
    assert protocol._read_event(c2)["status"] == "delivered"
    # The unrelated (unobserved, different-parent) spawn_completed is untouched.
    assert protocol._read_event(c_other)["status"] == "pending"
    # The gate event itself is untouched (gate owns it).
    assert protocol._read_event(gate_path)["status"] == "pending"


def test_spawn_completed_not_dispatched_after_parent_observed(tmp_path):
    """A spawn_completed event the parent actually *rendered* to its own
    wake surface (inbox.json / portal-state.json / prompt — all three read
    `_pending_events_for_agent`) is retired at parent closeout and must not
    reappear in list_dispatchable.

    Rewritten 2026-08-07 (#1146). The original fixture asserted retirement
    unconditionally, without ever simulating observation — which exercised
    exactly the bug this issue closes: pending-on-disk was being treated as
    proof the parent saw the completion. The old assertion was not merely
    inconvenient, it was checking the wrong invariant, despite the test's
    own name already promising "after_parent_observed". This version drives
    the real render path (`_pending_events_for_agent`) with the parent's own
    run id before retiring, so it exercises the stamp-then-retire contract.

    Drive red: remove the `observed_by` stamp in `_pending_events_for_agent`
    (or the `observed_by` check in `_retire_internal_event`) and confirm the
    completion survives retirement / never gets stamped; restore to keep.
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

    # The parent renders its own inbox at least once during its lifetime —
    # this is the observation point (#1146). Address a different waking
    # event id so the completion isn't excluded as "the current event".
    daemon._pending_events_for_agent(
        inbox, gate_event["id"], observer_run_id="run-parent-X",
    )
    observed = next(
        e for e in protocol.list_pending(inbox) if e.get("id") == completion.stem
    )
    assert observed.get("observed_by") == "run-parent-X", (
        "the render path must stamp observed_by for the declared parent"
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
        "an observed spawn_completed must not remain pending after the "
        "parent run's retire step"
    )


def test_spawn_completed_survives_retirement_when_never_observed(tmp_path):
    """The other half of #1146: a completion the parent's run never rendered
    to any wake surface — the child finishing after the parent's last tool
    boundary, the tail-of-run race the issue names — must NOT be retired at
    the parent's closeout. It stays pending so a successor (or, since
    #1147, an adopter) can still see and act on it, instead of the fact
    silently disappearing under `_retire_internal_event`'s unconditional
    match on `spawn_parent_run_id` alone.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"

    gate_path = protocol.create_event(
        inbox, "telegram", "parent task", chat_id="99",
    )
    gate_event = {"source": "telegram", "id": gate_path.stem, "_path": gate_path}
    protocol.set_status(gate_event, "processing")

    completion = protocol.create_event(
        inbox, "spawn_completed", "child run-x done: status=done",
        spawn_parent_run_id="run-parent-X",
    )

    # No call to `_pending_events_for_agent` here — the parent's run ends
    # without ever having folded this completion into its own view.
    daemon._retire_internal_event(
        gate_event, responses,
        inbox_dir=inbox,
        run_id="run-parent-X",
    )

    remaining = [e for e in protocol.list_pending(inbox) if e.get("status") == "pending"]
    assert any(e.get("id") == completion.stem for e in remaining), (
        "an unobserved spawn_completed must survive the parent's retire step"
    )
    assert any(
        e.get("id") == completion.stem for e in protocol.list_dispatchable(inbox)
    ), "a surviving completion must stay dispatchable for a successor/adopter"


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
