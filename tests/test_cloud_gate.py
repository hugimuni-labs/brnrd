"""Tests for the daemon-side ``cloud`` gate against a live brnrd app."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.inbox import CapturingForwarder  # noqa: E402
from brr import protocol  # noqa: E402
from brr.gates import cloud  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402


def _make_brnrd():
    forwarder = CapturingForwarder()
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            inbox_long_poll_max_s=0.1,
            inbox_poll_interval_s=0.02,
        ),
        forwarder=forwarder,
    )
    return TestClient(app), forwarder


def _route_to(client):
    """A ``cloud._request`` replacement that talks to the TestClient."""

    def fake_request(base_url, method, path, *, token=None, json=None,
                     params=None, timeout=60):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = client.request(method, path, json=json, params=params, headers=headers)
        if not 200 <= resp.status_code < 300:
            raise RuntimeError(f"{resp.status_code}: {resp.text}")
        return resp.json() if resp.content else {}

    return fake_request


def _account_and_project(client):
    headers = brnrd_account_headers(
        client.app, github_id="123", login="octocat", email="a@b.com"
    )
    pid = client.post(
        "/v1/accounts/projects", json={"name": "demo"}, headers=headers
    ).json()["project_id"]
    return headers, pid


def _handshake(client, acc_headers, pid):
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"project_id": pid},
        headers=acc_headers,
    )
    paired = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    return paired["daemon_token"]


def test_relay_pack_returns_render_url_that_renders(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    client, _ = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "project_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    pack = {
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
    url = cloud.relay_pack(brr_dir, pack)
    assert url and "/r/" in url
    # brnrd renders the relayed pack live at the returned capability URL.
    page = client.get(url[url.index("/r/"):])
    assert page.status_code == 200
    assert "the change in shape" in page.text


def test_relay_pack_noop_without_config(tmp_path):
    # Self-hosted mode (no cloud state) -> no relay, no rich link.
    assert cloud.relay_pack(tmp_path / ".brr", {"cards": []}) is None


def test_connect_persists_token(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    scripted = iter(
        [
            {"pair_code": "BR-TEST", "pair_url": "u", "poll_secret": "s"},
            {"status": "pending"},
            {"status": "paired", "project_id": "proj_x", "daemon_token": "bd_tok"},
        ]
    )
    seen = []

    def fake_request(base_url, method, path, **kwargs):
        seen.append((method, path))
        return next(scripted)

    monkeypatch.setattr(cloud, "_request", fake_request)
    state = cloud.connect(
        brr_dir,
        brnrd_url="http://brnrd.example",
        daemon_name="laptop",
        poll_interval_s=0,
        timeout_s=5,
        out=lambda *_: None,
    )
    assert state["token"] == "bd_tok"
    assert state["project_id"] == "proj_x"
    assert state["daemon_name"] == "laptop"
    # Persisted to .brr/gates/cloud.json and reports configured.
    assert cloud._load_state(brr_dir)["token"] == "bd_tok"
    assert cloud.is_configured(brr_dir)
    assert ("POST", "/v1/accounts/pair") in seen


def test_drain_deliver_and_cursor_resume(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, forwarder = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "project_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    # Two events queued on the brnrd side.
    e1 = client.post(
        "/v1/_dev/enqueue",
        json={"project_id": pid, "body": "first", "reply_to": {"chat": 1}},
        headers=acc,
    ).json()["event_id"]
    e2 = client.post(
        "/v1/_dev/enqueue", json={"project_id": pid, "body": "second"}, headers=acc
    ).json()["event_id"]

    # Drain: events land as local .brr/inbox files carrying the cloud id.
    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    pending = sorted(protocol.list_pending(inbox_dir), key=lambda ev: ev["body"])
    assert [ev["body"] for ev in pending] == ["first", "second"]
    assert [ev["source"] for ev in pending] == ["cloud", "cloud"]
    assert {ev["cloud_event_id"] for ev in pending} == {e1, e2}
    # Cursor advanced and persisted.
    assert cloud._load_state(brr_dir)["since"] == 2

    # Simulate the runner finishing both tasks.
    for ev in pending:
        protocol.set_status(ev, "done")
        protocol.write_response(responses_dir, ev["id"], f"answer to {ev['body']}")

    # Next loop: nothing new to drain, deliver the two responses back.
    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    delivered = {item.event_id: item.body for item in forwarder.items}
    assert delivered == {e1: "answer to first", e2: "answer to second"}
    # Delivered events + their response files are cleaned up locally.
    assert protocol.list_done(inbox_dir, "cloud") == []

    # Restart resume: a fresh load still has the advanced cursor, so a
    # new loop doesn't re-drain the already-handled events.
    assert cloud._load_state(brr_dir)["since"] == 2
    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    assert protocol.list_pending(inbox_dir) == []


def test_drain_preserves_github_origin_metadata(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, _ = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "project_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    client.post(
        "/v1/_dev/enqueue",
        json={
            "project_id": pid,
            "body": "@brr-bot fix this",
            "source": "github",
            "reply_to": {
                "platform": "github",
                "repo": "owner/repo",
                "issue_number": 17,
                "comment_id": 100,
                "kind": "pr-comment",
                "author": "alice",
                "html_url": "https://github.com/owner/repo/pull/17#issuecomment-100",
                "trigger": "mention",
                "mention": "@brr-bot",
                "pr_number": 17,
                "branch_target": "feature-x",
            },
        },
        headers=acc,
    )

    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    pending = protocol.list_pending(inbox_dir)
    assert len(pending) == 1
    ev = pending[0]
    assert ev["source"] == "cloud"
    assert ev["cloud_platform"] == "github"
    assert ev["cloud_chat_id"] == "owner/repo#17"
    assert ev["github_repo"] == "owner/repo"
    assert ev["github_kind"] == "pr-comment"
    assert ev["github_issue_number"] == 17
    assert ev["github_comment_id"] == 100
    assert ev["github_author"] == "alice"
    assert ev["github_trigger"] == "mention"
    assert ev["github_mention"] == "@brr-bot"
    assert ev["github_pr_number"] == 17
    assert ev["branch_target"] == "feature-x"


def test_drain_preserves_telegram_origin_identity(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, _ = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "project_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    client.post(
        "/v1/_dev/enqueue",
        json={
            "project_id": pid,
            "body": "fix from telegram",
            "source": "telegram",
            "reply_to": {
                "platform": "telegram",
                "chat_id": 555,
                "topic_id": 9,
                "message_id": 100,
                "user": "Ada",
                "user_id": 42,
                "username": "ada_l",
            },
        },
        headers=acc,
    )

    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    pending = protocol.list_pending(inbox_dir)
    assert len(pending) == 1
    ev = pending[0]
    assert ev["source"] == "cloud"
    assert ev["cloud_platform"] == "telegram"
    assert ev["cloud_chat_id"] == 555
    assert ev["cloud_topic_id"] == 9
    assert ev["cloud_message_id"] == 100
    assert ev["cloud_user"] == "Ada"
    assert ev["cloud_user_id"] == 42
    assert ev["cloud_username"] == "ada_l"


def test_loop_skips_delivery_without_cloud_event_id(tmp_path, monkeypatch):
    # A foreign event (no cloud_event_id) must not be posted to brnrd;
    # it is logged + skipped, leaving its files in place for triage.
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, forwarder = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "project_id": pid, "since": 99},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    ev = protocol.create_event(inbox_dir, source="cloud", body="orphan")
    event = protocol.list_pending(inbox_dir)[0]
    protocol.set_status(event, "done")
    protocol.write_response(responses_dir, event["id"], "x")

    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    assert forwarder.items == []
    assert ev.exists()


def test_render_update_relays_card_through_the_cloud_transport(tmp_path, monkeypatch):
    """A cloud task's progress card is rendered locally and POSTed to the
    brnrd card relay — send first, edit-in-place on later packets."""
    from brr import updates
    from brr.run import Run

    brr_dir = tmp_path / ".brr"
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": "tok", "project_id": "p", "since": 0},
    )

    posts: list[tuple[str, dict]] = []

    def fake_request(base_url, method, path, *, token=None, json=None,
                     params=None, timeout=60):
        posts.append((path, json or {}))
        return {"message_id": 9}

    monkeypatch.setattr(cloud, "_request", fake_request)

    # Seed a cloud task as the drain + runner would: source=cloud, origin
    # telegram, carrying the discrete routing fields render_update reads.
    conv_key = "cloud:telegram:555:"
    runs_dir = brr_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    task = Run(
        id="task-cloud-1", event_id="ev-1", body="x", env="docker",
        status="running", source="cloud", conversation_key=conv_key,
        meta={"cloud_event_id": "brnrd-evt-1", "cloud_platform": "telegram",
              "cloud_chat_id": 555},
    )
    task.save(runs_dir)

    def _emit(ptype, **payload):
        updates.emit(brr_dir, updates.UpdatePacket(
            type=ptype, conversation_key=conv_key, event_id="ev-1",
            payload={"run_id": task.id, "event_id": "ev-1", **payload},
        ))

    _emit("run_created", branch="auto", env="docker")
    cards = [body for path, body in posts if path == "/v1/daemons/card"]
    assert len(cards) == 1
    assert cards[0]["event_id"] == "brnrd-evt-1"
    assert "message_id" not in cards[0]      # first call is a send
    assert cards[0]["text"]                  # rendered card text present

    _emit("finalizing")
    cards = [body for path, body in posts if path == "/v1/daemons/card"]
    assert len(cards) == 2
    assert cards[1]["message_id"] == 9       # edit replays the returned id


def test_request_raises_auth_error_on_401(monkeypatch):
    class FakeResp:
        status_code = 401
        text = '{"detail":"invalid token"}'
        content = b'{"detail":"invalid token"}'

    monkeypatch.setattr(cloud._SESSION, "request", lambda *a, **k: FakeResp())
    with pytest.raises(cloud.BrnrdAuthError, match="invalid token"):
        cloud._request("http://brnrd", "GET", "/v1/daemons/inbox", token="bad")


def test_run_loop_exits_on_auth_error(tmp_path, monkeypatch):
    import threading

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    cloud._save_state(
        brr_dir,
        {
            "brnrd_url": "http://brnrd",
            "token": "bd_bad",
            "project_id": "proj_x",
            "since": 0,
        },
    )

    def fail_request(*_a, **_k):
        raise cloud.BrnrdAuthError("invalid token")

    monkeypatch.setattr(cloud, "_request", fail_request)
    thread = threading.Thread(
        target=cloud.run_loop,
        args=(brr_dir, inbox_dir, responses_dir),
        daemon=True,
    )
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
