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

from brnrd import create_app, stripe_api  # noqa: E402
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
