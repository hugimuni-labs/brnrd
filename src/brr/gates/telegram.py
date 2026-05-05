"""Telegram gate — polls Bot API for messages, delivers responses.

Runs as a thread inside the daemon (or standalone).  Communicates
with brr exclusively through the filesystem:

- Incoming messages → ``.brr/inbox/`` event files
- Outgoing replies  ← ``.brr/responses/`` response files

Credentials and runtime state live in ``.brr/gates/telegram.json``.
Telegram only requires a bot token; chat IDs are discovered from
incoming messages and stored on each event.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from .. import protocol, run_progress
from ..task import Task

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_TG_LEN = 3900
_POLL_TIMEOUT = 30
_BACKOFF_MAX = 120


# ── Bot API helpers ──────────────────────────────────────────────────


def _api_call(token: str, method: str, params: dict | None = None) -> dict:
    url = _API.format(token=token, method=method)
    body = json.dumps(params or {}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())


def _send_message(token: str, chat_id: int, text: str, topic_id: int | None = None) -> dict:
    params: dict = {"chat_id": chat_id, "text": text}
    if topic_id:
        params["message_thread_id"] = topic_id
    return _api_call(token, "sendMessage", params)


def _edit_message(
    token: str,
    chat_id: int,
    message_id: int,
    text: str,
) -> dict:
    return _api_call(token, "editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    })


def _post_gist(content: str, filename: str = "result.md") -> str | None:
    try:
        result = subprocess.run(
            ["gh", "gist", "create", "--public", "-f", filename, "-"],
            input=content, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _send_with_overflow(token: str, chat_id: int, topic_id: int | None, text: str) -> None:
    if len(text) <= _MAX_TG_LEN:
        _send_message(token, chat_id, text, topic_id)
        return
    url = _post_gist(text)
    if url:
        _send_message(token, chat_id, f"Result: {url}", topic_id)
    else:
        _send_message(token, chat_id, text[:_MAX_TG_LEN] + "\n\n[truncated]", topic_id)


# ── State ────────────────────────────────────────────────────────────


def _state_path(brr_dir: Path) -> Path:
    return brr_dir / "gates" / "telegram.json"


def _load_state(brr_dir: Path) -> dict:
    path = _state_path(brr_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_state(brr_dir: Path, state: dict) -> None:
    path = _state_path(brr_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _progress_state_path(brr_dir: Path) -> Path:
    return brr_dir / "gates" / "telegram_progress.json"


def _load_progress_state(brr_dir: Path) -> dict:
    path = _progress_state_path(brr_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_progress_state(brr_dir: Path, state: dict) -> None:
    path = _progress_state_path(brr_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _progress_key(task_id: str) -> str:
    return task_id


# ── Interactive setup ────────────────────────────────────────────────


def auth(brr_dir: Path) -> None:
    """Prompt for bot token, validate, save."""
    state = _load_state(brr_dir)
    token = input("Telegram bot token (from @BotFather): ").strip()
    if not token:
        print("[brr] No token provided.")
        return
    try:
        resp = _api_call(token, "getMe")
        bot = resp.get("result", {})
        print(f"[brr] Authenticated as @{bot.get('username', '?')}")
    except Exception as e:
        print(f"[brr] Authentication failed: {e}")
        return
    state["token"] = token
    _save_state(brr_dir, state)
    print("[brr] Token saved. Start the daemon, then send the bot a message.")


def bind(brr_dir: Path) -> None:
    """Optionally restrict Telegram to a single chat/topic."""
    state = _load_state(brr_dir)
    if "token" not in state:
        print("[brr] Run `brr auth telegram` first.")
        return
    print("[brr] Telegram works with just `brr auth telegram`.")
    chat_id = input(
        "Optional chat ID to restrict to (leave empty to accept all): "
    ).strip()
    if not chat_id:
        state.pop("chat_id", None)
        state.pop("topic_id", None)
        _save_state(brr_dir, state)
        print("[brr] Telegram will accept messages from any chat.")
        return
    try:
        state["chat_id"] = int(chat_id)
    except ValueError:
        print("[brr] Chat ID must be a number.")
        return
    topic_id = input("Topic/thread ID (leave empty for none): ").strip()
    if topic_id:
        try:
            state["topic_id"] = int(topic_id)
        except ValueError:
            print("[brr] Topic ID must be a number.")
            return
    else:
        state.pop("topic_id", None)
    try:
        _send_message(state["token"], state["chat_id"], "brr bound.", state.get("topic_id"))
        print("[brr] Test message sent.")
    except Exception as e:
        print(f"[brr] Failed: {e}")
        return
    _save_state(brr_dir, state)
    print("[brr] Binding saved")


def setup(brr_dir: Path) -> None:
    """Configure Telegram credentials and optional chat/topic restriction."""
    auth(brr_dir)
    if "token" in _load_state(brr_dir):
        bind(brr_dir)


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return "token" in state


# ── Gate loop ────────────────────────────────────────────────────────


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    """Main gate loop — poll messages, create events, deliver responses.

    Designed to run in a daemon thread. Crashes are caught and retried
    with exponential backoff.
    """
    backoff = 1
    while True:
        try:
            _loop_once(brr_dir, inbox_dir, responses_dir)
            backoff = 1
        except Exception as e:
            print(f"[brr:telegram] error: {e}, retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    token = state["token"]
    configured_chat_id = state.get("chat_id")
    configured_topic_id = state.get("topic_id")
    offset = state.get("offset", 0)

    updates = _api_call(token, "getUpdates", {
        "offset": offset,
        "timeout": _POLL_TIMEOUT,
        "allowed_updates": ["message"],
    }).get("result", [])

    for update in updates:
        offset = update["update_id"] + 1
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        if chat_id is None:
            continue
        if configured_chat_id is not None and chat_id != configured_chat_id:
            continue
        topic_id = msg.get("message_thread_id")
        if configured_topic_id and topic_id != configured_topic_id:
            continue
        text = msg.get("text", "").strip()
        if not text:
            continue

        user = msg.get("from", {}).get("first_name", "?")
        protocol.create_event(
            inbox_dir,
            source="telegram",
            body=text,
            telegram_chat_id=chat_id,
            telegram_topic_id=topic_id or "",
            telegram_user=user,
        )

    state["offset"] = offset
    _save_state(brr_dir, state)

    _deliver_responses(
        brr_dir, inbox_dir, responses_dir, token,
        configured_chat_id, configured_topic_id,
    )


def _deliver_responses(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
    token: str,
    default_chat_id: int | None = None,
    default_topic_id: int | None = None,
) -> None:
    for event in protocol.list_done(inbox_dir, "telegram"):
        eid = event["id"]
        body = protocol.read_response(responses_dir, eid)
        if body is None:
            continue
        chat_id = _event_int(event, "telegram_chat_id", default_chat_id)
        if chat_id is None:
            print(f"[brr:telegram] delivery error for {eid}: missing chat id")
            continue
        topic_id = _event_int(event, "telegram_topic_id", default_topic_id)
        try:
            _send_with_overflow(token, chat_id, topic_id, body)
        except Exception as e:
            print(f"[brr:telegram] delivery error for {eid}: {e}")
            continue
        resp_path = protocol.response_path(responses_dir, eid)
        protocol.cleanup(event["_path"], resp_path)


def _event_int(event: dict, key: str, default: int | None = None) -> int | None:
    if key not in event:
        return default
    return _coerce_optional_int(event.get(key))


def _coerce_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Live progress card ──────────────────────────────────────────────


_RENDERABLE_PACKETS = {
    "task_created",
    "triage_done",
    "env_prepared",
    "container_started",
    "container_preserved",
    "run_started",
    "attempt_started",
    "attempt_failed",
    "retrying",
    "artifact_created",
    "finalizing",
    "push_started",
    "push_done",
    "needs_context",
    "done",
    "failed",
    "conflict",
}


def render_update(brr_dir: Path, packet: Any) -> None:
    """Send/edit a Telegram progress card for *packet*.

    On ``task_created`` we send a fresh message in the originating chat
    or topic and store the resulting ``message_id`` so later packets can
    edit the same message via ``editMessageText``. Failures are swallowed
    — the daemon must keep running even if Telegram is misconfigured.
    """
    ptype = getattr(packet, "type", None)
    if ptype not in _RENDERABLE_PACKETS:
        return

    state = _load_state(brr_dir)
    token = state.get("token")
    if not token:
        return

    conv_key = getattr(packet, "conversation_key", "") or ""
    task_id = run_progress.task_id_from_packet(packet)
    if not conv_key or not task_id:
        return

    task = Task.from_file(brr_dir / "tasks" / f"{task_id}.md")
    if task is None or task.source != "telegram":
        return
    chat_id = _coerce_optional_int(task.meta.get("telegram_chat_id"))
    if chat_id is None:
        return
    topic_id = _coerce_optional_int(task.meta.get("telegram_topic_id"))

    view = run_progress.project_task(brr_dir, conv_key, task_id)
    if view is None:
        return
    text = run_progress.render_text(view, compact=True)

    progress_state = _load_progress_state(brr_dir)
    key = _progress_key(task_id)
    entry = progress_state.get(key)

    try:
        if entry and entry.get("message_id"):
            try:
                _edit_message(token, chat_id, int(entry["message_id"]), text)
                entry["last_render"] = ptype
                progress_state[key] = entry
                _save_progress_state(brr_dir, progress_state)
                return
            except Exception:
                # Fall through to send a replacement message.
                pass
        resp = _send_message(token, chat_id, text, topic_id)
        message_id = (resp.get("result") or {}).get("message_id")
        if message_id is None:
            return
        progress_state[key] = {
            "chat_id": chat_id,
            "topic_id": topic_id,
            "message_id": message_id,
            "last_render": ptype,
        }
        _save_progress_state(brr_dir, progress_state)
    except Exception:
        return
