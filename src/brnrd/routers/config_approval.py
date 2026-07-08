"""Loom-envelope Phase 2 — structured config-key change approval device-flow.

Mirrors ``pairing.py``'s ``PairRequest`` shape: a daemon mints a request,
the account owner approves or rejects it from a browser (session-cookie
gated, see ``brnrd_web/routes.py::config_approve_page``/``config_approve_submit``),
and the outcome rides back to the daemon over the *existing*
``GET /v1/daemons/inbox`` long-poll — the same channel any other
cloud-origin chat message already uses (``src/brr/gates/cloud.py::_loop_once``)
— as an ordinary inbox event whose body is ``approve config-change
<proposal_id>`` / ``reject config-change <proposal_id>``, the same reply
convention CS6's runner-policy proposals already use
(``src/brr/daemon.py::_runner_policy_reply``). No new daemon-side polling
loop, no new push channel: see
``kb/design-multi-workstream-concurrency.md`` §"Named forks — round 2",
sub-decision 3.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ids, inbox as inbox_service, schemas
from ..auth import Principal, get_db, require_daemon
from ..models import ConfigChangeRequest

router = APIRouter(prefix="/v1/daemons/config-requests", tags=["config-approval"])

# Sub-decision 1 (kb/design-multi-workstream-concurrency.md §"Named forks —
# round 2"): start narrow, one key, rather than "any `.brr/config` key" —
# that's a materially bigger trust-boundary call than this fork settled.
# Widen deliberately by editing this set, not by habit.
ALLOWED_CONFIG_KEYS = {"spawn.max_concurrent"}

# A week — long enough that an AFK-heavy account owner (the maintainer's
# own stated working style, kb/log.md 2026-07-06) doesn't lose a real
# request to a short TTL before they've even seen it.
_REQUEST_TTL_S = 7 * 24 * 3600


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@router.post("", response_model=schemas.ConfigChangeRequestOut)
def create_config_change_request(
    payload: schemas.ConfigChangeRequestCreate,
    request: Request,
    principal: Principal = Depends(require_daemon),
    db: Session = Depends(get_db),
):
    key = payload.config_key.strip()
    if key not in ALLOWED_CONFIG_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"config key '{key}' is not agent-proposable (allowed: {sorted(ALLOWED_CONFIG_KEYS)})",
        )
    settings = request.app.state.settings
    row = ConfigChangeRequest(
        id=ids.config_change_request_id(),
        account_id=principal.account_id,
        repo_id=principal.repo_id,
        proposal_id=payload.proposal_id.strip(),
        config_key=key,
        current_value=payload.current_value,
        requested_value=payload.requested_value,
        reason=payload.reason,
        status=ConfigChangeRequest.STATUS_PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=_REQUEST_TTL_S),
    )
    db.add(row)
    db.commit()
    approve_url = f"{settings.public_base_url.rstrip('/')}/config-approve/{row.id}"
    return schemas.ConfigChangeRequestOut(request_id=row.id, status=row.status, approve_url=approve_url)


@router.get("/{request_id}", response_model=schemas.ConfigChangeRequestOut)
def get_config_change_request(
    request_id: str,
    principal: Principal = Depends(require_daemon),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(ConfigChangeRequest).where(ConfigChangeRequest.id == request_id)
    ).scalar_one_or_none()
    if row is None or row.repo_id != principal.repo_id:
        raise HTTPException(status_code=404, detail="unknown config-change request")
    return schemas.ConfigChangeRequestOut(request_id=row.id, status=row.status, approve_url=None)


def decide_core(db: Session, account_id: str, request_id: str, *, approve: bool) -> ConfigChangeRequest:
    """Apply a browser decision; used by the session-gated web route.

    Enqueuing the outcome as a plain inbox ``Event`` (rather than writing
    anywhere under the daemon's local ``dispatch/inbox``, which this server
    process cannot reach — that directory lives on the user's own machine)
    is the load-bearing choice here: it rides the exact channel Telegram/
    GitHub messages already use to reach a daemon, so the daemon needs no
    new polling loop to learn the outcome.
    """
    row = db.execute(
        select(ConfigChangeRequest).where(ConfigChangeRequest.id == request_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown config-change request")
    if row.account_id != account_id:
        raise HTTPException(status_code=403, detail="not your config-change request")
    if row.status != ConfigChangeRequest.STATUS_PENDING:
        return row
    if _aware(row.expires_at) < datetime.now(timezone.utc):
        row.status = ConfigChangeRequest.STATUS_EXPIRED
        db.commit()
        return row
    row.status = ConfigChangeRequest.STATUS_APPROVED if approve else ConfigChangeRequest.STATUS_REJECTED
    row.decided_at = datetime.now(timezone.utc)
    db.commit()
    verb = "approve" if approve else "reject"
    inbox_service.enqueue(
        db,
        repo_id=row.repo_id,
        body=f"{verb} config-change {row.proposal_id}",
        source="cloud",
        reply_to={},
    )
    return row
