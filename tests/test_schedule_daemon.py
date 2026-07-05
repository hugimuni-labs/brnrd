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


def test_retire_internal_event_cleans_up_schedule_source(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    path = protocol.create_event(inbox, "schedule", "do upkeep", schedule_id="upkeep")
    event = {"source": "schedule", "id": path.stem, "_path": path}
    protocol.write_response(responses, path.stem, "done")

    assert daemon._retire_internal_event(event, responses) is True
    assert not path.exists()
    assert not protocol.response_exists(responses, path.stem)


def test_retire_internal_event_leaves_gate_events_alone(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    path = protocol.create_event(inbox, "telegram", "hi", chat_id="42")
    event = {"source": "telegram", "id": path.stem, "_path": path}

    assert daemon._retire_internal_event(event, responses) is False
    assert path.exists()  # the gate owns delivery + cleanup
