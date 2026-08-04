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

from .. import stripe_api
from ..auth import get_db
from ..models import Account, Subscription

router = APIRouter(prefix="/v1/stats", tags=["stats"])

_CACHE_TTL_S = 60.0
_cache: dict[str, Any] = {"at": 0.0, "payload": None}


def _reset_cache() -> None:
    """Test seam: forget the cached payload."""
    _cache.update(at=0.0, payload=None)


# Stripe is the source of truth for what /pricing shows (#831): a
# server-side read of the Price the checkout actually charges, so the page
# and the invoice cannot silently drift. Long TTL — the catalog changes
# about never, and every cache miss is up to four live Stripe reads.
_PRICE_CACHE_TTL_S = 900.0
_price_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_PRICE_TIERS = {
    "supporter_monthly": "stripe_price_supporter_monthly",
    "supporter_annual": "stripe_price_supporter_annual",
    "public_monthly": "stripe_price_public_monthly",
    "public_annual": "stripe_price_public_annual",
}


def _reset_price_cache() -> None:
    """Test seam: forget the cached pricing payload."""
    _price_cache.update(at=0.0, payload=None)


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


@router.get("/pricing")
def pricing(request: Request) -> dict[str, Any]:
    """The subscription figures `/pricing` displays, read from the Stripe
    Price objects checkout actually charges rather than duplicated as
    literals (#831). USD only: no `currency_options` are set on these
    Prices yet (checked live against production 2026-07-28 — the field
    exists but only carries a `usd` entry), and the market is US-primary
    with no location signal decided (#831's design report: whether
    `CF-IPCountry` reaches the origin is unverified, and `/pricing` is a
    static file that reaches no backend code today regardless).

    A tier reads `None` when Stripe is unconfigured, its price ID is
    unset, or the Stripe call fails — absence, not an error the visitor
    must read; the caller keeps its baked-in USD literal as the floor
    (see `$lib/pricing.ts`).
    """
    now = time.monotonic()
    if _price_cache["payload"] is not None and now - _price_cache["at"] < _PRICE_CACHE_TTL_S:
        return _price_cache["payload"]
    settings = request.app.state.settings
    payload: dict[str, Any] = {}
    for tier, attr in _PRICE_TIERS.items():
        price_id = getattr(settings, attr, "")
        if not price_id:
            payload[tier] = None
            continue
        try:
            price = stripe_api.get_price(settings, price_id)
        except stripe_api.StripeError:
            payload[tier] = None
            continue
        amount, currency = price.get("unit_amount"), price.get("currency")
        payload[tier] = {"amount": amount, "currency": currency} if amount and currency else None
    _price_cache.update(at=now, payload=payload)
    return payload


@router.get("/version")
def deployed_version() -> dict[str, Any]:
    """The deployed build's identity — "is my merge live?" as one curl.

    Public and unauthenticated on purpose: it names a commit already public
    on the forge and a timestamp; no accounts, no per-account facts.
    """
    from ..version_info import build_info

    return build_info()
