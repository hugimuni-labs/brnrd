"""Tests for the Telegram live progress card (``render_update`` hook)."""

from __future__ import annotations

from pathlib import Path

from brr import updates
from brr.gates import telegram
from brr.task import Task


def _seed_task(
    brr_dir: Path,
    task_id: str,
    *,
    chat_id: int = 555,
    topic_id: int | None = None,
    source: str = "telegram",
) -> Task:
    tasks_dir = brr_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    meta = {"telegram_chat_id": chat_id}
    if topic_id is not None:
        meta["telegram_topic_id"] = topic_id
    conv_key = f"telegram:{chat_id}:" + (str(topic_id) if topic_id is not None else "")
    task = Task(
        id=task_id, event_id="evt-" + task_id, body="x",
        env="docker", status="running",
        source=source, conversation_key=conv_key,
        meta=meta,
    )
    task.save(tasks_dir)
    return task


def _save_token(brr_dir: Path, token: str = "secret") -> None:
    telegram._save_state(brr_dir, {"token": token})


def _emit(brr_dir: Path, conv_key: str, ptype: str, **payload):
    updates.emit(brr_dir, updates.UpdatePacket(
        type=ptype, conversation_key=conv_key, payload=payload,
    ))


def test_render_update_sends_message_on_task_created(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-tg-1", chat_id=555, topic_id=7)

    api_calls: list[tuple] = []

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 42}}
        if method == "editMessageText":
            return {"result": {"message_id": 42}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    _emit(brr_dir, task.conversation_key, "task_created",
          task_id=task.id, event_id=task.event_id,
          branch="auto", env="docker")

    sends = [c for c in api_calls if c[1] == "sendMessage"]
    assert len(sends) == 1
    params = sends[0][2]
    assert params["chat_id"] == 555
    assert params["message_thread_id"] == 7
    assert task.id in params["text"]
    state = telegram._load_progress_state(brr_dir)
    assert state[task.id]["message_id"] == 42


def test_render_update_edits_existing_message(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-tg-2", chat_id=999)

    api_calls: list[tuple] = []

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 100}}
        if method == "editMessageText":
            return {"result": {"message_id": 100}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="host")
    _emit(brr_dir, task.conversation_key, "run_started", task_id=task.id)
    _emit(brr_dir, task.conversation_key, "done", task_id=task.id,
          event_id=task.event_id)

    methods = [m for _, m, _ in api_calls]
    assert methods.count("sendMessage") == 1
    assert methods.count("editMessageText") >= 2
    last_edit = next(c for c in reversed(api_calls) if c[1] == "editMessageText")
    assert last_edit[2]["chat_id"] == 999
    assert last_edit[2]["message_id"] == 100
    assert "done" in last_edit[2]["text"]


def test_render_update_falls_back_to_send_when_edit_fails(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-tg-3", chat_id=777)

    api_calls: list[tuple] = []
    fail_edit = {"flag": True}

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 200 + len(api_calls)}}
        if method == "editMessageText":
            if fail_edit["flag"]:
                fail_edit["flag"] = False
                raise RuntimeError("message gone")
            return {"result": {"message_id": params["message_id"]}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="host")
    _emit(brr_dir, task.conversation_key, "run_started", task_id=task.id)

    methods = [m for _, m, _ in api_calls]
    assert methods.count("sendMessage") == 2
    state = telegram._load_progress_state(brr_dir)
    assert state[task.id]["message_id"] != 201


def test_render_update_ignores_non_telegram_tasks(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    # Slack-source task in a 'telegram:' shaped key shouldn't slip through.
    task = _seed_task(brr_dir, "task-tg-x", source="slack")

    api_calls: list[tuple] = []
    monkeypatch.setattr(
        telegram, "_api_call",
        lambda t, m, p=None: api_calls.append((t, m, p)) or {},
    )

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="host")
    assert api_calls == []


def test_render_update_skips_when_token_missing(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    task = _seed_task(brr_dir, "task-no-token")

    api_calls: list[tuple] = []
    monkeypatch.setattr(
        telegram, "_api_call",
        lambda t, m, p=None: api_calls.append((t, m, p)) or {},
    )

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="host")
    assert api_calls == []
