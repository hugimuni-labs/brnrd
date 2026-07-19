"""Durable per-run outbound messages in the private brnrd home.

The runtime outbox and response files remain compatibility/staging seams.  A
message's durable identity and delivery state live here, under the run that
produced it; a successful or impossible delivery rewrites frontmatter in
place instead of deleting the only copy.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import account, protocol
from .run import list_runs


MESSAGES_PATH = "messages"
PENDING = "pending"
DELIVERED = "delivered"
COLLECTED = "collected"
UNDELIVERABLE = "undeliverable"
# ``collected`` is the dispatch-edge counterpart of ``delivered``: a worker's
# terminal report is consumed by the parent run that spawned it, not by a
# gate. Both carry a receipt, so both stamp the same receipt fields.
_RECEIPTED = {DELIVERED, COLLECTED}
_TERMINAL = _RECEIPTED | {UNDELIVERABLE}
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_WRITE_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def run_messages_dir(
    ctx: account.HomeContext,
    repo_label: str,
    run_id: str,
) -> Path:
    return account.run_dir(ctx, repo_label, run_id) / MESSAGES_PATH


def _render(meta: dict[str, object], body: str) -> str:
    lines = ["---"]
    lines.extend(f"{key}: {_clean(value)}" for key, value in meta.items() if value not in (None, ""))
    lines.extend(["---", "", body.strip(), ""])
    return "\n".join(lines)


def read(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta = protocol.parse_frontmatter(text)
    if not meta.get("status"):
        return None
    meta["body"] = protocol.frontmatter_body(text).strip()
    meta["_path"] = path
    return meta


def list_messages(messages_dir: Path, *, status: str | None = None) -> list[dict[str, Any]]:
    if not messages_dir.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(messages_dir.glob("*.md")):
        message = read(path)
        if message and (status is None or message.get("status") == status):
            result.append(message)
    return result


def _existing_by_source(messages_dir: Path, source_ref: str) -> Path | None:
    if not source_ref:
        return None
    for message in list_messages(messages_dir):
        if message.get("source_ref") == source_ref:
            return message["_path"]
    return None


def stage(
    ctx: account.HomeContext,
    *,
    repo_label: str,
    run_id: str,
    body: str,
    kind: str,
    target_event: str = "",
    target_gate: str = "",
    target_thread: str = "",
    source_ref: str = "",
    status: str = PENDING,
    reason: str = "",
    created_at: str | None = None,
) -> Path | None:
    """Create one durable outbound message; ``source_ref`` makes retries idempotent."""

    text = (body or "").strip()
    if not text or not run_id:
        return None
    messages_dir = run_messages_dir(ctx, repo_label, run_id)
    messages_dir.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        existing = _existing_by_source(messages_dir, source_ref)
        if existing is not None:
            return existing
        seqs = [int(path.name.split("-", 1)[0]) for path in messages_dir.glob("*.md") if path.name.split("-", 1)[0].isdigit()]
        seq = max(seqs, default=0) + 1
        safe_kind = _SAFE.sub("-", kind).strip("-.") or "message"
        path = messages_dir / f"{seq:06d}-{safe_kind}.md"
        meta = {
            "direction": "out",
            "status": status,
            "created_at": created_at or _now(),
            "kind": safe_kind,
            "target_event": target_event,
            "target_gate": target_gate,
            "target_thread": target_thread,
            "source_ref": source_ref,
            "reason": reason,
        }
        protocol._atomic_write(path, _render(meta, text))
        return path


def transition(
    path: Path,
    status: str,
    *,
    gate: str = "",
    platform_message_id: object = "",
    reason: str = "",
    delivered_at: str | None = None,
) -> bool:
    """Move a pending message to one terminal state, idempotently."""

    if status not in _TERMINAL:
        raise ValueError(f"invalid message terminal status: {status}")
    with _WRITE_LOCK:
        message = read(path)
        if message is None:
            return False
        current = str(message.get("status") or "")
        if current in _TERMINAL:
            return current == status
        if current != PENDING:
            return False
        meta = {k: v for k, v in message.items() if k not in {"body", "_path"}}
        meta["status"] = status
        if status in _RECEIPTED:
            meta["platform_gate"] = gate
            meta["platform_message_id"] = platform_message_id
            meta["delivered_at"] = delivered_at or _now()
        else:
            meta["reason"] = reason or "no configured gate owns this target"
        protocol._atomic_write(path, _render(meta, str(message.get("body") or "")))
        return True


def receipt_id(receipt: object) -> str:
    """Extract a mundane platform identifier from common gate return shapes."""

    if receipt in (None, ""):
        return ""
    if isinstance(receipt, (str, int)):
        return str(receipt)
    if not isinstance(receipt, dict):
        return str(receipt)
    result = receipt.get("result")
    if isinstance(result, dict):
        for key in ("message_id", "id", "ts"):
            if result.get(key) not in (None, ""):
                return str(result[key])
    for key in ("message_id", "id", "ts", "url", "html_url"):
        if receipt.get(key) not in (None, ""):
            return str(receipt[key])
    return ""


def message_path_from_queue(path: Path) -> Path | None:
    try:
        meta = protocol.parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    raw = str(meta.get("message_path") or "").strip()
    return Path(raw) if raw else None


def _artifact_owners(conversations_dir: Path) -> dict[str, str]:
    owners: dict[str, str] = {}
    if not conversations_dir.is_dir():
        return owners
    for path in conversations_dir.rglob("*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            source = str(record.get("path") or "")
            run_id = str(record.get("run_id") or "")
            if source and run_id and ".partials/" in source:
                owners[source] = run_id
    return owners


def resolve_stranded(
    ctx: account.HomeContext,
    *,
    repo_label: str,
    gate_owned: Callable[[str], bool],
) -> dict[str, int]:
    """Retire records left ``pending`` by a gate that never existed (#454).

    Before the dispatch-tree sources learned to resolve their own records,
    a reply addressed to an event no gate owns was staged ``pending`` and
    nothing ever moved it — the residue #454 tracked after #459 made the
    class visible. Two populations wear that one status:

    * a worker's terminal report (``target_gate: spawn``), which the
      spawning parent *did* read along the dispatch edge → ``collected``;
    * everything else — interims to ``spawn``/``spawn_completed``/
      ``dispatch_message``/``schedule`` events, which nothing consumes →
      ``undeliverable``.

    Boot-only is the right cadence *because the producers are fixed*: no
    new stranded record can be created, so this only ever has legacy rows
    to find. It is idempotent — a terminal record is never revisited.
    """
    counts = {"collected": 0, "undeliverable": 0}
    runs_root = ctx.runs_dir / account.slug_repo_label(repo_label)
    if not runs_root.is_dir():
        return counts
    for messages_dir in sorted(runs_root.glob(f"*/{MESSAGES_PATH}")):
        for message in list_messages(messages_dir, status=PENDING):
            gate = str(message.get("target_gate") or "")
            if not gate or gate_owned(gate):
                continue
            if gate == "spawn" and message.get("kind") == "terminal":
                if transition(
                    message["_path"], COLLECTED,
                    gate="dispatch-edge", platform_message_id="legacy-sweep",
                ):
                    counts["collected"] += 1
            elif transition(
                message["_path"], UNDELIVERABLE,
                reason=f"no gate owns {gate} events (resolved by the #454 sweep)",
            ):
                counts["undeliverable"] += 1
    return counts


def migrate_legacy(
    ctx: account.HomeContext,
    *,
    repo_root: Path,
    repo_label: str,
    brr_dir: Path,
) -> dict[str, int]:
    """Import orphan partials and knowledge replies; safe to run every boot."""

    counts = {"partials": 0, "replies": 0}
    runs = {run.event_id: run.id for run in list_runs(brr_dir / "runs")}
    owners = _artifact_owners(brr_dir / "conversations")
    for source in sorted((brr_dir / "responses").glob("*.partials/*.md")):
        linked = message_path_from_queue(source)
        if linked is not None and read(linked) is not None:
            # New-store carriers are retained deliberately. Migration only
            # owns the pre-store files that have no durable message pointer.
            continue
        event_id = source.parent.name.removesuffix(".partials")
        run_id = owners.get(str(source)) or runs.get(event_id) or f"unowned-{event_id}"
        try:
            body = protocol.read_partial(source) or ""
            created = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            continue
        messages_dir = run_messages_dir(ctx, repo_label, run_id)
        existed = _existing_by_source(messages_dir, str(source)) is not None
        path = stage(
            ctx,
            repo_label=repo_label,
            run_id=run_id,
            body=body,
            kind="interim",
            target_event=event_id,
            source_ref=str(source),
            status=UNDELIVERABLE,
            reason="legacy orphaned partial had no live gate owner",
            created_at=created,
        )
        if path is not None and not existed:
            counts["partials"] += 1

    old_replies = account.knowledge_path(ctx) / account.REPLIES_PATH / account.slug_repo_label(repo_label)
    if old_replies.is_dir():
        for source in sorted(old_replies.glob("*.md")):
            message = read(source)
            body = str(message.get("body") if message else protocol.frontmatter_body(source.read_text(encoding="utf-8"))).strip()
            old_meta = protocol.parse_frontmatter(source.read_text(encoding="utf-8"))
            run_id = str(old_meta.get("run") or source.stem)
            messages_dir = run_messages_dir(ctx, repo_label, run_id)
            existed = _existing_by_source(messages_dir, str(source)) is not None
            path = stage(
                ctx,
                repo_label=repo_label,
                run_id=run_id,
                body=body,
                kind="terminal",
                target_event=str(old_meta.get("event") or ""),
                target_gate=str(old_meta.get("source") or ""),
                target_thread=str(old_meta.get("thread") or ""),
                source_ref=str(source),
                status=PENDING,
                created_at=str(old_meta.get("delivered_at") or "") or None,
            )
            if path is not None and not existed:
                transition(
                    path,
                    DELIVERED,
                    gate=str(old_meta.get("source") or "legacy-archive"),
                    delivered_at=str(old_meta.get("delivered_at") or "") or None,
                )
                counts["replies"] += 1
    return counts
