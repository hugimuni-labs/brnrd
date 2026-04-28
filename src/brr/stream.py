"""Stream — workstream coordination object.

A workstream is a named line of work with intent, current state, gate
context, tasks, runs, and artifacts. It aggregates related events,
tasks, runs, and artifacts so agents and humans can reason about a
*line of work* rather than individual one-shot tasks.

Runtime layout under ``.brr/streams/``::

    .brr/streams/
        index.json                       — gate-thread → stream_id index
        <stream-id>/
            stream.md                    — manifest (frontmatter + body)
            events.ndjson                — append-only event records
            tasks.ndjson                 — append-only task records
            artifacts.ndjson             — append-only artifact records

Streams are runtime state — they live beside other ``.brr/`` data and
should not be committed. Durable knowledge still belongs in ``kb/``.
The stream layer indexes and summarises but does not replace it.
"""

from __future__ import annotations

import json
import os
import random
import string
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STREAM_STATUSES = ("active", "paused", "done", "archived")
DEFAULT_REPLY_PREFERENCE = "input_gate"
DEFAULT_REPLY_ALLOWED = ("input_gate", "stream_default", "git_pr")


# ── ID + atomic helpers ──────────────────────────────────────────────


def _generate_stream_id() -> str:
    ts = int(time.time())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"stream-{ts}-{rand}"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── Locations ────────────────────────────────────────────────────────


def streams_root(brr_dir: Path) -> Path:
    return brr_dir / "streams"


def stream_dir(brr_dir: Path, stream_id: str) -> Path:
    return streams_root(brr_dir) / stream_id


def manifest_path(brr_dir: Path, stream_id: str) -> Path:
    return stream_dir(brr_dir, stream_id) / "stream.md"


def events_path(brr_dir: Path, stream_id: str) -> Path:
    return stream_dir(brr_dir, stream_id) / "events.ndjson"


def tasks_path(brr_dir: Path, stream_id: str) -> Path:
    return stream_dir(brr_dir, stream_id) / "tasks.ndjson"


def artifacts_path(brr_dir: Path, stream_id: str) -> Path:
    return stream_dir(brr_dir, stream_id) / "artifacts.ndjson"


def index_path(brr_dir: Path) -> Path:
    return streams_root(brr_dir) / "index.json"


# ── Gate thread fingerprints ─────────────────────────────────────────


def gate_thread_key(meta: dict[str, Any]) -> str | None:
    """Return a stable string key for a gate thread, or None.

    The key is used to map repeat events from the same conversational
    thread (Telegram topic, Slack thread, git tasks/<file>) to the same
    stream. Returns None when the event carries no gate context that
    can serve as a stable thread anchor.
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


# ── Index ────────────────────────────────────────────────────────────


def load_index(brr_dir: Path) -> dict[str, str]:
    path = index_path(brr_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    return {}


def save_index(brr_dir: Path, mapping: dict[str, str]) -> None:
    _atomic_write_text(index_path(brr_dir), json.dumps(mapping, indent=2, sort_keys=True) + "\n")


def index_set(brr_dir: Path, key: str, stream_id: str) -> None:
    mapping = load_index(brr_dir)
    if mapping.get(key) == stream_id:
        return
    mapping[key] = stream_id
    save_index(brr_dir, mapping)


def index_get(brr_dir: Path, key: str) -> str | None:
    return load_index(brr_dir).get(key)


# ── Manifest ─────────────────────────────────────────────────────────


@dataclass
class StreamManifest:
    """In-memory representation of ``stream.md``."""

    id: str
    title: str = ""
    status: str = "active"
    intent: str = ""
    summary: str = ""
    open_questions: str = ""
    default_branch: str | None = None
    created: str = ""
    updated: str = ""
    gate_context: dict[str, Any] = field(default_factory=dict)
    reply_route: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        lines = ["---", f"id: {self.id}"]
        if self.title:
            lines.append(f"title: {self.title}")
        lines.append(f"status: {self.status}")
        if self.intent:
            lines.append(f"intent: {self.intent}")
        if self.default_branch:
            lines.append(f"default_branch: {self.default_branch}")
        if self.created:
            lines.append(f"created: {self.created}")
        if self.updated:
            lines.append(f"updated: {self.updated}")
        if self.gate_context:
            lines.append("gate_context:")
            for k in sorted(self.gate_context):
                lines.append(f"  {k}: {self.gate_context[k]}")
        if self.reply_route:
            lines.append("reply_route:")
            for k in ("preferred", "selected"):
                if k in self.reply_route:
                    lines.append(f"  {k}: {self.reply_route[k]}")
            allowed = self.reply_route.get("allowed")
            if isinstance(allowed, (list, tuple)):
                lines.append(f"  allowed: {','.join(str(a) for a in allowed)}")
            elif allowed:
                lines.append(f"  allowed: {allowed}")
        for k in sorted(self.extra):
            lines.append(f"{k}: {self.extra[k]}")
        lines.append("---")

        body_parts: list[str] = []
        if self.summary:
            body_parts.append("## Current summary\n\n" + self.summary.strip())
        if self.open_questions:
            body_parts.append("## Open questions\n\n" + self.open_questions.strip())
        body = "\n\n".join(body_parts).strip()
        return "\n".join(lines) + "\n" + (body + "\n" if body else "")

    @classmethod
    def from_text(cls, text: str) -> StreamManifest | None:
        from . import protocol

        fm = protocol.parse_frontmatter(text)
        if not fm.get("id"):
            return None
        body = protocol.frontmatter_body(text)
        summary, open_questions = _split_manifest_body(body)
        gate_context = fm.get("gate_context") if isinstance(fm.get("gate_context"), dict) else {}
        reply_route_raw = fm.get("reply_route") if isinstance(fm.get("reply_route"), dict) else {}
        reply_route: dict[str, Any] = dict(reply_route_raw) if reply_route_raw else {}
        if "allowed" in reply_route and isinstance(reply_route["allowed"], str):
            reply_route["allowed"] = [s.strip() for s in reply_route["allowed"].split(",") if s.strip()]
        known = {
            "id", "title", "status", "intent", "default_branch",
            "created", "updated", "gate_context", "reply_route",
        }
        extra = {k: v for k, v in fm.items() if k not in known}
        return cls(
            id=str(fm["id"]),
            title=str(fm.get("title", "")),
            status=str(fm.get("status", "active")),
            intent=str(fm.get("intent", "")),
            summary=summary,
            open_questions=open_questions,
            default_branch=fm.get("default_branch") or None,
            created=str(fm.get("created", "")),
            updated=str(fm.get("updated", "")),
            gate_context=gate_context,
            reply_route=reply_route,
            extra=extra,
        )


def _split_manifest_body(body: str) -> tuple[str, str]:
    """Split a manifest body into (summary, open_questions)."""
    summary = ""
    open_q = ""
    section: str | None = None
    buffer: list[str] = []

    def _flush() -> None:
        nonlocal summary, open_q, buffer
        text = "\n".join(buffer).strip()
        if section == "summary":
            summary = text
        elif section == "open":
            open_q = text
        buffer = []

    for line in body.splitlines():
        low = line.strip().lower()
        if low.startswith("## current summary"):
            _flush()
            section = "summary"
            continue
        if low.startswith("## open questions"):
            _flush()
            section = "open"
            continue
        if section is not None:
            buffer.append(line)
    _flush()
    return summary, open_q


def load_manifest(brr_dir: Path, stream_id: str) -> StreamManifest | None:
    path = manifest_path(brr_dir, stream_id)
    if not path.exists():
        return None
    return StreamManifest.from_text(path.read_text(encoding="utf-8"))


def save_manifest(brr_dir: Path, manifest: StreamManifest) -> Path:
    manifest.updated = _now_iso()
    if not manifest.created:
        manifest.created = manifest.updated
    path = manifest_path(brr_dir, manifest.id)
    _atomic_write_text(path, manifest.to_text())
    return path


def touch_manifest(brr_dir: Path, stream_id: str) -> None:
    """Refresh a stream manifest's updated timestamp if it exists."""
    manifest = load_manifest(brr_dir, stream_id)
    if manifest is None:
        return
    save_manifest(brr_dir, manifest)


# ── Title generation ─────────────────────────────────────────────────


def _slugify_title(text: str, max_words: int = 8) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return "Untitled stream"
    words = cleaned.split(" ")[:max_words]
    title = " ".join(words)
    if len(title) > 80:
        title = title[:77].rstrip() + "…"
    return title


def default_reply_route(source: str | None = None) -> dict[str, Any]:
    return {
        "preferred": DEFAULT_REPLY_PREFERENCE,
        "allowed": list(DEFAULT_REPLY_ALLOWED),
        "selected": DEFAULT_REPLY_PREFERENCE,
    }


# ── Resolution ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class StreamResolution:
    """Outcome of resolving an event to a stream."""

    stream_id: str
    created: bool
    reason: str  # explicit | thread | task | fallback
    thread_key: str | None = None


def _normalize_stream_id_hint(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def resolve_for_event(
    brr_dir: Path,
    event: dict[str, Any],
    *,
    related_task_stream: str | None = None,
) -> StreamResolution:
    """Resolve *event* to a stream, creating one when needed.

    Resolution order:

    1. Explicit ``stream_id`` in event metadata.
    2. Existing task / branch reference (``related_task_stream``).
    3. Gate-thread fingerprint via ``index.json``.
    4. Fallback: create a new stream.
    """
    explicit = _normalize_stream_id_hint(event.get("stream_id"))
    if explicit:
        if not (stream_dir(brr_dir, explicit)).exists():
            _create_stream(brr_dir, explicit, event)
            return StreamResolution(stream_id=explicit, created=True, reason="explicit")
        return StreamResolution(stream_id=explicit, created=False, reason="explicit")

    if related_task_stream:
        if (stream_dir(brr_dir, related_task_stream)).exists():
            return StreamResolution(
                stream_id=related_task_stream, created=False, reason="task",
            )

    thread_key = gate_thread_key(event)
    if thread_key:
        existing = index_get(brr_dir, thread_key)
        if existing and (stream_dir(brr_dir, existing)).exists():
            return StreamResolution(
                stream_id=existing, created=False, reason="thread",
                thread_key=thread_key,
            )

    new_id = _generate_stream_id()
    _create_stream(brr_dir, new_id, event)
    if thread_key:
        index_set(brr_dir, thread_key, new_id)
    return StreamResolution(
        stream_id=new_id, created=True, reason="fallback",
        thread_key=thread_key,
    )


def _create_stream(brr_dir: Path, stream_id: str, event: dict[str, Any]) -> None:
    body = (event.get("body") or "").strip()
    title = _slugify_title(body or stream_id)
    intent_line = body.split("\n", 1)[0].strip() if body else ""
    if len(intent_line) > 240:
        intent_line = intent_line[:237].rstrip() + "…"
    gate_context: dict[str, Any] = {}
    source = event.get("source")
    if source:
        gate_context["source"] = source
    for key in (
        "telegram_chat_id", "telegram_topic_id", "telegram_user",
        "slack_channel", "slack_thread_ts", "slack_ts", "slack_user",
        "git_file", "git_commit",
    ):
        if event.get(key) not in (None, ""):
            gate_context[key] = event[key]
    manifest = StreamManifest(
        id=stream_id,
        title=title,
        status="active",
        intent=intent_line,
        gate_context=gate_context,
        reply_route=default_reply_route(source if isinstance(source, str) else None),
    )
    save_manifest(brr_dir, manifest)


# ── Append-only records ──────────────────────────────────────────────


def append_event(brr_dir: Path, stream_id: str, event: dict[str, Any]) -> None:
    record = {
        "ts": _now_iso(),
        "event_id": event.get("id", ""),
        "source": event.get("source", ""),
        "thread_key": gate_thread_key(event),
        "summary": (event.get("body") or "").strip().splitlines()[0] if event.get("body") else "",
    }
    _append_jsonl(events_path(brr_dir, stream_id), record)
    touch_manifest(brr_dir, stream_id)


def append_task(
    brr_dir: Path,
    stream_id: str,
    *,
    task_id: str,
    event_id: str,
    branch: str,
    env: str,
    status: str,
    base_branch: str | None = None,
    branch_name: str | None = None,
) -> None:
    record = {
        "ts": _now_iso(),
        "task_id": task_id,
        "event_id": event_id,
        "branch": branch,
        "branch_name": branch_name,
        "base_branch": base_branch,
        "env": env,
        "status": status,
    }
    _append_jsonl(tasks_path(brr_dir, stream_id), record)
    touch_manifest(brr_dir, stream_id)


def append_artifact(
    brr_dir: Path,
    stream_id: str,
    *,
    kind: str,
    path: str,
    task_id: str | None = None,
    label: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    record: dict[str, Any] = {
        "ts": _now_iso(),
        "kind": kind,
        "path": path,
    }
    if task_id:
        record["task_id"] = task_id
    if label:
        record["label"] = label
    if extra:
        record.update(extra)
    _append_jsonl(artifacts_path(brr_dir, stream_id), record)
    touch_manifest(brr_dir, stream_id)


# ── Listing / read helpers ───────────────────────────────────────────


def list_streams(brr_dir: Path) -> list[StreamManifest]:
    root = streams_root(brr_dir)
    if not root.exists():
        return []
    out: list[StreamManifest] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        manifest = load_manifest(brr_dir, entry.name)
        if manifest is not None:
            out.append(manifest)
    return out


def read_events(brr_dir: Path, stream_id: str) -> list[dict[str, Any]]:
    return _read_jsonl(events_path(brr_dir, stream_id))


def read_tasks(brr_dir: Path, stream_id: str) -> list[dict[str, Any]]:
    return _read_jsonl(tasks_path(brr_dir, stream_id))


def read_artifacts(brr_dir: Path, stream_id: str) -> list[dict[str, Any]]:
    return _read_jsonl(artifacts_path(brr_dir, stream_id))


# ── Reply-route enforcement ──────────────────────────────────────────


def normalize_reply_route(
    requested: dict[str, Any] | None,
    *,
    stream_route: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Merge an agent-requested reply route with stream + default policy.

    The input gate is the default and always wins ties unless the agent
    explicitly recommends an alternative *and* that alternative is in
    the stream's allowed list.
    """
    base = dict(stream_route) if stream_route else default_reply_route(source)
    allowed_raw = base.get("allowed") or list(DEFAULT_REPLY_ALLOWED)
    if isinstance(allowed_raw, str):
        allowed = [s.strip() for s in allowed_raw.split(",") if s.strip()]
    else:
        allowed = list(allowed_raw)
    base["allowed"] = allowed
    base.setdefault("preferred", DEFAULT_REPLY_PREFERENCE)
    base.setdefault("selected", base["preferred"])

    if not requested:
        return base

    suggestion = requested.get("preferred") or requested.get("selected")
    suggestion = (str(suggestion).strip() if suggestion else "")
    if suggestion and suggestion in allowed:
        base["selected"] = suggestion
    return base
