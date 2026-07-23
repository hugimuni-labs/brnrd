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

One human can reach the resident through more than one gate that all
resolve to the same ``correspondent_key`` (a native ``telegram:<chat>:``
thread and the ``cloud:telegram:<chat>:`` thread the cloud gate mirrors
it onto are two conversation keys for one person).
``read_records_for_correspondent`` / ``read_recent_for_correspondent`` /
``build_communication_snapshot`` weave those sibling threads' records
together for a wake: the *N* most recent dialogue turns across *all* of
the correspondent's conversation keys, merged chronologically by ``ts``,
one shared ``limit`` for the whole woven set rather than a per-thread
budget. Because the dispatcher persists an inbound event on every
conversation key it lands on even when it recognises a mirrored
delivery and skips a second worker run, the same exchange can appear on
two sibling keys' stores; the weave dedups those before applying
``limit``, so a fixed budget is never spent twice on one exchange (see
``_dedupe_woven_records``). The surviving copy keeps the sibling
key(s) it also arrived on in ``duplicate_conversation_keys`` — dedup
collapses the display, never the provenance.

Single-line ``O_APPEND`` writes in binary mode rely on the kernel's
guarantee that the offset advance and the write happen atomically
together — defence in depth, since the per-event-file partitioning
already gives each file exactly one writer.

Conversations are runtime state. Durable knowledge still belongs in
``kb/`` — agents that want to track an ongoing line of work write a
kb page rather than asking brr for a typed identity field.
"""

from __future__ import annotations

import hashlib
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
    """Agent-facing descriptor for one grouped deep-history jsonl file.

    ``record_count`` is how many records actually landed in ``path``
    (bounded by ``HISTORY_GROUP_TAIL_LIMIT``); ``total_record_count`` is
    the thread's true size. When ``truncated`` is set, older records
    were dropped from this per-run copy — they still live permanently
    under ``store_path`` (the base conversation directory), which is
    never bounded or copied.
    """

    id: str
    kind: str
    source: str
    conversation_key: str
    label: str
    path: str
    record_count: int
    total_record_count: int
    truncated: bool
    store_path: str
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
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        # Conversation history carries full message bodies and sender
        # identities — owner-only, like the gate tokens (which repair
        # their mode on every load; this is the same net for a store
        # that predates the rule and may sit at 0644 on disk).
        os.fchmod(fd, 0o600)
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
    max_age_seconds: float | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any] | None:
    """Return a prior event record for the same source message, if any.

    The dedup this feeds catches a *genuine re-delivery* — the same external
    message landing on two configured channels — which is near-simultaneous by
    construction (one webhook, fanned out). So the scan is windowed:
    ``max_age_seconds`` (``None`` = unbounded, the old behaviour) drops a prior
    record whose ``ts`` is older than the window before it can match.

    Without the window, an origin key that *coincidentally* repeats — a stale
    message whose id collides with a new one — false-matches across arbitrary
    history and silently drops the new message as a duplicate. A real
    re-delivery never arrives a day (let alone a month) after the original;
    a collision is the only thing that does.
    """
    if not origin_message_key:
        return None
    cutoff: float | None = None
    if max_age_seconds is not None:
        base = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        cutoff = base - max_age_seconds
    for key in list_conversations(brr_dir):
        for record in read_records(brr_dir, key):
            if record.get("kind") != "event":
                continue
            if record.get("event_id") == exclude_event_id:
                continue
            if record.get("origin_message_key") != origin_message_key:
                continue
            if cutoff is not None and _ts_epoch(record) < cutoff:
                # Too old to be a re-delivery — a coincidental key collision,
                # not the same message arriving twice. Keep scanning: a genuine
                # recent re-delivery may still be ahead.
                continue
            return _tag_record(record, key)
    return None


# Window for the cross-thread duplicate fallback in
# `_dedupe_woven_records` — mirrors `daemon.py`'s
# `_DEDUP_WINDOW_SECONDS_DEFAULT` (dispatch-time re-delivery dedup),
# since both describe the same phenomenon: the cloud gate mirrors
# telegram, fanning one external message out to two conversation keys
# within seconds of each other. A body-hash match this far apart is
# treated as the same delivery; further apart, as a genuine repeat.
_CORRESPONDENT_DEDUP_WINDOW_SECONDS = 6 * 3600.0


def _correspondent_dedup_identity(record: dict[str, Any]) -> tuple[str, ...] | None:
    """Return the cross-thread duplicate identity for *record*, or None.

    Only ``event`` records are eligible. They are the only records the
    dispatcher persists on *every* conversation key an inbound message
    lands on — even when it recognises the delivery as a mirror of one
    already seen and skips a second worker run (``daemon.py``'s
    dispatch path calls :func:`append_event` before it checks
    :func:`find_event_by_origin_message`).

    ``origin_message_key`` is *not*, on its own, a safe exact handle here
    — checked against the live conversation store (#338), it is reused
    on purpose by a respawn chain: ``daemon.py``'s own comment on
    ``is_respawn_origin`` notes a respawned event "carries its parent's
    telegram_chat_id / telegram_message_id / telegram_topic_id forward
    so its eventual reply lands in the same thread," which recomputes to
    the *same* ``origin_message_key`` for what is a genuinely new,
    unrelated message. Live data confirmed this is not a rare edge case:
    62 of 1186 origin-key groups on one real correspondent were exactly
    this — same key, completely different body text, spanning most of a
    thread's history. Treating ``origin_message_key`` alone as identity
    would have silently collapsed dozens of real distinct turns.

    So identity always includes a body hash — an origin-key match still
    requires matching text to collapse, which is exactly what a genuine
    mirrored delivery has (the cloud gate mirrors the literal message)
    and a respawn continuation does not. When a record predates the
    ``origin_message_key`` field or its source computes none, identity
    falls back to the body hash alone; the caller windows every
    body-hash-bearing identity by time so an unrelated old message that
    merely repeats a later one's text is never mistaken for the same
    delivery.
    """
    if record.get("kind") != "event":
        return None
    body = record.get("body")
    if not isinstance(body, str):
        return None
    body = body.strip()
    if not body:
        return None
    body_hash = hashlib.sha1(body.encode("utf-8")).hexdigest()
    origin = str(record.get("origin_message_key") or "").strip()
    if origin:
        return ("origin", origin, body_hash)
    return ("body", body_hash)


def _dedupe_woven_records(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse cross-thread duplicates from a ``ts``-sorted merged list.

    Two conversation keys for the same correspondent — a native gate and
    the one that mirrors it (``telegram:<chat>:`` / ``cloud:telegram:
    <chat>:``) — both persist the inbound event even when the dispatcher
    recognises the mirrored delivery and skips a second worker run. Left
    unmerged, the woven view shows that one exchange twice and a fixed
    ``limit`` tail spends half its slots on the duplicate.

    Identity is :func:`_correspondent_dedup_identity` — always body-hash
    inclusive, per that function's docstring. Every identity is windowed
    to ``_CORRESPONDENT_DEDUP_WINDOW_SECONDS`` (records must already be
    ``ts``-sorted, so each candidate is checked against the nearest
    prior occurrence of the same identity) — two genuinely repeated
    identical messages days apart both survive as distinct turns; only a
    near-simultaneous mirror collapses.

    The earliest-arriving copy survives — the gate that actually
    delivered first. Its ``duplicate_conversation_keys`` field names any
    sibling conversation key(s) that also carried the exchange, so a
    dedup never silently erases which pipes carried a turn.

    O(n) over *records*: one dict lookup and update per record, no
    second read of the conversation store.
    """
    kept: list[dict[str, Any]] = []
    last_seen: dict[tuple[str, ...], tuple[float, int]] = {}
    for record in records:
        identity = _correspondent_dedup_identity(record)
        if identity is None:
            kept.append(record)
            continue
        ts_epoch = _ts_epoch(record)
        prior = last_seen.get(identity)
        if (
            prior is not None
            and (ts_epoch - prior[0]) <= _CORRESPONDENT_DEDUP_WINDOW_SECONDS
        ):
            index = prior[1]
            survivor = kept[index]
            other_key = str(record.get("conversation_key") or "").strip()
            survivor_key = str(survivor.get("conversation_key") or "").strip()
            if other_key and other_key != survivor_key:
                dupes = list(survivor.get("duplicate_conversation_keys") or [])
                if other_key not in dupes:
                    dupes.append(other_key)
                    survivor = {**survivor, "duplicate_conversation_keys": dupes}
                    kept[index] = survivor
            last_seen[identity] = (ts_epoch, index)
            continue
        last_seen[identity] = (ts_epoch, len(kept))
        kept.append(record)
    return kept


def read_records_for_correspondent(
    brr_dir: Path,
    key: str,
    correspondent_key: str | None,
) -> list[dict[str, Any]]:
    """Return merged, deduped records for the current thread's correspondent.

    The active *key* is always included. When the correspondent is known,
    sibling conversation directories that have carried the same
    ``correspondent_key`` are merged too, with ``conversation_key`` added
    to returned records so prompt renderers can show which pipe a turn
    came through, sorted chronologically by ``ts``, and deduped so an
    exchange mirrored onto more than one gate (e.g. the cloud gate
    mirroring telegram) appears once — see :func:`_dedupe_woven_records`
    for the identity and window. When *correspondent_key* is falsy this
    is exactly the single-thread path (no merge, no dedup): only *key*'s
    own records, unchanged from before dedup existed.
    """
    if not correspondent_key:
        return [_tag_record(r, key) for r in read_records(brr_dir, key)]
    out: list[dict[str, Any]] = []
    for related in conversation_keys_for_correspondent(
        brr_dir, correspondent_key, include_key=key,
    ):
        out.extend(_tag_record(r, related) for r in read_records(brr_dir, related))
    out.sort(key=_ts_key)
    return _dedupe_woven_records(out)


def read_recent_for_correspondent(
    brr_dir: Path,
    key: str,
    correspondent_key: str | None,
    limit: int = 10,
    *,
    include_lifecycle: bool = False,
) -> list[dict[str, Any]]:
    """Return the ``limit`` most recent turns across a thread and its siblings.

    One merged, chronological, deduped stream feeds *limit* — a single
    budget for the whole woven set, not one per conversation key — so
    dedup always runs (:func:`read_records_for_correspondent`) before the
    tail is cut, and every slot in the returned tail is a distinct turn.
    """
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


def _schedule_repeat_key(record: dict[str, Any]) -> tuple[str, str] | None:
    """Return the source/body identity used to collapse schedule repeats."""
    if record.get("kind") != "event" or record.get("source") != "schedule":
        return None
    schedule_id = str(record.get("schedule_id") or "").strip()
    if not schedule_id:
        # Older records did not persist schedule_id. The default thread key
        # still gives those records a safe per-entry identity.
        conversation_key = str(record.get("conversation_key") or "")
        if conversation_key.startswith("schedule:"):
            schedule_id = conversation_key.removeprefix("schedule:").strip()
    body = record.get("body")
    if not schedule_id or not isinstance(body, str):
        return None
    return schedule_id, body


def _collapse_schedule_repeats(
    dialogue: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse exact repeated firings while retaining the newest body.

    Schedule events are self-originated and can recur indefinitely without
    receiving a reply artifact. Keep one bounded marker for older exact
    copies, but leave the newest full event and all non-identical or
    non-schedule dialogue untouched.
    """
    groups: dict[tuple[str, str], list[int]] = {}
    for index, record in enumerate(dialogue):
        key = _schedule_repeat_key(record)
        if key is not None:
            groups.setdefault(key, []).append(index)

    replacements: dict[int, dict[str, Any]] = {}
    discarded: set[int] = set()
    for (schedule_id, _body), indices in groups.items():
        if len(indices) < 2:
            continue
        oldest = indices[0]
        newest = indices[-1]
        older_count = len(indices) - 1
        first_ts = _ts_key(dialogue[oldest])
        # The marker describes only the discarded firings; the newest firing
        # remains as the full turn immediately after it.
        last_ts = _ts_key(dialogue[indices[-2]])
        span = f", {first_ts} → {last_ts}" if first_ts and last_ts else ""
        summary = _summary_for_body(
            f"({older_count} earlier identical firings of schedule:{schedule_id}"
            f"{span})"
        )
        marker = dict(dialogue[oldest])
        marker.pop("event_id", None)
        marker["body"] = summary
        marker["summary"] = summary
        marker["schedule_repeat_summary"] = True
        marker["schedule_repeat_count"] = older_count
        replacements[oldest] = marker
        discarded.update(indices[1:-1])

    return [
        replacements.get(index, record)
        for index, record in enumerate(dialogue)
        if index not in discarded
    ]


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
    if limit <= 0:
        return dialogue
    dialogue = _collapse_schedule_repeats(dialogue)
    total = len(dialogue)
    if total <= limit:
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
    sibling channels for the same correspondent, and the ``recent_limit``
    most recent dialogue turns across *all* of those channels, merged
    chronologically by ``ts`` into one budget for the whole woven set —
    never a separate budget per thread. When a correspondent is known,
    the merge is deduped (:func:`_dedupe_woven_records`) so an exchange
    mirrored onto more than one gate (the cloud gate mirroring telegram)
    contributes one turn, not one per pipe it rode in on; a deduped
    turn's ``duplicate_conversation_keys`` names the sibling pipe(s) it
    also arrived on. ``related_threads``' per-thread counts are each
    thread's own raw record count, not deduped — they describe what that
    thread's store actually holds. A bounded recent-tail copy of deeper
    history lives in separate JSONL files produced by
    :func:`write_grouped_history_files`; the full, permanent history for
    every thread stays in the base conversation store
    (:func:`conversation_path`), named in each group's ``store_path``
    when its tail copy was truncated.
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
    if correspondent_key:
        # Single-thread path (no correspondent) stays exactly as it was
        # before dedup existed — there is only one gate to mirror against.
        merged = _dedupe_woven_records(merged)
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


# Cap on records written per grouped-history file (#500). The base
# conversation store under conversations/<key>/ already retains every
# record permanently and cheaply (~15 MB across a live deployment); the
# per-run copy this function writes is a wake-time convenience, not the
# store of record, so it only needs a bounded recent tail. Before this
# cap, one run's copy of a single long-lived thread ran to thousands of
# records (one observed file: 7,868 records / 5.3 MB), multiplied by
# every non-worker wake on that thread — 847 run dirs, 1.7 GB, most of
# it the same records copied over and over.
HISTORY_GROUP_TAIL_LIMIT = 400


def write_grouped_history_files(
    brr_dir: Path,
    output_dir: Path,
    key: str,
    correspondent_key: str | None = None,
) -> list[HistoryGroup]:
    """Write bounded per-thread JSONL history files for a wake.

    Each file groups records by the input thread that produced them:
    native gate threads (Telegram / Slack / cloud relay channels) or
    forge threads (GitHub issue / PR conversations). Each file holds at
    most the latest ``HISTORY_GROUP_TAIL_LIMIT`` records for its thread
    (oldest-first, same order as before) — the full, permanent history
    for every thread stays in the base conversation store
    (``conversation_path``); it is never bounded or copied. A truncated
    group's descriptor carries ``total_record_count``, ``truncated``,
    and ``store_path`` so a reader whose snapshot is too thin can find
    what a bounded per-run copy dropped. A manifest JSON is written
    beside the JSONL files for machine consumers, while callers use the
    returned group descriptors for prompt/context rendering.
    """
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Deep-history tail: owner-only, same stance as the base store.
    output_dir.chmod(0o700)

    groups: list[HistoryGroup] = []
    for related in conversation_keys_for_correspondent(
        brr_dir, correspondent_key, include_key=key,
    ):
        records = [_tag_record(r, related) for r in read_records(brr_dir, related)]
        if not records:
            continue
        total = len(records)
        tail = (
            records[-HISTORY_GROUP_TAIL_LIMIT:]
            if total > HISTORY_GROUP_TAIL_LIMIT
            else records
        )
        source = _conversation_source_from_key(related)
        kind = _history_group_kind(source)
        filename = f"{kind}-{safe_dir_name(related)}.jsonl"
        path = output_dir / filename
        payload = "\n".join(json.dumps(r, sort_keys=True) for r in tail)
        path.write_text(payload + "\n", encoding="utf-8")
        path.chmod(0o600)
        group: HistoryGroup = {
            "id": f"{kind}:{related}",
            "kind": kind,
            "source": source,
            "conversation_key": related,
            "label": _history_group_label(source, related),
            "path": str(path),
            "record_count": len(tail),
            "total_record_count": total,
            "store_path": str(conversation_path(brr_dir, related)),
            "dialogue_count": sum(1 for r in tail if _is_dialogue_record(r)),
            "latest_ts": _ts_key(tail[-1]),
        }
        if total > len(tail):
            group["truncated"] = True
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
    schedule_id = str(event.get("schedule_id") or "").strip()
    if schedule_id:
        record["schedule_id"] = schedule_id
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
