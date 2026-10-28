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
def isolate_hosting_body(monkeypatch):
    """A test starts as an operator, without the resident hosting pytest.

    BRNRD_ROOM changes caller authority; BRNRD_STOP_TIMEOUT and other body
    settings change adapter inputs. The room forge shim on PATH can also
    inject credentials even after all token variables have been removed.
    Tests that need a body set its environment explicitly.
    """
    def room_bin(entry):
        path = Path(entry).resolve()
        return (path.name == "bin" and path.parent.name == "port"
                and path.parent.parent.parent.name == "rooms")

    path = os.environ.get("PATH", os.defpath)
    monkeypatch.setenv("PATH", os.pathsep.join(
        entry for entry in path.split(os.pathsep) if not room_bin(entry)))
    for name in tuple(os.environ):
        # BRNRD_LOOM_SOURCE is the suite's own (tests/conftest.py); BRNRD_ROOT likewise (entry shim): without it a test loom follows this checkout.
        if name.startswith("BRNRD_") and name not in ("BRNRD_LOOM_SOURCE", "BRNRD_ROOT"):
            monkeypatch.delenv(name)


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


@pytest.fixture(autouse=True)
def _private_jack_clock(monkeypatch):
    """jack gets its own copy of `time` for each test.

    Tests drive the stop hook's clock with `monkeypatch.setattr(jack.time,
    "sleep", ...)`. `jack.time` is the shared module, so that patched every
    thread's sleep: under xdist a thread a previous test left running ticked
    a fake clock to 138 s (ci-speed, 2026-10-28, run 40772067022).
    """
    import time
    import types

    from brr.loom.runtime import jack

    clock = types.ModuleType("time")
    clock.__dict__.update(time.__dict__)
    monkeypatch.setattr(jack, "time", clock)
