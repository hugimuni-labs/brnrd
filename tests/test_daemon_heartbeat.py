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

import threading
import time
from types import SimpleNamespace

from brr import daemon
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
