"""Telegram gate — polls Bot API for messages, delivers responses.

Runs as a thread inside the daemon (or standalone).  Communicates
with brr exclusively through the filesystem:

- Incoming messages → ``.brr/inbox/`` event files
- Outgoing replies  ← ``.brr/responses/`` response files

Credentials and runtime state live in ``.brr/gates/telegram.json``.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

from .. import protocol

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


def _send_message(token: str, chat_id: int, text: str, topic_id: int | None = None) -> None:
    params: dict = {"chat_id": chat_id, "text": text}
    if topic_id:
        params["message_thread_id"] = topic_id
    _api_call(token, "sendMessage", params)


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
    print("[brr] Token saved")


def connect(brr_dir: Path) -> None:
    """Bind to a Telegram chat/topic."""
    state = _load_state(brr_dir)
    if "token" not in state:
        print("[brr] Run `brr auth telegram` first.")
        return
    chat_id = input("Chat ID (numeric): ").strip()
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
    try:
        _send_message(state["token"], state["chat_id"], "brr connected.", state.get("topic_id"))
        print("[brr] Test message sent.")
    except Exception as e:
        print(f"[brr] Failed: {e}")
        return
    _save_state(brr_dir, state)
    print("[brr] Connection saved")


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return "token" in state and "chat_id" in state


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
    chat_id = state["chat_id"]
    topic_id = state.get("topic_id")
    offset = state.get("offset", 0)

    updates = _api_call(token, "getUpdates", {
        "offset": offset,
        "timeout": _POLL_TIMEOUT,
        "allowed_updates": ["message"],
    }).get("result", [])

    for update in updates:
        offset = update["update_id"] + 1
        msg = update.get("message", {})
        if msg.get("chat", {}).get("id") != chat_id:
            continue
        if topic_id and msg.get("message_thread_id") != topic_id:
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

    _deliver_responses(brr_dir, inbox_dir, responses_dir, token, chat_id, topic_id)


def _deliver_responses(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
    token: str,
    chat_id: int,
    topic_id: int | None,
) -> None:
    for event in protocol.list_done(inbox_dir, "telegram"):
        eid = event["id"]
        body = protocol.read_response(responses_dir, eid)
        if body is None:
            continue
        try:
            _send_with_overflow(token, chat_id, topic_id, body)
        except Exception as e:
            print(f"[brr:telegram] delivery error for {eid}: {e}")
            continue
        resp_path = protocol.response_path(responses_dir, eid)
        protocol.cleanup(event["_path"], resp_path)
