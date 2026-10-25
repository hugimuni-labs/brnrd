"""Every launcher the loom writes runs ``$BRNRD_ROOT/bin/brnrd``, which runs ``current``."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import entry
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import SRC, _env, _prepare
from brr.loom.runtime.merge_driver import driver_command
from brr.loom.runtime.project import sender_threads
from brr.loom.runtime.selfrepo import git, room

from _step import write_thread

STRAND = "s-aaaa-entry"
OLD_PYTHON = "/Users/someone/Source/brnrd/.venv/bin/python3"


def fake_prefix(tmp_path: Path, name: str) -> Path:
    """A prefix whose bin/python says which one ran, and with what."""
    prefix = tmp_path / name
    (prefix / "bin").mkdir(parents=True)
    python = prefix / "bin" / "python"
    python.write_text(f'#!/bin/sh\necho {name} "$@"\n')
    python.chmod(0o755)
    return prefix


def test_the_shim_is_written_once_and_rewritten_only_when_it_differs():
    assert entry.write_shim() is True
    shim = entry.shim()
    before = shim.stat()
    assert entry.write_shim() is False
    assert shim.stat().st_ino == before.st_ino and shim.stat().st_mtime_ns == before.st_mtime_ns
    assert len(shim.read_text().splitlines()) <= 10
    shim.write_text("#!/bin/sh\nexit 1\n")
    assert entry.write_shim() is True
    assert shim.read_text() == entry.script() and os.access(shim, os.X_OK)


def test_the_shim_runs_whatever_current_points_at(tmp_path, monkeypatch):
    entry.write_shim()
    entry.point(fake_prefix(tmp_path, "one"))
    run = lambda argv: subprocess.run(argv, capture_output=True, text=True)
    assert run([str(entry.shim()), "runtime", "jack"]).stdout == "one -m brr runtime jack\n"
    entry.point(fake_prefix(tmp_path, "two"))
    assert run([str(entry.shim()), "runtime", "jack"]).stdout == "two -m brr runtime jack\n"

    # The loom's source re-exec goes through the shim, so it lands on the new current;
    # sys.executable would not, since Python resolves the link (product-shape §2.1).
    monkeypatch.setattr(sys, "orig_argv", ["/old/bin/python", "-m", "brr", "runtime", "loom", "--home", "/h"])
    argv = entry.reexec_argv()
    assert argv == [str(entry.shim()), "runtime", "loom", "--home", "/h"]
    entry.point(fake_prefix(tmp_path, "three"))
    assert run(argv).stdout == "three -m brr runtime loom --home /h\n"

    # A loom started the old way re-execs the old way.
    monkeypatch.setattr(sys, "orig_argv", ["/old/bin/python", "-m", "brr.loom.runtime", "loom"])
    assert entry.reexec_argv() is None


def test_a_missing_current_says_so():
    entry.write_shim()
    proc = subprocess.run([str(entry.shim()), "runtime"], capture_output=True, text=True)
    assert proc.returncode == 127 and "run brnrd loom install" in proc.stderr


def test_ensure_links_a_missing_or_broken_current_and_keeps_a_working_one(tmp_path, monkeypatch):
    here = fake_prefix(tmp_path, "here")
    monkeypatch.setattr(sys, "executable", str(here / "bin" / "python"))
    done = entry.ensure()
    assert done["to"] == str(here) and "was missing" in done["why"]
    assert entry.ensure() is None

    gone = fake_prefix(tmp_path, "gone")
    entry.point(gone)
    (gone / "bin" / "python").unlink()
    done = entry.ensure()
    assert done["to"] == str(here) and str(gone) in done["why"]

    other = fake_prefix(tmp_path, "other")
    entry.point(other)
    assert entry.ensure() is None
    assert entry.current().readlink() == other


def test_ensure_refuses_a_prefix_without_python(tmp_path, monkeypatch):
    entry.current().unlink(missing_ok=True)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bare" / "bin" / "python3"))
    with pytest.raises(OSError, match="no bin/python"):
        entry.ensure()


@pytest.fixture
def home(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "quit-now")
    return root


def test_a_room_made_before_the_shim_converges_at_its_next_wake(home, tmp_path):
    loom = Home(home, install="aaaa")
    append(loom, Fact(kind="lease", by="loom:aaaa", id="lease:brnrd:1", data={
        "thread": "brnrd", "strand": STRAND, "gen": 1}))
    clone = room(home, STRAND)
    hooks = clone.parent / ".claude" / "settings.json"
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{
        "type": "command", "command": f"{OLD_PYTHON} -m brr.loom.runtime jack --event stop"}]}]}}))
    git(clone, "config", "merge.loom-readme.driver",
        f"PYTHONPATH=/old/src {OLD_PYTHON} -m brr.loom.runtime merge-driver %O %A %B %P")
    entry.current().unlink(missing_ok=True)
    facts = read_facts(loom)

    prepared = _prepare(loom, STRAND, "brnrd", 1, [], sender_threads(facts), facts, "hook")

    # The shim and current exist, and the wake wrote down that it linked current.
    assert entry.sound()
    [fell] = [f for f in read_facts(loom) if f.kind == "room.fallback"]
    assert fell.data["want"] == "entry" and "was missing" in fell.data["why"]
    # The merge driver in the clone's git config runs through the shim.
    driver = git(clone, "config", "--get", "merge.loom-readme.driver").stdout.strip()
    assert driver == driver_command() and OLD_PYTHON not in driver
    assert f"{entry.shim()} runtime merge-driver" in driver
    # The wake text names the shim's verb, not an interpreter.
    wake = (prepared / "port" / "loom.md").read_text()
    assert "`brnrd runtime send --re" in wake and "python -m brr" not in wake
    assert sys.executable not in wake and str(entry.prefix()) not in wake

    # The claude body rewrites the room's hooks from the wake's env.
    env = _env(prepared)
    assert env["PATH"].split(os.pathsep)[0] == str(entry.shim().parent)
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    (fake / "claude").write_text("#!/bin/sh\nexit 0\n")
    (fake / "claude").chmod(0o755)
    env["PATH"] = f"{fake}{os.pathsep}{env['PATH']}"
    run = Path(SRC) / "brr" / "loom" / "runtime" / "bodies" / "claude" / "run"
    proc = subprocess.run([sys.executable, str(run), str(prepared), "opus"], env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    text = hooks.read_text()
    commands = [h["command"] for group in json.loads(text)["hooks"].values()
                for entry_ in group for h in entry_["hooks"]]
    assert len(commands) == 6
    assert all(c.startswith(f"{entry.shim()} runtime jack --room ") for c in commands)
    for leak in (OLD_PYTHON, sys.executable, str(entry.prefix())):
        assert leak not in text
