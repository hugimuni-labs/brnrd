"""Shadow facts for inbox letters. Status remains the behavioural authority.

The sidecar sits beside its event so both inboxes and retention use the same
path. Each append is one O_APPEND write; a failed shadow write is diagnostic,
never a reason to undo a status transition.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


STATUS_STATE = {
    "pending": "pending", "processing": "claimed",
    "done": "answered", "delivered": "answered",
    "noted": "retired", "cancelled": "retired",
    "stopped": "retired", "error": "retired", "conflict": "retired",
}


def sidecar(event_path: Path) -> Path:
    return event_path.with_suffix(".facts.jsonl")


def drift_path(event_path: Path) -> Path:
    # Repo inbox: .brr/inbox; account inbox: home/dispatch/inbox.
    return event_path.parent.parent / "letters-drift.jsonl"


def _append(path: Path, row: dict) -> None:
    data = (json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def fold(event_path: Path) -> str | None:
    path = sidecar(event_path)
    if not path.exists():
        return None
    state = None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            fact = json.loads(line)
            if fact.get("v") != 1:
                raise ValueError(f"unsupported letter fact version: {fact.get('v')}")
            kind = fact["kind"]
            if kind == "released":
                state = "pending"
            elif kind in ("pending", "claimed", "answered", "retired"):
                state = kind
            else:
                raise ValueError(f"unknown letter fact: {kind}")
    return state


def compare(event_path: Path, status: str, site: str) -> str:
    expected = STATUS_STATE.get(status)
    try:
        state = fold(event_path)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        state = f"invalid:{type(exc).__name__}"
    verdict = "unknown" if state is None else "agree" if state == expected else "disagree"
    if verdict == "disagree":
        row = {"event": event_path.stem, "status": status, "fold": state,
               "site": site, "at": datetime.now(timezone.utc).isoformat()}
        try:
            _append(drift_path(event_path), row)
        except OSError as exc:
            print(f"[brnrd] letter drift log failed: {exc}", file=sys.stderr)
    return verdict


def shadow(event_path: Path, status: str, *, by: str, old_status: str | None = None,
           data: dict | None = None) -> None:
    """Append a fact and check it, without changing the status writer's result."""
    kind = STATUS_STATE.get(status)
    if kind is None:
        print(f"[brnrd] unknown letter status {status!r} at {by}", file=sys.stderr)
        return
    payload = dict(data or {})
    if status == "pending" and old_status is not None:
        kind = "released"
        payload.setdefault("why", "status returned to pending")
    elif kind == "retired":
        payload.setdefault("why", "noted" if status == "noted" else status)
    row = {"v": 1, "kind": kind, "by": by,
           "at": datetime.now(timezone.utc).isoformat(), "data": payload}
    try:
        _append(sidecar(event_path), row)
        from . import protocol
        actual = protocol.parse_frontmatter(event_path.read_text(encoding="utf-8")).get("status")
        compare(event_path, str(actual or ""), by)
    except Exception as exc:  # shadow failure must never block the status writer
        print(f"[brnrd] letter fact failed for {event_path.name}: {exc}", file=sys.stderr)


def check(inboxes: list[Path], drift_logs: list[Path]) -> tuple[dict[str, int], list[str], int]:
    """Inspect the current status projection and count historical drift rows."""
    from . import protocol

    counts = {"agree": 0, "disagree": 0, "unknown": 0}
    disagreements = []
    for inbox in inboxes:
        for path in sorted(inbox.glob("*.md")):
            event = protocol._read_event(path)
            if event is None:
                continue
            verdict = compare(path, str(event.get("status", "")), "letters.check")
            counts[verdict] += 1
            if verdict == "disagree":
                try:
                    state = fold(path)
                except (OSError, ValueError, KeyError) as exc:
                    state = f"invalid:{type(exc).__name__}"
                disagreements.append(f"{path}: status={event.get('status')} fold={state}")
    historical = 0
    for path in set(drift_logs):
        try:
            with path.open(encoding="utf-8") as stream:
                historical += sum(1 for _ in stream)
        except FileNotFoundError:
            pass
    return counts, disagreements, historical
