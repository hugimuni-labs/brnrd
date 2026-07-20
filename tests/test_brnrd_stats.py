"""Public stats endpoint for the landing surface (#509)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
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
