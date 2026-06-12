"""Conversation log — per-event-pipeline append-only history.

A conversation is the history of one gate thread (Telegram chat, Slack
thread, GitHub issue or pull request). Other forge gates are expected
to follow the same pattern: a stable ``source``-specific key derived
from their thread anchor fields. It has no manifest, no title, no intent —
those were the leaky stream identity fields removed in the 2026-05-05
streams-to-conversations refactor (see ``kb/decision-drop-streams.md``).

Runtime layout::

    .brr/conversations/
        <safe-key>/
            <event-id>.jsonl    — append-only records for one pipeline run

Each ``<event-id>.jsonl`` file has exactly one writer for its lifetime:
the worker handling that one event/task pipeline. This per-event-
pipeline partitioning keeps overlapping thoughts (ad-hoc sessions, a
second daemon) contention-free without per-shared-file locks — see
``kb/subject-daemon.md``.

Each record carries ``ts`` (microsecond-precision UTC ISO 8601) plus a
``kind`` discriminator (``event``, ``task``, ``artifact``, ``update``)
plus type-specific fields. Reading projects one task's lifecycle by
opening just its ``<event-id>.jsonl``; reading the full conversation
context merges every file in the directory by ``ts``. Tailing only the
latest rows uses ``read_recent``, which avoids loading whole files
when *limit* is small (see that function's docstring).

Single-line ``O_APPEND`` writes in binary mode rely on the kernel's
guarantee that the offset advance and the write happen atomically
together — defence in depth, since the per-event-file partitioning
already gives each file exactly one writer.

Conversations are runtime state. Durable knowledge still belongs in
``kb/`` — agents that want to track an ongoing line of work write a
kb page rather than asking brr for a typed identity field.
"""

from __future__ import annotations

import heapq
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


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
    (Telegram chat+topic, Slack channel+thread, GitHub repo+issue/PR)
    onto the same conversation directory. Returns None when an event
    carries no gate context that can serve as a stable thread anchor.
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
    if source == "github":
        repo = (meta.get("github_repo") or "").strip()
        raw_n = meta.get("github_issue_number")
        if isinstance(raw_n, int):
            num = raw_n
        elif isinstance(raw_n, str) and raw_n.strip().isdigit():
            num = int(raw_n.strip())
        else:
            num = None
        if repo and num is not None:
            return f"github:{repo}:{num}"
        return None
    if source == "cloud":
        # Managed mode: the cloud gate carries the origin platform's
        # routing as discrete fields so back-and-forth in the same origin
        # chat threads onto one conversation, like a native gate.
        platform = (meta.get("cloud_platform") or "").strip()
        chat = meta.get("cloud_chat_id")
        topic = meta.get("cloud_topic_id") or ""
        if platform and chat not in (None, ""):
            return f"cloud:{platform}:{chat}:{topic}"
        return "cloud:default"
    if source:
        return f"{source}:default"
    return None


def conversation_key_for_event(event: dict[str, Any]) -> str | None:
    """Resolve an event to its conversation key, if it has one.

    Order:

    1. Explicit ``conversation_key`` carried on the event.
    2. Gate-thread fingerprint (Telegram chat, Slack thread, GitHub issue/PR).
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


def _iter_lines_reversed(path: Path) -> Iterator[str]:
    """Yield non-empty stripped lines from *path*, last line first.

    Reads the file in binary chunks from EOF so callers do not load
    whole multi-megabyte jsonl files just to tail a few records.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size == 0:
        return
    block = 8192
    try:
        fh = path.open("rb")
    except OSError:
        return
    try:
        incomplete = b""
        pos = size
        while pos > 0:
            take = min(block, pos)
            pos -= take
            fh.seek(pos)
            buf = fh.read(take) + incomplete
            parts = buf.split(b"\n")
            incomplete = parts[0]
            for raw in reversed(parts[1:]):
                raw = raw.strip()
                if raw:
                    yield raw.decode("utf-8", errors="replace")
        tail = incomplete.strip()
        if tail:
            yield tail.decode("utf-8", errors="replace")
    finally:
        fh.close()


def _iter_records_reversed(path: Path) -> Iterator[dict[str, Any]]:
    """Parse jsonl from *path* last record first (reverse physical order)."""
    for line in _iter_lines_reversed(path):
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _ts_epoch(record: dict[str, Any]) -> float:
    """UTC epoch seconds for sorting; missing or bad ``ts`` → -inf (oldest)."""
    ts = record.get("ts")
    if not isinstance(ts, str) or not ts:
        return float("-inf")
    try:
        if ts.endswith("Z"):
            dt = datetime.fromisoformat(ts[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return float("-inf")


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
    """Return the last *limit* records from the conversation log, oldest first.

    Merges globally by ``ts`` without building the full sorted history when
    *limit* > 0: each ``<event-id>.jsonl`` is scanned from the end in
    fixed-size chunks, and a small heap selects the newest *limit* rows.
    This matches :func:`read_records` as long as ``ts`` is non-decreasing
    within each file (single writer, monotonic clock — see
    ``kb/subject-daemon.md``).

    *limit* <= 0 means no cap — same as :func:`read_records` (full merge
    and sort).
    """
    if limit <= 0:
        return read_records(brr_dir, key)
    files = _iter_log_files(brr_dir, key)
    if not files:
        return []
    rev_iters = [_iter_records_reversed(p) for p in files]
    heap: list[tuple[float, int, int, dict[str, Any]]] = []
    seq = 0
    for i, it in enumerate(rev_iters):
        rec = next(it, None)
        if rec is not None:
            heapq.heappush(heap, (-_ts_epoch(rec), seq, i, rec))
            seq += 1
    picked: list[dict[str, Any]] = []
    while heap and len(picked) < limit:
        _, _, fi, rec = heapq.heappop(heap)
        picked.append(rec)
        nxt = next(rev_iters[fi], None)
        if nxt is not None:
            heapq.heappush(heap, (-_ts_epoch(nxt), seq, fi, nxt))
            seq += 1
    picked.reverse()
    return picked


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
    summary = summarize_text(body)
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
    target_branch: str | None = None,
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
    if target_branch:
        record["target_branch"] = target_branch
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


# ── Agent-facing rendering ───────────────────────────────────────────
# The log interleaves *message* records (user events + the agent's own
# replies) with *lifecycle* records (task / update / heartbeat /
# artifact). A flat "last N records" tail lets one long, chatty run's
# lifecycle evict the very message a short follow-up refers to — so the
# agent-facing tail renders messages in their own block (the actual
# back-and-forth) and demotes lifecycle to a compact secondary block.
# Both the daemon prompt and the run-context file render through here so
# the two surfaces can't drift. See kb/design-conversation-continuity.md.

_REPLY_ARTIFACT_KINDS = {"response", "interim_response", "outbound_message"}


def summarize_text(text: str, *, limit: int = 240) -> str:
    """Whitespace-collapsed, length-bounded one-line summary of a body."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 3].rstrip() + "..."
    return collapsed


def _is_reply_artifact(record: dict[str, Any]) -> bool:
    return (
        record.get("kind") == "artifact"
        and record.get("artifact_kind") in _REPLY_ARTIFACT_KINDS
    )


def _message_bullet(record: dict[str, Any]) -> str | None:
    ts = record.get("ts", "")
    if record.get("kind") == "event":
        summary = (record.get("summary") or "").strip()
        source = record.get("source") or ""
        return f"- {ts} user ({source}): {summary}".rstrip()
    if _is_reply_artifact(record):
        summary = (record.get("summary") or "").strip()
        if not summary:
            # Legacy reply records carried only a path, not the text; they
            # add no dialogue value, so leave them out of the message view.
            return None
        return f"- {ts} you: {summary}".rstrip()
    return None


def _lifecycle_bullet(record: dict[str, Any]) -> str | None:
    ts = record.get("ts", "")
    kind = record.get("kind")
    if kind == "task":
        tid = record.get("task_id", "")
        status = record.get("status") or "pending"
        branch = (
            record.get("publish_branch")
            or record.get("target_branch")
            or record.get("expected_publish_branch")  # compat: old records
            or record.get("branch_name")
            or ""
        )
        return f"- {ts} task {tid} status={status} branch={branch}".rstrip()
    if kind == "update":
        ptype = record.get("type") or ""
        tid = record.get("task_id") or ""
        stage = record.get("stage") or ""
        err = record.get("error") or ""
        bits = [f"- {ts} update {ptype}"]
        if tid:
            bits.append(f"task={tid}")
        if stage:
            bits.append(f"stage={stage}")
        if err:
            bits.append(f"error={err}")
        return " ".join(bits)
    if kind == "artifact":  # non-reply artifact (review pack, trace, ...)
        label = record.get("label") or record.get("artifact_kind") or ""
        path = record.get("path") or ""
        return f"- {ts} artifact {label} {path}".rstrip()
    return None


def render_conversation_tail(
    records: list[dict[str, Any]] | None,
    *,
    messages_max: int = 8,
    lifecycle_max: int = 3,
) -> str:
    """Render the agent-facing tail: a messages block + a lifecycle block.

    Keeps the most recent *messages_max* user turns and *messages_max*
    agent replies, merged chronologically, so a flood of one kind can't
    evict the other — then a compact *lifecycle_max* tail of task/update
    rows for operational orientation. Returns "" when nothing renders.
    """
    if not records:
        return ""
    user_events = [r for r in records if r.get("kind") == "event"]
    replies = [r for r in records if _is_reply_artifact(r)]
    lifecycle = [
        r for r in records
        if r.get("kind") in ("task", "update")
        or (r.get("kind") == "artifact" and not _is_reply_artifact(r))
    ]
    selected = user_events[-messages_max:] + replies[-messages_max:]
    selected.sort(key=lambda r: r.get("ts") or "")
    message_lines = [b for b in (_message_bullet(r) for r in selected) if b]

    blocks: list[str] = []
    if message_lines:
        blocks.append("Messages (oldest first):")
        blocks.extend(message_lines)
    if lifecycle_max > 0:
        life_lines = [
            b for b in (_lifecycle_bullet(r) for r in lifecycle[-lifecycle_max:])
            if b
        ]
        if life_lines:
            if blocks:
                blocks.append("")
            blocks.append("Task lifecycle (oldest first):")
            blocks.extend(life_lines)
    return "\n".join(blocks)
