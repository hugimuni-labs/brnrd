"""Tests for the Slack live progress card (``render_update`` hook)."""

from __future__ import annotations

from pathlib import Path

from brr import stream as stream_mod, updates
from brr.gates import slack


def _seed_stream(brr_dir: Path, *, sid: str = "stream-slack-1",
                 channel: str = "C12345",
                 thread_ts: str | None = "1700000.0001"
                 ) -> stream_mod.StreamManifest:
    gate_ctx = {"source": "slack", "slack_channel": channel}
    if thread_ts is not None:
        gate_ctx["slack_thread_ts"] = thread_ts
    manifest = stream_mod.StreamManifest(
        id=sid,
        title="Refactor login",
        status="active",
        intent="Make login testable",
        gate_context=gate_ctx,
    )
    stream_mod.save_manifest(brr_dir, manifest)
    return manifest


def _save_token(brr_dir: Path, token: str = "xoxb-secret",
                channel: str = "C12345") -> None:
    slack._save_state(brr_dir, {"token": token, "channel": channel})


def _emit(brr_dir: Path, sid: str, ptype: str, **payload):
    updates.emit(brr_dir, updates.UpdatePacket(
        type=ptype, stream_id=sid, payload=payload,
    ))


def test_render_update_posts_message_on_task_created(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    manifest = _seed_stream(brr_dir)
    sid = manifest.id

    api_calls: list[tuple] = []

    def fake_slack_api(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "chat.postMessage":
            return {"ok": True, "ts": "1700000.0500"}
        if method == "chat.update":
            return {"ok": True, "ts": params["ts"]}
        return {"ok": True}

    monkeypatch.setattr(slack, "_slack_api", fake_slack_api)

    _emit(brr_dir, sid, "task_created", task_id="task-sl-1",
          branch="auto", env="docker")

    posts = [c for c in api_calls if c[1] == "chat.postMessage"]
    assert len(posts) == 1
    params = posts[0][2]
    assert params["channel"] == "C12345"
    assert params["thread_ts"] == "1700000.0001"
    assert "task-sl-1" in params["text"]
    state = slack._load_progress_state(brr_dir)
    assert state[f"{sid}:task-sl-1"]["ts"] == "1700000.0500"


def test_render_update_updates_existing_message(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    manifest = _seed_stream(brr_dir, sid="stream-slack-update")
    sid = manifest.id

    api_calls: list[tuple] = []

    def fake_slack_api(token, method, params=None):
        api_calls.append((token, method, params))
        if method == "chat.postMessage":
            return {"ok": True, "ts": "1700000.0900"}
        if method == "chat.update":
            return {"ok": True, "ts": params["ts"]}
        return {"ok": True}

    monkeypatch.setattr(slack, "_slack_api", fake_slack_api)

    _emit(brr_dir, sid, "task_created", task_id="task-sl-2",
          branch="auto", env="host")
    _emit(brr_dir, sid, "run_started", task_id="task-sl-2")
    _emit(brr_dir, sid, "done", task_id="task-sl-2", event_id="evt-sl-2")

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
    manifest = _seed_stream(brr_dir, sid="stream-slack-fallback")
    sid = manifest.id

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

    _emit(brr_dir, sid, "task_created", task_id="task-sl-3", branch="auto",
          env="host")
    _emit(brr_dir, sid, "run_started", task_id="task-sl-3")

    posts = [c for c in api_calls if c[1] == "chat.postMessage"]
    assert len(posts) == 2


def test_render_update_ignores_non_slack_streams(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    _save_token(brr_dir)
    manifest = stream_mod.StreamManifest(
        id="stream-tg-only", title="Telegram",
        gate_context={"source": "telegram", "telegram_chat_id": 1},
    )
    stream_mod.save_manifest(brr_dir, manifest)

    api_calls: list[tuple] = []
    monkeypatch.setattr(
        slack, "_slack_api",
        lambda t, m, p=None: api_calls.append((t, m, p)) or {"ok": True},
    )

    _emit(brr_dir, manifest.id, "task_created", task_id="task-sl-x",
          branch="auto", env="host")
    assert api_calls == []


def test_render_update_skips_when_token_missing(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    manifest = _seed_stream(brr_dir, sid="stream-slack-no-token")

    api_calls: list[tuple] = []
    monkeypatch.setattr(
        slack, "_slack_api",
        lambda t, m, p=None: api_calls.append((t, m, p)) or {"ok": True},
    )

    _emit(brr_dir, manifest.id, "task_created", task_id="task-sl-x",
          branch="auto", env="host")
    assert api_calls == []
