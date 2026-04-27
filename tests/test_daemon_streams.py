"""Tests for daemon ↔ stream integration.

Covers the wiring added by the workstream-ergonomics plan: the worker
resolves an event to a stream, threads stream context through triage
and run prompts, persists task/artifact records, normalises reply
routes from the response, and emits lifecycle update packets.
"""

from __future__ import annotations

from pathlib import Path

from brr import daemon, stream as stream_mod, updates
from brr.runner import RunnerResult
from brr.task import Task


def _write_repo_scaffold(repo_root: Path) -> None:
    (repo_root / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    (repo_root / ".brr" / "inbox").mkdir(parents=True)
    (repo_root / ".brr" / "responses").mkdir(parents=True)


def _make_event(repo_root: Path, *, eid: str, body: str, source: str = "telegram",
                **extra) -> dict:
    event = {
        "id": eid,
        "status": "pending",
        "body": body,
        "source": source,
        "_path": repo_root / ".brr" / "inbox" / f"{eid}.md",
        **extra,
    }
    event["_path"].write_text(
        f"---\nid: {eid}\nstatus: pending\nsource: {source}\n---\n{body}\n",
        encoding="utf-8",
    )
    return event


def _install_runner_mocks(monkeypatch, *, captured: list) -> None:
    """Wire runner mocks. *captured* receives (kind, prompt, kwargs) tuples."""
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _: "codex")

    def _triage_prompt(body, eid, _root, **kwargs):
        captured.append(("triage", body, kwargs))
        return f"TRIAGE {eid}: {body}"

    def _daemon_prompt(task, eid, rp, _root, **kwargs):
        captured.append(("daemon", task, kwargs))
        return f"RUN {eid}: {task} -> {rp}"

    monkeypatch.setattr(daemon.runner, "build_triage_prompt", _triage_prompt)
    monkeypatch.setattr(daemon.runner, "build_daemon_prompt", _daemon_prompt)
    monkeypatch.setattr(daemon, "_kb_changed", lambda _: False)


def _make_invoke_runner(*, response_text: str = "---\n---\nall done\n",
                       triage_stdout: str | None = None):
    """Build a fake invoke_runner that always succeeds."""
    triage_stdout = triage_stdout or (
        "---\nbranch: current\nenv: local\n---\nrefined task body\n"
    )

    def _fake(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"], stdout=triage_stdout, stderr="",
                returncode=0, trace_dir=None, artifacts=[],
            )
        Path(invocation.response_path).write_text(response_text, encoding="utf-8")
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


def test_run_worker_resolves_stream_and_persists_records(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(
        tmp_path, eid="evt-stream-1", body="set up auth",
        telegram_chat_id=99, telegram_topic_id=1,
    )
    captured: list = []
    _install_runner_mocks(monkeypatch, captured=captured)
    monkeypatch.setattr(
        daemon.runner, "invoke_runner", _make_invoke_runner(),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )
    brr_dir = tmp_path / ".brr"

    assert task.status == "done"
    assert task.stream_id, "task should carry a stream_id"

    streams = stream_mod.list_streams(brr_dir)
    assert len(streams) == 1
    sid = streams[0].id
    assert sid == task.stream_id

    # Append-only records were populated.
    events = stream_mod.read_events(brr_dir, sid)
    tasks = stream_mod.read_tasks(brr_dir, sid)
    artifacts = stream_mod.read_artifacts(brr_dir, sid)
    assert any(ev.get("event_id") == "evt-stream-1" for ev in events)
    assert any(t.get("task_id") == task.id for t in tasks)
    assert any(a.get("kind") == "response" for a in artifacts)
    # Lifecycle update packets were appended too.
    assert any(ev.get("type") == "stream_created" for ev in events)
    assert any(ev.get("type") == "task_created" for ev in events)
    assert any(ev.get("type") == "done" for ev in events)


def test_run_worker_threads_stream_through_prompts(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(
        tmp_path, eid="evt-prompt-1", body="follow up",
        telegram_chat_id=1, telegram_topic_id=2,
    )
    captured: list = []
    _install_runner_mocks(monkeypatch, captured=captured)
    monkeypatch.setattr(
        daemon.runner, "invoke_runner", _make_invoke_runner(),
    )

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    triage_calls = [c for c in captured if c[0] == "triage"]
    daemon_calls = [c for c in captured if c[0] == "daemon"]
    assert triage_calls, "triage prompt builder should be invoked"
    assert daemon_calls, "daemon prompt builder should be invoked"
    # Both stages receive the resolved stream manifest.
    triage_kwargs = triage_calls[0][2]
    daemon_kwargs = daemon_calls[0][2]
    assert triage_kwargs.get("stream") is not None
    assert daemon_kwargs.get("stream") is not None
    assert daemon_kwargs.get("event_body") == "follow up"


def test_run_worker_followup_in_same_thread_reuses_stream(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    captured: list = []
    _install_runner_mocks(monkeypatch, captured=captured)
    monkeypatch.setattr(
        daemon.runner, "invoke_runner", _make_invoke_runner(),
    )

    first = _make_event(
        tmp_path, eid="evt-thread-1", body="kick off",
        telegram_chat_id=10, telegram_topic_id=5,
    )
    daemon._run_worker(first, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    second = _make_event(
        tmp_path, eid="evt-thread-2", body="ping again",
        telegram_chat_id=10, telegram_topic_id=5,
    )
    second_task = daemon._run_worker(
        second, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    streams = stream_mod.list_streams(tmp_path / ".brr")
    assert len(streams) == 1, "follow-up should attach to existing stream"
    assert second_task.stream_id == streams[0].id


def test_run_worker_records_reply_route_from_response(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(
        tmp_path, eid="evt-route-1", body="open a PR",
        telegram_chat_id=20,
    )
    captured: list = []
    _install_runner_mocks(monkeypatch, captured=captured)
    response_text = (
        "---\nreply_route:\n  preferred: git_pr\n---\nthe body\n"
    )
    monkeypatch.setattr(
        daemon.runner, "invoke_runner",
        _make_invoke_runner(response_text=response_text),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )
    manifest = stream_mod.load_manifest(tmp_path / ".brr", task.stream_id)
    assert manifest is not None
    # git_pr is in the default allowed list, so it should be selected.
    assert manifest.reply_route["selected"] == "git_pr"


def test_run_worker_rejects_disallowed_reply_route(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(
        tmp_path, eid="evt-route-2", body="ship it",
        telegram_chat_id=30,
    )
    captured: list = []
    _install_runner_mocks(monkeypatch, captured=captured)

    # Pre-create a stream with restricted allowed routes.
    brr_dir = tmp_path / ".brr"
    res = stream_mod.resolve_for_event(brr_dir, event)
    manifest = stream_mod.load_manifest(brr_dir, res.stream_id)
    assert manifest is not None
    manifest.reply_route = {
        "preferred": "input_gate",
        "selected": "input_gate",
        "allowed": ["input_gate"],
    }
    stream_mod.save_manifest(brr_dir, manifest)

    response_text = (
        "---\nreply_route:\n  preferred: git_pr\n---\nthe body\n"
    )
    monkeypatch.setattr(
        daemon.runner, "invoke_runner",
        _make_invoke_runner(response_text=response_text),
    )

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)
    refreshed = stream_mod.load_manifest(brr_dir, res.stream_id)
    assert refreshed is not None
    assert refreshed.reply_route["selected"] == "input_gate"
