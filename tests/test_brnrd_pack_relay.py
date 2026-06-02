"""Tests for the transient diffense pack relay.

Two surfaces: ``POST /v1/daemons/pack`` (a daemon relays a pack) and the
public ``GET /r/{token}`` (a reviewer opens the rendered view). The
load-bearing invariant is that the pack is **never persisted** — it lives
in a RAM-only TTL store, so it survives only as long as the relay holds
it and never touches the database.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.db import Base  # noqa: E402
from brnrd.pack_relay import PackRelayStore  # noqa: E402

_PACK = {
    "schema_version": "0.1-test",
    "metadata": {},
    "reading_order": ["summary:x"],
    "cards": [
        {
            "id": "summary:x",
            "kind": "summary",
            "identity": {"label": "the change in shape"},
            "lore": {"descriptive": "a small honest change"},
            "provenance": {},
        }
    ],
}


@pytest.fixture()
def client():
    return TestClient(
        create_app(
            Settings(
                database_url="sqlite:///:memory:",
                public_base_url="https://brnrd.example",
                inbox_long_poll_max_s=0.1,
                inbox_poll_interval_s=0.02,
            )
        )
    )


def _daemon_headers(client):
    key = client.post(
        "/v1/accounts", json={"email": "a@b.com", "password": "supersecret"}
    ).json()["api_key"]
    acc = {"Authorization": f"Bearer {key}"}
    pid = client.post(
        "/v1/accounts/projects", json={"name": "demo"}, headers=acc
    ).json()["project_id"]
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"project_id": pid},
        headers=acc,
    )
    token = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()["daemon_token"]
    return {"Authorization": f"Bearer {token}"}


# ── Store unit ───────────────────────────────────────────────────────


def test_store_roundtrip_and_unknown_token():
    store = PackRelayStore()
    token, _ = store.put({"a": 1})
    assert store.get(token) == {"a": 1}
    assert store.get("nope") is None


def test_store_expires(monkeypatch):
    import brnrd.pack_relay as pr

    now = [1000.0]
    monkeypatch.setattr(pr.time, "time", lambda: now[0])
    store = PackRelayStore(default_ttl_s=100)
    token, _ = store.put({"a": 1})
    assert store.get(token) == {"a": 1}
    now[0] = 1101.0  # past the TTL
    assert store.get(token) is None


# ── Endpoints ────────────────────────────────────────────────────────


def test_relay_requires_daemon_auth(client):
    resp = client.post("/v1/daemons/pack", json={"pack": _PACK})
    assert resp.status_code >= 400  # no bearer -> rejected


def test_relay_then_render_roundtrip(client):
    headers = _daemon_headers(client)
    ack = client.post("/v1/daemons/pack", json={"pack": _PACK}, headers=headers)
    assert ack.status_code == 200
    body = ack.json()
    assert body["render_url"].startswith("https://brnrd.example/r/")
    assert body["token"] in body["render_url"]

    # The public render route serves the diffense HTML with the pack inlined.
    path = body["render_url"][len("https://brnrd.example"):]
    page = client.get(path)
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "the change in shape" in page.text


def test_render_unknown_token_is_404(client):
    assert client.get("/r/does-not-exist").status_code == 404


def test_relay_rejects_oversized_pack(client):
    headers = _daemon_headers(client)
    huge = {"cards": [{"id": "x", "kind": "summary", "blob": "x" * 5_000_000}]}
    resp = client.post("/v1/daemons/pack", json={"pack": huge}, headers=headers)
    assert resp.status_code == 413


def test_pack_never_hits_the_database():
    # The data-ownership stance: brnrd renders relayed packs but stores
    # none. There must be no pack table in the schema.
    assert not any("pack" in name for name in Base.metadata.tables)
