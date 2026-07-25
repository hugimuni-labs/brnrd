"""Tests for the #53 billing core: webhook state machine, wallet ledger,
signature verification, cohort cutoff, and the API surface."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import account_deletion, billing, create_app, ids, stripe_api  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

WEBHOOK_SECRET = "whsec_test"


def _client() -> TestClient:
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            public_base_url="https://brnrd.example",
            stripe_api_key="sk_test_x",
            stripe_webhook_secret=WEBHOOK_SECRET,
            stripe_price_supporter_monthly="price_sup_m",
            stripe_price_supporter_annual="price_sup_y",
            stripe_price_public_monthly="price_pub_m",
            stripe_price_public_annual="price_pub_y",
            supporter_cohort_size=2,
        )
    )
    return TestClient(app, base_url="https://testserver")


def _account(client: TestClient, github_id: str = "123", login: str = "octocat"):
    return brnrd_account_headers(client.app, github_id=github_id, login=login, email=f"{login}@example.com")


def _signed(payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    mac = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"Stripe-Signature": f"t={ts},v1={mac}", "Content-Type": "application/json"}


def _post_event(client: TestClient, event: dict):
    body, headers = _signed(event)
    return client.post("/v1/webhooks/stripe", content=body, headers=headers)


def _account_id(client: TestClient, headers: dict) -> str:
    # The repos endpoint doesn't expose account id; go straight to the db.
    from brnrd.models import Account
    from sqlalchemy import select

    with client.app.state.SessionLocal() as db:
        return db.execute(select(Account)).scalars().first().id


def _topup_event(account_id: str, *, credits: int = 500, event_id: str = "evt_1", intent: str = "pi_1") -> dict:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_1",
                "mode": "payment",
                "customer": "cus_1",
                "payment_intent": intent,
                "amount_total": credits,
                "metadata": {
                    "brnrd_account_id": account_id,
                    "brnrd_purpose": "wallet_topup",
                    "brnrd_credits": str(credits),
                },
            }
        },
    }


def _subscription_event(
    account_id: str,
    *,
    event_id: str = "evt_sub_1",
    event_type: str = "customer.subscription.created",
    status: str = "active",
    price_id: str = "price_sup_m",
    cancel_at_period_end: bool = False,
    period_end: int = 2000000000,
) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "data": {
            "object": {
                "id": "sub_1",
                "status": status,
                "customer": "cus_1",
                "cancel_at_period_end": cancel_at_period_end,
                "current_period_end": period_end,
                "metadata": {"brnrd_account_id": account_id},
                "items": {
                    "data": [
                        {
                            "price": {
                                "id": price_id,
                                "recurring": {"interval": "month"},
                            }
                        }
                    ]
                },
            }
        },
    }


def _invoice_paid_event(account_id: str, *, event_id: str = "evt_inv_1", invoice_id: str = "in_1", billing_reason: str = "subscription_create") -> dict:
    return {
        "id": event_id,
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": invoice_id,
                "customer": "cus_1",
                "subscription": "sub_1",
                "billing_reason": billing_reason,
                "lines": {"data": [{"period": {"end": 2000000000}}]},
            }
        },
    }


# --- signature ---------------------------------------------------------------


def test_webhook_rejects_bad_signature():
    client = _client()
    body = json.dumps({"id": "evt_x", "type": "invoice.paid"}).encode()
    response = client.post(
        "/v1/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": "t=1,v1=deadbeef", "Content-Type": "application/json"},
    )
    assert response.status_code == 403


def test_signature_verification_tolerance_and_scheme():
    secret = "whsec_abc"
    payload = b'{"id":"evt"}'
    ts = int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={mac}"
    assert stripe_api.verify_webhook_signature(payload, header, secret)
    assert not stripe_api.verify_webhook_signature(payload, header, "whsec_other")
    stale = ts - 3600
    mac_stale = hmac.new(secret.encode(), f"{stale}.".encode() + payload, hashlib.sha256).hexdigest()
    assert not stripe_api.verify_webhook_signature(payload, f"t={stale},v1={mac_stale}", secret)


# --- wallet topup ------------------------------------------------------------


def test_topup_grants_credits_idempotently():
    client = _client()
    headers = _account(client)
    account_id = _account_id(client, headers)

    assert _post_event(client, _topup_event(account_id)).json()["disposition"] == "topup-granted"
    # same event id → duplicate guard
    assert _post_event(client, _topup_event(account_id)).json()["disposition"] == "duplicate"
    # new event id, same payment intent → bucket-level idempotency
    assert (
        _post_event(client, _topup_event(account_id, event_id="evt_2")).json()["disposition"]
        == "topup-duplicate"
    )

    wallet = client.get("/v1/accounts/wallet", headers=headers).json()
    assert wallet["balances"] == {"purchased": 500}
    assert wallet["total_credits"] == 500
    assert wallet["cumulative_purchased_credits_lifetime"] == 500

    ledger = client.get("/v1/accounts/wallet/ledger", headers=headers).json()["entries"]
    assert [e["op"] for e in ledger] == ["topup"]
    assert ledger[0]["credits_delta"] == 500


def test_charge_refund_claws_back_purchased_credits():
    client = _client()
    headers = _account(client)
    account_id = _account_id(client, headers)
    _post_event(client, _topup_event(account_id))
    refund = {
        "id": "evt_ref_1",
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": "pi_1", "amount_refunded": 200}},
    }
    assert _post_event(client, refund).json()["disposition"] == "refund-applied"
    wallet = client.get("/v1/accounts/wallet", headers=headers).json()
    assert wallet["balances"] == {"purchased": 300}


# --- subscription lifecycle ---------------------------------------------------


def test_subscription_lifecycle_flips_tier_and_grants_allowance():
    client = _client()
    headers = _account(client)
    account_id = _account_id(client, headers)

    assert client.get("/v1/accounts/subscription", headers=headers).json()["tier"] == "free"

    _post_event(client, _subscription_event(account_id))
    sub = client.get("/v1/accounts/subscription", headers=headers).json()
    assert sub["tier"] == "subscribed"
    assert sub["status"] == "active"
    assert sub["cohort"] == "supporter"
    assert sub["cadence"] == "monthly"

    # first invoice grants the monthly allowance once
    _post_event(client, _invoice_paid_event(account_id))
    _post_event(client, _invoice_paid_event(account_id, event_id="evt_inv_dup"))
    wallet = client.get("/v1/accounts/wallet", headers=headers).json()
    assert wallet["balances"] == {"subscriber_monthly": 300}

    # renewal expires the old grant and issues a fresh one
    _post_event(
        client,
        _invoice_paid_event(account_id, event_id="evt_inv_2", invoice_id="in_2", billing_reason="subscription_cycle"),
    )
    wallet = client.get("/v1/accounts/wallet", headers=headers).json()
    assert wallet["balances"] == {"subscriber_monthly": 300}
    ops = [e["op"] for e in client.get("/v1/accounts/wallet/ledger", headers=headers).json()["entries"]]
    assert "subscription_renewed" in ops
    assert "expire_subscriber_monthly" in ops

    # payment failure → past_due, tier keeps subscribed (dunning grace)
    failed = {
        "id": "evt_fail_1",
        "type": "invoice.payment_failed",
        "data": {"object": {"id": "in_3", "subscription": "sub_1"}},
    }
    _post_event(client, failed)
    sub = client.get("/v1/accounts/subscription", headers=headers).json()
    assert sub["status"] == "past_due"
    assert sub["tier"] == "subscribed"

    # deletion → canceled, tier free, allowance expired
    deleted = _subscription_event(account_id, event_id="evt_del_1", event_type="customer.subscription.deleted", status="canceled")
    _post_event(client, deleted)
    sub = client.get("/v1/accounts/subscription", headers=headers).json()
    assert sub["tier"] == "free"
    wallet = client.get("/v1/accounts/wallet", headers=headers).json()
    assert wallet["balances"] == {}


# --- cohort cutoff -----------------------------------------------------------


def test_supporter_cohort_cutoff(monkeypatch):
    client = _client()  # supporter_cohort_size=2
    seen = {}

    def fake_checkout(settings, **kwargs):
        seen["price_id"] = kwargs["price_id"]
        return {"url": "https://checkout.stripe.example/s"}

    monkeypatch.setattr("brnrd.routers.billing.stripe_api.create_subscription_checkout", fake_checkout)
    monkeypatch.setattr(
        "brnrd.routers.billing.stripe_api.create_customer",
        lambda settings, **kwargs: {"id": f"cus_{kwargs['account_id']}"},
    )

    first = _account(client, github_id="1", login="a")
    out = client.post("/v1/accounts/subscription/checkout", json={"cadence": "monthly"}, headers=first)
    assert out.json()["cohort"] == "supporter"
    assert seen["price_id"] == "price_sup_m"

    # two supporter subscriptions exist → third checkout is public-priced
    for n, github_id in enumerate(("1", "2")):
        account_headers = _account(client, github_id=github_id, login=f"user{github_id}")
        aid = None
        from brnrd.models import Account
        from sqlalchemy import select

        with client.app.state.SessionLocal() as db:
            aid = db.execute(select(Account).where(Account.github_id == github_id)).scalar_one().id
        event = _subscription_event(aid, event_id=f"evt_sub_{github_id}")
        event["data"]["object"]["id"] = f"sub_{github_id}"
        event["data"]["object"]["customer"] = f"cus_{github_id}"
        _post_event(client, event)

    third = _account(client, github_id="3", login="c")
    out = client.post("/v1/accounts/subscription/checkout", json={"cadence": "annual"}, headers=third)
    assert out.json()["cohort"] == "public"
    assert seen["price_id"] == "price_pub_y"


# --- topup bounds ------------------------------------------------------------


def test_topup_checkout_validates_bounds(monkeypatch):
    client = _client()
    headers = _account(client)
    assert (
        client.post("/v1/accounts/wallet/checkout", json={"amount_usd": 2}, headers=headers).status_code
        == 422
    )
    assert (
        client.post("/v1/accounts/wallet/checkout", json={"amount_usd": 900}, headers=headers).status_code
        == 422
    )
    seen = {}

    def fake_topup(settings, **kwargs):
        seen.update(kwargs)
        return {"url": "https://checkout.stripe.example/t"}

    monkeypatch.setattr("brnrd.routers.billing.stripe_api.create_topup_checkout", fake_topup)
    out = client.post("/v1/accounts/wallet/checkout", json={"amount_usd": 20}, headers=headers)
    assert out.status_code == 200
    assert seen["credits"] == 2000


# --- session-cookie auth seam (dashboard billing surface) --------------------


def test_billing_api_accepts_session_cookie():
    # The SPA authenticates with the brnrd_session cookie, never a bearer —
    # require_account_or_session lets that same Token row through for billing.
    client = _client()
    headers = _account(client)
    raw = headers["Authorization"].removeprefix("Bearer ")
    client.cookies.set(client.app.state.settings.session_cookie, raw)

    sub = client.get("/v1/accounts/subscription")
    assert sub.status_code == 200
    assert sub.json()["tier"] == "free"
    wallet = client.get("/v1/accounts/wallet")
    assert wallet.status_code == 200
    assert wallet.json()["total_credits"] == 0


def test_billing_api_cookie_posts_work_and_bearer_still_wins(monkeypatch):
    client = _client()
    headers = _account(client)
    raw = headers["Authorization"].removeprefix("Bearer ")
    client.cookies.set(client.app.state.settings.session_cookie, raw)

    def fake_checkout(settings, **kwargs):
        return {"url": "https://checkout.stripe.example/s"}

    monkeypatch.setattr("brnrd.routers.billing.stripe_api.create_customer", lambda *a, **k: {"id": "cus_c"})
    monkeypatch.setattr("brnrd.routers.billing.stripe_api.create_subscription_checkout", fake_checkout)
    out = client.post("/v1/accounts/subscription/checkout", json={"cadence": "monthly"})
    assert out.status_code == 200
    assert out.json()["checkout_url"] == "https://checkout.stripe.example/s"

    # An explicit (bad) bearer is never rescued by the cookie: the header
    # keeps its exact contract, the cookie is only a fallback.
    denied = client.get("/v1/accounts/subscription", headers={"Authorization": "Bearer nope"})
    assert denied.status_code == 401


def test_billing_api_still_401s_with_no_credentials():
    client = _client()
    assert client.get("/v1/accounts/subscription").status_code == 401
    assert client.get("/v1/accounts/wallet").status_code == 401
    assert client.post("/v1/accounts/subscription/checkout", json={"cadence": "monthly"}).status_code == 401


# --- Art 17 tombstone (#713) --------------------------------------------------
#
# The erasure (account_deletion.delete_account) sets Account.deleted_at and
# nothing in the billing module read it: one webhook carrying
# brnrd_account_id re-linked the customer, re-created the Subscription row and
# appended to the *retained* ledger. The guard lives at handle_stripe_event —
# ahead of the dispatch table — so these tests derive their coverage from
# billing._EVENT_HANDLERS rather than hand-listing the types. Add a seventh
# entry to that table and a seventh case appears here with no edit below.

GHOST_SUBSCRIPTION_ID = "sub_ghost"
GHOST_PAYMENT_INTENT = "pi_ghost"
GHOST_CUSTOMER_ID = "cus_ghost"


def _account_id_for(client: TestClient, github_id: str) -> str:
    from sqlalchemy import select

    from brnrd.models import Account

    with client.app.state.SessionLocal() as db:
        return db.execute(select(Account).where(Account.github_id == github_id)).scalar_one().id


def _tombstoned_account(client: TestClient, *, github_id: str = "77", login: str = "ghost") -> str:
    """Erase an account, then re-attach the billing rows that a resurrection —
    or a delete/webhook interleave, where Stripe's delivery reads rows the
    erasure commits away underneath it — leaves pointing at the tombstone.

    Without them the row-walking handlers (subscription.deleted,
    invoice.*, charge.refunded) would be inert for the wrong reason: their
    rows were swept, so they resolve nothing and a missing guard looks
    correct. Seeding the rows is what makes each dispatch type able to reach
    the tombstone, which is what the guard has to stop.
    """
    from brnrd.models import Account, CreditBucket, Subscription

    _account(client, github_id=github_id, login=login)
    account_id = _account_id_for(client, github_id)
    with client.app.state.SessionLocal() as db:
        account_deletion.delete_account(db, client.app.state.settings, db.get(Account, account_id))
        db.add(
            Subscription(
                id=ids.subscription_id(),
                account_id=account_id,
                stripe_subscription_id=GHOST_SUBSCRIPTION_ID,
                status=Subscription.STATUS_ACTIVE,
                stripe_price_id="price_sup_m",
            )
        )
        db.add(
            CreditBucket(
                id=ids.credit_bucket_id(),
                account_id=account_id,
                source=CreditBucket.SOURCE_PURCHASED,
                granted_credits=500,
                remaining_credits=500,
                stripe_ref=GHOST_PAYMENT_INTENT,
            )
        )
        db.commit()
    return account_id


def _account_keyed_snapshot(client: TestClient, account_id: str) -> dict:
    """Every row of every mapped class carrying an ``account_id``, plus the
    account row itself — derived from the mapper registry, so a store added
    later joins the snapshot with no edit here."""
    from sqlalchemy import inspect, select

    from brnrd.models import Account, Base

    snapshot: dict[str, object] = {}
    with client.app.state.SessionLocal() as db:
        for mapper in Base.registry.mappers:
            model = mapper.class_
            if model is Account or not hasattr(model, "account_id"):
                continue
            rows = db.execute(select(model).where(model.account_id == account_id)).scalars().all()
            snapshot[model.__name__] = sorted(
                repr({c.key: getattr(row, c.key) for c in mapper.column_attrs}) for row in rows
            )
        account = db.get(Account, account_id)
        snapshot["Account"] = repr(
            {c.key: getattr(account, c.key) for c in inspect(Account).column_attrs}
        )
    return snapshot


def _ghost_event(event_type: str, account_id: str, *, event_id: str | None = None) -> dict:
    """One object carrying every id any handler resolves an account through —
    metadata id, customer id, subscription id, payment intent — so a single
    payload shape drives every dispatch type, including ones added later.

    Deliberately *not* a wallet top-up: without ``brnrd_purpose`` the
    checkout arm takes its customer-attach path, which is the write #713
    reports (the erased ``stripe_customer_id`` coming back), and it leaves
    ``payment_intent`` free to address the seeded bucket for charge.refunded.
    """
    return {
        "id": event_id or f"evt_ghost_{event_type}",
        "type": event_type,
        "data": {
            "object": {
                "id": GHOST_SUBSCRIPTION_ID,
                "customer": GHOST_CUSTOMER_ID,
                "subscription": GHOST_SUBSCRIPTION_ID,
                "payment_intent": GHOST_PAYMENT_INTENT,
                "amount_refunded": 200,
                "amount_total": 500,
                "status": "active",
                "cancel_at_period_end": False,
                "current_period_end": 2000000000,
                "billing_reason": "subscription_cycle",
                "metadata": {"brnrd_account_id": account_id},
                "items": {
                    "data": [{"price": {"id": "price_sup_m", "recurring": {"interval": "month"}}}]
                },
                "lines": {"data": [{"period": {"end": 2000000000}}]},
            }
        },
    }


@pytest.mark.parametrize("event_type", sorted(billing._EVENT_HANDLERS))
def test_tombstoned_account_is_inert_for_every_dispatch_type(event_type):
    """Replay one event of every type the dispatcher knows against an erased
    account that still has billing rows pointing at it, and assert the event
    changed nothing anywhere account-keyed."""
    from brnrd.models import StripeEvent

    client = _client()
    account_id = _tombstoned_account(client)
    before = _account_keyed_snapshot(client, account_id)

    response = _post_event(client, _ghost_event(event_type, account_id))

    assert response.status_code == 200, response.text
    assert response.json()["disposition"] == billing.DISPOSITION_ACCOUNT_DELETED
    assert _account_keyed_snapshot(client, account_id) == before, (
        f"{event_type} wrote to a tombstoned account"
    )
    # Handled, not rejected: recorded in StripeEvent and answered 2xx, so
    # Stripe stops redelivering instead of looping against a deleted account.
    with client.app.state.SessionLocal() as db:
        assert db.get(StripeEvent, f"evt_ghost_{event_type}") is not None


def test_a_seventh_dispatch_type_inherits_the_tombstone_guard(monkeypatch):
    """#713's acceptance bar, driven: register a dispatch type that did not
    exist when the guard was written, and prove both arms — it runs for a live
    account, and is never reached for a tombstoned one — with no edit to the
    guard, the resolver, or this test's enumeration."""
    reached = []

    def _seventh(db, settings, obj):
        reached.append(obj.get("id"))
        return "seventh-applied"

    monkeypatch.setitem(billing._EVENT_HANDLERS, "customer.tax_id.created", _seventh)

    client = _client()
    _account(client, github_id="9", login="live")
    live_id = _account_id_for(client, "9")

    live = _post_event(client, _ghost_event("customer.tax_id.created", live_id, event_id="evt_7_live"))
    assert live.json()["disposition"] == "seventh-applied"
    assert reached == [GHOST_SUBSCRIPTION_ID], "positive control: the new type does dispatch"

    dead_id = _tombstoned_account(client)
    blocked = _post_event(client, _ghost_event("customer.tax_id.created", dead_id, event_id="evt_7_dead"))
    assert blocked.json()["disposition"] == billing.DISPOSITION_ACCOUNT_DELETED
    assert reached == [GHOST_SUBSCRIPTION_ID], "the seventh handler must never run on a tombstone"


def test_one_webhook_cannot_resurrect_a_deleted_account(monkeypatch):
    """#713's headline case end to end: subscribe, erase, then feed the single
    ``customer.subscription.updated`` the issue drove against main."""
    from sqlalchemy import select

    from brnrd.models import Account, BillingLedgerEntry, Subscription

    client = _client()
    _account(client, github_id="42", login="erased")
    account_id = _account_id_for(client, "42")
    _post_event(client, _subscription_event(account_id))
    _post_event(client, _invoice_paid_event(account_id))

    monkeypatch.setattr(
        "brnrd.account_deletion.stripe_api.cancel_subscription_now",
        lambda settings, *, subscription_id: None,
    )
    with client.app.state.SessionLocal() as db:
        account_deletion.delete_account(db, client.app.state.settings, db.get(Account, account_id))

    with client.app.state.SessionLocal() as db:
        ledger_before = sorted(
            e.op
            for e in db.execute(
                select(BillingLedgerEntry).where(BillingLedgerEntry.account_id == account_id)
            ).scalars()
        )

    resurrect = _subscription_event(
        account_id, event_id="evt_resurrect", event_type="customer.subscription.updated"
    )
    assert _post_event(client, resurrect).json()["disposition"] == billing.DISPOSITION_ACCOUNT_DELETED

    with client.app.state.SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account.deleted_at is not None, "the tombstone must stay set"
        assert account.stripe_customer_id is None, "the erased customer link must not come back"
        assert account.tier == Account.TIER_FREE
        assert (
            db.execute(
                select(Subscription).where(Subscription.account_id == account_id)
            ).scalars().all()
            == []
        )
        ledger_after = sorted(
            e.op
            for e in db.execute(
                select(BillingLedgerEntry).where(BillingLedgerEntry.account_id == account_id)
            ).scalars()
        )
    assert ledger_after == ledger_before, "the retained ledger must not gain post-erasure entries"


def test_grant_bucket_insert_order_survives_fk_enforcement():
    """Live incident 2026-07-23 (first real subscription): postgres rejected
    the subscriber grant with a ForeignKeyViolation — the flush emitted the
    billing_ledger insert ahead of the credit_buckets row it references.
    sqlite's default FK-off mode made every existing test blind to ordering;
    this one turns the pragma on, so the grant path is pinned against the
    same database that broke it in spirit."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from brnrd import billing
    from brnrd.models import Account, Base

    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Account(id="acc_fk", github_id="fk-9", github_login="fkpin"))
    db.commit()
    bucket = billing.grant_bucket(
        db,
        "acc_fk",
        source="subscriber_monthly",
        credits=300,
        op="grant_subscriber_monthly",
        stripe_ref="in_fk_pin",
    )
    db.commit()
    assert bucket is not None
    db.close()
