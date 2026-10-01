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

import types
from pathlib import Path

from brr import account, cli, daemon, hooks, protocol
from brr import config as conf


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


def test_the_letter_row_names_the_other_checkout():
    row = hooks._event_header(
        {"id": "evt-1-abcd", "source": "cloud", "foreign_repo": "o/elsewhere"},
        size=12,
    )
    assert "for o/elsewhere — not this checkout" in row
    plain = hooks._event_header({"id": "evt-1-abcd", "source": "cloud"}, size=12)
    assert "not this checkout" not in plain


def _account_add(tmp_path, monkeypatch, *, default: bool):
    calls = {}
    ctx = types.SimpleNamespace(
        kind="account",
        dominion_repo=tmp_path / "home",
        home_root=tmp_path / "home",
        default_repo=types.SimpleNamespace(label="o/main"),
    )
    monkeypatch.setattr(conf, "load_config", lambda *_a, **_k: {})
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_repo_root_from_arg", lambda _r: tmp_path / "sib")
    monkeypatch.setattr(account, "resolve_context", lambda *_a, **_k: ctx)
    monkeypatch.setattr(account, "repo_label", lambda *_a, **_k: "o/sibling")
    monkeypatch.setattr(
        account, "register_repo",
        lambda _ctx, _root, **kw: calls.update(kw),
    )
    cli.cmd_add(types.SimpleNamespace(repo="sib", default=default))
    return calls


def test_account_add_leaves_the_default_where_it_was(tmp_path, monkeypatch, capsys):
    calls = _account_add(tmp_path, monkeypatch, default=False)
    assert calls["make_default"] is False
    assert "account default unchanged: o/main" in capsys.readouterr().out


def test_account_add_default_moves_it_only_when_asked(tmp_path, monkeypatch, capsys):
    calls = _account_add(tmp_path, monkeypatch, default=True)
    assert calls["make_default"] is True
    assert "o/sibling is now the account default" in capsys.readouterr().out


def test_account_add_parser_carries_the_default_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["account", "add", "x", "--default"])
    assert args.default is True
    assert parser.parse_args(["account", "add", "x"]).default is False
