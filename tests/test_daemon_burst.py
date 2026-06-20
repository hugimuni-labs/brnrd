"""Tests for burst coalescing — the dispatch debounce.

When a burst of events is already queued, the daemon holds dispatch briefly
so the whole burst lands in one wake instead of spawning a fresh thought per
fragment (the live "the daemon wouldn't ship all the messages that
accumulated into a fresh wake" symptom). A lone event never waits, so the
debounce adds no latency to the common single-message case. This is the
first behavioural slice of the run/event model — see
``kb/design-run-event-model.md`` Q2 and #128.

The settle logic lives in the pure ``daemon._burst_settle_delay`` (tested
directly with controlled mtimes); the loop wiring is checked with a
deterministic stub and an end-to-end config path, both free of wall-clock
flakiness.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from brr import daemon
from brr import protocol
from brr.run import Run

from _helpers import write_repo_scaffold


def _seed(tmp_path, eid: str) -> dict:
    path = tmp_path / ".brr" / "inbox" / f"{eid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {eid}\nstatus: pending\n---\nbody for {eid}\n",
        encoding="utf-8",
    )
    return {"id": eid, "status": "pending", "_path": path}


def _aged(tmp_path, eid: str, age: float, now: float) -> dict:
    """Seed an event whose mtime is *age* seconds before *now*."""
    ev = _seed(tmp_path, eid)
    os.utime(ev["_path"], (now - age, now - age))
    return ev


# ── _burst_settle_delay (pure logic) ─────────────────────────────────

WINDOW = 1.5
MAX_WAIT = 12.0


def test_lone_event_never_holds(tmp_path):
    now = time.time()
    ev = _aged(tmp_path, "evt-a", 0.0, now)  # brand new
    assert daemon._burst_settle_delay([ev], WINDOW, MAX_WAIT, now) == 0.0


def test_window_zero_disables_coalescing(tmp_path):
    now = time.time()
    evs = [_aged(tmp_path, "evt-a", 0.0, now), _aged(tmp_path, "evt-b", 0.0, now)]
    assert daemon._burst_settle_delay(evs, 0.0, MAX_WAIT, now) == 0.0


def test_fresh_burst_holds(tmp_path):
    now = time.time()
    evs = [_aged(tmp_path, "evt-a", 0.0, now), _aged(tmp_path, "evt-b", 0.0, now)]
    delay = daemon._burst_settle_delay(evs, WINDOW, MAX_WAIT, now)
    assert 0.0 < delay <= WINDOW


def test_settled_burst_dispatches(tmp_path):
    now = time.time()
    # newest event is 2.0s old ≥ window → the inbox went quiet → dispatch.
    evs = [_aged(tmp_path, "evt-a", 5.0, now), _aged(tmp_path, "evt-b", 2.0, now)]
    assert daemon._burst_settle_delay(evs, WINDOW, MAX_WAIT, now) == 0.0


def test_cap_forces_dispatch_even_while_arriving(tmp_path):
    now = time.time()
    # Still arriving (a fresh event), but the oldest has waited past the cap.
    evs = [_aged(tmp_path, "evt-a", 13.0, now), _aged(tmp_path, "evt-b", 0.0, now)]
    assert daemon._burst_settle_delay(evs, WINDOW, MAX_WAIT, now) == 0.0


def test_still_arriving_holds_until_quiet_or_cap(tmp_path):
    now = time.time()
    # newest 0.5s old (< window) → hold; oldest 3s old (< cap).
    evs = [_aged(tmp_path, "evt-a", 3.0, now), _aged(tmp_path, "evt-b", 0.5, now)]
    delay = daemon._burst_settle_delay(evs, WINDOW, MAX_WAIT, now)
    # remaining window = 1.5 - 0.5 = 1.0; remaining cap = 12 - 3 = 9 → min 1.0
    assert delay == pytest.approx(1.0, abs=0.05)


def test_event_mtime_fallback_reads_as_old():
    # An event with no usable path reads as epoch-old, so it can never hold
    # the burst window open (the safe default is "dispatch, don't stall").
    assert daemon._event_mtime({}) == 0.0


def test_unstattable_member_does_not_stall_burst(tmp_path):
    now = time.time()
    fresh = _aged(tmp_path, "evt-a", 0.0, now)
    # The pathless member's mtime reads as 0.0 → oldest waited "forever" →
    # cap trips → dispatch rather than hold on a member we can't time.
    evs = [fresh, {"id": "evt-x", "status": "pending"}]
    assert daemon._burst_settle_delay(evs, WINDOW, MAX_WAIT, now) == 0.0


# ── Loop wiring ──────────────────────────────────────────────────────


def _loop_baseline(monkeypatch, cfg):
    monkeypatch.setattr(daemon, "read_pid", lambda _b: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _b: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _b: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_a: [])
    monkeypatch.setattr(daemon.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _e, _s: None)
    monkeypatch.setattr(daemon.conf, "load_config", lambda _r: cfg)


def _record_worker(queue, entered, lock):
    def run_worker(event, *_a, **_k):
        eid = event["id"]
        with lock:
            entered.append(eid)
            queue[:] = [e for e in queue if e["id"] != eid]
        return Run(id=f"t-{eid}", event_id=eid, body="x", status="done")

    return run_worker


def test_loop_holds_burst_then_dispatches(tmp_path, monkeypatch):
    """While the settle delay is positive the loop must not dispatch; once it
    returns 0 the burst dispatches (single-flight order). Stubbing the delay
    keeps this deterministic — no dependence on wall-clock mtimes."""
    write_repo_scaffold(tmp_path)
    a, b = _seed(tmp_path, "evt-a"), _seed(tmp_path, "evt-b")
    _loop_baseline(monkeypatch, cfg={})

    queue = [a, b]
    entered: list[str] = []
    lock = threading.Lock()
    monkeypatch.setattr(daemon, "_run_worker", _record_worker(queue, entered, lock))

    scans = {"n": 0}

    def fake_delay(pending, *_a, **_k):
        # Only consulted while a burst is pending and the slot is idle.
        with lock:
            if len(queue) >= 2:
                scans["n"] += 1
                # No dispatch must have happened during the hold.
                assert entered == []
                if scans["n"] <= 3:
                    return 0.05
        return 0.0

    monkeypatch.setattr(daemon, "_burst_settle_delay", fake_delay)

    idle = {"n": 0}

    def fake_list_pending(_inbox):
        with lock:
            if queue:
                return list(queue)
        idle["n"] += 1
        if idle["n"] > 5:
            raise StopIteration
        return []

    monkeypatch.setattr(daemon.protocol, "list_pending", fake_list_pending)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert entered == ["evt-a", "evt-b"]
    assert scans["n"] >= 3, "the loop should have held across several scans"


def test_config_window_zero_dispatches_burst_immediately(tmp_path, monkeypatch):
    """With coalescing disabled in config, a fresh ≥2 burst dispatches at
    once — the pre-coalescing behaviour — exercising the real settle
    function and the config path end-to-end."""
    write_repo_scaffold(tmp_path)
    a, b = _seed(tmp_path, "evt-a"), _seed(tmp_path, "evt-b")
    _loop_baseline(monkeypatch, cfg={"dispatch.burst_window_seconds": 0})

    queue = [a, b]
    entered: list[str] = []
    lock = threading.Lock()
    monkeypatch.setattr(daemon, "_run_worker", _record_worker(queue, entered, lock))

    idle = {"n": 0}

    def fake_list_pending(_inbox):
        with lock:
            if queue:
                return list(queue)
        idle["n"] += 1
        if idle["n"] > 5:
            raise StopIteration
        return []

    monkeypatch.setattr(daemon.protocol, "list_pending", fake_list_pending)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert entered == ["evt-a", "evt-b"]


def test_failure_defers_pending_siblings_without_hiding_them(tmp_path):
    """A terminal failure on the lead event brakes siblings instead of
    immediately re-spawning one failure wake per pending fragment."""
    inbox = tmp_path / ".brr" / "inbox"
    lead = _seed(tmp_path, "evt-a")
    _seed(tmp_path, "evt-b")
    _seed(tmp_path, "evt-c")
    protocol.set_status(lead, "done")

    changed = daemon._defer_pending_siblings_after_failure(
        inbox,
        lead_event_id="evt-a",
        run_id="run-x",
        seconds=60,
    )

    assert changed == 2
    pending = protocol.list_pending(inbox)
    assert [ev["id"] for ev in pending] == ["evt-b", "evt-c"]
    assert all(ev["deferred_by_run"] == "run-x" for ev in pending)
    assert all(ev["defer_reason"] == "operational_failure" for ev in pending)
    assert protocol.list_dispatchable(inbox) == []
    assert [
        ev["id"] for ev in protocol.list_dispatchable(inbox, now=time.time() + 61)
    ] == ["evt-b", "evt-c"]
