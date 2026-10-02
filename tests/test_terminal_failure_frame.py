"""Drive ending-failure persistence through real worker phases and cold reload.

Runner/profile/environment/prompt inputs use the worker test scaffold; this
is downstream proof, not a recorded native exec or await/restart incident.
"""
import json
from pathlib import Path

import pytest

from brr import daemon, runner_failures, worker
from brr.run import Run, run_manifest_path
from brr.runner import RunnerArtifactRecord
from brr.worker import Attempt
from _helpers import succeed_invoke
from test_daemon import _account_context_for_policy
import test_worker_phases as phases


@pytest.fixture(autouse=True)
def _clean_controls(monkeypatch):
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _cold(p, boundary):
    ended = worker.finalize(p, boundary)
    cold = Run.from_file(run_manifest_path(p.runs_dir, ended.task.id))
    assert cold is not None
    assert cold.status == ended.task.status
    return cold


def _frame(tmp_path, task, p=None):
    path = daemon._persist_run_state_doc(
        _account_context_for_policy(tmp_path), task, repo_label="Gurio/brr",
        stage="finished", work_dir=tmp_path,
        outbox_dir=p.outbox_dir if p else None,
    )
    assert path is not None
    return path.read_text()


def _record(frame):
    section = frame.split("## Ending failure\n", 1)[1]
    return json.loads(section.split("```json\n", 1)[1].split("\n```", 1)[0])


def test_structured_cause_survives_producer_save_reload_and_frame(tmp_path, monkeypatch):
    cause = {"kind": "server_overloaded", "message": "Capacity } cause\n---\n```\nindependent of teardown"}
    teardown = "Unknown process id 30097"

    def invoke(ctx, runner_name, invocation, cfg, *, trace=False):
        return phases._result(invocation, runner_name, code=1, stderr=teardown,
                              codex_task_error=cause)

    p = phases._prepared(tmp_path, monkeypatch, invoke)
    boundary = phases._to_boundary(p, Attempt(n=1, lane=p.lane))
    assert boundary.kind == "exhausted"
    cold = _cold(p, boundary)
    record = cold.meta["ending_failure"]
    assert isinstance(record, dict)
    assert record["codex_task_error"] == cause
    assert record["error"] == teardown
    assert record["exit_code"] == 1
    assert record["attempt"] == 1
    assert _record(_frame(tmp_path, cold, p)) == record
    assert "ending_codex_task_error" not in cold.meta


@pytest.mark.parametrize("recovered", [True, False])
def test_retry_history_is_not_the_ending_failure(tmp_path, monkeypatch, recovered):
    earlier = {"kind": "server_overloaded", "message": "earlier cause"}

    def invoke(ctx, runner_name, invocation, cfg, *, trace=False):
        if invocation.label.endswith("attempt-1"):
            return phases._result(
                invocation, runner_name, code=1,
                stdout="API Error: Connection closed mid-response. The response above may be incomplete.",
                codex_task_error=earlier,
            )
        if recovered:
            return succeed_invoke()(ctx, runner_name, invocation, cfg, trace=trace)
        return phases._result(invocation, runner_name, artifacts=[
            RunnerArtifactRecord(path=Path("out.md"), label="out.md", exists=False),
        ])

    p = phases._prepared(tmp_path, monkeypatch, invoke, max_retries=1)
    first = phases._to_boundary(p, Attempt(n=1, lane=p.lane))
    assert first.kind == "retry"
    ending = phases._to_boundary(p, first.next_attempt)
    assert ending.attempt.last_failure is None
    assert ending.attempt.failures[0]["codex_task_error"] == earlier
    cold = _cold(p, ending)
    frame = _frame(tmp_path, cold, p)
    if recovered:
        assert cold.status == "done"
        assert "ending_failure" not in cold.meta
        assert "## Ending failure" not in frame
    else:
        assert cold.status == "error"
        assert _record(frame) == {"attempt": 2, "failure_kind": runner_failures.NO_OUTPUT}
        assert "earlier cause" not in frame


@pytest.mark.parametrize("kind", [runner_failures.CORE_REFUSAL, runner_failures.INTERRUPTED])
def test_rendered_frame_applies_wording_policy_without_destroying_private_evidence(tmp_path, kind):
    private = {"attempt": 1, "failure_kind": kind, "exit_code": 1,
               "error": "private vendor wording", "codex_task_error": {
                   "kind": "vendor_kind", "message": "private vendor wording"}}
    task = Run(id="run-policy", event_id="evt-policy", body="synthetic", status="error",
               meta={"ending_failure": private})
    cold = Run.from_file(task.save(tmp_path / "runs"))
    assert cold.meta["ending_failure"] == private
    frame = _frame(tmp_path, cold)
    assert "private vendor wording" not in frame
    surfaced = _record(frame)
    assert surfaced["failure_kind"] == kind
    assert surfaced["exit_code"] == 1
    assert surfaced["codex_task_error"] == {"kind": "vendor_kind"}
    assert cold.meta["ending_failure"] == private


def test_ordinary_clean_finish_and_absent_account(tmp_path, monkeypatch):
    p = phases._prepared(tmp_path, monkeypatch)
    ending = phases._to_boundary(p, Attempt(n=1, lane=p.lane))
    assert ending.kind == "completed"
    cold = _cold(p, ending)
    assert "ending_failure" not in cold.meta
    assert "## Ending failure" not in _frame(tmp_path, cold, p)
    assert daemon._persist_run_state_doc(None, cold, repo_label="Gurio/brr", stage="finished") is None


def test_new_record_decode_does_not_change_other_json_strings(tmp_path):
    record = {"attempt": 1, "failure_kind": "runner_error", "exit_code": 1}
    task = Run(id="run-decode", event_id="evt-decode", body="synthetic",
               meta={"ending_failure": record, "run_state_digest": '{"keep": "string"}'})
    cold = Run.from_file(task.save(tmp_path / "runs"))
    assert cold.meta["ending_failure"] == record
    assert cold.meta["run_state_digest"] == '{"keep": "string"}'
    task.meta["ending_failure"] = "malformed record"
    cold = Run.from_file(task.save())
    assert cold.meta["ending_failure"] == "malformed record"
