import subprocess

import pytest

from brr import branching, envs
from brr.runner import DEFAULT_RUNNER_TIMEOUT, RunnerInvocation
from brr.run import Run

from _helpers import commit_files, init_git_repo


def _plan(seed: str = "main", target: str | None = "main") -> branching.PublishPlan:
    """Convenience: build a plan for tests that don't care about resolver state."""
    return branching.PublishPlan(
        seed_ref=seed,
        target_branch=target,
        source="test",
        host_context_branch=seed,
    )


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
    task = Run(id="task-1", event_id="evt-1", body="run in docker")
    response_path = tmp_path / ".brr" / "responses" / "evt-1.md"

    monkeypatch.setattr(envs.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="Docker CLI"):
        backend.prepare(
            task, tmp_path, {},
            branch_plan=_plan(), response_path=response_path,
        )

    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    with pytest.raises(RuntimeError, match="docker.image"):
        backend.prepare(
            task, tmp_path, {},
            branch_plan=_plan(), response_path=response_path,
        )


def test_docker_prepare_creates_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    worktree_path = tmp_path / ".brr" / "worktrees" / "task-2"
    created = []
    monkeypatch.setattr(
        envs.worktree,
        "create",
        lambda repo_root, run_id, base_ref="HEAD": created.append((repo_root, run_id, base_ref))
        or (worktree_path, f"brr/{run_id}"),
    )
    monkeypatch.setattr(envs.worktree, "switch_to", lambda _path, _branch: None)
    task = Run(id="task-2", event_id="evt-2", body="change code")
    response_path = tmp_path / ".brr" / "responses" / "evt-2.md"

    ctx = envs.get_env("docker").prepare(
        task, tmp_path, {"docker.image": "brr/test-runner:latest"},
        branch_plan=_plan(), response_path=response_path,
    )

    assert ctx.name == "docker"
    assert ctx.cwd == worktree_path
    # _plan() has target="main", so the auto-switch fires: agent starts on main.
    assert ctx.branch_name == "main"
    assert ctx.run_branch == "brr/task-2"
    assert task.meta["worktree_path"] == str(worktree_path)
    assert task.meta["branch_name"] == "main"
    assert created == [(tmp_path, "task-2", "main")]


def _isolate_docker_creds(monkeypatch, tmp_path):
    """Make docker invocations independent of the test host's HOME.

    Points HOME at an empty directory and clears all known runner env
    vars so credential mounts and -e passthroughs only appear when a
    test explicitly opts in.

    Also stubs ``subprocess.run`` so the GitHub-token resolver's
    ``gh auth token`` fallback (which now runs on every docker task,
    not just github-source ones — see ``DockerEnv.prepare``) reports
    failure by default. Tests that *want* the CLI fallback to succeed
    override ``subprocess.run`` themselves after calling this helper.
    Without this stub, CI on a host where the developer is logged in
    with ``gh`` would inject a real token into every test's docker
    argv and trip every "no github wiring expected" assertion below.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    for name in envs._DOCKER_DEFAULT_PASSTHROUGH_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("BRNRD_MANAGED_GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        envs.subprocess,
        "run",
        lambda command, **_kwargs: envs.subprocess.CompletedProcess(command, 1, "", "no auth"),
    )
    return fake_home


def _stub_worktree(monkeypatch, tmp_path):
    """Replace worktree.create and switch_to with stubs that just make the dir."""
    def _create(_repo_root, run_id, base_ref="HEAD"):
        path = tmp_path / ".brr" / "worktrees" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path, f"brr/{run_id}"

    monkeypatch.setattr(envs.worktree, "create", _create)
    monkeypatch.setattr(envs.worktree, "switch_to", lambda _path, _branch: None)


def _init_repo(repo):
    init_git_repo(repo)
    commit_files(repo, {"file.txt": "base\n"})


def _commit_in(path, filename, text, message):
    (path / filename).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=path, check=True, stdout=subprocess.PIPE)


def _make_finalize_task(
    repo, tid, *, plan, status="done",
):
    """Common setup for ``WorktreeEnv.finalize`` outcome-table tests.

    Returns ``(backend, ctx, task, runs_dir)``. Each test is responsible
    for whatever git activity inside ``ctx.cwd`` produces the worktree
    state it wants to classify.
    """
    response_path = repo / ".brr" / "responses" / f"{tid}.md"
    task = Run(id=tid, event_id=tid, body="change", status=status)
    backend = envs.get_env("worktree")
    ctx = backend.prepare(
        task, repo, {},
        branch_plan=plan, response_path=response_path,
    )
    return backend, ctx, task, repo / ".brr" / "runs"


def test_worktree_finalize_ready_when_run_branch_has_commits(tmp_path):
    """status=ready, publish_branch=brr/<run-id>; clean worktree torn down."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    plan = _plan(seed="main", target=None)
    backend, ctx, task, runs_dir = _make_finalize_task(
        repo, "task-preserve", plan=plan,
    )
    _commit_in(ctx.cwd, "change.txt", "change\n", "change")

    backend.finalize(ctx, task, runs_dir)

    assert task.meta["publish_status"] == "ready"
    assert task.meta["publish_branch"] == "brr/task-preserve"
    assert task.meta["branch_name"] == "brr/task-preserve"
    assert not ctx.cwd.exists()
    # Run branch stays alive: the daemon's publish step will read it.
    assert envs.gitops.branch_exists(repo, "brr/task-preserve")
    # Worktree changes never leak into the host checkout.
    assert not (repo / "change.txt").exists()


def test_worktree_prepare_auto_switches_to_target_branch(tmp_path):
    """prepare() switches the worktree HEAD to target_branch; agent starts there."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "branch", "feature/auto", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )
    plan = _plan(seed="main", target="feature/auto")
    task = Run(id="task-auto", event_id="evt-auto", body="do work")
    response_path = repo / ".brr" / "responses" / "evt-auto.md"
    backend = envs.get_env("worktree")

    ctx = backend.prepare(
        task, repo, {}, branch_plan=plan, response_path=response_path,
    )

    assert ctx.branch_name == "feature/auto"
    assert ctx.run_branch == "brr/task-auto"
    assert task.meta["branch_name"] == "feature/auto"
    # Worktree HEAD is on feature/auto, not on the task placeholder.
    assert envs.worktree.current_branch(ctx.cwd) == "feature/auto"


def test_worktree_prepare_falls_back_when_target_checked_out_elsewhere(
    tmp_path, capsys,
):
    """A target branch held by another worktree does not fail env setup."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "branch", "feature/held", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )
    held_path = tmp_path / "held"
    subprocess.run(
        ["git", "worktree", "add", str(held_path), "feature/held"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    plan = _plan(seed="feature/held", target="feature/held")
    task = Run(id="task-held", event_id="evt-held", body="do work")
    response_path = repo / ".brr" / "responses" / "evt-held.md"
    backend = envs.get_env("worktree")

    ctx = backend.prepare(task, repo, {}, branch_plan=plan, response_path=response_path)

    assert ctx.branch_name == "brr/task-held"
    assert ctx.run_branch == "brr/task-held"
    assert task.meta["branch_name"] == "brr/task-held"
    assert task.meta["target_branch"] == "feature/held"
    assert task.meta["branch_setup"] == "target-checked-out-elsewhere"
    assert task.meta["target_branch_checkout_path"] == str(held_path)
    assert "starting on 'brr/task-held'" in task.meta["branch_setup_notice"]
    assert envs.worktree.current_branch(ctx.cwd) == "brr/task-held"
    assert (
        "target branch 'feature/held' is checked out"
        in capsys.readouterr().out
    )


def test_worktree_finalize_nothing_preserves_target_branch(tmp_path):
    """status=nothing deletes only the task placeholder, not target_branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "branch", "feature/keep", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )
    plan = _plan(seed="main", target="feature/keep")
    backend, ctx, task, runs_dir = _make_finalize_task(repo, "task-keep", plan=plan)
    # No commits inside the worktree.

    backend.finalize(ctx, task, runs_dir)

    assert task.meta["publish_status"] == "nothing"
    assert not ctx.cwd.exists()
    # task placeholder is gone; target_branch is untouched.
    assert not envs.gitops.branch_exists(repo, "brr/task-keep")
    assert envs.gitops.branch_exists(repo, "feature/keep")


def test_worktree_finalize_ready_when_agent_on_target_branch(tmp_path):
    """status=ready, publish_branch=target_branch; brr/<run-id>
    placeholder is cleaned up."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "branch", "feature/pr", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )
    plan = _plan(seed="main", target="feature/pr")
    backend, ctx, task, runs_dir = _make_finalize_task(
        repo, "task-switch", plan=plan,
    )
    # After auto-switch in prepare, ctx.cwd is already on feature/pr.
    assert ctx.branch_name == "feature/pr"
    assert ctx.run_branch == "brr/task-switch"
    _commit_in(ctx.cwd, "pr.txt", "pr\n", "pr")

    backend.finalize(ctx, task, runs_dir)

    assert task.meta["publish_status"] == "ready"
    assert task.meta["publish_branch"] == "feature/pr"
    assert task.meta["branch_name"] == "feature/pr"
    assert not ctx.cwd.exists()
    assert envs.gitops.branch_exists(repo, "feature/pr")
    # The empty brr/<run-id> placeholder is cleaned up best-effort.
    assert not envs.gitops.branch_exists(repo, "brr/task-switch")


def test_worktree_finalize_nothing_when_no_commits_on_run_branch(tmp_path):
    """status=nothing, no publish_branch, run branch torn down."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    plan = _plan(seed="main", target=None)
    backend, ctx, task, runs_dir = _make_finalize_task(
        repo, "task-noop", plan=plan,
    )
    # No commits inside the worktree.

    backend.finalize(ctx, task, runs_dir)

    assert task.meta["publish_status"] == "nothing"
    assert "publish_branch" not in task.meta
    assert not ctx.cwd.exists()
    assert not envs.gitops.branch_exists(repo, "brr/task-noop")


def test_worktree_finalize_detached_keeps_worktree(tmp_path):
    """status=detached, no publish_branch, worktree kept for inspection."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    plan = _plan(seed="main", target=None)
    backend, ctx, task, runs_dir = _make_finalize_task(
        repo, "task-detach", plan=plan,
    )
    # Agent detaches HEAD in the worktree.
    subprocess.run(
        ["git", "checkout", "--detach", "HEAD"],
        cwd=ctx.cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    backend.finalize(ctx, task, runs_dir)

    assert task.meta["publish_status"] == "detached"
    assert "publish_branch" not in task.meta
    # Detached worktree is preserved so the operator can recover commits.
    assert ctx.cwd.exists()


def test_worktree_finalize_keeps_worktree_with_uncommitted_changes(tmp_path):
    """Even on a ``ready`` outcome, uncommitted files keep the worktree
    alive so the operator can inspect what the agent left behind."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    plan = _plan(seed="main", target=None)
    backend, ctx, task, runs_dir = _make_finalize_task(
        repo, "task-dirty", plan=plan,
    )
    _commit_in(ctx.cwd, "committed.txt", "ok\n", "committed")
    # Leave an unstaged file behind.
    (ctx.cwd / "dirty.txt").write_text("scratch\n", encoding="utf-8")

    backend.finalize(ctx, task, runs_dir)

    assert task.meta["publish_status"] == "ready"
    assert task.meta["publish_branch"] == "brr/task-dirty"
    # Worktree (and uncommitted file) are preserved.
    assert ctx.cwd.exists()
    assert (ctx.cwd / "dirty.txt").exists()


def test_worktree_finalize_skips_classification_when_task_not_done(tmp_path):
    """A non-``done`` task (error / conflict / timeout) bypasses outcome
    classification: the env layer just persists the task and leaves the
    worktree alone for the operator."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    plan = _plan(seed="main", target=None)
    backend, ctx, task, runs_dir = _make_finalize_task(
        repo, "task-error", plan=plan, status="error",
    )
    _commit_in(ctx.cwd, "partial.txt", "partial\n", "partial")

    backend.finalize(ctx, task, runs_dir)

    assert "publish_status" not in task.meta
    assert "publish_branch" not in task.meta
    assert ctx.cwd.exists()


def test_docker_invoke_wraps_runner_command(tmp_path, monkeypatch):
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    response_path = tmp_path / ".brr" / "responses" / "evt-3.md"
    response_path.parent.mkdir(parents=True)
    task = Run(id="task-3", event_id="evt-3", body="run in docker")
    ctx = envs.get_env("docker").prepare(
        task, tmp_path, {"docker.image": "brr/test-runner:latest"},
        branch_plan=_plan(), response_path=response_path,
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
    # HOME is set so the in-container CLIs find credentials at
    # ``$HOME/.codex`` etc. The git safe.directory wiring is
    # unconditional; runner credential env vars are only forwarded when
    # set on the daemon (none here).
    assert forwarded == [
        "HOME=/brr-home",
        "GIT_EDITOR=true",
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


def test_docker_invoke_attaches_stdin_devnull(tmp_path, monkeypatch):
    """Codex 0.128+ prints "Reading additional input from stdin..." and
    can block on an open-but-silent fd. We attach the container's stdin
    to docker's stdin and pin docker's stdin to /dev/null so the inner
    runner sees an immediate EOF instead of hanging until our timeout
    fires."""
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    captured: dict = {}

    def _fake_run(command, **kwargs):
        captured["command"] = command
        captured["stdin"] = kwargs.get("stdin")
        return envs.subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr(envs.subprocess, "run", _fake_run)
    response_path = tmp_path / ".brr" / "responses" / "evt-stdin.md"
    response_path.parent.mkdir(parents=True)
    task = Run(id="task-stdin", event_id="evt-stdin", body="hi")
    ctx = envs.get_env("docker").prepare(
        task, tmp_path, {"docker.image": "img:latest"},
        branch_plan=_plan(), response_path=response_path,
    )
    invocation = RunnerInvocation(
        kind="daemon-run",
        label="evt-stdin-1",
        prompt="hello",
        cwd=ctx.cwd,
        repo_root=tmp_path,
        response_path=str(response_path),
    )
    envs.get_env("docker").invoke(
        ctx, "mock-runner", invocation,
        {"docker.image": "img:latest", "runner_cmd": ["mock", "{prompt}"]},
    )

    assert captured["stdin"] == subprocess.DEVNULL
    # -i must be present so the container's stdin is wired up to docker's
    # stdin (which is /dev/null per the above), not left as an open pipe.
    assert "-i" in captured["command"]


def test_docker_invoke_uses_default_timeout(tmp_path, monkeypatch):
    """The default runner timeout is 7200s — the historic 600s default
    was killing live work mid-run for xhigh-reasoning models, and the
    1h follow-up default (3600s) still cut long implementation/research
    sessions short without a human knowing to extend ``.keepalive``
    (2026-07-06)."""
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    captured: dict = {}

    def _fake_run(command, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return envs.subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr(envs.subprocess, "run", _fake_run)
    response_path = tmp_path / ".brr" / "responses" / "evt-t.md"
    response_path.parent.mkdir(parents=True)
    task = Run(id="task-t", event_id="evt-t", body="hi")
    ctx = envs.get_env("docker").prepare(
        task, tmp_path, {"docker.image": "img:latest"},
        branch_plan=_plan(), response_path=response_path,
    )
    invocation = RunnerInvocation(
        kind="daemon-run",
        label="evt-t-1",
        prompt="hello",
        cwd=ctx.cwd,
        repo_root=tmp_path,
        response_path=str(response_path),
    )
    envs.get_env("docker").invoke(
        ctx, "mock-runner", invocation,
        {"docker.image": "img:latest", "runner_cmd": ["mock", "{prompt}"]},
    )

    assert captured["timeout"] == DEFAULT_RUNNER_TIMEOUT == 7200


def test_docker_invoke_honours_configured_timeout(tmp_path, monkeypatch):
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    captured: dict = {}

    def _fake_run(command, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return envs.subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr(envs.subprocess, "run", _fake_run)
    response_path = tmp_path / ".brr" / "responses" / "evt-cfg.md"
    response_path.parent.mkdir(parents=True)
    task = Run(id="task-cfg", event_id="evt-cfg", body="hi")
    cfg = {"docker.image": "img:latest", "runner.timeout_seconds": 1200}
    ctx = envs.get_env("docker").prepare(
        task, tmp_path, cfg,
        branch_plan=_plan(), response_path=response_path,
    )
    invocation = RunnerInvocation(
        kind="daemon-run",
        label="evt-cfg-1",
        prompt="hello",
        cwd=ctx.cwd,
        repo_root=tmp_path,
        response_path=str(response_path),
    )
    envs.get_env("docker").invoke(
        ctx, "mock-runner", invocation,
        {**cfg, "runner_cmd": ["mock", "{prompt}"]},
    )

    assert captured["timeout"] == 1200


def test_docker_invoke_timeout_message_uses_configured_value(
    tmp_path, monkeypatch,
):
    """When the docker subprocess times out, the appended stderr line
    must report the actual configured ceiling so operators reading the
    failed packet can tell what the budget was."""
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")

    def _timeout_run(command, **_kwargs):
        raise envs.subprocess.TimeoutExpired(
            cmd=command,
            timeout=42,
            output=b"",
            stderr=b"partial",
        )

    # Stub the docker kill call so we don't try to invoke real docker.
    kill_calls: list[list[str]] = []

    def _kill_run(command, **_kwargs):
        kill_calls.append(command)
        return envs.subprocess.CompletedProcess(command, 0, "", "")

    def _dispatch(command, **kwargs):
        if command[:2] == ["docker", "kill"]:
            return _kill_run(command, **kwargs)
        return _timeout_run(command, **kwargs)

    response_path = tmp_path / ".brr" / "responses" / "evt-to.md"
    response_path.parent.mkdir(parents=True)
    task = Run(id="task-to", event_id="evt-to", body="hi")
    cfg = {"docker.image": "img:latest", "runner.timeout_seconds": 42}
    ctx = envs.get_env("docker").prepare(
        task, tmp_path, cfg,
        branch_plan=_plan(), response_path=response_path,
    )
    monkeypatch.setattr(envs.subprocess, "run", _dispatch)
    invocation = RunnerInvocation(
        kind="daemon-run",
        label="evt-to-1",
        prompt="hello",
        cwd=ctx.cwd,
        repo_root=tmp_path,
        response_path=str(response_path),
    )
    result = envs.get_env("docker").invoke(
        ctx, "mock-runner", invocation,
        {**cfg, "runner_cmd": ["mock", "{prompt}"]},
    )

    assert result.returncode == 124
    assert "runner timed out after 42s" in result.stderr
    assert kill_calls  # docker kill <container> was attempted


def _build_docker_invoke(
    tmp_path,
    monkeypatch,
    *,
    cfg_extra=None,
    label="evt-x-1",
    invocation_env=None,
):
    """Helper: prepare a DockerEnv ctx + invocation and capture the docker run argv."""
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    response_path = tmp_path / ".brr" / "responses" / "evt-x.md"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    task = Run(id=f"task-{label}", event_id="evt-x", body="run in docker")
    cfg: dict = {"docker.image": "brr/test-runner:latest"}
    if cfg_extra:
        cfg.update(cfg_extra)
    ctx = envs.get_env("docker").prepare(
        task, tmp_path, cfg,
        branch_plan=_plan(), response_path=response_path,
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
        env=invocation_env or {},
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


def test_docker_invoke_passes_invocation_environment(tmp_path, monkeypatch):
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)

    command = _build_docker_invoke(
        tmp_path,
        monkeypatch,
        invocation_env={
            "BRR_OUTBOX_DIR": "/repo/.brr/outbox/evt-1",
            "BRR_PORTAL_STATE": "/repo/.brr/outbox/evt-1/portal-state.json",
        },
    )

    forwarded = [command[i + 1] for i, arg in enumerate(command) if arg == "-e"]
    assert "BRR_OUTBOX_DIR=/repo/.brr/outbox/evt-1" in forwarded
    assert (
        "BRR_PORTAL_STATE=/repo/.brr/outbox/evt-1/portal-state.json"
        in forwarded
    )


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


def test_docker_invoke_sets_pythonpath_for_brr_checkout(tmp_path, monkeypatch):
    """Dogfooding brr in docker should prefer the bind-mounted checkout.

    The image-baked pip install cannot keep pace with every source edit.
    """
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    (tmp_path / "src" / "brr").mkdir(parents=True)
    (tmp_path / "src" / "brr" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "brr" / "cli.py").write_text("", encoding="utf-8")

    command = _build_docker_invoke(tmp_path, monkeypatch)

    assert f"PYTHONPATH={tmp_path / 'src'}" in command


def test_docker_invoke_omits_pythonpath_for_non_brr_repos(tmp_path, monkeypatch):
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)

    command = _build_docker_invoke(tmp_path, monkeypatch)

    assert all(
        not (arg == "-e" and command[i + 1].startswith("PYTHONPATH="))
        for i, arg in enumerate(command[:-1])
    )


def test_docker_invoke_mounts_credential_dirs_when_present(tmp_path, monkeypatch):
    fake_home = _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")
    (fake_home / ".codex").mkdir()
    (fake_home / ".gitconfig").write_text("[user]\n", encoding="utf-8")
    (fake_home / ".config" / "gh").mkdir(parents=True)
    # No ~/.gemini — confirms missing dirs don't show up.

    command = _build_docker_invoke(tmp_path, monkeypatch)

    mounts = [
        command[i + 1] for i, arg in enumerate(command) if arg == "-v"
    ]
    assert f"{fake_home}/.claude:/brr-home/.claude" in mounts
    assert f"{fake_home}/.claude.json:/brr-home/.claude.json" in mounts
    assert f"{fake_home}/.codex:/brr-home/.codex" in mounts
    assert f"{fake_home}/.gitconfig:/brr-home/.gitconfig" in mounts
    assert all(":/brr-home/.gemini" not in m for m in mounts)
    # ``.config/gh`` is intentionally NOT mounted — see the comment on
    # ``_DOCKER_DEFAULT_CRED_PATHS``. gh auth lives in the keyring on
    # Linux; mounting the file-side config without keyring access leaves
    # gh with a stale account it can't authenticate, which makes
    # ``gh auth status`` exit non-zero even when ``GITHUB_TOKEN`` is set.
    # The token-injection path covers gh CLI auth instead.
    assert all(":/brr-home/.config/gh" not in m for m in mounts)
    # Repo bind mount is the last -v so its assertion is stable.
    assert mounts[-1] == f"{tmp_path}:{tmp_path}"


def test_docker_invoke_mounts_ssh_when_present(tmp_path, monkeypatch):
    """.ssh is in the default credential paths so the runner can push via
    SSH remotes (e.g. git@github.com:) without a separate setup step."""
    fake_home = _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    (fake_home / ".ssh").mkdir()
    (fake_home / ".ssh" / "id_ed25519").write_text("FAKE_KEY", encoding="utf-8")

    command = _build_docker_invoke(tmp_path, monkeypatch)

    mounts = [command[i + 1] for i, arg in enumerate(command) if arg == "-v"]
    assert f"{fake_home}/.ssh:/brr-home/.ssh" in mounts


@pytest.mark.parametrize("disabled_value", [False, "false"])
def test_docker_invoke_skips_credential_mounts_when_disabled(
    tmp_path, monkeypatch, disabled_value,
):
    """``docker.mount_credentials`` accepts both the typed ``False`` and
    the string ``"false"``; both must opt the credential bind-mounts
    out so the container runs with a clean ``/brr-home``."""
    fake_home = _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    (fake_home / ".claude").mkdir()

    command = _build_docker_invoke(
        tmp_path, monkeypatch,
        cfg_extra={"docker.mount_credentials": disabled_value},
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
    task = Run(id="task-4", event_id="evt-4", body="done", status="done")
    ctx = envs.RunContext(
        name="docker",
        cwd=tmp_path,
        repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr" / "responses" / "evt-4.md",
        response_path_env=tmp_path / ".brr" / "responses" / "evt-4.md",
        branch_name="brr/task-4",
        branch_plan=_plan(),
        env_state={
            "docker_containers": ["brr-task-4-evt-4-attempt-1"],
            "worktree_path": str(tmp_path),
        },
    )

    envs.get_env("docker").finalize(ctx, task, tmp_path / ".brr" / "runs")

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
    task = Run(id="task-5", event_id="evt-5", body="failed", status="error")
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

    envs.get_env("docker").finalize(ctx, task, tmp_path / ".brr" / "runs")

    assert commands == []
    assert task.meta["docker_containers"] == "brr-task-5-evt-5-attempt-1"
    persisted = Run.from_file(tmp_path / ".brr" / "runs" / "task-5" / "run.md")
    assert persisted is not None
    assert persisted.meta["docker_containers"] == "brr-task-5-evt-5-attempt-1"


def _build_docker_invoke_with_task(
    tmp_path,
    monkeypatch,
    *,
    task: "Run",
    cfg_extra=None,
    label="evt-gh-1",
):
    """Like ``_build_docker_invoke`` but accepts a caller-supplied task."""
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    response_path = tmp_path / ".brr" / "responses" / f"{task.event_id}.md"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    cfg: dict = {"docker.image": "brr/test-runner:latest"}
    if cfg_extra:
        cfg.update(cfg_extra)
    ctx = envs.get_env("docker").prepare(
        task, tmp_path, cfg,
        branch_plan=_plan(), response_path=response_path,
    )
    commands: list = []
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


def test_docker_inject_github_token_from_gate_state(tmp_path, monkeypatch):
    """When a task originated from the GitHub gate and the gate has a stored
    token, GITHUB_TOKEN is injected into the container environment so ``gh``
    CLI and HTTPS git operations authenticate without needing the system
    keyring (which is unavailable inside Docker)."""
    fake_home = _isolate_docker_creds(monkeypatch, tmp_path)  # noqa: F841
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    gate_dir = tmp_path / ".brr" / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "github.json").write_text(
        '{"token": "ghs_stored_token", "repo": "owner/repo", "bot_login": "bot"}',
        encoding="utf-8",
    )

    task = Run(id="task-gh", event_id="evt-gh", body="review PR", source="github")
    command = _build_docker_invoke_with_task(tmp_path, monkeypatch, task=task)

    kv_env = [
        command[i + 1]
        for i, arg in enumerate(command)
        if arg == "-e" and "=" in command[i + 1]
    ]
    assert "GITHUB_TOKEN=ghs_stored_token" in kv_env


def test_docker_github_token_rewrites_ssh_remotes(tmp_path, monkeypatch):
    """A GitHub token must also help plain ``git push`` when origin is SSH.

    The runner sees the repo's real remote URL. If that URL is
    ``git@github.com:...``, exporting ``GITHUB_TOKEN`` is not enough:
    git still tries SSH and fails without an agent/key. The Docker env
    injects git config that rewrites GitHub SSH remotes to HTTPS with
    the token for the duration of the container.
    """
    _isolate_docker_creds(monkeypatch, tmp_path)  # noqa: F841
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    gate_dir = tmp_path / ".brr" / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "github.json").write_text(
        '{"token": "ghs_stored_token"}', encoding="utf-8",
    )

    task = Run(
        id="task-gh-rewrite", event_id="evt-gh-r",
        body="rebase", source="github",
    )
    command = _build_docker_invoke_with_task(tmp_path, monkeypatch, task=task)

    env_values = [
        command[i + 1]
        for i, arg in enumerate(command)
        if arg == "-e" and "=" in command[i + 1]
    ]
    assert "GIT_CONFIG_COUNT=4" in env_values
    assert "GIT_CONFIG_KEY_1=url.https://github.com/.insteadOf" in env_values
    assert "GIT_CONFIG_VALUE_1=git@github.com:" in env_values
    assert "GIT_CONFIG_KEY_2=url.https://github.com/.insteadOf" in env_values
    assert "GIT_CONFIG_VALUE_2=ssh://git@github.com/" in env_values
    assert "GIT_CONFIG_KEY_3=credential.helper" in env_values
    assert any(
        v.startswith("GIT_CONFIG_VALUE_3=!f()")
        and "password=${GH_TOKEN:-$GITHUB_TOKEN}" in v
        for v in env_values
    )


def test_docker_managed_credential_uses_pointer_dir(tmp_path, monkeypatch):
    """Issue #477: on the managed path the container reads the *current* token
    from the daemon-refreshed pointer dir riding the bind mount — GH_CONFIG_DIR
    for gh, a token-file git helper for pushes — not a frozen env value."""
    from brr.gates import cloud

    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("BRNRD_MANAGED_GITHUB_TOKEN", "app-token")

    pointer_dir = tmp_path / ".brr" / "credentials" / "github"
    pointer_dir.mkdir(parents=True)
    (pointer_dir / "token").write_text("app-token\n", encoding="utf-8")
    monkeypatch.setattr(cloud, "github_credentials_dir", lambda brr_dir=None: pointer_dir)

    task = Run(id="task-managed", event_id="evt-managed", body="push work")
    command = _build_docker_invoke_with_task(tmp_path, monkeypatch, task=task)

    kv_env = [
        command[i + 1]
        for i, arg in enumerate(command)
        if arg == "-e" and "=" in command[i + 1]
    ]
    assert f"GH_CONFIG_DIR={pointer_dir}" in kv_env
    # No frozen token env that would override the pointer at gh's precedence.
    assert not any(v.startswith("GH_TOKEN=") for v in kv_env)
    assert not any(v.startswith("GITHUB_TOKEN=") for v in kv_env)
    # git helper cats the pointer's token file rather than echoing a value.
    assert "GIT_CONFIG_COUNT=4" in kv_env
    assert any(
        v.startswith("GIT_CONFIG_VALUE_3=!f()")
        and f"cat {pointer_dir / 'token'}" in v
        for v in kv_env
    )


def test_docker_managed_credential_falls_back_when_pointer_outside_mount(tmp_path, monkeypatch):
    """A pointer that does not ride the repo bind mount is invisible inside the
    container, so the managed path degrades to the legacy frozen injection."""
    from brr.gates import cloud

    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("BRNRD_MANAGED_GITHUB_TOKEN", "app-token")

    outside = tmp_path.parent / f"{tmp_path.name}-external" / "github"
    outside.mkdir(parents=True)
    (outside / "token").write_text("app-token\n", encoding="utf-8")
    monkeypatch.setattr(cloud, "github_credentials_dir", lambda brr_dir=None: outside)

    task = Run(id="task-frozen", event_id="evt-frozen", body="push work")
    command = _build_docker_invoke_with_task(tmp_path, monkeypatch, task=task)

    kv_env = [
        command[i + 1]
        for i, arg in enumerate(command)
        if arg == "-e" and "=" in command[i + 1]
    ]
    assert not any(v.startswith("GH_CONFIG_DIR=") for v in kv_env)
    assert "GH_TOKEN=app-token" in kv_env


def test_docker_gh_token_overrides_gate_and_github_token(tmp_path, monkeypatch):
    """GH_TOKEN selects the runner's publishing identity everywhere.

    A local GitHub gate may still hold a human ingress token, and a daemon
    manager may contribute GITHUB_TOKEN. Neither may leak into the runner when
    the operator explicitly selects a bot through GH_TOKEN.
    """
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "human-env-token")
    monkeypatch.setenv("GH_TOKEN", "bot-publish-token")
    gate_dir = tmp_path / ".brr" / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "github.json").write_text(
        '{"token": "human-gate-token"}', encoding="utf-8",
    )

    task = Run(id="task-bot", event_id="evt-bot", body="publish", source="telegram")
    command = _build_docker_invoke_with_task(tmp_path, monkeypatch, task=task)

    passed = [command[i + 1] for i, arg in enumerate(command) if arg == "-e"]
    assert "GH_TOKEN" in passed
    assert "GITHUB_TOKEN" not in passed
    assert not any(value.startswith("GITHUB_TOKEN=") for value in passed)


def test_docker_managed_app_token_overrides_human_credentials(tmp_path, monkeypatch):
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "human-env-token")
    monkeypatch.setenv("BRNRD_MANAGED_GITHUB_TOKEN", "app-installation-token")
    gate_dir = tmp_path / ".brr" / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "github.json").write_text(
        '{"token": "human-gate-token"}', encoding="utf-8",
    )

    task = Run(id="task-app", event_id="evt-app", body="publish", source="telegram")
    command = _build_docker_invoke_with_task(tmp_path, monkeypatch, task=task)

    passed = [command[i + 1] for i, arg in enumerate(command) if arg == "-e"]
    assert "GH_TOKEN=app-installation-token" in passed
    assert "GITHUB_TOKEN" not in passed
    assert not any(value.startswith("GITHUB_TOKEN=") for value in passed)


def test_docker_github_token_can_come_from_gh_cli(tmp_path, monkeypatch):
    """GitHub gate setup may use ``gh auth token`` without storing a token.

    The gate can poll with that token in-process, but a Docker runner
    still needs the token injected explicitly for push/rebase work.
    """
    _isolate_docker_creds(monkeypatch, tmp_path)  # noqa: F841
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    gate_dir = tmp_path / ".brr" / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "github.json").write_text(
        '{"repo": "owner/repo", "token_source": "gh-cli"}',
        encoding="utf-8",
    )

    def fake_run(command, **_kwargs):
        if command == ["gh", "auth", "token"]:
            return envs.subprocess.CompletedProcess(command, 0, "ghs_cli_token\n", "")
        return envs.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(envs.subprocess, "run", fake_run)

    task = Run(
        id="task-gh-cli", event_id="evt-gh-cli",
        body="push", source="github",
    )
    command = _build_docker_invoke_with_task(tmp_path, monkeypatch, task=task)

    kv_env = [
        command[i + 1]
        for i, arg in enumerate(command)
        if arg == "-e" and "=" in command[i + 1]
    ]
    assert "GITHUB_TOKEN=ghs_cli_token" in kv_env


def test_docker_inject_github_token_for_non_github_task(tmp_path, monkeypatch):
    """Tasks from any source must receive a resolved GitHub token.

    The container has no path to the host keyring or to the user's
    stored gh accounts (``~/.config/gh`` is intentionally not mounted,
    see the comment on ``_DOCKER_DEFAULT_CRED_PATHS``). Without an
    injected ``GITHUB_TOKEN`` the agent's ``gh`` CLI dies and HTTPS
    ``git push`` falls back to anonymous — which silently breaks any
    cross-source task that needs to look up GitHub issues, sibling PRs,
    or push a branch, even when the trigger came from Telegram, an
    ad-hoc CLI invocation, or a non-github gate.

    Earlier the resolver was scoped to ``task.source == "github"``,
    which paired with the now-deleted ``.config/gh`` mount as a
    half-working fallback for other sources. With the mount gone, the
    resolver becomes the only path, so it has to run uniformly.
    """
    fake_home = _isolate_docker_creds(monkeypatch, tmp_path)  # noqa: F841
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    gate_dir = tmp_path / ".brr" / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "github.json").write_text(
        '{"token": "ghs_stored_token"}', encoding="utf-8",
    )

    task = Run(id="task-tg", event_id="evt-tg", body="telegram task", source="telegram")
    command = _build_docker_invoke_with_task(tmp_path, monkeypatch, task=task)

    kv_env = [
        command[i + 1]
        for i, arg in enumerate(command)
        if arg == "-e" and "=" in command[i + 1]
    ]
    assert "GITHUB_TOKEN=ghs_stored_token" in kv_env


def test_docker_no_github_token_when_unresolvable(tmp_path, monkeypatch):
    """The resolver stays silent when no token source matches.

    With no gate state, no daemon env vars, and ``gh auth token``
    returning non-zero (or ``gh`` not installed), nothing is injected.
    The container then runs without GitHub auth — the agent's ``gh``
    CLI will fail cleanly with "not authenticated" rather than blowing
    up with a half-mounted broken account.
    """
    fake_home = _isolate_docker_creds(monkeypatch, tmp_path)  # noqa: F841
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    # ``_build_docker_invoke_with_task`` stubs ``shutil.which`` to always
    # return a docker path, so we can't use that to hide ``gh``. Stub
    # ``subprocess.run`` before ``prepare`` runs so the resolver's
    # ``gh auth token`` shell-out reports failure regardless of whether
    # gh is installed on the test host.
    monkeypatch.setattr(
        envs.subprocess,
        "run",
        lambda command, **_kwargs: envs.subprocess.CompletedProcess(command, 1, "", "no auth"),
    )

    task = Run(id="task-noauth", event_id="evt-noauth", body="adhoc", source="cli")
    command = _build_docker_invoke_with_task(tmp_path, monkeypatch, task=task)

    kv_env = [
        command[i + 1]
        for i, arg in enumerate(command)
        if arg == "-e" and "=" in command[i + 1]
    ]
    assert not any(v.startswith("GITHUB_TOKEN=") for v in kv_env)


def test_docker_github_token_not_duplicated_when_in_daemon_env(tmp_path, monkeypatch):
    """When GITHUB_TOKEN is already in the daemon's environment it is passed
    via ``-e GITHUB_TOKEN`` (no value, docker inherits from env) and we
    must not emit a second ``-e GITHUB_TOKEN=...`` from the gate state."""
    fake_home = _isolate_docker_creds(monkeypatch, tmp_path)  # noqa: F841
    _stub_worktree(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "from_daemon_env")

    gate_dir = tmp_path / ".brr" / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "github.json").write_text(
        '{"token": "ghs_stored_different"}', encoding="utf-8",
    )

    task = Run(id="task-gh2", event_id="evt-gh2", body="from env", source="github")
    command = _build_docker_invoke_with_task(tmp_path, monkeypatch, task=task)

    kv_env = [
        command[i + 1]
        for i, arg in enumerate(command)
        if arg == "-e" and command[i + 1].startswith("GITHUB_TOKEN=")
    ]
    assert kv_env == [], "injected key=value form must be absent when env already has GITHUB_TOKEN"
    bare_env = [
        command[i + 1]
        for i, arg in enumerate(command)
        if arg == "-e" and command[i + 1] == "GITHUB_TOKEN"
    ]
    assert bare_env == ["GITHUB_TOKEN"]


# ---------------------------------------------------------------------------
# solitary — the hardened preset (#508)
# ---------------------------------------------------------------------------

from brr import runner_select


def _claude_profile() -> runner_select.RunnerProfile:
    return runner_select.RunnerProfile(
        name="claude", profile="claude", shell="claude",
    )


class _DockerRecorder:
    """Fake ``subprocess.run`` that records every docker command and
    answers success, so solitary's network/proxy/run choreography is
    observable without a docker daemon."""

    def __init__(self):
        self.commands: list[list[str]] = []

    def __call__(self, command, **_kwargs):
        self.commands.append(list(command))
        return envs.subprocess.CompletedProcess(command, 0, "ok\n", "")

    def runner_argv(self) -> list[str]:
        for command in self.commands:
            if command[:2] == ["docker", "run"] and "-d" not in command:
                return command
        raise AssertionError(f"no runner docker run in {self.commands}")

    def only(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.commands if c[: len(prefix)] == list(prefix)]


def _solitary_ctx_and_recorder(
    tmp_path, monkeypatch, cfg=None, task=None,
):
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    cfg = {"docker.image": "brr/test-runner:latest", **(cfg or {})}
    task = task or Run(id="task-sol", event_id="evt-sol", body="hardened run")
    response_path = tmp_path / ".brr" / "responses" / f"{task.event_id}.md"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    backend = envs.get_env("solitary")
    ctx = backend.prepare(
        task, tmp_path, cfg, branch_plan=_plan(), response_path=response_path,
    )
    recorder = _DockerRecorder()
    monkeypatch.setattr(envs.subprocess, "run", recorder)
    return backend, ctx, recorder, cfg, task, response_path


def _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path):
    invocation = RunnerInvocation(
        kind="daemon-run",
        label="evt-sol-1",
        prompt="hello",
        cwd=ctx.cwd,
        repo_root=tmp_path,
        response_path=str(response_path),
        selected_runner=_claude_profile(),
    )
    return backend.invoke(
        ctx, "claude", invocation,
        {**cfg, "runner_cmd": ["mock", "{prompt}"]},
    )


def test_get_env_returns_solitary():
    assert envs.get_env("solitary").name == "solitary"


def test_solitary_prepare_never_resolves_github_token(tmp_path, monkeypatch):
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    gate_dir = tmp_path / ".brr" / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "github.json").write_text(
        '{"token": "ghs_stored"}', encoding="utf-8",
    )
    task = Run(id="task-sol-tok", event_id="evt-sol-tok", body="x")
    response_path = tmp_path / ".brr" / "responses" / "evt-sol-tok.md"
    response_path.parent.mkdir(parents=True, exist_ok=True)

    ctx = envs.get_env("solitary").prepare(
        task, tmp_path, {"docker.image": "img:latest"},
        branch_plan=_plan(), response_path=response_path,
    )

    assert "github_token" not in ctx.env_state
    assert ctx.env_state["solitary_network_mode"] == "isolated"
    assert task.meta["environment"] == "solitary"


def test_solitary_never_gets_github_credential_pointer(tmp_path, monkeypatch):
    """The #477 pointer must not undo #518's no-credential guarantee.

    Regression for the exact leak a naive rebase would have shipped: the
    pointer dir rides the repo bind mount (`.brr` is visible inside every
    container), so if pointer resolution lived anywhere but
    ``_resolve_publish_token`` — the seam ``SolitaryEnv`` no-ops — a solitary
    container would read the managed GitHub token through GH_CONFIG_DIR.
    Here every pointer precondition holds (managed pointer exists, under the
    mount, no operator GH_TOKEN) and solitary must still surface nothing.
    """
    from brr.gates import cloud

    monkeypatch.delenv("GH_TOKEN", raising=False)
    pointer_dir = tmp_path / ".brr" / "credentials" / "github"
    pointer_dir.mkdir(parents=True)
    (pointer_dir / "token").write_text("app-token\n", encoding="utf-8")
    monkeypatch.setattr(
        cloud, "github_credentials_dir", lambda brr_dir=None: pointer_dir,
    )

    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch,
    )
    assert "github_pointer_dir" not in ctx.env_state
    assert "github_token" not in ctx.env_state

    _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)
    argv = " ".join(recorder.runner_argv())
    assert "GH_CONFIG_DIR" not in argv
    assert "credential.helper" not in argv
    assert "app-token" not in argv


def test_solitary_isolated_network_and_proxy_choreography(tmp_path, monkeypatch):
    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch,
    )

    result = _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)

    assert result.returncode == 0
    net_creates = recorder.only("docker", "network", "create")
    assert len(net_creates) == 1
    assert "--internal" in net_creates[0]
    network = net_creates[0][-1]
    assert "solitary" in network

    sidecars = [
        c for c in recorder.commands
        if c[:2] == ["docker", "run"] and "-d" in c
    ]
    assert len(sidecars) == 1
    sidecar = sidecars[0]
    allow = next(
        v.split("=", 1)[1]
        for i, a in enumerate(sidecar)
        if a == "-e" and (v := sidecar[i + 1]).startswith("BRR_SOLITARY_ALLOW=")
    )
    assert "api.anthropic.com" in allow.split(",")
    assert "github.com" not in allow

    connects = recorder.only("docker", "network", "connect")
    assert connects and connects[0][3] == "bridge"

    command = recorder.runner_argv()
    assert command[command.index("--network") + 1] == network
    forwarded = [command[i + 1] for i, a in enumerate(command) if a == "-e"]
    proxy_env = [v for v in forwarded if v.startswith("HTTPS_PROXY=")]
    assert proxy_env and proxy_env[0].endswith(":3128")
    assert not any(v.startswith("GITHUB_TOKEN") for v in forwarded)
    assert not any(v.startswith("GH_TOKEN") for v in forwarded)
    # No token → no SSH-to-HTTPS rewrite config either.
    assert not any("insteadOf" in v for v in forwarded)


def test_solitary_network_none_skips_proxy(tmp_path, monkeypatch):
    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch, cfg={"solitary.network": "none"},
    )

    _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)

    assert recorder.only("docker", "network", "create") == []
    command = recorder.runner_argv()
    assert command[command.index("--network") + 1] == "none"
    forwarded = [command[i + 1] for i, a in enumerate(command) if a == "-e"]
    assert not any(v.startswith("HTTPS_PROXY") for v in forwarded)


def test_solitary_rejects_unknown_network_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(envs.shutil, "which", lambda _name: "/usr/bin/docker")
    _isolate_docker_creds(monkeypatch, tmp_path)
    _stub_worktree(monkeypatch, tmp_path)
    task = Run(id="task-sol-bad", event_id="evt-sol-bad", body="x")
    response_path = tmp_path / ".brr" / "responses" / "evt-sol-bad.md"
    response_path.parent.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="solitary.network"):
        envs.get_env("solitary").prepare(
            task, tmp_path,
            {"docker.image": "img:latest", "solitary.network": "bridge"},
            branch_plan=_plan(), response_path=response_path,
        )


def test_solitary_credentials_copy_stages_private_copy(tmp_path, monkeypatch):
    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch,
    )
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")
    (fake_home / ".ssh").mkdir()
    (fake_home / ".ssh" / "id_ed25519").write_text("key", encoding="utf-8")

    _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)

    stage = ctx.env_state["solitary_cred_stage"]
    assert (envs.Path(stage) / ".claude" / ".credentials.json").exists()
    command = recorder.runner_argv()
    mounts = [command[i + 1] for i, a in enumerate(command) if a == "-v"]
    claude_mounts = [m for m in mounts if "/.claude:" in m or "/.claude.json:" in m]
    assert claude_mounts, mounts
    for mount in claude_mounts:
        assert mount.startswith(stage), "copy mode must mount the staged copy"
        assert not mount.endswith(":ro")
    assert not any(".ssh" in m for m in mounts), ".ssh must never be mounted"


def test_solitary_credentials_ro_mounts_host_readonly(tmp_path, monkeypatch):
    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch, cfg={"solitary.credentials": "ro"},
    )
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)

    _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)

    command = recorder.runner_argv()
    mounts = [command[i + 1] for i, a in enumerate(command) if a == "-v"]
    claude_mounts = [m for m in mounts if "/.claude:" in m]
    assert claude_mounts and all(m.endswith(":ro") for m in claude_mounts)
    assert "solitary_cred_stage" not in ctx.env_state


def test_solitary_credentials_none_mounts_nothing(tmp_path, monkeypatch):
    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch, cfg={"solitary.credentials": "none"},
    )
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)

    _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)

    command = recorder.runner_argv()
    mounts = [command[i + 1] for i, a in enumerate(command) if a == "-v"]
    assert not any(".claude" in m for m in mounts)


def test_solitary_passthrough_keeps_model_keys_drops_github(tmp_path, monkeypatch):
    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.setenv("GH_TOKEN", "ghs_x")

    _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)

    command = recorder.runner_argv()
    forwarded = [command[i + 1] for i, a in enumerate(command) if a == "-e"]
    assert "ANTHROPIC_API_KEY" in forwarded
    assert "GITHUB_TOKEN" not in forwarded
    assert "GH_TOKEN" not in forwarded


def test_solitary_finalize_success_removes_network_and_cred_stage(
    tmp_path, monkeypatch,
):
    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch,
    )
    fake_home = tmp_path / "home"
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")
    _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)
    stage_parent = envs.Path(ctx.env_state["solitary_cred_stage"]).parent
    assert stage_parent.exists()
    task.status = "done"
    monkeypatch.setattr(
        envs.worktree, "current_branch", lambda _p: ctx.branch_name,
    )
    monkeypatch.setattr(
        envs.worktree, "has_commits_beyond", lambda _p, _s: False,
    )
    monkeypatch.setattr(
        envs.worktree, "has_uncommitted_changes", lambda _p: False,
    )
    monkeypatch.setattr(
        envs.worktree, "remove",
        lambda *_a, **_k: None,
    )
    recorder.commands.clear()

    backend.finalize(ctx, task, tmp_path / ".brr" / "runs")

    assert not stage_parent.exists(), "credential copies must not outlive the run"
    removed = recorder.only("docker", "rm", "-f")
    assert any("solitary-proxy" in c[-1] for c in removed)
    net_rms = recorder.only("docker", "network", "rm")
    assert net_rms and net_rms[0][-1] == ctx.env_state["solitary_network"]


def test_solitary_finalize_failure_stops_proxy_keeps_containers(
    tmp_path, monkeypatch,
):
    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch,
    )
    fake_home = tmp_path / "home"
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")
    _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)
    stage_parent = envs.Path(ctx.env_state["solitary_cred_stage"]).parent
    task.status = "error"
    recorder.commands.clear()

    backend.finalize(ctx, task, tmp_path / ".brr" / "runs")

    assert not stage_parent.exists(), "credential copies must not outlive the run"
    assert recorder.only("docker", "rm", "-f") == []
    stops = recorder.only("docker", "stop")
    assert stops and "solitary-proxy" in stops[0][-1]
    assert recorder.only("docker", "network", "rm") == []


def test_solitary_proxy_start_failure_cleans_up_network(tmp_path, monkeypatch):
    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch,
    )

    def failing_run(command, **_kwargs):
        recorder.commands.append(list(command))
        if command[:2] == ["docker", "run"] and "-d" in command:
            return envs.subprocess.CompletedProcess(command, 125, "", "boom")
        return envs.subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr(envs.subprocess, "run", failing_run)

    with pytest.raises(RuntimeError, match="proxy sidecar"):
        _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)

    assert recorder.only("docker", "network", "rm"), "network must be torn down"
    assert "solitary_network" not in ctx.env_state


def test_solitary_proxy_allowlist_matcher():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "solitary_proxy", envs._solitary_proxy_script(),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    entries = ["api.anthropic.com", ".chatgpt.com"]
    assert module.host_allowed("api.anthropic.com", entries)
    assert module.host_allowed("API.ANTHROPIC.COM.", entries)
    assert module.host_allowed("chatgpt.com", entries)
    assert module.host_allowed("ab.chatgpt.com", entries)
    assert not module.host_allowed("anthropic.com", entries)
    assert not module.host_allowed("evil-api.anthropic.com.attacker.io", entries)
    assert not module.host_allowed("github.com", entries)
    assert not module.host_allowed("", entries)


def test_solitary_cred_stage_is_outside_the_repo_mount(tmp_path, monkeypatch):
    """The repo's .brr rides the bind mount into every container; a stage
    under it would expose this run's copied tokens to sibling containers."""
    backend, ctx, recorder, cfg, task, response_path = _solitary_ctx_and_recorder(
        tmp_path, monkeypatch,
    )
    fake_home = tmp_path / "home"
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")

    _solitary_invoke(backend, ctx, recorder, cfg, response_path, tmp_path)

    stage = envs.Path(ctx.env_state["solitary_cred_stage"]).resolve()
    assert not str(stage).startswith(str(tmp_path.resolve())), (
        "credential stage must not live under the repo root"
    )
    # cleanup: the test bypasses finalize
    envs.shutil.rmtree(stage.parent, ignore_errors=True)
