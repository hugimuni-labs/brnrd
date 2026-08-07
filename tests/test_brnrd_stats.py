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


def test_public_stats_does_not_count_erased_accounts():
    """The landing counter names living accounts, not rows.

    Driven through the real deletion endpoint, not a hand-set ``deleted_at``:
    ``delete_account`` keeps the row (the retained billing ledger's FK needs
    it) and rewrites ``github_id`` to a tombstone value, which frees the
    unique constraint so the same person can sign up again. So one
    delete-and-recreate cycle leaves two rows behind one living account —
    exactly the shape that made the public number climb during a user test
    and made it unable to answer "is that a new user?".
    """
    client = _client()
    headers = brnrd_account_headers(
        client.app, github_id="7", login="octocat", email="octocat@example.com"
    )
    stats_router._reset_cache()
    assert client.get("/v1/stats/public").json()["accounts"] == 1

    assert (
        client.post(
            "/v1/accounts/delete",
            json={"confirm_login": "octocat"},
            headers=headers,
        ).status_code
        == 200
    )

    # The row survives — this is the fact the count has to see past, and
    # asserting it here is what keeps the test honest if erasure ever
    # becomes a hard delete and the filter turns into a silent no-op.
    from brnrd.models import Account as _Account

    with client.app.state.SessionLocal() as db:
        assert db.query(_Account).count() == 1

    stats_router._reset_cache()
    assert client.get("/v1/stats/public").json()["accounts"] == 0

    # And the same person signing up again is one account, not three rows.
    brnrd_account_headers(
        client.app, github_id="8", login="octocat", email="octocat@example.com"
    )
    stats_router._reset_cache()
    assert client.get("/v1/stats/public").json()["accounts"] == 1
    with client.app.state.SessionLocal() as db:
        assert db.query(_Account).count() == 2


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
    resp = client.get("/v1/stats/version")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["commit"] is None
    assert payload["built_at"] is None
    assert payload["started_at"]  # process start is always known

    # A genuine git-sourced sha, third line says so — the only case
    # `commit` may carry a value.
    stamped = tmp_path / "build_info.txt"
    stamped.write_text(
        "abc1234\n2026-07-21T18:00:00+00:00\ngit\n", encoding="utf-8",
    )
    monkeypatch.setattr(version_info, "_BUILD_INFO_PATH", stamped)
    payload = client.get("/v1/stats/version").json()
    assert payload["commit"] == "abc1234"
    assert payload["built_at"] == "2026-07-21T18:00:00+00:00"


def test_deployed_version_never_reports_a_commit_from_a_non_git_source(
    tmp_path, monkeypatch
):
    """The 2026-07-30 incident: a build stamped without a real git sha must
    never report a ``commit``. The stamp below is a surviving image from the
    retired PaaS build hook — a git-less build tree whose identity was the
    platform's exported tree id, marked ``tree`` on the third line. Nothing
    writes that source any more (2026-07-31), but the reader's rule is the
    point and it is stated positively: ``commit`` is carried only when the
    source line says ``git``, so any other value — old, new, or garbage —
    answers ``None`` rather than being relabelled a sha."""
    from brnrd import version_info

    client = _client()
    stamped = tmp_path / "build_info.txt"
    stamped.write_text(
        "bebd5c1d\n2026-07-30T10:19:01+00:00\ntree\n", encoding="utf-8",
    )
    monkeypatch.setattr(version_info, "_BUILD_INFO_PATH", stamped)
    payload = client.get("/v1/stats/version").json()
    assert payload["commit"] is None
    assert payload["built_at"] == "2026-07-30T10:19:01+00:00"


def test_deployed_version_pre_fix_two_line_file_reads_as_unknown(tmp_path, monkeypatch):
    """A build_info.txt stamped before this fix has no source line at all —
    that ambiguity must resolve to unknown, never to a guessed source."""
    from brnrd import version_info

    client = _client()
    stamped = tmp_path / "build_info.txt"
    stamped.write_text("abc1234\n2026-07-21T18:00:00+00:00\n", encoding="utf-8")
    monkeypatch.setattr(version_info, "_BUILD_INFO_PATH", stamped)
    payload = client.get("/v1/stats/version").json()
    assert payload["commit"] is None
    assert payload["built_at"] == "2026-07-21T18:00:00+00:00"


# --- Shells-and-doors support matrix (#1070 follow-up) ----------------------


def test_support_matrix_reads_ready_when_deployment_is_unconfigured():
    """"soon" means the gate's code does not exist yet — every door here is
    shipped, so an unconfigured deployment reads "ready" (shipped, no
    confirmed brnrd.dev identity), never "soon" and never a fabricated
    "live". Slack and Signal have no hosted axis at all (self-hosted
    gates, full stop) and land on "ready" the same way an unconfigured
    WhatsApp does — one state for "no confirmed identity", regardless of
    which of the two reasons caused it."""
    client = _client()
    payload = client.get("/v1/stats/support").json()
    by_slug = {door["slug"]: door["status"] for door in payload["doors"]}
    assert by_slug["telegram"] == "ready"
    assert by_slug["whatsapp"] == "ready"
    assert by_slug["github"] == "ready"
    assert by_slug["slack"] == "ready"
    assert by_slug["signal"] == "ready"
    # The one door with no cloud_settings that stays live regardless: it
    # *is* brnrd.dev.
    assert by_slug["dashboard"] == "live"


def test_support_matrix_reads_live_once_this_deployment_is_configured():
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            public_base_url="https://brnrd.example",
            telegram_bot_token="t",
            telegram_bot_username="brnrd_bot",
        )
    )
    client = TestClient(app, base_url="https://testserver")
    payload = client.get("/v1/stats/support").json()
    by_slug = {door["slug"]: door["status"] for door in payload["doors"]}
    assert by_slug["telegram"] == "live"
    # Unaffected by an unrelated door's configuration.
    assert by_slug["whatsapp"] == "ready"


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
