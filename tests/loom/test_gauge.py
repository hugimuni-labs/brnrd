"""The wake gauge: part sizes from a real wake, the trace's first line, and the summary line."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime import gauge, selfrepo, tooltrace
from brr.loom.runtime.jack import execute
from brr.loom.runtime.loom import _reads

ROOT = Path(__file__).resolve().parents[2]


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _git(repo: Path, *args: str) -> None:
    env = dict(_env(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@x")
    done = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_a_wake_reports_each_part_and_the_parts_sum_to_what_was_delivered(tmp_path: Path) -> None:
    home = tmp_path / "home"
    init = subprocess.run([sys.executable, "-m", "brr.loom.runtime", "init", "--home", str(home)],
                          cwd=ROOT, env=_env(), capture_output=True, text=True)
    assert init.returncode == 0, init.stderr
    thread = home / "self" / "threads" / "w-1"
    thread.mkdir(parents=True)
    (thread / "README.md").write_text("---\nid: w-1\nstatus: open\ntense: plan\n---\n# Headline\n\n## People\nnot the people block\n")
    _git(home / "self", "add", "-A")
    _git(home / "self", "commit", "-m", "thread")
    clone = selfrepo.room(home, "seeded")
    owed = tmp_path / "owed.json"
    owed.write_text(json.dumps([{"id": "l-1", "from": "p", "body": "## Tree\na letter with a heading"}]))
    part = tmp_path / "part"
    part.write_text("loom part\n")
    core, sizes, out = tmp_path / "core.md", tmp_path / "sizes.json", tmp_path / "out"
    with out.open("w") as stdout:
        assert selfrepo.run_wake(clone, "w-1", owed, loom_part=part, core=core, stdout=stdout, sizes=sizes) == 0
    got = json.loads(sizes.read_text())
    delivered = core.read_bytes() + out.read_bytes()
    assert got["total"] == len(delivered)
    assert sum(got["parts"].values()) == got["total"]
    # The headings inside the README and the letter do not move bytes between parts.
    assert {"core", "briefing", "readme", "tree", "people", "occasion"} <= set(got["parts"])
    assert "recipe" not in got["parts"]
    assert got["parts"]["occasion"] > len("a letter with a heading")


def test_a_recipe_that_names_no_parts_is_one_lump_never_a_zero(tmp_path: Path) -> None:
    sizes = tmp_path / "sizes.json"
    sizes.write_text('{"readme": 1}')  # wrong: does not sum to the rest
    got = selfrepo.wake_sizes(sizes, "x" * 100, briefing="b" * 10, generated="")
    assert got == {"total": 100, "parts": {"briefing": 10, "generated": 0, "recipe": 90}}


def _call(room: Path, n: int, tool: str, given: dict, response: object) -> None:
    payload = {"tool_name": tool, "tool_input": given, "tool_use_id": f"t{n}"}
    execute("pre", room, json.dumps(payload))
    execute("post", room, json.dumps(dict(payload, tool_response=response, duration_ms=3)))


def test_reads_before_the_first_line_count_what_came_back_until_a_commit(tmp_path: Path) -> None:
    room = tmp_path / "rooms" / "s-ab12-aaaaaa"
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    start = datetime.now(timezone.utc).timestamp() - 1
    long_cd = "cd /a/very/long/room/path/" + "x" * 150 + " && "
    _call(room, 0, "Read", {"file_path": "/a/b.py"}, "y" * 1000)
    _call(room, 1, "Bash", {"command": long_cd + "grep -n def b.py"}, {"stdout": "z" * 500})
    _call(room, 2, "Bash", {"command": long_cd + "git commit -qm 'a; b'"}, {"stdout": ""})
    _call(room, 3, "Read", {"file_path": "/a/c.py"}, "w" * 200)
    rows = tooltrace.read(room)
    assert [r["w"] for r in rows] == [False, False, True, False]
    assert rows[2]["cmd"].endswith("…")  # the stored command is cut before the commit
    fact = Fact(kind="body.started", by="loom:x", data={"strand": "s-ab12-aaaaaa", "gen": 1},
                at=datetime.fromtimestamp(start, timezone.utc).isoformat())
    got = _reads(room, fact)
    assert got["calls"] == 4 and got["before"] == 2 and got["first_s"] is not None
    assert got["before_bytes"] == rows[0]["out"] + rows[1]["out"] >= 1500
    assert got["bytes"] > got["before_bytes"]
    assert tooltrace.produces("Write", {}) and tooltrace.produces("Bash", {"command": "brnrd runtime send --to x y"})
    assert not tooltrace.produces("Bash", {"command": "echo git commit"})


def test_the_line_says_boots_spend_wake_and_reads_and_names_its_unknowns() -> None:
    now = datetime.now(timezone.utc)

    def fact(kind, data, ident):
        return Fact(kind=kind, by="loom:x", data=data, id=ident, at=now.isoformat())

    facts = [
        fact("wake.sizes", {"thread": "t", "strand": "s1", "gen": 1, "budget": 24000, "total": 20000,
                            "parts": {"core": 8000, "readme": 12000}}, "wake.sizes:s1:1"),
        fact("wake.sizes", {"thread": "t", "strand": "s1", "gen": 2, "budget": 24000, "total": 20000,
                            "parts": {"core": 8000, "readme": 12000}}, "wake.sizes:s1:2"),
        fact("attempt.ended", {"thread": "t", "strand": "s1", "gen": 1, "evidence": {
            "reads": {"calls": 9, "before": 6, "before_bytes": 300, "bytes": 1000, "first_s": 40}}}, "a:1"),
        fact("wish.result", {"wish": "w-1", "delivered": True}, "r:1"),
    ]
    text = gauge.line(gauge.gauge(facts, 7, now=now, spent=lambda s: 500_000))
    assert text == ("7d: 2 boots · spent 500k · wake 20.0 KB avg (core 40%, readme 60%) · "
                    "2.0 boots per delivered wish · 30% of read bytes before a first line (1 bodies traced)")
    bare = gauge.line(gauge.gauge(facts[2:3], 7, now=now, spent=lambda s: None))
    assert bare == ("7d: 1 boots · spent unknown · wake size unknown (no wake.sizes yet) · "
                    "1 boots, 0 delivered · 30% of read bytes before a first line (1 bodies traced)")


def test_gauge_and_attention_find_the_home_from_the_room_like_every_body_verb(tmp_path: Path) -> None:
    """Inside a body only BRNRD_ROOM is set: a verb that needs --home there refuses where send succeeds."""
    home = tmp_path / "home"
    init = subprocess.run([sys.executable, "-m", "brr.loom.runtime", "init", "--home", str(home)],
                          cwd=ROOT, env=_env(), capture_output=True, text=True)
    assert init.returncode == 0, init.stderr
    room = home / "rooms" / "s-test"
    room.mkdir(parents=True)
    env = {k: v for k, v in _env().items() if k != "BRNRD_HOME"}
    env["BRNRD_ROOM"] = str(room)
    env["HOME"] = str(tmp_path)  # no default ~/brnrd to fall back on
    for verb in ("gauge", "attention"):
        done = subprocess.run([sys.executable, "-m", "brr.loom.runtime", verb], cwd=room, env=env,
                              capture_output=True, text=True)
        assert done.returncode == 0, (verb, done.stderr)
        assert "no loom home" not in done.stderr
    done = subprocess.run([sys.executable, "-m", "brr.loom.runtime", "gauge", "--days", "7"], cwd=room,
                          env=env, capture_output=True, text=True)
    assert done.stdout.startswith("7d: 0 boots"), done.stdout
    # an explicit --home still wins over the room
    other = tmp_path / "other"
    assert subprocess.run([sys.executable, "-m", "brr.loom.runtime", "init", "--home", str(other)],
                          cwd=ROOT, env=_env(), capture_output=True, text=True).returncode == 0
    done = subprocess.run([sys.executable, "-m", "brr.loom.runtime", "gauge", "--home", str(other)],
                          cwd=room, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
