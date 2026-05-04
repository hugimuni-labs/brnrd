"""Tests for the expanded daemon lifecycle packets.

These tests verify that the worker emits the new run-progress packets
in the right order for happy-path, retry, and Docker-preserved-container
scenarios. They reuse the lightweight scaffolding pattern from
``test_daemon_streams.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import daemon, envs, stream as stream_mod
from brr.runner import RunnerResult
from brr.task import Task


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
    monkeypatch.setattr(
        daemon.runner, "build_triage_prompt",
        lambda body, eid, _root, **_kw: f"TRIAGE {eid}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner, "build_daemon_prompt",
        lambda task, eid, rp, _root, **kw: f"RUN {eid}: {task} -> {rp}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _: False)


def _success_invoke_runner(triage_branch: str = "current",
                           triage_env: str = "host"):
    triage_stdout = (
        f"---\nbranch: {triage_branch}\nenv: {triage_env}\n---\n"
        "refined task body\n"
    )

    def _fake(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"], stdout=triage_stdout, stderr="",
                returncode=0, trace_dir=None, artifacts=[],
            )
        Path(invocation.response_path).write_text(
            "---\n---\nall done\n", encoding="utf-8",
        )
        return RunnerResult(
            invocation=invocation, runner_name=runner_name,
            command=["mock"], stdout="ok", stderr="",
            returncode=0, trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=Path(invocation.response_path),
                    label=f"response:{invocation.label}",
                    exists=True, trace_copy=None,
                )
            ],
        )

    return _fake


def _packet_types(brr_dir: Path, sid: str) -> list[str]:
    return [ev.get("type") for ev in stream_mod.read_events(brr_dir, sid)]


def test_success_emits_full_progress_lifecycle(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(
        tmp_path, eid="evt-success", body="ship it",
        telegram_chat_id=10, telegram_topic_id=1,
    )
    _patch_runner(monkeypatch)
    monkeypatch.setattr(daemon.runner, "invoke_runner", _success_invoke_runner())

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    types = _packet_types(tmp_path / ".brr", task.stream_id)
    assert "task_created" in types
    assert "triage_done" in types
    assert "env_prepared" in types
    assert "attempt_started" in types
    assert "run_started" in types
    assert "finalizing" in types
    assert "done" in types
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

    triage_stdout = "---\nbranch: current\nenv: host\n---\nbody\n"

    def _retry_invoke(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"], stdout=triage_stdout, stderr="",
                returncode=0, trace_dir=None, artifacts=[],
            )
        response = Path(invocation.response_path)
        if invocation.label.endswith("attempt-1"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"], stdout="first try", stderr="",
                returncode=0, trace_dir=None,
                artifacts=[
                    daemon.runner.RunnerArtifactRecord(
                        path=response, label="response:evt-retry",
                        exists=False, trace_copy=None,
                    )
                ],
            )
        response.write_text("---\n---\ndone\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name,
            command=["mock"], stdout="second try", stderr="",
            returncode=0, trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=response, label="response:evt-retry",
                    exists=True, trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", _retry_invoke)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 1,
    )

    assert task.status == "done"
    events = stream_mod.read_events(tmp_path / ".brr", task.stream_id)
    types = [ev.get("type") for ev in events]
    assert types.count("attempt_started") == 2
    assert "attempt_failed" in types
    assert "retrying" in types
    failed = next(e for e in events if e.get("type") == "attempt_failed")
    assert failed.get("will_retry") is True


def test_failure_after_retries_emits_failed_and_finalizing(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, eid="evt-fail", body="never works")
    _patch_runner(monkeypatch)

    triage_stdout = "---\nbranch: current\nenv: host\n---\nbody\n"

    def _always_fail(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"], stdout=triage_stdout, stderr="",
                returncode=0, trace_dir=None, artifacts=[],
            )
        return RunnerResult(
            invocation=invocation, runner_name=runner_name,
            command=["mock"], stdout="nope", stderr="",
            returncode=0, trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=Path(invocation.response_path),
                    label=f"response:{invocation.label}",
                    exists=False, trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", _always_fail)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    types = _packet_types(tmp_path / ".brr", task.stream_id)
    assert "attempt_failed" in types
    assert "failed" in types
    assert types.index("failed") < types.index("finalizing")


class _FakeDockerEnv:
    """In-memory Docker env stub for daemon packet assertions."""

    name = "docker"

    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed
        self.containers: list[str] = []

    def prepare(self, task, repo_root, cfg, *, branch_name, base_branch,
                response_path, debug=False):
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
            response.write_text("---\n---\ndocker ok\n", encoding="utf-8")
            exists = True
        else:
            exists = False
        return RunnerResult(
            invocation=invocation, runner_name=runner_name,
            command=["mock"], stdout="ok" if self.succeed else "bad", stderr="",
            returncode=0, trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=response,
                    label=f"response:{invocation.label}",
                    exists=exists, trace_copy=None,
                )
            ],
        )

    def finalize(self, ctx, task, tasks_dir, *, debug=False):
        preserved = ctx.env_state.get("docker_containers", [])
        if preserved and task.status != "done":
            task.meta["docker_containers"] = ", ".join(preserved)
            task.save(tasks_dir)
        return task


def _triage_only_invoke(env_name: str = "docker"):
    triage_stdout = f"---\nbranch: current\nenv: {env_name}\n---\nbody\n"

    def _fake(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"], stdout=triage_stdout, stderr="",
                returncode=0, trace_dir=None, artifacts=[],
            )
        # Daemon-run goes through the env backend; this path should be unused.
        raise AssertionError("env backend should handle invoke")

    return _fake


def test_docker_env_emits_container_started(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, eid="evt-docker", body="run docker")
    _patch_runner(monkeypatch)

    fake_env = _FakeDockerEnv(succeed=True)
    monkeypatch.setattr(envs, "get_env", lambda _name: fake_env)
    monkeypatch.setattr(daemon.runner, "invoke_runner", _triage_only_invoke())

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    types = _packet_types(tmp_path / ".brr", task.stream_id)
    assert "container_started" in types
    container_event = next(
        e for e in stream_mod.read_events(tmp_path / ".brr", task.stream_id)
        if e.get("type") == "container_started"
    )
    assert container_event.get("container", "").startswith("brr-")


def test_docker_failed_emits_container_preserved(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, eid="evt-docker-fail", body="never finishes")
    _patch_runner(monkeypatch)

    fake_env = _FakeDockerEnv(succeed=False)
    monkeypatch.setattr(envs, "get_env", lambda _name: fake_env)
    monkeypatch.setattr(daemon.runner, "invoke_runner", _triage_only_invoke())

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    types = _packet_types(tmp_path / ".brr", task.stream_id)
    assert "failed" in types
    assert "container_preserved" in types
    preserved = next(
        e for e in stream_mod.read_events(tmp_path / ".brr", task.stream_id)
        if e.get("type") == "container_preserved"
    )
    assert preserved.get("containers"), preserved


def test_push_emits_started_and_done_packets(tmp_path, monkeypatch):
    """_push_if_needed should emit push packets when commits are unpushed."""
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    sid = "stream-push-1"
    manifest = stream_mod.StreamManifest(id=sid, title="Push test")
    stream_mod.save_manifest(brr_dir, manifest)

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

    daemon._push_if_needed(tmp_path, stream_id=sid, task_id="task-push")

    types = _packet_types(brr_dir, sid)
    assert "push_started" in types
    assert "push_done" in types
