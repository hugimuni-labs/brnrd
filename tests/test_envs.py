import pytest

from brr import envs
from brr.runner import RunnerArtifactSpec, RunnerInvocation
from brr.task import Task


def test_get_env_returns_real_builtins():
    assert envs.get_env("host").name == "host"
    assert envs.get_env("worktree").name == "worktree"
    assert envs.get_env("docker").name == "docker"


def test_get_env_rejects_unknown_backend():
    with pytest.raises(envs.UnsupportedEnvironmentError) as exc:
        envs.get_env("firecracker")

    assert "environment backend 'firecracker' is not available yet" in str(exc.value)


def test_docker_prepare_requires_cli_and_image(tmp_path, monkeypatch):
    backend = envs.get_env("docker")
    task = Task(id="task-1", event_id="evt-1", body="run in docker")
    response_path = tmp_path / ".brr" / "responses" / "evt-1.md"

    monkeypatch.setattr(envs.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="Docker CLI"):
        backend.prepare(
            task, tmp_path, {},
            branch_name=None, base_branch="main", response_path=response_path,
        )

    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    with pytest.raises(RuntimeError, match="docker.image"):
        backend.prepare(
            task, tmp_path, {},
            branch_name=None, base_branch="main", response_path=response_path,
        )


def test_docker_prepare_current_branch_uses_repo_root(tmp_path, monkeypatch):
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    (tmp_path / ".brr" / "responses").mkdir(parents=True)
    response_path = tmp_path / ".brr" / "responses" / "evt-1.md"
    task = Task(id="task-1", event_id="evt-1", body="run in docker")

    ctx = envs.get_env("docker").prepare(
        task, tmp_path, {"docker.image": "brr/test-runner:latest", "docker.network": "none"},
        branch_name=None, base_branch="main", response_path=response_path,
    )

    assert ctx.name == "docker"
    assert ctx.cwd == tmp_path
    assert ctx.repo_root == tmp_path
    assert ctx.response_path_host == response_path
    assert ctx.response_path_env == response_path
    assert ctx.env_state["docker_image"] == "brr/test-runner:latest"
    assert ctx.env_state["docker_network"] == "none"
    assert task.meta["docker_image"] == "brr/test-runner:latest"


def test_docker_prepare_branch_uses_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(envs.gitops, "branch_exists", lambda *_args: False)
    worktree_path = tmp_path / ".brr" / "worktrees" / "task-2"
    created = []
    monkeypatch.setattr(
        envs.worktree,
        "create",
        lambda *args, **kwargs: created.append((args, kwargs)) or worktree_path,
    )
    task = Task(id="task-2", event_id="evt-2", body="change code", branch="auto")
    response_path = tmp_path / ".brr" / "responses" / "evt-2.md"

    ctx = envs.get_env("docker").prepare(
        task, tmp_path, {"docker.image": "brr/test-runner:latest"},
        branch_name="brr/task-2", base_branch="main", response_path=response_path,
    )

    assert ctx.name == "docker"
    assert ctx.cwd == worktree_path
    assert ctx.branch_name == "brr/task-2"
    assert ctx.log_file == "kb/log-task-2.md"
    assert task.meta["worktree_path"] == str(worktree_path)
    assert created[0][0][:3] == (tmp_path, "task-2", "brr/task-2")


def test_docker_invoke_wraps_runner_command(tmp_path, monkeypatch):
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    response_path = tmp_path / ".brr" / "responses" / "evt-3.md"
    response_path.parent.mkdir(parents=True)
    task = Task(id="task-3", event_id="evt-3", body="run in docker")
    ctx = envs.get_env("docker").prepare(
        task, tmp_path, {"docker.image": "brr/test-runner:latest"},
        branch_name=None, base_branch="main", response_path=response_path,
    )
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return envs.subprocess.CompletedProcess(command, 0, "agent reply\n", "")

    monkeypatch.setattr(envs.subprocess, "run", fake_run)
    invocation = RunnerInvocation(
        kind="daemon-run",
        label="evt-3-attempt-1",
        prompt="hello",
        cwd=ctx.cwd,
        repo_root=tmp_path,
        response_path=str(response_path),
    )

    result = envs.get_env("docker").invoke(
        ctx,
        "mock-runner",
        invocation,
        {"docker.image": "brr/test-runner:latest", "runner_cmd": ["mock", "--flag", "{prompt}"]},
    )

    assert result.returncode == 0
    assert result.validation_ok
    assert response_path.read_text(encoding="utf-8") == "agent reply\n"
    command = commands[0]
    assert command[:2] == ["docker", "run"]
    assert "--name" in command
    assert command[command.index("--network") + 1] == "bridge"
    assert command[command.index("-v") + 1] == f"{tmp_path}:{tmp_path}"
    assert command[command.index("-w") + 1] == str(tmp_path)
    assert command[-4:] == ["brr/test-runner:latest", "mock", "--flag", "hello"]
    assert ctx.env_state["docker_containers"] == ["brr-task-3-evt-3-attempt-1"]


def test_docker_finalize_removes_containers_after_success(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(
        envs.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command)
        or envs.subprocess.CompletedProcess(command, 0, "", ""),
    )
    task = Task(id="task-4", event_id="evt-4", body="done", status="done")
    ctx = envs.RunContext(
        name="docker",
        cwd=tmp_path,
        repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr" / "responses" / "evt-4.md",
        response_path_env=tmp_path / ".brr" / "responses" / "evt-4.md",
        env_state={"docker_containers": ["brr-task-4-evt-4-attempt-1"]},
    )

    envs.get_env("docker").finalize(ctx, task, tmp_path / ".brr" / "tasks")

    assert commands == [["docker", "rm", "-f", "brr-task-4-evt-4-attempt-1"]]


def test_docker_finalize_preserves_containers_on_error(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(
        envs.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command)
        or envs.subprocess.CompletedProcess(command, 0, "", ""),
    )
    task = Task(id="task-5", event_id="evt-5", body="failed", status="error")
    ctx = envs.RunContext(
        name="docker",
        cwd=tmp_path,
        repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr" / "responses" / "evt-5.md",
        response_path_env=tmp_path / ".brr" / "responses" / "evt-5.md",
        env_state={"docker_containers": ["brr-task-5-evt-5-attempt-1"]},
    )

    envs.get_env("docker").finalize(ctx, task, tmp_path / ".brr" / "tasks")

    assert commands == []
    assert task.meta["docker_containers"] == "brr-task-5-evt-5-attempt-1"
    persisted = Task.from_file(tmp_path / ".brr" / "tasks" / "task-5.md")
    assert persisted is not None
    assert persisted.meta["docker_containers"] == "brr-task-5-evt-5-attempt-1"
