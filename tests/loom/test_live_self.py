"""The live loop follows the self -> wake -> body -> branch -> immune road."""

import time
from pathlib import Path

from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.project import owed
from brr.loom.runtime.selfrepo import _commit, git, init_self

from _step import Loom, wait_until, write_thread


def test_the_self_decides_the_live_bodys_wake(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = Path("home")  # The clone's cwd must not retarget relative port paths.
    write_thread(root, "custom", "Custom wake", "hold", wait="0")
    recipe = root / "self" / "core" / "loom" / "wake"
    recipe.write_text("#!/bin/sh\necho custom\n")
    git(root / "self", "add", "core/loom/wake")
    _commit(root / "self", "The self rewrites its wake.")
    inject_letter(root, to="thread:custom", body="begin")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "body.started" for f in loom.facts()), 8, loom.dump)
        start = next(f for f in loom.facts() if f.kind == "body.started")
        room = root / "rooms" / start.data["strand"]
        wait_until(lambda: (room / "port" / "wake-seen").exists(), 8, loom.dump)
        assert (room / "port" / "wake.md").read_text() == "custom\n"
        assert (room / "port" / "wake-seen").read_text() == "custom\n"
        assert git(room / "self", "branch", "--show-current").stdout.strip() == (
            "strand/" + start.data["strand"])
    finally:
        loom.halt()
    assert not loom.errors


def test_a_live_body_sees_identity_and_lands_a_labeled_commit(tmp_path):
    root = tmp_path / "home"
    init_self(root, person="ada")
    write_thread(root, "live", "Carry the self", "self-commit", wait="0")
    seed = git(root / "self", "rev-parse", "main").stdout.strip()
    inject_letter(root, to="thread:live", body="carry it", sender="p-ada")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: (root / "self" / "memory" / "moves" / "live-body.md").is_file()
                   and not owed(loom.facts(), "live"), 15, loom.dump)
        facts = loom.facts()
        start = next(f for f in facts if f.kind == "body.started")
        room = root / "rooms" / start.data["strand"]
        wake = (room / "port" / "wake.md").read_text()
        assert (room / "self" / "core" / "identity.md").read_text() in wake
        assert (room / "self" / "memory" / "stance.md").read_text() in wake
        assert "## Tree" in wake and "carry it" in wake
        main = git(root / "self", "rev-parse", "main").stdout.strip()
        assert main != seed
        assert git(room / "self", "rev-parse", "HEAD").stdout.strip() == main
        message = git(root / "self", "log", "-1", "--format=%B").stdout
        assert f"Loom-Strand: {start.data['strand']}" in message
        assert "Loom-Label: taint=0; audience=self" in message
        assert git(root / "self", "log", "-1", "--format=%ae %ce").stdout.strip() == (
            "loom@localhost loom@localhost")
        assert not [f for f in facts if f.kind in {"body.died", "attention"}]
        assert (room / ".body").exists()
        assert not (room / "self" / "port").exists()
    finally:
        loom.halt()
    assert not loom.errors


def test_a_wake_over_budget_refuses_one_start_without_retry(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "fat", "A fat wake", "hold", wait="0")
    monkeypatch.setenv("LOOM_BUDGET_BYTES", "1")
    inject_letter(root, to="thread:fat", body="begin")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: any(f.id.startswith("attention:wake:") for f in loom.facts()),
                   8, loom.dump)
        time.sleep(0.2)  # Several further ticks cannot retry the held start.
        facts = loom.facts()
        rows = [f for f in facts if f.kind == "attention"]
        assert len(rows) == 1
        assert "wake exited 3: over budget:" in rows[0].data["why"]
        assert len([f for f in facts if f.kind == "lease"]) == 1
        assert not [f for f in facts if f.kind.startswith("body.")]
        assert owed(facts, "fat")
        assert not list((root / "rooms").glob("*/port/wake.md"))
    finally:
        loom.halt()
    assert not loom.errors
