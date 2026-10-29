"""The board's parts in the cloud: the loom pushes them, a signed-in person reads them."""

from __future__ import annotations

import gzip
import json

import pytest

pytest.importorskip("fastapi")

from test_brnrd_loom import _account, _relay_token, _repo, _signed_in, client  # noqa: E402,F401

LETTERS = [{"id": f"letter:{i}", "at": f"2026-10-28T00:{i:02d}:00Z", "body": f"secret {i}"} for i in range(5)]
PARTS = {
    "index": {"v": 1, "threads": [{"id": "web-main"}], "parts": {"thread/web-main": "t1", "beads": "b1"}},
    "thread/web-main": {"id": "web-main", "count": 5, "letters": LETTERS, "wishes": [], "tools": {}, "live": {}},
    "beads": {"items": []},
}


def _push(client, relay, parts=PARTS, keys=None, hashes=None, gz=True):
    hashes = hashes or {k: f"h-{k}" for k in parts}
    doc = {"keys": list(keys if keys is not None else parts),
           "parts": {k: {"hash": hashes[k], "part": v} for k, v in parts.items()}}
    raw = json.dumps(doc).encode()
    headers = {**relay, "Content-Type": "application/json"}
    if gz:
        raw, headers["Content-Encoding"] = gzip.compress(raw), "gzip"
    return client.put("/v1/daemons/loom/parts", content=raw, headers=headers)


def test_pushed_parts_read_back_for_their_account(client):
    headers, session = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    r = _push(client, relay)
    assert r.status_code == 200, r.text
    assert r.json()["hashes"] == {"index": "h-index", "thread/web-main": "h-thread/web-main", "beads": "h-beads"}

    _signed_in(client, session)
    index = client.get("/v1/loom/index")
    assert index.status_code == 200 and index.json() == PARTS["index"]
    assert index.headers["etag"] == '"h-index"'
    assert client.get("/v1/loom/index", headers={"If-None-Match": '"h-index"'}).status_code == 304
    assert client.get("/v1/loom/beads").json() == {"items": []}
    thread = client.get("/v1/loom/thread/web-main").json()
    assert [row["id"] for row in thread["letters"]] == [row["id"] for row in LETTERS]
    older = client.get("/v1/loom/thread/web-main", params={"before": "letter:3"}).json()
    assert [row["id"] for row in older["letters"]] == ["letter:0", "letter:1", "letter:2"]
    assert client.get("/v1/loom/thread/nope").status_code == 404


def test_a_second_account_sees_none_of_the_parts(client):
    headers, _ = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    other_headers, other_session = _account(client, login="mallory")
    # The other account has a loom of its own; arseni's pushes last, so the
    # newest index on the cloud is not theirs.
    other_relay = _relay_token(client, other_headers, _repo(client, other_headers, name="hers"))
    mine = {"index": {"v": 1, "threads": [], "parts": {}}}
    assert _push(client, other_relay, parts=mine).status_code == 200
    assert _push(client, relay).status_code == 200

    _signed_in(client, other_session)
    assert client.get("/v1/loom/index").json() == mine["index"]
    assert client.get("/v1/loom/thread/web-main").status_code == 404
    assert client.get("/v1/loom/beads").status_code == 404
    for path in ("/v1/loom/index", "/v1/loom/thread/web-main", "/v1/loom/beads"):
        assert "secret" not in client.get(path).text


def test_signed_out_reads_are_401(client):
    _signed_in(client, None)
    assert client.get("/v1/loom/index").status_code == 401
    assert client.get("/v1/loom/events").status_code == 401


def test_parts_need_a_daemon_token(client):
    headers, session = _account(client)
    _repo(client, headers)
    _signed_in(client, session)
    assert _push(client, {}).status_code in (401, 403)
    assert _push(client, headers).status_code == 403


def test_a_part_that_left_the_keys_leaves_the_store(client):
    headers, session = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    assert _push(client, relay).status_code == 200
    r = _push(client, relay, parts={}, keys=["index", "beads"])
    assert set(r.json()["hashes"]) == {"index", "beads"}
    _signed_in(client, session)
    assert client.get("/v1/loom/thread/web-main").status_code == 404


def test_consent_off_withholds_and_drops_the_parts(client):
    from brnrd import publish_scope
    from brnrd.models import Repo

    headers, session = _account(client)
    repo_id = _repo(client, headers)
    relay = _relay_token(client, headers, repo_id)
    assert _push(client, relay).status_code == 200
    with client.app.state.SessionLocal() as db:
        repo = db.get(Repo, repo_id)
        publish_scope.purge_removed_scope(db, repo=repo, new_publish_layers="quota")
        repo.publish_layers = "quota"
        db.commit()
    _signed_in(client, session)
    assert client.get("/v1/loom/index").status_code == 404
    _signed_in(client, None)
    assert _push(client, relay).status_code == 451
    _signed_in(client, session)
    assert client.get("/v1/loom/index").status_code == 404


def test_bad_pushes_are_refused(client):
    headers, _ = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    assert _push(client, relay, parts={"../etc": {}}).status_code == 422
    assert _push(client, relay, keys=["index"]).status_code == 422
    assert client.put("/v1/daemons/loom/parts", content=b"not gzip",
                      headers={**relay, "Content-Encoding": "gzip"}).status_code == 400


def test_events_send_the_index_hash(client, monkeypatch):
    from brnrd.routers import loom

    monkeypatch.setattr(loom, "EVENTS_LIFE_S", 0.3)
    monkeypatch.setattr(loom, "EVENTS_POLL_S", 0.05)
    headers, session = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    assert _push(client, relay).status_code == 200
    _signed_in(client, session)
    r = client.get("/v1/loom/events")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: index\ndata: h-index\n\n" in r.text


def test_a_board_letter_goes_through_the_relay(client):
    headers, session = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    _signed_in(client, session)
    r = client.post("/v1/loom/letters", json={"body": "ship it", "wish": "w-abc123", "verdict": "agree"})
    assert r.status_code == 200, r.text
    assert r.json()["thread"] == "inbox"
    _signed_in(client, None)
    events = client.get("/v1/daemons/inbox", params={"wait": 0}, headers=relay).json()["events"]
    assert events[0]["body"] == "ship it"
    assert events[0]["reply_to"]["wish"] == "w-abc123"
    _signed_in(client, session)
    assert client.post("/v1/loom/letters", json={"body": "  "}).status_code == 400


def test_the_loom_pusher_lands_only_moved_parts_through_the_cloud(client, monkeypatch, tmp_path):
    """brr's Pusher against this app: the first push lands every part, the next only the moved one."""
    import threading
    from types import SimpleNamespace

    from brr.gates import cloud
    from brr.loom.runtime.board import Pusher

    headers, session = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    token = relay["Authorization"].split(" ", 1)[1]
    monkeypatch.setattr(cloud, "_load_state_from_dir", lambda d: {"brnrd_url": "http://testserver", "token": token})
    puts = []

    def request(method, url, *, data, headers, timeout):
        puts.append(json.loads(gzip.decompress(data))["parts"])
        _signed_in(client, None)
        return client.request(method, url.removeprefix("http://testserver"), content=data, headers=headers)

    monkeypatch.setattr(cloud._SESSION, "request", request)
    board = SimpleNamespace(parts=dict(PARTS), hashes={k: f"h-{k}" for k in PARTS}, changed=threading.Condition())
    pusher = Pusher(board)
    pusher.EVERY = 0
    config = SimpleNamespace(relay_state=str(tmp_path))

    pusher.step(config)
    pusher.worker.join(5)
    assert set(puts[0]) == set(PARTS) and list(puts[0])[-1] == "index"
    assert pusher.sent == board.hashes

    moved = dict(PARTS["thread/web-main"], letters=LETTERS[:2])
    board.parts = {**PARTS, "thread/web-main": moved}
    board.hashes = {**board.hashes, "thread/web-main": "h-moved"}
    pusher.step(config)
    pusher.worker.join(5)
    assert set(puts[1]) == {"thread/web-main"}
    pusher.step(config)  # nothing moved: no push
    assert len(puts) == 2 and pusher.failing is None

    _signed_in(client, session)
    got = client.get("/v1/loom/thread/web-main").json()
    assert [row["id"] for row in got["letters"]] == ["letter:0", "letter:1"]


def test_the_loom_pusher_raises_a_refused_push(client, monkeypatch, tmp_path):
    import threading
    from types import SimpleNamespace

    from brr.gates import cloud
    from brr.loom.runtime.board import Pusher

    monkeypatch.setattr(cloud, "_load_state_from_dir", lambda d: {"brnrd_url": "http://x", "token": "t"})
    monkeypatch.setattr(cloud._SESSION, "request",
                        lambda *a, **k: SimpleNamespace(status_code=403, text="withheld", json=dict))
    # a 403 that is not the consent refusal stays a plain failure
    board = SimpleNamespace(parts={"index": {}}, hashes={"index": "h"}, changed=threading.Condition())
    pusher = Pusher(board)
    config = SimpleNamespace(relay_state=str(tmp_path))
    try:
        pusher.step(config)  # the worker may already have failed when the step checks
    except RuntimeError:
        pass
    pusher.worker.join(5)
    with pytest.raises(RuntimeError, match="403"):
        pusher.step(config)


def test_the_loom_pusher_names_a_withheld_board(monkeypatch, tmp_path):
    import threading
    from types import SimpleNamespace

    from brr.gates import cloud
    from brr.loom.runtime.board import BoardWithheld, Pusher

    detail = '{"detail":"this repo\'s publish scope withholds the activity lane"}'
    monkeypatch.setattr(cloud, "_load_state_from_dir", lambda d: {"brnrd_url": "http://x", "token": "t"})
    monkeypatch.setattr(cloud._SESSION, "request",
                        lambda *a, **k: SimpleNamespace(status_code=451, text=detail, json=dict))
    board = SimpleNamespace(parts={"index": {}}, hashes={"index": "h"}, changed=threading.Condition())
    pusher = Pusher(board)
    config = SimpleNamespace(relay_state=str(tmp_path))
    try:
        pusher.step(config)
    except RuntimeError:
        pass
    pusher.worker.join(5)
    with pytest.raises(BoardWithheld, match="said yes"):
        pusher.step(config)


def _loom_without_consent(client):
    """A loom connected as `brnrd account connect` does it: no scope sent, so `none`, and one refused push."""
    headers, session = _account(client)
    repo_id = _repo(client, headers, publish_layers=None)
    relay = _relay_token(client, headers, repo_id)
    assert _push(client, relay).status_code == 451
    return session, relay, repo_id


def test_consent_is_none_without_a_loom_and_401_signed_out(client):
    _, session = _account(client)
    _signed_in(client, None)
    assert client.get("/v1/loom/consent").status_code == 401
    assert client.post("/v1/loom/consent", json={"send": True}).status_code == 401
    _signed_in(client, session)
    assert client.get("/v1/loom/consent").json() == {"state": "none"}
    assert client.post("/v1/loom/consent", json={"send": True}).status_code == 404


def test_a_new_loom_is_asked_and_a_yes_lets_its_board_in(client):
    from brnrd.models import BoardConsent, Repo

    session, relay, repo_id = _loom_without_consent(client)
    # installs before the withheld status read the refusal by its text, so the 451 keeps it
    assert _push(client, relay).json()["detail"] == "this repo's publish scope withholds the activity lane"
    _signed_in(client, session)
    got = client.get("/v1/loom/consent").json()
    assert got["state"] == "ask" and got["repo"] == "Gurio/demo" and got["last_seen_at"]
    assert client.get("/v1/loom/index").status_code == 404
    assert client.post("/v1/loom/consent", json={"send": "yes"}).status_code == 400

    assert client.post("/v1/loom/consent", json={"send": True}).json() == {"state": "granted"}
    assert client.get("/v1/loom/consent").json()["state"] == "granted"
    with client.app.state.SessionLocal() as db:
        repo = db.get(Repo, repo_id)
        assert repo.publish_layers == "activity" and db.get(BoardConsent, repo_id) is not None
    _signed_in(client, None)
    assert _push(client, relay).status_code == 200
    _signed_in(client, session)
    assert client.get("/v1/loom/index").json() == PARTS["index"]


def test_a_no_is_remembered_and_drops_the_copy_but_keeps_other_lanes(client):
    from brnrd.models import Repo

    headers, session = _account(client)
    repo_id = _repo(client, headers, publish_layers="activity,quota,authored")
    relay = _relay_token(client, headers, repo_id)
    assert _push(client, relay).status_code == 200
    _signed_in(client, session)
    assert client.get("/v1/loom/consent").json()["state"] == "granted"

    assert client.post("/v1/loom/consent", json={"send": False}).json() == {"state": "declined"}
    assert client.get("/v1/loom/consent").json()["state"] == "declined"
    assert client.get("/v1/loom/index").status_code == 404
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id).publish_layers == "quota,authored"
    _signed_in(client, None)
    assert _push(client, relay).status_code == 451

    _signed_in(client, session)
    client.post("/v1/loom/consent", json={"send": True})
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id).publish_layers == "activity,quota,authored"


def test_consent_is_scoped_to_the_signed_in_account(client):
    session, _, _ = _loom_without_consent(client)
    _, other = _account(client, login="sasha")
    _signed_in(client, other)
    assert client.get("/v1/loom/consent").json() == {"state": "none"}
    assert client.post("/v1/loom/consent", json={"send": True}).status_code == 404
    _signed_in(client, session)
    assert client.get("/v1/loom/consent").json()["state"] == "ask"


def test_the_pusher_names_a_withheld_board_until_a_yes_on_brnrd_dev_clears_it(client, monkeypatch, tmp_path):
    """Against the real app: `account connect`'s scope `none` refuses with the withheld status, the pusher raises the
    named condition (the health guard tells it once), and the person's yes lets the next push land and clears it."""
    import threading
    from types import SimpleNamespace

    from brr.gates import cloud
    from brr.loom.runtime.board import BoardWithheld, Pusher

    session, relay, _ = _loom_without_consent(client)
    token = relay["Authorization"].split(" ", 1)[1]
    monkeypatch.setattr(cloud, "_load_state_from_dir", lambda d: {"brnrd_url": "http://testserver", "token": token})

    def request(method, url, *, data, headers, timeout):
        _signed_in(client, None)
        return client.request(method, url.removeprefix("http://testserver"), content=data, headers=headers)

    monkeypatch.setattr(cloud._SESSION, "request", request)
    hashes = {k: f"h-{k}" for k in PARTS}
    pusher = Pusher(SimpleNamespace(parts=dict(PARTS), hashes=hashes, changed=threading.Condition()))
    pusher._put(tmp_path, dict(PARTS), hashes)  # the worker's body, run here so no thread races the asserts
    assert isinstance(pusher.failing[0], BoardWithheld) and "board withheld" in str(pusher.failing[0])

    _signed_in(client, session)
    assert client.post("/v1/loom/consent", json={"send": True}).status_code == 200
    pusher._put(tmp_path, dict(PARTS), hashes)
    assert pusher.failing is None and pusher.sent == hashes
