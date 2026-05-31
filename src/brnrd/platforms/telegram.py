"""Telegram Bot API client — parse webhook updates, send replies.

A single managed bot serves every account, multiplexed by chat_id.
``parse_update`` normalizes an inbound webhook payload; ``send_message``
posts a reply (used both for pairing confirmations and for forwarding
runner responses). Tests monkeypatch ``send_message``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

_API = "https://api.telegram.org/bot{token}/{method}"
_START_RE = re.compile(r"^/start(?:@\w+)?\s+(\S+)")


@dataclass
class ParsedMessage:
    chat_id: str
    text: str
    message_id: int | None
    topic_id: int | None
    user: str


def parse_update(payload: dict) -> ParsedMessage | None:
    """Normalize a Telegram update into a message, or None if it isn't
    a text message we can act on."""
    msg = payload.get("message") or payload.get("edited_message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    if chat_id is None or not text:
        return None
    return ParsedMessage(
        chat_id=str(chat_id),
        text=text,
        message_id=msg.get("message_id"),
        topic_id=msg.get("message_thread_id"),
        user=(msg.get("from") or {}).get("first_name", "?"),
    )


def pair_code_from_text(text: str) -> str | None:
    """Return the code in a ``/start <code>`` command, or None."""
    m = _START_RE.match(text)
    return m.group(1) if m else None


def send_message(
    token: str,
    chat_id: str | int,
    text: str,
    *,
    topic_id: int | None = None,
    reply_to_message_id: int | None = None,
    timeout: float = 30.0,
) -> None:
    params: dict = {"chat_id": chat_id, "text": text}
    if topic_id:
        params["message_thread_id"] = topic_id
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True
    resp = httpx.post(
        _API.format(token=token, method="sendMessage"), json=params, timeout=timeout
    )
    resp.raise_for_status()
