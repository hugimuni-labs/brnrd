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
from brr.loom.runtime import ledger, loom
from brr.loom.runtime.home import Home
from brr.loom.runtime.loom import tick_once
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import _commit, git

from _step import write_thread

LABEL = "\n\nLoom-Label: taint=0; audience=self\n"
CHILD = "---\nid: c-1\nstatus: open\ntense: plan\nopened-by: thread:w-1\n---\n# child\n"


def test_kept_boundary_equals_a_fresh_render_and_every_input_re_renders(tmp_path, monkeypatch):
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
        assert tick()
        assert not tick()

        # The child's body ends: the parent sees it (the loom records the end in
        # the same tick, so the ledger revision alone re-renders it here).
        fcntl.flock(held, fcntl.LOCK_UN)
        assert tick()
        assert "opened: c-1 holding" not in boundary()

        # Main moves: a new child landed on main appears in the parent's boundary.
        write_thread(root, "c-2", CHILD.replace("c-1", "c-2"), "quit-now", wait="0")
        assert tick()
        assert "idle: 2 more opened here" in boundary()

        # A letter to the parent's thread is a ledger change.
        ledger.append(home, Fact(kind="letter", by="person:test", id="letter-to-parent",
                                 data={"id": "letter-to-parent", "from": "person:test", "to": "thread:w-1",
                                       "body": "a word for the parent"}))
        assert tick()
        assert "a word for the parent" in boundary()

        # A config change is a file the render opened.
        config = root / "loom/config.toml"
        config.write_text(config.read_text() + "\n# touched\n")
        assert tick()
        assert not tick()

        # The clock is the one input no stat sees: a render is retired after its age.
        monkeypatch.setattr(loom, "RENDER_MAX_AGE", 0.0)
        assert tick()
    held.close()
    for proc in started:
        proc.kill()
