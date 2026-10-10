"""The live loop follows the self -> wake -> body -> branch -> immune road."""

from pathlib import Path

import pytest

from brr.loom.runtime import speak
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.project import owed
from brr.loom.runtime.selfrepo import _commit, git, init_self

from _step import Loom, notices, wait_until, write_thread


def test_the_self_decides_the_live_bodys_wake(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = Path("home")  # The clone's cwd must not retarget relative port paths.
    write_thread(root, "custom", "Custom wake", "echo-wake", wait="0")
    recipe = root / "self" / "core" / "loom" / "wake"
    recipe.write_text("#!/bin/sh\necho custom\n")
    git(root / "self", "add", "core/loom/wake")
    _commit(root / "self", "The self rewrites its wake.")
    inject_letter(root, to="thread:custom", body="begin")
    sent = []
    monkeypatch.setitem(speak.EFFECTS, "fake", lambda _h, _c, _k, text, _ctx: sent.append(text))
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: sent, 8, loom.dump)
        assert sent == ["custom"]
        assert not owed(loom.facts(), "custom")
    finally:
        loom.halt()
    assert not loom.errors


@pytest.mark.parametrize("sender", ["p-ada", "p-stranger"])
def test_a_live_body_sees_identity_and_lands_a_labeled_commit(tmp_path, sender):
    root = tmp_path / "home"
    init_self(root, person="ada")
    write_thread(root, "live", "Carry the self", "self-commit", wait="0")
    seed = git(root / "self", "rev-parse", "main").stdout.strip()
    inject_letter(root, to="thread:live", body="carry it", sender=sender)
    loom = Loom(root)
    loom.start()
    try:
        if sender == "p-stranger":
            wait_until(lambda: any(f.id.startswith("attention:fuse:") for f in loom.facts()),
                       15, loom.dump)
            assert git(root / "self", "rev-parse", "main").stdout.strip() == seed
            assert not (root / "self" / "memory" / "moves" / "live-body.md").exists()
            assert owed(loom.facts(), "live")
            return
        wait_until(lambda: (root / "self" / "memory" / "moves" / "live-body.md").is_file()
                   and not owed(loom.facts(), "live"), 15, loom.dump)
        facts = loom.facts()
        start = next(f for f in facts if f.kind == "body.started")
        main = git(root / "self", "rev-parse", "main").stdout.strip()
        assert main != seed
        assert (root / "self" / "memory" / "moves" / "live-body.md").read_text() == (
            "# A live body saw its self.\n")
        message = git(root / "self", "log", "-1", "--format=%B").stdout
        assert f"Loom-Strand: {start.data['strand']}" in message
        assert "Loom-Label: taint=0; audience=self" in message
        assert git(root / "self", "log", "-1", "--format=%ae %ce").stdout.strip() == (
            "loom@localhost loom@localhost")
        assert not [f for f in facts if f.kind in {"body.died", "attention"}]
    finally:
        loom.halt()
        assert not loom.errors


def test_a_wake_over_budget_fuses_then_a_new_letter_retries(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "fat", "A fat wake", "answer-fast", wait="0")
    monkeypatch.setenv("LOOM_BUDGET_BYTES", "1")
    original = inject_letter(root, to="thread:fat", body="begin")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: notices(loom.facts()), 8, loom.dump)
        facts = loom.facts()
        assert len([f for f in facts if f.kind == "body.died"]) == 2
        # The notice to inbox names the thread, the cause and the way out.
        (told,) = notices(facts)
        assert told.data["to"] == "thread:inbox"
        assert "wake exited 3: over budget:" in told.data["body"]
        assert "a new letter to thread:fat resets the fuse" in told.data["body"]
        assert not [f for f in facts if f.kind == "body.started"]
        assert owed(facts, "fat")
        monkeypatch.delenv("LOOM_BUDGET_BYTES")
        inject_letter(root, to="thread:fat", body="fixed, retry")
        wait_until(lambda: not owed(loom.facts(), "fat"), 8, loom.dump)
        assert original not in {f.data["id"] for f in owed(loom.facts(), "fat")}
    finally:
        loom.halt()
    assert not loom.errors
