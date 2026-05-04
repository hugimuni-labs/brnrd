"""Tests for the gate-agnostic run progress projection."""

from __future__ import annotations

from pathlib import Path

from brr import run_progress, stream as stream_mod, updates


def _seed_stream(brr_dir: Path, sid: str = "stream-rp-1") -> stream_mod.StreamManifest:
    manifest = stream_mod.StreamManifest(
        id=sid,
        title="Refactor login flow",
        status="active",
        intent="Make login testable",
        gate_context={"source": "telegram", "telegram_chat_id": 123},
        reply_route={
            "preferred": "input_gate",
            "selected": "input_gate",
            "allowed": ["input_gate", "git_pr"],
        },
    )
    stream_mod.save_manifest(brr_dir, manifest)
    return manifest


def _emit(brr_dir: Path, sid: str, ptype: str, **payload):
    updates.emit(
        brr_dir,
        updates.UpdatePacket(type=ptype, stream_id=sid, payload=payload),
    )


def test_project_task_returns_none_when_stream_missing(tmp_path):
    view = run_progress.project_task(tmp_path / ".brr", "stream-x", "task-x")
    assert view is None


def test_project_task_succeeds_through_full_lifecycle(tmp_path):
    brr_dir = tmp_path / ".brr"
    manifest = _seed_stream(brr_dir)
    sid = manifest.id
    stream_mod.append_task(
        brr_dir, sid,
        task_id="task-1", event_id="evt-1",
        branch="auto", env="docker", status="running",
        base_branch="main", branch_name="brr/task-1",
    )
    _emit(brr_dir, sid, "task_created", task_id="task-1", event_id="evt-1",
          branch="auto", env="docker")
    _emit(brr_dir, sid, "triage_done", task_id="task-1", branch="auto", env="docker")
    _emit(brr_dir, sid, "env_prepared", task_id="task-1", env="docker",
          branch_name="brr/task-1")
    _emit(brr_dir, sid, "container_started", task_id="task-1",
          env="docker", container="brr-task-1-evt-1-attempt-1")
    _emit(brr_dir, sid, "attempt_started", task_id="task-1", attempt=1)
    _emit(brr_dir, sid, "run_started", task_id="task-1", branch="brr/task-1",
          env="docker")
    _emit(brr_dir, sid, "artifact_created", task_id="task-1", kind="response",
          path="/tmp/r.md", label="response:evt-1")
    stream_mod.append_artifact(
        brr_dir, sid,
        kind="response", path="/tmp/r.md",
        task_id="task-1", label="response:evt-1",
    )
    _emit(brr_dir, sid, "finalizing", task_id="task-1", stage="done")
    _emit(brr_dir, sid, "done", task_id="task-1", event_id="evt-1")

    view = run_progress.project_task(brr_dir, sid, "task-1")
    assert view is not None
    assert view.state == "succeeded"
    assert view.phase == "delivered"
    assert view.is_terminal is True
    assert view.branch == "auto"
    assert view.branch_name == "brr/task-1"
    assert view.env == "docker"
    assert view.attempt == 1
    assert view.response_path == "/tmp/r.md"
    assert "brr-task-1-evt-1-attempt-1" in view.container_ids
    assert view.title == "Refactor login flow"


def test_project_task_failed_with_retry(tmp_path):
    brr_dir = tmp_path / ".brr"
    manifest = _seed_stream(brr_dir, "stream-rp-fail")
    sid = manifest.id
    stream_mod.append_task(
        brr_dir, sid,
        task_id="task-2", event_id="evt-2",
        branch="current", env="host", status="running",
    )
    _emit(brr_dir, sid, "task_created", task_id="task-2", event_id="evt-2",
          branch="current", env="host")
    _emit(brr_dir, sid, "attempt_started", task_id="task-2", attempt=1)
    _emit(brr_dir, sid, "attempt_failed", task_id="task-2", attempt=1,
          reason="missing required output(s): response:evt-2", will_retry=True)
    _emit(brr_dir, sid, "retrying", task_id="task-2", attempt=2,
          reason="missing required output(s): response:evt-2")
    _emit(brr_dir, sid, "attempt_started", task_id="task-2", attempt=2)
    _emit(brr_dir, sid, "attempt_failed", task_id="task-2", attempt=2,
          reason="missing required output(s)", will_retry=False)
    _emit(brr_dir, sid, "failed", task_id="task-2", event_id="evt-2", stage="run")

    view = run_progress.project_task(brr_dir, sid, "task-2")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "failed"
    assert view.attempt == 2


def test_project_task_needs_context(tmp_path):
    brr_dir = tmp_path / ".brr"
    manifest = _seed_stream(brr_dir, "stream-rp-nc")
    sid = manifest.id
    _emit(brr_dir, sid, "task_created", task_id="task-3", branch="current", env="host")
    _emit(brr_dir, sid, "needs_context", task_id="task-3", event_id="evt-3")

    view = run_progress.project_task(brr_dir, sid, "task-3")
    assert view is not None
    assert view.state == "needs_context"
    assert view.phase == "needs_context"
    assert view.status_label() == "needs context"


def test_project_task_conflict(tmp_path):
    brr_dir = tmp_path / ".brr"
    manifest = _seed_stream(brr_dir, "stream-rp-conf")
    sid = manifest.id
    _emit(brr_dir, sid, "task_created", task_id="task-4", branch="auto", env="worktree")
    _emit(brr_dir, sid, "done", task_id="task-4")  # finalize then conflict
    _emit(brr_dir, sid, "conflict", task_id="task-4", branch="brr/task-4")

    view = run_progress.project_task(brr_dir, sid, "task-4")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "conflict"
    assert view.status_label() == "conflict"
    assert "brr/task-4" in view.detail


def test_project_task_container_preserved(tmp_path):
    brr_dir = tmp_path / ".brr"
    manifest = _seed_stream(brr_dir, "stream-rp-pres")
    sid = manifest.id
    _emit(brr_dir, sid, "task_created", task_id="task-5", branch="current",
          env="docker")
    _emit(brr_dir, sid, "container_preserved", task_id="task-5",
          containers=["brr-task-5-attempt-1", "brr-task-5-attempt-2"])
    _emit(brr_dir, sid, "failed", task_id="task-5", stage="run")

    view = run_progress.project_task(brr_dir, sid, "task-5")
    assert view is not None
    assert view.state == "failed"
    assert view.container_ids == [
        "brr-task-5-attempt-1",
        "brr-task-5-attempt-2",
    ]


def test_project_stream_latest_picks_most_recent_task(tmp_path):
    brr_dir = tmp_path / ".brr"
    manifest = _seed_stream(brr_dir, "stream-rp-latest")
    sid = manifest.id
    stream_mod.append_task(
        brr_dir, sid,
        task_id="task-old", event_id="evt-old",
        branch="current", env="host", status="done",
    )
    stream_mod.append_task(
        brr_dir, sid,
        task_id="task-new", event_id="evt-new",
        branch="auto", env="docker", status="running",
    )
    _emit(brr_dir, sid, "task_created", task_id="task-new", branch="auto",
          env="docker")
    _emit(brr_dir, sid, "run_started", task_id="task-new")

    view = run_progress.project_stream_latest(brr_dir, sid)
    assert view is not None
    assert view.task_id == "task-new"
    assert view.state == "active"


def test_project_stream_latest_returns_empty_when_no_tasks(tmp_path):
    brr_dir = tmp_path / ".brr"
    manifest = _seed_stream(brr_dir, "stream-rp-empty")
    view = run_progress.project_stream_latest(brr_dir, manifest.id)
    assert view is not None
    assert view.task_id is None
    assert view.title == "Refactor login flow"


def test_render_text_compact_includes_essentials(tmp_path):
    brr_dir = tmp_path / ".brr"
    manifest = _seed_stream(brr_dir, "stream-rp-render")
    sid = manifest.id
    _emit(brr_dir, sid, "task_created", task_id="task-r", branch="auto",
          env="docker")
    _emit(brr_dir, sid, "env_prepared", task_id="task-r", env="docker",
          branch_name="brr/task-r")
    _emit(brr_dir, sid, "attempt_started", task_id="task-r", attempt=1)
    _emit(brr_dir, sid, "run_started", task_id="task-r")

    view = run_progress.project_task(brr_dir, sid, "task-r")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    assert "brr" in text
    assert "task-r" in text
    assert "running" in text
    assert "Refactor login flow" in text
    assert "phase: running" in text
    assert "branch: brr/task-r" in text
    assert "env: docker" in text


def test_task_id_from_packet():
    packet = updates.UpdatePacket(
        type="task_created", stream_id="s", payload={"task_id": "task-x"}
    )
    assert run_progress.task_id_from_packet(packet) == "task-x"

    empty = updates.UpdatePacket(type="event_received", stream_id="s")
    assert run_progress.task_id_from_packet(empty) is None
