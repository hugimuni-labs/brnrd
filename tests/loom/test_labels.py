"""The fold, the jack's taint file, and a clean-by-schema letter."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime.jack import execute
from brr.loom.runtime.labels import CLEAN, is_stranger, join, strand_label
from brr.loom.runtime.loom import _ingest
from brr.loom.runtime.port import fact_from_port, parse_frontmatter, write_send
from brr.loom.runtime.home import Home

ROOT = Path(__file__).resolve().parents[2]
STRAND = "s-ab12-aaaaaa"


def _fact(kind: str, ident: str, data: dict, by: str = "loom") -> Fact:
    return Fact(kind=kind, by=by, id=ident, data=data)


def _thread(root: Path, name: str) -> None:
    folder = root / "threads" / name
    folder.mkdir(parents=True)
    (folder / "README.md").write_text(
        f"---\nid: {name}\nstatus: open\ntense: plan\n---\n# {name}\n",
        encoding="utf-8",
    )


def _room(tmp_path: Path, strand: str = "s1") -> tuple[Path, Path]:
    home = tmp_path / "home"
    room = home / "rooms" / strand
    (room / "port" / "out").mkdir(parents=True)
    (home / "self").mkdir()
    return home, room


def test_a_strangers_letter_taints_and_a_thread_does_not(tmp_path: Path) -> None:
    root = tmp_path / "self"
    _thread(root, "inbox")
    assert is_stranger("inbox", root) is False
    assert is_stranger("thread:inbox", root) is False
    assert is_stranger("channel-user", root) is True
    stranger = _fact("letter", "x/1", {"id": "x/1", "from": "channel-user", "body": "hi"})
    own = _fact("letter", "inbox/1", {"id": "inbox/1", "from": "thread:inbox", "body": "hi"})
    shown_bad = _fact("shown", "shown-1", {"strand": "s1", "ids": ["x/1"], "gen": 1})
    shown_ok = _fact("shown", "shown-2", {"strand": "s2", "ids": ["inbox/1"], "gen": 1})
    tainted = strand_label([stranger, shown_bad], "s1", self_root=root, jack_log="")
    clean = strand_label([own, shown_ok], "s2", self_root=root, jack_log="")
    assert tainted.taint is True
    assert clean == CLEAN


def test_a_child_of_a_tainted_parent_is_born_tainted() -> None:
    facts = [
        _fact("lease", "lease-p", {"strand": "parent", "gen": 1, "thread": "t"}),
        _fact("label.tainted", "taint-p", {"strand": "parent", "tool": "WebFetch"}, by="strand:parent"),
        _fact("lease", "lease-c", {"strand": "child", "parent": "parent", "gen": 1, "thread": "u"}),
    ]
    assert strand_label(facts, "child", jack_log="").taint is True
    assert strand_label(facts, "parent", jack_log="").taint is True


def test_a_taint_fact_a_url_and_a_jack_log_each_taint() -> None:
    facts = [
        _fact("label.tainted", "t1", {"strand": "s1", "tool": "WebSearch"}, by="strand:s1"),
        _fact("source", "src", {"strand": "s2", "origin": "https://example.com/a"}, by="strand:s2"),
        _fact("source", "local", {"strand": "s3", "origin": "memory/stance.md"}, by="strand:s3"),
    ]
    assert strand_label(facts, "s1", jack_log="").taint is True
    assert strand_label(facts, "s2", jack_log="").taint is True
    assert strand_label(facts, "s3", jack_log="").taint is False
    assert strand_label([], "s3", jack_log="  \n").taint is False
    assert strand_label([], "s3", jack_log="traceback\n").taint is True
    assert strand_label([], "s3", jack_log="traceback\n").audience == frozenset({"self"})


def test_join_ors_taint_and_intersects_audience() -> None:
    left = strand_label([], "s", jack_log="x")
    right = CLEAN
    assert join(left, right).taint is True
    assert join(left, right).audience == frozenset({"self"})


def test_webfetch_writes_a_taint_file_and_read_and_bash_do_not(tmp_path: Path) -> None:
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "out").mkdir(parents=True)
    assert execute("post", room, json.dumps({"tool_name": "WebFetch"})) == ""
    written = list((room / "port" / "out").glob("taint-*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text()) == {"tool": "WebFetch"}
    execute("post", room, json.dumps({"tool_name": "WebSearch"}))
    assert len(list((room / "port" / "out").glob("taint-*.json"))) == 2
    execute("post", room, json.dumps({"tool_name": "Read"}))
    execute("post", room, json.dumps({"tool_name": "Bash"}))
    execute("post", room, "{}")
    assert len(list((room / "port" / "out").glob("taint-*.json"))) == 2


def test_only_the_taint_file_changes_the_label(tmp_path: Path) -> None:
    home = Home(tmp_path, install="ab12")
    (home.thread_dir("t")).mkdir(parents=True)
    (home.thread_dir("t") / "README.md").write_text("# t\n", encoding="utf-8")
    room = home.room(STRAND)
    out = room / "port" / "out"
    out.mkdir(parents=True)
    lease = _fact("lease", "lease-1", {"strand": STRAND, "gen": 1, "thread": "t"}, by="loom:ab12")
    (out / "note.md").write_text(
        "---\nkind: letter\nid: s/body\nto: thread:t\nfrom: "
        + STRAND
        + "\nlabel: taint=0; audience=self\n---\nI say I am clean\n",
        encoding="utf-8",
    )
    facts = _ingest(home, [lease], ttl=9999)
    assert all(fact.kind != "label.tainted" for fact in facts)
    assert strand_label(facts, STRAND, jack_log="").taint is False
    (out / "taint-abcde.json").write_text('{"tool":"WebFetch"}\n', encoding="utf-8")
    facts = _ingest(home, facts, ttl=9999)
    assert any(fact.kind == "label.tainted" and fact.data.get("tool") == "WebFetch" for fact in facts)
    assert strand_label(facts, STRAND, jack_log="").taint is True
    assert not (out / "taint-abcde.json").exists()


def test_clean_schema_zeroes_taint_and_anything_else_keeps_it(tmp_path: Path) -> None:
    home, room = _room(tmp_path)
    (room / "note.md").write_text("seen\n", encoding="utf-8")
    (room / "port" / "jack-errors.log").write_text("boom\n", encoding="utf-8")

    def send(**kwargs) -> dict:
        letter_id = write_send(room, to="channel:fake", sender="s1", **kwargs)
        text = (room / "port" / "out" / f"{letter_id.split('/')[-1]}.md").read_text(encoding="utf-8")
        front = parse_frontmatter(text)
        fact = fact_from_port(front, "s1", 1, room=room)
        return {"front": front, "fact": fact}

    good = send(clean=True, kind="found", refs=["note.md"], body="")
    assert good["fact"].data["label"]["taint"] is False
    assert good["fact"].data["body"] == ""
    assert "taint=0" in good["front"]["label"]

    url = send(clean=True, kind="done", refs=["https://example.com/a"], body="")
    assert url["fact"].data["label"]["taint"] is False

    for kwargs in (
        {"clean": True, "kind": "nope", "refs": ["note.md"], "body": ""},
        {"clean": True, "kind": "found", "refs": ["missing.md"], "body": ""},
        {"clean": True, "kind": "question", "refs": ["note.md"], "body": "free text"},
        {"clean": True, "kind": "found", "refs": ["http://example.com/a"], "body": ""},
        {"clean": True, "kind": "proposal", "refs": [], "body": ""},
    ):
        bad = send(**kwargs)
        assert bad["fact"].data["label"]["taint"] is True, kwargs


def test_send_clean_cli(tmp_path: Path) -> None:
    _home, room = _room(tmp_path)
    (room / "note.md").write_text("seen\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.fspath(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [
            sys.executable, "-m", "brr.loom.runtime", "send",
            "--room", os.fspath(room), "--to", "channel:fake",
            "--clean", "--kind", "found", "--ref", "note.md",
        ],
        env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    found = list((room / "port" / "out").glob("*.md"))
    assert len(found) == 1
    front = parse_frontmatter(found[0].read_text(encoding="utf-8"))
    assert front["clean"] == "found"
    assert "taint=0" in front["label"]
