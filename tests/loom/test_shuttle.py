"""Two homes, one remote: each install's ledger folder travels on
refs/ledger/<install>, and a router that takes the inbox lease over grants
nothing until it holds the previous holder's ledger at the sha its lease
carried."""

from __future__ import annotations

import shutil
import subprocess

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.router import Router
from brr.loom.runtime.shuttle import segments


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _home(tmp_path, install, remote):
    home = Home(tmp_path / install, install=install)
    (home.root / "loom").mkdir(parents=True, exist_ok=True)
    (home.root / "loom/config.toml").write_text(f'lease_remote = "{remote}"\nrouter_ttl = 90\n')
    return home


def _router(home, now):
    router = Router(home)
    router.gate.clock = Clock(now)
    return router


def _remote(tmp_path):
    remote = tmp_path / "self.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    return remote


def _note(home, n):
    return append(home, Fact(kind="note", by="loom:" + home.install_id(),
                             id=f"note:{home.install_id()}:{n}", data={"n": n}))


def test_a_sealed_segment_keeps_its_cut_when_the_file_grows():
    lines = b"".join(b"%05d\n" % n for n in range(100))
    before = segments(lines, 64)
    after = segments(lines + b"x" * 10 + b"\n", 64)
    assert b"".join(before) == lines
    assert after[:-1] == before[:-1] and all(len(s) >= 64 for s in before[:-1])


def test_a_peers_folder_arrives_and_then_grows_in_place(tmp_path):
    remote = _remote(tmp_path)
    a_home, b_home = _home(tmp_path, "aaaa", remote), _home(tmp_path, "bbbb", remote)
    a, b = _router(a_home, 100), _router(b_home, 100)
    _note(a_home, 1)
    a.begin_tick(read_facts(a_home))
    b.begin_tick(read_facts(b_home))
    assert "note:aaaa:1" in {f.id for f in read_facts(b_home)}

    folder = b_home.root / "ledger/facts/aaaa"
    inodes = {p.name: p.stat().st_ino for p in folder.glob("*.jsonl")}
    _note(a_home, 2)
    a.gate.clock.now += 60
    a.begin_tick(read_facts(a_home))
    b.gate.clock.now += 60
    b.begin_tick(read_facts(b_home))
    assert "note:aaaa:2" in {f.id for f in read_facts(b_home)}
    assert {p.name: p.stat().st_ino for p in folder.glob("*.jsonl")} == inodes
    # Nothing of b's travels into its own folder from the remote.
    assert not (a_home.root / "ledger/facts/aaaa/bbbb").exists()


def test_a_takeover_grants_only_with_the_previous_holders_ledger(tmp_path):
    remote = _remote(tmp_path)
    a_home, b_home = _home(tmp_path, "aaaa", remote), _home(tmp_path, "bbbb", remote)
    a, b = _router(a_home, 100), _router(b_home, 9_000)
    a.begin_tick(read_facts(a_home))
    b.begin_tick(read_facts(b_home))
    assert a.armed() and not b.armed()

    a.gate.clock.now += 31
    a.begin_tick(read_facts(a_home))
    b.gate.clock.now += 60
    b.begin_tick(read_facts(b_home))
    # b has a's gen-1 router fact from that sync, the hour-old view
    # an earlier router fact let through. Now b stops fetching ledgers.
    assert "router:1" in {f.id for f in read_facts(b_home)}
    sync = b.shuttle.sync
    b.shuttle.sync = lambda **_: []

    # a writes, renews once more (its ledger rides the renewal), then dies.
    _note(a_home, 7)
    a.gate.clock.now += 31
    a.begin_tick(read_facts(a_home))
    b.gate.clock.now += 31
    b.begin_tick(read_facts(b_home))  # sees the renewal, counts from here
    b.gate.clock.now += 200

    # b takes the lease but does not grant.
    b.begin_tick(read_facts(b_home))
    assert b.gate_lease is not None and b.gate_lease.gen == 2
    assert not b.armed()

    b.shuttle.sync = sync
    b.gate.clock.now += 31
    b.begin_tick(read_facts(b_home))
    assert b.armed()
    assert "note:aaaa:7" in {f.id for f in read_facts(b_home)}


def test_a_wiped_gate_repo_still_renews(tmp_path):
    remote = _remote(tmp_path)
    home = _home(tmp_path, "aaaa", remote)
    a = _router(home, 100)
    _note(home, 1)
    a.begin_tick(read_facts(home))
    assert a.armed()

    shutil.rmtree(home.root / "ledger/git")
    a = _router(home, 100)
    _note(home, 2)
    a.begin_tick(read_facts(home))
    assert a.armed()
    a.gate.clock.now += 31
    a.begin_tick(read_facts(home))
    assert a.armed() and a.gate_lease is not None
