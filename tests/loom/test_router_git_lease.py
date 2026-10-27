"""Two homes, one remote: the git inbox lease decides which router is armed,
and the loser of it releases its local lease so speak steps down."""

from __future__ import annotations

import subprocess

from brr.daemon2.facts import Fact
from brr.daemon2.leases import LocalLeaseAuthority
from brr.loom.runtime.config import loom_clock
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.router import Router


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


def test_one_router_is_armed_and_the_other_takes_over_after_expiry(tmp_path):
    remote = tmp_path / "self.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    a_home, b_home = _home(tmp_path, "aaaa", remote), _home(tmp_path, "bbbb", remote)
    a, b = _router(a_home, 100), _router(b_home, 9_000)

    a.begin_tick(read_facts(a_home))
    b.begin_tick(read_facts(b_home))
    assert a.armed() and not b.armed()
    assert a.gate_lease.gen == 1 and b.lease is None

    # a's machine stops renewing (asleep, unplugged); b counts on its own clock.
    a.gate.clock.now += 100
    b.gate.clock.now += 100
    assert not a.armed()
    # The gen-1 router fact would reach b by the ledger transport (P2).
    append(b_home, Fact(kind="router", by="loom:aaaa", id="router:1",
                        data={"install": "aaaa", "gen": 1}))
    b.begin_tick(read_facts(b_home))
    assert b.armed() and b.gate_lease.gen == 2

    facts = a.begin_tick(read_facts(a_home))
    fenced = [f for f in facts if f.kind == "lease.fenced"]
    assert fenced and fenced[0].data["gen"] == 1
    assert a.lease is None and not a.armed()
    # speak authorizes the router lease it was handed: a's old one is dead.
    stale = LocalLeaseAuthority(a_home.root / "ledger/leases", clock=loom_clock)
    row = stale._read(stale._paths("router")[0])
    assert row["until"] == 0


def test_the_first_git_gen_starts_above_the_homes_local_gens(tmp_path):
    remote = tmp_path / "self.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    home = Home(tmp_path / "aaaa", install="aaaa")
    (home.root / "loom").mkdir(parents=True, exist_ok=True)
    local = LocalLeaseAuthority(home.root / "ledger/leases", clock=loom_clock)
    for _ in range(7):
        lease = local.acquire("router", "aaaa", 30)
        local.release(lease)
    append(home, Fact(kind="router", by="loom:aaaa", id="router:7", data={"install": "aaaa", "gen": 7}))
    (home.root / "loom/config.toml").write_text(f'lease_remote = "{remote}"\n')
    router = _router(home, 5)
    router.begin_tick(read_facts(home))
    assert router.gate_lease.gen == 8 and router.lease.gen == 8
    assert router.armed()


def test_without_lease_remote_there_is_no_gate(tmp_path):
    home = Home(tmp_path / "aaaa", install="aaaa")
    assert Router(home).gate is None


def test_the_git_lease_lasts_at_least_90_seconds_under_the_default_router_ttl(tmp_path):
    remote = tmp_path / "self.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    home = Home(tmp_path / "aaaa", install="aaaa")
    (home.root / "loom").mkdir(parents=True, exist_ok=True)
    (home.root / "loom/config.toml").write_text(f'lease_remote = "{remote}"\n')
    router = _router(home, 100)
    assert router.config.router_ttl == 30
    router.begin_tick(read_facts(home))
    assert router.gate.view("inbox")["ttl"] == 90
    # Past the local 30 s, inside the git 90 s: still armed, nothing fenced.
    router.gate.clock.now += 60
    facts = router.begin_tick(read_facts(home))
    assert router.armed()
    assert not [f for f in facts if f.kind == "lease.fenced"]


def test_a_fence_opens_a_lease_streak_and_taking_the_lease_back_closes_it(tmp_path):
    from brr.loom.runtime.health import degraded_line, open_streaks
    remote = tmp_path / "self.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    a_home, b_home = _home(tmp_path, "aaaa", remote), _home(tmp_path, "bbbb", remote)
    a, b = _router(a_home, 100), _router(b_home, 9_000)
    a.begin_tick(read_facts(a_home))
    b.begin_tick(read_facts(b_home))

    a.gate.clock.now += 100
    b.gate.clock.now += 100
    b.begin_tick(read_facts(b_home))
    a.begin_tick(read_facts(a_home))
    [streak] = open_streaks(read_facts(a_home), "aaaa")
    assert streak.data["step"] == "lease"
    assert degraded_line(read_facts(a_home), "aaaa").startswith("degraded: lease since")
    # A second fenced tick does not open a second streak.
    a.begin_tick(read_facts(a_home))
    assert len(open_streaks(read_facts(a_home), "aaaa")) == 1

    # b goes silent in turn; a takes inbox back and the streak closes.
    b.gate.clock.now += 200
    a.gate.clock.now += 200
    a.begin_tick(read_facts(a_home))
    assert a.gate_lease is not None and a.gate_lease.gen == 3
    assert open_streaks(read_facts(a_home), "aaaa") == []
