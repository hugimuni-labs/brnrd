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


def _routing(monkeypatch, threads):
    """Route's slow edges (main snapshot, seat, clone/start) cut out; the
    grant decision and the lease fact it writes are the real ones."""
    from brr.loom.runtime import adapters, lifecycle, loom, repos, router as router_mod
    started = []
    monkeypatch.setattr(router_mod, "_routable", lambda home, facts: list(threads))
    monkeypatch.setattr(router_mod, "main_snapshot", lambda home: {})
    monkeypatch.setattr(router_mod, "letters_for", lambda facts, snap, thread: [
        Fact(kind="letter", by="person:operator", id=f"letter:{thread}", data={"to": f"thread:{thread}"})])
    monkeypatch.setattr(router_mod, "readme_for", lambda home, thread: None)
    monkeypatch.setattr(router_mod, "heir_of", lambda *a: None)
    monkeypatch.setattr(router_mod.Router, "_unrouted", lambda self, facts, known: facts)
    monkeypatch.setattr(lifecycle, "read_thread", lambda *a: {"status": "open"})
    monkeypatch.setattr(loom, "_closures", lambda home, facts, threads: facts)
    monkeypatch.setattr(loom, "_person_dms", lambda *a: [])
    monkeypatch.setattr(loom, "_wake_choice", lambda *a: None)
    monkeypatch.setattr(adapters, "seat_text", lambda home, thread: "")
    monkeypatch.setattr(adapters, "at_birth", lambda home, thread, default, text: ("fake", "echo"))
    monkeypatch.setattr(adapters, "repos", lambda text, home=None: [])
    monkeypatch.setattr(repos, "require_body_author", lambda home: None)
    monkeypatch.setattr(loom, "_policy", lambda home, thread: {})

    def start(home, strand, thread, gen, bodies, facts, choice, **kw):
        started.append((home.install_id(), thread))
        return facts
    monkeypatch.setattr(loom, "_start", start)
    return started


def _carry(*routers):
    """One shuttle round: each pushes its folder (the holder's rides a renew),
    then each fetches the others'."""
    for r in routers + routers:
        if r.gate_lease is not None:
            r._gate_at = None
            r.begin_tick(read_facts(r.home))
        r.shuttle._beat = None
        r.shuttle.sync(holding=r.gate_lease is not None)


def test_a_pinned_thread_is_granted_only_by_its_install_whoever_holds_inbox(tmp_path, monkeypatch):
    """``machines pin deck bbbb`` written on a reaches b on the ledger. a, the
    inbox holder, grants ``work`` and never ``deck``; b, which holds no
    lease, grants ``deck``, and that grant holds the thread in both ledgers."""
    from brr.loom.runtime.__main__ import main
    from brr.loom.runtime.project import fold
    remote = tmp_path / "self.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    a_home, b_home = _home(tmp_path, "aaaa", remote), _home(tmp_path, "bbbb", remote)
    a, b = _router(a_home, 100), _router(b_home, 9_000)
    a.begin_tick(read_facts(a_home))
    b.begin_tick(read_facts(b_home))
    assert a.armed() and not b.armed()
    _carry(a, b)
    started = _routing(monkeypatch, ["deck", "work"])
    (a_home.root / "ledger" / "facts" / "bbbb").mkdir(parents=True, exist_ok=True)

    assert main(["machines", "--home", str(a_home.root), "--install", "aaaa", "pin", "deck", "bbbb"]) == 0
    _carry(a, b)
    a.begin_tick(read_facts(a_home))
    a.route(read_facts(a_home), {}, ("fake", "echo"))
    # A router gen b has not seen yet: b's grant is behind it in the union,
    # and still holds the thread, because its own install wrote it.
    append(a_home, Fact(kind="router", by="loom:aaaa", id="router:7",
                        data={"install": "aaaa", "gen": 7, "until": 0}))
    b.route(read_facts(b_home), {}, ("fake", "echo"))
    assert sorted(started) == [("aaaa", "work"), ("bbbb", "deck")]

    _carry(a, b)
    for home in (a_home, b_home):
        state = fold(read_facts(home))
        assert state.holder["deck"][0].startswith("s-bbbb-")
        assert state.holder["work"][0].startswith("s-aaaa-")

    # Unpinned, deck follows inbox again: b grants nothing more.
    assert main(["machines", "--home", str(b_home.root), "--install", "bbbb", "unpin", "deck"]) == 0
    started.clear()
    b.route(read_facts(b_home), {}, ("fake", "echo"))
    assert started == []


def test_a_body_seated_away_from_its_pinned_install_leaves_and_a_park_there_ends(tmp_path, monkeypatch):
    """Pinning a thread to another machine moves it: the body running here
    leaves, a parked one ends ``moved``, and a body on a thread pinned here
    does not leave when this install is fenced."""
    from brr.loom.runtime import lifecycle, loom
    from brr.loom.runtime.__main__ import main
    from brr.loom.runtime.bodies import TABLE, decide, row_for
    remote = tmp_path / "self.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    a_home, b_home = _home(tmp_path, "aaaa", remote), _home(tmp_path, "bbbb", remote)
    a, b = _router(a_home, 100), _router(b_home, 9_000)
    a.begin_tick(read_facts(a_home))
    b.begin_tick(read_facts(b_home))
    _carry(a, b)
    monkeypatch.setattr(loom, "body_alive", lambda room: True)
    monkeypatch.setattr(lifecycle, "read_thread", lambda *a: {"status": "open"})
    strand = "s-aaaa-000001"
    for kind, data in (("lease", {"thread": "deck", "install": "aaaa", "router_gen": 1,
                                  "shell": "fake", "core": "echo", "repos": []}),
                       ("body.started", {"pid": 123, "shell": "fake", "core": "echo",
                                         "readme": "", "lstart": "start"})):
        append(a_home, Fact(kind=kind, by="loom:aaaa", id=f"{kind}:{strand}:1",
                            data={"strand": strand, "gen": 1, **data}))

    def observe():
        o, c = loom._observe_body(a_home, read_facts(a_home), strand, "deck", 1, {}, force=True)
        return o, c, [x.name for x in decide(read_facts(a_home), [o])]

    assert observe()[2] == []
    assert main(["machines", "--home", str(b_home.root), "--install", "bbbb", "pin", "deck", "bbbb"]) == 0
    _carry(a, b)
    o, c, names = observe()
    assert names == ["leaving"] and "pinned to install bbbb" in c.why

    monkeypatch.setattr(loom, "body_alive", lambda room: False)
    append(a_home, Fact(kind="body.parked", by="loom:aaaa", id=f"body.parked:{strand}:1",
                        data={"strand": strand, "gen": 1, "shell": "fake", "core": "echo",
                              "window": "w", "left_pct": 0, "until": 0}))
    o, c, names = observe()
    assert TABLE[row_for(read_facts(a_home), o)].observed == "pinned away"

    # Pinned back here, the body stays through a fence.
    assert main(["machines", "--home", str(a_home.root), "--install", "aaaa", "pin", "deck", "aaaa"]) == 0
    monkeypatch.setattr(loom, "body_alive", lambda room: True)
    monkeypatch.setattr(loom, "_fenced", lambda home, facts: True)
    o, c, names = observe()
    assert not o.fenced and not o.away


def test_pin_refuses_inbox_and_an_unseen_install(tmp_path):
    from brr.loom.runtime.__main__ import main
    a_home = _home(tmp_path, "aaaa", tmp_path / "self.git")
    base = ["machines", "--home", str(a_home.root), "--install", "aaaa"]
    assert main(base + ["pin", "inbox", "aaaa"]) == 2
    assert main(base + ["pin", "deck", "bbbb"]) == 2
    assert not [f for f in read_facts(a_home) if f.kind == "machines.pin"]
    assert main(base + ["pin", "deck", "aaaa"]) == 0
    assert main(base + ["unpin", "deck"]) == 0
    assert [f.data for f in read_facts(a_home) if f.kind == "machines.pin"] == [
        {"thread": "deck", "install": "aaaa", "from": "aaaa"},
        {"thread": "deck", "install": None, "from": "aaaa"}]
