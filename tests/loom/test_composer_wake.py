"""A body chosen in brnrd.dev's composer is a person's wake word, off a live thread."""

import time

import pytest

from connector_support import drop_once
from brr.loom.runtime.selfrepo import git

from _step import Loom, kill_bodies, mail, wait_until, write_thread
from test_lifecycle import _parked_home


class _Composer:
    """The board's POST /v1/dashboard/loom/letters, as the relay pulls it."""

    def __init__(self, home, cursor=100):
        self.home, self.cursor = home, cursor

    def says(self, event_id, thread, text, body=None, login="ada"):
        reply_to = {"platform": "loom", "thread": thread, "message_id": event_id, "user_id": login}
        if body is not None:
            reply_to["body"] = body
        event = {"event_id": event_id, "body": text, "source": "web", "reply_to": reply_to, "attachments": []}
        class Client:
            def pull(_, cursor):
                return {"cursor": cursor + 1, "events": [event]}
        self.cursor = drop_once(self.home, Client(), self.cursor)
        return f"letter:relay:loom:{login}:{event_id}"


def _home(tmp_path, monkeypatch):
    """The parked brnrd home, its owner enrolled on brnrd.dev, and an idle thread on spare/y."""
    home = _parked_home(tmp_path, monkeypatch)
    (home.root / "self/people/ada/channels.toml").write_text(
        'channels = ["relay:telegram:42", "relay:loom:ada"]\n')
    write_thread(home.root, "second", "Second", "answer-fast", wait="0")
    (home.root / "self/threads/second/seat").write_text("body: spare/y\n")
    git(home.root / "self", "add", "threads")
    git(home.root / "self", "commit", "-m", "Seat second.")
    return home


def _leases(loom, thread):
    return [(f.data["shell"], f.data["core"]) for f in loom.facts()
            if f.kind == "lease" and f.data["thread"] == thread]


def _woken(loom):
    return [d for f in loom.facts() if f.kind == "letter.disposed"
            for d in f.data["dispositions"] if d["id"].endswith("#wake")]


def test_composer_body_moves_a_parked_thread_and_its_text_stays_owed(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    loom = Loom(home.root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 10, loom.dump)
        letter = _Composer(loom.home).says("w1", "inbox", "try the other body", body="spare/x")
        wait_until(lambda: home.starts.is_file(), 10, loom.dump)
        assert _leases(loom, "inbox") == [("claude", "echo"), ("spare", "x")]
        assert _woken(loom) == [{"id": letter + "#wake", "outcome": "done", "reason": "wake: on spare/x",
                                 "thread": "inbox", "body": "spare/x"}]
        assert letter in {str(l.data["id"]) for l in mail(home.root, loom.facts(), "inbox")}
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


@pytest.mark.parametrize("body, login, leased", [
    ("claude/echo", "ada", ("claude", "echo")),   # the owner's choice starts the idle thread
    (None, "ada", ("spare", "y")),                # no choice: the seat
    ("claude/echo", "eve", ("spare", "y")),       # a stranger cannot choose a body
])
def test_composer_body_starts_an_idle_thread_only_for_the_owner(tmp_path, monkeypatch, body, login, leased):
    home = _home(tmp_path, monkeypatch)
    loom = Loom(home.root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 10, loom.dump)
        _Composer(loom.home).says("w1", "second", "start here", body=body, login=login)
        wait_until(lambda: _leases(loom, "second"), 10, loom.dump)
        assert _leases(loom, "second") == [leased]
        assert len(_woken(loom)) == (login == "ada" and body is not None)
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_composer_body_on_a_live_thread_is_ignored(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    loom = Loom(home.root)
    loom.start()
    try:
        composer = _Composer(loom.home)
        composer.says("w1", "second", "start here")
        wait_until(lambda: home.starts.is_file(), 10, loom.dump)
        composer.says("w2", "second", "and now switch", body="claude/echo")
        time.sleep(0.5)
        assert _leases(loom, "second") == [("spare", "y")]
        assert _woken(loom) == []
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_composer_body_the_wake_refuses_gets_its_words_and_the_seat(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    loom = Loom(home.root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 10, loom.dump)
        _Composer(loom.home).says("w1", "second", "start here", body="codex")
        wait_until(lambda: any(m["to"] == "loom:ada" for m in home.sent), 10, loom.dump)
        refused = next(m for m in home.sent if m["to"] == "loom:ada")
        assert "No body 'codex' here; bodies are claude, spare. Nothing was started." in refused["body"]
        assert _leases(loom, "second") == [("spare", "y")]
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors
