"""A room a crash left broken is repaired or rebuilt before a body wakes in it."""

import os
import time
from pathlib import Path

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import selfrepo
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _prepare
from brr.loom.runtime.port import parse_boundary
from brr.loom.runtime.project import sender_threads
from brr.loom.runtime.selfrepo import git, room

from _step import write_thread

STRAND = "s-aaaa-heal"


@pytest.fixture
def home(tmp_path):
    root = tmp_path / "home with spaces"
    write_thread(root, "brnrd", "Brnrd", "quit-now")
    return root


def tracked(clone: Path) -> list[str]:
    return git(clone, "ls-files").stdout.split()


def repairs(root: Path) -> list[Fact]:
    return [f for f in read_facts(Home(root)) if f.kind == "room.repaired"]


def commit_in_room(clone: Path) -> str:
    (clone / "memory" / "kept.md").parent.mkdir(parents=True, exist_ok=True)
    (clone / "memory" / "kept.md").write_text("strand work\n")
    git(clone, "add", "memory")
    git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "strand work")
    return git(clone, "rev-parse", "HEAD").stdout.strip()


def test_an_emptied_index_and_missing_files_are_healed(home):
    clone = room(home, STRAND)
    head = tracked(clone)
    assert len(head) > 5
    sha = git(clone, "rev-parse", "HEAD").stdout.strip()
    (clone / ".git" / "index").unlink()
    gone = head[:4]
    for path in gone:
        (clone / path).unlink()
    (clone / head[4]).write_text("")  # the OOM crash also truncated one
    edited = head[5]
    (clone / edited).write_text("a real edit\n")
    (clone / "notes.md").write_text("untracked\n")

    assert room(home, STRAND) == clone

    assert tracked(clone) == head
    for path in gone:
        assert (clone / path).read_bytes() == git(clone, "show", f"HEAD:{path}").stdout.encode()
    assert (clone / head[4]).stat().st_size > 0
    assert (clone / edited).read_text() == "a real edit\n"
    assert git(clone, "status", "--porcelain").stdout.split() == ["M", edited, "??", "notes.md"]
    [fact] = repairs(home)
    assert fact.data["strand"] == STRAND
    assert any("empty index" in why for why in fact.data["found"])
    assert any("4 tracked files missing" in why for why in fact.data["found"])
    assert any("1 tracked files empty" in why for why in fact.data["found"])
    assert "restored 5 files from HEAD" in fact.data["done"]
    assert fact.data["aside"] is None
    # The crash site, read before the repair: the deletions and truncation too.
    assert fact.data["head"] == sha
    assert sorted(fact.data["dirty"]) == sorted(
        [f" D {p}" for p in gone] + [f" M {head[4]}", f" M {edited}", "?? notes.md"])
    assert fact.data["dirty_count"] == 7


def test_a_room_with_real_local_work_is_left_alone(home):
    clone = room(home, STRAND)
    sha = commit_in_room(clone)
    some = tracked(clone)[0]
    (clone / some).write_text("edited, not committed\n")
    (clone / "untracked.md").write_text("new\n")
    git(clone, "add", "untracked.md")
    before = git(clone, "status", "--porcelain").stdout

    assert room(home, STRAND) == clone

    assert git(clone, "rev-parse", "HEAD").stdout.strip() == sha
    assert git(clone, "status", "--porcelain").stdout == before
    assert (clone / some).read_text() == "edited, not committed\n"
    assert repairs(home) == []


def test_a_dead_index_lock_is_cleared_and_a_live_one_is_not(home):
    clone = room(home, STRAND)
    lock = clone / ".git" / "index.lock"
    lock.write_text("")
    room(home, STRAND)
    assert lock.exists(), "a lock younger than any loom git call may have a holder"
    assert repairs(home) == []

    old = time.time() - 600
    os.utime(lock, (old, old))
    room(home, STRAND)
    assert not lock.exists()
    git(clone, "add", "-A")  # the index is writable again
    [fact] = repairs(home)
    assert any("stale index.lock" in why for why in fact.data["found"])


def test_a_half_created_dest_is_rebuilt_and_kept_aside(home):
    dest = home / "rooms" / STRAND / "self"
    (dest / "core").mkdir(parents=True)
    (dest / "core" / "partial").write_text("half a checkout\n")

    clone = room(home, STRAND)

    assert clone == dest.resolve()
    assert git(clone, "branch", "--show-current").stdout.strip() == f"strand/{STRAND}"
    assert tracked(clone) == git(home / "self", "ls-files").stdout.split()
    [aside] = (home / "rooms" / STRAND).glob("self.broken-*")
    assert (aside / "core" / "partial").read_text() == "half a checkout\n"
    [fact] = repairs(home)
    assert fact.data["aside"] == str(aside)


def test_a_room_whose_head_broke_is_recloned_with_its_commits(home):
    clone = room(home, STRAND)
    sha = commit_in_room(clone)
    (clone / "scratch.md").write_text("work the crash interrupted\n")
    (clone / ".git" / "HEAD").write_text("")  # git no longer calls this a repository

    assert room(home, STRAND) == clone

    assert git(clone, "rev-parse", "HEAD").stdout.strip() == sha
    assert git(clone, "branch", "--show-current").stdout.strip() == f"strand/{STRAND}"
    assert (clone / "memory" / "kept.md").read_text() == "strand work\n"
    [aside] = (home / "rooms" / STRAND).glob("self.broken-*")
    assert (aside / ".git" / "HEAD").read_text() == ""  # read, never written
    [fact] = repairs(home)
    assert any("kept strand/" in done for done in fact.data["done"])
    assert fact.data["head"] == sha
    assert fact.data["dirty"] == ["?? scratch.md"]


def test_a_clone_that_dies_midway_leaves_no_room(home, monkeypatch):
    real = selfrepo.git

    def dying(cwd, *args, **kw):
        if args[:2] == ("checkout", "-b"):
            raise selfrepo.SelfError("killed")
        return real(cwd, *args, **kw)

    monkeypatch.setattr(selfrepo, "git", dying)
    with pytest.raises(selfrepo.SelfError):
        room(home, STRAND)
    monkeypatch.undo()
    parent = home / "rooms" / STRAND
    assert not (parent / "self").exists()
    assert [p.name for p in parent.iterdir()] == []

    clone = room(home, STRAND)
    assert git(clone, "branch", "--show-current").stdout.strip() == f"strand/{STRAND}"
    assert repairs(home) == []


def test_the_waking_body_sees_what_was_repaired(home):
    loom = Home(home, install="aaaa")
    append(loom, Fact(kind="lease", by="loom:aaaa", id="lease:brnrd:1", data={
        "thread": "brnrd", "strand": STRAND, "gen": 1}))
    clone = room(home, STRAND)
    sha = git(clone, "rev-parse", "HEAD").stdout.strip()
    (clone / ".git" / "index").unlink()
    (clone / "half-written.md").write_text("x\n")
    facts = read_facts(loom)

    prepared = _prepare(loom, STRAND, "brnrd", 1, [], sender_threads(facts), facts, "hand")

    boundary = (prepared / "port/in/boundary.md").read_text()
    [line] = [l for l in boundary.splitlines() if l.startswith("repaired:")]
    assert "empty index" in line and "rebuilt the index from HEAD" in line
    assert f"crash site: strand/{STRAND} was at {sha[:12]}; 1 dirty paths (half-written.md)" in line
    parse_boundary(boundary)  # the jack still reads it
    [fact] = repairs(home)
    assert fact.data["gen"] == 1


def test_a_parent_room_without_its_branch_does_not_block_the_wake(home):
    loom = Home(home, install="aaaa")
    parent = "s-aaaa-parent"
    shell = home / "rooms" / parent / "self"
    git(home, "clone", "-q", "--shared", str(home / "self"), str(shell))  # never got strand/<parent>
    append(loom, Fact(kind="lease", by="loom:aaaa", id="lease:brnrd:1", data={
        "thread": "brnrd", "strand": STRAND, "gen": 1, "parent": parent}))
    facts = read_facts(loom)

    prepared = _prepare(loom, STRAND, "brnrd", 1, [], sender_threads(facts), facts, "hand")

    clone = prepared / "self"
    assert git(clone, "rev-parse", "HEAD").stdout == git(home / "self", "rev-parse", "HEAD").stdout
    assert git(clone, "branch", "--show-current").stdout.strip() == f"strand/{STRAND}"
    assert (prepared / "port/in/boundary.md").is_file()
    [fact] = [f for f in read_facts(loom) if f.kind == "room.unseeded"]
    assert fact.data["parent"] == parent and fact.data["room"] == str(shell.resolve())
    assert "couldn't find remote ref" in fact.data["error"]
