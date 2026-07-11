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
the worker handling that event-led run pipeline. This per-event-
pipeline partitioning keeps overlapping thoughts (ad-hoc sessions, a
second daemon) contention-free without per-shared-file locks — see
``kb/subject-daemon.md``.

Each record carries ``ts`` (microsecond-precision UTC ISO 8601) plus a
``kind`` discriminator (``event``, ``run``, ``artifact``, ``update``)
plus type-specific fields. Dialogue records carry inline ``body`` text
so the resident can read the prior chat without chasing response files.
Reading projects one run's lifecycle by opening just its
``<event-id>.jsonl``; reading the full conversation context merges every
file in the directory by ``ts``. Tailing only the latest rows uses
``read_recent``, which avoids loading whole files when *limit* is small
(see that function's docstring).

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
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, TypedDict


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


# ── Gate thread / correspondent identity ─────────────────────────────


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


def _clean_identity(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())


def _identity_value(meta: dict[str, Any], *keys: str) -> tuple[str, str] | None:
    for key in keys:
        value = _clean_identity(meta.get(key))
        if value:
            return key, value
    return None


def _identity_component(value: str, *, fold: bool = False) -> str:
    text = value.casefold() if fold else value
    return _SAFE_RE.sub("_", text.replace(":", "_"))


def correspondent_key_for_event(meta: dict[str, Any]) -> str | None:
    """Return the per-human identity key for *meta*, when known.

    A conversation key answers "which thread should receive the reply".
    The correspondent key answers "who is talking" and deliberately sits
    above gate-thread keys, so a local Telegram gate and a brnrd-relayed
    Telegram gate can be recognised as the same person without merging
    their delivery channels.
    """
    explicit = _clean_identity(meta.get("correspondent_key"))
    if explicit:
        return explicit

    source = _clean_identity(meta.get("source"))
    if source == "cloud":
        platform = _clean_identity(meta.get("cloud_platform"))
        if platform == "telegram":
            ident = _identity_value(
                meta, "cloud_user_id", "cloud_username", "cloud_user",
            )
            if ident is None:
                return None
            field, value = ident
            if field == "cloud_username":
                return f"telegram:username:{_identity_component(value, fold=True)}"
            if field == "cloud_user_id":
                return f"telegram:user-id:{_identity_component(value)}"
            return f"telegram:user:{_identity_component(value, fold=True)}"
        if platform == "github":
            ident = _identity_value(meta, "github_author", "cloud_user")
            if ident is None:
                return None
            return f"github:login:{_identity_component(ident[1], fold=True)}"
        if platform:
            ident = _identity_value(
                meta, "cloud_user_id", "cloud_username", "cloud_user",
            )
            if ident is None:
                return None
            return (
                f"{_identity_component(platform, fold=True)}:"
                f"user:{_identity_component(ident[1], fold=True)}"
            )
        return None

    if source == "telegram":
        ident = _identity_value(
            meta, "telegram_user_id", "telegram_username", "telegram_user",
        )
        if ident is None:
            return None
        field, value = ident
        if field == "telegram_username":
            return f"telegram:username:{_identity_component(value, fold=True)}"
        if field == "telegram_user_id":
            return f"telegram:user-id:{_identity_component(value)}"
        return f"telegram:user:{_identity_component(value, fold=True)}"

    if source == "slack":
        ident = _identity_value(meta, "slack_user")
        if ident is None:
            return None
        return f"slack:user:{_identity_component(ident[1], fold=True)}"

    if source == "github":
        ident = _identity_value(meta, "github_author")
        if ident is None:
            return None
        return f"github:login:{_identity_component(ident[1], fold=True)}"

    return None


def origin_message_key_for_event(meta: dict[str, Any]) -> str | None:
    """Return a canonical source-message key for exact duplicate detection."""
    source = _clean_identity(meta.get("source"))
    if source == "telegram":
        chat = _clean_identity(meta.get("telegram_chat_id"))
        topic = _clean_identity(meta.get("telegram_topic_id"))
        message = _clean_identity(meta.get("telegram_message_id"))
        if chat and message:
            return (
                f"telegram:{_identity_component(chat)}:"
                f"{_identity_component(topic)}:{_identity_component(message)}"
            )
        return None
    if source == "cloud" and _clean_identity(meta.get("cloud_platform")) == "telegram":
        chat = _clean_identity(meta.get("cloud_chat_id"))
        topic = _clean_identity(meta.get("cloud_topic_id"))
        message = _clean_identity(meta.get("cloud_message_id"))
        if chat and message:
            return (
                f"telegram:{_identity_component(chat)}:"
                f"{_identity_component(topic)}:{_identity_component(message)}"
            )
        return None
    if source in {"github", "cloud"}:
        platform = (
            _clean_identity(meta.get("cloud_platform"))
            if source == "cloud" else "github"
        )
        if platform != "github":
            return None
        repo = _clean_identity(meta.get("github_repo"))
        comment_id = _clean_identity(meta.get("github_comment_id"))
        if repo and comment_id:
            return (
                f"github:{_identity_component(repo, fold=True)}:"
                f"{_identity_component(comment_id)}"
            )
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
_LIFECYCLE_KINDS = {"run", "update"}
_DIALOGUE_ARTIFACT_KINDS = {"response", "interim_response", "outbound_message"}
# Anchor for records that arrive on a conversation without an
# associated event id (mis-emitted packets or orphan tests). The
# daemon never produces these, but keeping a deterministic fallback
# file means a buggy emitter shows up as visible noise on the next
# read rather than a silent drop.
_ORPHAN_BASENAME = "_orphan"


class HistoryGroup(TypedDict, total=False):
    """Agent-facing descriptor for one grouped deep-history jsonl file."""

    id: str
    kind: str
    source: str
    conversation_key: str
    label: str
    path: str
    record_count: int
    dialogue_count: int
    latest_ts: str


class CommunicationSnapshot(TypedDict, total=False):
    """Wake-time, curated view over conversation history."""

    current_thread: str
    correspondent_key: str
    related_threads: list[dict[str, Any]]
    recent_turns: list[dict[str, Any]]
    history_groups: list[HistoryGroup]
    forge: dict[str, Any]
    prior_failure: dict[str, Any]


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


def _tag_record(record: dict[str, Any], key: str) -> dict[str, Any]:
    if record.get("conversation_key") == key:
        return record
    return {"conversation_key": key, **record}


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


def _summary_for_body(body: str, *, limit: int = 240) -> str:
    """One-line preview for UI surfaces; ``body`` keeps the full text."""
    summary = " ".join(body.split())
    if len(summary) > limit:
        summary = summary[: limit - 3].rstrip() + "..."
    return summary


def _is_dialogue_record(record: dict[str, Any]) -> bool:
    """True for records that represent human/agent turns.

    ``read_recent`` is prompt-facing by default, so lifecycle records do
    not compete with dialogue. Unknown custom kinds remain visible for
    backwards compatibility with callers using the low-level append API.
    """
    kind = record.get("kind")
    if kind == "event":
        return True
    if kind == "artifact":
        artifact_kind = record.get("artifact_kind")
        body = record.get("body")
        return (
            artifact_kind in _DIALOGUE_ARTIFACT_KINDS
            and isinstance(body, str)
            and bool(body.strip())
        )
    if kind in _LIFECYCLE_KINDS:
        return False
    return True


def _next_recent_record(
    iterator: Iterator[dict[str, Any]],
    *,
    include_lifecycle: bool,
) -> dict[str, Any] | None:
    for record in iterator:
        if include_lifecycle or _is_dialogue_record(record):
            return record
    return None


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


def conversation_keys_for_correspondent(
    brr_dir: Path,
    correspondent_key: str | None,
    *,
    include_key: str | None = None,
) -> list[str]:
    """Return conversation keys with event records for *correspondent_key*.

    ``include_key`` keeps the active thread in the set even before any
    prior event in that thread has been written with the identity tag.
    """
    keys_by_dir: dict[str, str] = {}

    def add_key(value: str, *, prefer: bool = False) -> None:
        safe = safe_dir_name(value)
        if prefer or safe not in keys_by_dir:
            keys_by_dir[safe] = value

    if include_key:
        add_key(include_key, prefer=True)
    if not correspondent_key:
        return sorted(keys_by_dir.values())
    for key in list_conversations(brr_dir):
        for record in read_records(brr_dir, key):
            if (
                record.get("kind") == "event"
                and record.get("correspondent_key") == correspondent_key
            ):
                add_key(key)
                break
    return sorted(keys_by_dir.values())


def find_event_by_origin_message(
    brr_dir: Path,
    origin_message_key: str | None,
    *,
    exclude_event_id: str = "",
) -> dict[str, Any] | None:
    """Return a prior event record for the same source message, if any."""
    if not origin_message_key:
        return None
    for key in list_conversations(brr_dir):
        for record in read_records(brr_dir, key):
            if record.get("kind") != "event":
                continue
            if record.get("event_id") == exclude_event_id:
                continue
            if record.get("origin_message_key") == origin_message_key:
                return _tag_record(record, key)
    return None


def read_records_for_correspondent(
    brr_dir: Path,
    key: str,
    correspondent_key: str | None,
) -> list[dict[str, Any]]:
    """Return merged records for the current thread's correspondent.

    The active *key* is always included. When the correspondent is known,
    sibling conversation directories that have carried the same
    ``correspondent_key`` are merged too, with ``conversation_key`` added
    to returned records so prompt renderers can show which pipe a turn
    came through.
    """
    if not correspondent_key:
        return [_tag_record(r, key) for r in read_records(brr_dir, key)]
    out: list[dict[str, Any]] = []
    for related in conversation_keys_for_correspondent(
        brr_dir, correspondent_key, include_key=key,
    ):
        out.extend(_tag_record(r, related) for r in read_records(brr_dir, related))
    out.sort(key=_ts_key)
    return out


def read_recent_for_correspondent(
    brr_dir: Path,
    key: str,
    correspondent_key: str | None,
    limit: int = 10,
    *,
    include_lifecycle: bool = False,
) -> list[dict[str, Any]]:
    """Return the recent tail for a thread plus its known sibling channels."""
    records = read_records_for_correspondent(brr_dir, key, correspondent_key)
    if not include_lifecycle:
        records = [r for r in records if _is_dialogue_record(r)]
    if limit <= 0:
        return records
    return records[-limit:]


def _conversation_source_from_key(key: str) -> str:
    """Best-effort source label from a conversation key."""
    if key.startswith("cloud:"):
        parts = key.split(":", 2)
        if len(parts) >= 2 and parts[1]:
            return f"cloud/{parts[1]}"
        return "cloud"
    return key.split(":", 1)[0] if ":" in key else key


def _history_group_kind(source: str) -> str:
    return "forge_thread" if source in {"github", "cloud/github"} else "gate_thread"


def _history_group_label(source: str, key: str) -> str:
    if source in {"github", "cloud/github"}:
        return f"GitHub thread {key}"
    if source.startswith("cloud/"):
        return f"{source} thread {key}"
    return f"{source or 'conversation'} thread {key}"


def _thread_summary(
    key: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    source = _conversation_source_from_key(key)
    latest = _ts_key(records[-1]) if records else ""
    return {
        "conversation_key": key,
        "source": source,
        "kind": _history_group_kind(source),
        "record_count": len(records),
        "dialogue_count": sum(1 for r in records if _is_dialogue_record(r)),
        "latest_ts": latest,
    }


# Terminal run outcomes on a thread. ``done`` is a clean success; ``failed``
# is an operational failure (runner crash / env setup / retry exhaustion) —
# the daemon only emits it on those paths, never on a normal agent noop. A
# push ``conflict`` is a delivery outcome, not a run outcome, so it is not in
# this set: it never masks or stands in for a prior run failure.
_RUN_TERMINAL_TYPES = {"done", "failed"}


def _select_prior_failure(
    records: list[dict[str, Any]],
    *,
    key: str,
) -> dict[str, Any] | None:
    """Return a failure facet iff this thread's last run failed operationally.

    Walks *records* (already excludes the current run) newest-first,
    restricted to the current thread *key*, and stops at the first terminal
    run outcome. Surfaces a facet only when that outcome was a ``failed``
    packet — so a later success clears a stale failure, and a normal
    "agent chose to noop" (which leaves no ``failed`` record) never reads
    as one. The facet carries the structured reason from the persisted
    packet (error detail, attempt count, exit code, timeout flag, timestamp)
    so a wake landing after an interruption opens knowing it.
    """
    for record in reversed(records):
        if record.get("conversation_key") != key:
            continue
        if record.get("kind") != "update":
            continue
        rtype = record.get("type")
        if rtype not in _RUN_TERMINAL_TYPES:
            continue
        if rtype == "done":
            return None
        facet: dict[str, Any] = {"stage": str(record.get("stage") or "run")}
        reason = str(record.get("error") or "").strip()
        if reason:
            facet["reason"] = reason
        attempts = record.get("attempts")
        if isinstance(attempts, int):
            facet["attempts"] = attempts
        exit_code = record.get("exit_code")
        if isinstance(exit_code, int):
            facet["exit_code"] = exit_code
        if record.get("timed_out"):
            facet["timed_out"] = True
        ts = _ts_key(record)
        if ts:
            facet["ts"] = ts
        event_id = str(record.get("event_id") or "").strip()
        if event_id:
            facet["event_id"] = event_id
        return facet
    return None


def _without_current_records(
    records: list[dict[str, Any]],
    *,
    event_id: str = "",
    run_id: str = "",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records:
        if event_id and record.get("event_id") == event_id:
            continue
        if run_id and record.get("run_id") == run_id:
            continue
        out.append(record)
    return out


# How far back (relative to the thread's newest dialogue turn) the
# unanswered-event boost in _select_snapshot_turns reaches. Answered-ness
# is bookkeeping, not truth: a reply folded into a sibling event's answer,
# an image-only message, or a pre-artifact-tagging delivery leaves the
# event "unanswered" forever. Without a horizon those fossils get boosted
# into *every* future wake's snapshot on a busy thread (observed live
# 2026-07-11: month-old turns permanently occupying half the recent-turns
# budget). A genuinely pending week-old ask belongs in the inbox/plan, not
# in a recency snapshot.
_UNANSWERED_BOOST_HORIZON = timedelta(days=7)


def _parse_record_ts(value: Any) -> datetime | None:
    """Parse a record ``ts`` into an aware datetime, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _select_snapshot_turns(
    records: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Select dialogue turns for the wake snapshot.

    Recency is the main signal, but unanswered user events get a boost so
    an older pending ask is less likely to disappear behind a more recent
    answered exchange. The boost is capped at half the budget: on a busy
    thread, a handful of stray old unanswered events (an image-only
    message, a reply folded into a sibling event's answer, ...) must never
    be able to outrank *every* recent turn and blank out what just
    happened — recency always keeps at least half the slots. The boost is
    also bounded by ``_UNANSWERED_BOOST_HORIZON``: an event that has sat
    unanswered longer than that (relative to the newest dialogue turn) is
    treated as answered-in-substance or abandoned, not re-pinned into
    every wake forever. The selected rows are returned in chronological
    order so the prompt still reads like a chat.
    """
    dialogue = [r for r in records if _is_dialogue_record(r)]
    total = len(dialogue)
    if limit <= 0 or total <= limit:
        return dialogue

    answered_event_ids = {
        str(r.get("event_id") or "")
        for r in dialogue
        if (
            r.get("kind") == "artifact"
            and r.get("artifact_kind") in _DIALOGUE_ARTIFACT_KINDS
            and r.get("event_id")
        )
    }

    newest_ts = _parse_record_ts(dialogue[-1].get("ts"))
    boost_cutoff = (
        newest_ts - _UNANSWERED_BOOST_HORIZON if newest_ts is not None else None
    )

    def _is_unanswered(index: int) -> bool:
        record = dialogue[index]
        if not (
            record.get("kind") == "event"
            and record.get("event_id")
            and str(record.get("event_id")) not in answered_event_ids
        ):
            return False
        if boost_cutoff is None:
            return True
        record_ts = _parse_record_ts(record.get("ts"))
        return record_ts is not None and record_ts >= boost_cutoff

    unanswered_count = sum(1 for i in range(total) if _is_unanswered(i))
    unanswered_budget = min(limit // 2, unanswered_count)
    recency_budget = limit - unanswered_budget

    picked_indices = set(range(total - recency_budget, total))
    remaining = limit - len(picked_indices)
    if remaining:
        for index in range(total - 1, -1, -1):
            if remaining <= 0:
                break
            if index in picked_indices or not _is_unanswered(index):
                continue
            picked_indices.add(index)
            remaining -= 1

    return [dialogue[i] for i in sorted(picked_indices)]


def build_communication_snapshot(
    brr_dir: Path,
    key: str,
    correspondent_key: str | None = None,
    *,
    event_id: str = "",
    run_id: str = "",
    recent_limit: int = 8,
    history_groups: list[HistoryGroup] | None = None,
) -> CommunicationSnapshot:
    """Return the curated wake-time communication snapshot.

    The snapshot is prompt-facing: it shows which thread is active,
    sibling channels for the same correspondent, and recent dialogue
    turns woven across those channels. The full untruncated history stays
    behind separate JSONL files produced by
    :func:`write_grouped_history_files`.
    """
    records_by_key: dict[str, list[dict[str, Any]]] = {}
    related_keys = conversation_keys_for_correspondent(
        brr_dir, correspondent_key, include_key=key,
    )
    for related in related_keys:
        records_by_key[related] = [
            _tag_record(r, related) for r in read_records(brr_dir, related)
        ]

    merged: list[dict[str, Any]] = []
    for records in records_by_key.values():
        merged.extend(records)
    merged.sort(key=_ts_key)
    prior = _without_current_records(
        merged, event_id=event_id, run_id=run_id,
    )
    recent_turns = _select_snapshot_turns(prior, limit=recent_limit)

    snapshot: CommunicationSnapshot = {
        "current_thread": key,
        "related_threads": [
            _thread_summary(related, records_by_key.get(related, []))
            for related in related_keys
        ],
        "recent_turns": recent_turns,
    }
    if correspondent_key:
        snapshot["correspondent_key"] = correspondent_key
    prior_failure = _select_prior_failure(prior, key=key)
    if prior_failure:
        snapshot["prior_failure"] = prior_failure
    if history_groups:
        snapshot["history_groups"] = history_groups
    return snapshot


def write_grouped_history_files(
    brr_dir: Path,
    output_dir: Path,
    key: str,
    correspondent_key: str | None = None,
) -> list[HistoryGroup]:
    """Write untruncated per-thread JSONL history files for a wake.

    Each file groups records by the input thread that produced them:
    native gate threads (Telegram / Slack / cloud relay channels) or
    forge threads (GitHub issue / PR conversations). A manifest JSON is
    written beside the JSONL files for machine consumers, while callers
    use the returned group descriptors for prompt/context rendering.
    """
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    groups: list[HistoryGroup] = []
    for related in conversation_keys_for_correspondent(
        brr_dir, correspondent_key, include_key=key,
    ):
        records = [_tag_record(r, related) for r in read_records(brr_dir, related)]
        if not records:
            continue
        source = _conversation_source_from_key(related)
        kind = _history_group_kind(source)
        filename = f"{kind}-{safe_dir_name(related)}.jsonl"
        path = output_dir / filename
        payload = "\n".join(json.dumps(r, sort_keys=True) for r in records)
        path.write_text(payload + "\n", encoding="utf-8")
        group: HistoryGroup = {
            "id": f"{kind}:{related}",
            "kind": kind,
            "source": source,
            "conversation_key": related,
            "label": _history_group_label(source, related),
            "path": str(path),
            "record_count": len(records),
            "dialogue_count": sum(1 for r in records if _is_dialogue_record(r)),
            "latest_ts": _ts_key(records[-1]),
        }
        groups.append(group)

    manifest = output_dir / "manifest.json"
    manifest.write_text(
        json.dumps({"groups": groups}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return groups


def read_recent(
    brr_dir: Path,
    key: str,
    limit: int = 10,
    *,
    include_lifecycle: bool = False,
) -> list[dict[str, Any]]:
    """Return the recent prompt-facing tail, oldest first.

    By default the tail is kind-aware: dialogue records are selected and
    lifecycle ``run``/``update`` rows are dropped, so bursts of progress
    packets cannot evict user/agent turns from the next prompt. Pass
    ``include_lifecycle=True`` for the raw historical tail.

    Merges globally by ``ts`` without building the full sorted history
    when *limit* > 0: each ``<event-id>.jsonl`` is scanned from the end
    in fixed-size chunks, and a small heap selects the newest *limit*
    matching rows. This matches :func:`read_records` as long as ``ts`` is
    non-decreasing within each file (single writer, monotonic clock —
    see ``kb/subject-daemon.md``).

    *limit* <= 0 means no cap over the selected kind set.
    """
    if limit <= 0:
        records = read_records(brr_dir, key)
        if include_lifecycle:
            return records
        return [r for r in records if _is_dialogue_record(r)]
    files = _iter_log_files(brr_dir, key)
    if not files:
        return []
    rev_iters = [_iter_records_reversed(p) for p in files]
    heap: list[tuple[float, int, int, dict[str, Any]]] = []
    seq = 0
    for i, it in enumerate(rev_iters):
        rec = _next_recent_record(it, include_lifecycle=include_lifecycle)
        if rec is not None:
            heapq.heappush(heap, (-_ts_epoch(rec), seq, i, rec))
            seq += 1
    picked: list[dict[str, Any]] = []
    while heap and len(picked) < limit:
        _, _, fi, rec = heapq.heappop(heap)
        picked.append(rec)
        nxt = _next_recent_record(
            rev_iters[fi],
            include_lifecycle=include_lifecycle,
        )
        if nxt is not None:
            heapq.heappush(heap, (-_ts_epoch(nxt), seq, fi, nxt))
            seq += 1
    picked.reverse()
    return picked


def read_event_records(
    brr_dir: Path, key: str, event_id: str,
) -> list[dict[str, Any]]:
    """Return the records for one event pipeline only.

    Cheaper than ``read_records`` followed by a run-id filter because
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
    body = str(event.get("body") or "")
    eid = str(event.get("id") or "")
    record = {
        "kind": "event",
        "event_id": eid,
        "source": event.get("source", ""),
        "conversation_key": key,
        "summary": _summary_for_body(body),
        "body": body,
    }
    correspondent_key = correspondent_key_for_event(event)
    if correspondent_key:
        record["correspondent_key"] = correspondent_key
    origin_message_key = origin_message_key_for_event(event)
    if origin_message_key:
        record["origin_message_key"] = origin_message_key
    append_record(brr_dir, key, record, event_id=eid)


def append_run(
    brr_dir: Path,
    key: str,
    *,
    run_id: str,
    event_id: str,
    env: str,
    status: str,
    branch_name: str | None = None,
    seed_ref: str | None = None,
    target_branch: str | None = None,
    branch_source: str | None = None,
    host_context_branch: str | None = None,
    repo_label: str | None = None,
) -> None:
    """Record a run lifecycle row on the conversation log."""
    record = {
        "kind": "run",
        "run_id": run_id,
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
    if repo_label:
        record["repo_label"] = repo_label
    append_record(brr_dir, key, record, event_id=event_id)


def append_artifact(
    brr_dir: Path,
    key: str,
    *,
    kind: str,
    path: str,
    run_id: str | None = None,
    event_id: str = "",
    label: str | None = None,
    body: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Record an artifact creation on the conversation log."""
    record: dict[str, Any] = {
        "kind": "artifact",
        "artifact_kind": kind,
        "path": path,
    }
    if run_id:
        record["run_id"] = run_id
    if event_id:
        record["event_id"] = event_id
    if label:
        record["label"] = label
    if extra:
        record.update(extra)
    if body is not None:
        record["body"] = body
        if "summary" not in record:
            record["summary"] = _summary_for_body(body)
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
    if type == "heartbeat":
        return
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


def records_for_run(
    brr_dir: Path, key: str, run_id: str,
) -> list[dict[str, Any]]:
    """Return all records mentioning *run_id* in this conversation."""
    return [
        record for record in read_records(brr_dir, key)
        if record.get("run_id") == run_id
    ]
