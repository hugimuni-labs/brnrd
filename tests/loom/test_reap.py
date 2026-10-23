"""The loop reaps what nothing needs, with a fact each, and keeps every room with work only it holds."""

from datetime import datetime, timedelta, timezone

from brr.daemon2.facts import Fact
from brr.loom.runtime import reap
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.selfrepo import _commit, git, init_self, room as clone_room

from _step import Loom, wait_until, write_thread


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _ended(home, thread, strand, days):
    """A strand whose one gen was leased, released ``days`` ago and drained."""
    write_thread(home.root, thread, f"---\nid: {thread}\nstatus: open\ntense: plan\n---\n# {thread}",
                 "quit-now", wait="0")
    append(home, Fact(kind="lease", by="router:0001", id=f"lease:{strand}",
                      data=dict(thread=thread, strand=strand, gen=1)))
    clone_room(home.root, strand)
    append(home, Fact(kind="released", by="loom:0001", id=f"released:{strand}:1", at=_ago(days),
                      data=dict(thread=thread, strand=strand, gen=1, why="done", install="0001")))
    append(home, Fact(kind="port.drained", by="loom:0001", id=f"port.drained:{strand}:1",
                      data=dict(thread=thread, strand=strand, gen=1)))
    return home.room(strand)


def _reaped(home):
    return {f.data["path"]: f for f in read_facts(home) if f.kind == "room.reaped"}


def test_the_loop_reaps_only_what_is_held_elsewhere(tmp_path, monkeypatch):
    home = Home(tmp_path / "home", install="0001")
    init_self(home.root, person="test")
    done = _ended(home, "done", "0001-done", 8)
    young = _ended(home, "young", "0001-young", 1)
    kept = _ended(home, "kept", "0001-kept", 8)
    (kept / "self" / "work.md").write_text("only here")
    git(kept / "self", "add", "work.md")
    _commit(kept / "self", "Work.\n\nLoom-Label: taint=0; audience=self\n")
    old, fresh = kept / "self.broken-20261001T000000Z", kept / "self.broken-20261022T000000Z"
    for aside, days in ((old, 15), (fresh, 1)):
        aside.mkdir()
        append(home, Fact(kind="room.repaired", by="loom:0001", at=_ago(days), data={
            "strand": "0001-kept", "gen": 1, "found": ["x"], "done": ["y"], "aside": str(aside)}))
    dead = kept / ".self-clone-999999-abc"
    dead.mkdir()
    head = git(done / "self", "rev-parse", "strand/0001-done").stdout.strip()
    log = home.root / "loom" / "loom.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * 100)
    monkeypatch.setattr(reap, "LOG_MAX", 50)

    looming = Loom(home.root, tick=0.01)
    looming.start()
    try:
        wait_until(lambda: log.exists() and "reap: " in log.read_text(), 20, looming.dump)
    finally:
        looming.halt()
    assert not looming.errors

    found = _reaped(home)
    assert set(found) == {"rooms/0001-done", f"rooms/0001-kept/{old.name}", f"rooms/0001-kept/{dead.name}"}
    room = found["rooms/0001-done"]
    assert room.id == "room.reaped:rooms/0001-done"
    assert room.data["what"] == "room" and room.data["bytes"] > 0
    assert room.data["heads"]["self"]["strand/0001-done"] == head
    assert not done.exists() and not old.exists() and not dead.exists()
    # Work only this room holds, a young release and a young aside all stay.
    assert (kept / "self" / "work.md").exists() and fresh.exists() and young.exists()
    assert (home.root / "loom" / "loom.log.1").read_text().startswith("x" * 100)
    assert "reap: 1 rooms reaped; kept 1 (leftovers in self), 1 (released less than 7 days ago)" in log.read_text()


def test_a_fact_left_by_a_crash_mid_delete_is_finished(tmp_path):
    home = Home(tmp_path / "home", install="0001")
    init_self(home.root, person="test")
    room = _ended(home, "young", "0001-young", 1)
    append(home, Fact(kind="room.reaped", by="loom:0001", id="room.reaped:rooms/0001-young", data={
        "what": "room", "strand": "0001-young", "path": "rooms/0001-young", "bytes": 1, "heads": {}}))
    reap.Reaper(home, lambda *_: None).reap()
    assert not room.exists()
    assert len(_reaped(home)) == 1


def test_under_the_disk_floor_the_loop_degrades_and_recovers(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    free = [1]
    monkeypatch.setattr(reap, "floor", lambda path: (free[0], 2))
    looming = Loom(root, tick=0.01)
    looming.start()
    try:
        wait_until(lambda: [f for f in read_facts(Home(root)) if f.kind == "loom.degraded"], 10, looming.dump)
        free[0] = 3
        wait_until(lambda: [f for f in read_facts(Home(root)) if f.kind == "loom.recovered"], 10, looming.dump)
    finally:
        looming.halt()
    assert not looming.errors
    [down] = [f for f in read_facts(Home(root)) if f.kind == "loom.degraded"]
    assert down.data["step"] == "disk"
    assert down.data["error"].startswith("DiskFloor: 0 MB free")
