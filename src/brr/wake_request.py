"""#328 tap-to-request — the daemon-local half of a spool-rack tap.

The server owns a tap end to end (``brnrd/wake_requests.py``): it mints the
row, expires it, and decides — in one transaction, at
``POST /v1/daemons/runners/wake-request/claim`` — whether a dispatching wake
spends it. This module is what remains on the daemon's side of that, and it
is deliberately almost nothing:

- ``.brr/wake-request.json`` — a **presence bit**, written by the cloud
  gate's publish tick (`gates/cloud.py::_publish_runners`) from the pending
  request the server hands back. It answers exactly one question: *is any
  tap parked for this daemon?* No mirror ⇒ dispatch makes no HTTP call at
  all, so the overwhelmingly common wake pays nothing and a local-only
  account never calls out.
- ``.brr/wake-request-receipt.json`` — the local trace of what became of a
  tap, written from the server's claim answer. The reason a refusal gives is
  the surface a human reads (and `facets.py` renders as
  ``resources.runner.wake_request``) to see that a tap existed and did not
  apply.

#733: this file used to hold a second opinion — a 900 s mirror TTL and a
120 s claim window, judged against a ``parked_at`` stamp — while the
dashboard chip truthfully reported the server's 24 h row TTL. Three
staleness horizons, and the smallest, least visible one decided; the
maintainer's tap died twice that way. A local replica of a fact its source
owns can disagree with its source, and does. So the horizons are gone
rather than reconciled: there is no correct second answer, so there is
nothing here to tune. The mirror lagging its source by up to a publish tick
is the only staleness left, and it is harmless — a stale id claims a row the
server has already decided, and the server says so.

Files are daemon-owned control state, not user surfaces. Writes are
atomic-rename; the cancel path is simply the server no longer returning the
request, upon which the mirror file is removed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PENDING_NAME = "wake-request.json"
_RECEIPT_NAME = "wake-request-receipt.json"


def _pending_path(brr_dir: Path) -> Path:
    return brr_dir / _PENDING_NAME


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


def pending_id(brr_dir: Path) -> str | None:
    """The id of the tap parked for this daemon, or None if none is.

    The whole presence bit. Deliberately *not* the tap's profile, repo, or
    park time: every one of those is a fact the server owns, and mirroring
    a fact you don't own is how #733 happened. Dispatch needs one thing from
    this file — an id to name in its claim — and gets exactly that; the
    claim's answer carries the rest, authoritatively.
    """
    data = _read_json(_pending_path(brr_dir))
    if not isinstance(data, dict):
        return None
    return str(data.get("request_id") or "").strip() or None


def store_pending(brr_dir: Path, request: dict[str, Any] | None) -> None:
    """Mirror the server's pending request (None ⇒ none pending ⇒ remove).

    No resurrect-guard: that existed only because the mirror lagged the
    daemon's own consumption ack, and there is no ack any more. What the
    server says is pending *is* pending — including a row the server
    re-offers because a claim of ours was refused.
    """
    path = _pending_path(brr_dir)
    request_id = str((request or {}).get("request_id") or "").strip()
    if not request_id:
        path.unlink(missing_ok=True)
        return
    if pending_id(brr_dir) == request_id:
        return  # unchanged; don't churn the file every tick
    _write_json(path, {"request_id": request_id})


def drop_pending(brr_dir: Path) -> None:
    """Forget the presence bit.

    Called only when the server's own claim answer reports the row is no
    longer ``pending``. Not a local judgement — a shortcut past waiting a
    publish tick for the mirror to catch up with what we were just told.
    """
    _pending_path(brr_dir).unlink(missing_ok=True)


def record_receipt(
    brr_dir: Path,
    request_id: str,
    *,
    source: str,
    event_id: str | None = None,
    profile: str | None = None,
    outcome: str = "consumed",
    reason: str | None = None,
) -> None:
    """#564/#733: the human-readable trace of what became of a tap.

    The server knows the answer; this is how the answer reaches the machine
    the tap was parked for. #733's other half was exactly that the
    distinction never left the server: a tap that lapsed and a tap that was
    spent looked identical from the daemon, and from the operator squinting
    at it.

    ``event_id`` is the *event*, not a run: the claim happens at dispatch,
    before a run exists, so there is no run id to record. Naming the field
    for what it actually holds is the point — a receipt that misnames its
    own subject is the failure it was built to prevent.

    ``outcome="consumed"`` (with ``reason=None``) is an applied tap;
    ``outcome="refused"`` carries the server's reason verbatim and
    ``profile=None``, so "this was asked for and never happened, here's why"
    reads unmistakably differently from "this was asked for and did." One
    requester parks at most one tap at a time, so only the latest outcome is
    live context — each call overwrites the last.

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
    if outcome and outcome != "consumed":
        payload["outcome"] = outcome
    if reason:
        payload["reason"] = reason
    _write_json(_receipt_path(brr_dir), payload)
    verb = "consumed" if outcome == "consumed" else outcome
    print(
        f"[brnrd] wake request {request_id} {verb} by "
        f"{payload['event_id'] or 'an unnamed event'} "
        f"(source={payload['source'] or 'unknown'}, "
        f"profile={payload['profile'] or 'unknown'}"
        f"{', reason=' + reason if reason else ''})"
    )


def last_receipt(brr_dir: Path) -> dict[str, Any] | None:
    """The most recent claim receipt, or None."""
    data = _read_json(_receipt_path(brr_dir))
    return data if isinstance(data, dict) else None
