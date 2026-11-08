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


def test_a_handover_yields_to_the_preferred_install_until_ttl(tmp_path):
    remote = _bare(tmp_path / "remote.git")
    clock_a, clock_b, clock_c = Clock(1000), Clock(50_000), Clock(7)
    a = _authority(tmp_path, "a", remote, clock_a)
    b = _authority(tmp_path, "b", remote, clock_b)
    c = _authority(tmp_path, "c", remote, clock_c)
    lease = a.acquire("inbox", "aaaa", 90)
    assert a.release(lease, preferred="cccc", ttl=90)
    # Neither the releaser nor a bystander takes it while cccc may.
    assert a.acquire("inbox", "aaaa", 90) is None
    assert b.acquire("inbox", "bbbb", 90) is None
    clock_b.now += 60
    assert b.acquire("inbox", "bbbb", 90) is None
    # cccc never came: past ttl on b's own clock, anyone takes it.
    clock_b.now += 40
    taken = b.acquire("inbox", "bbbb", 90)
    assert taken is not None and taken.gen == 2


def test_the_preferred_install_takes_a_handover_at_once(pair):
    a, b, *_ = pair
    lease = a.acquire("inbox", "aaaa", 90)
    assert a.release(lease, preferred="bbbb", ttl=90)
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


def _flaky_push(auth: GitLeaseAuthority, answer):
    """The next push runs, then ``answer(proc)`` says what the caller hears."""
    real = auth.git
    state = {"armed": True}

    def git(tail):
        proc = real(tail)
        if state["armed"] and tail and tail[0] == "push":
            state["armed"] = False
            return answer(proc)
        return proc

    auth.git = git


@pytest.mark.parametrize("ttl", [90, 180])
def test_a_renew_whose_answer_was_lost_does_not_fence_the_next_one(pair, ttl):
    # 11-06, 13:57Z: a renew landed but its answer did not arrive; the next
    # renew named the older sha, lost the swap to its own commit and fenced
    # the only machine there was. 90 s was live that day; 180 s is the floor.
    a, _b, clock_a, _clock_b, remote = pair
    lease = a.acquire("inbox", "aaaa", ttl)
    _flaky_push(a, lambda proc: subprocess.CompletedProcess(proc.args, 128, "",
                                                            "fatal: the remote hung up"))
    clock_a.now += ttl / 3
    lease = a.renew(lease, ttl)  # landed; heard as no network, so graced
    assert lease is not None
    clock_a.now += ttl / 3
    renewed = a.renew(lease, ttl)
    assert renewed is not None and renewed.gen == 1
    assert a.authorize(renewed)
    assert _remote_sha(remote, "refs/leases/inbox") == a.held["inbox"][1]


@pytest.mark.parametrize("ttl", [90, 180])
def test_a_renew_refused_once_by_the_remote_retries_on_the_ref_it_reads(pair, ttl):
    a, _b, clock_a, _clock_b, remote = pair
    lease = a.acquire("inbox", "aaaa", ttl)
    before = _remote_sha(remote, "refs/leases/inbox")
    _flaky_push(a, lambda proc: subprocess.CompletedProcess(
        proc.args, 1, "!\trefs/leases/inbox\t[remote rejected] (failed to update ref)\n", ""))
    clock_a.now += ttl / 3
    renewed = a.renew(lease, ttl)
    assert renewed is not None and a.authorize(renewed)
    assert _remote_sha(remote, "refs/leases/inbox") not in ("", before)


def test_a_renew_still_loses_to_a_takeover(pair):
    a, b, clock_a, clock_b, _remote = pair
    lease = a.acquire("inbox", "aaaa", 90)
    b.poll()
    clock_b.now += 96
    assert b.acquire("inbox", "bbbb", 90) is not None
    assert a.renew(lease, 90) is None


@pytest.mark.parametrize("ttl", [90, 180])
def test_a_renew_refused_every_time_keeps_the_lease_when_the_ref_still_names_us(pair, ttl):
    # 11-06: every refused write fenced the only machine. A loss is now only
    # what the ref shows; a rejection alone never fences.
    a, _b, clock_a, _clock_b, remote = pair
    lease = a.acquire("inbox", "aaaa", ttl)
    before = _remote_sha(remote, "refs/leases/inbox")
    real = a.git

    def git(tail):
        if tail and tail[0] == "push":
            return subprocess.CompletedProcess(
                tail, 1, "!\trefs/leases/inbox\t[remote rejected] (failed to update ref)\n", "")
        return real(tail)

    a.git = git
    clock_a.now += ttl / 3
    renewed = a.renew(lease, ttl)
    assert renewed is not None and a.authorize(renewed)
    assert _remote_sha(remote, "refs/leases/inbox") == before == a.held["inbox"][1]


def test_a_renew_fences_when_the_ref_reads_released(pair):
    a, _b, *_ = pair
    lease = a.acquire("inbox", "aaaa", 90)
    held = a.held["inbox"]
    assert a.release(lease, preferred="bbbb", ttl=90)
    a.held["inbox"] = held
    assert a.renew(lease, 90) is None
    assert not a.authorize(lease)


def test_persistent_priority_survives_absence_return_and_restart(pair, tmp_path):
    from brr.loom.runtime.router import GATE_MIN_TTL, GATE_MARGIN, GATE_POLL
    a, b, ca, cb, remote = pair
    a.strict = b.strict = True
    ttl = GATE_MIN_TTL
    first = a.acquire("inbox", "aaaa", ttl)
    assert a.set_priority("inbox", "aaaa")
    # Preferred up: the other cannot displace it, even over several renewals.
    for _ in range(3):
        ca.now += ttl / 3
        cb.now += ttl / 3
        first = a.renew(first, ttl)
        assert first and b.acquire("inbox", "bbbb", ttl) is None
    b.poll()
    ca.now += ttl + GATE_MARGIN + GATE_POLL
    cb.now += ttl + GATE_MARGIN + GATE_POLL
    assert not a.authorize(first)
    fallback = b.acquire("inbox", "bbbb", ttl)
    assert fallback and fallback.gen > first.gen
    # Preferred absent: the fallback keeps renewing, preserving the preference.
    for _ in range(3):
        cb.now += ttl / 3
        fallback = b.renew(fallback, ttl)
        assert fallback and b.authorize(fallback)
        assert b.view("inbox")["priority"] == "aaaa"
    # A fresh process needs no private config copy to know the preference.
    returning = _authority(tmp_path, "returning", remote, ca, strict=True)
    assert returning.acquire("inbox", "aaaa", ttl) is None
    assert returning.view("inbox")["request"] == "aaaa"
    cb.now += ttl / 3
    assert b.renew(fallback, ttl) is None
    assert not b.authorize(fallback)
    returned = returning.acquire("inbox", "aaaa", ttl)
    assert returned and returned.gen > fallback.gen
    assert returning.authorize(returned)
    assert b.acquire("inbox", "bbbb", ttl) is None
    assert returning.view("inbox")["priority"] == "aaaa"


def test_observing_a_fresh_holder_fences_even_inside_old_validity(pair):
    a, b, ca, cb, _ = pair
    old = a.acquire("inbox", "aaaa", 180)
    # Replay a stale process that still believes its old deadline is live.
    assert a.release(old)
    new = b.acquire("inbox", "bbbb", 180)
    assert new
    a.held["inbox"] = (old, "stale", ca.now + 180)
    a.poll()
    assert not a.authorize(old)


def test_request_racing_a_renewal_is_not_erased(pair):
    a, b, *_ = pair
    old = a.acquire("inbox", "aaaa", 180)
    assert a.set_priority("inbox", "bbbb")
    original = a._push
    raced = []

    def push(what, body, expected):
        if not raced:
            raced.append(True)
            assert b.acquire("inbox", "bbbb", 180) is None
        return original(what, body, expected)

    a._push = push
    assert a.renew(old, 180) is None
    assert not a.authorize(old)
    assert b.acquire("inbox", "bbbb", 180) is not None
