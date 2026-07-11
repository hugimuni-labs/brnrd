"""Billing API surface (#53) — subscription + wallet endpoints.

The design-billing.md §"API surface" subset that makes test-mode dogfooding
possible: state reads, Checkout session creation, cancel/resume, Customer
Portal. Auto-topup and the refund request endpoint are follow-ups. All
state *transitions* ride the verified Stripe webhook, never these handlers.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import billing, schemas, stripe_api
from ..auth import Principal, get_db, require_account
from ..models import Account, BillingLedgerEntry, Subscription

router = APIRouter(prefix="/v1/accounts", tags=["billing"])


def _settings(request: Request):
    return request.app.state.settings


def _account(db: Session, principal: Principal) -> Account:
    account = db.get(Account, principal.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return account


def _live_subscription(db: Session, account_id: str) -> Subscription | None:
    return db.execute(
        select(Subscription)
        .where(
            Subscription.account_id == account_id,
            Subscription.status != Subscription.STATUS_CANCELED,
        )
        .order_by(Subscription.created_at.desc())
    ).scalars().first()


def _ensure_customer(db: Session, settings, account: Account) -> str:
    if account.stripe_customer_id:
        return account.stripe_customer_id
    customer = stripe_api.create_customer(settings, account_id=account.id, email=account.email)
    account.stripe_customer_id = customer.get("id")
    db.commit()
    return account.stripe_customer_id


def _subscription_out(subscription: Subscription | None, tier: str) -> schemas.SubscriptionOut:
    if subscription is None:
        return schemas.SubscriptionOut(tier=tier)
    return schemas.SubscriptionOut(
        tier=tier,
        status=subscription.status,
        cohort=subscription.cohort,
        cadence=subscription.cadence,
        cancel_at_period_end=subscription.cancel_at_period_end,
        current_period_end=subscription.current_period_end,
    )


@router.get("/subscription", response_model=schemas.SubscriptionOut)
def get_subscription(principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    account = _account(db, principal)
    return _subscription_out(_live_subscription(db, account.id), account.tier)


@router.post("/subscription/checkout", response_model=schemas.CheckoutOut)
def subscription_checkout(
    payload: schemas.SubscriptionCheckoutIn,
    request: Request,
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    settings = _settings(request)
    account = _account(db, principal)
    if _live_subscription(db, account.id) is not None:
        raise HTTPException(status_code=409, detail="account already has a subscription")
    price_id, cohort = billing.resolve_subscription_price(settings, db, payload.cadence)
    if not price_id:
        raise HTTPException(status_code=503, detail=f"no Stripe price configured for {cohort} {payload.cadence}")
    customer_id = _ensure_customer(db, settings, account)
    try:
        session = stripe_api.create_subscription_checkout(
            settings,
            customer_id=customer_id,
            price_id=price_id,
            account_id=account.id,
            success_url=f"{settings.public_base_url}/?billing=subscribed",
            cancel_url=f"{settings.public_base_url}/?billing=canceled",
        )
    except stripe_api.StripeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return schemas.CheckoutOut(checkout_url=session.get("url") or "", cohort=cohort)


@router.post("/subscription/cancel", response_model=schemas.SubscriptionOut)
def cancel_subscription(
    request: Request,
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    return _set_cancel(request, principal, db, cancel=True)


@router.post("/subscription/resume", response_model=schemas.SubscriptionOut)
def resume_subscription(
    request: Request,
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    return _set_cancel(request, principal, db, cancel=False)


def _set_cancel(request: Request, principal: Principal, db: Session, *, cancel: bool):
    settings = _settings(request)
    account = _account(db, principal)
    subscription = _live_subscription(db, account.id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="no active subscription")
    try:
        stripe_api.set_subscription_cancel_at_period_end(
            settings, subscription_id=subscription.stripe_subscription_id, cancel=cancel
        )
    except stripe_api.StripeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    # Optimistic local mirror; the subscription.updated webhook confirms.
    subscription.cancel_at_period_end = cancel
    db.commit()
    return _subscription_out(subscription, account.tier)


@router.post("/subscription/portal", response_model=schemas.PortalOut)
def customer_portal(
    request: Request,
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    settings = _settings(request)
    account = _account(db, principal)
    if not account.stripe_customer_id:
        raise HTTPException(status_code=404, detail="no Stripe customer for this account yet")
    try:
        session = stripe_api.create_portal_session(
            settings,
            customer_id=account.stripe_customer_id,
            return_url=f"{settings.public_base_url}/",
        )
    except stripe_api.StripeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return schemas.PortalOut(portal_url=session.get("url") or "")


@router.get("/wallet", response_model=schemas.WalletOut)
def get_wallet(principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    account = _account(db, principal)
    snapshot = billing.wallet_balances(db, account.id)
    return schemas.WalletOut(
        balances=snapshot["balances"],
        total_credits=snapshot["total"],
        cumulative_purchased_credits_lifetime=snapshot["cumulative_purchased_credits_lifetime"],
    )


@router.post("/wallet/checkout", response_model=schemas.CheckoutOut)
def wallet_checkout(
    payload: schemas.TopupCheckoutIn,
    request: Request,
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    settings = _settings(request)
    account = _account(db, principal)
    usd = payload.amount_usd
    if usd < settings.topup_min_usd or usd > settings.topup_max_usd:
        raise HTTPException(
            status_code=422,
            detail=f"top-up must be between ${settings.topup_min_usd} and ${settings.topup_max_usd}",
        )
    try:
        session = stripe_api.create_topup_checkout(
            settings,
            customer_id=account.stripe_customer_id,
            credits=usd * 100,  # $0.01/credit
            account_id=account.id,
            success_url=f"{settings.public_base_url}/?billing=topup-complete",
            cancel_url=f"{settings.public_base_url}/?billing=topup-canceled",
        )
    except stripe_api.StripeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return schemas.CheckoutOut(checkout_url=session.get("url") or "")


@router.get("/wallet/ledger", response_model=schemas.BillingLedgerList)
def wallet_ledger(
    limit: int = Query(default=50, ge=1, le=500),
    before_seq: int | None = Query(default=None),
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    query = select(BillingLedgerEntry).where(BillingLedgerEntry.account_id == principal.account_id)
    if before_seq is not None:
        query = query.where(BillingLedgerEntry.seq < before_seq)
    rows = list(db.execute(query.order_by(BillingLedgerEntry.seq.desc()).limit(limit)).scalars())
    entries = []
    for row in rows:
        try:
            metadata = json.loads(row.metadata_json or "{}")
        except ValueError:
            metadata = {}
        entries.append(
            schemas.BillingLedgerEntryOut(
                seq=row.seq,
                op=row.op,
                credits_delta=row.credits_delta,
                metadata=metadata if isinstance(metadata, dict) else {},
                created_at=row.created_at,
            )
        )
    return schemas.BillingLedgerList(entries=entries)
