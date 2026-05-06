import pytest

from brr import envs
from brr.runner import RunnerInvocation
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
            base_branch="main", response_path=response_path,
        )

    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    with pytest.raises(RuntimeError, match="docker.image"):
        backend.prepare(
            task, tmp_path, {},
            base_branch="main", response_path=response_path,
        )


def test_docker_prepare_creates_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    worktree_path = tmp_path / ".brr" / "worktrees" / "task-2"
    created = []
    monkeypatch.setattr(
        envs.worktree,
        "create",
        lambda repo_root, task_id: created.append((repo_root, task_id))
        or (worktree_path, f"brr/{task_id}"),
    )
    task = Task(id="task-2", event_id="evt-2", body="change code")
    response_path = tmp_path / ".brr" / "responses" / "evt-2.md"

    ctx = envs.get_env("docker").prepare(
        task, tmp_path, {"docker.image": "brr/test-runner:latest"},
        base_branch="main", response_path=response_path,
    )

    assert ctx.name == "docker"
    assert ctx.cwd == worktree_path
    assert ctx.branch_name == "brr/task-2"
    assert ctx.log_file == "kb/log-task-2.md"
    assert task.meta["worktree_path"] == str(worktree_path)
    assert task.meta["branch_name"] == "brr/task-2"
    assert created == [(tmp_path, "task-2")]


def _isolate_docker_creds(monkeypatch, tmp_path):
    """Make docker invocations independent of the test host's HOME.

    Points HOME at an empty directory and clears all known runner env
    vars so credential mounts and -e passthroughs only appear when a
    test explicitly opts in.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    for name in envs._DOCKER_DEFAULT_PASSTHROUGH_ENV:
        monkeypatch.delenv(name, raising=False)
    return fake_home


def _stub_worktree(monkeypatch, tmp_path):
    """Replace worktree.create with a stub that just makes the dir."""
    def _create(_repo_root, task_id):
        path = tmp_path / ".brr" / "worktrees" / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path, f"brr/{task_id}"

    monkeypatch.setattr(envs.worktree, "create", _create)


def test_docker_invoke_wraps_runner_command(tmp_path, monkeypatch):
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    response_path = tmp_path / ".brr" / "responses" / "evt-3.md"
    response_path.parent.mkdir(parents=True)
    task = Task(id="task-3", event_id="evt-3", body="run in docker")
    ctx = envs.get_env("docker").prepare(
        task, tmp_path, {"docker.image": "brr/test-runner:latest"},
        base_branch="main", response_path=response_path,
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
    assert command[command.index("-w") + 1] == str(ctx.cwd)
    assert command[-4:] == ["brr/test-runner:latest", "mock", "--flag", "hello"]
    assert ctx.env_state["docker_containers"] == ["brr-task-3-evt-3-attempt-1"]
    forwarded = [command[i + 1] for i, arg in enumerate(command) if arg == "-e"]
    # The git safe.directory wiring is unconditional; runner credential
    # env vars are only forwarded when set on the daemon (none here).
    assert forwarded == [
        "GIT_CONFIG_COUNT=1",
        "GIT_CONFIG_KEY_0=safe.directory",
        "GIT_CONFIG_VALUE_0=*",
    ]


def test_docker_invoke_injects_git_safe_directory(tmp_path, monkeypatch):
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)

    command = _build_docker_invoke(tmp_path, monkeypatch)

    forwarded = [command[i + 1] for i, arg in enumerate(command) if arg == "-e"]
    assert "GIT_CONFIG_COUNT=1" in forwarded
    assert "GIT_CONFIG_KEY_0=safe.directory" in forwarded
    assert "GIT_CONFIG_VALUE_0=*" in forwarded


def _build_docker_invoke(tmp_path, monkeypatch, *, cfg_extra=None, label="evt-x-1"):
    """Helper: prepare a DockerEnv ctx + invocation and capture the docker run argv."""
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    response_path = tmp_path / ".brr" / "responses" / "evt-x.md"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    task = Task(id=f"task-{label}", event_id="evt-x", body="run in docker")
    cfg: dict = {"docker.image": "brr/test-runner:latest"}
    if cfg_extra:
        cfg.update(cfg_extra)
    ctx = envs.get_env("docker").prepare(
        task, tmp_path, cfg,
        base_branch="main", response_path=response_path,
    )
    commands = []
    monkeypatch.setattr(
        envs.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command)
        or envs.subprocess.CompletedProcess(command, 0, "ok\n", ""),
    )
    invocation = RunnerInvocation(
        kind="daemon-run",
        label=label,
        prompt="hello",
        cwd=ctx.cwd,
        repo_root=tmp_path,
        response_path=str(response_path),
    )
    envs.get_env("docker").invoke(
        ctx, "mock-runner", invocation, {**cfg, "runner_cmd": ["mock", "{prompt}"]},
    )
    return commands[0]


def _passthrough_env_names(command: list[str]) -> list[str]:
    """Filter the docker run command to credential-passthrough names only.

    Credential passthroughs are emitted as ``-e NAME`` (no ``=`` — docker
    reads the value from the parent environment). The git safe.directory
    wiring uses ``-e KEY=VALUE``, which we exclude here so each test can
    assert on credential behaviour without being entangled with
    safe.directory.
    """
    return [
        command[i + 1]
        for i, arg in enumerate(command)
        if arg == "-e" and "=" not in command[i + 1]
    ]


def test_docker_invoke_passes_known_runner_env_when_set(tmp_path, monkeypatch):
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    command = _build_docker_invoke(tmp_path, monkeypatch)

    forwarded = _passthrough_env_names(command)
    assert "OPENAI_API_KEY" in forwarded
    assert "ANTHROPIC_API_KEY" not in forwarded
    assert "GEMINI_API_KEY" not in forwarded
    assert "GOOGLE_API_KEY" not in forwarded


def test_docker_invoke_passes_no_env_when_none_set(tmp_path, monkeypatch):
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)

    command = _build_docker_invoke(tmp_path, monkeypatch)

    assert _passthrough_env_names(command) == []


def test_docker_invoke_passthrough_extra_env_keys(tmp_path, monkeypatch):
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.setenv("CUSTOM_TOKEN", "tok-1")

    command = _build_docker_invoke(
        tmp_path, monkeypatch,
        cfg_extra={"docker.env": "CUSTOM_TOKEN, MISSING_TOKEN"},
    )

    assert _passthrough_env_names(command) == ["CUSTOM_TOKEN"]


def test_docker_invoke_extra_env_does_not_duplicate_defaults(tmp_path, monkeypatch):
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    command = _build_docker_invoke(
        tmp_path, monkeypatch,
        cfg_extra={"docker.env": "OPENAI_API_KEY,OPENAI_API_KEY"},
    )

    assert _passthrough_env_names(command) == ["OPENAI_API_KEY"]


def test_docker_invoke_mounts_credential_dirs_when_present(tmp_path, monkeypatch):
    fake_home = _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")
    (fake_home / ".codex").mkdir()
    # No ~/.gemini — confirms missing dirs don't show up.

    command = _build_docker_invoke(tmp_path, monkeypatch)

    mounts = [
        command[i + 1] for i, arg in enumerate(command) if arg == "-v"
    ]
    assert f"{fake_home}/.claude:/root/.claude" in mounts
    assert f"{fake_home}/.claude.json:/root/.claude.json" in mounts
    assert f"{fake_home}/.codex:/root/.codex" in mounts
    assert all(":/root/.gemini" not in m for m in mounts)
    # Repo bind mount is the last -v so its assertion is stable.
    assert mounts[-1] == f"{tmp_path}:{tmp_path}"


def test_docker_invoke_skips_credential_mounts_when_disabled(tmp_path, monkeypatch):
    fake_home = _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    (fake_home / ".claude").mkdir()

    command = _build_docker_invoke(
        tmp_path, monkeypatch,
        cfg_extra={"docker.mount_credentials": False},
    )

    mounts = [
        command[i + 1] for i, arg in enumerate(command) if arg == "-v"
    ]
    assert mounts == [f"{tmp_path}:{tmp_path}"]


def test_docker_invoke_skips_credential_mounts_when_disabled_string(tmp_path, monkeypatch):
    fake_home = _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    (fake_home / ".claude").mkdir()

    command = _build_docker_invoke(
        tmp_path, monkeypatch,
        cfg_extra={"docker.mount_credentials": "false"},
    )

    mounts = [
        command[i + 1] for i, arg in enumerate(command) if arg == "-v"
    ]
    assert mounts == [f"{tmp_path}:{tmp_path}"]


def test_docker_finalize_removes_containers_after_success(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(
        envs.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command)
        or envs.subprocess.CompletedProcess(command, 0, "", ""),
    )
    # Avoid touching real git inside finalize.
    monkeypatch.setattr(envs.worktree, "current_branch", lambda _path: None)
    task = Task(id="task-4", event_id="evt-4", body="done", status="done")
    ctx = envs.RunContext(
        name="docker",
        cwd=tmp_path,
        repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr" / "responses" / "evt-4.md",
        response_path_env=tmp_path / ".brr" / "responses" / "evt-4.md",
        branch_name="brr/task-4",
        env_state={
            "docker_containers": ["brr-task-4-evt-4-attempt-1"],
            "worktree_path": str(tmp_path),
        },
    )

    envs.get_env("docker").finalize(ctx, task, tmp_path / ".brr" / "tasks")

    assert ["docker", "rm", "-f", "brr-task-4-evt-4-attempt-1"] in commands


def test_docker_finalize_preserves_containers_on_error(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(
        envs.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command)
        or envs.subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(envs.worktree, "current_branch", lambda _path: None)
    task = Task(id="task-5", event_id="evt-5", body="failed", status="error")
    ctx = envs.RunContext(
        name="docker",
        cwd=tmp_path,
        repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr" / "responses" / "evt-5.md",
        response_path_env=tmp_path / ".brr" / "responses" / "evt-5.md",
        branch_name="brr/task-5",
        env_state={
            "docker_containers": ["brr-task-5-evt-5-attempt-1"],
            "worktree_path": str(tmp_path),
        },
    )

    envs.get_env("docker").finalize(ctx, task, tmp_path / ".brr" / "tasks")

    assert commands == []
    assert task.meta["docker_containers"] == "brr-task-5-evt-5-attempt-1"
    persisted = Task.from_file(tmp_path / ".brr" / "tasks" / "task-5.md")
    assert persisted is not None
    assert persisted.meta["docker_containers"] == "brr-task-5-evt-5-attempt-1"
