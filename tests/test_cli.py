"""Tests for CLI dispatch."""

import pytest

from brr.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


@pytest.mark.parametrize("command", ["status", "inspect", "docs", "streams", "stream", "eject"])
def test_removed_diagnostic_commands_are_not_public(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main([command])
    assert exc.value.code == 2


def test_run_requires_instruction():
    with pytest.raises(SystemExit):
        main(["run"])


def test_up_dev_reload_flag_passes_to_daemon(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr("brr.cli._repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "brr.daemon.start",
        lambda repo_root, *, dev_reload=None: calls.append(
            (repo_root, dev_reload),
        ),
    )

    main(["up", "--dev-reload"])

    assert calls == [(tmp_path, True)]


def test_bind_dispatches_to_gate_bind(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def bind(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["bind", "telegram"])

    assert calls == [tmp_path / ".brr"]


def test_setup_dispatches_to_gate_setup(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def setup(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["setup", "telegram"])

    assert calls == [tmp_path / ".brr"]


def test_setup_falls_back_to_auth_then_bind(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def auth(brr_dir):
            calls.append(("auth", brr_dir))

        @staticmethod
        def bind(brr_dir):
            calls.append(("bind", brr_dir))

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["setup", "telegram"])

    assert calls == [
        ("auth", tmp_path / ".brr"),
        ("bind", tmp_path / ".brr"),
    ]


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
        env="worktree",
        status="done",
        source="telegram",
        meta={
            "branch_name": "brr/task-123-abc",
            "response_path": str(tmp_path / ".brr" / "responses" / "evt-99.md"),
            "trace_dirs": "traces/daemon-run/evt-99-attempt-1",
        },
    )
    task.save(tasks_dir)
    (tmp_path / ".brr" / "responses" / "evt-99.md").write_text("result\n")

    output = inspect_task("task-123-abc", tmp_path)
    assert "task-123-abc" in output
    assert "evt-99" in output
    assert "done" in output
    assert "brr/task-123-abc" in output
    assert "Traces:" in output
    assert "daemon-run" in output
    assert "Event file:" in output
    assert "Latest prompt:" in output

    verbose = inspect_task("task-123-abc", tmp_path, show_event_body=True, show_prompt=True)
    assert "Event body:" in verbose
    assert "original event" in verbose
    assert "runner prompt" in verbose


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


def test_inspect_task_includes_conversation_context(tmp_path):
    from brr import conversations
    from brr.status import inspect_task
    from brr.task import Task

    brr_dir = tmp_path / ".brr"
    tasks_dir = brr_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    (brr_dir / "responses").mkdir(parents=True)
    (brr_dir / "inbox").mkdir(parents=True)

    conv_key = "telegram:42:"
    conversations.append_artifact(
        brr_dir, conv_key,
        kind="response", path=str(brr_dir / "responses" / "evt-x.md"),
        task_id="task-conv-x", label="response:evt-x",
    )

    task = Task(
        id="task-conv-x", event_id="evt-x", body="fix",
        status="done", source="telegram",
        conversation_key=conv_key,
    )
    task.save(tasks_dir)

    output = inspect_task("task-conv-x", tmp_path)
    assert "Conv:     telegram:42:" in output
    assert "response:evt-x" in output


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
