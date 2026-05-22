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
    message_id: int | None = None,
) -> Task:
    tasks_dir = brr_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    meta = {"telegram_chat_id": chat_id}
    if topic_id is not None:
        meta["telegram_topic_id"] = topic_id
    if message_id is not None:
        meta["telegram_message_id"] = message_id
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
    # Compact card opens with the env in the header (no task ID — that's
    # dev-side noise in a chat reply).
    assert "docker" in params["text"]
    assert "preparing" in params["text"]
    assert task.id not in params["text"]
    # Telegram-flavoured rendering: HTML parse_mode so the strike-
    # through markup later in the lifecycle renders as the user expects.
    assert params.get("parse_mode") == "HTML"
    entry = telegram._load_progress_for_task(brr_dir, task.id)
    assert entry["message_id"] == 42


def test_render_update_threads_first_send_under_source_message(
    tmp_path, monkeypatch,
):
    """The initial progress card must reply to the user's message.

    Edits don't carry a reply target (Telegram has no way to change
    a message's reply pointer after the fact), so only the very first
    ``sendMessage`` should set ``reply_to_message_id``.
    """
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-tg-thread", chat_id=555, message_id=4242)

    api_calls: list[tuple] = []

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 90}}
        if method == "editMessageText":
            return {"result": {"message_id": 90}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          event_id=task.event_id, branch="auto", env="host")
    _emit(brr_dir, task.conversation_key, "attempt_started", task_id=task.id,
          attempt=1)

    sends = [c for c in api_calls if c[1] == "sendMessage"]
    edits = [c for c in api_calls if c[1] == "editMessageText"]
    assert len(sends) == 1
    assert sends[0][2]["reply_to_message_id"] == 4242
    assert sends[0][2]["allow_sending_without_reply"] is True
    # Edits never carry the reply pointer — Telegram rejects it on edit
    # endpoints and we'd lose the visible thread anchor either way.
    assert all("reply_to_message_id" not in c[2] for c in edits)


def test_render_update_first_send_omits_reply_to_when_unknown(
    tmp_path, monkeypatch,
):
    # Legacy tasks (created before the message-id capture landed) carry
    # no ``telegram_message_id``; the gate must still post the card,
    # just without the reply pointer.
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-tg-legacy", chat_id=555)

    api_calls: list[tuple] = []

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 91}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          event_id=task.event_id, branch="auto", env="host")

    sends = [c for c in api_calls if c[1] == "sendMessage"]
    assert len(sends) == 1
    assert "reply_to_message_id" not in sends[0][2]


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
    _emit(brr_dir, task.conversation_key, "attempt_started", task_id=task.id,
          attempt=1)
    _emit(brr_dir, task.conversation_key, "finalizing", task_id=task.id,
          stage="done")
    _emit(brr_dir, task.conversation_key, "done", task_id=task.id,
          event_id=task.event_id)

    methods = [m for _, m, _ in api_calls]
    assert methods.count("sendMessage") == 1
    assert methods.count("editMessageText") >= 2
    last_edit = next(c for c in reversed(api_calls) if c[1] == "editMessageText")
    assert last_edit[2]["chat_id"] == 999
    assert last_edit[2]["message_id"] == 100
    # Terminal phase reads as "delivered" in the new card layout.
    assert "delivered" in last_edit[2]["text"]
    assert last_edit[2].get("parse_mode") == "HTML"


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
    # attempt_started actually changes the rendered card (preparing →
    # running), so the gate tries to edit; the stub raises, which forces
    # the fall-back sendMessage.
    _emit(brr_dir, task.conversation_key, "attempt_started", task_id=task.id,
          attempt=1)

    methods = [m for _, m, _ in api_calls]
    assert methods.count("sendMessage") == 2
    entry = telegram._load_progress_for_task(brr_dir, task.id)
    assert entry["message_id"] != 201


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
    """Packets that don't change anything visible in the compact card
    must be dropped before any HTTP request.

    Regresses the duplication bug where a sequence of packets projecting
    to the same compact card triggered an editMessageText call, received
    "message is not modified" 400, fell through to sendMessage, and
    posted a duplicate. We dedupe before hitting the API.

    container_started and artifact_created are tracked in the verbose
    view but are intentionally invisible in the compact card, so they're
    the natural "should produce zero traffic" cases.
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

    # task_created → preparing card. Initial post.
    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="docker")
    # container_started → no compact-card change → no API call.
    _emit(brr_dir, task.conversation_key, "container_started",
          task_id=task.id, env="docker", container="brr-task-tg-dedupe-x")
    # artifact_created → also invisible in compact → no API call.
    _emit(brr_dir, task.conversation_key, "artifact_created",
          task_id=task.id, kind="response",
          path="/tmp/r.md", label="response:evt-x")

    methods = [m for _, m, _ in api_calls]
    assert methods.count("sendMessage") == 1
    assert methods.count("editMessageText") == 0


def test_render_update_html_escapes_user_content(tmp_path, monkeypatch):
    """Telegram parses HTML in the card text. Any user-controlled string
    that lands in the failure detail (runner stderr, branch names with
    ``<``, etc.) must be escaped before render so Telegram doesn't 400
    on ``can't parse entities``."""
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-tg-esc", chat_id=333)

    api_calls: list[tuple] = []

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 700}}
        if method == "editMessageText":
            return {"result": {"message_id": 700}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    _emit(brr_dir, task.conversation_key, "task_created", task_id=task.id,
          branch="auto", env="docker")
    _emit(brr_dir, task.conversation_key, "attempt_started",
          task_id=task.id, attempt=1)
    _emit(brr_dir, task.conversation_key, "finalizing",
          task_id=task.id, stage="failed")
    _emit(brr_dir, task.conversation_key, "failed",
          task_id=task.id, event_id=task.event_id,
          stage="run", attempts=1, exit_code=1,
          error="docker run failed: <ulimit & oom-killer>")

    last_edit = next(c for c in reversed(api_calls) if c[1] == "editMessageText")
    text = last_edit[2]["text"]
    # Raw < / & must not survive into the payload — they get escaped.
    assert "<ulimit" not in text
    assert "&lt;ulimit &amp; oom-killer&gt;" in text
    # The strike-through tags themselves stay intact.
    assert "<s>preparing" in text
    assert last_edit[2].get("parse_mode") == "HTML"


def test_render_update_treats_not_modified_as_noop(tmp_path, monkeypatch):
    """Telegram's 400 'message is not modified' must not fall through to send.

    Cached state should detect the unchanged text earlier, but if a
    request reaches Telegram and comes back with that error (e.g. fresh
    daemon process with no cached state), render_update should treat it
    as a successful no-op.
    """
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    task = _seed_task(brr_dir, "task-tg-nm", chat_id=222)

    # Pre-seed progress state: a prior message exists, but with no
    # cached last_text — so render_update will try to edit, hit
    # "not modified", and must NOT fall through to sendMessage.
    telegram._save_progress_for_task(brr_dir, task.id, {
        "chat_id": 222, "topic_id": None, "message_id": 900,
        "last_render": "task_created",
    })

    api_calls: list[tuple] = []

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "editMessageText":
            raise telegram._TelegramNotModified("message is not modified")
        if method == "sendMessage":
            return {"result": {"message_id": 901}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    _emit(brr_dir, task.conversation_key, "run_started", task_id=task.id)

    methods = [m for _, m, _ in api_calls]
    assert methods.count("editMessageText") == 1
    assert methods.count("sendMessage") == 0
    entry = telegram._load_progress_for_task(brr_dir, task.id)
    assert entry["message_id"] == 900
