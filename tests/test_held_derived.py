"""``held`` leaves the run axis — every moved reader decides from the hold record.

Move 3 of the daemon rewrite. ``_arm_resource_hold`` still writes
``Run.status = "held"`` (existing expectations pin the word), but no reader
below decides anything from it any more: each reads the active
``meta["resource_hold"]`` through :func:`resource_hold.run_is_held`. The proof
is the manifest a stopped write would leave — status ``running`` (or
``pending``) with an active hold — and every moved reader must call it held,
while each reader's old verdicts on legacy manifests stay exactly as they
were.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from brr import daemon, resource_hold
from brr.operator_console import history
from brr.run import Run

from _helpers import make_event, write_repo_scaffold


def _dead_pid() -> int:
    proc = subprocess.Popen(["sleep", "0"])
    proc.wait()
    return proc.pid


def _hold(*, released: bool = False, armed_at: float | None = None) -> dict:
    meta = resource_hold.build(
        reason=resource_hold.REASON_QUOTA_EXHAUSTED, provider="codex",
        now=armed_at,
    )
    return resource_hold.mark_released(meta, by="test") if released else meta


def _run(runs_dir: Path, run_id: str, *, status: str, hold: dict | None = None,
         pid: int | None = None, age_seconds: float = 0.0) -> Run:
    task = Run(id=run_id, event_id=f"evt-{run_id}", body="work",
               source="telegram", status=status)
    if hold is not None:
        task.meta["resource_hold"] = hold
    if pid is not None:
        task.meta["pid"] = pid
    path = task.save(runs_dir)
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(path, (old, old))
    return task


@pytest.mark.parametrize(
    ("status", "hold", "expected"),
    [
        ("running", "active", True),     # the manifest a stopped write leaves
        ("pending", "active", True),
        ("held", "active", True),        # the mirror, still written today
        ("held", "released", False),     # a stale word is not a hold
        ("held", None, False),
        ("done", "active", False),       # an ended run is not held
        ("error", "active", False),
        ("running", None, False),
    ],
)
def test_run_is_held_reads_the_record_not_the_word(status, hold, expected):
    meta = {} if hold is None else {"resource_hold": _hold(released=hold == "released")}
    assert resource_hold.run_is_held(status, meta) is expected


def test_held_runs_for_repo_lists_a_hold_whatever_its_status_word(tmp_path):
    runs_dir = tmp_path / ".brr" / "runs"
    _run(runs_dir, "run-running-held", status="running", hold=_hold(armed_at=200.0))
    _run(runs_dir, "run-legacy-held", status="held", hold=_hold(armed_at=100.0))
    _run(runs_dir, "run-stale-word", status="held", hold=_hold(released=True))
    _run(runs_dir, "run-ended", status="done", hold=_hold(armed_at=300.0))
    _run(runs_dir, "run-plain", status="running")

    listed = [r.id for r in daemon._held_runs_for_repo(runs_dir)]

    # newest-armed first, exactly as before; only the selection is derived
    assert listed == ["run-running-held", "run-legacy-held"]


def test_repo_seat_finds_a_hold_whose_status_word_is_not_held(tmp_path):
    runs_dir = tmp_path / ".brr" / "runs"
    _run(runs_dir, "run-seat", status="running", hold=_hold(armed_at=100.0))

    seat = daemon._repo_seat(runs_dir, account_home=tmp_path / ".brr")

    assert seat is not None and seat.id == "run-seat"


def test_mark_interrupted_runs_leaves_a_parked_run_alone(tmp_path):
    write_repo_scaffold(tmp_path)
    runs_dir = tmp_path / ".brr" / "runs"
    parked = _run(runs_dir, "run-parked", status="pending", hold=_hold(),
                  pid=_dead_pid(), age_seconds=25 * 3600)
    ctx = daemon.account.resolve_context(tmp_path, {})

    assert daemon._mark_interrupted_runs(ctx, tmp_path, {}) == 0
    assert Run.from_file(runs_dir / parked.id / "run.md").status == "pending"

    # control: the same frozen run without a hold is still marked
    _run(runs_dir, "run-frozen", status="pending", pid=_dead_pid(),
         age_seconds=25 * 3600)
    assert daemon._mark_interrupted_runs(ctx, tmp_path, {}) == 1


def test_zombie_manifest_reaper_leaves_a_parked_run_alone(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo, {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    now = time.time()
    runs_dir = daemon.gitops.shared_brr_dir(repo) / "runs"
    parked = _run(runs_dir, "run-parked", status="running", hold=_hold())
    ancient = _run(runs_dir, "run-ancient", status="running")
    for task in (parked, ancient):
        path = runs_dir / task.id / "run.md"
        os.utime(path, (now - 2 * 86400, now - 2 * 86400))

    reaped = daemon._reap_zombie_run_manifests(ctx, now=now)

    assert [p.parent.name for p in reaped] == ["run-ancient"]
    assert Run.from_file(runs_dir / parked.id / "run.md").status == "running"


def test_processing_event_of_a_parked_run_is_proven_orphaned(tmp_path):
    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    # A live recorded pid would keep an ordinary running run "not orphaned";
    # a parked run is finished work for its event, exactly as `held` was.
    _run(runs_dir, "run-parked", status="running", hold=_hold(), pid=os.getpid())
    _run(runs_dir, "run-live", status="running", pid=os.getpid())

    def orphaned(run_id: str) -> bool:
        return daemon._processing_event_is_orphaned(
            {"id": f"evt-{run_id}", "run_id": run_id}, brr_dir,
            live_run_ids=set(), now=time.time(),
        )

    assert orphaned("run-parked") is True
    assert orphaned("run-live") is False


def test_operator_history_labels_a_parked_run_held(tmp_path):
    runs_dir = tmp_path / ".brr" / "runs"
    parked = _run(runs_dir, "run-parked", status="running", hold=_hold())
    ended = _run(runs_dir, "run-ended", status="done", hold=_hold())

    parked_view = history._summary_from_task(parked, runs_dir / parked.id, "Gurio/brr")
    ended_view = history._summary_from_task(ended, runs_dir / ended.id, "Gurio/brr")

    assert parked_view.label.startswith("held · ")
    assert parked_view.manifest["status"] == "held"
    assert ended_view.label.startswith("done · ")


def test_teardown_keeps_a_parked_runs_spawn_event(tmp_path, monkeypatch):
    """`_run_worker_and_finalize` tells `_retire_internal_event` the run
    parked — from the hold record, not the status word."""
    write_repo_scaffold(tmp_path)
    runs_dir = tmp_path / ".brr" / "runs"
    parked = _run(runs_dir, "run-parked", status="running", hold=_hold())
    retired: list[dict] = []

    monkeypatch.setattr(daemon, "_run_worker", lambda *_a, **_k: parked)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(
        daemon, "_retire_internal_event",
        lambda event, responses_dir, **kw: retired.append(kw) or True,
    )
    event = make_event(tmp_path, eid="evt-run-parked", source="schedule", body="tick")

    daemon._run_worker_and_finalize(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert retired and retired[-1]["parked"] is True
    # and the letter records the outcome from the same record
    assert event.get("run_outcome") == resource_hold.RUN_STATUS
