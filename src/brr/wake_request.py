"""#328 tap-to-request — the daemon-local half of a spool-rack tap.

The server parks a one-shot "next wake on this profile" request
(``brnrd/wake_requests.py``); this module is the file protocol between the
two daemon threads that touch it locally:

- the **cloud gate** publish tick (`gates/cloud.py::_publish_runners`)
  mirrors the server's pending request into ``.brr/wake-request.json`` and
  reports decided ids back on the next ``PUT /v1/daemons/runners`` — spent
  ones as ``consumed_wake_request_ids``, expired ones as
  ``lapsed_wake_request_ids`` (#733: one list used to carry both, so every
  expiry was reported as a spend);
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

#733: staleness has exactly one owner, and it is the server. The row's
``expires_at`` rides the wire beside ``requested_at``, mirrors into the
pending file, and ``expired()`` is the only thing that reads it. Previously
this module kept its own 900 s horizon while the server's row lived 24 h and
the dashboard chip reported pending against *that* — so the surface the
maintainer reloaded to confirm his tap had held was not the surface that
decided, and the two disagreed by 96x. The claim window's upper bound was a
third copy of the same judgement (120 s), and being the smallest number it was
the one that actually fired; it is gone. What survives of #577 is the
direction that answers a different question — "was this tap parked after the
event already existed?" — which no expiry can answer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PENDING_NAME = "wake-request.json"
_CONSUMED_NAME = "wake-request-consumed.json"
_LAPSED_NAME = "wake-request-lapsed.json"
_RECEIPT_NAME = "wake-request-receipt.json"

# A tap parked slightly *after* the event's ``created`` stamp is still the
# same tap-and-message "breath" more often than it is clock skew — small
# tolerance in that direction.
#
# #733: this is all that is left of what #577 called the *claim window*, and
# the surviving half is the one that was ever doing work. The two directions
# answer different questions:
#
# - *after* the event: "was this tap parked when the event already existed?"
#   A tap minted while a wake is already queued was not parked for that wake
#   and must wait for the next one. Structural, cheap, and kept.
# - *before* the event: "is this tap too old for this event?" — which is the
#   same question the tap's own expiry answers, and it was answered with a
#   second, much smaller number: 120 s against the server's 24 h. A human who
#   taps the rack and then composes a message takes longer than two minutes,
#   so the guard fired on the exact behaviour the feature exists to serve. It
#   ate a real tap 16 minutes after minting (#733) while the dashboard the
#   maintainer reloaded truthfully reported it pending. Deleted, not widened:
#   staleness now has exactly one owner, ``expires_at``, checked in
#   :func:`expired`.
_CLAIM_SKEW_TOLERANCE_S = 5.0


def _pending_path(brr_dir: Path) -> Path:
    return brr_dir / _PENDING_NAME


def _consumed_path(brr_dir: Path) -> Path:
    return brr_dir / _CONSUMED_NAME


def _lapsed_path(brr_dir: Path) -> Path:
    return brr_dir / _LAPSED_NAME


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
    """The mirrored pending wake request, or None.

    A read, and only a read. #733: this used to take a ``ttl_seconds`` and
    lapse a stale request *inside* the accessor, which made every miss
    invisible to the surface built to report it — a lapse returned ``None``,
    so ``daemon.py``'s ``wake_request_report`` was never built and
    ``body_provenance`` stayed ``None``. #577's "you asked for X, got Y,
    because Z" line was structurally blind to its single most common outcome.

    Staleness now belongs to :func:`expired`, which the callers ask
    explicitly, so the tap is still in hand when the report is composed.
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
    for key in ("parked_at", "expires_at"):
        value = str(data.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def expired(request: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Has this tap outlived the window the server gave it?

    #733: one horizon, and it is the server's. ``RunnerWakeRequest`` carries
    an ``expires_at`` the row is minted with, the server's own lazy sweep
    already honours it (``wake_requests.pending_for_account``), and the
    dashboard chip reports pending against it. This daemon used to keep a
    *second*, independent horizon — a hardcoded 900 s — so the surface that
    answered "is my tap still live?" (up to 24 h, truthfully) was not the
    surface that decided (15 minutes). 96x apart, and reloading the chip to
    confirm the tap held was both the correct instinct and unable to work.

    A mirror with no ``expires_at`` is never expired here: an older server
    that doesn't send the field leaves staleness where it already lives — the
    server simply stops returning the request, and ``store_pending`` removes
    the mirror on the next publish tick. Inventing a local horizon to fill
    that silence is the bug this function replaced.
    """
    raw = str(request.get("expires_at") or "").strip()
    if not raw:
        return False
    expires = _parse_iso(raw)
    if expires is None:
        return False
    return expires < (now or datetime.now(timezone.utc))


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
) -> bool:
    """Was ``request`` parked *before* the event that wants to spend it?

    #577 asked this as "close enough in time", with a window in both
    directions. #733 kept only the direction that was doing work: a tap minted
    after an event was already created was not parked for that event — the
    maintainer tapped while a wake was already queued, and meant the next one.
    ``_CLAIM_SKEW_TOLERANCE_S`` allows the small overlap where a tap and the
    message it rode in on are genuinely the same breath and the clocks
    disagree.

    The other direction — "too old for this event" — is deliberately gone. It
    duplicated :func:`expired` with a number 720x smaller and, being the
    smaller number, it was the one that decided. That is what ate the tap in
    #733.

    Either timestamp missing or unparseable ⇒ claimable: nothing to judge
    against, so a parsing hiccup must not silently swallow a tap.
    """
    parked_at = str(request.get("parked_at") or "").strip()
    if not parked_at or not event_created:
        return True
    parked = _parse_iso(parked_at)
    created = _parse_iso(str(event_created))
    if parked is None or created is None:
        return True
    return (created - parked).total_seconds() >= -_CLAIM_SKEW_TOLERANCE_S


def store_pending(brr_dir: Path, request: dict[str, Any] | None) -> None:
    """Mirror the server's pending request (None ⇒ none pending ⇒ remove).

    A request whose id is already in the consumed *or* lapsed ledger is not
    resurrected: the server simply hasn't processed our ack yet — one
    publish tick of lag is expected, a double consume is not. (#733 split the
    two ledgers; both are "already decided locally, awaiting ack", so both
    block a resurrection. Checking only one would let an expired tap reappear
    on the very next tick.)
    """
    path = _pending_path(brr_dir)
    if not request:
        path.unlink(missing_ok=True)
        return
    request_id = str(request.get("request_id") or "").strip()
    profile = str(request.get("profile") or "").strip()
    decided = set(consumed_ids(brr_dir)) | set(lapsed_ids(brr_dir))
    if not request_id or not profile or request_id in decided:
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
    # #733: and the expiry the server minted the row with, so local staleness
    # has one authority instead of a second hardcoded horizon. Optional in the
    # same way ``parked_at`` is: an older server sends neither, and the local
    # side then simply has no opinion about staleness (see :func:`expired`).
    expires_at = str(request.get("expires_at") or "").strip()
    if expires_at:
        payload["expires_at"] = expires_at
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
    """Consumed ids not yet acked to the server — genuinely *spent* taps."""
    return _ledger_ids(_consumed_path(brr_dir))


def lapsed_ids(brr_dir: Path) -> list[str]:
    """Lapsed ids not yet acked — taps that existed and never applied.

    #733: separate from :func:`consumed_ids` because the two are different
    facts and the server has different words for them. They used to share one
    list, so every lapse was reported to ``wake_requests.mark_consumed``,
    whose own docstring says *"these requests were spent on a dispatched
    wake."* It wasn't. ``RunnerWakeRequest.STATUS_EXPIRED`` already existed
    for the server's own sweep; the daemon simply had no field to route a
    lapse into. This is that field.
    """
    return _ledger_ids(_lapsed_path(brr_dir))


def _ledger_ids(path: Path) -> list[str]:
    data = _read_json(path)
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

    #733: this now says so *on the wire*, not only in a local file. It used to
    move the id into the same ledger :func:`consume` writes, and defended that
    on the grounds that "the receipt is where the distinction actually lives…
    so a human or the dashboard can tell." Neither could: the receipt is
    ``.brr/wake-request-receipt.json``, a file that never leaves the machine,
    and the dashboard reads the server row — which the shared ledger had just
    reported as ``consumed``. A comment claiming more than the code beneath it
    can do, in the exact place a reader goes to check whether the loss was
    deliberate.

    So the id goes to the *lapsed* ledger (:func:`lapsed_ids`), rides its own
    field on the next publish, and lands as ``STATUS_EXPIRED`` server-side. The
    local receipt keeps its extra detail — which event, which source, why — but
    it is no longer the only place the truth exists.
    """
    request_id = str(request_id or "").strip()
    if not request_id:
        return
    ids = lapsed_ids(brr_dir)
    if request_id not in ids:
        ids.append(request_id)
        _write_json(_lapsed_path(brr_dir), ids)
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
    """Drop consumed ids the server has acknowledged (post-publish)."""
    _clear_ledger(_consumed_path(brr_dir), acked)


def clear_lapsed(brr_dir: Path, acked: list[str]) -> None:
    """Drop lapsed ids the server has acknowledged (post-publish)."""
    _clear_ledger(_lapsed_path(brr_dir), acked)


def _clear_ledger(path: Path, acked: list[str]) -> None:
    if not acked:
        return
    remaining = [rid for rid in _ledger_ids(path) if rid not in set(acked)]
    if remaining:
        _write_json(path, remaining)
    else:
        path.unlink(missing_ok=True)
