"""Billing core (#53): wallet ledger ops + Stripe webhook state machine.

Implements the kb design-billing.md contract on the brnrd side:

- bucketed credit wallet (``CreditBucket`` + append-only ``BillingLedgerEntry``
  audit rows); grants/top-ups land here, the debit machinery is #54;
- subscription mirror (``Subscription``) driven exclusively by verified
  Stripe webhook events — brnrd never flips ``Account.tier`` from its own
  API handlers;
- supporter→public cohort cutoff for new checkouts (existing subscribers
  keep their signup-time Price; grandfathering is Stripe-native).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import ids
from .config import Settings
from .models import Account, BillingLedgerEntry, CreditBucket, Subscription

# Stripe subscription status → local mirror status.
_STATUS_MAP = {
    "active": Subscription.STATUS_ACTIVE,
    "trialing": Subscription.STATUS_ACTIVE,
    "past_due": Subscription.STATUS_PAST_DUE,
    "unpaid": Subscription.STATUS_CANCELED,
    "canceled": Subscription.STATUS_CANCELED,
    "incomplete": Subscription.STATUS_CANCELED,
    "incomplete_expired": Subscription.STATUS_CANCELED,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _naive_utc(dt: datetime) -> datetime:
    # Model DateTime columns store naive UTC (matching models._utcnow usage
    # with SQLite); normalize aware datetimes on the way in.
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# --- ledger -----------------------------------------------------------------


def ledger_append(
    db: Session,
    account_id: str,
    op: str,
    *,
    credits_delta: int = 0,
    bucket_id: str | None = None,
    metadata: dict | None = None,
) -> BillingLedgerEntry:
    entry = BillingLedgerEntry(
        id=ids.billing_ledger_id(),
        account_id=account_id,
        op=op,
        credits_delta=credits_delta,
        bucket_id=bucket_id,
        metadata_json=json.dumps(metadata or {}, sort_keys=True),
    )
    db.add(entry)
    return entry


def grant_bucket(
    db: Session,
    account_id: str,
    *,
    source: str,
    credits: int,
    op: str,
    expires_at: datetime | None = None,
    stripe_ref: str | None = None,
    metadata: dict | None = None,
) -> CreditBucket | None:
    """Create a credit bucket + audit row; idempotent on ``stripe_ref``.

    Returns ``None`` when the ref was already granted (webhook redelivery).
    """
    if stripe_ref:
        existing = db.execute(
            select(CreditBucket).where(CreditBucket.stripe_ref == stripe_ref)
        ).scalar_one_or_none()
        if existing is not None:
            return None
    bucket = CreditBucket(
        id=ids.credit_bucket_id(),
        account_id=account_id,
        source=source,
        granted_credits=credits,
        remaining_credits=credits,
        expires_at=_naive_utc(expires_at) if expires_at else None,
        stripe_ref=stripe_ref,
    )
    db.add(bucket)
    ledger_append(
        db, account_id, op, credits_delta=credits, bucket_id=bucket.id, metadata=metadata
    )
    return bucket


def expire_buckets(db: Session, account_id: str, source: str, *, op: str) -> int:
    """Zero out remaining credits in all live buckets of ``source``."""
    expired = 0
    rows = db.execute(
        select(CreditBucket).where(
            CreditBucket.account_id == account_id,
            CreditBucket.source == source,
            CreditBucket.remaining_credits > 0,
        )
    ).scalars()
    for bucket in rows:
        remaining = bucket.remaining_credits
        bucket.remaining_credits = 0
        expired += remaining
        ledger_append(
            db, account_id, op, credits_delta=-remaining, bucket_id=bucket.id
        )
    return expired


def wallet_balances(db: Session, account_id: str) -> dict:
    now = _utcnow().replace(tzinfo=None)
    rows = list(
        db.execute(
            select(CreditBucket).where(
                CreditBucket.account_id == account_id,
                CreditBucket.remaining_credits > 0,
            )
        ).scalars()
    )
    by_source: dict[str, int] = {}
    for bucket in rows:
        if bucket.expires_at is not None and bucket.expires_at <= now:
            continue  # lazily-expired; #54's sweep writes the audit rows
        by_source[bucket.source] = by_source.get(bucket.source, 0) + bucket.remaining_credits
    purchased_lifetime = db.execute(
        select(func.coalesce(func.sum(CreditBucket.granted_credits), 0)).where(
            CreditBucket.account_id == account_id,
            CreditBucket.source == CreditBucket.SOURCE_PURCHASED,
        )
    ).scalar_one()
    return {
        "balances": by_source,
        "total": sum(by_source.values()),
        "cumulative_purchased_credits_lifetime": int(purchased_lifetime),
    }


# --- cohort / price resolution ----------------------------------------------


def resolve_subscription_price(settings: Settings, db: Session, cadence: str) -> tuple[str, str]:
    """Pick the Price for a *new* checkout → ``(price_id, cohort)``.

    Supporter until ``supporter_cohort_size`` non-canceled supporter
    subscriptions exist or the optional deadline passes, then public.
    """
    cohort = Subscription.COHORT_SUPPORTER
    count = db.execute(
        select(func.count()).select_from(Subscription).where(
            Subscription.cohort == Subscription.COHORT_SUPPORTER,
            Subscription.status != Subscription.STATUS_CANCELED,
        )
    ).scalar_one()
    if count >= settings.supporter_cohort_size:
        cohort = Subscription.COHORT_PUBLIC
    elif settings.supporter_cohort_deadline:
        try:
            deadline = datetime.fromisoformat(settings.supporter_cohort_deadline)
        except ValueError:
            deadline = None
        if deadline is not None and _naive_utc(_utcnow()) >= _naive_utc(deadline):
            cohort = Subscription.COHORT_PUBLIC
    if cohort == Subscription.COHORT_SUPPORTER:
        price_id = (
            settings.stripe_price_supporter_monthly
            if cadence == "monthly"
            else settings.stripe_price_supporter_annual
        )
    else:
        price_id = (
            settings.stripe_price_public_monthly
            if cadence == "monthly"
            else settings.stripe_price_public_annual
        )
    return price_id, cohort


def _cohort_for_price(settings: Settings, price_id: str) -> str:
    if price_id in (settings.stripe_price_public_monthly, settings.stripe_price_public_annual):
        return Subscription.COHORT_PUBLIC
    return Subscription.COHORT_SUPPORTER


# --- webhook state machine ----------------------------------------------------


def _account_for_event(db: Session, *, metadata: dict | None, customer_id: str | None) -> Account | None:
    account_id = (metadata or {}).get("brnrd_account_id")
    if account_id:
        account = db.get(Account, account_id)
        if account is not None:
            return account
    if customer_id:
        return db.execute(
            select(Account).where(Account.stripe_customer_id == customer_id)
        ).scalar_one_or_none()
    return None


def _subscription_fields(obj: dict) -> tuple[str, str, datetime | None]:
    """Extract (price_id, cadence, current_period_end) across API versions."""
    items = ((obj.get("items") or {}).get("data")) or [{}]
    first = items[0] if items else {}
    price = first.get("price") or {}
    price_id = price.get("id") or ""
    interval = ((price.get("recurring") or {}).get("interval")) or ""
    cadence = "annual" if interval == "year" else "monthly"
    period_end_ts = obj.get("current_period_end") or first.get("current_period_end")
    period_end = (
        datetime.fromtimestamp(period_end_ts, tz=timezone.utc).replace(tzinfo=None)
        if period_end_ts
        else None
    )
    return price_id, cadence, period_end


def _refresh_tier(db: Session, account: Account) -> None:
    db.flush()  # sessions run autoflush=False; make pending rows countable
    live = db.execute(
        select(func.count()).select_from(Subscription).where(
            Subscription.account_id == account.id,
            Subscription.status != Subscription.STATUS_CANCELED,
        )
    ).scalar_one()
    account.tier = Account.TIER_SUBSCRIBED if live else Account.TIER_FREE


def handle_stripe_event(db: Session, settings: Settings, event: dict) -> str:
    """Apply one verified Stripe event; returns a short disposition string.

    Caller owns idempotency (``StripeEvent``) and the commit.
    """
    event_type = event.get("type") or ""
    obj = ((event.get("data") or {}).get("object")) or {}

    if event_type == "checkout.session.completed":
        return _on_checkout_completed(db, settings, obj)
    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        return _on_subscription_upserted(db, settings, obj)
    if event_type == "customer.subscription.deleted":
        return _on_subscription_deleted(db, obj)
    if event_type == "invoice.paid":
        return _on_invoice_paid(db, settings, obj)
    if event_type == "invoice.payment_failed":
        return _on_invoice_payment_failed(db, obj)
    if event_type == "charge.refunded":
        return _on_charge_refunded(db, obj)
    return "ignored"


def _on_checkout_completed(db: Session, settings: Settings, obj: dict) -> str:
    metadata = obj.get("metadata") or {}
    account = _account_for_event(db, metadata=metadata, customer_id=obj.get("customer"))
    if account is None:
        return "no-account"
    customer_id = obj.get("customer")
    if customer_id and not account.stripe_customer_id:
        account.stripe_customer_id = customer_id
    if metadata.get("brnrd_purpose") == "wallet_topup" and obj.get("mode") == "payment":
        try:
            credits = int(metadata.get("brnrd_credits") or 0)
        except ValueError:
            credits = 0
        amount_total = obj.get("amount_total")
        if credits <= 0 and isinstance(amount_total, int):
            credits = amount_total  # $0.01/credit → cents == credits
        if credits <= 0:
            return "topup-no-credits"
        bucket = grant_bucket(
            db,
            account.id,
            source=CreditBucket.SOURCE_PURCHASED,
            credits=credits,
            op="topup",
            stripe_ref=obj.get("payment_intent") or obj.get("id"),
            metadata={"checkout_session": obj.get("id")},
        )
        return "topup-granted" if bucket else "topup-duplicate"
    # Subscription checkout: customer attach only; the subscription events
    # carry the state machine.
    return "customer-attached"


def _on_subscription_upserted(db: Session, settings: Settings, obj: dict) -> str:
    metadata = obj.get("metadata") or {}
    account = _account_for_event(db, metadata=metadata, customer_id=obj.get("customer"))
    if account is None:
        return "no-account"
    price_id, cadence, period_end = _subscription_fields(obj)
    status = _STATUS_MAP.get(obj.get("status") or "", Subscription.STATUS_CANCELED)
    stripe_subscription_id = obj.get("id") or ""
    row = db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    ).scalar_one_or_none()
    created = row is None
    if created:
        row = Subscription(
            id=ids.subscription_id(),
            account_id=account.id,
            stripe_subscription_id=stripe_subscription_id,
            cohort=_cohort_for_price(settings, price_id),
        )
        db.add(row)
        ledger_append(
            db,
            account.id,
            "subscription_started",
            metadata={"stripe_subscription_id": stripe_subscription_id, "price_id": price_id},
        )
    was_cancel_pending = bool(row.cancel_at_period_end)
    row.stripe_price_id = price_id or row.stripe_price_id
    row.cadence = cadence
    row.status = status
    row.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    row.current_period_end = period_end or row.current_period_end
    row.updated_at = _utcnow().replace(tzinfo=None)
    if row.cancel_at_period_end and not was_cancel_pending and not created:
        ledger_append(
            db,
            account.id,
            "subscription_canceled_at_period_end",
            metadata={"stripe_subscription_id": stripe_subscription_id},
        )
    if account.stripe_customer_id is None and obj.get("customer"):
        account.stripe_customer_id = obj.get("customer")
    _refresh_tier(db, account)
    return "subscription-created" if created else "subscription-updated"


def _on_subscription_deleted(db: Session, obj: dict) -> str:
    stripe_subscription_id = obj.get("id") or ""
    row = db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    ).scalar_one_or_none()
    if row is None:
        return "no-subscription"
    row.status = Subscription.STATUS_CANCELED
    row.updated_at = _utcnow().replace(tzinfo=None)
    account = db.get(Account, row.account_id)
    if account is None:
        return "no-account"
    ledger_append(
        db,
        account.id,
        "subscription_canceled_immediate" if not row.cancel_at_period_end else "subscription_ended",
        metadata={"stripe_subscription_id": stripe_subscription_id},
    )
    expire_buckets(
        db,
        account.id,
        CreditBucket.SOURCE_SUBSCRIBER_MONTHLY,
        op="expire_subscriber_monthly",
    )
    _refresh_tier(db, account)
    return "subscription-canceled"


def _invoice_subscription_id(obj: dict) -> str | None:
    sub = obj.get("subscription")
    if isinstance(sub, str) and sub:
        return sub
    if isinstance(sub, dict):
        return sub.get("id")
    parent = obj.get("parent") or {}
    details = parent.get("subscription_details") or {}
    sub = details.get("subscription")
    if isinstance(sub, str) and sub:
        return sub
    if isinstance(sub, dict):
        return sub.get("id")
    return None


def _on_invoice_paid(db: Session, settings: Settings, obj: dict) -> str:
    subscription_id = _invoice_subscription_id(obj)
    if not subscription_id:
        return "ignored"  # non-subscription invoice
    row = db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
    ).scalar_one_or_none()
    account = (
        db.get(Account, row.account_id)
        if row is not None
        else _account_for_event(db, metadata=None, customer_id=obj.get("customer"))
    )
    if account is None:
        return "no-account"
    # Idempotency first: a redelivered invoice event must not re-expire the
    # allowance it granted the first time around.
    if obj.get("id") and db.execute(
        select(CreditBucket).where(CreditBucket.stripe_ref == obj.get("id"))
    ).scalar_one_or_none() is not None:
        return "grant-duplicate"
    if row is not None and row.status == Subscription.STATUS_PAST_DUE:
        row.status = Subscription.STATUS_ACTIVE
        _refresh_tier(db, account)
    billing_reason = obj.get("billing_reason") or ""
    if billing_reason == "subscription_cycle":
        ledger_append(
            db,
            account.id,
            "subscription_renewed",
            metadata={"stripe_subscription_id": subscription_id, "invoice": obj.get("id")},
        )
    # Refresh the monthly allowance: old grant does not roll over.
    expire_buckets(
        db,
        account.id,
        CreditBucket.SOURCE_SUBSCRIBER_MONTHLY,
        op="expire_subscriber_monthly",
    )
    period_end = row.current_period_end if row is not None else None
    lines = ((obj.get("lines") or {}).get("data")) or []
    if lines:
        line_period_end = (lines[0].get("period") or {}).get("end")
        if line_period_end:
            period_end = datetime.fromtimestamp(line_period_end, tz=timezone.utc).replace(tzinfo=None)
    bucket = grant_bucket(
        db,
        account.id,
        source=CreditBucket.SOURCE_SUBSCRIBER_MONTHLY,
        credits=settings.subscriber_monthly_credits,
        op="grant_subscriber_monthly",
        expires_at=period_end,
        stripe_ref=obj.get("id"),
        metadata={"stripe_subscription_id": subscription_id},
    )
    return "grant-issued" if bucket else "grant-duplicate"


def _on_invoice_payment_failed(db: Session, obj: dict) -> str:
    subscription_id = _invoice_subscription_id(obj)
    if not subscription_id:
        return "ignored"
    row = db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
    ).scalar_one_or_none()
    if row is None:
        return "no-subscription"
    if row.status == Subscription.STATUS_ACTIVE:
        row.status = Subscription.STATUS_PAST_DUE
        row.updated_at = _utcnow().replace(tzinfo=None)
    account = db.get(Account, row.account_id)
    if account is not None:
        ledger_append(
            db,
            account.id,
            "subscription_payment_failed",
            metadata={"stripe_subscription_id": subscription_id, "invoice": obj.get("id")},
        )
    # Dunning grace: tier stays subscribed while past_due (design table).
    return "past-due"


def _on_charge_refunded(db: Session, obj: dict) -> str:
    payment_intent = obj.get("payment_intent")
    if not payment_intent:
        return "ignored"
    bucket = db.execute(
        select(CreditBucket).where(CreditBucket.stripe_ref == payment_intent)
    ).scalar_one_or_none()
    if bucket is None:
        return "no-bucket"
    refunded_cents = obj.get("amount_refunded") or 0
    clawback = min(int(refunded_cents), bucket.remaining_credits)
    if clawback > 0:
        bucket.remaining_credits -= clawback
    ledger_append(
        db,
        bucket.account_id,
        "refund_purchased",
        credits_delta=-clawback,
        bucket_id=bucket.id,
        metadata={"payment_intent": payment_intent, "amount_refunded_cents": refunded_cents},
    )
    return "refund-applied"
