"""A library page names a person's folder or repo; the wake names every page on main."""

from __future__ import annotations

import socket
from pathlib import Path

from brr.loom.runtime import library
import json
import time

from brr.loom.runtime.__main__ import main as cli
from brr.loom.runtime.jack import execute
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import _commit, git

from test_project_repos import LABEL, _home, _kill, _log, _room, _seat, _source, _started, _tick


def _labelled(self_dir: Path) -> None:
    """A test home's self is main itself: give the verb's commit the trailer a push would carry."""
    message = git(self_dir, "log", "-1", "--format=%B").stdout.strip()
    git(self_dir, "commit", "--amend", "--only", "-q", "-m", message + LABEL)


def _add(root: Path, *args: str) -> int:
    code = cli(["lib", "add", *args, "--room", str(root / "self")])
    if code == 0:
        _labelled(root / "self")
    return code


def test_lib_add_writes_a_page_and_commits_only_it(tmp_path: Path, capsys) -> None:
    root = _home(tmp_path)
    kb = tmp_path / "kb"
    kb.mkdir()
    (root / "self" / "stray.md").write_text("someone else's\n")
    git(root / "self", "add", "stray.md")
    assert _add(root, "notes", str(kb), "--kind", "kept", "--whose", "Ada",
                "--for", "her working notes", "--git-init") == 0
    shown = git(root / "self", "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert shown == ["library/notes.md"]
    assert git(root / "self", "diff", "--cached", "--name-only").stdout.split() == ["stray.md"]
    entry = library.parse("notes", (root / "self/library/notes.md").read_text())
    assert entry.path == str(kb.resolve()) and entry.kind == "kept"
    assert entry.machine == socket.gethostname().split(".")[0]
    assert entry.inode == f"{kb.stat().st_dev}:{kb.stat().st_ino}"


def test_lib_add_refuses_a_missing_folder_and_a_second_page(tmp_path: Path, capsys) -> None:
    root = _home(tmp_path)
    assert _add(root, "gone", str(tmp_path / "nope"), "--kind", "known", "--whose", "Ada",
                "--for", "x") == 1
    assert "not a folder" in capsys.readouterr().err
    assert _add(root, "kb", str(tmp_path), "--kind", "known", "--whose", "Ada", "--for", "x") == 0
    assert _add(root, "kb", str(tmp_path), "--kind", "known", "--whose", "Ada", "--for", "x") == 1
    assert "exists" in capsys.readouterr().err


def test_lib_drop_removes_the_page_and_leaves_the_folder(tmp_path: Path) -> None:
    root = _home(tmp_path)
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text("mine\n")
    assert _add(root, "kb", str(kb), "--kind", "known", "--whose", "Ada", "--for", "x") == 0
    assert cli(["lib", "drop", "kb", "--room", str(root / "self")]) == 0
    assert not (root / "self/library/kb.md").exists()
    assert (kb / "a.md").read_text() == "mine\n"


def test_the_wake_names_each_library_entry(tmp_path: Path) -> None:
    root = _home(tmp_path)
    kb = tmp_path / "kb"
    kb.mkdir()
    gone = tmp_path / "old"
    gone.mkdir()
    assert _add(root, "notes", str(kb), "--kind", "kept", "--whose", "Ada",
                "--for", "her working notes", "--git-init") == 0
    assert _add(root, "old", str(gone), "--kind", "known", "--whose", "Ada", "--for", "old kb") == 0
    gone.rmdir()
    _seat(root, "work", [])
    home = Home(root)
    bodies: dict = {}
    _tick(home, bodies, Router(home))
    assert _started(root), _log(root)
    wake = (_room(root) / "port" / "wake.md").read_text()
    shown = library._home_short(str(kb.resolve()))
    kept = next(line for line in wake.splitlines() if line.startswith("kept notes: "))
    assert len(kept) <= library.LINE_CAP
    assert (f"kept notes: Ada's at lib/notes ({shown}); I commit here; no commits, clean; "
            "for: her working notes").startswith(kept.rstrip("…"))
    assert f"known old: Ada's MISSING at {gone.resolve()}; read and work on request" in wake


def test_a_long_library_is_capped_in_the_wake(tmp_path: Path) -> None:
    root = _home(tmp_path)
    for index in range(library.SHOWN + 2):
        folder = tmp_path / f"f{index:02d}"
        folder.mkdir()
        assert _add(root, f"f{index:02d}", str(folder), "--kind", "known", "--whose", "Ada",
                    "--for", "x" * 400) == 0
    text = library.wake_text(Home(root))
    lines = text.splitlines()
    assert len(lines) == library.SHOWN + 1
    assert all(len(line) <= library.LINE_CAP for line in lines)
    assert lines[-1].startswith("and 2 more: ")


def test_a_seat_names_a_library_repo_by_name(tmp_path: Path) -> None:
    root = _home(tmp_path)
    bare, _work = _source(tmp_path)
    assert _add(root, "widget", f"file://{bare}", "--kind", "kept", "--whose", "Ada",
                "--for", "her widget") == 0
    _seat(root, "work", ["widget"])
    home = Home(root)
    bodies: dict = {}
    _tick(home, bodies, Router(home))
    assert _started(root), _log(root)
    room = _room(root)
    checkout = room / "widget"
    assert git(checkout, "branch", "--show-current").stdout.strip() == "loom/work"
    assert git(checkout, "remote", "get-url", "origin").stdout.strip() == f"file://{bare}"
    wake = (room / "port" / "wake.md").read_text()
    assert f"repo widget: {checkout} on loom/work" in wake
    assert "kept widget: Ada's file://" in wake


def test_a_seat_naming_no_library_entry_starts_no_body(tmp_path: Path) -> None:
    root = _home(tmp_path)
    _seat(root, "work", ["widget"])
    home = Home(root)
    _tick(home, {}, Router(home))
    assert not _started(root)
    notices = [f for f in read_facts(home) if "library/widget.md" in str(f.data)]
    assert notices, _log(root)


def _kept(root: Path, tmp_path: Path, name: str = "notes") -> Path:
    kb = tmp_path / name
    kb.mkdir()
    (kb / "a.md").write_text("his\n")
    assert _add(root, name, str(kb), "--kind", "kept", "--whose", "Ada", "--for", "her notes",
                "--git-init") == 0
    git(kb, "add", "a.md")
    git(kb, "commit", "-q", "-m", "his first")
    return kb


def test_a_kept_plain_folder_needs_git_init_said_out_loud(tmp_path: Path, capsys) -> None:
    root = _home(tmp_path)
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text("x" * 2048)
    assert _add(root, "kb", str(kb), "--kind", "kept", "--whose", "Ada", "--for", "x") == 1
    assert "--git-init" in capsys.readouterr().err and not (kb / ".git").exists()
    inner = kb / "sub"
    inner.mkdir()
    assert _add(root, "kb", str(kb), "--kind", "kept", "--whose", "Ada", "--for", "x", "--git-init") == 0
    assert (kb / ".git").is_dir() and "1 file(s)" in capsys.readouterr().out
    # A folder inside his repo is not a repo of its own: committing there would commit to his.
    assert _add(root, "sub", str(inner), "--kind", "kept", "--whose", "Ada", "--for", "x",
                "--git-init") == 1
    assert "inside the repo" in capsys.readouterr().err and not (inner / ".git").exists()


def test_two_committers_in_one_tree_each_commit_only_their_paths(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    kb.mkdir()
    git(kb, "init", "-q")
    (kb / "base.md").write_text("b\n")
    git(kb, "add", "base.md")
    git(kb, "commit", "-q", "-m", "base")
    (kb / "base.md").write_text("theirs\n")
    git(kb, "add", "base.md")  # the other body staged its edit and has not committed
    (kb / "mine.md").write_text("new\n")
    (kb / "his.md").write_text("untracked, his\n")
    library.commit(kb, "mine", ["mine.md"])
    assert git(kb, "show", "--name-only", "--format=", "HEAD").stdout.split() == ["mine.md"]
    assert git(kb, "diff", "--cached", "--name-only").stdout.split() == ["base.md"]
    assert "?? his.md" in git(kb, "status", "--porcelain").stdout
    library.commit(kb, "theirs", [str(kb / "base.md")])
    assert git(kb, "show", "--name-only", "--format=", "HEAD").stdout.split() == ["base.md"]
    try:
        library.commit(kb, "escape", [str(tmp_path / "elsewhere.md")])
    except library.LibraryError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("a path outside the folder was committed")


def test_a_held_index_lock_is_waited_out_once(tmp_path: Path, monkeypatch) -> None:
    kb = tmp_path / "kb"
    kb.mkdir()
    git(kb, "init", "-q")
    (kb / "a.md").write_text("a\n")
    lock = kb / ".git" / "index.lock"
    lock.write_text("")
    slept = []
    monkeypatch.setattr(library.time, "sleep", lambda s: (slept.append(s), lock.unlink()))
    library.commit(kb, "a", ["a.md"])
    assert slept == [1.0]
    assert git(kb, "log", "--format=%s").stdout.split() == ["a"]


def test_a_body_gets_its_kept_folder_at_lib_and_its_own_writes_block_one_stop(
        tmp_path: Path, monkeypatch) -> None:
    root = _home(tmp_path)
    kb = _kept(root, tmp_path)
    _seat(root, "work", [])
    home = Home(root)
    bodies: dict = {}
    try:
        _tick(home, bodies, Router(home))
        assert _started(root), _log(root)
        room = _room(root)
        assert (room / "lib" / "notes").resolve() == kb.resolve()
        assert json.loads((room / "port/library.json").read_text())[0]["name"] == "notes"
        monkeypatch.setenv("BRNRD_THREAD", "work")
        monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "91")
        before = time.time() - 5
        import os
        os.utime(room / "port/library.json", (before, before))
        (room / "lib/notes/b.md").write_text("mine\n")
        first = json.loads(execute("stop", room, "{}"))
        assert first["decision"] == "block" and "lib/notes/b.md" in first["reason"]
        assert "lib commit" in first["reason"]
        assert "lib/notes/b.md" not in execute("stop", room, "{}")  # told once
        assert cli(["lib", "commit", "notes", "-m", "b", "b.md", "--home", str(root)]) == 0
        assert git(kb, "log", "-1", "--format=%s").stdout.strip() == "b"
        assert "lib/notes" not in execute("stop", room, "{}")
    finally:
        _kill(read_facts(home))


def test_the_idle_check_opens_and_clears_kept_folder_problems(tmp_path: Path, monkeypatch) -> None:
    root = _home(tmp_path)
    kb = _kept(root, tmp_path)
    home = Home(root)
    keeper = library.Keeper(home, lambda *a: None)
    keeper.step()
    assert library.problem_line(read_facts(home), home.install_id()) == ""
    (kb / "a.md").write_text("his edit\n")
    old = time.time() - library.DIRTY_AFTER - 60
    import os
    os.utime(kb / "a.md", (old, old))
    keeper.checked = None
    keeper.step()
    line = library.problem_line(read_facts(home), home.install_id())
    assert line.startswith("library: notes 1 file(s) uncommitted, untouched since "), line
    keeper.checked = None
    keeper.step()  # still dirty: no second fact
    assert len([f for f in read_facts(home) if f.kind == "library.problem"]) == 1
    git(kb, "commit", "-q", "-am", "his")
    keeper.checked = None
    keeper.step()
    assert library.problem_line(read_facts(home), home.install_id()) == ""
    assert [f.kind for f in read_facts(home) if f.kind.startswith("library.")] == \
        ["library.problem", "library.cleared"]
    import shutil
    shutil.rmtree(kb / ".git")
    keeper.checked = None
    keeper.step()
    assert "notes no .git at" in library.problem_line(read_facts(home), home.install_id())


def _call(tool: str, cwd: Path, **given) -> str:
    return json.dumps({"tool_name": tool, "tool_input": given, "cwd": str(cwd), "tool_use_id": "t",
                       "hook_event_name": "PostToolUse"})


def test_a_known_folder_a_body_touched_is_nudged_once_at_stop(tmp_path: Path, monkeypatch) -> None:
    root = _home(tmp_path)
    taxes = tmp_path / "taxes"
    taxes.mkdir()
    (tmp_path / "taxes-old").mkdir()
    assert _add(root, "taxes", str(taxes), "--kind", "known", "--whose", "Ada", "--for", "tax returns") == 0
    _seat(root, "work", [])
    home = Home(root)
    bodies: dict = {}
    try:
        _tick(home, bodies, Router(home))
        assert _started(root), _log(root)
        room = _room(root)
        assert not (room / "lib" / "taxes").exists()  # a known folder is not linked
        monkeypatch.setenv("BRNRD_THREAD", "work")
        monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "91")
        assert "known folder" not in execute("stop", room, "{}")
        execute("post", room, _call("Bash", room, command=f"ls {tmp_path}/taxes-old", description="list"))
        assert "known folder" not in execute("stop", room, "{}")  # a neighbour's name is not the folder
        execute("post", room, _call("Read", room, file_path=str(taxes / "2025.pdf")))
        execute("post", room, _call("Read", room, file_path=str(taxes / "2024.pdf")))
        execute("post", room, _call("Bash", room, command=f"cd '{taxes}' && ls", description="list"))
        execute("post", room, _call("Edit", taxes, file_path="notes.md", old_string="a", new_string="b"))
        first = json.loads(execute("stop", room, "{}"))
        assert first["decision"] == "block"
        assert "taxes/ (read 2, wrote 1, ran 1)" in first["reason"] and "tidy" in first["reason"]
        assert str(taxes) not in (room / "port/tools.jsonl").read_text()  # the trace keeps basenames
        assert "known folder" not in execute("stop", room, "{}")  # asked once per body
        execute("post", room, _call("Read", room, file_path=str(taxes / "2023.pdf")))
        assert "known folder" not in execute("stop", room, "{}")
        later = time.time() + 5
        import os
        os.utime(room / "port/library.json", (later, later))  # the next body
        execute("post", room, _call("Grep", room, pattern="IBAN", path=str(taxes)))
        again = json.loads(execute("stop", room, "{}"))
        assert "taxes/ (read 1)" in again["reason"] and "worth noting" in again["reason"]
    finally:
        _kill(read_facts(home))
