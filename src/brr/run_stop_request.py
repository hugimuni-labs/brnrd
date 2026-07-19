"""#476 wyrd §3 — the daemon-local half of a user-issued run stop.

The server parks a "stop that run" row (``brnrd/run_stop_requests.py``);
this module holds the one piece of local state that handshake needs: the
ledger of request ids this daemon has already dispatched into the kill
path but not yet acknowledged to the server.

Deliberately *thinner* than its sibling ``wake_request.py``. A wake request
has to be mirrored to disk because it is consumed by a different thread at
a different time (the cloud gate receives it; the dispatch loop spends it
on the next wake). A stop is consumed the moment it arrives — the kill is
synchronous and thread-safe from any caller — so there is nothing to park.
What must survive is the ack, because delivery and acknowledgement ride the
same endpoint one tick apart: without the ledger the server would re-serve
a stop we already dispatched, and we would kill a second run that happened
to inherit the handle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONSUMED_NAME = "run-stop-consumed.json"


def _consumed_path(brr_dir: Path) -> Path:
    return brr_dir / _CONSUMED_NAME


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def consumed_ids(brr_dir: Path) -> list[str]:
    """Stop ids dispatched here but not yet acked to the server."""
    try:
        data = json.loads(_consumed_path(brr_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if str(item).strip()]


def unhandled(brr_dir: Path, requests: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Filter a served batch down to stops this daemon hasn't dispatched yet."""
    seen = set(consumed_ids(brr_dir))
    out: list[dict[str, str]] = []
    for request in requests or []:
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("request_id") or "").strip()
        run_id = str(request.get("run_id") or "").strip()
        if not request_id or not run_id or request_id in seen:
            continue
        out.append({"request_id": request_id, "run_id": run_id})
    return out


def record_consumed(brr_dir: Path, request_id: str) -> None:
    """Mark a stop dispatched: it rides the next publish as an ack."""
    request_id = str(request_id or "").strip()
    if not request_id:
        return
    ids = consumed_ids(brr_dir)
    if request_id in ids:
        return
    ids.append(request_id)
    _write_json(_consumed_path(brr_dir), ids)


def clear_consumed(brr_dir: Path, acked: list[str]) -> None:
    """Drop ids the server has acknowledged (post-publish)."""
    if not acked:
        return
    remaining = [rid for rid in consumed_ids(brr_dir) if rid not in set(acked)]
    if remaining:
        _write_json(_consumed_path(brr_dir), remaining)
    else:
        _consumed_path(brr_dir).unlink(missing_ok=True)
