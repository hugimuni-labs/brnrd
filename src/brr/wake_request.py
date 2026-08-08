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
- ``.brr/wake-request-sticky.json`` — #932's conversation-sticky record:
  the profile a claimed tap applied, bound to the claiming lead event's
  correspondent/conversation key with a TTL. Daemon-owned outright (the
  server's row is already retired by the time this exists), so the #733
  don't-replicate-server-facts rule does not apply to it.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_PENDING_NAME = "wake-request.json"
_RECEIPT_NAME = "wake-request-receipt.json"
_STICKY_NAME = "wake-request-sticky.json"

# #932: how long a claimed tap stays bound to the conversation that claimed
# it. The maintainer priced this fork explicitly: the one-shot semantics are
# a cost-protection measure, and a plain sticky tap would inherit the
# forget-to-downgrade problem — so the expiry is load-bearing, not
# decoration. Two hours covers a photo album or a burst of follow-ups; the
# cheap config default returns on its own tomorrow. Override per repo with
# ``wake_request.sticky_ttl_seconds`` in ``.brr/config``.
STICKY_TTL_SECONDS = 2 * 60 * 60


def _pending_path(brr_dir: Path) -> Path:
    return brr_dir / _PENDING_NAME


def _receipt_path(brr_dir: Path) -> Path:
    return brr_dir / _RECEIPT_NAME


def _sticky_path(brr_dir: Path) -> Path:
    return brr_dir / _STICKY_NAME


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


def store_sticky(
    brr_dir: Path,
    *,
    request_id: str,
    profile: str,
    correspondent_key: str | None = None,
    conversation_key: str | None = None,
    claimed_at: str | None = None,
) -> None:
    """#932: bind a just-claimed tap to the conversation that claimed it.

    Written at the one claim point (`daemon._apply_dashboard_wake_request`)
    when the server applies a tap, and consulted only by later dispatches
    with *no* pending tap. Unlike the pending mirror this is not a replica
    of a server fact — the server retires the row at claim time; what the
    conversation inherits afterwards is a daemon-local promise, so the
    daemon owns this record outright, TTL included.

    ``correspondent_key`` is the preferred binding: it collapses
    ``cloud:telegram:…`` and ``telegram:…`` to one human (#930 is what raw
    thread keys did to menus). ``conversation_key`` is the fallback for
    events that carry no correspondent identity. A tap claimed by an event
    that yields *neither* cannot be inherited by anything, so it clears any
    previous record instead of leaving a stale promise behind — a new tap
    always replaces the record, even with nothing to bind to.

    Each call overwrites the last: one requester parks at most one tap at a
    time, and the newest tap owns the conversation.
    """
    profile = str(profile or "").strip()
    correspondent_key = str(correspondent_key or "").strip() or None
    conversation_key = str(conversation_key or "").strip() or None
    if not profile or not (correspondent_key or conversation_key):
        drop_sticky(brr_dir)
        return
    payload: dict[str, Any] = {
        "request_id": str(request_id or "").strip() or None,
        "profile": profile,
        "claimed_at": (
            str(claimed_at or "").strip()
            or datetime.now(timezone.utc).isoformat(timespec="seconds")
        ),
    }
    if correspondent_key:
        payload["correspondent_key"] = correspondent_key
    if conversation_key:
        payload["conversation_key"] = conversation_key
    _write_json(_sticky_path(brr_dir), payload)


def sticky_record(brr_dir: Path) -> dict[str, Any] | None:
    """The live conversation-sticky record, or None."""
    data = _read_json(_sticky_path(brr_dir))
    return data if isinstance(data, dict) else None


def sticky_ttl_seconds(cfg: dict | None) -> float:
    """#932: the sticky record's lifetime, config-overridable per repo.

    One resolver for every reader — dispatch
    (`daemon._apply_sticky_wake_profile`) and the dashboard publish tick
    (`gates/cloud._runners_snapshot`) must agree on when a sticky dies, or
    the rack renders a promise dispatch no longer honours (the #733 class:
    two staleness horizons, the invisible one deciding).
    """
    try:
        return float(
            (cfg or {}).get("wake_request.sticky_ttl_seconds", STICKY_TTL_SECONDS)
        )
    except (TypeError, ValueError):
        return float(STICKY_TTL_SECONDS)


def _parse_stamp(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def live_sticky_view(
    brr_dir: Path,
    ttl_seconds: float,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """The sticky record as a renderable claim, or None when it decides nothing.

    Returns ``{profile, claimed_at, expires_at, correspondent_key,
    conversation_key, request_id}`` (stamps ISO-8601, seconds precision) for
    a well-formed, unexpired record; ``None`` for absent, malformed, or
    expired. Read-only on purpose: dropping a dead record is dispatch's job
    (`daemon._apply_sticky_wake_profile`), because a publish tick must never
    race the dispatcher for the file's lifecycle.
    """
    record = sticky_record(brr_dir)
    if record is None:
        return None
    profile = str(record.get("profile") or "").strip()
    claimed_at = _parse_stamp(record.get("claimed_at"))
    if not profile or claimed_at is None:
        return None
    expires_at = claimed_at + timedelta(seconds=ttl_seconds)
    if (now or datetime.now(timezone.utc)) >= expires_at:
        return None
    view: dict[str, Any] = {
        "profile": profile,
        "claimed_at": claimed_at.isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds"),
    }
    for key in ("correspondent_key", "conversation_key", "request_id"):
        value = str(record.get(key) or "").strip()
        if value:
            view[key] = value
    return view


def drop_sticky(brr_dir: Path) -> None:
    """Forget the sticky record (expiry, or an unbindable replacement)."""
    _sticky_path(brr_dir).unlink(missing_ok=True)


def release_sticky(brr_dir: Path, requested_at_raw: Any) -> bool:
    """Honour a dashboard release ask (#932's exit tap), guarded by tense.

    Drops the record only when it was claimed at or before the ask — a tap
    claimed *after* the user pressed release is a newer decision and wins.
    Returns True when a record was dropped. An unparsable ask releases
    nothing (a guard may only assert what the run can be proven wrong
    about; a garbled stamp proves nothing).
    """
    requested_at = _parse_stamp(requested_at_raw)
    if requested_at is None:
        return False
    record = sticky_record(brr_dir)
    if record is None:
        return False
    claimed_at = _parse_stamp(record.get("claimed_at"))
    if claimed_at is not None and claimed_at > requested_at:
        return False
    drop_sticky(brr_dir)
    return True
