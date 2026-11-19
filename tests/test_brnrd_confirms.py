"""Only the install's web-session owner can issue a confirmation proof."""

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import connector_support

from brr.loom.runtime import confirm
from brr.loom.runtime.config import set_value
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from test_brnrd_loom import _account, _repo, _relay_token, _signed_in, client  # noqa: F401


@pytest.mark.parametrize("decision", ["approve", "decline"])
def test_only_owner_can_press_and_answer_rides_inbox(client, tmp_path, monkeypatch, decision):
    home = Home(tmp_path / "loom-home", "aaaa")
    ident = confirm.ask(home, "work", "Send the contract", "w-1", {"command": "echo ready"})
    row = confirm.entry(read_facts(home), ident)
    req = row["request"]
    owner, owner_session = _account(client, "owner")
    _, stranger_session = _account(client, "stranger")
    relay = _relay_token(client, owner, _repo(client, owner))
    endpoint = f"/v1/dashboard/loom/confirms/{ident}"
    assert client.post("/v1/daemons/confirms", json=row, headers=relay).status_code == 200
    assert client.post("/v1/daemons/confirms", json=row, headers=relay).status_code == 200
    _signed_in(client, None)
    assert client.post(endpoint, json={"decision": decision}).status_code == 401
    _signed_in(client, stranger_session)
    assert client.get(endpoint).status_code == 404
    assert client.post(endpoint, json={"decision": decision}).status_code == 404
    _signed_in(client, owner_session)
    assert client.get(endpoint).json()["request"] == req
    assert client.post(endpoint, json={"decision": decision}, headers={"Origin": "https://elsewhere.test"}).status_code == 403

    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("BRNRD_CONFIRM_K1_SECRET", seed.hex())
    set_value(home.root, "confirm_key", public.hex())
    page = client.post(endpoint, json={"decision": decision})
    assert page.status_code == 200, page.text
    assert client.post(endpoint, json={"decision": decision}).status_code == 409
    inbox = client.get("/v1/daemons/inbox?wait=0", headers=relay).json()
    event, = inbox["events"]
    assert event["source"] == "confirm"
    relay_client = SimpleNamespace(pull=lambda _: inbox)
    assert connector_support.pull_once(home, relay_client, 0) == inbox["cursor"]
    assert connector_support.pull_once(home, relay_client, 0) == inbox["cursor"]
    assert confirm.state(home, read_facts(home), req, confirm.now())[0] == (
        "given" if decision == "approve" else "refused")
