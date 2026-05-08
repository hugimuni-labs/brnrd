"""Tests for the daemon lifecycle packets after triage was removed.

These verify that the worker emits the run-progress packets in the
right order for happy-path, retry, and Docker-preserved-container
scenarios. Records are read directly from the per-conversation log.
"""

from __future__ import annotations

from pathlib import Path

from brr import conversations, daemon, envs
from brr.runner import RunnerResult


def _write_repo_scaffold(repo_root: Path) -> None:
    (repo_root / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    (repo_root / ".brr" / "inbox").mkdir(parents=True)
    (repo_root / ".brr" / "responses").mkdir(parents=True)


def _make_event(repo_root: Path, *, eid: str, body: str, **extra) -> dict:
    event = {
        "id": eid,
        "status": "pending",
        "body": body,
        "source": "telegram",
        "_path": repo_root / ".brr" / "inbox" / f"{eid}.md",
        **extra,
    }
    event["_path"].write_text(
        f"---\nid: {eid}\nstatus: pending\nsource: telegram\n---\n{body}\n",
        encoding="utf-8",
    )
    return event


def _patch_runner(monkeypatch):
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner, "build_daemon_prompt",
        lambda task, eid, rp, _root, **kw: f"RUN {eid}: {task} -> {rp}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _: False)


class _StubWorktreeEnv:
    """Minimal env backend that the daemon worker can drive end-to-end."""

    name = "worktree"

    def __init__(self, *, invoke_fn) -> None:
        self._invoke = invoke_fn

    def prepare(self, task, repo_root, cfg, *, base_branch, response_path, debug=False):
        return envs.RunContext(
            name=self.name,
            cwd=repo_root,
            repo_root=repo_root,
            runtime_dir=repo_root / ".brr",
            response_path_host=response_path,
            response_path_env=response_path,
            branch_name=f"brr/{task.id}",
            base_branch=base_branch,
            env_state={"worktree_path": str(repo_root)},
        )

    def invoke(self, ctx, runner_name, invocation, cfg, *, trace=False):
        return self._invoke(ctx, runner_name, invocation, cfg, trace=trace)

    def finalize(self, _ctx, task, _tasks_dir, *, debug=False):
        return task


def _update_records(brr_dir: Path, conv_key: str) -> list[dict]:
    return [r for r in conversations.read_records(brr_dir, conv_key)
            if r.get("kind") == "update"]


def _packet_types(brr_dir: Path, conv_key: str) -> list[str]:
    return [r.get("type") for r in _update_records(brr_dir, conv_key)]


def _success_invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
    Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
    Path(invocation.response_path).write_text("all done\n", encoding="utf-8")
    return RunnerResult(
        invocation=invocation, runner_name=runner_name, command=["mock"],
        stdout="all done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
    )


def test_success_emits_full_progress_lifecycle(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(
        tmp_path, eid="evt-success", body="ship it",
        telegram_chat_id=10, telegram_topic_id=1,
    )
    _patch_runner(monkeypatch)
    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: _StubWorktreeEnv(invoke_fn=_success_invoke),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    types = _packet_types(tmp_path / ".brr", task.conversation_key)
    assert "task_created" in types
    assert "env_prepared" in types
    assert "attempt_started" in types
    assert "run_started" in types
    assert "finalizing" in types
    assert "done" in types
    assert "triage_done" not in types
    assert types.index("env_prepared") < types.index("attempt_started")
    assert types.index("attempt_started") < types.index("finalizing")
    assert types.index("finalizing") < types.index("done")


def test_retry_emits_attempt_failed_and_retrying(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(
        tmp_path, eid="evt-retry", body="missing artifact",
        telegram_chat_id=20,
    )
    _patch_runner(monkeypatch)

    def _retry_invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        if invocation.label.endswith("attempt-1"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="", stderr="", returncode=0, trace_dir=None, artifacts=[],
            )
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: _StubWorktreeEnv(invoke_fn=_retry_invoke),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 1,
    )

    assert task.status == "done"
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    types = [r.get("type") for r in records]
    assert types.count("attempt_started") == 2
    assert "attempt_failed" in types
    assert "retrying" in types
    failed = next(r for r in records if r.get("type") == "attempt_failed")
    assert failed.get("will_retry") is True


def test_failure_after_retries_emits_failed_and_finalizing(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, eid="evt-fail", body="never works",
                        telegram_chat_id=30)
    _patch_runner(monkeypatch)

    def _always_fail(_ctx, runner_name, invocation, _cfg, *, trace=False):
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: _StubWorktreeEnv(invoke_fn=_always_fail),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    types = _packet_types(tmp_path / ".brr", task.conversation_key)
    assert "attempt_failed" in types
    assert "failed" in types
    assert types.index("failed") < types.index("finalizing")


class _FakeDockerEnv:
    """In-memory Docker env stub for daemon packet assertions."""

    name = "docker"

    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed
        self.containers: list[str] = []

    def prepare(self, task, repo_root, cfg, *, base_branch, response_path, debug=False):
        ctx = envs.RunContext(
            name=self.name,
            cwd=repo_root,
            repo_root=repo_root,
            runtime_dir=repo_root / ".brr",
            response_path_host=response_path,
            response_path_env=response_path,
            branch_name=None,
            base_branch=base_branch,
        )
        ctx.env_state.update({
            "task_id": task.id,
            "docker_image": "img:latest",
            "docker_containers": [],
        })
        task.meta["docker_image"] = "img:latest"
        return ctx

    def invoke(self, ctx, runner_name, invocation, cfg, *, trace=False):
        cid = f"brr-{ctx.env_state['task_id']}-{invocation.label}"
        ctx.env_state["docker_containers"].append(cid)
        ctx.env_state["docker_container"] = cid
        self.containers.append(cid)
        response = Path(invocation.response_path)
        if self.succeed:
            response.parent.mkdir(parents=True, exist_ok=True)
            response.write_text("docker ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name,
            command=["mock"], stdout="docker ok\n" if self.succeed else "",
            stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    def finalize(self, ctx, task, tasks_dir, *, debug=False):
        preserved = ctx.env_state.get("docker_containers", [])
        if preserved and task.status != "done":
            task.meta["docker_containers"] = ", ".join(preserved)
            task.save(tasks_dir)
        return task


def test_docker_env_emits_container_started(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, eid="evt-docker", body="run docker",
                        telegram_chat_id=40)
    _patch_runner(monkeypatch)

    fake_env = _FakeDockerEnv(succeed=True)
    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: fake_env)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    types = [r.get("type") for r in records]
    assert "container_started" in types
    container_event = next(r for r in records if r.get("type") == "container_started")
    assert container_event.get("container", "").startswith("brr-")


def test_docker_failed_emits_container_preserved(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, eid="evt-docker-fail", body="never finishes",
                        telegram_chat_id=50)
    _patch_runner(monkeypatch)

    fake_env = _FakeDockerEnv(succeed=False)
    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: fake_env)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    types = [r.get("type") for r in records]
    assert "failed" in types
    assert "container_preserved" in types
    preserved = next(r for r in records if r.get("type") == "container_preserved")
    assert preserved.get("containers"), preserved


def test_push_emits_started_and_done_packets(tmp_path, monkeypatch):
    """_push_if_needed should emit push packets when commits are unpushed."""
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    conv_key = "telegram:99:"

    monkeypatch.setattr(daemon.gitops, "shared_brr_dir", lambda _r: brr_dir)

    calls = []

    class _Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(args, **kwargs):
        calls.append(args)
        if "log" in args:
            return _Result(returncode=0, stdout="abc Fix bug\n")
        if "push" in args:
            return _Result(returncode=0)
        return _Result(returncode=0)

    monkeypatch.setattr(daemon.subprocess, "run", _fake_run)

    daemon._push_if_needed(tmp_path, conversation_key=conv_key, task_id="task-push")

    types = _packet_types(brr_dir, conv_key)
    assert "push_started" in types
    assert "push_done" in types
