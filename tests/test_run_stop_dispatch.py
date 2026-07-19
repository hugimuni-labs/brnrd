"""The daemon half of the user-side stop affordance (#476 wyrd §3).

The load-bearing claim under test: a stop tapped in a browser reaches the
*same* kill path the ``stop:`` outbox verb reaches (PR #461), rather than a
second one written alongside it.
"""

from __future__ import annotations

import pytest

from brr import daemon, run_stop_request, runner
from brr.gates import cloud


@pytest.fixture(autouse=True)
def _clean_registries():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    with runner._proc_lock:
        runner._active_procs.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    with runner._proc_lock:
        runner._active_procs.clear()


# ── the local ack ledger ────────────────────────────────────────────


def test_unhandled_filters_out_already_dispatched_stops(tmp_path):
    """Delivery and acknowledgement ride the same endpoint one tick apart, so
    the server re-serves a stop we already killed. Acting on it twice would
    kill a second run that inherited the handle."""
    served = [
        {"request_id": "stopreq-1", "run_id": "run-a"},
        {"request_id": "stopreq-2", "run_id": "run-b"},
    ]
    assert len(run_stop_request.unhandled(tmp_path, served)) == 2

    run_stop_request.record_consumed(tmp_path, "stopreq-1")
    remaining = run_stop_request.unhandled(tmp_path, served)
    assert [row["request_id"] for row in remaining] == ["stopreq-2"]


def test_consumed_ledger_clears_only_what_the_server_acked(tmp_path):
    run_stop_request.record_consumed(tmp_path, "stopreq-1")
    run_stop_request.record_consumed(tmp_path, "stopreq-2")
    run_stop_request.clear_consumed(tmp_path, ["stopreq-1"])
    assert run_stop_request.consumed_ids(tmp_path) == ["stopreq-2"]


def test_unhandled_ignores_malformed_rows(tmp_path):
    served = [{"request_id": "", "run_id": "run-a"}, {"run_id": "run-b"}, "junk"]
    assert run_stop_request.unhandled(tmp_path, served) == []


# ── the dispatch reaches the shared kill path ───────────────────────


def test_dispatch_reaches_the_same_kill_path_as_the_stop_verb(tmp_path, monkeypatch):
    """A user stop kills by invocation-label prefix, exactly as `stop:` does —
    `runner.kill_matching`, not a second mechanism."""
    daemon._register_run_control("evt-resident", None)
    daemon._bind_run_control("evt-resident", "run-resident")

    killed: list[str] = []
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: killed.append(prefix) or True)

    cloud._dispatch_run_stops(
        tmp_path, None, [{"request_id": "stopreq-1", "run_id": "run-resident"}]
    )

    assert killed == ["evt-resident-attempt-"]
    assert daemon._stopped_run_control("evt-resident") is not None
    assert run_stop_request.consumed_ids(tmp_path) == ["stopreq-1"]


def test_dispatch_resolves_a_run_by_either_handle(tmp_path, monkeypatch):
    """The live-runs view names runs by run id; the registry is keyed by event
    id. `_find_run_control` already bridges both — the user path reuses it."""
    daemon._register_run_control("evt-child", "run-parent")
    daemon._bind_run_control("evt-child", "run-child")
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    cloud._dispatch_run_stops(
        tmp_path, None, [{"request_id": "stopreq-1", "run_id": "evt-child"}]
    )
    assert daemon._stopped_run_control("evt-child") is not None


def test_dispatch_acks_a_stop_for_a_run_that_already_finished(tmp_path, monkeypatch):
    """Nothing to kill is not a failure. Leaving it pending would re-serve the
    stop every tick until its TTL."""
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    cloud._dispatch_run_stops(
        tmp_path, None, [{"request_id": "stopreq-1", "run_id": "run-long-gone"}]
    )
    assert run_stop_request.consumed_ids(tmp_path) == ["stopreq-1"]


def test_user_stop_records_who_stopped_it(tmp_path, monkeypatch):
    """`stopped_by` is what `_finalize_stopped_run` writes onto the run, so a
    dashboard kill reads as one rather than as an anonymous death."""
    daemon._register_run_control("evt-resident", None)
    daemon._bind_run_control("evt-resident", "run-resident")
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    cloud._dispatch_run_stops(
        tmp_path, None, [{"request_id": "stopreq-1", "run_id": "run-resident"}]
    )
    control = daemon._find_run_control("run-resident")
    assert control["stopped_by"] == "user"
    assert control["stop_reason"] == "stopped from the dashboard"


# ── authority: the two principals differ ────────────────────────────


def test_a_run_still_cannot_stop_a_run_it_did_not_dispatch():
    """The dispatch-edge rule is untouched by #476. Widening the registry to
    hold resident thoughts must not hand runs a way to kill each other."""
    daemon._register_run_control("evt-resident", None)
    daemon._bind_run_control("evt-resident", "run-resident")

    control = daemon._find_run_control("run-resident")
    # What `_queue_stop_request` checks: a resident thought's control carries
    # no parent run id, so no run's id can ever match it.
    assert control["parent_run_id"] is None
