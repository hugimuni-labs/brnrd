"""Tests for the single-flight daemon loop.

The local daemon is a thin reflex loop: it runs exactly one *thought* at
a time. When idle and work is pending it spawns one worker; events that
arrive mid-thought wait their turn. (This reshapes the former parallel
worker pool — see ``kb/design-agent-dominion.md`` §4 and
``kb/subject-daemon.md``.) The cases here exercise the integration: that
``daemon.start()`` never runs two thoughts at once, that the legacy
``max_workers`` knob no longer buys parallelism, and that a crashing
thought doesn't take the daemon down with it.
"""

from __future__ import annotations

import threading
import time

import pytest

from brr import daemon
from brr.run import Run

from _helpers import write_repo_scaffold


def _seed_event(tmp_path, eid: str) -> dict:
    """Create an inbox file for *eid* and return the event metadata dict."""
    path = tmp_path / ".brr" / "inbox" / f"{eid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
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
    # These cases assert immediate, ordered dispatch of two pending events;
    # burst coalescing (which would hold a ≥2 burst to settle) is a separate
    # concern, exercised in test_daemon_burst.py. Disable it here.
    monkeypatch.setattr(daemon, "_BURST_WINDOW_DEFAULT", 0.0)


def _run_two_events(tmp_path, monkeypatch, cfg):
    """Drive ``daemon.start()`` over two pending events.

    Returns ``(timeline, peak_concurrency)``. The worker mock records
    when each thought enters and exits and tracks how many run at once,
    so the caller can assert the single-flight invariant directly.
    """
    write_repo_scaffold(tmp_path)
    a = _seed_event(tmp_path, "evt-a")
    b = _seed_event(tmp_path, "evt-b")
    _baseline_patches(monkeypatch)
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: cfg)

    timeline: list[str] = []
    active = {"n": 0, "peak": 0}
    lock = threading.Lock()
    queue = [a, b]

    def run_worker(event, *_a, **_k):
        eid = event["id"]
        with lock:
            active["n"] += 1
            active["peak"] = max(active["peak"], active["n"])
            timeline.append(f"enter:{eid}")
        time.sleep(0.05)
        with lock:
            timeline.append(f"exit:{eid}")
            active["n"] -= 1
            queue[:] = [e for e in queue if e["id"] != eid]
        return Run(id=f"task-{eid}", event_id=eid, body="x", status="done")

    monkeypatch.setattr(daemon, "_run_worker", run_worker)
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _e, _s: None)

    idle_scans = {"n": 0}

    def fake_list_pending(_inbox):
        with lock:
            if queue:
                return list(queue)
        idle_scans["n"] += 1
        if idle_scans["n"] > 5:
            raise StopIteration
        return []

    monkeypatch.setattr(daemon.protocol, "list_pending", fake_list_pending)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    return timeline, active["peak"]


def test_single_flight_runs_one_thought_at_a_time(tmp_path, monkeypatch):
    """Two pending events never run concurrently, and the second only
    enters after the first has exited."""
    timeline, peak = _run_two_events(tmp_path, monkeypatch, cfg={})

    assert peak == 1, f"two thoughts ran at once: {timeline}"
    enters = [i for i, label in enumerate(timeline) if label.startswith("enter:")]
    exits = [i for i, label in enumerate(timeline) if label.startswith("exit:")]
    assert len(enters) == 2 and len(exits) == 2
    assert exits[0] < enters[1], f"second thought started early: {timeline}"


def test_legacy_max_workers_is_ignored(tmp_path, monkeypatch):
    """The old ``max_workers`` knob no longer buys parallelism — the
    local daemon is single-flight by design (the parallel-pool thesis is
    superseded)."""
    _timeline, peak = _run_two_events(
        tmp_path, monkeypatch, cfg={"max_workers": 4},
    )

    assert peak == 1


def test_thought_crash_does_not_kill_daemon(tmp_path, monkeypatch):
    """A thought that raises is logged but the loop keeps scanning;
    subsequent events still dispatch and succeed."""
    write_repo_scaffold(tmp_path)
    bad = _seed_event(tmp_path, "evt-bad")
    good = _seed_event(tmp_path, "evt-good")
    _baseline_patches(monkeypatch)
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})

    completed: list[str] = []
    queue = [bad, good]
    lock = threading.Lock()

    def run_worker(event, *_a, **_k):
        eid = event["id"]
        with lock:
            queue[:] = [e for e in queue if e["id"] != eid]
        if eid == "evt-bad":
            raise RuntimeError("boom")
        completed.append(eid)
        return Run(id=f"task-{eid}", event_id=eid, body="x", status="done")

    monkeypatch.setattr(daemon, "_run_worker", run_worker)
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _e, _s: None)

    idle_scans = {"n": 0}

    def fake_list_pending(_inbox):
        with lock:
            if queue:
                return list(queue)
        if completed:
            raise StopIteration
        idle_scans["n"] += 1
        if idle_scans["n"] > 80:
            raise AssertionError("stuck waiting for good event")
        return []

    monkeypatch.setattr(daemon.protocol, "list_pending", fake_list_pending)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert completed == ["evt-good"], (
        "good event must still complete after the bad one crashes"
    )
