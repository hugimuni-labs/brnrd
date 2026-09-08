"""the-parked-seat-has-two-buttons — the daemon-local half of a user-issued
run *respawn on another core*.

Sibling of ``run_stop_request.py`` / ``run_release_request.py`` (#476 wyrd
§3 and its follow-on). Same ledger shape — park an ack, never a directive —
with one addition: a respawn names a runner (``shell``/``core``), so the
served row and the ack both carry it through, unlike a stop or a release
which only ever need the run id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONSUMED_NAME = "run-respawn-consumed.json"


def _consumed_path(brr_dir: Path) -> Path:
    return brr_dir / _CONSUMED_NAME


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def consumed_ids(brr_dir: Path) -> list[str]:
    """Respawn ids dispatched here but not yet acked to the server."""
    try:
        data = json.loads(_consumed_path(brr_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if str(item).strip()]


def unhandled(brr_dir: Path, requests: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Filter a served batch down to respawns this daemon hasn't dispatched yet."""
    seen = set(consumed_ids(brr_dir))
    out: list[dict[str, str]] = []
    for request in requests or []:
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("request_id") or "").strip()
        run_id = str(request.get("run_id") or "").strip()
        if not request_id or not run_id or request_id in seen:
            continue
        out.append({
            "request_id": request_id,
            "run_id": run_id,
            "shell": str(request.get("shell") or "").strip(),
            "core": str(request.get("core") or "").strip(),
        })
    return out


def record_consumed(brr_dir: Path, request_id: str) -> None:
    """Mark a respawn dispatched: it rides the next publish as an ack."""
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
