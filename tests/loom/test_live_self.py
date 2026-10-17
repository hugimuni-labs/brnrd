from _step import mail
"""The live loop follows the self -> wake -> body -> branch -> immune road."""

from pathlib import Path

import pytest

from connector_support import install_fake
from brr.loom.runtime.ledger import inject_letter

from brr.loom.runtime.selfrepo import _commit, git, init_self

from _step import Loom, notices, wait_until, write_thread
from brr.loom.runtime.owners import letter_owners
from brr.loom.runtime.lifecycle import main_snapshot


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
    install_fake(monkeypatch, lambda text: sent.append(text))
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: sent, 8, loom.dump)
        assert sent == ["custom"]
        assert not mail(root, loom.facts(), "custom")
    finally:
        loom.halt()
    assert not loom.errors


@pytest.mark.parametrize("sender", ["p-ada", "p-stranger"])
def test_a_live_body_sees_identity_and_lands_a_labeled_commit(tmp_path, sender):
    root = tmp_path / "home"
    init_self(root, person="ada")
    write_thread(root, "live", "Carry the self", "self-commit", wait="0")
    seed = git(root / "self", "rev-parse", "main").stdout.strip()
    ident = inject_letter(root, to="thread:live", body="carry it", sender=sender)
    loom = Loom(root)
    loom.start()
    try:
        if sender == "p-stranger":
            wait_until(lambda: letter_owners(loom.facts(), main_snapshot(loom.home))[ident].owner == "operator",
                       15, loom.dump)
            assert git(root / "self", "rev-parse", "main").stdout.strip() == seed
            assert not (root / "self" / "memory" / "moves" / "live-body.md").exists()
            assert not mail(root, loom.facts(), "live")
            return
        wait_until(lambda: (root / "self" / "memory" / "moves" / "live-body.md").is_file()
                   and not mail(root, loom.facts(), "live"), 15, loom.dump)
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
        assert not [f for f in facts if f.kind == "attempt.ended" and f.data["cause"] == "failed"]
    finally:
        loom.halt()
        assert not loom.errors


def test_a_home_wake_over_budget_keeps_original_owed_and_recovers_without_resend(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "fat", "A fat wake", "answer-fast", wait="0")
    monkeypatch.setenv("LOOM_BUDGET_BYTES", "1")
    original = inject_letter(root, to="thread:fat", body="begin")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: len([f for f in loom.facts() if f.kind == "attempt.ended"
                               and f.data["thread"] == "fat"]) >= 1, 8, loom.dump)
        facts = loom.facts()
        fate = letter_owners(facts, main_snapshot(loom.home))[original]
        assert not fate.done and fate.reason == "home-caused failure"
        assert not [f for f in facts if f.kind == "body.started"]
        assert original in {f.id for f in mail(root, facts, "fat")}
        notices = [f for f in facts if f.id.startswith("loom/self-refusal-")]
        assert len(notices) == 1 and notices[0].data["to"] == "thread:inbox"
        monkeypatch.delenv("LOOM_BUDGET_BYTES")
        # A repair outside self recovers at the ten-minute fallback.
        from datetime import datetime, timedelta
        from brr.loom.runtime import router
        class AfterFallback(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(seconds=601)
        monkeypatch.setattr(router, "datetime", AfterFallback)
        wait_until(lambda: letter_owners(loom.facts(), main_snapshot(loom.home))[original].done,
                   8, loom.dump)
        assert any(f.kind == "note" and f.data.get("re") == original for f in loom.facts())
    finally:
        loom.halt()
    assert not loom.errors
