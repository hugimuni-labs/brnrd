"""Exact direct-chat commands traverse the router and real body observer."""
import time

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import adapters, body_limits
from brr.loom.runtime.selfrepo import git
from _step import Loom, kill_bodies, wait_until, mail
from test_composer_wake import _home, _Composer, _leases


@pytest.mark.parametrize("text, core", [("wake claude/opus", "opus"), ("reincarnate claude/opus", "opus"), ("/WAKE claude/opus", "opus"), ("wake claude", "")])
def test_idle_exact_command_selects_body_before_routing(tmp_path, monkeypatch, text, core):
    home = _home(tmp_path, monkeypatch)
    loom = Loom(home.root)
    ident = _Composer(loom.home).says("control", "second", text)
    loom.start()
    try:
        wait_until(lambda: _leases(loom, "second"), 10, loom.dump)
        lease = next(f for f in loom.facts() if f.kind == "lease" and f.data["thread"] == "second")
        assert (lease.data["shell"], lease.data["core"], lease.data["body_source"]) == ("claude", core, "wake word")
        assert ident not in {f.id for f in mail(home.root, loom.facts(), "second")}
        assert (home.root / "self/threads/second/seat").read_text() == "body: spare/y\n"
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


@pytest.mark.parametrize("text, target", [("wake claude/opus", ("claude", "opus")), ("reincarnate", ("spare", "y"))])
def test_running_exact_command_replaces_body_and_keeps_original_work(tmp_path, monkeypatch, text, target):
    home = _home(tmp_path, monkeypatch)
    # The real process is signalled through the normal grace/proof path.
    with (home.root / "loom/config.toml").open("a") as stream:
        stream.write("\nstop_grace = 0.05\n")
    loom = Loom(home.root)
    composer = _Composer(loom.home)
    original = composer.says("request", "second", "carry on with this")
    loom.start()
    try:
        wait_until(lambda: home.starts.is_file() and home.starts.read_text(), 10, loom.dump)
        command = composer.says("switch", "second", text)
        wait_until(lambda: len(_leases(loom, "second")) == 2, 10, loom.dump)
        assert _leases(loom, "second") == [("spare", "y"), target]
        owed = {f.id for f in mail(home.root, loom.facts(), "second")}
        assert original in owed and command not in owed
        ended = next(f for f in loom.facts() if f.kind == "attempt.ended" and f.data["thread"] == "second")
        assert ended.data["cause"] == "reincarnated"
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


@pytest.mark.parametrize("text, refused", [("wake missing/core", True), ("wake claude/banana", True),
                                            ("wake claude/", True), ("please wake claude/opus", False)])
def test_invalid_arguments_are_refused_and_prose_reaches_seat(tmp_path, monkeypatch, text, refused):
    home = _home(tmp_path, monkeypatch)
    loom = Loom(home.root)
    ident = _Composer(loom.home).says("command", "second", text)
    loom.start()
    try:
        if refused:
            wait_until(lambda: any(f.id == f"letter.disposed:{ident}" for f in loom.facts()), 10, loom.dump)
            assert _leases(loom, "second") == []
            assert any(f.kind == "letter" and f.id.startswith("loom/wake-refused-") for f in loom.facts())
        else:
            wait_until(lambda: _leases(loom, "second"), 10, loom.dump)
            assert _leases(loom, "second") == [("spare", "y")]
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_resolver_reports_precedence(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home.root / "loom/preferences.toml").write_text('model.default = "spare/default"\nmodel.inbox = "claude/opus"\nmodel.second = "spare/thread"\n')
    assert adapters.resolve_body(Loom(home.root).home, [], "second")[2] == "this thread's seat pin"
    assert adapters.resolve_body(Loom(home.root).home, [], "second", text="") == ("spare", "thread", "preferences model.second")
    assert adapters.resolve_body(Loom(home.root).home, [], "inbox", text="") == ("claude", "opus", "preferences model.inbox")


def test_limit_evidence_is_per_core_and_survives_replay(tmp_path):
    from brr.loom.runtime.home import Home
    lease = Fact(by="loom:test", kind="lease", data={"strand": "s", "gen": 1, "shell": "claude", "core": "fable", "body_source": "seat pin"})
    evidence = body_limits.limit_evidence([lease], "s", 1, "You've reached your Fable limit")
    ended = Fact(by="loom:test", kind="attempt.ended", data={"evidence": evidence})
    assert body_limits.dry_core([ended], "claude", "fable")
    assert not body_limits.dry_core([ended], "claude", "opus")
    assert body_limits.next_core([ended], "claude", "fable") == ("claude", "opus")
    assert body_limits.limit_evidence([lease], "s", 1, "Connection closed mid-response") == {}


@pytest.mark.parametrize("core, successor", [("fable", "opus"), ("echo", None)])
def test_failed_core_with_healthy_shell_gauge_falls_past_pin_or_parks(tmp_path, monkeypatch, core, successor):
    import sys
    from pathlib import Path
    from brr.loom.runtime import usage
    from test_lifecycle import _parked_home
    starts = tmp_path / "core-starts"
    fake = Path(usage.__file__).parent / "bodies/fake/run"
    program = (f"import os, sys\nfrom pathlib import Path\n"
               f"with Path({str(starts)!r}).open('a') as out: out.write(sys.argv[2] + '\\n')\n"
               f"if sys.argv[2] == {core!r}:\n"
               "    print(\"You've reached your Fable limit\", flush=True)\n"
               "    sys.exit(1)\n"
               f"os.execv({str(fake)!r}, [{str(fake)!r}, *sys.argv[1:]])\n")
    home = _parked_home(tmp_path, monkeypatch, run=program, left="100")
    (home.root / "self/threads/inbox/seat").write_text(f"body: claude/{core}\n")
    git(home.root / "self", "add", "threads")
    git(home.root / "self", "commit", "--allow-empty", "-m", "Pin the core under test")
    loom = Loom(home.root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "attempt.ended" and f.data.get("evidence", {}).get("core_limit") for f in loom.facts()), 10, loom.dump)
        if successor:
            wait_until(lambda: not mail(home.root, loom.facts(), "inbox"), 10, loom.dump)
            assert starts.read_text().splitlines() == [core, successor]
            assert any("continuing on claude/opus" in sent["body"] and "wake claude/fable" in sent["body"]
                       and "seat pin" in sent["body"] for sent in home.sent)
            assert _leases(loom, "inbox")[0] == ("claude", core)
            assert _leases(loom, "inbox")[-1] == ("claude", successor)
            move = next(f for f in loom.facts() if f.kind == "body.moved")
            assert move.data["from_core"] == core and move.data["to_core"] == successor
            assert next(f for f in reversed(loom.facts()) if f.kind == "lease").data["body_source"] == "quota recovery"
        else:
            wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 10, loom.dump)
            time.sleep(0.3)
            assert starts.read_text().splitlines() == [core]
            assert mail(home.root, loom.facts(), "inbox")
        assert (home.root / "self/threads/inbox/seat").read_text() == f"body: claude/{core}\n"
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


@pytest.mark.parametrize("message, count", [("Connection closed mid-response", 2), ("Authentication failed", 1)])
def test_transport_retries_but_auth_notifies_immediately(tmp_path, monkeypatch, message, count):
    from test_lifecycle import _parked_home
    starts = tmp_path / "failed-starts"
    program = (f"from pathlib import Path\n"
               f"with Path({str(starts)!r}).open('a') as out: out.write('start\\n')\n"
               f"raise SystemExit({message!r})")
    home = _parked_home(tmp_path, monkeypatch, run=program, left="100")
    loom = Loom(home.root)
    loom.start()
    try:
        wait_until(lambda: any("could not take your letter" in sent["body"] for sent in home.sent), 15, loom.dump)
        assert starts.read_text().splitlines() == ["start"] * count
        notice = next(sent for sent in home.sent if "could not take your letter" in sent["body"])
        assert "claude/echo" in notice["body"] and "seat pin" in notice["body"]
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_fallback_skips_dry_cores_and_expires_only_the_recorded_core(monkeypatch):
    monkeypatch.setattr(body_limits.time, "time", lambda: 100)
    facts = [Fact(by="loom:test", kind="attempt.ended", data={"evidence": {"core_limit": {
        "shell": "claude", "core": core, "until": until}}})
        for core, until in [("fable", 200), ("opus", 300), ("sonnet", 99)]]
    assert body_limits.next_core(facts, "claude", "fable") == ("claude", "sonnet")
    assert not body_limits.dry_core(facts, "claude", "sonnet")
    assert body_limits.dry_core(facts, "claude", "opus")["until"] == 300


def test_strangers_exact_words_reach_the_seat(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    loom = Loom(home.root)
    _Composer(loom.home).says("stranger", "second", "wake claude/opus", login="eve")
    loom.start()
    try:
        wait_until(lambda: _leases(loom, "second"), 10, loom.dump)
        assert _leases(loom, "second") == [("spare", "y")]
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_unknown_reset_backoff_stops_at_known_weekly_reset(tmp_path, monkeypatch):
    from brr.loom.runtime import usage
    from brr.loom.runtime.home import Home
    monkeypatch.setattr(body_limits.time, "time", lambda: 1000.0)
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: {"windows": [
        {"name": "week", "resets_at": "1970-01-01T00:20:00+00:00"}]})
    lease = Fact(by="loom:test", kind="lease", data={"strand": "s", "gen": 1, "shell": "claude", "core": "fable"})
    limit = body_limits.limit_evidence([lease], "s", 1, "You've reached your Fable limit", home=Home(tmp_path))["core_limit"]
    assert limit["until"] == 1200


def test_quiet_running_ticks_do_not_walk_person_letters_or_wake_history(tmp_path, monkeypatch):
    from brr.loom.runtime import loom as runtime
    from brr.loom.runtime.ledger import LedgerView
    facts = LedgerView(Fact(by="loom:test", id=f"old-{i}", kind="speech", hlc=(i + 1, 0, "test"), data={}) for i in range(20000))
    facts.append(Fact(by="person:ada", id="initial-person-input", kind="letter", hlc=(20001, 0, "test"), data={"from": "person:ada", "to": "thread:work"}))
    scans = []
    monkeypatch.setattr(runtime, "_person_dms", lambda *args: scans.append(1) or [])
    for _ in range(100):
        assert runtime._commands(None, facts, "work", ("claude", "fable")) == []
    assert len(scans) == 1
    facts.append(Fact(by="loom:test", id="body-output", kind="letter", hlc=(20002, 0, "test"), data={"from": "s-worker", "to": "thread:work"}))
    assert runtime._commands(None, facts, "work", ("claude", "fable")) == []
    assert len(scans) == 1
    facts.append(Fact(by="person:ada", id="person-input", kind="letter", hlc=(20003, 0, "test"), data={"from": "person:ada", "to": "thread:work"}))
    assert runtime._commands(None, facts, "work", ("claude", "fable")) == []
    assert len(scans) == 2
    assert runtime._wake_choice(facts, "work") is None
    monkeypatch.setattr(runtime, "_uncached_wake_choice", lambda *args: pytest.fail("quiet wake history scan"))
    for _ in range(100):
        assert runtime._wake_choice(facts, "work") is None


def test_resolver_names_automatic_move_as_quota_recovery():
    from brr.loom.runtime.home import Home
    facts = [Fact(by="loom:test", kind="lease", data={"thread": "work"}),
             Fact(by="loom:test", kind="body.moved", data={"thread": "work", "to_shell": "claude", "to_core": "opus"})]
    assert adapters.resolve_body(None, facts, "work") == ("claude", "opus", "quota recovery")


def test_repeated_real_core_failures_share_one_dm_and_increase_hold(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from brr.loom.runtime import usage
    from test_lifecycle import _parked_home
    clock = [time.time()]
    monkeypatch.setattr(body_limits, "time", SimpleNamespace(time=lambda: clock[0]))
    starts = tmp_path / "repeated-core-starts"
    fake = Path(usage.__file__).parent / "bodies/fake/run"
    program = ("import os, sys\nfrom pathlib import Path\n"
               f"with Path({str(starts)!r}).open('a') as out: out.write(sys.argv[2] + '\\n')\n"
               "if sys.argv[2] == 'fable':\n"
               "    print(\"You've reached your Fable limit\", flush=True)\n"
               "    sys.exit(1)\n"
               f"os.execv({str(fake)!r}, [{str(fake)!r}, *sys.argv[1:]])\n")
    home = _parked_home(tmp_path, monkeypatch, run=program, left="100")
    (home.root / "self/threads/inbox/seat").write_text("body: claude/fable\n")
    git(home.root / "self", "add", "threads")
    git(home.root / "self", "commit", "-m", "Pin Fable for repeated-limit proof")
    loom = Loom(home.root)
    loom.start()
    try:
        wait_until(lambda: starts.is_file() and starts.read_text().splitlines() == ["fable", "opus"]
                   and not mail(home.root, loom.facts(), "inbox"), 15, loom.dump)
        first = next(f.data["evidence"]["core_limit"] for f in loom.facts()
                     if f.kind == "attempt.ended" and f.data.get("evidence", {}).get("core_limit"))
        clock[0] = first["until"] + 1
        home.person.says("again", "please answer another question")
        wait_until(lambda: starts.is_file() and starts.read_text().splitlines() == ["fable", "opus", "fable", "opus"]
                   and not mail(home.root, loom.facts(), "inbox"), 15, loom.dump)
        limits = [f.data["evidence"]["core_limit"] for f in loom.facts()
                  if f.kind == "attempt.ended" and f.data.get("evidence", {}).get("core_limit")]
        assert len(limits) == 2
        assert limits[1]["until"] - clock[0] == 1800
        assert limits[1]["spell"] == limits[0]["spell"]
        assert len([sent for sent in home.sent if "continuing on claude/opus" in sent["body"]]) == 1
        assert len([f for f in loom.facts() if f.kind == "body.moved"]) == 2
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors
