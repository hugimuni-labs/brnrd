"""The loom follows main: a green tip fast-forwards the checkout at an idle tick and re-execs;
red waits; dirty and diverged are refused as facts; seated bodies hear the move once."""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import loom, update
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.port import PortError, parse_boundary, render_boundary
from brr.loom.runtime.since import since_gone

from _step import Loom, wait_until, write_thread

STRAND = "s-ab12-aaaaaa"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


class Forge:
    def __init__(self, verdict):
        self.verdict_of, self.asked = verdict, []

    def verdict(self, url, sha):
        self.asked.append(sha)
        return self.verdict_of


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A home, an origin whose main is one merge ahead, and the checkout the loom runs from."""
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    origin, source, upstream = tmp_path / "origin.git", tmp_path / "source", tmp_path / "upstream"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(upstream))
    (upstream / "loom.py").write_text("v1\n")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-qm", "v1")
    _git(upstream, "push", "-q", "origin", "HEAD:main")
    _git(tmp_path, "clone", "-q", str(origin), str(source))
    _git(upstream, "switch", "-qc", "loom/feature")
    (upstream / "feature.py").write_text("new\n")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-qm", "feature")
    _git(upstream, "switch", "-q", "main")
    _git(upstream, "merge", "-q", "--no-ff", "-m", "Merge pull request #7 from o/loom/feature",
         "loom/feature")
    _git(upstream, "push", "-q", "origin", "main")
    monkeypatch.setenv("BRNRD_LOOM_SOURCE", str(source))
    monkeypatch.setattr(update, "EVERY", 0)
    monkeypatch.setattr(update, "LOOK_EVERY", 0)
    execs = []
    monkeypatch.setattr(os, "execv", lambda *args: execs.append(args))

    class World:
        pass

    w = World()
    w.root, w.source, w.upstream, w.execs = root, source, upstream, execs
    w.old, w.tip = _git(source, "rev-parse", "HEAD"), _git(upstream, "rev-parse", "HEAD")
    w.forge = Forge("green")
    monkeypatch.setattr(update, "forge_for", lambda config: w.forge)
    return w


def _degraded(root):
    return [f for f in read_facts(Home(root)) if f.kind == "loom.degraded" and f.data["step"] == "update"]


def _run(root, until, timeout=10):
    looming = Loom(root, tick=0.01)
    looming.start()
    try:
        wait_until(until, timeout, looming.dump)
    finally:
        looming.halt()
    assert not looming.errors


def test_a_green_tip_fast_forwards_re_execs_and_the_next_start_says_what_loaded(world):
    _run(world.root, lambda: world.execs)
    assert _git(world.source, "rev-parse", "HEAD") == world.tip
    assert not _degraded(world.root)
    # The re-exec'd loom is the next start: it names the move and the merge it loaded.
    _run(world.root, lambda: [f for f in read_facts(Home(world.root)) if f.kind == "loom.moved"])
    [moved] = [f for f in read_facts(Home(world.root)) if f.kind == "loom.moved"]
    assert (moved.data["from"], moved.data["to"], moved.data["merges"]) == (world.old, world.tip, ["#7"])
    assert moved.id == f"loom.moved:{moved.data['started']}"
    assert update.moved_text(moved) == f"loom moved {world.old[:8]} → {world.tip[:8]}, loaded #7"


def test_a_red_tip_waits_without_a_fact(world):
    world.forge.verdict_of = "red"
    _run(world.root, lambda: len(world.forge.asked) >= 3)
    assert _git(world.source, "rev-parse", "HEAD") == world.old
    assert not world.execs and not _degraded(world.root)


def test_a_dirty_checkout_is_refused_and_left_as_it_was(world):
    (world.source / "loom.py").write_text("my edit\n")
    _run(world.root, lambda: _degraded(world.root))
    [down] = _degraded(world.root)
    assert "1 uncommitted change ( M loom.py)" in down.data["error"].replace("(M", "( M")
    assert "not touched" in down.data["error"]
    assert _git(world.source, "rev-parse", "HEAD") == world.old
    assert (world.source / "loom.py").read_text() == "my edit\n"
    assert not world.execs


def test_a_diverged_checkout_is_refused_without_asking_the_forge(world):
    (world.source / "mine.py").write_text("local\n")
    _git(world.source, "add", "-A")
    _git(world.source, "commit", "-qm", "local only")
    mine = _git(world.source, "rev-parse", "HEAD")
    _run(world.root, lambda: _degraded(world.root))
    [down] = _degraded(world.root)
    assert f"at {mine[:8]} has diverged from main {world.tip[:8]}" in down.data["error"]
    assert _git(world.source, "rev-parse", "HEAD") == mine
    assert not world.execs and not world.forge.asked


def test_the_move_and_the_re_exec_wait_for_an_idle_tick(world, monkeypatch):
    busy = [True]
    monkeypatch.setattr(loom, "_idle", lambda home: not busy[0])
    looming = Loom(world.root, tick=0.01)
    looming.start()
    try:
        wait_until(lambda: len(world.forge.asked) >= 3, 10, looming.dump)
        assert _git(world.source, "rev-parse", "HEAD") == world.old and not world.execs
        busy[0] = False
        wait_until(lambda: world.execs, 10, looming.dump)
    finally:
        looming.halt()
    assert _git(world.source, "rev-parse", "HEAD") == world.tip


def test_an_open_tool_call_of_a_live_body_is_not_idle(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    home = Home(root)
    strand = f"s-{home.install_id()}-aaaaaa"
    room = home.room(strand)
    (room / "port").mkdir(parents=True)

    class Folded:
        holder = {"brnrd": (strand, 1)}

    monkeypatch.setattr(loom, "fold", lambda facts: Folded)
    monkeypatch.setattr(loom, "body_alive", lambda r: True)
    assert loom._idle(home)
    from brr.loom.runtime import tooltrace
    call = '{"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "ls"}}'
    tooltrace.note(room, call, "pre")
    assert not loom._idle(home)
    tooltrace.note(room, call, "post")
    assert loom._idle(home)


def _at(minutes):
    return (datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def test_a_seated_body_hears_the_move_once_and_a_new_body_reads_it_in_its_wake(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    home = Home(root)
    install = home.install_id()
    append(home, Fact(kind="body.started", by=f"loom:{install}", at=_at(0), data={
        "strand": STRAND, "gen": 1, "pid": 1, "shell": "fake", "core": "x", "readme": "", "lstart": ""}))
    append(home, Fact(kind="loom.moved", by=f"loom:{install}", at=_at(5), id="loom.moved:x", data={
        "install": install, "from": "a" * 40, "to": "b" * 40, "merges": ["#7"], "started": "x"}))
    line = update.moved_line(read_facts(home), STRAND, 1, install)
    assert line == "moved: loom moved aaaaaaaa → bbbbbbbb, loaded #7"
    # A body started after the move has no boundary line: its wake carries it.
    assert update.moved_line(read_facts(home), STRAND, 2, install) == ""
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    text = render_boundary(STRAND, 1, "brnrd", [], set(), {}, moved=line)
    parse_boundary(text)
    (room / "port/in/boundary.md").write_text(render_boundary(STRAND, 1, "brnrd", [], set(), {}))
    execute("start", room, "{}")
    (room / "port/in/boundary.md").write_text(text)
    assert line in json.loads(execute("post", room, "{}"))["hookSpecificOutput"]["additionalContext"]
    assert execute("post", room, "{}") == ""
    # The wake's since-section names it for a body whose predecessor's cut precedes it.
    rows = [Fact(kind="lease", by="loom", at=_at(-10), data={"thread": "brnrd", "strand": STRAND, "gen": 1}),
            Fact(kind="attempt.ended", by="loom", at=_at(-5), data={
                "thread": "brnrd", "strand": STRAND, "gen": 1, "cause": "settled", "code": 0}),
            *[f for f in read_facts(home) if f.kind == "loom.moved"],
            Fact(kind="lease", by="loom", at=_at(10), data={"thread": "brnrd", "strand": STRAND, "gen": 2})]
    gone = since_gone(rows, STRAND, 2, "brnrd", {}, install)
    assert "loom moved aaaaaaaa → bbbbbbbb, loaded #7 (loom.moved:x)" in gone


def test_no_test_loom_follows_the_checkout_under_test():
    from brr.loom.runtime.receipts import source_root
    assert not (source_root() / ".git").exists(), source_root()


def test_a_jack_skips_a_boundary_line_kind_it_does_not_know(tmp_path):
    """A held jack keeps the code it imported; after a move the loom writes kinds that
    code never listed (moved: at a0552d77 raised and dropped the boundary)."""
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    (room / "port/in/boundary.md").write_text(render_boundary(STRAND, 1, "brnrd", [], set(), {}))
    execute("start", room, "{}")
    letter = "- s-ab12-bbbbbb/x1 · from s-ab12-bbbbbb · reply thread:other — the defect"
    text = (f"<!-- port {STRAND} gen 1 · owed s-ab12-bbbbbb/x1 -->\n"
            "status: strand · gen 1\nweather? a kind from a later loom\n"
            f"moved: loom moved aaaaaaaa → bbbbbbbb, loaded #7\nowed:\n{letter}\n")
    assert [view.id for view in parse_boundary(text).letters] == ["s-ab12-bbbbbb/x1"]
    (room / "port/in/boundary.md").write_text(text)
    shown = json.loads(execute("post", room, "{}"))["hookSpecificOutput"]["additionalContext"]
    assert "s-ab12-bbbbbb/x1" in shown
    assert not (room / "port" / "jack-errors.log").exists()
    # A "- " line is still a letter or corrupt.
    with pytest.raises(PortError):
        parse_boundary(text.replace(letter, "- not a letter"))
