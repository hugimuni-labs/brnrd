"""Pure Telegram transport rules shared by the local and hosted homes.

The module deliberately owns no HTTP client, filesystem protocol, daemon
state, or webhook framework.  Callers inject the one Bot API operation they
need, keeping ``brr``'s default dependency set independent of the hosted
backend's HTTP stack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


ApiCall = Callable[[str, dict[str, Any], float], dict]

_START_RE = re.compile(r"^/start(?:@\w+)?\s+(\S+)")
_BOT_TOKEN_IN_URL_RE = re.compile(r"(?<=/bot)\d+:[A-Za-z0-9_-]{30,}")


def redact_secrets(text: str) -> str:
    """Remove Telegram bot credentials while preserving the diagnosis."""
    return _BOT_TOKEN_IN_URL_RE.sub("<redacted>", text)


def sanitize_meta_str(value: str) -> str:
    """Flatten sender-controlled strings before they reach frontmatter."""
    return value.replace("\r", " ").replace("\n", " ")


@dataclass(frozen=True)
class ParsedMessage:
    chat_id: str
    text: str
    message_date: datetime | None
    message_id: int | None
    topic_id: int | None
    user: str
    user_id: int | None
    username: str
    sent_at: int | None = None
    is_edit: bool = False
    has_media: bool = False
    attachments: list[dict] = field(default_factory=list)
    # Telegram's own chat classification ("private", "group", "supergroup",
    # "channel"); "" when the update carried none. Load-bearing for the
    # room-membership grant (w-52): authorization may widen only when the
    # chat is verifiably a group, and this field is the verification.
    chat_type: str = ""


def _safe_filename(name: str, fallback: str) -> str:
    """Return a bounded basename safe to materialize as a local file."""
    cleaned = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if cleaned in ("", ".", ".."):
        cleaned = fallback
    return cleaned[:128]


def extract_attachments(msg: dict) -> list[dict]:
    """Return image pointers, preferring the largest photo over a document."""
    photo = msg.get("photo")
    if isinstance(photo, list) and photo:
        largest = photo[-1]
        if isinstance(largest, dict) and largest.get("file_id"):
            pointer: dict = {
                "file_id": str(largest["file_id"]),
                "filename": "photo.jpg",
                "kind": "photo",
            }
            if isinstance(largest.get("file_size"), int):
                pointer["file_size"] = largest["file_size"]
            return [pointer]
    document = msg.get("document")
    if isinstance(document, dict):
        mime = str(document.get("mime_type") or "")
        if mime.startswith("image/") and document.get("file_id"):
            pointer = {
                "file_id": str(document["file_id"]),
                "filename": _safe_filename(
                    str(document.get("file_name") or ""), "image"
                ),
                "kind": "document",
            }
            if isinstance(document.get("file_size"), int):
                pointer["file_size"] = document["file_size"]
            return [pointer]
    return []


def parse_update(payload: dict) -> ParsedMessage | None:
    """Normalize a Telegram message update without applying home policy."""
    is_edit = payload.get("message") is None and isinstance(
        payload.get("edited_message"), dict
    )
    msg = payload.get("message") or payload.get("edited_message")
    if not isinstance(msg, dict):
        return None
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip() or (msg.get("caption") or "").strip()
    has_media = any(
        msg.get(key)
        for key in (
            "photo", "document", "video", "video_note", "voice", "audio",
            "animation", "sticker",
        )
    )
    if chat_id is None or (not text and not has_media):
        return None

    raw_date = msg.get("date")
    try:
        sent_at = int(raw_date)
        message_date = datetime.fromtimestamp(sent_at, timezone.utc)
    except (TypeError, ValueError, OSError):
        sent_at = None
        message_date = None

    sender = msg.get("from") or {}
    user_id = sender.get("id")
    # #409: sender_chat is not a personal identity, even when Telegram also
    # supplies a generic service account in ``from``.
    if msg.get("sender_chat") is not None:
        user_id = None
    return ParsedMessage(
        chat_id=str(chat_id),
        chat_type=str((msg.get("chat") or {}).get("type") or ""),
        text=text,
        message_date=message_date,
        message_id=msg.get("message_id"),
        topic_id=msg.get("message_thread_id"),
        user=sanitize_meta_str(str(sender.get("first_name", "?"))),
        user_id=user_id,
        username=sanitize_meta_str(str(sender.get("username") or "")),
        sent_at=sent_at,
        is_edit=is_edit,
        has_media=has_media,
        attachments=extract_attachments(msg),
    )


def parse_migration(payload: dict) -> tuple[str, str] | None:
    """Return ``(old_chat_id, new_chat_id)`` for a migration service update."""
    msg = payload.get("message")
    if not isinstance(msg, dict):
        return None
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return None
    to_id = msg.get("migrate_to_chat_id")
    if to_id is not None:
        return str(chat_id), str(to_id)
    from_id = msg.get("migrate_from_chat_id")
    if from_id is not None:
        return str(from_id), str(chat_id)
    return None


def pair_code_from_text(text: str) -> str | None:
    """Return the code in a ``/start <code>`` command, or None."""
    match = _START_RE.match(text)
    return match.group(1) if match else None


@dataclass(frozen=True)
class MessagePolicy:
    """A home's explicit policy for splitting an outbound body."""

    limit: int | None
    max_chunks: int | None = None
    truncation_marker: str = "\n\n[truncated]"


def split_message(
    text: str,
    limit: int = 4000,
    *,
    max_chunks: int | None = 12,
    truncation_marker: str = "\n\n[truncated]",
) -> list[str]:
    """Split text at line boundaries and optionally cap the fan-out."""
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
    if max_chunks is not None and len(parts) > max_chunks:
        parts = parts[:max_chunks]
        marker = truncation_marker
        parts[-1] = parts[-1][: limit - len(marker)].rstrip() + marker
    return parts or [""]


def send_message(
    call: ApiCall,
    chat_id: str | int,
    text: str,
    *,
    policy: MessagePolicy,
    topic_id: int | None = None,
    reply_to_message_id: int | None = None,
    parse_mode: str | None = None,
    reply_markup: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> list[dict]:
    """Send a body using the caller's HTTP adapter and explicit policy."""
    parts = (
        split_message(
            text,
            policy.limit,
            max_chunks=policy.max_chunks,
            truncation_marker=policy.truncation_marker,
        )
        if policy.limit is not None
        else [text]
    )
    results: list[dict] = []
    for index, part in enumerate(parts):
        params: dict[str, Any] = {"chat_id": chat_id, "text": part or " "}
        if topic_id:
            params["message_thread_id"] = topic_id
        if parse_mode:
            params["parse_mode"] = parse_mode
        if index == 0 and reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id
            params["allow_sending_without_reply"] = True
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        results.append(call("sendMessage", params, timeout))
    return results


def edit_message(
    call: ApiCall,
    chat_id: str | int,
    message_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict:
    """Edit one Telegram message through the caller's HTTP adapter."""
    params: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text or " ",
    }
    if parse_mode:
        params["parse_mode"] = parse_mode
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return call("editMessageText", params, timeout)
