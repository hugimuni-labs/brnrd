"""Tests for durable-frame exposure of ending attempt failure (issue #2151).

Proves that the durable account run state.md includes the exact structured
cause/exit from the ending attempt's failure, distinguishable from cleanup error.
"""

import sys
import json
from pathlib import Path
from dataclasses import replace

import pytest

from brr import daemon, worker, runner
from brr.worker import Attempt

sys.path.insert(0, str(Path(__file__).parent))
import test_worker_phases as phases
from test_daemon import _account_context_for_policy


def test_structured_failure_reaches_durable_frame(tmp_path, monkeypatch):
    """Ending attempt's codex_task_error and exit code appear in state.md frontmatter."""
    cause = {"kind": "server_overloaded", "message": "Selected model is at capacity. Please try a different model."}
    def invoke(ctx, runner_name, invocation, cfg, *, trace=False):
        return phases._result(invocation, runner_name, code=1,
            stderr="Unknown process id 30097\ncodex task_complete error (server overloaded): " + cause["message"],
            codex_task_error=cause)
    
    p = phases._prepared(tmp_path, monkeypatch, invoke)
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    
    records = []
    original = p.emit
    def emit(packet_type, **data):
        records.append({"kind": "update", "type": packet_type, "conversation_key": p.task.conversation_key, **data})
        original(packet_type, **data)
    for key in ("conversation_key", "brr_dir", "event_id"):
        setattr(emit, key, getattr(original, key))
    p = replace(p, emit=emit)
    
    b = phases._to_boundary(p, Attempt(n=1, lane=p.lane))
    assert b.kind == "exhausted"
    assert b.attempt.last_failure["codex_task_error"] == cause
    assert b.attempt.last_failure["exit_code"] == 1
    assert cause["message"] in b.attempt.last_failure["error"]
    
    ended = worker.finalize(p, b)
    assert ended.task.status == "error"
    
    frame_path = daemon._persist_run_state_doc(
        _account_context_for_policy(tmp_path), 
        ended.task,
        repo_label="Gurio/brr",
        stage="failed",
        work_dir=tmp_path,
        outbox_dir=p.outbox_dir
    )
    assert frame_path is not None
    frame = frame_path.read_text()
    
    # The durable frame should now contain the structured failure
    assert "status: error" in frame
    assert "ending_failure:" in frame
    assert "server_overloaded" in frame
    assert cause["message"] in frame
    assert '"exit_code": 1' in frame
    assert "codex_task_error:" in frame
    
    # Verify the JSON structure is valid by finding and parsing the complete JSON
    ending_failure_start = frame.find("ending_failure: ")
    if ending_failure_start != -1:
        # Find the start of the JSON object
        json_start = frame.find("{", ending_failure_start)
        if json_start != -1:
            # Count braces to find the matching closing brace
            brace_count = 0
            json_end = json_start
            for i, char in enumerate(frame[json_start:]):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = json_start + i + 1
                        break
            
            ending_failure_json = frame[json_start:json_end]
            ending_failure_data = json.loads(ending_failure_json)
            assert ending_failure_data["codex_task_error"] == cause
            assert ending_failure_data["exit_code"] == 1
            assert cause["message"] in ending_failure_data["error"]


def test_clean_completion_has_no_ending_failure(tmp_path, monkeypatch):
    """A successful run does not have ending_failure in its durable frame."""
    def invoke(ctx, runner_name, invocation, cfg, *, trace=False):
        return phases._result(invocation, runner_name, code=0, stdout="Task completed successfully")
    
    p = phases._prepared(tmp_path, monkeypatch, invoke)
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    
    b = phases._to_boundary(p, Attempt(n=1, lane=p.lane))
    assert b.kind == "completed"
    
    ended = worker.finalize(p, b)
    assert ended.task.status == "done"
    
    frame_path = daemon._persist_run_state_doc(
        _account_context_for_policy(tmp_path), 
        ended.task,
        repo_label="Gurio/brr",
        stage="done",
        work_dir=tmp_path,
        outbox_dir=p.outbox_dir
    )
    assert frame_path is not None
    frame = frame_path.read_text()
    
    # Clean runs should not have ending failure info
    assert "ending_failure:" not in frame
    assert "ending_codex_task_error:" not in frame
    assert "status: done" in frame


if __name__ == "__main__":
    pytest.main([__file__, "-v"])