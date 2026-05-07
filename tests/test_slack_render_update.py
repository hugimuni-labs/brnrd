"""Tests for the Slack live progress card (``render_update`` hook)."""

from __future__ import annotations

from pathlib import Path

from brr import updates
from brr.gates import slack
from brr.task import Task


def _seed_task(
    brr_dir: Path,
    task_id: str,
    *,
    channel: str = "C12345",
    thread_ts: str | None = "1700000.0001",
    source: str = "slack",
) -> Task:
    tasks_dir = brr_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    meta = {"slack_channel": channel}
    if thread_ts is not None:
        meta["slack_thread_ts"] = thread_ts
    conv_key = f"slack:{channel}:" + (thread_ts or "")
    task = Task(
        id=task_id, event_id="evt-" + task_id, body="x",
        env="docker", status="running",
        source=source, conversation_key=conv_key,
        meta=meta,
    )
    task.save(tasks_dir)
    return task


def _save_token(brr_dir: Path, token: str = "xoxb-secret",
                channel: str = "C12345") -> None:
    slack._save_state(brr_dir, {"token": token, "channel": channel})


def _emit(brr_dir: Path, conv_key: str, ptype: str, **payload):
    updates.emit(brr_dir, updates.UpdatePacket(
        type=ptype, conversation_key=conv_key, payload=payload,
    ))


def test_render_update_posts_message_on_task_created(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-sl-1")

    api_calls: list[tuple] = []

    def fake_slack_api(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "chat.postMessage":
            return {"ok": True, "ts": "1700000.0500"}
        if method == "chat.update":
            return {"ok": True, "ts": params["ts"]}
        return {"ok": True}

    monkeypatch.setattr(slack, "_slack_api", fake_slack_api)

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="docker")

    posts = [c for c in api_calls if c[1] == "chat.postMessage"]
    assert len(posts) == 1
    params = posts[0][2]
    assert params["channel"] == "C12345"
    assert params["thread_ts"] == "1700000.0001"
    assert task.id in params["text"]
    state = slack._load_progress_state(brr_dir)
    assert state[task.id]["ts"] == "1700000.0500"


def test_render_update_updates_existing_message(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-sl-2")

    api_calls: list[tuple] = []

    def fake_slack_api(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "chat.postMessage":
            return {"ok": True, "ts": "1700000.0900"}
        if method == "chat.update":
            return {"ok": True, "ts": params["ts"]}
        return {"ok": True}

    monkeypatch.setattr(slack, "_slack_api", fake_slack_api)

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="host")
    _emit(brr_dir, task.conversation_key, "run_started", task_id=task.id)
    _emit(brr_dir, task.conversation_key, "done", task_id=task.id,
          event_id=task.event_id)

    methods = [m for _, m, _ in api_calls]
    assert methods.count("chat.postMessage") == 1
    assert methods.count("chat.update") >= 2
    last_update = next(c for c in reversed(api_calls) if c[1] == "chat.update")
    assert last_update[2]["channel"] == "C12345"
    assert last_update[2]["ts"] == "1700000.0900"
    assert "done" in last_update[2]["text"]


def test_render_update_falls_back_to_post_when_update_fails(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-sl-3")

    api_calls: list[tuple] = []
    fail_update = {"flag": True}

    def fake_slack_api(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "chat.postMessage":
            return {"ok": True, "ts": f"1700000.0{len(api_calls):03d}"}
        if method == "chat.update":
            if fail_update["flag"]:
                fail_update["flag"] = False
                raise RuntimeError("message lost")
            return {"ok": True, "ts": params["ts"]}
        return {"ok": True}

    monkeypatch.setattr(slack, "_slack_api", fake_slack_api)

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="host")
    _emit(brr_dir, task.conversation_key, "run_started", task_id=task.id)

    posts = [c for c in api_calls if c[1] == "chat.postMessage"]
    assert len(posts) == 2


def test_render_update_ignores_non_slack_tasks(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-sl-x", source="telegram")

    api_calls: list[tuple] = []
    monkeypatch.setattr(
        slack, "_slack_api",
        lambda t, m, p=None: api_calls.append((t, m, p)) or {"ok": True},
    )

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="host")
    assert api_calls == []


def test_render_update_skips_when_token_missing(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    task = _seed_task(brr_dir, "task-sl-no-token")

    api_calls: list[tuple] = []
    monkeypatch.setattr(
        slack, "_slack_api",
        lambda t, m, p=None: api_calls.append((t, m, p)) or {"ok": True},
    )

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="host")
    assert api_calls == []
