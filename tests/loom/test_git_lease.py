"""Leases as refs on a shared remote: one winner per swap, expiry on each
machine's own clock, and the contested rule for a renew with no network."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr.loom.runtime.gitlease import GitLeaseAuthority, boot_clock


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _bare(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True)
    return path


def _authority(tmp: Path, name: str, remote: Path, clock: Clock, **kw) -> GitLeaseAuthority:
    repo = tmp / name
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    offline = {"down": False}

    def git(tail):
        if offline["down"]:
            return subprocess.CompletedProcess(tail, 128, "", "fatal: unable to access remote")
        return subprocess.run(["git", "-C", str(repo), *tail], capture_output=True, text=True)

    auth = GitLeaseAuthority(repo, str(remote), git, clock=clock, margin=5, **kw)
    auth.offline = offline
    return auth


@pytest.fixture
def pair(tmp_path):
    remote = _bare(tmp_path / "remote.git")
    clock_a, clock_b = Clock(1000), Clock(50_000)  # unrelated clocks: never compared
    a = _authority(tmp_path, "a", remote, clock_a)
    b = _authority(tmp_path, "b", remote, clock_b)
    return a, b, clock_a, clock_b, remote


def _remote_sha(remote: Path, ref: str) -> str:
    return subprocess.run(["git", "-C", str(remote), "rev-parse", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_one_holder_and_the_follower_waits_out_ttl_on_its_own_clock(pair):
    a, b, clock_a, clock_b, remote = pair
    lease = a.acquire("inbox", "aaaa", 90)
    assert lease is not None and lease.gen == 1
    assert a.authorize(lease)
    assert b.acquire("inbox", "bbbb", 90) is None  # b first sees the sha now

    clock_b.now += 90  # ttl, but not the margin
    assert b.acquire("inbox", "bbbb", 90) is None
    clock_b.now += 6
    clock_a.now += 96
    assert not a.authorize(lease)  # a stopped before b could start
    taken = b.acquire("inbox", "bbbb", 90)
    assert taken is not None and taken.gen == 2
    assert a.renew(lease, 90) is None  # the swap names a's old sha and loses
    assert not a.authorize(lease)
    assert _remote_sha(remote, "refs/leases/inbox") == b.held["inbox"][1]


def test_a_renewal_moves_the_sha_and_restarts_the_followers_count(pair):
    a, b, clock_a, clock_b, _remote = pair
    lease = a.acquire("inbox", "aaaa", 90)
    b.acquire("inbox", "bbbb", 90)
    for _ in range(5):
        clock_a.now += 30
        clock_b.now += 30
        lease = a.renew(lease, 90)
        assert lease is not None and lease.gen == 1
        assert b.acquire("inbox", "bbbb", 90) is None
    assert a.authorize(lease)


def test_two_takeovers_of_one_expired_lease_land_exactly_once(pair, tmp_path):
    a, b, clock_a, clock_b, remote = pair
    c = _authority(tmp_path, "c", remote, Clock(7))
    lease = a.acquire("inbox", "aaaa", 90)
    b.poll()
    c.poll()
    a.release(lease)
    b.poll()
    c.poll()
    # Both judge it released and push against the same sha; one wins.
    won = [x.acquire("inbox", h, 90) for x, h in ((b, "bbbb"), (c, "cccc"))]
    assert sum(w is not None for w in won) == 1
    assert [w.gen for w in won if w] == [2]


def test_a_released_lease_is_free_at_once(pair):
    a, b, *_ = pair
    lease = a.acquire("inbox", "aaaa", 90)
    b.poll()
    assert a.release(lease)
    assert not a.authorize(lease)
    taken = b.acquire("inbox", "bbbb", 90)
    assert taken is not None and taken.gen == 2


def test_ledger_rides_the_lease_push(tmp_path):
    remote = _bare(tmp_path / "remote.git")
    holder = {}
    a = _authority(tmp_path, "a", remote, Clock(), riders=lambda: holder)
    tree = subprocess.run(["git", "-C", str(a.repo), "hash-object", "-t", "tree", "/dev/null"],
                          capture_output=True, text=True, check=True).stdout.strip()
    ledger = subprocess.run(["git", "-C", str(a.repo), "-c", "user.name=t", "-c", "user.email=t@t",
                             "commit-tree", tree, "-m", "facts"],
                            capture_output=True, text=True, check=True).stdout.strip()
    holder["refs/ledger/aaaa"] = ledger
    assert a.acquire("inbox", "aaaa", 90) is not None
    assert _remote_sha(remote, "refs/ledger/aaaa") == ledger
    b = _authority(tmp_path, "b", remote, Clock())
    b.poll()
    assert b.view("inbox")["ledger"] == ledger


def test_an_uncontested_holder_keeps_answering_through_an_outage(pair):
    a, _b, clock_a, _clock_b, _remote = pair
    lease = a.acquire("inbox", "aaaa", 90)
    a.offline["down"] = True
    clock_a.now += 30
    assert a.renew(lease, 90) is lease
    clock_a.now += 600
    assert a.authorize(lease)


def test_a_contested_holder_fences_when_it_cannot_renew(tmp_path):
    remote = _bare(tmp_path / "remote.git")
    clock_a, clock_b = Clock(), Clock()
    a = _authority(tmp_path, "a", remote, clock_a)
    peer = {}
    b = _authority(tmp_path, "b", remote, clock_b, riders=lambda: peer)
    lease = a.acquire("inbox", "aaaa", 90)
    # b is alive: its ledger heartbeat lands on the remote.
    tree = subprocess.run(["git", "-C", str(b.repo), "hash-object", "-t", "tree", "/dev/null"],
                          capture_output=True, text=True, check=True).stdout.strip()
    beat = subprocess.run(["git", "-C", str(b.repo), "-c", "user.name=t", "-c", "user.email=t@t",
                           "commit-tree", tree, "-m", "beat"],
                          capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(b.repo), "push", "-q", str(remote), f"{beat}:refs/ledger/bbbb"],
                   check=True)
    a.poll()
    assert a.contested("aaaa")
    a.offline["down"] = True
    clock_a.now += 30
    assert a.renew(lease, 90) is lease  # still inside its own ttl
    clock_a.now += 60
    assert not a.authorize(lease)


def test_strict_fences_even_uncontested(tmp_path):
    remote = _bare(tmp_path / "remote.git")
    clock = Clock()
    a = _authority(tmp_path, "a", remote, clock, strict=True)
    lease = a.acquire("inbox", "aaaa", 90)
    a.offline["down"] = True
    clock.now += 90
    a.renew(lease, 90)
    assert not a.authorize(lease)


def test_boot_clock_moves():
    first = boot_clock()
    assert boot_clock() >= first
