"""Tests for the gate-agnostic run progress projection over conversation logs."""

from __future__ import annotations

from pathlib import Path

from brr import conversations, run_progress, updates


def _emit(brr_dir: Path, key: str, ptype: str, **payload):
    updates.emit(
        brr_dir,
        updates.UpdatePacket(type=ptype, conversation_key=key, payload=payload),
    )


def test_project_task_returns_none_when_conversation_missing(tmp_path):
    view = run_progress.project_task(tmp_path / ".brr", "no-such", "task-x")
    assert view is None


def test_project_task_succeeds_through_full_lifecycle(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:1:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-1", event_id="evt-1",
        env="docker", status="running",
        base_branch="main", branch_name="brr/task-1",
    )
    _emit(brr_dir, key, "task_created", task_id="task-1", event_id="evt-1",
          env="docker")
    _emit(brr_dir, key, "env_prepared", task_id="task-1", env="docker",
          branch_name="brr/task-1")
    _emit(brr_dir, key, "container_started", task_id="task-1",
          env="docker", container="brr-task-1-evt-1-attempt-1")
    _emit(brr_dir, key, "attempt_started", task_id="task-1", attempt=1)
    _emit(brr_dir, key, "run_started", task_id="task-1", branch="brr/task-1",
          env="docker")
    _emit(brr_dir, key, "artifact_created", task_id="task-1", kind="response",
          path="/tmp/r.md", label="response:evt-1")
    conversations.append_artifact(
        brr_dir, key,
        kind="response", path="/tmp/r.md",
        task_id="task-1", label="response:evt-1",
    )
    _emit(brr_dir, key, "finalizing", task_id="task-1", stage="done")
    _emit(brr_dir, key, "done", task_id="task-1", event_id="evt-1")

    view = run_progress.project_task(brr_dir, key, "task-1")
    assert view is not None
    assert view.state == "succeeded"
    assert view.phase == "delivered"
    assert view.is_terminal is True
    assert view.branch_name == "brr/task-1"
    assert view.env == "docker"
    assert view.attempt == 1
    assert view.response_path == "/tmp/r.md"
    assert "brr-task-1-evt-1-attempt-1" in view.container_ids


def test_project_task_failed_with_retry(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:2:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-2", event_id="evt-2",
        env="worktree", status="running",
    )
    _emit(brr_dir, key, "task_created", task_id="task-2", event_id="evt-2",
          env="worktree")
    _emit(brr_dir, key, "attempt_started", task_id="task-2", attempt=1)
    _emit(brr_dir, key, "attempt_failed", task_id="task-2", attempt=1,
          reason="missing required output(s): response:evt-2", will_retry=True)
    _emit(brr_dir, key, "retrying", task_id="task-2", attempt=2,
          reason="missing required output(s): response:evt-2")
    _emit(brr_dir, key, "attempt_started", task_id="task-2", attempt=2)
    _emit(brr_dir, key, "attempt_failed", task_id="task-2", attempt=2,
          reason="missing required output(s)", will_retry=False)
    _emit(brr_dir, key, "failed", task_id="task-2", event_id="evt-2", stage="run")

    view = run_progress.project_task(brr_dir, key, "task-2")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "failed"
    assert view.attempt == 2


def test_project_task_conflict(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:4:"
    _emit(brr_dir, key, "task_created", task_id="task-4", env="worktree")
    _emit(brr_dir, key, "done", task_id="task-4")
    _emit(brr_dir, key, "conflict", task_id="task-4", branch="brr/task-4")

    view = run_progress.project_task(brr_dir, key, "task-4")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "conflict"
    assert view.status_label() == "conflict"
    assert "brr/task-4" in view.detail


def test_project_task_failure_detail_survives_finalizing(tmp_path):
    """The daemon emits ``finalizing(stage=failed)`` before ``failed`` so
    the operator's view ends on the real error rather than the generic
    "finalizing (failed)" placeholder. The projection should fold them
    in that order and end with the failed packet's detail."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:f:"
    _emit(brr_dir, key, "task_created", task_id="task-f", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-f", attempt=1)
    _emit(brr_dir, key, "attempt_failed", task_id="task-f", attempt=1,
          reason="timed out", will_retry=False, exit_code=124, timed_out=True)
    _emit(brr_dir, key, "finalizing", task_id="task-f", stage="failed")
    _emit(brr_dir, key, "failed", task_id="task-f", stage="run", attempts=1,
          exit_code=124, timed_out=True,
          error="runner timed out after 3600s")

    view = run_progress.project_task(brr_dir, key, "task-f")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "failed"
    assert "timed out" in view.detail
    assert "runner timed out after 3600s" in view.detail
    assert "finalizing" not in view.detail
    assert view.error == "runner timed out after 3600s"


def test_project_task_container_preserved(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:5:"
    _emit(brr_dir, key, "task_created", task_id="task-5",
          env="docker")
    _emit(brr_dir, key, "container_preserved", task_id="task-5",
          containers=["brr-task-5-attempt-1", "brr-task-5-attempt-2"])
    _emit(brr_dir, key, "failed", task_id="task-5", stage="run")

    view = run_progress.project_task(brr_dir, key, "task-5")
    assert view is not None
    assert view.state == "failed"
    assert view.container_ids == [
        "brr-task-5-attempt-1",
        "brr-task-5-attempt-2",
    ]


def test_project_conversation_latest_picks_most_recent_task(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:6:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-old", event_id="evt-old",
        env="host", status="done",
    )
    conversations.append_task(
        brr_dir, key,
        task_id="task-new", event_id="evt-new",
        env="docker", status="running",
    )
    _emit(brr_dir, key, "task_created", task_id="task-new",
          env="docker")
    _emit(brr_dir, key, "run_started", task_id="task-new")

    view = run_progress.project_conversation_latest(brr_dir, key)
    assert view is not None
    assert view.task_id == "task-new"
    assert view.state == "active"


def test_project_conversation_latest_returns_none_when_no_tasks(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:7:"
    conversations.append_event(brr_dir, key, {"id": "evt-1", "source": "telegram"})
    view = run_progress.project_conversation_latest(brr_dir, key)
    assert view is None


def test_render_text_compact_is_terse(tmp_path):
    """Compact card is the chat surface — header + phase, nothing else.

    Branch / env / response paths are dev-side noise in a chat reply;
    they live in the verbose form for ``brr status`` and ``brr inspect``.
    """
    brr_dir = tmp_path / ".brr"
    key = "telegram:8:"
    _emit(brr_dir, key, "task_created", task_id="task-r",
          env="docker")
    _emit(brr_dir, key, "env_prepared", task_id="task-r", env="docker",
          branch_name="brr/task-r")
    _emit(brr_dir, key, "attempt_started", task_id="task-r", attempt=1)
    _emit(brr_dir, key, "run_started", task_id="task-r")

    view = run_progress.project_task(brr_dir, key, "task-r")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    assert "brr" in text
    assert "task-r" in text
    assert "running" in text
    assert "phase: running" in text
    assert "branch:" not in text
    assert "env:" not in text
    assert "last:" not in text
    assert "response:" not in text


def test_render_text_compact_shows_attempt_only_during_retry(tmp_path):
    """attempt: counter shows up only mid-flight, not after delivery."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8b:"
    _emit(brr_dir, key, "task_created", task_id="task-rt", env="worktree")
    _emit(brr_dir, key, "attempt_started", task_id="task-rt", attempt=2)

    view_active = run_progress.project_task(brr_dir, key, "task-rt")
    assert view_active is not None
    assert "attempt: 2" in run_progress.render_text(view_active, compact=True)

    _emit(brr_dir, key, "done", task_id="task-rt")
    view_done = run_progress.project_task(brr_dir, key, "task-rt")
    assert view_done is not None
    assert "attempt:" not in run_progress.render_text(view_done, compact=True)


def test_render_text_verbose_keeps_dev_fields(tmp_path):
    """Verbose mode (compact=False) keeps the operator-facing detail."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8c:"
    _emit(brr_dir, key, "task_created", task_id="task-v", env="docker")
    _emit(brr_dir, key, "env_prepared", task_id="task-v", env="docker",
          branch_name="brr/task-v")
    _emit(brr_dir, key, "run_started", task_id="task-v")
    _emit(brr_dir, key, "done", task_id="task-v")

    view = run_progress.project_task(brr_dir, key, "task-v")
    assert view is not None
    text = run_progress.render_text(view, compact=False)
    assert "branch: brr/task-v" in text
    assert "env: docker" in text


def test_render_text_compact_does_not_inject_conversation_identity(tmp_path):
    """Compact card must not surface arbitrary conversation strings.

    This is the key regression from the dropped streams design — old
    code rendered a frozen stream title/intent here. Now there is no
    such field, so any conversation identifier must not leak into the
    compact card unprompted.
    """
    brr_dir = tmp_path / ".brr"
    key = "telegram:99:USER CONTEXTUAL OVERRIDE"
    _emit(brr_dir, key, "task_created", task_id="task-c",
          env="docker")
    _emit(brr_dir, key, "failed", task_id="task-c", stage="env",
          error="docker env requires docker.image in .brr/config")

    view = run_progress.project_task(brr_dir, key, "task-c")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    assert "USER CONTEXTUAL OVERRIDE" not in text


def test_task_id_from_packet():
    packet = updates.UpdatePacket(
        type="task_created", conversation_key="k", payload={"task_id": "task-x"},
    )
    assert run_progress.task_id_from_packet(packet) == "task-x"

    empty = updates.UpdatePacket(type="event_received", conversation_key="k")
    assert run_progress.task_id_from_packet(empty) is None
