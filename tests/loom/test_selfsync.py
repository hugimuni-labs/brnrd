"""Self-sync through the jack's own hook command: land at stop, pull at a boundary, conflicts as notices."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import _ingest, _record
from brr.loom.runtime.selfrepo import git, room as make_room

from _step import notices, write_thread

ROOT = Path(__file__).resolve().parents[2]
THREAD = "ta"
_DISCOVERY = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR",
              "GIT_NAMESPACE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"}


def _home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    write_thread(root, THREAD, "# Thread ta.", "accept")
    # 20-labels fails closed: the test's own identity pushes the fixture commits.
    self_dir = root / "self"
    email = git(self_dir, "config", "--global", "user.email").stdout.strip()
    listing = self_dir / "core" / "loom" / "people-commit"
    listing.write_text(listing.read_text() + email + "\n")
    (self_dir / "plain.txt").write_text("base\n")
    git(self_dir, "add", "-A")
    git(self_dir, "commit", "-m", "fixture: plain file\n\nLoom-Label: taint=0; audience=self\n")
    return root


def _strand(root: Path, tag: str) -> tuple[str, Path]:
    home = Home(root)
    strand = f"s-{home.install_id()}-{tag * 6}"
    clone = make_room(root, strand)
    port = clone.parent / "port"
    (port / "in").mkdir(parents=True, exist_ok=True)
    (port / "out").mkdir(exist_ok=True)
    # No cache lifetime: the hold ends at once, after the landing.
    (port / "wait").write_text("0\n")
    _record(home, "lease", {"thread": THREAD, "strand": strand, "gen": 1,
                            "install": home.install_id()}, f"lease:{THREAD}:{tag}")
    return strand, clone.parent


def _jack(room: Path, event: str, payload: dict | None = None) -> subprocess.Popen:
    """The command the body's hooks run, in its own process."""
    # Repo discovery stays out; identity stays in, as a body's own environment carries it.
    env = {k: v for k, v in os.environ.items() if k not in _DISCOVERY and k != "BRNRD_THREAD"}
    env["PYTHONPATH"] = os.fspath(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["BRNRD_STOP_TIMEOUT"] = "100"
    return subprocess.Popen(
        [sys.executable, "-m", "brr.loom.runtime", "jack", "--event", event, "--room", os.fspath(room)],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _run(room: Path, event: str, payload: dict | None = None) -> str:
    proc = _jack(room, event)
    out, err = proc.communicate(json.dumps(payload or {}), timeout=120)
    assert proc.returncode == 0, err
    assert not (room / "port" / "jack-errors.log").exists(), (room / "port" / "jack-errors.log").read_text()
    assert not (room / "port" / "sync-errors.log").exists(), (room / "port" / "sync-errors.log").read_text()
    return out


def _state(room: Path) -> dict:
    return json.loads((room / "port" / "jack-state.json").read_text())


def _main_has(root: Path, path: str) -> str:
    proc = git(root / "self", "show", f"main:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else ""


def test_stop_lands_tracked_changes_and_leaves_untracked_files(tmp_path):
    root = _home(tmp_path)
    _strand_id, room = _strand(root, "a")
    clone = room / "self"
    (clone / "plain.txt").write_text("base\nfrom a\n")
    (clone / "people" / "someone").mkdir(parents=True)
    (clone / "people" / "someone" / "channels.toml").write_text("not ours\n")

    _run(room, "stop")

    assert _main_has(root, "plain.txt") == "base\nfrom a\n"
    assert _main_has(root, "people/someone/channels.toml") == ""
    assert (clone / "people" / "someone" / "channels.toml").read_text() == "not ours\n"
    assert git(clone, "status", "--porcelain", "--untracked-files=no").stdout == ""
    sha = git(root / "self", "rev-parse", "--short=7", "main").stdout.strip()
    assert _state(room)["sync"] == f"landed {sha}"
    # Shown where the body looks: the status line.
    from brr.loom.runtime.port import render_boundary
    from brr.loom.runtime.selfsync import sync_line
    assert f" · sync landed {sha}" in render_boundary(
        "s", 1, THREAD, [], set(), {}, sync=sync_line(room))


def test_a_stop_with_nothing_to_land_does_nothing(tmp_path):
    root = _home(tmp_path)
    _strand_id, room = _strand(root, "a")
    before = git(root / "self", "rev-parse", "main").stdout
    _run(room, "stop")
    assert git(root / "self", "rev-parse", "main").stdout == before
    assert "sync" not in _state(room) if (room / "port" / "jack-state.json").exists() else True


def test_two_rooms_stopping_at_once_both_land_and_never_wedge_the_self(tmp_path):
    # #2370: two pushes into the working self at once once left a mixed
    # index staged there, and every later push was refused.
    root = _home(tmp_path)
    rooms = [_strand(root, tag)[1] for tag in "ab"]
    for turn in range(3):
        for index, room in enumerate(rooms):
            scar = room / "self" / "memory" / "scars" / f"race-{index}-{turn}.md"
            scar.parent.mkdir(parents=True, exist_ok=True)
            scar.write_text(f"{index} {turn}\n")
            # Tracked: the body added it, as a new scar is.
            git(room / "self", "add", "--", os.fspath(scar.relative_to(room / "self")))
        procs = [_jack(room, "stop") for room in rooms]
        for proc in procs:
            _out, err = proc.communicate("{}", timeout=180)
            assert proc.returncode == 0, err
        for index, room in enumerate(rooms):
            assert not (room / "port" / "sync-errors.log").exists(), (room / "port" / "sync-errors.log").read_text()
            assert _state(room)["sync"].startswith("landed "), (turn, index, _state(room))
            assert _main_has(root, f"memory/scars/race-{index}-{turn}.md") == f"{index} {turn}\n"
        assert git(root / "self", "status", "--porcelain").stdout == "", turn
        assert git(root / "self", "diff", "--cached", "--quiet", check=False).returncode == 0, turn


def test_a_boundary_pulls_a_clean_clone_and_leaves_a_dirty_one(tmp_path):
    root = _home(tmp_path)
    (_a, writer), (_b, clean), (_c, dirty) = (_strand(root, tag) for tag in "abc")
    (writer / "self" / "plain.txt").write_text("base\nfrom a\n")
    _run(writer, "stop")
    (dirty / "self" / "plain.txt").write_text("mid-edit\n")
    dirty_head = git(dirty / "self", "rev-parse", "HEAD").stdout

    said = json.loads(_run(clean, "post", {"tool_name": "Bash"}))
    context = said["hookSpecificOutput"]["additionalContext"]
    assert "self-sync: pulled" in context and "plain.txt" in context
    assert (clean / "self" / "plain.txt").read_text() == "base\nfrom a\n"
    assert _state(clean)["sync"].startswith("pulled ")

    _run(dirty, "post", {"tool_name": "Bash"})
    assert git(dirty / "self", "rev-parse", "HEAD").stdout == dirty_head
    assert (dirty / "self" / "plain.txt").read_text() == "mid-edit\n"

    # Nothing moved since: the next boundary says nothing.
    assert _run(clean, "post", {"tool_name": "Bash"}) == ""


@pytest.mark.parametrize("end", ["stop", "post"])
def test_a_conflict_the_drivers_cannot_resolve_is_a_notice(tmp_path, end):
    root = _home(tmp_path)
    (_a, first), (strand, second) = (_strand(root, tag) for tag in "ab")
    (first / "self" / "plain.txt").write_text("from a\n")
    _run(first, "stop")
    clone = second / "self"
    (clone / "plain.txt").write_text("from b\n")
    if end == "post":
        # A clean clone with an unlanded commit: the pull is the one that meets main.
        git(clone, "commit", "-am", "b's own commit")
    own = None if end == "stop" else git(clone, "rev-parse", "HEAD").stdout

    _run(second, end, {"tool_name": "Bash"})

    from brr.loom.runtime.merge import _rebasing
    assert not _rebasing(clone)
    assert (clone / "plain.txt").read_text() == "from b\n"
    if own is not None:
        assert git(clone, "rev-parse", "HEAD").stdout == own
    assert _main_has(root, "plain.txt") == "from a\n"
    assert _state(second)["sync"] == "conflict: plain.txt"
    assert list((second / "port" / "out").glob("sync-*.json"))

    home = Home(root)
    facts = _ingest(home, read_facts(home))
    found = [n for n in notices(facts) if "plain.txt" in n.data["body"]]
    assert len(found) == 1, [n.data for n in notices(facts)]
    assert found[0].data["to"] == f"thread:{THREAD}"
    assert strand in found[0].data["body"] and "send-self" in found[0].data["body"]
    assert not list((second / "port" / "out").glob("sync-*.json"))

    # The same condition again is the same notice, not a second one.
    _run(second, end, {"tool_name": "Bash"})
    facts = _ingest(home, read_facts(home))
    assert len([n for n in notices(facts) if "plain.txt" in n.data["body"]]) == 1


def test_off_leaves_the_clone_to_the_body(tmp_path):
    root = _home(tmp_path)
    with (root / "loom" / "config.toml").open("a") as handle:
        handle.write('self_sync = "off"\n')
    _strand_id, room = _strand(root, "a")
    (room / "self" / "plain.txt").write_text("base\nfrom a\n")
    _run(room, "stop")
    assert _main_has(root, "plain.txt") == "base\n"
    assert git(room / "self", "status", "--porcelain", "--untracked-files=no").stdout != ""


def test_a_stop_with_a_delegate_at_work_lands_nothing(tmp_path):
    root = _home(tmp_path)
    _strand_id, room = _strand(root, "a")
    (room / "self" / "plain.txt").write_text("half made\n")
    (room / "port" / "live-delegates").mkdir()
    (room / "port" / "live-delegates" / "agent-1").touch()
    _run(room, "stop")
    assert _main_has(root, "plain.txt") == "base\n"
    assert _state(room)["sync"] == "held: a delegate is at work"


def test_an_untracked_file_main_would_overwrite_is_a_notice_not_a_loss(tmp_path):
    root = _home(tmp_path)
    (_a, first), (strand, second) = (_strand(root, tag) for tag in "ab")
    (first / "self" / "new.txt").write_text("tracked by a\n")
    git(first / "self", "add", "new.txt")
    _run(first, "stop")
    (second / "self" / "new.txt").write_text("b's own, untracked\n")

    _run(second, "post", {"tool_name": "Bash"})

    assert (second / "self" / "new.txt").read_text() == "b's own, untracked\n"
    assert _state(second)["sync"].startswith("pull refused")
    home = Home(root)
    facts = _ingest(home, read_facts(home))
    found = [n for n in notices(facts) if strand in n.data["body"]]
    assert len(found) == 1 and "new.txt" in found[0].data["body"], [n.data for n in found]
