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


def _text(path):
    try:  # the pass may rotate it between two reads
        return path.read_text()
    except FileNotFoundError:
        return ""


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
    loose = _ended(home, "loose", "0001-loose", 8)
    (loose / "pr2-body.md").write_text("a draft only here")
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
        wait_until(lambda: "reap: " in _text(log), 20, looming.dump)
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
    # Work only this room holds, a loose file at a root, a young release and a young aside all stay.
    assert (kept / "self" / "work.md").exists() and fresh.exists() and young.exists()
    assert (loose / "pr2-body.md").exists()
    assert (home.root / "loom" / "loom.log.1").read_text().startswith("x" * 100)
    assert "reap: 1 rooms reaped; kept 1 (leftovers in self), 1 (loose files), 1 (released less than 7 days ago)" in log.read_text()


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


def _jack(room, event, thread):
    import json, os, re, subprocess, sys
    env = dict(os.environ, BRNRD_THREAD=thread, BRNRD_ROOM=str(room))
    for name in ("BRNRD_BODY_JACK", "BRNRD_BODY_SHELL"):
        env.pop(name, None)
    out = subprocess.run([sys.executable, "-m", "brr.loom.runtime", "jack", "--event", event, "--room", str(room)],
                         input="{}", text=True, capture_output=True, env=env, timeout=60).stdout
    return json.loads(out) if out.strip() else {}


def test_a_stop_with_work_only_its_room_holds_is_blocked_and_says_how_to_clear_it(tmp_path):
    import os, re, subprocess
    home = Home(tmp_path / "home", install="0001")
    init_self(home.root, person="test")
    write_thread(home.root, "live", "# live", "quit-now", wait="0")
    append(home, Fact(kind="lease", by="router:0001", id="lease:0001-live",
                      data=dict(thread="live", strand="0001-live", gen=1)))
    room = clone_room(home.root, "0001-live").parent
    for sub in ("in", "out"):
        (room / "port" / sub).mkdir(parents=True, exist_ok=True)
    (room / "port" / "wait").write_text("0\n")
    from brr.loom.runtime.port import render_boundary
    (room / "port" / "in" / "boundary.md").write_text(render_boundary("0001-live", 1, "live", [], set(), {}))
    clean = _jack(room, "stop", "live")
    assert "left in room" not in clean.get("reason", "")

    (room / "pr2-body.md").write_text("a draft only here")
    (room / "self" / "work.md").write_text("not committed")
    _jack(room, "post", "live")
    held = _jack(room, "stop", "live")
    assert held["decision"] == "block"
    reason = held["reason"]
    assert reason.startswith("left in room: ")
    assert "pr2-body.md: a loose file" in reason and "self: 1 paths not committed (work.md)" in reason
    # Each item names the one command that clears it; run them as printed.
    for command in re.findall(r"or discard (?:it|them): `([^`]+)`", reason):
        ran = subprocess.run(command, shell=True, cwd=room, text=True, capture_output=True,
                             env=dict(os.environ, BRNRD_ROOM=str(room), BRNRD_THREAD="live"))
        assert ran.returncode == 0, ran.stderr
    assert not (room / "pr2-body.md").exists() and not (room / "self" / "work.md").exists()
    _jack(room, "post", "live")
    assert "left in room" not in _jack(room, "stop", "live").get("reason", "")
    [gone] = [f for f in read_facts(home) if f.kind == "room.discarded"]
    assert gone.data["path"] == "pr2-body.md" and gone.data["why"] == "<why>"


def test_rooms_kept_for_a_body_reach_inbox_once_their_thread_at_wake_and_force_clears_one(tmp_path):
    import json, os, subprocess, sys
    home = Home(tmp_path / "home", install="0001")
    init_self(home.root, person="test")
    write_thread(home.root, "brnrd", "Brnrd", "quit-now", wait="0")
    loose = _ended(home, "loose", "0001-loose", 8)
    (loose / "pr2-body.md").write_text("a draft only here")
    _ended(home, "young", "0001-young", 1)
    reaper = reap.Reaper(home, lambda *_: None)
    reaper.reap()
    reaper.reap()
    notices = [f for f in read_facts(home) if f.kind == "letter" and f.id.startswith("loom/rooms-kept-")]
    assert len(notices) == 1
    assert notices[0].data["to"] == f"thread:{home.main_thread()}"
    assert "- 0001-loose (thread loose): loose files: pr2-body.md" in notices[0].data["body"]
    assert "reap <strand> --force" in notices[0].data["body"]
    assert "loose files" in reap.kept_line(home, "loose", {"0001-loose": "loose"})
    assert reap.kept_line(home, "young", {"0001-young": "young"}) == ""

    def verb(*args):
        return subprocess.run([sys.executable, "-m", "brr.loom.runtime", "reap", *args, "--home", str(home.root)],
                              text=True, capture_output=True, timeout=60)
    assert verb("0001-loose").stdout.startswith("kept: loose files")
    assert verb("0001-loose", "--force").returncode == 2  # no --why
    done = verb("0001-loose", "--force", "--why", "the draft is in the PR body")
    assert done.returncode == 0, done.stderr
    assert not loose.exists()
    assert _reaped(home)["rooms/0001-loose"].data["forced"] == "the draft is in the PR body"
    reaper.reap()
    assert json.loads((home.root / reap.KEPT).read_text()) == {"kept": {}, "told": []}


def _project(tmp_path, home, strands):
    """Strands of one thread whose seat declares ``widget``; each released ``days`` ago, oldest lease first."""
    from brr.loom.runtime.repos import prepare_repos
    from test_project_repos import _source
    source, _ = _source(tmp_path)
    write_thread(home.root, "w", "---\nid: w\nstatus: open\ntense: plan\n---\n# w", "quit-now", wait="0")
    (home.thread_dir("w") / "seat").write_text(f"repo: {source}\n")
    git(home.root / "self", "add", "threads")
    _commit(home.root / "self", "Seat.\n\nLoom-Label: taint=0; audience=self\n")
    for strand, days in strands:
        append(home, Fact(kind="lease", by="router:0001", id=f"lease:{strand}",
                          data=dict(thread="w", strand=strand, gen=1)))
        clone_room(home.root, strand)
        prepare_repos(home, strand, "w", [("widget", str(source))], None)
        (home.room(strand) / "port" / "in").mkdir(parents=True)
        if days is not None:
            append(home, Fact(kind="released", by="loom:0001", id=f"released:{strand}:1", at=_ago(days),
                              data=dict(thread="w", strand=strand, gen=1, why="done", install="0001")))
            append(home, Fact(kind="port.drained", by="loom:0001", id=f"port.drained:{strand}:1",
                              data=dict(thread="w", strand=strand, gen=1)))
    return source


def test_a_dead_strands_clean_clone_goes_and_self_and_port_stay(tmp_path):
    home = Home(tmp_path / "home", install="0001")
    init_self(home.root, person="test")
    _project(tmp_path, home, [("0001-old", 3), ("0001-dirty", 3), ("0001-young", 1),
                              ("0001-open", None), ("0001-latest", 3)])
    (home.room("0001-dirty") / "widget" / "draft.md").write_text("untracked, only here")
    lines = []
    reaper = reap.Reaper(home, lambda _home, line: lines.append(line))
    reaper.reap()

    old = home.room("0001-old")
    assert not (old / "widget").exists()
    assert (old / "self" / ".git").exists() and (old / "port").is_dir()
    [fact] = [f for f in read_facts(home) if f.kind == "room.reaped"]
    assert fact.id == "room.reaped:rooms/0001-old/widget"
    assert fact.data["what"] == "clone" and fact.data["strand"] == "0001-old" and fact.data["bytes"] > 0
    assert "main" in fact.data["heads"]["."]
    for kept in ("0001-dirty", "0001-young", "0001-open", "0001-latest"):
        assert (home.room(kept) / "widget" / ".git").exists(), kept
    [summary] = [line for line in lines if "clones reaped" in line]
    assert summary.endswith("kept 1 (gen 1 is not released), 1 (it is its thread's latest strand),"
                            " 1 (leftovers in widget), 1 (released less than 48 h ago)")
    # Untracked work is work: the room goes to inbox as one notice, under the same rooms-kept key.
    [notice] = [f for f in read_facts(home) if f.kind == "letter" and "rooms are kept" in str(f.data)]
    assert "0001-dirty" in str(notice.data) and "draft.md" not in str(notice.data)

    # A second pass converges: nothing more goes, nothing is told twice.
    reaper.reap()
    assert len([f for f in read_facts(home) if f.kind == "room.reaped"]) == 1


def test_a_link_into_a_persons_folder_is_never_a_clone_or_loose(tmp_path):
    home = Home(tmp_path / "home", install="0001")
    init_self(home.root, person="test")
    _project(tmp_path, home, [("0001-old", 3), ("0001-latest", 3)])
    kb = tmp_path / "kb"
    kb.mkdir()
    git(kb, "init", "-q")
    (kb / "notes.md").write_text("his, untracked, nowhere else")
    room = home.room("0001-old")
    (room / "lib").mkdir()
    (room / "lib" / "kb").symlink_to(kb)
    (room / "kb").symlink_to(kb)
    assert reap.clones(room) == [room / "widget"]
    assert reap.loose(room) == []
    assert set(reap.heads(room)) == {"self", "widget"}

    lines = []
    reap.Reaper(home, lambda _home, line: lines.append(line)).reap()
    assert not (room / "widget").exists()
    assert list(_reaped(home)) == ["rooms/0001-old/widget"]
    assert (kb / ".git").is_dir() and (kb / "notes.md").read_text() == "his, untracked, nowhere else"
    assert (room / "lib" / "kb").is_symlink() and (room / "kb").is_symlink()
    assert not [f for f in read_facts(home) if f.kind == "letter" and "rooms are kept" in str(f.data)]


def test_a_clone_fact_left_by_a_crash_mid_delete_is_finished(tmp_path):
    home = Home(tmp_path / "home", install="0001")
    init_self(home.root, person="test")
    _project(tmp_path, home, [("0001-young", 1)])
    clone = home.room("0001-young") / "widget"
    (clone / "only-here.md").write_text("the fact came first; the delete was cut short")
    append(home, Fact(kind="room.reaped", by="loom:0001", id="room.reaped:rooms/0001-young/widget", data={
        "what": "clone", "strand": "0001-young", "path": "rooms/0001-young/widget", "bytes": 1, "heads": {}}))
    reap.Reaper(home, lambda *_: None).reap()
    assert not clone.exists() and (home.room("0001-young") / "self" / ".git").exists()
    assert len(_reaped(home)) == 1


def test_a_process_in_the_room_keeps_its_clone(tmp_path, monkeypatch):
    home = Home(tmp_path / "home", install="0001")
    init_self(home.root, person="test")
    _project(tmp_path, home, [("0001-old", 3), ("0001-latest", 3)])
    monkeypatch.setattr(reap, "busy", lambda rooms: {"0001-old"})
    reap.Reaper(home, lambda *_: None).reap()
    assert (home.room("0001-old") / "widget" / ".git").exists()
    assert not _reaped(home)


def test_a_settled_threads_room_goes_at_release_and_a_reopen_wakes_it_into_a_fresh_one(tmp_path):
    home = Home(tmp_path / "home", install="0001")
    init_self(home.root, person="test")
    done = _ended(home, "done", "0001-done", 0)
    young = _ended(home, "young", "0001-young", 0)
    readme = home.thread_dir("done") / "README.md"
    readme.write_text(readme.read_text().replace("status: open", "status: settled"))
    git(home.root / "self", "add", "threads")
    _commit(home.root / "self", "Settle.\n\nLoom-Label: taint=0; audience=self\n")
    reaper = reap.Reaper(home, lambda *_: None)
    reaper.reap()
    # Settled: no age, and its latest strand is no reason. Open: the 7 days still hold.
    assert not done.exists() and young.exists()

    # A wish reopens it; the next wake leases the same strand into a fresh room at that path.
    append(home, Fact(kind="lease", by="router:0001", id="lease:0001-done:2",
                      data=dict(thread="done", strand="0001-done", gen=2)))
    clone_room(home.root, "0001-done")
    reaper.reap()
    assert (done / "self" / ".git").exists()
