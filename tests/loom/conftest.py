"""Puts this directory on the path so the suite can share ``_step``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


import os
import pytest


@pytest.fixture(autouse=True)
def subprocess_tree(monkeypatch):
    """Product CLI subprocesses must run this tree, including in a worktree."""
    src = str(Path(__file__).resolve().parents[2] / 'src')
    prior = os.environ.get('PYTHONPATH', '')
    monkeypatch.setenv('PYTHONPATH', src + (os.pathsep + prior if prior else ''))


@pytest.fixture(autouse=True)
def reap_bodies(tmp_path):
    """A body outlives its loom by design, so a test's loom halting leaves it
    running. Kill every body still holding its room under this test's tmp_path;
    the room's flock proves the recorded pid is still that body."""
    yield
    import signal
    from brr.loom.runtime.home import Home
    from brr.loom.runtime.ledger import read_facts
    from brr.loom.runtime.loom import body_alive
    for room in tmp_path.glob("**/rooms/*"):
        if not body_alive(room):
            continue
        try:
            facts = read_facts(Home(room.parent.parent))
        except Exception:
            continue
        pids = {f.data.get("pid") for f in facts
                if f.kind == "body.started" and f.data.get("strand") == room.name}
        for pid in pids - {None}:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
