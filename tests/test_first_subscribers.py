"""The first paying subscribers see the install-session link (his 11426).

Every test drives state the way production does, through the signed Stripe
webhook route, and reads it the way the page does, through
``GET /v1/accounts/subscription``. No Stripe call happens at read time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402

from _helpers import brnrd_account_headers, brnrd_client  # noqa: E402

WEBHOOK_SECRET = "whsec_test"
LINK = "https://calendly.com/alexandra-hugimuni/hugimuni-sas"


def _client(**overrides) -> TestClient:
    kwargs = dict(
        stripe_api_key="sk_test_x",
        stripe_webhook_secret=WEBHOOK_SECRET,
        stripe_price_public_monthly="price_pub_m",
        stripe_price_public_annual="price_pub_y",
    )
    kwargs.update(overrides)
    return brnrd_client(**kwargs)


def _post(client: TestClient, event: dict) -> str:
    body = json.dumps(event).encode()
    ts = str(int(time.time()))
    mac = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    response = client.post(
        "/v1/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": f"t={ts},v1={mac}", "Content-Type": "application/json"},
    )
    assert response.status_code == 200, response.text
    return response.json()["disposition"]


class Payer:
    """One GitHub account with its own Stripe customer and subscription ids."""

    def __init__(self, client: TestClient, login: str, n: int):
        self.client = client
        self.login = login
        self.headers = brnrd_account_headers(client.app, github_id=str(1000 + n), login=login, email=None)
        from brnrd.models import Account
        from sqlalchemy import select

        with client.app.state.SessionLocal() as db:
            self.account_id = db.execute(select(Account.id).where(Account.github_login == login)).scalar_one()
        self.customer = f"cus_{login}"
        self.sub = f"sub_{login}"

    def subscribe(self, *, pay: bool = True) -> None:
        assert _post(self.client, {
            "id": f"evt_sub_{self.login}",
            "type": "customer.subscription.created",
            "data": {"object": {
                "id": self.sub,
                "status": "active" if pay else "incomplete",
                "customer": self.customer,
                "cancel_at_period_end": False,
                "current_period_end": 2000000000,
                "metadata": {"brnrd_account_id": self.account_id},
                "items": {"data": [{"price": {"id": "price_pub_m", "recurring": {"interval": "month"}}}]},
            }},
        }) == "subscription-created"
        if pay:
            assert _post(self.client, {
                "id": f"evt_inv_{self.login}",
                "type": "invoice.paid",
                "data": {"object": {
                    "id": f"in_{self.login}",
                    "customer": self.customer,
                    "subscription": self.sub,
                    "billing_reason": "subscription_create",
                    "lines": {"data": [{"period": {"end": 2000000000}}]},
                }},
            }) == "grant-issued"

    def refund(self, *, full: bool = True) -> str:
        return _post(self.client, {
            "id": f"evt_refund_{self.login}_{full}",
            "type": "charge.refunded",
            "data": {"object": {
                "id": f"ch_{self.login}",
                "customer": self.customer,
                "payment_intent": f"pi_{self.login}",
                "amount_refunded": 700 if full else 100,
                "refunded": full,
            }},
        })

    def cancel(self) -> None:
        assert _post(self.client, {
            "id": f"evt_del_{self.login}",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": self.sub, "customer": self.customer}},
        }) == "subscription-canceled"

    def page(self) -> dict:
        response = self.client.get("/v1/accounts/subscription", headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()


def _payers(client: TestClient, *logins: str) -> list[Payer]:
    return [Payer(client, login, n) for n, login in enumerate(logins)]


def test_the_first_n_paying_subscribers_see_the_link_and_the_next_does_not():
    client = _client(first_subscribers_seats=2)
    a, b, c = _payers(client, "ada", "bob", "cyd")
    for p in (a, b, c):
        p.subscribe()

    assert a.page()["install_session_url"] == LINK
    assert a.page()["first_subscribers_seats"] == 2
    assert b.page()["install_session_url"] == LINK
    assert c.page()["install_session_url"] is None
    assert c.page()["first_subscribers_seats"] is None


def test_the_default_offer_is_twenty_seats_and_his_calendly_link():
    from brnrd.config import Settings

    assert Settings.first_subscribers_seats == 20
    assert Settings.first_subscribers_link == LINK


def test_a_checkout_that_never_paid_takes_no_seat():
    client = _client(first_subscribers_seats=1)
    unpaid, paid = _payers(client, "ada", "bob")
    unpaid.subscribe(pay=False)
    paid.subscribe()

    assert paid.page()["install_session_url"] == LINK


def test_admins_and_excluded_logins_take_no_seat():
    client = _client(
        first_subscribers_seats=1,
        admin_github_logins=("boss",),
        first_subscribers_exclude=("tester",),
    )
    boss, tester, real = _payers(client, "boss", "tester", "real")
    for p in (boss, tester, real):
        p.subscribe()

    assert boss.page()["install_session_url"] is None
    assert tester.page()["install_session_url"] is None
    assert real.page()["install_session_url"] == LINK


def test_a_full_refund_gives_the_seat_up_and_a_partial_one_does_not():
    client = _client(first_subscribers_seats=1)
    buyer, next_in_line = _payers(client, "buyer", "next")
    buyer.subscribe()
    next_in_line.subscribe()
    assert buyer.page()["install_session_url"] == LINK

    assert buyer.refund(full=False) == "subscription-refund-recorded"
    assert buyer.page()["install_session_url"] == LINK
    assert next_in_line.page()["install_session_url"] is None

    assert buyer.refund(full=True) == "subscription-refund-recorded"
    assert buyer.page()["install_session_url"] is None
    assert next_in_line.page()["install_session_url"] == LINK


def test_a_canceled_subscriber_loses_the_link_and_keeps_the_seat():
    client = _client(first_subscribers_seats=1)
    first, second = _payers(client, "first", "second")
    first.subscribe()
    second.subscribe()
    first.cancel()

    assert first.page()["status"] is None
    assert first.page()["install_session_url"] is None
    assert second.page()["install_session_url"] is None


def test_the_link_survives_cancel_at_period_end():
    client = _client(first_subscribers_seats=1)
    (payer,) = _payers(client, "ada")
    payer.subscribe()

    from brnrd import stripe_api

    original = stripe_api.set_subscription_cancel_at_period_end
    stripe_api.set_subscription_cancel_at_period_end = lambda *a, **k: {}
    try:
        response = client.post("/v1/accounts/subscription/cancel", headers=payer.headers)
    finally:
        stripe_api.set_subscription_cancel_at_period_end = original
    assert response.status_code == 200, response.text
    assert response.json()["cancel_at_period_end"] is True
    assert response.json()["install_session_url"] == LINK


@pytest.mark.parametrize("overrides", [{"first_subscribers_seats": 0}, {"first_subscribers_link": ""}])
def test_zero_seats_or_an_empty_link_turns_the_offer_off(overrides):
    client = _client(**overrides)
    (payer,) = _payers(client, "ada")
    payer.subscribe()

    assert payer.page()["install_session_url"] is None


def test_the_admin_read_out_lists_the_order_seats_and_reasons():
    client = _client(first_subscribers_seats=1, admin_github_logins=("boss",))
    boss, ada, bob = _payers(client, "boss", "ada", "bob")
    for p in (boss, ada, bob):
        p.subscribe()

    assert client.get("/v1/admin/first-subscribers", headers=ada.headers).status_code == 404
    client.cookies.set("brnrd_session", boss.headers["Authorization"].removeprefix("Bearer "))
    response = client.get("/v1/admin/first-subscribers")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["seats"] == 1 and body["link"] == LINK
    assert [(r["github_login"], r["seat"], r["skipped"]) for r in body["rows"]] == [
        ("boss", False, "admin"),
        ("ada", True, ""),
        ("bob", False, "past the seats"),
    ]
