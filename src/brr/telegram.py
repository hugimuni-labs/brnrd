"""Telegram connector — Bot API via stdlib urllib."""

from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

_API = "https://api.telegram.org/bot{token}/{method}"


def _local_dir() -> Path:
    d = Path.cwd() / ".brr.local"
    d.mkdir(exist_ok=True)
    return d


def _load_creds() -> dict:
    path = _local_dir() / "telegram.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_creds(data: dict) -> None:
    path = _local_dir() / "telegram.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def api_call(token: str, method: str, params: dict | None = None) -> dict:
    """Call a Telegram Bot API method. Returns the parsed response."""
    url = _API.format(token=token, method=method)
    body = json.dumps(params or {}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram API error: {e.code} {e.read().decode()}")


def send_message(token: str, chat_id: int, text: str, topic_id: int | None = None) -> dict:
    """Send a text message to a chat, optionally in a topic."""
    params: dict = {"chat_id": chat_id, "text": text}
    if topic_id:
        params["message_thread_id"] = topic_id
    return api_call(token, "sendMessage", params)


def get_updates(token: str, offset: int = 0, timeout: int = 30) -> list[dict]:
    """Long-poll for new messages. Returns list of updates."""
    result = api_call(token, "getUpdates", {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": ["message"],
    })
    return result.get("result", [])


def auth() -> None:
    """Interactive auth: prompt for bot token, validate, save."""
    creds = _load_creds()
    token = input("Telegram bot token (from @BotFather): ").strip()
    if not token:
        print("[brr] No token provided.")
        return

    # Validate
    try:
        resp = api_call(token, "getMe")
        bot = resp.get("result", {})
        print(f"[brr] Authenticated as @{bot.get('username', '?')}")
    except RuntimeError as e:
        print(f"[brr] Authentication failed: {e}")
        return

    creds["token"] = token
    _save_creds(creds)
    print("[brr] Token saved to .brr.local/telegram.json")


def connect() -> None:
    """Bind current repo to a Telegram chat/topic."""
    creds = _load_creds()
    if "token" not in creds:
        print("[brr] Run `brr auth telegram` first.")
        return

    chat_id = input("Chat ID (numeric, use @RawDataBot to find it): ").strip()
    if not chat_id:
        print("[brr] No chat ID provided.")
        return

    try:
        creds["chat_id"] = int(chat_id)
    except ValueError:
        print("[brr] Chat ID must be a number.")
        return

    topic_id = input("Topic/thread ID (leave empty for no topic): ").strip()
    if topic_id:
        try:
            creds["topic_id"] = int(topic_id)
        except ValueError:
            print("[brr] Topic ID must be a number.")
            return

    # Test by sending a message
    try:
        send_message(creds["token"], creds["chat_id"], "brr connected.", creds.get("topic_id"))
        print("[brr] Test message sent.")
    except RuntimeError as e:
        print(f"[brr] Failed to send test message: {e}")
        return

    _save_creds(creds)
    print("[brr] Connection saved to .brr.local/telegram.json")
