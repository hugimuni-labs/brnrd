"""Tests for the daemon's runner heartbeat helper.

The heartbeat keeps long-running runs visible on the chat card. Codex
(and friends) can sit silent for many minutes; without a periodic tick
the gate's elapsed counter looks frozen.

These tests drive ``_invoke_with_heartbeat`` directly with a short
interval — the production cadence (30s) is a hard-coded constant, not
exposed as config, but the helper accepts an *interval* override
expressly so the test can assert on tick count without sleeping for
production-scale durations.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from brr import daemon, hooks
from brr import runner as runner_mod
from brr.runner import RunnerInvocation, RunnerResult


def _invocation(label: str = "test") -> RunnerInvocation:
    return RunnerInvocation(
        kind="daemon-run", label=label, prompt="x", cwd=None,
        repo_root=None, response_path="/tmp/x",
    )


def _ok_result(invocation: RunnerInvocation) -> RunnerResult:
    return RunnerResult(
        invocation=invocation, runner_name="codex", command=["mock"],
        stdout="", stderr="", returncode=0, trace_dir=None, artifacts=[],
    )


def test_invoke_with_heartbeat_drains_on_flush_signal(tmp_path):
    """A .flush signal dropped mid-run triggers on_flush and is consumed,
    without waiting for the (long) heartbeat interval."""
    flush_path = tmp_path / ".flush"
    flushes: list[float] = []
    heartbeats: list[float] = []

    def slow_invoke(_ctx, _runner, invocation, cfg, *, trace=False):
        # Drop the signal partway through, then keep "working".
        time.sleep(0.05)
        flush_path.write_text("now")
        time.sleep(0.2)
        return _ok_result(invocation)

    backend = SimpleNamespace(invoke=slow_invoke)
    result = daemon._invoke_with_heartbeat(
        backend, None, "codex", _invocation(),
        cfg={}, trace=False,
        on_heartbeat=lambda: heartbeats.append(time.monotonic()),
        on_flush=lambda: flushes.append(time.monotonic()),
        flush_path=flush_path,
        flush_interval=0.02,
        interval=10.0,  # long: a flush must not wait for the heartbeat tick
    )

    assert result.returncode == 0
    assert len(flushes) >= 1  # the signal was noticed and drained
    assert not flush_path.exists()  # and consumed
    assert (tmp_path / hooks.FLUSH_ACK_NAME).read_text().strip() == "now"
    assert heartbeats == []  # the 10s heartbeat never fired in this window


def test_invoke_with_heartbeat_ticks_during_long_run():
    """A runner that takes longer than the interval gets at least one
    heartbeat tick while it's still alive."""
    ticks: list[float] = []
    started = time.monotonic()

    def slow_invoke(_ctx, _runner, invocation, cfg, *, trace=False):
        time.sleep(0.25)
        return _ok_result(invocation)

    backend = SimpleNamespace(invoke=slow_invoke)
    result = daemon._invoke_with_heartbeat(
        backend, None, "codex", _invocation(),
        cfg={}, trace=False,
        on_heartbeat=lambda: ticks.append(time.monotonic() - started),
        interval=0.05,
    )

    assert result.returncode == 0
    assert len(ticks) >= 2  # 0.25s run / 0.05s interval ≈ 5 ticks
    assert all(t < 1.0 for t in ticks)  # sanity: didn't run forever


def test_invoke_with_heartbeat_skips_for_fast_runs():
    """A runner that returns inside the first interval window gets no
    ticks — heartbeats fire only when the run is still alive at the
    interval boundary."""
    ticks: list[None] = []

    def fast_invoke(_ctx, _runner, invocation, cfg, *, trace=False):
        return _ok_result(invocation)

    backend = SimpleNamespace(invoke=fast_invoke)
    result = daemon._invoke_with_heartbeat(
        backend, None, "codex", _invocation(),
        cfg={}, trace=False,
        on_heartbeat=lambda: ticks.append(None),
        interval=10.0,  # arbitrarily long; thread joins immediately
    )

    assert result.returncode == 0
    assert ticks == []


def test_invoke_with_heartbeat_swallows_callback_errors():
    """A misbehaving heartbeat callback must never break the in-flight
    runner — heartbeats are best-effort cosmetic plumbing."""

    def slow_invoke(_ctx, _runner, invocation, cfg, *, trace=False):
        time.sleep(0.15)
        return _ok_result(invocation)

    backend = SimpleNamespace(invoke=slow_invoke)
    result = daemon._invoke_with_heartbeat(
        backend, None, "codex", _invocation(),
        cfg={}, trace=False,
        on_heartbeat=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        interval=0.05,
    )

    assert result.returncode == 0


def test_invoke_with_heartbeat_propagates_runner_exception():
    """Runner exceptions must surface to the caller — the heartbeat
    layer is transparent on errors, not absorbing them."""

    def raising_invoke(_ctx, _runner, _invocation, cfg, *, trace=False):
        raise RuntimeError("docker daemon down")

    backend = SimpleNamespace(invoke=raising_invoke)
    try:
        daemon._invoke_with_heartbeat(
            backend, None, "codex", _invocation(),
            cfg={}, trace=False,
            on_heartbeat=lambda: None,
            interval=0.05,
        )
    except RuntimeError as exc:
        assert "docker daemon down" in str(exc)
    else:
        raise AssertionError("expected RuntimeError to propagate")


def test_invoke_with_heartbeat_runs_runner_on_separate_thread():
    """The runner must execute off the daemon's main thread so the
    heartbeat callback can fire concurrently."""
    runner_thread: list[str] = []
    callback_thread: list[str] = []

    def slow_invoke(_ctx, _runner, invocation, cfg, *, trace=False):
        runner_thread.append(threading.current_thread().name)
        time.sleep(0.15)
        return _ok_result(invocation)

    backend = SimpleNamespace(invoke=slow_invoke)

    def cb():
        callback_thread.append(threading.current_thread().name)

    daemon._invoke_with_heartbeat(
        backend, None, "codex", _invocation(label="thread-test"),
        cfg={}, trace=False, on_heartbeat=cb, interval=0.05,
    )

    assert runner_thread, "runner never executed"
    assert callback_thread, "heartbeat never fired"
    # Runner ran on the dedicated worker; callback ran on the caller.
    assert runner_thread[0].startswith("runner-")
    assert callback_thread[0] != runner_thread[0]


# ── Liveness budget enforcement ──────────────────────────────────────


def _real_sleep_invoke(seconds: int = 30):
    """A fake backend whose invoke registers a real killable subprocess as
    the active runner, so kill_active() can reclaim it like in production."""

    def invoke(_ctx, _runner, invocation, cfg, *, trace=False):
        proc = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])
        with runner_mod._proc_lock:
            runner_mod._active_proc = proc
        try:
            proc.wait()
        finally:
            with runner_mod._proc_lock:
                runner_mod._active_proc = None
        return RunnerResult(
            invocation=invocation, runner_name="codex", command=["sleep"],
            stdout="", stderr="", returncode=proc.returncode,
            trace_dir=None, artifacts=[],
        )

    return invoke


def test_budget_kills_runner_and_reports_124():
    """Past its budget the runner is killed via kill_active and the result
    is presented like the wall-clock timeout (124)."""
    backend = SimpleNamespace(invoke=_real_sleep_invoke(30))
    result = daemon._invoke_with_heartbeat(
        backend, None, "codex", _invocation(),
        cfg={}, trace=False, on_heartbeat=lambda: None,
        interval=0.05, budget_seconds=0.1, hard_cap_seconds=5,
        keepalive_path=None,
    )
    assert result.returncode == 124
    assert "budget" in result.stderr


def test_keepalive_extends_budget(tmp_path):
    """An agent keepalive pushes the deadline out, so a tiny budget no
    longer kills a run that finishes within the extension."""
    ka = tmp_path / ".keepalive"
    ka.write_text("+1h\n")

    def slow_invoke(_ctx, _runner, invocation, cfg, *, trace=False):
        time.sleep(0.2)
        return _ok_result(invocation)

    backend = SimpleNamespace(invoke=slow_invoke)
    result = daemon._invoke_with_heartbeat(
        backend, None, "codex", _invocation(),
        cfg={}, trace=False, on_heartbeat=lambda: None,
        interval=0.05, budget_seconds=0.05, hard_cap_seconds=3600,
        keepalive_path=ka,
    )
    assert result.returncode == 0


def test_keepalive_capped_by_hard_cap(tmp_path):
    """A wildly generous keepalive can't pin the slot past the hard cap."""
    ka = tmp_path / ".keepalive"
    ka.write_text("+10h\n")
    backend = SimpleNamespace(invoke=_real_sleep_invoke(30))
    result = daemon._invoke_with_heartbeat(
        backend, None, "codex", _invocation(),
        cfg={}, trace=False, on_heartbeat=lambda: None,
        interval=0.05, budget_seconds=0.05, hard_cap_seconds=0.2,
        keepalive_path=ka,
    )
    assert result.returncode == 124


class TestBudgetHelpers:
    def test_keepalive_until_parses_iso_and_duration(self, tmp_path):
        ka = tmp_path / ".keepalive"
        ka.write_text("2099-01-01T00:00:00Z")
        assert daemon._keepalive_until(ka) > time.time()

        ka.write_text("+30m")
        now = time.time()
        os.utime(ka, (now, now))
        val = daemon._keepalive_until(ka)
        assert abs(val - (now + 1800)) < 5

    def test_keepalive_until_none_for_missing_empty_garbage(self, tmp_path):
        assert daemon._keepalive_until(None) is None
        assert daemon._keepalive_until(tmp_path / "nope") is None
        empty = tmp_path / "empty"
        empty.write_text("   \n")
        assert daemon._keepalive_until(empty) is None
        junk = tmp_path / "junk"
        junk.write_text("not a time")
        assert daemon._keepalive_until(junk) is None

    def test_budget_exceeded_basic(self):
        now = time.monotonic()
        assert not daemon._budget_exceeded(now, 100, None, None)
        assert daemon._budget_exceeded(now - 200, 100, None, None)

    def test_budget_keepalive_within_cap_prevents_kill(self, tmp_path):
        ka = tmp_path / ".keepalive"
        ka.write_text("+1h")
        now = time.time()
        os.utime(ka, (now, now))
        # 10s elapsed, base budget 5s (passed), but a +1h keepalive under a
        # generous cap moves the deadline far out.
        assert not daemon._budget_exceeded(time.monotonic() - 10, 5, 3600, ka)

    def test_budget_hard_cap_overrides_keepalive(self, tmp_path):
        ka = tmp_path / ".keepalive"
        ka.write_text("+10h")
        now = time.time()
        os.utime(ka, (now, now))
        # The cap (100s from start) bites before the +10h keepalive.
        assert daemon._budget_exceeded(time.monotonic() - 200, 100, 100, ka)
