"""#1244 fork 2 — the connect-time greeting event.

`connect_greeting.queue_greeting` is the plumbing that turns a completed
`brnrd account connect` on an uninitialized repo into a first-wake event
the resident's normal dispatch loop will pick up. These tests exercise the
module directly (no CLI, no real network) — `test_cli.py`'s
`test_account_connect_*` tests cover the wiring into the command itself.
"""

from __future__ import annotations

from pathlib import Path

from brr import connect_greeting, protocol
from brr.gates import runtime as gate_runtime

from _helpers import init_git_repo


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    brr_dir = repo / ".brr"
    return repo, brr_dir


def test_door_for_greeting_none_when_nothing_configured(tmp_path):
    repo, brr_dir = _repo(tmp_path)
    assert connect_greeting.door_for_greeting(brr_dir) is None


def test_door_for_greeting_ignores_cloud_only(tmp_path):
    """`cloud`'s relay is reply-shaped — it cannot originate a message, so a
    cloud-only pairing (no direct chat gate) must not be treated as a door."""
    repo, brr_dir = _repo(tmp_path)
    gate_runtime.save_state(brr_dir, "cloud", {
        "token": "tok", "brnrd_url": "https://brnrd.example", "account_id": "acc1",
    })
    assert connect_greeting.door_for_greeting(brr_dir) is None


def test_door_for_greeting_prefers_telegram_when_bound(tmp_path):
    repo, brr_dir = _repo(tmp_path)
    gate_runtime.save_state(brr_dir, "telegram", {"token": "t", "chat_id": 4242})
    door, meta = connect_greeting.door_for_greeting(brr_dir)
    assert door == "telegram"
    assert meta == {"telegram_chat_id": 4242}


def test_door_for_greeting_telegram_without_bind_falls_back_to_last_chat(tmp_path):
    """`is_configured` only requires a token; a gate that has seen inbound
    traffic but never had an explicit `bind` still has somewhere to reply,
    mirroring `telegram.py`'s own outbound fallback (`chat_id` then
    `last_chat_id`)."""
    repo, brr_dir = _repo(tmp_path)
    gate_runtime.save_state(brr_dir, "telegram", {"token": "t", "last_chat_id": 999})
    door, meta = connect_greeting.door_for_greeting(brr_dir)
    assert door == "telegram"
    assert meta == {"telegram_chat_id": 999}


def test_door_for_greeting_falls_back_to_slack_when_no_telegram(tmp_path):
    repo, brr_dir = _repo(tmp_path)
    gate_runtime.save_state(brr_dir, "slack", {
        "token": "t", "bot_user_id": "U1", "channel": "C123",
    })
    door, meta = connect_greeting.door_for_greeting(brr_dir)
    assert door == "slack"
    assert meta == {"slack_channel": "C123"}


def test_queue_greeting_mints_a_pending_event_on_the_resolved_door(tmp_path):
    repo, brr_dir = _repo(tmp_path)
    gate_runtime.save_state(brr_dir, "telegram", {"token": "t", "chat_id": 4242})

    outcome = connect_greeting.queue_greeting(repo, brr_dir)

    assert outcome.queued is True
    assert outcome.door == "telegram"
    assert outcome.event_id

    pending = protocol.list_pending(brr_dir / "inbox")
    assert len(pending) == 1
    event = pending[0]
    assert event["source"] == "telegram"
    assert event.get("telegram_chat_id") == 4242
    assert event.get(connect_greeting.GREETING_META_KEY)
    # trust.resolve_tier fails closed (untrusted) for any ingress-gate
    # source with no stamped trust_tier — this event was never inbound, so
    # it must stamp its own tier or the wake it dispatches would be
    # refused/downgraded as a stranger's message on the owner's own repo.
    assert event.get("trust_tier") == "owner"
    # The task text carries the reused init playbook and adopter template,
    # not a hand-rolled restatement — spot-check both landed.
    assert "AGENTS.md" in event["body"]
    assert "adopter template" in event["body"].lower()


def test_queue_greeting_is_idempotent_across_a_second_connect(tmp_path):
    repo, brr_dir = _repo(tmp_path)
    gate_runtime.save_state(brr_dir, "telegram", {"token": "t", "chat_id": 4242})

    first = connect_greeting.queue_greeting(repo, brr_dir)
    second = connect_greeting.queue_greeting(repo, brr_dir)

    assert first.queued is True
    assert second.queued is False
    assert second.event_id == first.event_id
    assert len(protocol.list_pending(brr_dir / "inbox")) == 1


def test_queue_greeting_skips_once_agents_md_exists(tmp_path):
    repo, brr_dir = _repo(tmp_path)
    gate_runtime.save_state(brr_dir, "telegram", {"token": "t", "chat_id": 4242})
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")

    outcome = connect_greeting.queue_greeting(repo, brr_dir)

    assert outcome.queued is False
    assert "AGENTS.md" in outcome.reason
    assert protocol.list_pending(brr_dir / "inbox") == []


def test_queue_greeting_reports_reason_when_no_door(tmp_path):
    repo, brr_dir = _repo(tmp_path)

    outcome = connect_greeting.queue_greeting(repo, brr_dir)

    assert outcome.queued is False
    assert outcome.event_id is None
    assert "door" in outcome.reason or "cloud" in outcome.reason
    assert protocol.list_pending(brr_dir / "inbox") == []
