"""A due when: is one letter. The clock is the machine's; nothing here is injected."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import tick_once
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import init_self, room, seed_root
from brr.loom.runtime.threads import thread_table

from _step import write_thread

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
CHECKER = seed_root() / "core" / "immune.d" / "10-readme"


def _stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timers(facts, thread=None):
    found = [fact for fact in facts
             if fact.kind == "letter" and str(fact.id).startswith("letter:timer:")]
    if thread is None:
        return found
    prefix = f"letter:timer:{thread}:"
    return [fact for fact in found if str(fact.id).startswith(prefix)]


def _started(facts, thread):
    strands = {fact.data.get("strand") for fact in facts
               if fact.kind == "lease" and fact.data.get("thread") == thread}
    return [fact for fact in facts
            if fact.kind == "body.started" and fact.data.get("strand") in strands]


def _kill(facts) -> None:
    for fact in facts:
        if fact.kind != "body.started":
            continue
        pid = fact.data.get("pid")
        if not isinstance(pid, int):
            continue
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _thread(root: Path, thread: str, when: str, status: str = "open") -> None:
    write_thread(root, thread, (
        f"---\nid: {thread}\nstatus: {status}\ntense: plan\n"
        f"when: {when}\n---\n# {thread}\n"
    ), "quit-now", wait="0")


def _tick(home, bodies, router=None) -> None:
    tick_once(home, bodies, "fake", "haiku", router)


def _until(due: datetime) -> None:
    left = (due - datetime.now(timezone.utc)).total_seconds()
    if left > 0:
        time.sleep(left + 0.05)


def test_timer_letter_fires_once_across_a_restart(tmp_path: Path) -> None:
    root = tmp_path / "home"
    init_self(root, person="test")
    due = datetime.now(timezone.utc) + timedelta(seconds=2)
    when = _stamp(due)
    _thread(root, "w-1", when)
    home = Home(root)
    assert datetime.now(timezone.utc) < due
    bodies: dict = {}
    _tick(home, bodies)
    assert _timers(read_facts(home), "w-1") == []
    _until(due)
    _tick(home, bodies)
    try:
        facts = read_facts(home)
        letters = _timers(facts, "w-1")
        assert len(letters) == 1
        letter = letters[0]
        assert letter.id == f"letter:timer:w-1:{when}"
        assert letter.by == f"loom:{home.install_id()}"
        assert letter.data["from"] == "loom"
        assert letter.data["to"] == "thread:w-1"
        assert letter.data["body"] == (
            f"The time {when} has come for thread w-1. "
            f"when: still holds {when} until this body changes it."
        )
        assert letter.data["label"] == {"taint": False, "audience": ["self"]}
        assert len(_started(facts, "w-1")) == 1
        fresh: dict = {}
        router = Router(home)
        for _ in range(20):
            _tick(home, fresh, router)
        facts = read_facts(home)
        assert len(_timers(facts, "w-1")) == 1
        assert len(_started(facts, "w-1")) == 1
        row = next(row for row in thread_table(home, facts) if row["thread"] == "w-1")
        assert row["when"] == "spent"
    finally:
        _kill(read_facts(home))


def test_a_second_install_leaves_the_one_letter(tmp_path: Path) -> None:
    root = tmp_path / "home"
    init_self(root, person="test")
    when = _stamp(datetime.now(timezone.utc) - timedelta(seconds=2))
    _thread(root, "w-1", when)
    home = Home(root, install="aaaa")
    router = Router(home)
    bodies: dict = {}
    _tick(home, bodies, router)
    try:
        assert len(_timers(read_facts(home), "w-1")) == 1
        assert router.lease is not None
        assert router.authority.release(router.lease)
        other = Home(root, install="bbbb")
        _tick(other, {}, Router(other))
        facts = read_facts(other)
        letters = _timers(facts, "w-1")
        assert len(letters) == 1
        assert letters[0].by == "loom:aaaa"
        assert not any(fact.by.startswith("loom:bbbb") and str(fact.id).startswith("letter:timer:")
                       for fact in facts)
    finally:
        _kill(read_facts(home))


def test_a_settled_thread_does_not_fire(tmp_path: Path) -> None:
    root = tmp_path / "home"
    init_self(root, person="test")
    when = _stamp(datetime.now(timezone.utc) - timedelta(seconds=2))
    _thread(root, "w-1", when, status="settled")
    home = Home(root)
    _tick(home, {})
    facts = read_facts(home)
    assert _timers(facts) == []
    assert _started(facts, "w-1") == []
    row = next(row for row in thread_table(home, facts) if row["thread"] == "w-1")
    assert row["when"] == when


def test_readme_gate_accepts_only_an_empty_or_absolute_when(tmp_path: Path) -> None:
    folder = tmp_path / "w-1"
    folder.mkdir()
    path = folder / "README.md"

    def check(front: str) -> subprocess.CompletedProcess[str]:
        path.write_text(
            f"---\nid: w-1\nstatus: open\ntense: plan\n{front}---\n# W\n",
            encoding="utf-8",
        )
        return subprocess.run(
            [sys.executable, str(CHECKER), "--file", str(path)],
            cwd=ROOT, capture_output=True, text=True,
        )

    refused = check("when: tomorrow\n")
    assert refused.returncode == 1
    assert "tomorrow" in refused.stderr
    assert check('when: ""\n').returncode == 0
    assert check("when: 2026-10-13T00:00:00Z\n").returncode == 0
    assert check("").returncode == 0
    assert check("when: 2026-02-31T00:00:00Z\n").returncode == 1


def _env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _when(room: Path, text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "brr.loom.runtime", "when", text, "--room", str(room)],
        cwd=ROOT, env=_env(), capture_output=True, text=True,
    )


def test_when_verb_stores_an_absolute_instant_and_clear(tmp_path: Path) -> None:
    root = tmp_path / "home"
    init_self(root, person="test")
    _thread(root, "w-1", '""')
    home = Home(root)
    strand = "s-aaaa-body01"
    append(home, Fact(
        kind="lease", by=f"loom:{home.install_id()}", id="lease:w-1:1",
        data={"thread": "w-1", "strand": strand, "gen": 1, "install": home.install_id()},
    ))
    room_dir = room(root, strand).parent
    before = datetime.now(timezone.utc)
    stored = _when(room_dir, "+1d")
    assert stored.returncode == 0, stored.stderr
    instant = datetime.strptime(stored.stdout.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert timedelta(hours=23, minutes=59) < instant - before < timedelta(days=1, seconds=5)
    readme = (root / "self" / "threads" / "w-1" / "README.md").read_text(encoding="utf-8")
    assert f"when: {stored.stdout.strip()}" in readme
    via_brnrd = subprocess.run(
        [sys.executable, "-m", "brr", "when", "+2h", "--room", str(room_dir)],
        cwd=ROOT, env=_env(), capture_output=True, text=True,
    )
    assert via_brnrd.returncode == 0, via_brnrd.stderr
    cleared = _when(room_dir, "clear")
    assert cleared.returncode == 0, cleared.stderr
    readme = (root / "self" / "threads" / "w-1" / "README.md").read_text(encoding="utf-8")
    assert 'when: ""' in readme
    bad = _when(room_dir, "tomorrow")
    assert bad.returncode == 1
    assert "when:" in bad.stderr
