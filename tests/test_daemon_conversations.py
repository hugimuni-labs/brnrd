"""Daemon-level tests for conversation routing and recent-history threading."""

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


def _stub_env(monkeypatch, tmp_path):
    """Stub env backend that just runs the runner and returns the task."""

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, base_branch, response_path, branch_plan=None, debug=False):
            return envs.RunContext(
                name=self.name,
                cwd=tmp_path,
                repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                branch_name=f"brr/{task.id}",
                base_branch=base_branch,
                env_state={"worktree_path": str(tmp_path)},
            )

        def invoke(self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
            Path(invocation.response_path).write_text("result\n", encoding="utf-8")
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="result\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
            )

        def finalize(self, _ctx, task, _tasks_dir, *, debug=False):
            return task

    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: StubEnv())


def _patch_runner_minimal(monkeypatch, captured_prompts=None):
    captured_prompts = captured_prompts if captured_prompts is not None else []
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")

    def _build_daemon(task, eid, rp, _root, **kw):
        captured_prompts.append(("daemon", eid, kw.get("recent_conversation")))
        return f"RUN {eid}: {task} -> {rp}"

    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", _build_daemon)
    monkeypatch.setattr(daemon, "_kb_changed", lambda _: False)
    monkeypatch.setattr(
        daemon.runner,
        "invoke_runner",
        lambda *_a, **_kw: RunnerResult(
            invocation=_a[1], runner_name=_a[0], command=["mock"],
            stdout="ok", stderr="", returncode=0, trace_dir=None, artifacts=[],
        ),
    )
    return captured_prompts


def test_run_worker_routes_to_conversation_and_persists_records(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(
        tmp_path, eid="evt-conv-1", body="ship it",
        telegram_chat_id=42, telegram_topic_id=5,
    )
    _patch_runner_minimal(monkeypatch)
    _stub_env(monkeypatch, tmp_path)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.conversation_key == "telegram:42:5"
    records = conversations.read_records(tmp_path / ".brr", task.conversation_key)
    kinds = [r.get("kind") for r in records]
    assert "event" in kinds
    assert "task" in kinds
    assert "update" in kinds
    artifact_kinds = [
        r.get("artifact_kind") for r in records if r.get("kind") == "artifact"
    ]
    assert "response" in artifact_kinds


def test_run_worker_threads_recent_conversation_through_prompt(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    first = _make_event(
        tmp_path, eid="evt-thread-1", body="first",
        telegram_chat_id=77,
    )
    second = _make_event(
        tmp_path, eid="evt-thread-2", body="second",
        telegram_chat_id=77,
    )

    captured: list = []
    _patch_runner_minimal(monkeypatch, captured)
    _stub_env(monkeypatch, tmp_path)

    daemon._run_worker(
        first, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )
    daemon._run_worker(
        second, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    daemon_records = [
        c[2] for c in captured if c[0] == "daemon" and c[1] == "evt-thread-2"
    ][0]
    # The daemon prompt receives prior conversation records only. The
    # in-flight event/task are rendered elsewhere in the Task Context Bundle.
    assert daemon_records is not None
    assert any(r.get("event_id") == "evt-thread-1" for r in daemon_records)
    assert not any(r.get("event_id") == "evt-thread-2" for r in daemon_records)


def test_run_worker_followup_in_same_thread_reuses_conversation(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    _patch_runner_minimal(monkeypatch)
    _stub_env(monkeypatch, tmp_path)

    first = _make_event(
        tmp_path, eid="evt-thread-A", body="initial",
        telegram_chat_id=88, telegram_topic_id=3,
    )
    second = _make_event(
        tmp_path, eid="evt-thread-B", body="follow-up",
        telegram_chat_id=88, telegram_topic_id=3,
    )

    task1 = daemon._run_worker(
        first, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )
    task2 = daemon._run_worker(
        second, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task1.conversation_key == task2.conversation_key == "telegram:88:3"
    records = conversations.read_records(tmp_path / ".brr", task1.conversation_key)
    event_ids = [r.get("event_id") for r in records if r.get("kind") == "event"]
    assert "evt-thread-A" in event_ids
    assert "evt-thread-B" in event_ids
