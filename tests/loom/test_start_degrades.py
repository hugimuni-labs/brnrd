"""A home a crash or a busy thread left awkward still wakes its resident.

The resident is who repairs its home, and it can only do that awake.
"""

import json

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.loom import _graft, _prepare
from brr.loom.runtime.owners import letter_owners
from brr.loom.runtime.project import sender_threads
from brr.loom.runtime.selfrepo import git, room

from _step import Loom, kill_bodies, mail, wait_until, write_thread


def test_a_successor_starts_from_its_parents_head_when_the_strand_branch_is_gone(tmp_path):
    # 2026-10-23: an OOM killed a room between clone and `checkout -b`, its body
    # worked on main, and every successor failed to fetch strand/<parent>.
    root = tmp_path / "home"
    write_thread(root, "inbox", "Inbox", "quit-now")
    parent = room(root, "s-aaaa-parent")
    git(parent, "checkout", "-q", "main")
    git(parent, "branch", "-D", "strand/s-aaaa-parent")
    (parent / "memory/kept.md").write_text("the parent's work\n")
    git(parent, "add", "memory")
    git(parent, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "parent work")
    child = room(root, "s-aaaa-child")

    _graft(Home(root), child, "s-aaaa-child", "s-aaaa-parent")

    assert (git(child, "rev-parse", "HEAD").stdout == git(parent, "rev-parse", "HEAD").stdout)
    assert git(child, "branch", "--show-current").stdout.strip() == "strand/s-aaaa-child"


def test_owed_letters_that_do_not_fit_the_wake_arrive_at_the_first_boundary(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "inbox", "Inbox", "quit-now")
    home = Home(root, install="aaaa")
    empty = _prepare(home, "s-aaaa-empty", "inbox", 1, [], {}, read_facts(home), "hand")
    base = len((empty / "port/wake.md").read_bytes())
    ids = [inject_letter(root, to="thread:inbox", body=f"{n} " + "x" * 3000) for n in range(3)]
    facts = read_facts(home)
    letters = mail(root, facts, "inbox")
    monkeypatch.setenv("LOOM_BUDGET_BYTES", str(base + 4500))

    prepared = _prepare(home, "s-aaaa-full", "inbox", 2, letters, sender_threads(facts), facts, "hand")

    carried = [row["id"] for row in json.loads((prepared / "port/owed.json").read_text())]
    assert 1 <= len(carried) < 3 and carried == ids[:len(carried)]
    left = ids[len(carried):]
    assert all(ident in (prepared / "port/loom.md").read_text() for ident in left)
    boundary = (prepared / "port/in/boundary.md").read_text()
    assert all(ident in boundary for ident in ids)


def test_a_broken_wake_recipe_wakes_the_resident_with_the_packaged_one(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "work", "The ask", "answer-fast", wait="0")
    clone = root / "self"
    (clone / "core/loom/wake").write_text("#!/bin/sh\necho broken recipe >&2\nexit 2\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "A recipe that cannot wake")
    ident = inject_letter(root, to="thread:work", body="Please answer")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: letter_owners(loom.facts(), main_snapshot(loom.home))[ident].done,
                   15, loom.dump)
        start = next(f for f in loom.facts() if f.kind == "body.started")
        told = (loom.home.room(start.data["strand"]) / "port/wake.md").read_text()
        assert "core/loom/wake failed (wake exited 2: broken recipe)" in told
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors
