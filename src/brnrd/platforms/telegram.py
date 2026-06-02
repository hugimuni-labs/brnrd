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

# Telegram rejects messages over 4096 chars with HTTP 400; stay under
# it with margin and split long bodies across several messages rather
# than letting the send fail (the daemon would otherwise retry forever).
_MAX_LEN = 4000
_MAX_CHUNKS = 12


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


def split_message(text: str, limit: int = _MAX_LEN) -> list[str]:
    """Split *text* into Telegram-sized parts, preferring line breaks.

    Bodies past ``_MAX_CHUNKS`` parts are truncated with a marker so a
    pathological response can't fan out into dozens of messages.
    """
    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if len(parts) > _MAX_CHUNKS:
        parts = parts[:_MAX_CHUNKS]
        parts[-1] = parts[-1][: limit - 16].rstrip() + "\n\n[truncated]"
    return parts or [""]


def send_message(
    token: str,
    chat_id: str | int,
    text: str,
    *,
    topic_id: int | None = None,
    reply_to_message_id: int | None = None,
    timeout: float = 30.0,
) -> None:
    # Reply threading only on the first part; the rest follow it.
    for i, part in enumerate(split_message(text)):
        params: dict = {"chat_id": chat_id, "text": part or " "}
        if topic_id:
            params["message_thread_id"] = topic_id
        if i == 0 and reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id
            params["allow_sending_without_reply"] = True
        resp = httpx.post(
            _API.format(token=token, method="sendMessage"),
            json=params,
            timeout=timeout,
        )
        resp.raise_for_status()


class CardGone(RuntimeError):
    """A progress card can't be edited (deleted/expired) — resend.

    Distinct so the card relay can answer 409 and let the daemon's card
    driver fall back to a fresh send instead of treating it as a hard
    failure.
    """


def send_card(
    token: str,
    chat_id: str | int,
    text: str,
    *,
    topic_id: int | None = None,
    reply_to_message_id: int | None = None,
    timeout: float = 30.0,
) -> int | None:
    """Send a single progress-card message (HTML), return its message id.

    A card is small, so unlike ``send_message`` this never splits — and
    it returns the platform ``message_id`` so the daemon's shared card
    driver can edit the same message in place on later packets. The card
    text arrives already HTML-formatted by the daemon.
    """
    params: dict = {"chat_id": chat_id, "text": text or " ", "parse_mode": "HTML"}
    if topic_id:
        params["message_thread_id"] = topic_id
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True
    resp = httpx.post(
        _API.format(token=token, method="sendMessage"), json=params, timeout=timeout
    )
    resp.raise_for_status()
    return ((resp.json() or {}).get("result") or {}).get("message_id")


def edit_card(
    token: str,
    chat_id: str | int,
    message_id: int,
    text: str,
    *,
    timeout: float = 30.0,
) -> None:
    """Edit a progress card in place (HTML).

    A Telegram "message is not modified" reply is a benign no-op
    (success); any other 400 means the message is gone, surfaced as
    ``CardGone`` so the relay can ask the daemon to send a fresh card.
    """
    params: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text or " ",
        "parse_mode": "HTML",
    }
    resp = httpx.post(
        _API.format(token=token, method="editMessageText"), json=params, timeout=timeout
    )
    if resp.status_code == 400:
        try:
            desc = str((resp.json() or {}).get("description", ""))
        except ValueError:
            desc = resp.text
        if "not modified" in desc.lower():
            return
        raise CardGone(desc or "card not editable")
    resp.raise_for_status()
