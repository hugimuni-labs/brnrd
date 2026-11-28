"""A boundary is rendered again only when something it read changed (loom-cpu).

Every production render is compared with a fresh one: a kept boundary is
byte-equal to the uncached render, and each input the render reads (the
ledger, main, a child's liveness and jack state, the gauge) re-renders it.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess

from brr.daemon2.facts import Fact
from brr.loom.runtime import ledger, loom, port
from brr.loom.runtime.home import Home
from brr.loom.runtime.loom import tick_once
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import _commit, git

from _step import write_thread

LABEL = "\n\nLoom-Label: taint=0; audience=self\n"
CHILD = "---\nid: c-1\nstatus: open\ntense: plan\nopened-by: thread:w-1\n---\n# child\n"


def test_kept_boundary_equals_a_fresh_render_and_every_input_re_renders(tmp_path, monkeypatch):
    # The header clock is sampled on render and is independent of cache inputs.
    stamp = port.clock()
    now = [100.0]
    monkeypatch.setattr(loom, "_live_observation_time", lambda: now[0])
    monkeypatch.setattr(port, "clock", lambda: stamp)
    root = tmp_path / "home"
    write_thread(root, "w-1", "# parent\n", "quit-now", wait="0")
    write_thread(root, "c-1", CHILD, "quit-now", wait="0")
    home = Home(root)
    router = Router(home)
    tick_once(home, {}, "fake", "haiku", router)
    router.clock = lambda: router.lease.until - router.config.router_ttl
    install = home.install_id()
    parent, child = f"s-{install}-parent", f"s-{install}-child"
    for strand, thread in ((parent, "w-1"), (child, "c-1")):
        ledger.append(home, Fact(kind="lease", by=f"loom:{install}", data={
            "strand": strand, "thread": thread, "gen": 1,
            "router_gen": router.lease.gen, "install": install}))
        ledger.append(home, Fact(kind="body.started", by=f"loom:{install}", data={
            "strand": strand, "gen": 1, "shell": "fake", "core": "haiku",
            "pid": 0, "readme": "", "lstart": ""}))
        (home.room(strand) / "port/in").mkdir(parents=True)

    renders, real = [], loom._rendered

    def checked(facts, key, render):
        before = loom._RENDERS.get(key[0])
        text = real(facts, key, render)
        if loom._RENDERS.get(key[0]) is not before:
            renders.append(key[0])
        assert text == render(), "a kept boundary differs from a fresh render"
        return text

    monkeypatch.setattr(loom, "_rendered", checked)
    # The test holds each body's lock itself: a body the loom started for a
    # dead lease would race the liveness this test sets, so a start runs a
    # process that takes no lock and writes no facts.
    started = []

    def spawn(room, *a):
        started.append(subprocess.Popen(["sleep", "600"]))
        return started[-1], open(os.devnull, "ab")

    monkeypatch.setattr(loom, "_spawn", spawn)

    def tick():
        renders.clear()
        tick_once(home, {}, "fake", "haiku", router)
        return parent in renders

    def boundary():
        return (home.room(parent) / "port/in/boundary.md").read_text()

    held = (home.room(child) / ".body").open("a")
    with (home.room(parent) / ".body").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert tick()
        assert "opened: c-1 holding" in boundary()
        assert not any(tick() for _ in range(3)), "an idle tick re-rendered"

        # A renewal is the one append no boundary reads.
        ledger.append(home, Fact(kind="router.renewed", by=f"loom:{install}", id="renewal:test",
                                 data={"gen": 0}, hlc=(10**15, 0, install)))
        assert not tick()

        # The child's jack state was opened by the render: a write to it re-renders.
        (home.room(child) / "port/jack-state.json").write_text(json.dumps({"stop_blocks": 1}))
        now[0] += .2
        assert not tick()
        now[0] += .8
        assert tick()
        assert not tick()

        # The child's body ends: the parent sees it (the loom records the end in
        # the same tick, so the ledger revision alone re-renders it here).
        fcntl.flock(held, fcntl.LOCK_UN)
        now[0] += 1
        assert tick()
        assert "opened: c-1 holding" not in boundary()

        # Main moves: a new child landed on main appears in the parent's boundary.
        write_thread(root, "c-2", CHILD.replace("c-1", "c-2"), "quit-now", wait="0")
        now[0] += 1
        assert tick()
        assert "idle: 2 more opened here" in boundary()

        # A letter to the parent's thread is a ledger change: it shows at the very next tick, however long a
        # render is kept (busy-cpu), and the boundary is newer than every ledger file, as the jack's stop reads it.
        monkeypatch.setattr(loom, "RENDER_MAX_AGE", 3600.0)
        ledger.append(home, Fact(kind="letter", by="person:test", id="letter-to-parent",
                                 data={"id": "letter-to-parent", "from": "person:test", "to": "thread:w-1",
                                       "body": "a word for the parent"}))
        assert tick()
        assert "a word for the parent" in boundary()
        rendered = (home.room(parent) / "port/in/boundary.md").stat().st_mtime
        assert all(path.stat().st_mtime <= rendered for path in (root / "ledger/facts").glob("*/*.jsonl"))
        monkeypatch.setattr(loom, "RENDER_MAX_AGE", 30.0)

        # A config change is a file the render opened.
        config = root / "loom/config.toml"
        config.write_text(config.read_text() + "\n# touched\n")
        now[0] += 1
        assert tick()
        assert not tick()

        # The clock is the one input no stat sees: a render is retired after its age.
        monkeypatch.setattr(loom, "RENDER_MAX_AGE", 0.0)
        now[0] += 1
        assert tick()
    held.close()
    for proc in started:
        proc.kill()


def test_a_growing_transcript_keeps_the_render_and_a_watched_file_retires_it(tmp_path, monkeypatch):
    """busy-cpu: a body's transcript grows at every turn; a render that read it was re-rendered at the next tick.
    The spend read from it keeps its own TTL, so it is read but not watched."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    transcript = tmp_path / "claude/projects/-room-self/one.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    watched = tmp_path / "config.toml"
    watched.write_text("a = 1\n")
    facts, renders = object(), []

    def render():
        renders.append(1)
        return transcript.read_text() + watched.read_text()
    key = ("s-aaaa-render", 1)
    loom._rendered(facts, key, render)
    with transcript.open("a") as out:
        out.write("{}\n")
    loom._rendered(facts, key, render)
    assert len(renders) == 1
    watched.write_text("a = 2\n")
    loom._rendered(facts, key, render)
    assert len(renders) == 2


def test_two_boundaries_share_file_reads_and_both_see_replacement(tmp_path, monkeypatch):
    from collections import Counter
    from pathlib import Path
    from types import SimpleNamespace

    from brr.loom.runtime.tick_inputs import tick_inputs

    root = tmp_path / "home"
    for name in ("a", "b"):
        write_thread(root, name, f"# parent {name}\n", "quit-now", wait="0")
        child = CHILD.replace("c-1", f"c-{name}").replace("w-1", name)
        write_thread(root, f"c-{name}", child, "quit-now", wait="0")
    home = Home(root)
    install = home.install_id()
    opened = [Fact(kind="pr.opened", by=f"loom:{install}", data={
        "thread": f"c-{name}", "repo": "acme/tool", "number": 7, "head": "abc"})
        for name in ("a", "b")]
    facts = ledger.LedgerView(opened)
    cards = root / "loom/prs.json"
    gauges = root / "loom/usage"
    gauges.mkdir()
    sample = gauges / "claude.json"
    reads = Counter()
    original = Path.read_text

    def counted(path, *args, **kwargs):
        if path in (cards, sample):
            reads[path] += 1
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    monkeypatch.setattr(loom, "RENDER_MAX_AGE", 3600)

    def boundaries():
        with tick_inputs():
            result = []
            for name in ("a", "b"):
                strand = f"s-{install}-{name}"
                path = home.room(strand) / "port/in"
                path.mkdir(parents=True, exist_ok=True)
                context = SimpleNamespace(thread=name, fuel="", why="", shell="claude")
                loom._boundary(home, facts, strand, 1, context)
                result.append((path / "boundary.md").read_text())
            return result

    # Both renders register absence, including the render that hit the cache.
    assert all("#7" in text and "session pace:" not in text for text in boundaries())
    for state, left in (("pending", 31), ("green", 52)):
        for path, payload in (
            (cards, {"acme/tool#7": {"state": "open", "ci": state}}),
            (sample, {"at": 100, "windows": [
                {"name": "session", "left_pct": left, "resets_at": "2099-01-01T00:00:00Z"}]}),
        ):
            fresh = path.with_suffix(".new")
            fresh.write_text(json.dumps(payload))
            fresh.replace(path)
        reads.clear()
        rendered = boundaries()
        assert all(f"#7 ready {state}" in text and f"session {left}%" in text for text in rendered)
        assert reads == {cards: 1, sample: 1}
    cards.unlink()
    sample.unlink()
    assert all("#7 ready" not in text and "session pace:" not in text for text in boundaries())


def test_connector_config_snapshot_retires_boundary_when_config_is_replaced(tmp_path):
    from brr.loom.runtime.config import load_config, tick_config
    from brr.loom.runtime.connector import instances
    from brr.loom.runtime.tick_inputs import stat, tick_inputs

    home = Home(tmp_path)
    config = tmp_path / "loom/config.toml"
    config.parent.mkdir()
    alias = tmp_path.parent / (tmp_path.name + "-alias")
    alias.symlink_to(tmp_path, target_is_directory=True)
    facts = object()

    def render():
        return ", ".join(instances(Home(alias)))

    for name in ("first", "second"):
        fresh = config.with_suffix(".new")
        fresh.write_text(f'connectors = ["{name}"]\n')
        fresh.replace(config)
        with tick_config(home.root, load_config(home.root), stat(config)), tick_inputs():
            assert loom._rendered(facts, ("connector-config",), render) == name
