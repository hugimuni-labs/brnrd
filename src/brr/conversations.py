"""Conversation log — per-event-pipeline append-only history.

A conversation is the history of one gate thread (Telegram chat, Slack
thread, git source file). It has no manifest, no title, no intent —
those were the leaky stream identity fields removed in the 2026-05-05
streams-to-conversations refactor (see ``kb/decision-drop-streams.md``).

Runtime layout::

    .brr/conversations/
        <safe-key>/
            <event-id>.jsonl    — append-only records for one pipeline run

Each ``<event-id>.jsonl`` file has exactly one writer for its lifetime:
the worker thread handling that one event/task pipeline. This per-
event-pipeline partitioning is what makes the concurrent worker pool
contention-free without per-shared-file locks — see
``kb/design-concurrent-execution.md``.

Each record carries ``ts`` (microsecond-precision UTC ISO 8601) plus a
``kind`` discriminator (``event``, ``task``, ``artifact``, ``update``)
plus type-specific fields. Reading projects one task's lifecycle by
opening just its ``<event-id>.jsonl``; reading the full conversation
context glob+merges every file in the directory, sorted by ``ts``.

Single-line ``O_APPEND`` writes in binary mode rely on the kernel's
guarantee that the offset advance and the write happen atomically
together — defence in depth, since the per-event-file partitioning
already gives each file exactly one writer.

Conversations are runtime state. Durable knowledge still belongs in
``kb/`` — agents that want to track an ongoing line of work write a
kb page rather than asking brr for a typed identity field.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


# ── Time helpers ─────────────────────────────────────────────────────


def _now_iso() -> str:
    """Microsecond-precision UTC ISO 8601 timestamp.

    Second-precision was fine when only one worker wrote to the
    conversation log at a time; concurrent writers from the same chat
    can land records in the same second and the projection sorts by
    ``ts`` across files, so we need finer granularity to keep ordering
    stable.
    """
    now = time.time()
    base = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
    micros = int((now - int(now)) * 1_000_000)
    return f"{base}.{micros:06d}Z"


# ── Gate thread → conversation key ───────────────────────────────────


def gate_thread_key(meta: dict[str, Any]) -> str | None:
    """Return a stable conversation key for the gate thread, or None.

    The key threads repeat events from the same conversational source
    (Telegram chat+topic, Slack channel+thread, git source file) onto
    the same conversation directory. Returns None when an event carries
    no gate context that can serve as a stable thread anchor.
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
# Anchor for records that arrive on a conversation without an
# associated event id (mis-emitted packets or orphan tests). The
# daemon never produces these, but keeping a deterministic fallback
# file means a buggy emitter shows up as visible noise on the next
# read rather than a silent drop.
_ORPHAN_BASENAME = "_orphan"


def safe_dir_name(key: str) -> str:
    """Filesystem-safe directory name for a conversation key.

    Each ``:`` becomes ``__`` so the original key can be recovered by
    swapping back. Other unsafe characters are collapsed to ``_``.
    """
    rendered = key.replace(":", "__")
    return _SAFE_RE.sub("_", rendered)


def key_from_dir_name(name: str) -> str:
    """Inverse of :func:`safe_dir_name` for the colon encoding."""
    return name.replace("__", ":")


def conversations_root(brr_dir: Path) -> Path:
    return brr_dir / "conversations"


def conversation_path(brr_dir: Path, key: str) -> Path:
    """Return the conversation *directory* for *key*.

    Previously this returned a single ndjson file; under the per-
    event-pipeline layout it's the directory holding one jsonl per
    pipeline run. Callers that previously rendered the path in a
    prompt (e.g. ``run_context.py``) still get a useful filesystem
    location.
    """
    return conversations_root(brr_dir) / safe_dir_name(key)


def event_log_path(brr_dir: Path, key: str, event_id: str) -> Path:
    """Return the jsonl path for one event pipeline within a conversation."""
    safe_event = _SAFE_RE.sub("_", event_id) if event_id else _ORPHAN_BASENAME
    return conversation_path(brr_dir, key) / f"{safe_event}.jsonl"


# ── Atomic append ────────────────────────────────────────────────────


def _atomic_append_line(path: Path, line: str) -> None:
    """Append *line* + newline to *path* in one ``O_APPEND`` syscall.

    Opening with ``os.O_APPEND`` guarantees the offset advance and the
    write happen atomically together (POSIX). Each ``write`` call here
    pushes one fully-formed jsonl record, so even on the rare path
    where two writers share a file (orphan fallback), records can't
    interleave. The single-writer-per-file invariant is the primary
    guarantee; this is defence in depth.
    """
    payload = (line + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


# ── Append + read ────────────────────────────────────────────────────


def append_record(
    brr_dir: Path,
    key: str,
    record: dict[str, Any],
    *,
    event_id: str = "",
) -> None:
    """Append *record* to the conversation log; stamp ``ts`` if missing.

    *event_id* selects the target file under the conversation
    directory. Empty / missing routes to the orphan fallback so a
    buggy emitter is observable.
    """
    if "ts" not in record:
        record = {"ts": _now_iso(), **record}
    line = json.dumps(record, sort_keys=True)
    path = event_log_path(brr_dir, key, event_id)
    _atomic_append_line(path, line)


def _iter_log_files(brr_dir: Path, key: str) -> list[Path]:
    """Return every jsonl file under the conversation directory."""
    directory = conversation_path(brr_dir, key)
    if not directory.exists():
        return []
    files: list[Path] = []
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix == ".jsonl":
            files.append(entry)
    return files


def _records_from_file(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def _ts_key(record: dict[str, Any]) -> str:
    ts = record.get("ts")
    return ts if isinstance(ts, str) else ""


def read_records(brr_dir: Path, key: str) -> list[dict[str, Any]]:
    """Return all records from every event-log file under the conversation.

    Records are merged from each per-event jsonl and sorted by ``ts``
    so a downstream projection sees a chronological stream across
    pipelines. Within a single file the append order already matches
    ``ts`` order (one writer, monotonic clock).
    """
    files = _iter_log_files(brr_dir, key)
    if not files:
        return []
    out: list[dict[str, Any]] = []
    for path in files:
        out.extend(_records_from_file(path))
    out.sort(key=_ts_key)
    return out


def read_recent(
    brr_dir: Path, key: str, limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the last *limit* records from the conversation log."""
    records = read_records(brr_dir, key)
    if limit <= 0 or len(records) <= limit:
        return records
    return records[-limit:]


def read_event_records(
    brr_dir: Path, key: str, event_id: str,
) -> list[dict[str, Any]]:
    """Return the records for one event pipeline only.

    Cheaper than ``read_records`` followed by a task-id filter because
    we open exactly the one file the pipeline wrote to.
    """
    path = event_log_path(brr_dir, key, event_id)
    if not path.exists():
        return []
    return _records_from_file(path)


# ── Specialised appenders ────────────────────────────────────────────


def append_event(brr_dir: Path, key: str, event: dict[str, Any]) -> None:
    """Record an event arrival on the conversation log.

    The event's own id is the file routing key — every record this
    pipeline produces lands in the same ``<event-id>.jsonl``.
    """
    body = (event.get("body") or "").strip()
    summary = body.splitlines()[0] if body else ""
    eid = str(event.get("id") or "")
    record = {
        "kind": "event",
        "event_id": eid,
        "source": event.get("source", ""),
        "summary": summary,
    }
    append_record(brr_dir, key, record, event_id=eid)


def append_task(
    brr_dir: Path,
    key: str,
    *,
    task_id: str,
    event_id: str,
    env: str,
    status: str,
    branch_name: str | None = None,
    seed_ref: str | None = None,
    auto_land_branch: str | None = None,
    branch_source: str | None = None,
    host_context_branch: str | None = None,
) -> None:
    """Record a task lifecycle row on the conversation log."""
    record = {
        "kind": "task",
        "task_id": task_id,
        "event_id": event_id,
        "branch_name": branch_name,
        "env": env,
        "status": status,
    }
    if seed_ref:
        record["seed_ref"] = seed_ref
    if auto_land_branch:
        record["auto_land_branch"] = auto_land_branch
    if branch_source:
        record["branch_source"] = branch_source
    if host_context_branch:
        record["host_context_branch"] = host_context_branch
    append_record(brr_dir, key, record, event_id=event_id)


def append_artifact(
    brr_dir: Path,
    key: str,
    *,
    kind: str,
    path: str,
    task_id: str | None = None,
    event_id: str = "",
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
    if event_id:
        record["event_id"] = event_id
    if label:
        record["label"] = label
    if extra:
        record.update(extra)
    append_record(brr_dir, key, record, event_id=event_id)


def append_update(
    brr_dir: Path,
    key: str,
    *,
    type: str,
    payload: dict[str, Any],
    event_id: str = "",
) -> None:
    """Record a lifecycle update packet on the conversation log."""
    record = {
        "kind": "update",
        "type": type,
        **payload,
    }
    if event_id and "event_id" not in record:
        record["event_id"] = event_id
    append_record(brr_dir, key, record, event_id=event_id)


# ── Listing ──────────────────────────────────────────────────────────


def list_conversations(brr_dir: Path) -> list[str]:
    """Return known conversation keys (decoded), sorted alphabetically."""
    root = conversations_root(brr_dir)
    if not root.exists():
        return []
    keys: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        keys.append(key_from_dir_name(entry.name))
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
