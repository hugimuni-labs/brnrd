"""Daemon-side self-scheduling: firing due thoughts + retiring them.

Covers `daemon._fire_due_schedules` (reflex firing of dominion schedule
specs into the inbox) and `daemon._retire_internal_event` (gateless
schedule events clean up after themselves). See
`kb/design-self-scheduled-thoughts.md`.
"""

from __future__ import annotations

import time

from brr import daemon, dominion, protocol, schedule

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
