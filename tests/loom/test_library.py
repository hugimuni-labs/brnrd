"""A library page names a person's folder or repo; the wake names every page on main."""

from __future__ import annotations

import socket
from pathlib import Path

from brr.loom.runtime import library
from brr.loom.runtime.__main__ import main as cli
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import _commit, git

from test_project_repos import LABEL, _home, _log, _room, _seat, _source, _started, _tick


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
                "--for", "her working notes") == 0
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
                "--for", "her working notes") == 0
    assert _add(root, "old", str(gone), "--kind", "known", "--whose", "Ada", "--for", "old kb") == 0
    gone.rmdir()
    _seat(root, "work", [])
    home = Home(root)
    bodies: dict = {}
    _tick(home, bodies, Router(home))
    assert _started(root), _log(root)
    wake = (_room(root) / "port" / "wake.md").read_text()
    assert f"kept notes: Ada's at {kb.resolve()}; I commit here; for: her working notes\n" in wake
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
