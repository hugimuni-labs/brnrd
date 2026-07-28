"""Public stats endpoint for the landing surface (#509)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app, stripe_api  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.routers import stats as stats_router  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402


def _client(supporter_cohort_size: int = 200) -> TestClient:
    stats_router._reset_cache()
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            public_base_url="https://brnrd.example",
            supporter_cohort_size=supporter_cohort_size,
        )
    )
    return TestClient(app, base_url="https://testserver")


def test_public_stats_is_unauthenticated_and_coarse():
    client = _client()
    resp = client.get("/v1/stats/public")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload == {
        "accounts": 0,
        "supporter_seats_total": 200,
        "supporter_seats_taken": 0,
    }
    # Coarse counters only — no identity-shaped keys may ever appear here.
    assert not any("login" in k or "email" in k or "id" in k for k in payload)


def test_public_stats_counts_accounts():
    client = _client(supporter_cohort_size=2)
    brnrd_account_headers(client.app, github_id="1", login="a", email="a@example.com")
    brnrd_account_headers(client.app, github_id="2", login="b", email="b@example.com")
    stats_router._reset_cache()
    payload = client.get("/v1/stats/public").json()
    assert payload["accounts"] == 2
    assert payload["supporter_seats_total"] == 2


def test_public_stats_caches_between_calls():
    client = _client()
    assert client.get("/v1/stats/public").json()["accounts"] == 0
    brnrd_account_headers(client.app, github_id="3", login="c", email="c@example.com")
    # Still cached: the new account is invisible until the TTL lapses.
    assert client.get("/v1/stats/public").json()["accounts"] == 0
    stats_router._reset_cache()
    assert client.get("/v1/stats/public").json()["accounts"] == 1


def test_deployed_version_is_public_and_never_fabricates(tmp_path, monkeypatch):
    """/v1/stats/version answers "is my merge live?" — and degrades to None
    (not a guess) when no build_info.txt was stamped (local/dev installs)."""
    from brnrd import version_info

    client = _client()
    monkeypatch.setattr(
        version_info, "_BUILD_INFO_PATH", tmp_path / "absent.txt",
    )
    monkeypatch.delenv("PLATFORM_TREE_ID", raising=False)
    resp = client.get("/v1/stats/version")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["commit"] is None
    assert payload["built_at"] is None
    assert payload["tree_id"] is None
    assert payload["started_at"]  # process start is always known

    stamped = tmp_path / "build_info.txt"
    stamped.write_text("abc1234\n2026-07-21T18:00:00+00:00\n", encoding="utf-8")
    monkeypatch.setattr(version_info, "_BUILD_INFO_PATH", stamped)
    monkeypatch.setenv("PLATFORM_TREE_ID", "tree-xyz")
    payload = client.get("/v1/stats/version").json()
    assert payload["commit"] == "abc1234"
    assert payload["built_at"] == "2026-07-21T18:00:00+00:00"
    assert payload["tree_id"] == "tree-xyz"


# --- Stripe-derived pricing (#831) -------------------------------------------


def _price(amount: int, currency: str = "usd") -> dict:
    return {"unit_amount": amount, "currency": currency}


def test_pricing_is_null_when_stripe_is_unconfigured():
    stats_router._reset_price_cache()
    client = _client()
    payload = client.get("/v1/stats/pricing").json()
    assert payload == {
        "supporter_monthly": None,
        "supporter_annual": None,
        "public_monthly": None,
        "public_annual": None,
    }


def test_pricing_reads_stripe_prices(monkeypatch):
    stats_router._reset_price_cache()
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            public_base_url="https://brnrd.example",
            stripe_api_key="sk_test_x",
            stripe_price_supporter_monthly="price_sup_m",
            stripe_price_supporter_annual="price_sup_y",
            stripe_price_public_monthly="price_pub_m",
            stripe_price_public_annual="price_pub_y",
        )
    )
    client = TestClient(app, base_url="https://testserver")
    prices = {
        "price_sup_m": _price(500),
        "price_sup_y": _price(5000),
        "price_pub_m": _price(700),
        "price_pub_y": _price(7000),
    }
    monkeypatch.setattr(stripe_api, "get_price", lambda settings, price_id: prices[price_id])
    payload = client.get("/v1/stats/pricing").json()
    assert payload == {
        "supporter_monthly": {"amount": 500, "currency": "usd"},
        "supporter_annual": {"amount": 5000, "currency": "usd"},
        "public_monthly": {"amount": 700, "currency": "usd"},
        "public_annual": {"amount": 7000, "currency": "usd"},
    }


def test_pricing_tier_is_null_on_a_stripe_failure_without_failing_the_others(monkeypatch):
    stats_router._reset_price_cache()
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            public_base_url="https://brnrd.example",
            stripe_api_key="sk_test_x",
            stripe_price_supporter_monthly="price_sup_m",
            stripe_price_supporter_annual="price_sup_y",
        )
    )
    client = TestClient(app, base_url="https://testserver")

    def fake_get_price(settings, price_id):
        if price_id == "price_sup_m":
            raise stripe_api.StripeError("boom")
        return _price(5000)

    monkeypatch.setattr(stripe_api, "get_price", fake_get_price)
    payload = client.get("/v1/stats/pricing").json()
    assert payload["supporter_monthly"] is None
    assert payload["supporter_annual"] == {"amount": 5000, "currency": "usd"}
    # No price ID configured for these — Stripe is never called for them.
    assert payload["public_monthly"] is None
    assert payload["public_annual"] is None


def test_pricing_caches_between_calls(monkeypatch):
    stats_router._reset_price_cache()
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            public_base_url="https://brnrd.example",
            stripe_api_key="sk_test_x",
            stripe_price_supporter_monthly="price_sup_m",
        )
    )
    client = TestClient(app, base_url="https://testserver")
    calls = {"n": 0}

    def fake_get_price(settings, price_id):
        calls["n"] += 1
        return _price(500)

    monkeypatch.setattr(stripe_api, "get_price", fake_get_price)
    client.get("/v1/stats/pricing")
    client.get("/v1/stats/pricing")
    assert calls["n"] == 1
    stats_router._reset_price_cache()
    client.get("/v1/stats/pricing")
    assert calls["n"] == 2
