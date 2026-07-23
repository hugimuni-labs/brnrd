"""#328 tap-to-request — the daemon-local half of a spool-rack tap.

The server parks a one-shot "next wake on this profile" request
(``brnrd/wake_requests.py``); this module is the file protocol between the
two daemon threads that touch it locally:

- the **cloud gate** publish tick (`gates/cloud.py::_publish_runners`)
  mirrors the server's pending request into ``.brr/wake-request.json`` and
  reports consumed ids back on the next ``PUT /v1/daemons/runners``;
- the **dispatch loop** (`daemon.py`) applies the pending request as a
  one-shot runner override on the next wake — gated to non-``schedule``
  sources (#564: a scheduled wake is never the interactive one a tap was
  parked for) and only on an actual apply, never a drop — and moves its id
  to ``.brr/wake-request-consumed.json``, leaving a trace of who spent it
  in ``.brr/wake-request-receipt.json``.

Files are daemon-owned control state, not user surfaces. Writes are
atomic-rename; the cancel path is simply the server no longer returning
the request, upon which the mirror file is removed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PENDING_NAME = "wake-request.json"
_CONSUMED_NAME = "wake-request-consumed.json"
_RECEIPT_NAME = "wake-request-receipt.json"


def _pending_path(brr_dir: Path) -> Path:
    return brr_dir / _PENDING_NAME


def _consumed_path(brr_dir: Path) -> Path:
    return brr_dir / _CONSUMED_NAME


def _receipt_path(brr_dir: Path) -> Path:
    return brr_dir / _RECEIPT_NAME


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


def record_receipt(
    brr_dir: Path,
    request_id: str,
    *,
    source: str,
    event_id: str | None = None,
    profile: str | None = None,
) -> None:
    """#564: the human/dashboard-readable trace of *who* spent a request.

    ``consume()`` is the file-protocol spend — one id moved to the ack
    ledger, source-blind by design (it has to be: it's shared by every
    dispatch-time caller). That blindness is exactly what let a scheduled
    wake silently eat a dashboard tap parked for an interactive one, with
    zero trace anywhere. This is the trace: which event consumed the
    request and what woke it, so a future wake or the dashboard can tell
    "spent, and by what" instead of just "gone." One requester parks at
    most one pending request at a time, so only the latest consumption is
    live context — each call overwrites the last.

    ``event_id`` is the *event*, not a run: both call sites bind the tap
    before a run exists, so there is no run id to record. Naming the field
    for what it actually holds is the point — a receipt that misnames its
    own subject is the failure it was built to prevent.

    Also emitted as one stdout line, because a JSON file nothing reads is
    not yet a receipt: the daemon log is the surface an operator already
    watches when asking "where did my dashboard pick go?".
    """
    request_id = str(request_id or "").strip()
    if not request_id:
        return
    payload = {
        "request_id": request_id,
        "source": str(source or ""),
        "event_id": str(event_id or "") or None,
        "profile": str(profile or "") or None,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_json(_receipt_path(brr_dir), payload)
    print(
        f"[brnrd] wake request {request_id} consumed by "
        f"{payload['event_id'] or 'an unnamed event'} "
        f"(source={payload['source'] or 'unknown'}, "
        f"profile={payload['profile'] or 'unknown'})"
    )


def last_receipt(brr_dir: Path) -> dict[str, Any] | None:
    """The most recent consumption receipt, or None."""
    data = _read_json(_receipt_path(brr_dir))
    return data if isinstance(data, dict) else None


def clear_consumed(brr_dir: Path, acked: list[str]) -> None:
    """Drop ids the server has acknowledged (post-publish)."""
    if not acked:
        return
    remaining = [rid for rid in consumed_ids(brr_dir) if rid not in set(acked)]
    if remaining:
        _write_json(_consumed_path(brr_dir), remaining)
    else:
        _consumed_path(brr_dir).unlink(missing_ok=True)
