"""A molt's cost is written at the next wake's first act, and the wake after opens on that line."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime import moltcost, notes, selfrepo
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.registry import problems

ROOT = Path(__file__).resolve().parents[2]
STARTED = "2026-11-22T00:00:00+00:00"


def _env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
    }
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _git(repo: Path, *args: str) -> None:
    env = dict(
        _env(),
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@x",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@x",
    )
    done = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr


def _wish(ident: str) -> Fact:
    return Fact(
        kind="wish",
        by="strand:s-ab12-oldold",
        id=ident,
        at="2026-11-21T00:00:00+00:00",
        data={
            "wish": ident,
            "says": [],
            "from": "person",
            "line": ident,
            "thread": "t-1",
            "parent": "w-root",
        },
    )


def _read(n: int, ident: str, at: str, strand: str, reading: str, on: str) -> Fact:
    return Fact(
        kind="wish.read",
        by=f"strand:{strand}",
        id=f"wish.read:{n}",
        at=at,
        data={"wish": ident, "reading": reading, "by": "resident", "wait": {"on": on}},
    )


def test_set_again_counts_only_the_wait_the_old_context_had_live() -> None:
    old, new = "s-ab12-oldold", "s-ab12-newnew"
    facts = [
        _wish("w-park"),
        _wish("w-ask"),
        _wish("w-diff"),
        _wish("w-fresh"),
        _wish("w-cleared"),
        _read(
            1, "w-park", "2026-11-21T01:00:00+00:00", old, "waiting: ci", "ci:o/r@abc"
        ),
        _read(
            2, "w-park", "2026-11-22T00:01:00+00:00", new, "waiting: ci", "ci:o/r@abc"
        ),
        _read(
            3,
            "w-ask",
            "2026-11-21T01:00:00+00:00",
            old,
            "waiting: ask w-x: go",
            "wish:w-x",
        ),
        _read(
            4,
            "w-ask",
            "2026-11-22T00:01:00+00:00",
            new,
            "waiting: ask w-x: go",
            "wish:w-x",
        ),
        _read(
            5, "w-diff", "2026-11-21T01:00:00+00:00", old, "waiting: old", "ci:o/r@old"
        ),
        _read(
            6, "w-diff", "2026-11-22T00:01:00+00:00", new, "waiting: new", "ci:o/r@new"
        ),
        _read(
            7,
            "w-fresh",
            "2026-11-22T00:01:00+00:00",
            new,
            "waiting: fresh",
            "at:2026-11-24T00:00:00Z",
        ),
        _read(
            8,
            "w-cleared",
            "2026-11-21T01:00:00+00:00",
            old,
            "waiting: ci",
            "ci:o/r@abc",
        ),
        _read(9, "w-cleared", "2026-11-21T02:00:00+00:00", old, "heard", "ci:o/r@abc"),
        _read(
            10,
            "w-cleared",
            "2026-11-22T00:01:00+00:00",
            new,
            "waiting: ci",
            "ci:o/r@abc",
        ),
    ]
    parks, asks = moltcost.set_again(facts, "t-1", old, new, STARTED)
    assert parks == ["w-park"]
    assert asks == ["w-ask"]


def test_a_shown_scar_counts_only_when_its_program_blocked_again(
    tmp_path: Path, monkeypatch
) -> None:
    room = tmp_path / "rooms" / "s-ab12-cccccc"
    (room / "port").mkdir(parents=True)
    (room / "port" / "notes-shown.json").write_text(
        json.dumps({"shown": ["memory/scars/loom/x.md", "memory/scars/loom/y.md"]})
        + "\n"
    )
    named = notes.Note("memory/scars/loom/x.md", "a gh refusal", ("gh pr view",))
    other = notes.Note("memory/scars/loom/y.md", "ssh", ("ssh",))
    monkeypatch.setattr(notes, "load", lambda _room: [named, other])
    monkeypatch.setattr(
        notes, "scar_text", lambda _room, path: ["the merge conflict is the refusal"]
    )
    monkeypatch.setattr(
        notes, "blocked_since", lambda _room, _boot: [("gh", "merge conflict")]
    )
    assert moltcost.repeated(room, 10.0) == ["memory/scars/loom/x.md"]
    monkeypatch.setattr(notes, "blocked_since", lambda _room, _boot: [])
    assert moltcost.repeated(room, 10.0) == []


def _call(room: Path, n: int, tool: str, given: dict, response: object) -> None:
    payload = {"tool_name": tool, "tool_input": given, "tool_use_id": f"t{n}"}
    execute("pre", room, json.dumps(payload))
    execute(
        "post", room, json.dumps(dict(payload, tool_response=response, duration_ms=3))
    )


def _room(root: Path, strand: str) -> tuple[Home, Path]:
    home = Home(root)
    room = root / "rooms" / strand
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    (room / "port" / "in" / "boundary.md").write_text(
        f"<!-- port {strand} gen 2 · owed -->\n"
    )
    return home, room


def _started(home: Home, strand: str, thread: str) -> None:
    loom = f"loom:{home.install_id()}"
    append(
        home,
        Fact(
            kind="lease",
            by=loom,
            id=f"lease:{strand}:2",
            data={
                "thread": thread,
                "strand": strand,
                "gen": 2,
                "install": home.install_id(),
                "router_gen": 1,
                "shell": "grok",
                "core": "grok",
                "repos": [],
            },
        ),
    )
    append(
        home,
        Fact(
            kind="body.started",
            by=loom,
            id=f"body.started:{strand}:2",
            at=STARTED,
            data={
                "strand": strand,
                "gen": 2,
                "pid": 1,
                "shell": "grok",
                "core": "grok",
                "readme": "abc",
                "lstart": "1",
            },
        ),
    )


def test_a_carry_string_is_the_recorded_text_not_a_path(tmp_path: Path) -> None:
    """#2816 stores the file's text. A string that names a real file is still that text."""
    room = tmp_path / "room"
    named = room / "self" / "carry.md"
    named.parent.mkdir(parents=True)
    named.write_bytes(b"x" * 99)
    path = Fact(
        kind="reincarnate", by="strand:s", id="s/path", data={"carry": "self/carry.md"}
    )
    assert moltcost.carry_bytes(room, path) == len(b"self/carry.md")
    text = Fact(
        kind="reincarnate", by="strand:s", id="s/text", data={"carry": "hello carry\n"}
    )
    assert moltcost.carry_bytes(room, text) == 12
    counted = Fact(kind="reincarnate", by="strand:s", id="s/n", data={"carry": 4})
    assert moltcost.carry_bytes(room, counted) == 4
    for raw in (None, "", True, -1, 1.5):
        bare = Fact(kind="reincarnate", by="strand:s", id="s/x", data={"carry": raw})
        assert moltcost.carry_bytes(room, bare) is None
    ended = Fact(kind="attempt.ended", by="loom:x", id="e", data={"carry": "hello"})
    assert moltcost.carry_bytes(room, ended) is None


def test_the_first_act_after_a_molt_writes_one_reading_and_a_stop_does_not(
    tmp_path: Path,
) -> None:
    root = tmp_path / "home"
    strand = "s-ab12-newnew"
    home, room = _room(root, strand)
    loom = f"loom:{home.install_id()}"
    _started(home, strand, "t-1")
    append(
        home,
        Fact(
            kind="attempt.ended",
            by=loom,
            id="attempt.ended:s-ab12-oldold:1",
            data={
                "thread": "t-1",
                "strand": "s-ab12-oldold",
                "gen": 1,
                "ids": [],
                "cause": "reincarnated",
                "code": 0,
                "dispositions": [],
                "evidence": {},
            },
        ),
    )
    append(
        home,
        Fact(
            kind="reincarnate",
            by="strand:s-ab12-oldold",
            id="s-ab12-oldold/abcde",
            data={
                "strand": "s-ab12-oldold",
                "gen": 1,
                "why": "residue",
                "carry": "hello carry\n",
            },
        ),
    )
    for fact in (
        _wish("w-park"),
        _wish("w-ask"),
        _wish("w-diff"),
        _read(
            1,
            "w-park",
            "2026-11-21T01:00:00+00:00",
            "s-ab12-oldold",
            "waiting: ci",
            "ci:o/r@abc",
        ),
        _read(
            2,
            "w-park",
            "2026-11-22T00:01:00+00:00",
            strand,
            "waiting: ci",
            "ci:o/r@abc",
        ),
        _read(
            3,
            "w-ask",
            "2026-11-21T01:00:00+00:00",
            "s-ab12-oldold",
            "waiting: ask w-x: go",
            "wish:w-x",
        ),
        _read(
            4,
            "w-ask",
            "2026-11-22T00:01:00+00:00",
            strand,
            "waiting: ask w-x: go",
            "wish:w-x",
        ),
        _read(
            5,
            "w-diff",
            "2026-11-21T01:00:00+00:00",
            "s-ab12-oldold",
            "waiting: old",
            "ci:o/r@old",
        ),
        _read(
            6,
            "w-diff",
            "2026-11-22T00:01:00+00:00",
            strand,
            "waiting: new",
            "ci:o/r@new",
        ),
    ):
        append(home, fact)
    _call(room, 0, "Read", {"file_path": "/a/b.py"}, "y" * 1000)
    assert [f for f in read_facts(home) if f.kind == "molt.cost"] == []
    _call(room, 1, "Bash", {"command": "git commit -qm 'land'"}, {"stdout": ""})
    _call(room, 2, "Bash", {"command": "git commit -qm 'again'"}, {"stdout": ""})
    costs = [f for f in read_facts(home) if f.kind == "molt.cost"]
    assert len(costs) == 1
    data = costs[0].data
    assert problems(costs[0]) == []
    assert data["opened"] == "s-ab12-oldold/abcde"
    assert data["tokens"] is None
    assert data["reset"] == {"parks": ["w-park"], "asks": ["w-ask"]}
    assert data["repeated"] == []
    assert data["carry_bytes"] == len(b"hello carry\n")
    assert data["read_bytes"] == 1000
    assert moltcost.line(data) == (
        "last molt: tokens unknown · 1 park set again · 1 ask set again · 0 scars again · "
        "carry 12 B against 1.0 KB read before the first act"
    )
    assert moltcost.wake_line(read_facts(home), "t-1") == moltcost.line(data)

    other = "s-ab12-stoppd"
    _home, stopped = _room(root, other)
    (stopped / "port" / "in" / "boundary.md").write_text(
        f"<!-- port {other} gen 2 · owed -->\n"
    )
    _started(home, other, "t-2")
    append(
        home,
        Fact(
            kind="attempt.ended",
            by=loom,
            id="attempt.ended:s-ab12-crashd:1",
            data={
                "thread": "t-2",
                "strand": "s-ab12-crashd",
                "gen": 1,
                "ids": [],
                "cause": "stopped",
                "code": 0,
                "dispositions": [],
                "evidence": {},
            },
        ),
    )
    _call(stopped, 0, "Bash", {"command": "git commit -qm 'land'"}, {"stdout": ""})
    assert [
        f
        for f in read_facts(home)
        if f.kind == "molt.cost" and f.data["thread"] == "t-2"
    ] == []
    assert (
        json.loads((stopped / "port" / "jack-state.json").read_text())["molt_cost"]
        == "skip"
    )


def test_the_next_wake_opens_on_the_last_molt_line(tmp_path: Path) -> None:
    home = tmp_path / "home"
    init = subprocess.run(
        [sys.executable, "-m", "brr.loom.runtime", "init", "--home", str(home)],
        cwd=ROOT,
        env=_env(),
        capture_output=True,
        text=True,
    )
    assert init.returncode == 0, init.stderr
    thread = home / "self" / "threads" / "w-1"
    thread.mkdir(parents=True)
    (thread / "README.md").write_text(
        "---\nid: w-1\nstatus: open\ntense: plan\n---\n# Headline\n\n## People\nnot the people block\n"
    )
    _git(home / "self", "add", "-A")
    _git(home / "self", "commit", "-m", "thread")
    reading = {
        "thread": "w-1",
        "strand": "s-ab12-newnew",
        "gen": 2,
        "opened": "s-ab12-oldold/abcde",
        "tokens": 40000,
        "reset": {"parks": ["w-park"], "asks": []},
        "repeated": [],
        "carry_bytes": 12,
        "read_bytes": 1000,
    }
    stored = Home(home)
    append(
        stored,
        Fact(
            kind="molt.cost",
            by=f"loom:{stored.install_id()}",
            id="molt.cost:s-ab12-newnew:2",
            at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            data=reading,
        ),
    )
    clone = selfrepo.room(home, "seeded")
    owed, part = tmp_path / "owed.json", tmp_path / "part"
    owed.write_text("[]")
    part.write_text("loom part\n")
    core, sizes, out = tmp_path / "core.md", tmp_path / "sizes.json", tmp_path / "out"
    with out.open("w") as stdout:
        assert (
            selfrepo.run_wake(
                clone,
                "w-1",
                owed,
                loom_part=part,
                core=core,
                stdout=stdout,
                sizes=sizes,
            )
            == 0
        )
    text = out.read_text()
    assert text.startswith(moltcost.line(reading) + "\n")
    got = json.loads(sizes.read_text())
    delivered = core.read_bytes() + out.read_bytes()
    assert got["total"] == len(delivered)
    assert sum(got["parts"].values()) == got["total"]


def test_an_unowed_wake_holds_the_map_and_keeps_the_carry() -> None:
    wake = (
        "last molt: tokens unknown · 0 parks set again · 0 asks set again · 0 scars again · "
        "carry 4 B against 1 B read before the first act\n"
        "Molted at 01:00Z. Carried across:\n"
        "keep this\n"
        "## threads/t/README.md\n"
        "spec\n"
        "## Topics close to this wake\n"
        "topics/wake.md\n"
        "## Tree\n"
        "threads/a/README.md\n"
        "## Topics\n"
        "topics/wake.md · 2026-11-02 · title\n"
        "## Loom\n"
        "Molted notes stay\n"
        "you are strand s-ab12-newnew on thread t\n"
        "loom: runs abc\n"
        "repo brnrd: somewhere\n"
        "to send a repo's branch out: publish\n"
        "## Occasion\n"
        "the letter\n"
    )
    kept, later = selfrepo.hold_back(wake)
    assert kept.startswith("last molt:")
    assert "keep this" in kept and "spec" in kept and "Molted notes stay" in kept
    assert "the letter" in kept
    assert "## Tree" not in kept and "you are strand" not in kept and "loom: runs abc" not in kept
    assert "## Tree" in later and "topics/wake.md" in later and "you are strand" in later
    assert "Molted notes stay" not in later and "keep this" not in later


def test_the_first_boundary_says_what_the_wake_held_back(tmp_path: Path) -> None:
    from brr.loom.runtime.port import render_boundary
    strand = "s-ab12-aaaaaa"
    room = tmp_path / "rooms" / strand
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    (room / "port" / "in" / "boundary.md").write_text(
        render_boundary(strand, 1, "ta", [], set(), {}))
    (room / "port" / "later.md").write_text("## Tree\nthreads/a/README.md\n")
    out = execute("start", room, "{}")
    assert "## Tree" in out and "threads/a/README.md" in out
    assert not (room / "port" / "later.md").is_file()
    assert "## Tree" not in execute("start", room, "{}")
