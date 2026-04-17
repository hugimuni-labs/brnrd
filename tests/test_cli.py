"""Tests for CLI dispatch."""

import pytest

from brr.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


def test_status_outside_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["status"])
    assert "not in a git repo" in capsys.readouterr().out


def test_run_requires_instruction():
    with pytest.raises(SystemExit):
        main(["run"])


def test_inspect_task(tmp_path):
    from brr.status import inspect_task
    from brr.task import Task

    tasks_dir = tmp_path / ".brr" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tmp_path / ".brr" / "responses").mkdir(parents=True)

    task = Task(
        id="task-123-abc",
        event_id="evt-99",
        body="fix the bug",
        branch="auto",
        env="worktree",
        status="done",
        source="telegram",
        meta={
            "branch_name": "brr/task-123-abc",
            "response_path": str(tmp_path / ".brr" / "responses" / "evt-99.md"),
            "trace_dirs": "traces/triage/evt-99-xxx, traces/daemon-run/evt-99-attempt-1-yyy",
        },
    )
    task.save(tasks_dir)
    (tmp_path / ".brr" / "responses" / "evt-99.md").write_text("---\n---\nresult\n")

    output = inspect_task("task-123-abc", tmp_path)
    assert "task-123-abc" in output
    assert "evt-99" in output
    assert "done" in output
    assert "brr/task-123-abc" in output
    assert "Traces:" in output
    assert "triage" in output
    assert "daemon-run" in output


def test_inspect_task_not_found(tmp_path):
    from brr.status import inspect_task

    (tmp_path / ".brr" / "tasks").mkdir(parents=True)
    output = inspect_task("nonexistent", tmp_path)
    assert "No task found" in output


def test_inspect_task_partial_match(tmp_path):
    from brr.status import inspect_task
    from brr.task import Task

    tasks_dir = tmp_path / ".brr" / "tasks"
    tasks_dir.mkdir(parents=True)

    task = Task(id="task-12345-xyz", event_id="evt-1", body="test", status="done")
    task.save(tasks_dir)

    output = inspect_task("12345", tmp_path)
    assert "task-12345-xyz" in output
