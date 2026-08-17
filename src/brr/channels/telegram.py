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
    # #1389 — Telegram's own album marker: every message in one
    # multi-photo send shares this id. `None` for an ordinary message (most
    # of them); a caller that wants to coalesce an album into one event
    # reads this field, nothing heuristic.
    media_group_id: str | None = None


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
        media_group_id=(
            str(msg["media_group_id"]) if msg.get("media_group_id") is not None else None
        ),
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


#: The marker every "this had to be cut" path stamps on the last thing it
#: kept, so a reader can tell content loss from a message that just ends.
#: One literal, reused by :func:`split_message`'s ``max_chunks`` overflow and
#: by ``gates/delivery.py``'s single-message truncate-or-gist policy, so the
#: two surfaces don't drift to two different words for the same event.
TRUNCATION_MARKER = "\n\n[truncated]"


def utf16_len(text: str) -> int:
    """Length of *text* in UTF-16 code units.

    Telegram's Bot API documents its character limits (message text,
    captions, ``MessageEntity.offset``) in UTF-16 code units, not Unicode
    code points. Python's ``len()`` agrees with that count for every
    character in the Basic Multilingual Plane but undercounts by one for
    each character outside it — most emoji included: an astral code point
    is two UTF-16 units but one Python ``str`` index. A split built on
    ``len()`` alone can hand Telegram a chunk that measures under the
    budget in Python and over it on the wire, and the send 400s instead
    of landing.
    """
    if text.isascii():
        return len(text)
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def _utf16_prefix_end(text: str, limit: int) -> int:
    """Largest ``i`` such that ``utf16_len(text[:i]) <= limit``."""
    if utf16_len(text) <= limit:
        return len(text)
    units = 0
    for i, ch in enumerate(text):
        units += 2 if ord(ch) > 0xFFFF else 1
        if units > limit:
            return i
    return len(text)


def trim_to_limit(text: str, limit: int) -> str:
    """The longest prefix of *text* within *limit* UTF-16 units.

    Prefers to end at a line break, then a space, over a mid-word cut.
    Shared by :func:`split_message`'s single-line fallback and by the
    daemon's gist-or-truncate overflow policy (``gates/delivery.py``'s
    ``resolve_overflow``) — one boundary rule for every "this has to fit
    in one message" path, rather than each caller cutting blind at an
    index and calling it done.
    """
    cut = _utf16_prefix_end(text, limit)
    if cut >= len(text):
        return text
    nl = text.rfind("\n", 0, cut)
    if nl > 0:
        return text[:nl]
    sp = text.rfind(" ", 0, cut)
    if sp > 0:
        return text[:sp]
    return text[:cut]


_FENCE_MARK = "```"


def _open_fence(text: str) -> str | None:
    """The opening delimiter line if *text* ends inside an unclosed fence.

    Toggles on every line that starts, after stripping leading
    whitespace, with a triple backtick: the first such line in a run is
    an opener, the next a closer, and so on. Returns the *exact* opening
    line (backticks plus any language tag, e.g. ` ```python `) so a
    reopened fence keeps its syntax highlighting; ``None`` when the text
    is fence-balanced.
    """
    open_line: str | None = None
    for line in text.split("\n"):
        if line.lstrip().startswith(_FENCE_MARK):
            open_line = None if open_line is not None else line.lstrip()
    return open_line


@dataclass(frozen=True)
class MessagePolicy:
    """A home's explicit policy for splitting an outbound body."""

    limit: int | None
    max_chunks: int | None = None
    truncation_marker: str = TRUNCATION_MARKER


def split_message(
    text: str,
    limit: int = 4000,
    *,
    max_chunks: int | None = 12,
    truncation_marker: str = TRUNCATION_MARKER,
) -> list[str]:
    """Split text into platform-sized chunks, never mid-word, fence-safe.

    A cut prefers the last line boundary within *limit* UTF-16 units
    (Telegram's own accounting — see :func:`utf16_len`); a single line
    longer than the limit falls back to :func:`trim_to_limit`'s
    word-boundary rule rather than an index cut. A markdown code fence
    left open at a cut is closed at the end of its chunk and reopened
    (same language tag) at the start of the next, so every chunk renders
    as valid markdown standing alone instead of leaking an unclosed
    fence into every following message.
    """
    close_reserve = len(_FENCE_MARK) + 1  # "\n```"
    parts: list[str] = []
    remaining = text
    reopen: str | None = None
    while remaining:
        prefix = f"{reopen}\n" if reopen else ""
        budget = max(1, limit - utf16_len(prefix))
        if utf16_len(remaining) <= budget:
            chunk = remaining
            rest_after = ""
        else:
            cut = _utf16_prefix_end(remaining, budget)
            nl = remaining.rfind("\n", 0, cut)
            chunk = remaining[:nl] if nl > 0 else trim_to_limit(remaining, budget)
            # A fence left open only matters when more text is still
            # coming — reserving close-marker room on every cut (even the
            # common case with no fence at all) would waste it for
            # nothing, so it's spent only where `_open_fence` says it's
            # actually owed.
            if chunk and _open_fence(prefix + chunk) and remaining[len(chunk):].lstrip("\n"):
                shrink_budget = max(1, budget - close_reserve)
                if utf16_len(chunk) > shrink_budget:
                    cut2 = _utf16_prefix_end(chunk, shrink_budget)
                    nl2 = chunk.rfind("\n", 0, cut2)
                    shrunk = chunk[:nl2] if nl2 > 0 else trim_to_limit(chunk, shrink_budget)
                    if shrunk:
                        chunk = shrunk
            if not chunk:
                # Budget too small even for one boundary-respecting
                # character — a pathological *limit*, or a reopened fence
                # line that alone ate nearly all of it. Advance by exactly
                # one character rather than spin forever re-cutting an
                # empty chunk.
                chunk = remaining[:1]
            rest_after = remaining[len(chunk):].lstrip("\n")
        body = prefix + chunk
        still_open = _open_fence(body)
        if still_open and rest_after:
            body += "\n" + _FENCE_MARK
            reopen = still_open
        else:
            reopen = None
        parts.append(body)
        remaining = rest_after
    if max_chunks is not None and len(parts) > max_chunks:
        parts = parts[:max_chunks]
        marker = truncation_marker
        parts[-1] = trim_to_limit(parts[-1], limit - utf16_len(marker)) + marker
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
