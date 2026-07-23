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

#577: the mirror is written by the cloud gate's own publish tick (every
``_DASHBOARD_PUBLISH_INTERVAL_S``), a clock unrelated to event pickup — a
tap parked in the same breath as the message it was meant for can lose that
race and land on disk *after* dispatch already read ``pending()`` and moved
on. ``parked_at`` closes that gap: the server already timestamps a tap the
moment it is minted (``RunnerWakeRequest.created_at``, carried on the wire
as ``requested_at`` — see ``brnrd/wake_requests.py::view``) and that value
usually precedes the message the maintainer sends right after tapping, even
though the *local mirror file* only appears on this daemon's disk a publish
tick later. Stamping the mirror with that server timestamp (not the local
write time — the local write time is exactly the delayed clock that causes
the race) is what lets ``claimable_for_event`` judge a late-landing tap
against the event it was actually parked for. Additive and optional: a
request whose payload carries no ``requested_at`` (an older server, a
daemon that predates this field) mirrors with no ``parked_at``, and
``claimable_for_event`` treats that as unconditionally claimable — the
pre-#577 behaviour, never a silently dropped tap.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PENDING_NAME = "wake-request.json"
_CONSUMED_NAME = "wake-request-consumed.json"
_RECEIPT_NAME = "wake-request-receipt.json"

# #577: how far *before* an event's ``created`` stamp a tap's ``parked_at``
# may fall and still be claimed by that event. Wide enough to absorb the
# cloud gate's publish-tick lag (seconds) plus normal dashboard-to-message
# composing time; narrow enough that a tap parked for an earlier wake
# doesn't ambush one the maintainer has stopped thinking about.
DEFAULT_CLAIM_WINDOW_S = 120.0
# A tap parked slightly *after* the event's ``created`` stamp is still the
# same tap-and-message "breath" more often than it is clock skew — small
# tolerance in that direction, not the full window.
_CLAIM_SKEW_TOLERANCE_S = 5.0

# #577: absolute staleness backstop, checked lazily on read (mirrors the
# server's own ``pending_for_account`` lazy-expiry pattern) — independent of
# whether any event ever tries to claim the tap. A tap nobody dispatches
# against for this long has outlived the wake it was parked for.
DEFAULT_TTL_S = 900.0


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


def pending(
    brr_dir: Path, *, ttl_seconds: float | None = None
) -> dict[str, Any] | None:
    """The mirrored pending wake request, or None.

    #577: when ``ttl_seconds`` is given and the request carries a
    ``parked_at`` older than that, it has outlived any wake it could
    plausibly have been meant for — lazily lapse it (mirrors the server's
    own lazy-expiry-on-read in ``wake_requests.pending_for_account``) and
    return ``None`` instead of handing back a tap that would otherwise
    ambush whatever unrelated wake reads next. ``ttl_seconds=None`` (the
    default) skips the check entirely — existing callers that don't pass it
    get the pre-#577 behaviour unchanged.
    """
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
    parked_at = str(data.get("parked_at") or "").strip()
    if parked_at:
        out["parked_at"] = parked_at
    if ttl_seconds is not None and parked_at:
        parked = _parse_iso(parked_at)
        if parked is not None:
            age = (datetime.now(timezone.utc) - parked).total_seconds()
            if age > ttl_seconds:
                lapse(
                    brr_dir, request_id,
                    source="ttl", reason=f"unclaimed for {int(age)}s (ttl {int(ttl_seconds)}s)",
                )
                return None
    return out


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def claimable_for_event(
    request: dict[str, Any],
    event_created: str | None,
    *,
    window_seconds: float = DEFAULT_CLAIM_WINDOW_S,
) -> bool:
    """#577: is ``request`` (from :func:`pending`) close enough in time to
    ``event_created`` (an event's ISO-8601 ``created`` stamp) to be the tap
    that event was meant to spend?

    Either timestamp missing or unparseable ⇒ claimable — we have nothing
    to judge the window against, so the pre-#577 behaviour (claim whatever
    is pending) wins rather than a parsing hiccup silently swallowing a
    tap. Otherwise claimable when ``parked_at`` falls up to
    ``window_seconds`` *before* ``event_created`` (the tap predates the
    message it rode in on, but the mirror file landed late), with a small
    tolerance the other way for ordinary clock/publish-tick jitter when the
    tap and the message are truly the same breath.
    """
    parked_at = str(request.get("parked_at") or "").strip()
    if not parked_at or not event_created:
        return True
    parked = _parse_iso(parked_at)
    created = _parse_iso(str(event_created))
    if parked is None or created is None:
        return True
    delta = (created - parked).total_seconds()
    return -_CLAIM_SKEW_TOLERANCE_S <= delta <= window_seconds


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
    # #577: the server timestamps a tap the moment it is minted
    # (``requested_at`` on the wire — see module docstring). Carry it
    # through as ``parked_at`` so a claim can be judged against the true
    # tap time, not this file's own — potentially publish-tick-delayed —
    # write time. Additive and optional: absent when the server payload
    # doesn't carry it (older server, older daemon).
    requested_at = str(request.get("requested_at") or "").strip()
    if requested_at:
        payload["parked_at"] = requested_at
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
    outcome: str = "consumed",
    reason: str | None = None,
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

    ``outcome``/``reason`` (#577): the default ``outcome="consumed"`` with
    ``reason=None`` reproduces the pre-#577 payload shape exactly (neither
    key is written) — every existing caller and test is unaffected. A
    :func:`lapse` call passes ``outcome="lapsed"`` and a reason, so the
    receipt can say "this tap existed and did not apply, here's why"
    instead of looking identical to a successful spend.

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


def lapse(
    brr_dir: Path,
    request_id: str,
    *,
    source: str,
    event_id: str | None = None,
    reason: str,
) -> None:
    """#577: expire a pending tap that never got applied to a profile.

    Mechanically identical to :func:`consume` (move the id to the consumed
    ledger so it doesn't resurrect on the next mirror tick, drop the
    pending file) — the wire protocol has no separate "expired, never
    used" signal to give the server, and adding one is out of scope here
    (server-side protocol changes are explicitly not this fix). The
    receipt is where the distinction actually lives: ``profile=None`` and
    ``outcome="lapsed"`` read unmistakably differently from a real spend,
    so a human or the dashboard can tell "this was asked for and never
    happened" apart from "this was asked for and did."
    """
    request_id = str(request_id or "").strip()
    if not request_id:
        return
    ids = consumed_ids(brr_dir)
    if request_id not in ids:
        ids.append(request_id)
        _write_json(_consumed_path(brr_dir), ids)
    _pending_path(brr_dir).unlink(missing_ok=True)
    record_receipt(
        brr_dir, request_id, source=source, event_id=event_id, profile=None,
        outcome="lapsed", reason=reason,
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
