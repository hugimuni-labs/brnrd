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
def isolate_gh_login(monkeypatch, tmp_path_factory):
    """No test loom sees this machine's gh login: a loom start sets an unset
    forge_token to ``gh auth token`` when gh answers (gates.converge). An
    empty GH_CONFIG_DIR is not enough on macOS, where gh reads the keychain,
    so a signed-out gh goes first on PATH. A test that wants a signed-in gh
    puts its own fake ahead of it."""
    bin_dir = tmp_path_factory.mktemp("gh-signed-out")
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\necho 'gh: not logged in (test)' >&2\nexit 4\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', os.defpath)}")
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.delenv(name, raising=False)


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
