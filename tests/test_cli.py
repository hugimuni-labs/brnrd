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
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    (inbox_dir / "evt-99.md").write_text(
        "---\nid: evt-99\nstatus: done\nsource: telegram\n---\noriginal event\n",
        encoding="utf-8",
    )
    trace_dir = tmp_path / ".brr" / "traces" / "daemon-run" / "evt-99-attempt-1"
    trace_dir.mkdir(parents=True)
    (trace_dir / "prompt.md").write_text("runner prompt", encoding="utf-8")

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
            "trace_dirs": "traces/triage/evt-99-xxx, traces/daemon-run/evt-99-attempt-1",
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
    assert "Event file:" in output
    assert "Latest prompt:" in output

    verbose = inspect_task("task-123-abc", tmp_path, show_event_body=True, show_prompt=True)
    assert "Event body:" in verbose
    assert "original event" in verbose
    assert "runner prompt" in verbose


def test_inspect_task_recovers_event_body_from_triage_trace(tmp_path):
    from brr.status import inspect_task
    from brr.task import Task

    tasks_dir = tmp_path / ".brr" / "tasks"
    tasks_dir.mkdir(parents=True)
    triage_trace = tmp_path / ".brr" / "traces" / "triage" / "evt-99-triage"
    triage_trace.mkdir(parents=True)
    (triage_trace / "prompt.md").write_text(
        "triage prompt\n---\nEvent ID: evt-99\n\noriginal event from trace",
        encoding="utf-8",
    )
    task = Task(
        id="task-123-abc",
        event_id="evt-99",
        body="fix",
        status="done",
        meta={"trace_dirs": "traces/triage/evt-99-triage"},
    )
    task.save(tasks_dir)

    output = inspect_task("task-123-abc", tmp_path, show_event_body=True)

    assert "Event file:" in output
    assert "(missing)" in output
    assert "original event from trace" in output


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


def test_inspect_task_from_worktree_uses_shared_runtime(tmp_path):
    import subprocess

    from brr.status import inspect_task
    from brr.task import Task

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    tasks_dir = repo / ".brr" / "tasks"
    tasks_dir.mkdir(parents=True)
    task = Task(id="task-123-abc", event_id="evt-99", body="fix", status="done")
    task.save(tasks_dir)

    worktree = repo / ".brr" / "worktrees" / "task-123-abc"
    subprocess.run(
        ["git", "worktree", "add", "-b", "brr/task-123-abc", str(worktree), "HEAD"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )

    try:
        output = inspect_task("task-123-abc", worktree)
        assert "task-123-abc" in output
        assert "evt-99" in output
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo, check=True)
        subprocess.run(["git", "branch", "-D", "brr/task-123-abc"], cwd=repo, check=True, stdout=subprocess.PIPE)
