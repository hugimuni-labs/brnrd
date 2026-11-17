"""Every place a body's work lands in is a ledger fact the loom reads back (threads/touched-places).

Each act goes through the caller that produces it: the post-tool hook for a
write, a clone and a repo created, ``publish`` for a push, ``send --repo`` for
a thread opened on a repo; the loom's idle look writes the library page and
the home wake offers what the library does not hold.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr.loom.runtime import library
from brr.loom.runtime.__main__ import main as cli
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import git

from test_library import _add, _call
from test_project_repos import _home, _kill, _log, _room, _seat, _source, _started, _tick


@pytest.fixture(autouse=True)
def _temp_is_a_place(monkeypatch):
    """pytest's folders sit under the OS temp roots, which are no place in use."""
    monkeypatch.setattr(library, "EPHEMERAL", ())


def _touched(home: Home) -> list:
    return [f for f in read_facts(home) if f.kind == "place.touched"]


def _body(root: Path, monkeypatch, thread: str = "work", sources=()) -> tuple[Home, Path]:
    _seat(root, thread, list(sources))
    home = Home(root)
    _tick(home, {}, Router(home))
    assert _started(root), _log(root)
    room = _room(root)
    monkeypatch.setenv("BRNRD_THREAD", thread)
    monkeypatch.setenv("BRNRD_ROOM", str(room))
    return home, room


def test_a_write_lands_once_per_place_with_the_letter_and_wish_that_woke_the_body(
        tmp_path: Path, monkeypatch) -> None:
    root = _home(tmp_path)
    site = tmp_path / "site"
    (site / ".git").mkdir(parents=True)
    (site / "docs").mkdir()
    home, room = _body(root, monkeypatch)
    try:
        owed = [row["id"] for row in json.loads((room / "port/owed.json").read_text())]
        assert owed  # the seat's letter woke it
        wished = json.loads((room / "port" / library.WISHES).read_text())
        execute("post", room, _call("Write", room, file_path=str(site / "docs/a.md"), content="x"))
        execute("post", room, _call("Edit", site, file_path="docs/b.md", old_string="a", new_string="b"))
        execute("post", room, _call("Write", room, file_path=str(room / "tmp/scratch.md"), content="x"))
        execute("post", room, _call("Write", room, file_path=str(root / "loom/x.md"), content="x"))
        execute("post", room, _call("Read", room, file_path=str(tmp_path / "elsewhere/c.md")))
        facts = _touched(home)
        assert [f.data["place"] for f in facts] == [library.folder_key(str(site.resolve()))]
        data = facts[0].data
        assert data["act"] == "wrote" and data["thread"] == "work" and data["strand"] == room.name
        assert data["letters"] == owed and data["wishes"] == wished
        plain = tmp_path / "plain" / "deep"
        plain.mkdir(parents=True)
        execute("post", room, _call("Write", room, file_path=str(plain / "n.md"), content="x"))
        assert _touched(home)[-1].data["place"] == library.folder_key(str(plain.resolve()))
    finally:
        _kill(read_facts(home))


def test_a_clone_and_a_repo_created_in_the_shell_are_places(tmp_path: Path, monkeypatch) -> None:
    root = _home(tmp_path)
    home, room = _body(root, monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    try:
        execute("post", room, _call("Bash", out, command="git clone --depth 1 git@github.com:Ada/Widget.git w && ls",
                                    description="clone"))
        execute("post", room, json.dumps({
            "tool_name": "Bash", "cwd": str(room), "hook_event_name": "PostToolUse",
            "tool_input": {"command": "gh repo create fresh --private", "description": "make"},
            "tool_response": {"stdout": "✓ Created repository ada/fresh on GitHub\n  https://github.com/ada/fresh\n"}}))
        execute("post", room, _call("Bash", room, command="cd tmp; gh repo clone ada/other", description="clone"))
        found = {(f.data["act"], f.data["place"]) for f in _touched(home)}
        assert found == {("cloned", "repo:github.com/ada/widget"),
                         ("cloned", library.folder_key(str((out / "w").resolve()))),
                         ("created", "repo:github.com/ada/fresh"),
                         ("cloned", "repo:github.com/ada/other")}  # its folder is in the room
    finally:
        _kill(read_facts(home))


def test_publish_and_a_thread_opened_on_a_repo_are_places(tmp_path: Path, monkeypatch) -> None:
    from brr.loom.runtime.publish import publish
    from test_project_repos import _note

    root = _home(tmp_path)
    bare, _work = _source(tmp_path)
    assert _add(root, "widget", f"file://{bare}", "--kind", "known", "--whose", "Ada", "--for", "her widget") == 0
    home, room = _body(root, monkeypatch, sources=["widget"])
    try:
        _note(room / "widget", room, "fix.txt", "fixed\n")
        publish(room, "widget")
        assert cli(["send", "--room", str(room), "--to", "thread:next", "--wish", "new:More widget",
                    "--repo", "widget", "More widget\nDetails"]) == 0
        rows = {f.data["act"]: f.data for f in _touched(home)}
        key = library.repo_key(f"file://{bare}")
        assert rows["published"]["place"] == key and rows["published"]["thread"] == "work"
        assert rows["opened"]["place"] == key and rows["opened"]["thread"] == "next"
        opened_for = git(root / "self", "show", "main:threads/next/README.md").stdout
        assert rows["opened"]["wishes"] and all(w in opened_for for w in rows["opened"]["wishes"])
    finally:
        _kill(read_facts(home))


def test_the_loom_writes_the_work_on_the_library_page_and_keeps_the_map(tmp_path: Path, monkeypatch) -> None:
    root = _home(tmp_path)
    kb = tmp_path / "kb"
    kb.mkdir()
    assert _add(root, "kb", str(kb), "--kind", "known", "--whose", "Ada", "--for", "her notes") == 0
    home, room = _body(root, monkeypatch)
    keeper = library.Keeper(home, lambda *a: None)
    try:
        execute("post", room, _call("Write", room, file_path=str(kb / "sub/a.md"), content="x"))
        keeper.step()
        text = git(root / "self", "show", "main:library/kb.md").stdout
        assert "What lives where, the person's conventions" in text  # the body's map stays
        head, _mark, block = text.partition(f"\n{library.WORK}\n")
        assert block.count("\n- ") == 1 and "wrote · thread work" in block, text
        tip = git(root / "self", "rev-parse", "main").stdout
        keeper.checked = None
        keeper.step()  # nothing new: no second commit
        assert git(root / "self", "rev-parse", "main").stdout == tip
    finally:
        _kill(read_facts(home))


def test_the_home_wake_offers_an_unlisted_place_once_and_a_dismissal_holds(
        tmp_path: Path, monkeypatch) -> None:
    root = _home(tmp_path)
    kb = tmp_path / "kb"
    kb.mkdir()
    assert _add(root, "kb", str(kb), "--kind", "known", "--whose", "Ada", "--for", "her notes") == 0
    stray = tmp_path / "stray"
    (stray / "inner").mkdir(parents=True)
    home, room = _body(root, monkeypatch, thread="inbox")
    assert home.main_thread() == "inbox"
    assert "touched, not in the library" not in (room / "port/wake.md").read_text()
    execute("post", room, _call("Write", room, file_path=str(kb / "a.md"), content="x"))
    execute("post", room, _call("Write", room, file_path=str(stray / "s.md"), content="x"))
    execute("post", room, _call("Write", room, file_path=str(stray / "inner/t.md"), content="x"))
    execute("post", room, json.dumps({"tool_name": "Bash", "cwd": str(room), "tool_input": {
        "command": "gh repo create ada/fresh", "description": "make"}, "tool_response": {"stdout": ""}}))
    _kill(read_facts(home))
    _tick(home, {}, Router(home))  # the home thread's next body
    try:
        again = [f for f in _started(root) if f.data.get("gen") == 2]
        assert again, _log(root)
        wake = root / "rooms" / again[0].data["strand"] / "port/wake.md"
        lines = [line for line in wake.read_text().splitlines()
                 if line.startswith("touched, not in the library:")]
        assert len(lines) == 2, lines  # kb is listed; stray/inner is inside stray
        assert any(f"lib add stray {stray.resolve()} --kind known" in line for line in lines)
        assert any("lib dismiss repo:github.com/ada/fresh" in line for line in lines)
        facts = read_facts(home)
        assert not library.unlisted(facts, library.entries(home)[0])  # said once
        assert cli(["lib", "dismiss", "repo:github.com/ada/fresh", "--room", str(room)]) == 0
        execute("post", room, json.dumps({"tool_name": "Bash", "cwd": str(room), "tool_input": {
            "command": "gh repo clone ada/fresh", "description": "x"}}))
        assert not library.unlisted(read_facts(home), library.entries(home)[0])
    finally:
        _kill(read_facts(home))


@pytest.mark.parametrize("spelling", [
    "https://github.com/Ada/Widget", "https://github.com/ada/widget.git", "git@github.com:Ada/Widget.git",
    "ssh://git@github.com/ada/widget/", "https://token@GitHub.com/ada/widget"])
def test_one_repo_is_one_place_however_it_is_spelled(spelling: str) -> None:
    assert library.repo_key(spelling) == "repo:github.com/ada/widget"
