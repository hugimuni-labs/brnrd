"""Tests for the Telegram live progress card (``render_update`` hook)."""

from __future__ import annotations

import json
from pathlib import Path

from brr import stream as stream_mod, updates
from brr.gates import telegram


def _seed_stream(brr_dir: Path, sid: str = "stream-tg-1",
                 chat_id: int = 555,
                 topic_id: int | None = None) -> stream_mod.StreamManifest:
    gate_ctx = {"source": "telegram", "telegram_chat_id": chat_id}
    if topic_id is not None:
        gate_ctx["telegram_topic_id"] = topic_id
    manifest = stream_mod.StreamManifest(
        id=sid,
        title="Refactor login",
        status="active",
        intent="Make login testable",
        gate_context=gate_ctx,
    )
    stream_mod.save_manifest(brr_dir, manifest)
    return manifest


def _save_token(brr_dir: Path, token: str = "secret") -> None:
    telegram._save_state(brr_dir, {"token": token})


def _emit(brr_dir: Path, sid: str, ptype: str, **payload):
    updates.emit(brr_dir, updates.UpdatePacket(
        type=ptype, stream_id=sid, payload=payload,
    ))


def test_render_update_sends_message_on_task_created(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    manifest = _seed_stream(brr_dir, chat_id=555, topic_id=7)
    sid = manifest.id

    api_calls: list[tuple] = []

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 42}}
        if method == "editMessageText":
            return {"result": {"message_id": 42}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    _emit(brr_dir, sid, "task_created", task_id="task-tg-1",
          event_id="evt-1", branch="auto", env="docker")

    sends = [c for c in api_calls if c[1] == "sendMessage"]
    assert len(sends) == 1
    params = sends[0][2]
    assert params["chat_id"] == 555
    assert params["message_thread_id"] == 7
    assert "task-tg-1" in params["text"]
    state = telegram._load_progress_state(brr_dir)
    key = f"{sid}:task-tg-1"
    assert state[key]["message_id"] == 42


def test_render_update_edits_existing_message(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    manifest = _seed_stream(brr_dir, chat_id=999)
    sid = manifest.id

    api_calls: list[tuple] = []

    def fake_api_call(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "sendMessage":
            return {"result": {"message_id": 100}}
        if method == "editMessageText":
            return {"result": {"message_id": 100}}
        return {}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    _emit(brr_dir, sid, "task_created", task_id="task-tg-2", branch="auto",
          env="host")
    _emit(brr_dir, sid, "run_started", task_id="task-tg-2")
    _emit(brr_dir, sid, "done", task_id="task-tg-2", event_id="evt-2")

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
    manifest = _seed_stream(brr_dir, chat_id=777)
    sid = manifest.id

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

    _emit(brr_dir, sid, "task_created", task_id="task-tg-3", branch="auto",
          env="host")
    _emit(brr_dir, sid, "run_started", task_id="task-tg-3")

    methods = [m for _, m, _ in api_calls]
    assert methods.count("sendMessage") == 2
    state = telegram._load_progress_state(brr_dir)
    assert state[f"{sid}:task-tg-3"]["message_id"] != 201


def test_render_update_ignores_non_telegram_streams(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    manifest = stream_mod.StreamManifest(
        id="stream-slack-only", title="Slack",
        gate_context={"source": "slack", "slack_channel": "C1"},
    )
    stream_mod.save_manifest(brr_dir, manifest)

    api_calls: list[tuple] = []
    monkeypatch.setattr(
        telegram, "_api_call",
        lambda t, m, p=None: api_calls.append((t, m, p)) or {},
    )

    _emit(brr_dir, manifest.id, "task_created", task_id="task-tg-x",
          branch="auto", env="host")
    assert api_calls == []


def test_render_update_skips_when_token_missing(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    manifest = _seed_stream(brr_dir, "stream-no-token")

    api_calls: list[tuple] = []
    monkeypatch.setattr(
        telegram, "_api_call",
        lambda t, m, p=None: api_calls.append((t, m, p)) or {},
    )

    _emit(brr_dir, manifest.id, "task_created", task_id="task-no-token",
          branch="auto", env="host")
    assert api_calls == []
