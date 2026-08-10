"""Reproduces #cloud-gate-401: does `cloud.connect()`'s own pairing loop,
driven by the *real* brnrd backend (not a scripted `_request` stub), leave
the daemon holding a token that a later, independent `/v1/daemons/inbox`
call actually authenticates with?

Every existing `cloud.connect()` test in ``test_cloud_gate.py`` scripts
``cloud._request`` with a hand-written dict sequence — it never drives the
real pairing state machine in ``routers/pairing.py``. The tests that *do*
exercise the real backend (`_handshake`) hand-roll the pair/approve/poll
HTTP calls directly and never call `cloud.connect()` itself. This file
closes that gap: `cloud.connect()` runs its real poll loop against a live
`TestClient`-backed app, approval lands from a concurrent thread (mirroring
the human clicking "approve" mid-poll), and the token `connect()` persists
to disk is then replayed against a *fresh* `/v1/daemons/inbox` call — the
same shape as a freshly-installed daemon service's first poll.
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from sqlalchemy import select  # noqa: E402

from brnrd.models import PairRequest  # noqa: E402
from brr.gates import cloud  # noqa: E402
from _helpers import init_git_repo  # noqa: E402

from test_cloud_gate import _account_and_project, _make_brnrd, _route_to  # noqa: E402


def test_connect_token_authenticates_a_later_independent_inbox_poll(tmp_path, monkeypatch):
    client, _forwarder = _make_brnrd()
    acc_headers, pid = _account_and_project(client)
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    init_git_repo(tmp_path)
    brr_dir = tmp_path / ".brr"

    result: dict = {}

    def run_connect():
        result["state"] = cloud.connect(
            brr_dir,
            brnrd_url="http://testserver",
            poll_interval_s=0.02,
            timeout_s=10,
            out=lambda _msg: None,
        )

    thread = threading.Thread(target=run_connect)
    thread.start()

    # Wait for connect()'s own POST /v1/accounts/pair to land, then approve
    # it the way a human does at the approval page — a real HTTP call, not a
    # DB row forged directly.
    deadline = time.monotonic() + 5
    pair_code = None
    while time.monotonic() < deadline:
        with client.app.state.SessionLocal() as db:
            row = db.execute(select(PairRequest)).scalars().first()
        if row is not None:
            pair_code = row.pair_code
            break
        time.sleep(0.02)
    assert pair_code, "connect() never created a pair request"

    approve = client.post(
        f"/v1/accounts/pair/{pair_code}/approve",
        json={"repo_id": pid},
        headers=acc_headers,
    )
    assert approve.status_code == 200, approve.text

    thread.join(timeout=10)
    assert not thread.is_alive(), "connect() never returned after approval"
    state = result.get("state")
    assert state is not None and state.get("token"), "connect() did not persist a token"

    # The persisted on-disk state is what a *separately started* daemon
    # process reads before its very first inbox poll.
    fresh_state = cloud._load_state(brr_dir)
    assert fresh_state.get("token") == state["token"]

    resp = client.request(
        "GET",
        "/v1/daemons/inbox",
        headers={"Authorization": f"Bearer {fresh_state['token']}"},
    )
    assert resp.status_code == 200, (
        f"the token connect() persisted was rejected by /v1/daemons/inbox: "
        f"{resp.status_code} {resp.text}"
    )
