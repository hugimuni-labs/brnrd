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


def test_a_fenced_install_s_seated_body_leaves_and_stays_once_the_lease_is_back(tmp_path, monkeypatch):
    """The fence reaches the body table through ``_observe_body``: a running
    body on the fenced install leaves, and taking inbox back keeps it."""
    from brr.loom.runtime import lifecycle, loom
    from brr.loom.runtime.bodies import decide
    remote = tmp_path / "self.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    a_home, b_home = _home(tmp_path, "aaaa", remote), _home(tmp_path, "bbbb", remote)
    a, b = _router(a_home, 100), _router(b_home, 9_000)
    a.begin_tick(read_facts(a_home))
    b.begin_tick(read_facts(b_home))
    monkeypatch.setattr(loom, "body_alive", lambda room: True)
    monkeypatch.setattr(lifecycle, "read_thread", lambda *a: {"status": "open"})
    strand = "s-aaaa-000001"
    for kind, data in (("lease", {"thread": "work", "install": "aaaa", "router_gen": 1,
                                  "shell": "fake", "core": "echo", "repos": []}),
                       ("body.started", {"pid": 123, "shell": "fake", "core": "echo",
                                         "readme": "", "lstart": "start"})):
        append(a_home, Fact(kind=kind, by="loom:aaaa", id=f"{kind}:{strand}:1",
                            data={"strand": strand, "gen": 1, **data}))

    def observe():
        return loom._observe_body(a_home, read_facts(a_home), strand, "work", 1, {}, force=True)

    o, c = observe()
    assert decide(read_facts(a_home), [o]) == []

    a.gate.clock.now += 100
    b.gate.clock.now += 100
    b.begin_tick(read_facts(b_home))
    a.begin_tick(read_facts(a_home))
    o, c = observe()
    # The thread is open: only the fence can make the body leave.
    assert [x.name for x in decide(read_facts(a_home), [o])] == ["leaving"]
    assert not o.closed and "inbox lease" in c.why
    append(a_home, Fact(kind="body.leaving", by="loom:aaaa", id=f"body.leaving:{strand}:1",
                        data={"strand": strand, "gen": 1, "deadline": o.now + 60}))

    b.gate.clock.now += 200
    a.gate.clock.now += 200
    a.begin_tick(read_facts(a_home))
    o, c = observe()
    assert [x.name for x in decide(read_facts(a_home), [o])] == ["stayed"]


def test_a_prefer_hands_inbox_to_that_install_and_the_old_holder_s_bodies_leave(tmp_path, monkeypatch):
    """``machines prefer bbbb`` written on b reaches a on the ledger; a steps
    down and releases inbox to b, b takes it at once, and a does not take it
    back. a's seated body leaves with no degraded streak."""
    from brr.loom.runtime import lifecycle, loom
    from brr.loom.runtime.__main__ import main
    from brr.loom.runtime.bodies import decide
    from brr.loom.runtime.health import open_streaks
    remote = tmp_path / "self.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    a_home, b_home = _home(tmp_path, "aaaa", remote), _home(tmp_path, "bbbb", remote)
    a, b = _router(a_home, 100), _router(b_home, 9_000)
    a.begin_tick(read_facts(a_home))
    b.begin_tick(read_facts(b_home))
    assert a.armed() and not b.armed()
    monkeypatch.setattr(loom, "body_alive", lambda room: True)
    monkeypatch.setattr(lifecycle, "read_thread", lambda *a: {"status": "open"})
    strand = "s-aaaa-000001"
    for kind, data in (("lease", {"thread": "work", "install": "aaaa", "router_gen": 1,
                                  "shell": "fake", "core": "echo", "repos": []}),
                       ("body.started", {"pid": 123, "shell": "fake", "core": "echo",
                                         "readme": "", "lstart": "start"})):
        append(a_home, Fact(kind=kind, by="loom:aaaa", id=f"{kind}:{strand}:1",
                            data={"strand": strand, "gen": 1, **data}))

    def observe():
        o, _c = loom._observe_body(a_home, read_facts(a_home), strand, "work", 1, {}, force=True)
        return [x.name for x in decide(read_facts(a_home), [o])]

    assert observe() == []

    assert main(["machines", "--home", str(b_home.root), "--install", "bbbb", "prefer", "bbbb"]) == 0
    # The shuttle carries b's folder to a within a heartbeat.
    b.shuttle._beat = None
    b.shuttle.sync(holding=False)
    a.shuttle._beat = None
    a.shuttle.sync(holding=True)
    a._gate_at = None
    facts = a.begin_tick(read_facts(a_home))
    [handed] = [f for f in facts if f.kind == "lease.handed"]
    assert handed.data["to"] == "bbbb" and handed.data["gen"] == 1
    assert a.gate_lease is None and not a.armed()
    assert open_streaks(read_facts(a_home), "aaaa") == []
    assert observe() == ["leaving"]

    # No clock moved: b, the preferred install, takes it at once.
    b._gate_at = None
    b.begin_tick(read_facts(b_home))
    assert b.gate_lease is not None and b.gate_lease.gen == 2

    a._gate_at = None
    a.begin_tick(read_facts(a_home))
    assert a.gate_lease is None
    # A second tick does not hand again or fence.
    facts = read_facts(a_home)
    assert len([f for f in facts if f.kind == "lease.handed"]) == 1
    assert not [f for f in facts if f.kind == "lease.fenced"]


def test_prefer_refuses_an_install_whose_ledger_this_home_has_never_seen(tmp_path):
    """A typo would hand inbox to nobody, so the verb writes nothing for an
    install with no ``ledger/facts/<install>/`` here, and accepts it once the
    shuttle has brought that folder."""
    from brr.loom.runtime.__main__ import main
    a_home = _home(tmp_path, "aaaa", tmp_path / "self.git")
    argv = ["machines", "--home", str(a_home.root), "--install", "aaaa", "prefer", "bbbb"]
    assert main(argv) == 2
    assert not [f for f in read_facts(a_home) if f.kind == "machines.prefer"]
    (a_home.root / "ledger" / "facts" / "bbbb").mkdir(parents=True)
    assert main(argv) == 0
    assert [f.data for f in read_facts(a_home) if f.kind == "machines.prefer"] == [
        {"install": "bbbb", "from": "aaaa"}]
