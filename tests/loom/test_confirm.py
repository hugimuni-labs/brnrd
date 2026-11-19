"""Dashboard presses authorize the signed call and wake its parked wish."""

import base64
import http.client
import json
import subprocess
from datetime import timedelta
from types import SimpleNamespace

import pytest

from brr.loom.runtime import confirm, grants, waits
from brr.loom.runtime.board import Parts, serve
from brr.loom.runtime.config import load_config, set_value
from brr.loom.runtime.home import Home, notice_id
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import LedgerView, kind_facts, read_facts
from test_doors import _shim_home
from test_final_rule import _room
from test_guards import _call
from test_marks import _env
from test_waits import park
from test_wishes import heard, ledger  # noqa: F401


WHAT = "Send the contract"
COMMAND = {"command": "gh pr close 42"}


@pytest.fixture
def seat(tmp_path, monkeypatch):
    room, home = _room(tmp_path)
    (home.root / "loom").mkdir(exist_ok=True)
    (home.root / "loom/install-id").write_text("aaaa\n")
    monkeypatch.setenv("BRNRD_THREAD", "work")
    monkeypatch.setenv("BRNRD_WALL", "1")
    monkeypatch.setenv("BRNRD_MARKS", "1")
    return room, home


def _ask(home):
    ident = confirm.ask(home, "work", WHAT, "w-1", COMMAND)
    return ident, confirm.entry(read_facts(home), ident)["request"]


def _signed(home, request, *, install_key=True):
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    key = ed25519.Ed25519PrivateKey.generate()
    if install_key:
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        set_value(home.root, "confirm_key", public.hex())
    proof = {"verifier": "server-k1", "pressed_by": "person:arseni", "pressed_at": confirm.now().isoformat()}
    proof["sig"] = base64.b64encode(key.sign(confirm.signed_bytes(request, proof))).decode()
    return {"request": request, "proof": proof}


def test_signed_request_and_changed_byte(seat):
    _, home = seat
    _, request = _ask(home)
    token = _signed(home, request)
    assert confirm.verified(home, token, request, confirm.now())
    changed = {**request, "what": "T" + request["what"][1:]}
    assert confirm.verified(home, {**token, "request": changed}, changed, confirm.now()) is None


@pytest.mark.parametrize("verifier", ["server-k1", "imaginary", "passkey"])
def test_forged_or_unsupported_ledger_proof_grants_nothing(seat, verifier):
    room, home = seat
    ident, req = _ask(home)
    token = _signed(home, req, install_key=False)
    token["proof"]["verifier"] = verifier
    confirm.put(home, "given", ident, {"token": token})
    assert not grants.judge(room, WHAT, f"confirm:{ident}", call=COMMAND).granted


def test_call_thread_and_install_are_bound(seat, monkeypatch):
    room, home = seat
    ident, req = _ask(home)
    token = _signed(home, req)
    confirm.ingest(home, {**token, "decision": "given"})
    assert grants.judge(room, WHAT, f"confirm:{ident}", call=COMMAND).granted
    assert not grants.judge(room, WHAT, f"confirm:{ident}", call={"command": "gh pr close 43"}).granted
    monkeypatch.setenv("BRNRD_THREAD", "other")
    assert not grants.judge(room, WHAT, f"confirm:{ident}", call=COMMAND).granted
    assert not confirm.verified(Home(home.root, "bbbb"), token, req, confirm.now())


def test_expired_and_used_proofs_are_refused(seat, monkeypatch):
    room, home = seat
    ident, req = _ask(home)
    confirm.ingest(home, {**_signed(home, req), "decision": "given"})
    approved = grants.judge(room, WHAT, f"confirm:{ident}", call=COMMAND)
    future = confirm.now() + confirm.PROOF_LIFE
    with monkeypatch.context() as patch:
        patch.setattr(confirm, "now", lambda: future)
        assert not grants.judge(room, WHAT, f"confirm:{ident}", call=COMMAND).granted
    assert grants.note(room, approved)
    assert not grants.judge(room, WHAT, f"confirm:{ident}", call=COMMAND).granted
    with pytest.raises(ValueError):
        grants.note(room, approved)


@pytest.mark.parametrize("citation", ["", " · confirm"])
def test_one_receipt_through_hook_and_door(tmp_path, monkeypatch, citation):
    _, room, home = _shim_home(tmp_path, monkeypatch)
    monkeypatch.setenv("BRNRD_THREAD", "work")
    monkeypatch.setenv("BRNRD_WALL", "1")
    request_line = f'BRNRD_SAY="final · {WHAT}{citation}" {COMMAND["command"]}'
    denied = json.loads(execute("pre", room, _call("Bash", command=request_line)))["hookSpecificOutput"]
    assert denied["permissionDecision"] == "deny"
    asked, = kind_facts(read_facts(home), "confirm.asked")
    ident = asked.data["request"]["id"]
    assert f"--wait confirm:{ident}" in denied["permissionDecisionReason"]
    assert confirm.local_page(home, ident, "approve")[0] == 200
    final_mark = f"final · {WHAT} · confirm:{ident}"
    command = f'BRNRD_SAY="{final_mark}" {COMMAND["command"]}'
    given = json.loads(execute("pre", room, _call("Bash", command=command)))["hookSpecificOutput"]
    assert given["permissionDecision"] == "allow"
    env = {**_env(room), "BRNRD_THREAD": "work", "BRNRD_MARKS": "1", "BRNRD_WALL": "1",
           "BRNRD_SAY": final_mark}

    def forge(number):
        return subprocess.run(["gh", "pr", "close", number], env=env,
                              capture_output=True, text=True, timeout=20)

    assert forge("43").returncode != 0
    allowed = forge("42")
    assert allowed.returncode == 0, allowed.stderr
    assert forge("42").returncode != 0
    assert "deny" in execute("pre", room, _call("Bash", command=command))
    facts = read_facts(home)
    grant, = kind_facts(facts, "guard.granted")
    assert grant.data["letter"] == ident
    assert len(kind_facts(facts, "confirm.used")) == 1


@pytest.fixture
def board(seat):
    _, home = seat
    server = serve(Parts(home, lambda: load_config(home.root)), 0)
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _press(port, ident, decision="approve"):
    connection = http.client.HTTPConnection("localhost", port, timeout=5)
    try:
        connection.request("POST", f"/v1/loom/confirms/{ident}", json.dumps({"decision": decision}),
                           {"Content-Type": "application/json", "Origin": f"http://localhost:{port}"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_local_http_press_answers_only_live_requests(seat, board, monkeypatch):
    _, home = seat
    ident, req = _ask(home)
    assert _press(board, "c-000000000000")[0] == 404
    with monkeypatch.context() as patch:
        patch.setattr(confirm, "now", lambda: confirm.instant(req["ask_exp"]))
        assert _press(board, ident)[0] == 409
    assert _press(board, ident)[0] == 200
    assert _press(board, ident)[0] == 409
    given, = kind_facts(read_facts(home), "confirm.given")
    assert given.data["token"]["proof"]["verifier"] == "local"


def test_cloud_config_disables_local_press_until_opted_in(seat, board):
    _, home = seat
    set_value(home.root, "connectors", ("relay",))
    ident, _ = _ask(home)
    assert _press(board, ident)[0] == 403
    assert not kind_facts(read_facts(home), "confirm.given")
    set_value(home.root, "confirm_local", "on")
    assert _press(board, ident)[0] == 200


@pytest.mark.parametrize("decision, expected", [("approve", "given"), ("decline", "refused"), (None, "expired")])
def test_confirmation_park_wakes_its_thread(ledger, decision, expected):
    home, seat = ledger
    wish = heard(home, seat, "Send the contract")
    ident = confirm.ask(home, "work", WHAT, wish, COMMAND)
    read = park(home, seat, wish, "--wait", f"confirm:{ident}")
    assert read["wait"]["on"] == f"confirm:{ident}"
    facts = LedgerView(read_facts(home))
    waits.observe_waits(home, facts, True, now=confirm.now())
    before = {f.id for f in read_facts(home)}
    if decision:
        assert confirm.local_page(home, ident, decision)[0] == 200
        for fact in kind_facts(read_facts(home), "confirm.given", "confirm.refused"):
            facts.append(fact)
        moment = confirm.now()
    else:
        moment = confirm.now() + confirm.ASK_LIFE
    waits.observe_waits(home, facts, True, now=moment)
    notice, = [f for f in kind_facts(read_facts(home), "letter") if f.id not in before]
    assert notice.data["to"] == "thread:work"
    assert f"confirm:{ident} {expected}" in notice.data["body"]


def test_pressed_unused_expiry_is_told_once(seat, monkeypatch):
    _, home = seat
    ident, _ = _ask(home)
    confirm.local_page(home, ident, "approve")
    from brr.loom.runtime import verbs_wishes
    monkeypatch.setattr(verbs_wishes, "_person_dm", lambda *_: "channel:relay/123")
    router = SimpleNamespace(config=load_config(home.root), lease=None, armed=lambda: True)
    notifications = confirm.Notifications(home)
    facts = list(read_facts(home))
    notifications.step(router, facts)
    expired = confirm.now() + confirm.PROOF_LIFE
    monkeypatch.setattr(confirm, "now", lambda: expired)
    notifications.step(router, facts)
    notifications.step(router, facts)
    expiry, = [f.data["body"] for f in read_facts(home) if f.id == notice_id("confirm-expired", ident)]
    assert "nothing was done. It has expired." in expiry


def test_typed_reply_lands_as_letter_and_points_back_to_page(seat):
    import connector_support
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.ledger import append
    from brr.loom.runtime.loom import notice

    _, home = seat
    person = home.root / "self/people/arseni/channels.toml"
    person.write_text('channels = ["relay:telegram:42"]\n')
    connector_support.enable(home)
    channel = "channel:relay/telegram:42"
    ident, _ = _ask(home)
    ask_id = notice_id("confirm-asked", ident)
    notice(home, read_facts(home), "confirm-asked", ident, "Please press", "", channel=channel)
    append(home, Fact(kind="speech.part", by="loom:aaaa", data={
        "key": ask_id, "channel": channel, "receipt": {"message_id": "telegram:42:21"},
    }))
    event = {
        "event_id": "ev-22", "source": "telegram", "body": "ok",
        "reply_to": {"platform": "telegram", "chat_id": 42, "user_id": 42,
                     "message_id": 22, "reply_to_message_id": 21},
    }
    connector_support.pull_once(home, SimpleNamespace(pull=lambda _: {"events": [event], "cursor": 22}), 0)
    facts = read_facts(home)
    reply, = [f for f in kind_facts(facts, "letter") if f.data.get("body") == "ok"]
    assert reply.data["from"] == "person:arseni"
    response, = [f for f in kind_facts(facts, "letter") if f.id == notice_id("confirm-reply", reply.id)]
    assert response.data["to"] == channel
    assert response.data["body"] == "Press it on the page: a message can't confirm this."
    assert not kind_facts(facts, "confirm.given")


def test_forge_stand_in_asks_for_final_calls_outside_its_audited_verbs(tmp_path, monkeypatch):
    _, room, home = _shim_home(tmp_path, monkeypatch)
    argv = ["gh", "repo", "archive", "example/test"]
    env = {**_env(room), "BRNRD_THREAD": "work", "BRNRD_MARKS": "1",
           "BRNRD_SAY": "final · archive example/test · confirm"}
    env.pop("BRNRD_WALL", None)

    def invoke():
        return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)

    blocked = invoke()
    assert blocked.returncode != 0
    asked, = kind_facts(read_facts(home), "confirm.asked")
    ident = asked.data["request"]["id"]
    assert f"--wait confirm:{ident}" in blocked.stderr
    assert confirm.local_page(home, ident, "approve")[0] == 200
    env["BRNRD_SAY"] = f"final · archive example/test · confirm:{ident}"
    allowed = invoke()
    assert allowed.returncode == 0, allowed.stderr
    assert invoke().returncode != 0
    assert len(kind_facts(read_facts(home), "guard.granted")) == 1
