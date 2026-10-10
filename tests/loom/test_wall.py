"""A body that ends on a provider limit waits and starts again. It is not a death."""

import time

from brr.loom.runtime import loom as loom_module
from brr.loom.runtime.adapters import walled
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.loom import _recover
from brr.loom.runtime.project import holder, owed

from _step import Loom, wait_until, write_thread


def _kinds(facts, kind):
    return [fact for fact in facts if fact.kind == kind]


def test_the_limit_lines_providers_print_are_walls():
    assert walled("You've hit your session limit · resets 3:40am (Europe/Paris)")
    assert walled("402 Grok Build usage balance exhausted")
    assert not walled("Traceback (most recent call last):\nRuntimeError: boom")


def test_a_walled_body_starts_again_and_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(loom_module, "WALL_WAIT_S", 0.3)
    root = tmp_path / "home"
    write_thread(root, "tw", "thread tw", "wall-once", wait="3")
    inject_letter(root, to="thread:tw", body="begin")
    loom = Loom(root, tick=0.05)
    loom.start()
    try:
        wait_until(lambda: not owed(loom.facts(), "tw")
                   and len(_kinds(loom.facts(), "body.started")) == 2, 10, loom.dump)
        facts = loom.facts()
        [wall] = _kinds(facts, "body.walled")
        starts = _kinds(facts, "body.started")
        assert [fact.data["strand"] for fact in starts] == [wall.data["strand"]] * 2
        assert not _kinds(facts, "body.died")
        assert not _kinds(facts, "attention")
    finally:
        loom.halt()
        assert not loom.errors


def test_a_walled_body_waits_holding_its_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(loom_module, "WALL_WAIT_S", 60.0)
    root = tmp_path / "home"
    write_thread(root, "tw", "thread tw", "wall-once", wait="3")
    inject_letter(root, to="thread:tw", body="begin")
    loom = Loom(root, tick=0.05)
    loom.start()
    try:
        wait_until(lambda: _kinds(loom.facts(), "body.walled"), 10, loom.dump)
        # A second start would land on a later tick. Give it several.
        time.sleep(0.5)
        facts = loom.facts()
        [wall] = _kinds(facts, "body.walled")
        assert len(_kinds(facts, "body.started")) == 1
        assert not _kinds(facts, "attention")
        assert holder(facts, "tw") == (wall.data["strand"], wall.data["gen"])
        assert owed(facts, "tw")
        assert wall.data["until"] > time.time() + 30
    finally:
        loom.halt()
        assert not loom.errors
    # A restarted loom leaves the walled strand holding: its wait still stands.
    _recover(Home(root))
    assert holder(read_facts(Home(root)), "tw") == (wall.data["strand"], wall.data["gen"])


def test_a_crash_that_mentions_a_limit_earlier_still_fuses(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "tc", "thread tc", "die-citing-limit", wait="3")
    inject_letter(root, to="thread:tc", body="begin")
    loom = Loom(root, tick=0.05)
    loom.start()
    try:
        wait_until(lambda: any(fact.id.startswith("attention:fuse:")
                               for fact in loom.facts()), 10, loom.dump)
        facts = loom.facts()
        assert not _kinds(facts, "body.walled")
        assert len(_kinds(facts, "body.died")) == 2
    finally:
        loom.halt()
        assert not loom.errors
