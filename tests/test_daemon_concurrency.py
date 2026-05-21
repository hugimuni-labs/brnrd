"""Tests for the threaded daemon worker pool.

The contention-free design assertion (per-event conversation files,
per-task gate progress files, per-branch locks for push/ff) is
covered by the module-level tests for those subsystems. The cases
here exercise the *integration*: that ``daemon.start()`` actually
dispatches in parallel up to ``max_workers``, that one slow task
doesn't block unrelated tasks, that ``max_workers=1`` reproduces
the old serial behaviour, and that a crashing worker doesn't take
the daemon down with it.
"""

from __future__ import annotations

import threading
import time

import pytest

from brr import daemon
from brr.task import Task

from _helpers import write_repo_scaffold


def _seed_event(tmp_path, eid: str) -> dict:
    """Create an inbox file for *eid* and return the event metadata dict."""
    path = tmp_path / ".brr" / "inbox" / f"{eid}.md"
    path.write_text(
        f"---\nid: {eid}\nstatus: pending\n---\nbody for {eid}\n",
        encoding="utf-8",
    )
    return {"id": eid, "status": "pending", "_path": path}


def _baseline_patches(monkeypatch):
    """Common setup: skip PID file, skip gates, no signal traps."""
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)


def test_two_events_dispatch_in_parallel(tmp_path, monkeypatch):
    """With max_workers=2, two pending events run concurrently rather
    than one-after-the-other. The slow worker mock blocks on a barrier
    that only releases once both workers have entered it — if dispatch
    were serial the test would deadlock and time out.
    """
    write_repo_scaffold(tmp_path)
    a = _seed_event(tmp_path, "evt-a")
    b = _seed_event(tmp_path, "evt-b")
    _baseline_patches(monkeypatch)
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {"max_workers": 2})

    barrier = threading.Barrier(2, timeout=5.0)
    entered: list[str] = []
    entered_lock = threading.Lock()

    def slow_run_worker(event, *_a, **_k):
        with entered_lock:
            entered.append(event["id"])
        # Both workers must reach this point for the barrier to fire.
        # If the pool were serial, the second worker never starts and
        # the first hangs here forever (caught by the barrier timeout).
        barrier.wait()
        return Task(
            id=f"task-{event['id']}",
            event_id=event["id"],
            body="x",
            status="done",
        )

    monkeypatch.setattr(daemon, "_run_worker", slow_run_worker)
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _e, _s: None)

    pending_calls: list[int] = []

    def fake_list_pending(_inbox):
        pending_calls.append(1)
        if len(pending_calls) == 1:
            return [a, b]
        if len(pending_calls) <= 3:
            # Keep the main loop alive long enough for both workers to
            # leave the barrier and reach reap; then stop.
            return []
        raise StopIteration

    monkeypatch.setattr(daemon.protocol, "list_pending", fake_list_pending)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert sorted(entered) == ["evt-a", "evt-b"]


def test_max_workers_one_serialises_dispatch(tmp_path, monkeypatch):
    """With max_workers=1, two pending events are dispatched one at a
    time. The second event must not enter the worker until the first
    has completed.
    """
    write_repo_scaffold(tmp_path)
    a = _seed_event(tmp_path, "evt-a")
    b = _seed_event(tmp_path, "evt-b")
    _baseline_patches(monkeypatch)
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {"max_workers": 1})

    timeline: list[str] = []
    timeline_lock = threading.Lock()

    def stamp(label: str) -> None:
        with timeline_lock:
            timeline.append(label)

    def run_worker(event, *_a, **_k):
        stamp(f"enter:{event['id']}")
        # No barrier — the first worker should finish before the second
        # is even allowed to start. The sleep just adds enough headroom
        # for a wrong implementation to interleave.
        time.sleep(0.05)
        stamp(f"exit:{event['id']}")
        return Task(
            id=f"task-{event['id']}",
            event_id=event["id"],
            body="x",
            status="done",
        )

    monkeypatch.setattr(daemon, "_run_worker", run_worker)
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _e, _s: None)

    pending_calls: list[int] = []
    # Hand the events one at a time when capacity allows — the main
    # loop will only pick the second up after the first future has
    # been reaped.
    queue = [a, b]

    def fake_list_pending(_inbox):
        pending_calls.append(1)
        if queue:
            return queue.copy()
        if len(pending_calls) > 30:
            raise StopIteration
        return []

    monkeypatch.setattr(daemon.protocol, "list_pending", fake_list_pending)

    # Once both workers have exited, drop the events so the loop
    # eventually raises StopIteration. The hook fires every time we
    # observe a fresh exit.
    real_stamp = stamp

    def stamp_with_drain(label: str) -> None:
        real_stamp(label)
        if label == "exit:evt-a":
            queue.remove(a)
        elif label == "exit:evt-b":
            queue.remove(b)

    nonlocal_stamp_holder = {"stamp": stamp_with_drain}

    def run_worker_drained(event, *_a, **_k):
        nonlocal_stamp_holder["stamp"](f"enter:{event['id']}")
        time.sleep(0.05)
        nonlocal_stamp_holder["stamp"](f"exit:{event['id']}")
        return Task(
            id=f"task-{event['id']}",
            event_id=event["id"],
            body="x",
            status="done",
        )

    monkeypatch.setattr(daemon, "_run_worker", run_worker_drained)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    # Whatever order they enter, the second one must enter after the
    # first one exits. That's the serial-dispatch invariant.
    enters = [i for i, label in enumerate(timeline) if label.startswith("enter:")]
    exits = [i for i, label in enumerate(timeline) if label.startswith("exit:")]
    assert len(enters) == 2 and len(exits) == 2
    assert exits[0] < enters[1], (
        f"second worker started before first exited: {timeline}"
    )


def test_worker_crash_does_not_kill_daemon(tmp_path, monkeypatch):
    """A worker that raises an exception is logged but the main loop
    keeps scanning. Subsequent events still dispatch and succeed.
    """
    write_repo_scaffold(tmp_path)
    bad = _seed_event(tmp_path, "evt-bad")
    good = _seed_event(tmp_path, "evt-good")
    _baseline_patches(monkeypatch)
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {"max_workers": 1})

    completed: list[str] = []
    completed_lock = threading.Lock()

    def run_worker(event, *_a, **_k):
        if event["id"] == "evt-bad":
            raise RuntimeError("boom")
        with completed_lock:
            completed.append(event["id"])
        return Task(
            id=f"task-{event['id']}",
            event_id=event["id"],
            body="x",
            status="done",
        )

    monkeypatch.setattr(daemon, "_run_worker", run_worker)
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _e, _s: None)

    queue = [bad, good]
    pending_calls: list[int] = []

    def fake_list_pending(_inbox):
        pending_calls.append(1)
        if queue:
            return queue.copy()
        if completed:
            raise StopIteration
        if len(pending_calls) > 50:
            raise AssertionError("stuck waiting for good event")
        return []

    monkeypatch.setattr(daemon.protocol, "list_pending", fake_list_pending)

    # Once each event is dispatched, remove it from the queue so we
    # don't redispatch on the next scan.
    real_run_worker = run_worker

    def dispatched_run_worker(event, *_a, **_k):
        queue.remove(event)
        return real_run_worker(event, *_a, **_k)

    monkeypatch.setattr(daemon, "_run_worker", dispatched_run_worker)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert completed == ["evt-good"], (
        "good event must still complete after bad one crashes"
    )


def test_default_max_workers_when_config_absent(tmp_path, monkeypatch):
    """Adopters who never set ``max_workers`` get the bounded default
    rather than unlimited concurrency or a hard-coded 1.
    """
    write_repo_scaffold(tmp_path)
    _baseline_patches(monkeypatch)
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})

    observed: list[int] = []

    real_executor = daemon.concurrent.futures.ThreadPoolExecutor

    def spy_executor(*args, max_workers, **kwargs):
        observed.append(max_workers)
        return real_executor(max_workers=max_workers, *args, **kwargs)

    monkeypatch.setattr(
        daemon.concurrent.futures, "ThreadPoolExecutor", spy_executor,
    )

    def stop_immediately(_inbox):
        raise StopIteration

    monkeypatch.setattr(daemon.protocol, "list_pending", stop_immediately)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert observed == [daemon._DEFAULT_MAX_WORKERS]
