"""Tests for src/brr/pause.py and its CLI/hooks wiring.

Real subprocess children throughout, driven with `os.kill` and read back
with `ps -o stat` (T = stopped) — a mocked signal would only prove the mock
fires, not that SIGSTOP/SIGCONT/SIGTERM/SIGKILL do what this feature
depends on them doing.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from brr import hooks, pause
from brr.cli import main


def _ps_state(pid: int) -> str:
    out = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True, text=True, check=False,
    )
    return out.stdout.strip()[:1]


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    ok = predicate()
    while not ok and time.monotonic() < deadline:
        time.sleep(interval)
        ok = predicate()
    return ok


def _spawn_chain(tmp_path):
    """A root process with one child, which itself has one child (`sleep`).

    Returns the root `Popen`; the root itself is never a pause target (a
    run's Shell process is never touched) — its child is the "qualifying
    child" and that child's own child is the grandchild both stop steps
    are supposed to reach.
    """
    inner = tmp_path / "inner.py"
    inner.write_text("import subprocess, time\n"
                      "subprocess.Popen(['sleep', '30'])\n"
                      "time.sleep(30)\n")
    outer = tmp_path / "outer.py"
    outer.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([{sys.executable!r}, {str(inner)!r}])\n"
        "time.sleep(30)\n"
    )
    root = subprocess.Popen([sys.executable, str(outer)])
    return root


@pytest.fixture
def chain(tmp_path):
    root = _spawn_chain(tmp_path)
    try:
        yield root
    finally:
        if root.poll() is None:
            root.kill()
        try:
            root.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # Reap anything still alive under the root — best-effort, tests
        # must not leak stopped/zombie processes into the rest of the suite.
        for proc in pause.descendants(root.pid):
            try:
                import os
                import signal
                os.kill(proc.pid, signal.SIGCONT)
                os.kill(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def _child_and_grandchild(root_pid, timeout=5.0):
    """Poll until the chain has grown its child + grandchild, return both pids."""
    found = {}

    def _ready():
        kids = pause.descendants(root_pid)
        if len(kids) >= 2:
            found["kids"] = kids
            return True
        return False

    assert _wait_until(_ready, timeout=timeout), "chain never grew a grandchild"
    kids = found["kids"]
    child = next(p for p in kids if p.ppid == root_pid)
    grandchild = next(p for p in kids if p.ppid == child.pid)
    return child, grandchild


class TestPausableChildren:
    def test_filters_by_since_ts_and_brnrd_argv(self):
        now = time.time()
        old = pause.ProcInfo(pid=1, ppid=100, started_at=now - 100, command="sleep 30")
        unparsed = pause.ProcInfo(pid=2, ppid=100, started_at=None, command="sleep 30")
        brnrd_hook = pause.ProcInfo(
            pid=3, ppid=100, started_at=now + 1, command="brnrd hook post-tool")
        brnrd_await = pause.ProcInfo(
            pid=4, ppid=100, started_at=now + 1, command="/usr/local/bin/brnrd await")
        fresh = pause.ProcInfo(pid=5, ppid=100, started_at=now + 1, command="sleep 30")
        snapshot = [old, unparsed, brnrd_hook, brnrd_await, fresh]
        result = pause.pausable_children(100, now, snapshot=snapshot)
        assert [p.pid for p in result] == [5]

    def test_deep_descendant_of_a_skipped_process_is_not_dropped_by_ancestry_alone(self):
        # pausable_children only ever consults each process's own start time
        # and argv — a process is not exempted merely because its parent
        # would have been.
        now = time.time()
        old_parent = pause.ProcInfo(pid=10, ppid=1, started_at=now - 100, command="sleep 9999")
        fresh_child = pause.ProcInfo(pid=11, ppid=10, started_at=now + 1, command="sleep 30")
        result = pause.pausable_children(1, now, snapshot=[old_parent, fresh_child])
        assert [p.pid for p in result] == [11]


def test_pause_stops_child_and_grandchild_deepest_first(chain, tmp_path):
    child, grandchild = _child_and_grandchild(chain.pid)
    since = time.time() - 60
    records = pause.pause_run_children(
        runner_pid=chain.pid, since_ts=since, outbox_dir=tmp_path, cap_seconds=600,
    )
    assert records and records[0]["pid"] == child.pid
    assert _wait_until(lambda: _ps_state(child.pid) == "T")
    assert _wait_until(lambda: _ps_state(grandchild.pid) == "T")
    # The root itself is never touched.
    assert _ps_state(chain.pid) not in ("T",)
    on_disk = pause.read_paused_record(tmp_path)
    assert on_disk == records


def test_pause_is_idempotent_when_already_paused(chain, tmp_path):
    _child_and_grandchild(chain.pid)
    since = time.time() - 60
    first = pause.pause_run_children(
        runner_pid=chain.pid, since_ts=since, outbox_dir=tmp_path, cap_seconds=600,
    )
    assert first
    second = pause.pause_run_children(
        runner_pid=chain.pid, since_ts=since, outbox_dir=tmp_path, cap_seconds=600,
    )
    assert second is None
    assert pause.read_paused_record(tmp_path) == first


def test_resume_revives_child_and_grandchild(chain, tmp_path):
    child, grandchild = _child_and_grandchild(chain.pid)
    since = time.time() - 60
    pause.pause_run_children(
        runner_pid=chain.pid, since_ts=since, outbox_dir=tmp_path, cap_seconds=600,
    )
    assert _wait_until(lambda: _ps_state(child.pid) == "T")
    assert _wait_until(lambda: _ps_state(grandchild.pid) == "T")
    resumed = pause.resume_pids(tmp_path)
    assert resumed
    assert _wait_until(lambda: _ps_state(child.pid) in ("S", "R"))
    assert _wait_until(lambda: _ps_state(grandchild.pid) in ("S", "R"))
    assert pause.read_paused_record(tmp_path) == []


def test_resume_only_pid_leaves_others_paused_and_recorded(chain, tmp_path):
    child, grandchild = _child_and_grandchild(chain.pid)
    since = time.time() - 60
    pause.pause_run_children(
        runner_pid=chain.pid, since_ts=since, outbox_dir=tmp_path, cap_seconds=600,
    )
    resumed = pause.resume_pids(tmp_path, only_pid=999999)  # nothing matches
    assert resumed == []
    assert len(pause.read_paused_record(tmp_path)) == 1
    assert _ps_state(child.pid) == "T"


def test_drop_terminates_a_cooperative_process(chain, tmp_path):
    child, grandchild = _child_and_grandchild(chain.pid)
    since = time.time() - 60
    pause.pause_run_children(
        runner_pid=chain.pid, since_ts=since, outbox_dir=tmp_path, cap_seconds=600,
    )
    dropped = pause.drop_pids(tmp_path, grace_seconds=2.0)
    assert dropped
    # The recorded child (and its own grandchild) exit under SIGTERM — a
    # plain python/sleep process with no handler installed. Terminated but
    # unreaped (its parent, `chain`, never calls wait()) shows as a zombie
    # ("Z") rather than vanishing outright; either is "not running". The
    # root (playing the run's Shell) is never a drop target and must
    # survive.
    assert _wait_until(lambda: _ps_state(child.pid) in ("", "Z"))
    assert _wait_until(lambda: _ps_state(grandchild.pid) in ("", "Z"))
    assert _ps_state(chain.pid) not in ("", "Z")
    assert pause.read_paused_record(tmp_path) == []


def test_release_all_clears_the_record(chain, tmp_path):
    child, _ = _child_and_grandchild(chain.pid)
    since = time.time() - 60
    pause.pause_run_children(
        runner_pid=chain.pid, since_ts=since, outbox_dir=tmp_path, cap_seconds=600,
    )
    pause.release_all(tmp_path)
    assert pause.read_paused_record(tmp_path) == []
    assert _wait_until(lambda: _ps_state(child.pid) in ("S", "R"))


def test_overdue_records_names_only_records_past_their_cap(tmp_path):
    now = time.time()
    fresh = {"pid": 1, "argv": "sleep 30", "stopped_at": now, "resumes_at": now + 600}
    stale = {"pid": 2, "argv": "sleep 30", "stopped_at": now - 700, "resumes_at": now - 100}
    pause.write_paused_record(tmp_path, [fresh, stale])
    overdue = pause.overdue_records(tmp_path, now=now)
    assert overdue == [stale]


def test_describe_paused_uses_basename_and_first_arg():
    records = [{"pid": 1, "argv": "/usr/bin/sleep 300 extra-arg"}]
    assert pause.describe_paused(records) == "sleep 300"


class TestBoundaryLine:
    def test_read_paused_reflects_the_control_file(self, tmp_path):
        from brr.hooks import HookContext

        env = {"BRR_OUTBOX_DIR": str(tmp_path)}
        ctx = HookContext(env)
        assert hooks._read_paused(ctx) is None
        pause.write_paused_record(
            tmp_path, [{"pid": 123, "argv": "sleep 300", "stopped_at": 0, "resumes_at": 600}],
        )
        assert hooks._read_paused(ctx) == "sleep 300"

    def test_format_delta_renders_paused_chip_and_stands_gateless(self):
        payload = {
            "run": {}, "attention": {"pending_event_count": 0},
            "resources": {}, "card": {}, "produce": {},
            "outbound": {}, "notices": [],
        }
        line = hooks.format_delta(payload, paused="sleep 300")
        assert line is not None
        assert "paused: sleep 300 · brnrd resume | brnrd drop" in line


class TestCLI:
    def _pause_one(self, tmp_path):
        proc = subprocess.Popen(["sleep", "30"])
        pause.write_paused_record(
            tmp_path,
            [{
                "pid": proc.pid, "argv": "sleep 30",
                "stopped_at": time.time(), "resumes_at": time.time() + 600,
            }],
        )
        import os
        import signal
        os.kill(proc.pid, signal.SIGSTOP)
        return proc

    def test_resume_verb_revives_and_prints(self, tmp_path, capsys):
        proc = self._pause_one(tmp_path)
        try:
            assert _wait_until(lambda: _ps_state(proc.pid) == "T")
            rc = main(["resume", "--outbox", str(tmp_path)])
            assert rc == 0
            out = capsys.readouterr().out
            assert "resumed 1 process" in out
            assert _wait_until(lambda: _ps_state(proc.pid) in ("S", "R"))
            assert pause.read_paused_record(tmp_path) == []
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_drop_verb_terminates_and_prints(self, tmp_path, capsys):
        proc = self._pause_one(tmp_path)
        rc = main(["drop", "--outbox", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "dropped 1 process" in out
        assert _wait_until(lambda: proc.poll() is not None)
        assert pause.read_paused_record(tmp_path) == []

    def test_resume_with_nothing_paused_is_a_clean_no_op(self, tmp_path, capsys):
        rc = main(["resume", "--outbox", str(tmp_path)])
        assert rc == 0
        assert "nothing paused" in capsys.readouterr().out
