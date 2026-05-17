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
    assert "docker" in params["text"]
    assert "preparing" in params["text"]
    assert task.id not in params["text"]
    entry = slack._load_progress_for_task(brr_dir, task.id)
    assert entry["ts"] == "1700000.0500"


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
    _emit(brr_dir, task.conversation_key, "attempt_started", task_id=task.id,
          attempt=1)
    _emit(brr_dir, task.conversation_key, "finalizing", task_id=task.id,
          stage="done")
    _emit(brr_dir, task.conversation_key, "done", task_id=task.id,
          event_id=task.event_id)

    methods = [m for _, m, _ in api_calls]
    assert methods.count("chat.postMessage") == 1
    assert methods.count("chat.update") >= 2
    last_update = next(c for c in reversed(api_calls) if c[1] == "chat.update")
    assert last_update[2]["channel"] == "C12345"
    assert last_update[2]["ts"] == "1700000.0900"
    assert "delivered" in last_update[2]["text"]
    # Slack rendering uses the mrkdwn ``~text~`` strike-through tokens
    # for closed phase entries.
    assert "~preparing" in last_update[2]["text"]


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
    # attempt_started flips the card from "preparing" to a struck
    # "preparing" + live "running", so the gate attempts an update.
    _emit(brr_dir, task.conversation_key, "attempt_started", task_id=task.id,
          attempt=1)

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


# ── Event capture and final response delivery ──────────────────────


def test_loop_captures_parent_thread_ts(tmp_path, monkeypatch):
    """A message posted inside an existing thread surfaces ``thread_ts``.

    Slack's ``conversations.history`` returns the parent message's ts
    via the ``thread_ts`` field on replies. The gate captures it so both
    the progress card and the final response anchor under the same
    thread root instead of starting a new sibling thread.
    """
    from brr import protocol

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    slack._save_state(brr_dir, {"token": "secret", "channel": "C123"})

    def fake_slack_api(token, method, params=None):
        if method == "conversations.history":
            return {
                "ok": True,
                "messages": [
                    # Top-level message (no thread_ts) — captured plain.
                    {"ts": "1700.0001", "user": "U1", "text": "first"},
                    # Reply inside an existing thread — parent ts must
                    # ride through as slack_thread_ts.
                    {
                        "ts": "1700.0050",
                        "thread_ts": "1700.0001",
                        "user": "U2",
                        "text": "second, in-thread",
                    },
                ],
            }
        return {"ok": True}

    monkeypatch.setattr(slack, "_slack_api", fake_slack_api)
    slack._loop_once(brr_dir, inbox_dir, responses_dir)

    events = sorted(
        protocol.list_pending(inbox_dir), key=lambda ev: ev["slack_ts"],
    )
    assert [ev["body"] for ev in events] == ["first", "second, in-thread"]
    # Plain top-level post has no parent thread.
    assert events[0]["slack_thread_ts"] == ""
    # In-thread reply carries the parent ts.
    assert events[1]["slack_thread_ts"] == "1700.0001"


def test_response_delivery_threads_under_source_message(tmp_path, monkeypatch):
    """Final responses post with ``thread_ts`` matching the source.

    Previously the progress card threaded but the final response did
    not, splitting the conversation in half. Both now thread under the
    same parent so the user sees the bot's reply nested under their
    request.
    """
    from brr import protocol

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    slack._save_state(brr_dir, {"token": "secret", "channel": "C999"})

    # Two events: a top-level message (slack_ts becomes thread root)
    # and an in-thread reply (slack_thread_ts is the parent).
    protocol.create_event(
        inbox_dir, source="slack", body="first",
        slack_channel="C999", slack_user="U1",
        slack_ts="1700.1000", slack_thread_ts="",
    )
    protocol.create_event(
        inbox_dir, source="slack", body="second",
        slack_channel="C999", slack_user="U2",
        slack_ts="1700.2000", slack_thread_ts="1700.1500",
    )
    first, second = sorted(
        protocol.list_pending(inbox_dir), key=lambda ev: ev["body"],
    )
    protocol.set_status(first, "done")
    protocol.set_status(second, "done")
    protocol.write_response(responses_dir, first["id"], "answer one")
    protocol.write_response(responses_dir, second["id"], "answer two")

    posts: list[dict] = []

    def fake_slack_api(token, method, params=None):
        if method == "chat.postMessage":
            posts.append(dict(params or {}))
        return {"ok": True, "ts": "1700.9999"}

    monkeypatch.setattr(slack, "_slack_api", fake_slack_api)
    slack._deliver_responses(brr_dir, inbox_dir, responses_dir, "secret", "C999")

    assert posts == [
        # Top-level source → final response threads on its own ts.
        {"channel": "C999", "text": "answer one", "thread_ts": "1700.1000"},
        # In-thread source → final response threads on the parent ts.
        {"channel": "C999", "text": "answer two", "thread_ts": "1700.1500"},
    ]


def test_response_delivery_omits_thread_ts_when_unknown(tmp_path, monkeypatch):
    # Legacy events created before slack_ts capture landed have neither
    # field. Delivery must still post the response (just unthreaded).
    from brr import protocol

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    slack._save_state(brr_dir, {"token": "secret", "channel": "C000"})

    protocol.create_event(
        inbox_dir, source="slack", body="legacy",
        slack_channel="C000", slack_user="U?",
    )
    event = protocol.list_pending(inbox_dir)[0]
    protocol.set_status(event, "done")
    protocol.write_response(responses_dir, event["id"], "ok")

    posts: list[dict] = []
    monkeypatch.setattr(
        slack, "_slack_api",
        lambda t, m, p=None: (
            posts.append(dict(p or {})) if m == "chat.postMessage" else None,
            {"ok": True, "ts": "x"},
        )[1],
    )
    slack._deliver_responses(brr_dir, inbox_dir, responses_dir, "secret", "C000")

    assert posts == [{"channel": "C000", "text": "ok"}]
