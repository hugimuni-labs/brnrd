"""The approve step has to prove it came from the daemon that asked (A-1).

The defect these tests pin: `approve_core` used to bind a pairing to whoever
*approved* it, with nothing tying the approver to the party that *initiated*
the handshake. Because `POST /v1/accounts/pair` is unauthenticated and the
short `pair_code` is the only thing an approve needed, an attacker who
guessed or enumerated a live code could approve it into their **own**
account; the victim's daemon then polled, received a daemon token scoped to
the attacker, and executed the attacker's tasks on the victim's host.

Every test here drives the real request path an attacker would use — the
HTTP routes, not `approve_core` directly — because the guard is only worth
anything at the seam a request actually arrives on.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import PairRequest, Repo, Token  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(
        create_app(
            Settings(
                database_url="sqlite:///:memory:",
                public_base_url="https://brnrd.example",
            )
        )
    )


def _account(client, *, github_id: str, login: str, repo: str) -> tuple[dict, str]:
    """An account with one connected repo. Returns (bearer headers, repo id)."""
    headers = brnrd_account_headers(
        client.app, github_id=github_id, login=login, email=f"{login}@example.com"
    )
    r = client.post("/v1/accounts/repos", json={"repo_full_name": repo}, headers=headers)
    assert r.status_code == 201, r.text
    return headers, r.json()["repo_id"]


def _start_pair(client) -> dict:
    r = client.post("/v1/accounts/pair")
    assert r.status_code == 200, r.text
    return r.json()


def _poll(client, pair: dict):
    return client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    )


def _pair_row(client, code: str) -> PairRequest:
    with client.app.state.SessionLocal() as db:
        return db.execute(
            select(PairRequest).where(PairRequest.pair_code == code)
        ).scalar_one()


def _daemon_tokens(client) -> list[Token]:
    with client.app.state.SessionLocal() as db:
        return list(
            db.execute(select(Token).where(Token.kind == Token.KIND_DAEMON)).scalars()
        )


# --- the approval proof itself ------------------------------------------


def test_start_pair_hands_the_initiator_a_proof_and_hides_it_in_the_url_fragment():
    """The secret rides the fragment, so it never reaches the server as a
    query string (access logs, `Referer`) — only the browser the human
    pastes the terminal's link into can read it back out."""
    client = TestClient(
        create_app(
            Settings(
                database_url="sqlite:///:memory:",
                public_base_url="https://brnrd.example",
            )
        )
    )
    pair = _start_pair(client)
    secret = pair["approve_secret"]
    assert secret
    # 128-bit floor. `secrets.token_urlsafe(32)` is 256 bits in 43 chars;
    # the assertion is on the entropy the guard needs, not the spelling.
    assert len(secret) >= 22
    assert pair["pair_url"] == (
        f"https://brnrd.example/connect/{pair['pair_code']}#{secret}"
    )
    # Never stored in the clear — hashed exactly like the poll secret.
    row = _pair_row(client, pair["pair_code"])
    assert row.approve_secret_hash
    assert secret not in row.approve_secret_hash


def test_approve_without_the_initiator_proof_is_refused(client):
    """The guard, neutered: same request, no `approve_secret`."""
    headers, repo_id = _account(client, github_id="1", login="owner", repo="Gurio/laptop")
    pair = _start_pair(client)

    r = client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id},
        headers=headers,
    )
    assert r.status_code == 403, r.text
    assert _pair_row(client, pair["pair_code"]).status == PairRequest.STATUS_PENDING
    assert _daemon_tokens(client) == []


def test_approve_with_a_wrong_initiator_proof_is_refused(client):
    headers, repo_id = _account(client, github_id="1", login="owner", repo="Gurio/laptop")
    pair = _start_pair(client)

    r = client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id, "approve_secret": "not-the-one"},
        headers=headers,
    )
    assert r.status_code == 403, r.text
    assert _pair_row(client, pair["pair_code"]).status == PairRequest.STATUS_PENDING


def test_approve_with_the_initiator_proof_still_works(client):
    """The guard must not break the flow it protects: the human at the
    terminal that ran `brnrd account connect` opens the printed link and
    the daemon pairs, exactly as before."""
    headers, repo_id = _account(client, github_id="1", login="owner", repo="Gurio/laptop")
    pair = _start_pair(client)

    r = client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    polled = _poll(client, pair).json()
    assert polled["status"] == "paired"
    assert polled["daemon_token"]
    assert polled["repo_id"] == repo_id


def test_a_pair_row_carrying_no_proof_cannot_be_approved_at_all(client):
    """Fail closed on the rollout window. A row written before the column
    existed has no initiator proof, so there is no way to tell an approve
    from the hijack — refuse it and let the 600s TTL retire it."""
    headers, repo_id = _account(client, github_id="1", login="owner", repo="Gurio/laptop")
    pair = _start_pair(client)
    with client.app.state.SessionLocal() as db:
        row = db.execute(
            select(PairRequest).where(PairRequest.pair_code == pair["pair_code"])
        ).scalar_one()
        row.approve_secret_hash = ""
        db.commit()

    r = client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
        headers=headers,
    )
    assert r.status_code == 403, r.text
    assert _pair_row(client, pair["pair_code"]).status == PairRequest.STATUS_PENDING


# --- the hijack, end to end ---------------------------------------------


def test_the_hijack_is_closed_end_to_end(client):
    """The audit's confirmed chain, replayed: the attacker knows the
    victim's live pair code (it is ~20 bits and the poll route is an
    existence oracle) and approves it into their own account. The victim
    daemon must not come back bound to the attacker."""
    victim_headers, victim_repo = _account(
        client, github_id="1", login="victim", repo="Victim/laptop"
    )
    attacker_headers, attacker_repo = _account(
        client, github_id="2", login="attacker", repo="Attacker/box"
    )
    victim_account = None
    with client.app.state.SessionLocal() as db:
        victim_account = db.execute(
            select(Repo).where(Repo.id == victim_repo)
        ).scalar_one().account_id

    # [victim] daemon starts the handshake; only it holds poll+approve secrets.
    pair = _start_pair(client)

    # [attacker] has the code — the whole premise of the finding — and tries
    # to bind it to their own repo/account.
    hijack = client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": attacker_repo},
        headers=attacker_headers,
    )
    assert hijack.status_code == 403, hijack.text

    # [victim] polls: still pending, no token, nothing bound.
    assert _poll(client, pair).json()["status"] == "pending"
    assert _daemon_tokens(client) == []

    # [victim] the human at the terminal opens the printed link (which
    # carries the proof) and approves — the flow still completes, bound to
    # the *victim's* account.
    ok = client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": victim_repo, "approve_secret": pair["approve_secret"]},
        headers=victim_headers,
    )
    assert ok.status_code == 200, ok.text
    polled = _poll(client, pair).json()
    assert polled["status"] == "paired"
    assert polled["account_id"] == victim_account
    tokens = _daemon_tokens(client)
    assert len(tokens) == 1
    assert tokens[0].account_id == victim_account


# --- single approval, atomically ----------------------------------------


def test_a_pair_code_can_only_be_approved_once(client):
    """Even holding the proof, a second approve is refused — before the
    daemon has polled. The old code only refused `consumed`, so an
    already-approved pair could be re-bound (and a second daemon token
    minted) right up until the poll landed."""
    headers, repo_id = _account(client, github_id="1", login="owner", repo="Gurio/laptop")
    second_repo = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/other"},
        headers=headers,
    ).json()["repo_id"]
    pair = _start_pair(client)
    body = {"repo_id": repo_id, "approve_secret": pair["approve_secret"]}

    first = client.post(f"/v1/accounts/pair/{pair['pair_code']}/approve", json=body, headers=headers)
    assert first.status_code == 200, first.text

    again = client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": second_repo, "approve_secret": pair["approve_secret"]},
        headers=headers,
    )
    assert again.status_code == 409, again.text

    # One token, and the binding is still the first approve's.
    assert len(_daemon_tokens(client)) == 1
    assert _pair_row(client, pair["pair_code"]).repo_id == repo_id


def test_a_second_account_cannot_rebind_an_approved_pair(client):
    """The single-approval rule and the initiator rule are separate guards:
    this one holds even if a proof leaks after the fact."""
    owner_headers, repo_id = _account(client, github_id="1", login="owner", repo="Gurio/laptop")
    attacker_headers, attacker_repo = _account(
        client, github_id="2", login="attacker", repo="Attacker/box"
    )
    pair = _start_pair(client)
    assert client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
        headers=owner_headers,
    ).status_code == 200

    r = client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": attacker_repo, "approve_secret": pair["approve_secret"]},
        headers=attacker_headers,
    )
    assert r.status_code == 409, r.text
    polled = _poll(client, pair).json()
    assert polled["repo_id"] == repo_id


def test_a_row_written_without_the_column_defaults_to_unapprovable(client):
    """The fail-closed direction, pinned at the schema layer rather than
    trusted from the application one.

    `approve_secret_hash` defaults to `""`, and `""` is what `approve_core`
    refuses. If that default ever became NULL-and-permissive, or the column
    picked up a truthy default, every legacy row would become approvable by
    anyone again — which is the original finding, restored by a schema edit
    nobody would read as a security change.
    """
    with client.app.state.SessionLocal() as db:
        from datetime import datetime, timedelta, timezone

        from brnrd import ids
        from brnrd.security import hash_token

        row = PairRequest(
            id=ids.pair_request_id(),
            pair_code="BR-OLDX",
            poll_secret_hash=hash_token("s"),
            status=PairRequest.STATUS_PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
        )
        db.add(row)
        db.commit()

    assert _pair_row(client, "BR-OLDX").approve_secret_hash == ""

    headers, repo_id = _account(client, github_id="1", login="owner", repo="Gurio/laptop")
    r = client.post(
        "/v1/accounts/pair/BR-OLDX/approve",
        json={"repo_id": repo_id, "approve_secret": "anything"},
        headers=headers,
    )
    assert r.status_code == 403, r.text
