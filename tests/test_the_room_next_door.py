"""THE ROOM NEXT DOOR (2026-10-01).

One resident slot serves every repo under an account. Two schedule seats
in Gurio/mistral-vibe sat in `brnrd await` while four of the maintainer's
messages — cloud events labelled `repo_label: hugimuni-labs/brnrd` — queued
behind them. `_pending_events_for_agent` dropped every event labelled for a
different repo, so the await never resolved and the slot never freed; he
killed both seats by hand. And the seats were in mistral-vibe at all because
`brnrd account add` made the added repo the account default as a side effect.
"""

from __future__ import annotations

from pathlib import Path

from brr import account, daemon, protocol


def _ctx(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return account.resolve_context(
        repo, {"home.path": str(tmp_path / "home"), "repo.label": "o/seat"},
    )


def test_a_person_writing_to_the_other_repo_reaches_the_live_seat(tmp_path):
    ctx = _ctx(tmp_path)
    inbox = tmp_path / ".brr" / "inbox"
    own = protocol.create_event(inbox, "schedule", "wire round", status="processing")
    # The production shape: a cloud message carrying the thread's repo label.
    chat = protocol.create_event(
        inbox, "cloud", "are you there?", repo_label="o/elsewhere",
    )
    view = {
        r["id"]: r for r in daemon._pending_events_for_agent(
            inbox, own.stem, account_context=ctx, repo_label="o/seat",
        )
    }
    assert chat.stem in view
    assert view[chat.stem]["foreign_repo"] == "o/elsewhere"


def test_internal_traffic_for_the_other_repo_stays_in_its_own_room(tmp_path):
    ctx = _ctx(tmp_path)
    inbox = tmp_path / ".brr" / "inbox"
    own = protocol.create_event(inbox, "cloud", "hello", status="processing")
    tick = protocol.create_event(
        inbox, "schedule", "goal pulse", repo_label="o/elsewhere",
    )
    ids = {
        r["id"] for r in daemon._pending_events_for_agent(
            inbox, own.stem, account_context=ctx, repo_label="o/seat",
        )
    }
    assert tick.stem not in ids


def test_a_strand_never_sees_the_other_repos_chat(tmp_path):
    ctx = _ctx(tmp_path)
    inbox = tmp_path / ".brr" / "inbox"
    own = protocol.create_event(inbox, "spawn", "child", status="processing")
    chat = protocol.create_event(
        inbox, "cloud", "are you there?", repo_label="o/elsewhere",
    )
    ids = {
        r["id"] for r in daemon._pending_events_for_agent(
            inbox, own.stem, strand=True, account_context=ctx, repo_label="o/seat",
        )
    }
    assert chat.stem not in ids


def test_same_repo_chat_is_not_marked_foreign(tmp_path):
    # Positive control for the marker: the ordinary case carries no tag.
    ctx = _ctx(tmp_path)
    inbox = tmp_path / ".brr" / "inbox"
    own = protocol.create_event(inbox, "schedule", "tick", status="processing")
    chat = protocol.create_event(inbox, "cloud", "hi", repo_label="o/seat")
    view = {
        r["id"]: r for r in daemon._pending_events_for_agent(
            inbox, own.stem, account_context=ctx, repo_label="o/seat",
        )
    }
    assert chat.stem in view and "foreign_repo" not in view[chat.stem]


