"""Public, unauthenticated stats for the landing surface (#509).

Coarse counters only — totals, never identities or per-account facts.
Cached in-process for a minute so anonymous landing traffic cannot become
a database hammer; the numbers move slowly enough that staleness is
invisible at this granularity.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_db
from ..models import Account, Subscription

router = APIRouter(prefix="/v1/stats", tags=["stats"])

_CACHE_TTL_S = 60.0
_cache: dict[str, Any] = {"at": 0.0, "payload": None}


def _reset_cache() -> None:
    """Test seam: forget the cached payload."""
    _cache.update(at=0.0, payload=None)


@router.get("/public")
def public_stats(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    now = time.monotonic()
    if _cache["payload"] is not None and now - _cache["at"] < _CACHE_TTL_S:
        return _cache["payload"]
    settings = request.app.state.settings
    accounts = db.execute(select(func.count()).select_from(Account)).scalar_one()
    supporters = db.execute(
        select(func.count())
        .select_from(Subscription)
        .where(
            Subscription.cohort == Subscription.COHORT_SUPPORTER,
            Subscription.status != Subscription.STATUS_CANCELED,
        )
    ).scalar_one()
    payload = {
        "accounts": int(accounts),
        "supporter_seats_total": int(settings.supporter_cohort_size),
        "supporter_seats_taken": int(supporters),
    }
    _cache.update(at=now, payload=payload)
    return payload
