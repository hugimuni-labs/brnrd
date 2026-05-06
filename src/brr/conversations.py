"""Conversation log — append-only history per gate thread.

A conversation is the history of one gate thread (Telegram chat,
Slack thread, git source file). It has no manifest, no title, no
intent — those were the leaky stream identity fields removed in the
2026-05-05 streams-to-conversations refactor (see
``kb/decision-drop-streams.md``).

Runtime layout::

    .brr/conversations/
        <safe-key>.ndjson      — append-only records for one gate thread

A single ndjson per conversation. Each record carries a ``kind``
discriminator (``event``, ``task``, ``artifact``, ``update``) plus
type-specific fields. Records always include ``ts``. Reading the tail
gives the agent recent activity for context; reading and filtering by
``task_id`` projects a single task's lifecycle.

Conversations are runtime state. Durable knowledge still belongs in
``kb/`` — agents that want to track an ongoing line of work write a
kb page rather than asking brr for a typed identity field.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any


# ── Time helpers ─────────────────────────────────────────────────────


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── Gate thread → conversation key ───────────────────────────────────


def gate_thread_key(meta: dict[str, Any]) -> str | None:
    """Return a stable conversation key for the gate thread, or None.

    The key threads repeat events from the same conversational source
    (Telegram chat+topic, Slack channel+thread, git source file) onto
    the same conversation log. Returns None when an event carries no
    gate context that can serve as a stable thread anchor.
    """
    source = (meta.get("source") or "").strip()
    if source == "telegram":
        chat = meta.get("telegram_chat_id")
        topic = meta.get("telegram_topic_id") or ""
        if chat:
            return f"telegram:{chat}:{topic}"
        return None
    if source == "slack":
        channel = meta.get("slack_channel") or ""
        thread = meta.get("slack_thread_ts") or meta.get("slack_ts") or ""
        if channel:
            return f"slack:{channel}:{thread}"
        return None
    if source == "git":
        f = meta.get("git_file") or ""
        if f:
            return f"git:{f}"
        return None
    if source:
        return f"{source}:default"
    return None


def conversation_key_for_event(event: dict[str, Any]) -> str | None:
    """Resolve an event to its conversation key, if it has one.

    Order:

    1. Explicit ``conversation_key`` carried on the event.
    2. Gate-thread fingerprint (Telegram chat, Slack thread, git file).
    """
    explicit = event.get("conversation_key")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return gate_thread_key(event)


# ── Filesystem layout ────────────────────────────────────────────────


_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_filename(key: str) -> str:
    """Filesystem-safe filename for a conversation key.

    Each ``:`` becomes ``__`` so the original key can be recovered by
    swapping back. Other unsafe characters are collapsed to ``_``.
    """
    rendered = key.replace(":", "__")
    rendered = _SAFE_RE.sub("_", rendered)
    return f"{rendered}.ndjson"


def key_from_filename(filename: str) -> str:
    """Inverse of :func:`safe_filename` for the colon encoding."""
    stem = filename
    if stem.endswith(".ndjson"):
        stem = stem[: -len(".ndjson")]
    return stem.replace("__", ":")


def conversations_root(brr_dir: Path) -> Path:
    return brr_dir / "conversations"


def conversation_path(brr_dir: Path, key: str) -> Path:
    return conversations_root(brr_dir) / safe_filename(key)


# ── Append + read ────────────────────────────────────────────────────


def append_record(brr_dir: Path, key: str, record: dict[str, Any]) -> None:
    """Append a record to the conversation log; stamp ``ts`` if missing."""
    path = conversation_path(brr_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if "ts" not in record:
        record = {"ts": _now_iso(), **record}
    line = json.dumps(record, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def read_records(brr_dir: Path, key: str) -> list[dict[str, Any]]:
    """Return all records from the conversation log (oldest first)."""
    path = conversation_path(brr_dir, key)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def read_recent(
    brr_dir: Path, key: str, limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the last *limit* records from the conversation log."""
    records = read_records(brr_dir, key)
    if limit <= 0 or len(records) <= limit:
        return records
    return records[-limit:]


# ── Specialised appenders ────────────────────────────────────────────


def append_event(brr_dir: Path, key: str, event: dict[str, Any]) -> None:
    """Record an event arrival on the conversation log."""
    body = (event.get("body") or "").strip()
    summary = body.splitlines()[0] if body else ""
    record = {
        "kind": "event",
        "event_id": event.get("id", ""),
        "source": event.get("source", ""),
        "summary": summary,
    }
    append_record(brr_dir, key, record)


def append_task(
    brr_dir: Path,
    key: str,
    *,
    task_id: str,
    event_id: str,
    env: str,
    status: str,
    base_branch: str | None = None,
    branch_name: str | None = None,
) -> None:
    """Record a task lifecycle row on the conversation log."""
    record = {
        "kind": "task",
        "task_id": task_id,
        "event_id": event_id,
        "branch_name": branch_name,
        "base_branch": base_branch,
        "env": env,
        "status": status,
    }
    append_record(brr_dir, key, record)


def append_artifact(
    brr_dir: Path,
    key: str,
    *,
    kind: str,
    path: str,
    task_id: str | None = None,
    label: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Record an artifact creation on the conversation log."""
    record: dict[str, Any] = {
        "kind": "artifact",
        "artifact_kind": kind,
        "path": path,
    }
    if task_id:
        record["task_id"] = task_id
    if label:
        record["label"] = label
    if extra:
        record.update(extra)
    append_record(brr_dir, key, record)


def append_update(
    brr_dir: Path,
    key: str,
    *,
    type: str,
    payload: dict[str, Any],
) -> None:
    """Record a lifecycle update packet on the conversation log."""
    record = {
        "kind": "update",
        "type": type,
        **payload,
    }
    append_record(brr_dir, key, record)


# ── Listing ──────────────────────────────────────────────────────────


def list_conversations(brr_dir: Path) -> list[str]:
    """Return known conversation keys (decoded), sorted alphabetically."""
    root = conversations_root(brr_dir)
    if not root.exists():
        return []
    keys: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.suffix != ".ndjson":
            continue
        keys.append(key_from_filename(entry.name))
    return keys


# ── Convenience ──────────────────────────────────────────────────────


def records_for_task(
    brr_dir: Path, key: str, task_id: str,
) -> list[dict[str, Any]]:
    """Return all records mentioning *task_id* in this conversation."""
    return [
        record for record in read_records(brr_dir, key)
        if record.get("task_id") == task_id
    ]
