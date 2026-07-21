"""#328 tap-to-request — the daemon-local half of a spool-rack tap.

The server parks a one-shot "next wake on this profile" request
(``brnrd/wake_requests.py``); this module is the file protocol between the
two daemon threads that touch it locally:

- the **cloud gate** publish tick (`gates/cloud.py::_publish_runners`)
  mirrors the server's pending request into ``.brr/wake-request.json`` and
  reports consumed ids back on the next ``PUT /v1/daemons/runners``;
- the **dispatch loop** (`daemon.py`) applies the pending request as a
  one-shot runner override on the next wake and moves its id to
  ``.brr/wake-request-consumed.json``.

Files are daemon-owned control state, not user surfaces. Writes are
atomic-rename; the cancel path is simply the server no longer returning
the request, upon which the mirror file is removed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PENDING_NAME = "wake-request.json"
_CONSUMED_NAME = "wake-request-consumed.json"


def _pending_path(brr_dir: Path) -> Path:
    return brr_dir / _PENDING_NAME


def _consumed_path(brr_dir: Path) -> Path:
    return brr_dir / _CONSUMED_NAME


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pending(brr_dir: Path) -> dict[str, Any] | None:
    """The mirrored pending wake request, or None."""
    data = _read_json(_pending_path(brr_dir))
    if not isinstance(data, dict):
        return None
    request_id = str(data.get("request_id") or "").strip()
    profile = str(data.get("profile") or "").strip()
    if not request_id or not profile:
        return None
    out = {"request_id": request_id, "profile": profile}
    for key in ("repo_label", "environment"):
        value = str(data.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def store_pending(brr_dir: Path, request: dict[str, Any] | None) -> None:
    """Mirror the server's pending request (None ⇒ none pending ⇒ remove).

    A request whose id is already in the consumed ledger is *not*
    resurrected: the server simply hasn't processed our ack yet — one
    publish tick of lag is expected, a double consume is not.
    """
    path = _pending_path(brr_dir)
    if not request:
        path.unlink(missing_ok=True)
        return
    request_id = str(request.get("request_id") or "").strip()
    profile = str(request.get("profile") or "").strip()
    if not request_id or not profile or request_id in consumed_ids(brr_dir):
        path.unlink(missing_ok=True)
        return
    current = pending(brr_dir)
    if current and current["request_id"] == request_id:
        return  # unchanged; don't churn the file every tick
    payload = {"request_id": request_id, "profile": profile}
    for key in ("repo_label", "environment"):
        value = str(request.get(key) or "").strip()
        if value:
            payload[key] = value
    _write_json(path, payload)


def consume(brr_dir: Path, request_id: str) -> None:
    """Spend the pending request: move its id to the consumed ledger."""
    request_id = str(request_id or "").strip()
    if not request_id:
        return
    ids = consumed_ids(brr_dir)
    if request_id not in ids:
        ids.append(request_id)
        _write_json(_consumed_path(brr_dir), ids)
    _pending_path(brr_dir).unlink(missing_ok=True)


def consumed_ids(brr_dir: Path) -> list[str]:
    """Consumed ids not yet acked to the server."""
    data = _read_json(_consumed_path(brr_dir))
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if str(item).strip()]


def clear_consumed(brr_dir: Path, acked: list[str]) -> None:
    """Drop ids the server has acknowledged (post-publish)."""
    if not acked:
        return
    remaining = [rid for rid in consumed_ids(brr_dir) if rid not in set(acked)]
    if remaining:
        _write_json(_consumed_path(brr_dir), remaining)
    else:
        _consumed_path(brr_dir).unlink(missing_ok=True)
