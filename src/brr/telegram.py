"""Telegram connector — transport adapter for the brr daemon.

Implements the ``Connector`` protocol from ``daemon.py`` using the
Telegram Bot API (via stdlib urllib, zero deps).  Also provides
interactive setup (auth/connect) and gist overflow for long output.

To add another connector (Discord, Slack, etc.), implement the same
``Connector`` protocol and pass it to ``daemon.start()``.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

from .daemon import Message

_API = "https://api.telegram.org/bot{token}/{method}"


# ── Credentials ──────────────────────────────────────────────────────

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


# ── Bot API ──────────────────────────────────────────────────────────

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


# ── Interactive setup ────────────────────────────────────────────────

def auth() -> None:
    """Interactive auth: prompt for bot token, validate, save."""
    creds = _load_creds()
    token = input("Telegram bot token (from @BotFather): ").strip()
    if not token:
        print("[brr] No token provided.")
        return

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

    try:
        send_message(creds["token"], creds["chat_id"], "brr connected.", creds.get("topic_id"))
        print("[brr] Test message sent.")
    except RuntimeError as e:
        print(f"[brr] Failed to send test message: {e}")
        return

    _save_creds(creds)
    print("[brr] Connection saved to .brr.local/telegram.json")


# ── Gist overflow ────────────────────────────────────────────────────

_MAX_TG_LEN = 3900


def _post_gist(content: str, filename: str = "result.md") -> str | None:
    """Post content to a GitHub gist via ``gh``. Returns URL or None."""
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
    """Send a reply, posting overflow to a gist when too long for Telegram."""
    if len(text) <= _MAX_TG_LEN:
        send_message(token, chat_id, text, topic_id)
        return
    url = _post_gist(text)
    if url:
        send_message(token, chat_id, f"Result: {url}", topic_id)
    else:
        send_message(token, chat_id, text[:_MAX_TG_LEN] + "\n\n[truncated]", topic_id)


# ── Connector ────────────────────────────────────────────────────────

class TelegramConnector:
    """Implements the daemon Connector protocol over Telegram Bot API."""

    def __init__(self, token: str, chat_id: int, topic_id: int | None = None) -> None:
        self._token = token
        self._chat_id = chat_id
        self._topic_id = topic_id
        self._offset = 0

    def poll(self, timeout: int) -> list[Message]:
        updates = get_updates(self._token, offset=self._offset, timeout=timeout)
        messages: list[Message] = []
        for update in updates:
            self._offset = update["update_id"] + 1
            msg = update.get("message", {})

            if msg.get("chat", {}).get("id") != self._chat_id:
                continue
            if self._topic_id and msg.get("message_thread_id") != self._topic_id:
                continue

            text = msg.get("text", "").strip()
            if not text:
                continue

            user = msg.get("from", {}).get("first_name", "?")
            messages.append(Message(text=text, user=user))
        return messages

    def reply(self, text: str) -> None:
        try:
            _send_with_overflow(self._token, self._chat_id, self._topic_id, text)
        except RuntimeError as e:
            print(f"[brr] telegram reply error: {e}")

    def describe(self) -> str:
        desc = f"telegram chat {self._chat_id}"
        if self._topic_id:
            desc += f" topic {self._topic_id}"
        return desc


def make_connector() -> TelegramConnector | None:
    """Build a TelegramConnector from saved credentials, or None."""
    creds = _load_creds()
    if "token" not in creds or "chat_id" not in creds:
        print("[brr] Telegram not configured.  Run `brr auth telegram` and `brr connect telegram` first.")
        return None
    return TelegramConnector(
        token=creds["token"],
        chat_id=creds["chat_id"],
        topic_id=creds.get("topic_id"),
    )
