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


def test_render_update_skips_api_when_text_unchanged(tmp_path, monkeypatch):
    """Subsequent packets that project to identical text don't hit the API.

    Regresses the duplication bug where a sequence of packets that
    project to the same compact card triggered an editMessageText call,
    received "message is not modified" 400, fell through to sendMessage,
    and posted a duplicate.

    The compact card shows phase only, so packets that don't change the
    phase (env_prepared after task_created stays "preparing"; run_started
    after attempt_started stays "running") must not produce any API
    call at all.
    """
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-tg-dedupe", chat_id=111)

    api_calls: list[tuple] = []

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 500}}
        if method == "editMessageText":
            return {"result": {"message_id": 500}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    # task_created → phase "preparing", header "preparing". Initial post.
    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="docker")
    # env_prepared → still "preparing" → identical card → no API call.
    _emit(brr_dir, task.conversation_key, "env_prepared", task_id=task.id,
          env="docker", branch_name="brr/task-tg-dedupe")
    # attempt_started → phase flips to "running" → one edit.
    _emit(brr_dir, task.conversation_key, "attempt_started", task_id=task.id,
          attempt=1)
    # run_started → still "running" → identical card → no API call.
    _emit(brr_dir, task.conversation_key, "run_started", task_id=task.id)

    methods = [m for _, m, _ in api_calls]
    # 1 send (task_created) + 1 edit (preparing → running) total. The two
    # no-op packets are dropped before any HTTP request.
    assert methods.count("sendMessage") == 1
    assert methods.count("editMessageText") == 1


def test_render_update_treats_not_modified_as_noop(tmp_path, monkeypatch):
    """Telegram's 400 'message is not modified' must not fall through to send.

    Cached state should detect the unchanged text earlier, but if a
    request reaches Telegram and comes back with that error (e.g. fresh
    daemon process with no cached state), render_update should treat it
    as a successful no-op.
    """
    import urllib.error

    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-tg-nm", chat_id=222)

    # Pre-seed progress state: a prior message exists, but with no
    # cached last_text — so render_update will try to edit, hit
    # "not modified", and must NOT fall through to sendMessage.
    telegram._save_progress_state(brr_dir, {
        task.id: {"chat_id": 222, "topic_id": None, "message_id": 900,
                  "last_render": "task_created"},
    })

    api_calls: list[tuple] = []

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "editMessageText":
            err = urllib.error.HTTPError(
                "https://api.telegram.org/...",
                400,
                "Bad Request",
                {},
                None,
            )
            err.read = lambda: (
                b'{"ok":false,"error_code":400,'
                b'"description":"Bad Request: message is not modified"}'
            )
            raise telegram._TelegramNotModified("message is not modified")
        if method == "sendMessage":
            return {"result": {"message_id": 901}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    _emit(brr_dir, task.conversation_key, "run_started", task_id=task.id)

    methods = [m for _, m, _ in api_calls]
    assert methods.count("editMessageText") == 1
    assert methods.count("sendMessage") == 0
    state = telegram._load_progress_state(brr_dir)
    assert state[task.id]["message_id"] == 900
