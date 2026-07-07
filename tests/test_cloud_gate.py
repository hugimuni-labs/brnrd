"""Tests for the daemon-side ``cloud`` gate against a live brnrd app."""

from __future__ import annotations

import os

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
        "/v1/accounts/repos", json={"repo_full_name": "Gurio/demo"}, headers=headers
    ).json()["repo_id"]
    return headers, pid


def _handshake(client, acc_headers, pid):
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": pid},
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
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 0},
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
            {
                "status": "paired",
                "account_id": "acct_x",
                "repo_id": "proj_x",
                "daemon_token": "bd_tok",
                "telegram_pair": {
                    "pair_code": "TG-TEST",
                    "instructions": "Open https://t.me/brnrd_bot?start=TG-TEST",
                    "deep_link": "https://t.me/brnrd_bot?start=TG-TEST",
                },
            },
        ]
    )
    seen = []

    def fake_request(base_url, method, path, **kwargs):
        seen.append((method, path))
        return next(scripted)

    monkeypatch.setattr(cloud, "_request", fake_request)
    output: list[str] = []
    state = cloud.connect(
        brr_dir,
        brnrd_url="http://brnrd.example",
        daemon_name="laptop",
        poll_interval_s=0,
        timeout_s=5,
        out=output.append,
    )
    assert state["token"] == "bd_tok"
    assert state["account_id"] == "acct_x"
    assert state["repo_id"] == "proj_x"
    assert state["daemon_name"] == "laptop"
    # Persisted to .brr/gates/cloud.json and reports configured.
    assert cloud._load_state(brr_dir)["token"] == "bd_tok"
    assert cloud.is_configured(brr_dir)
    assert ("POST", "/v1/accounts/pair") in seen
    assert output == [
        "[brr] Approve this daemon at: u",
        "[brr] Connected to brnrd repo proj_x.",
        "[brnrd] Pair Telegram chat: https://t.me/brnrd_bot?start=TG-TEST",
        "[brnrd] If Telegram only opens the chat, send: /start TG-TEST",
    ]


def test_drain_deliver_and_cursor_resume(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, forwarder = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    # Two events queued on the brnrd side.
    e1 = client.post(
        "/v1/_dev/enqueue",
        json={"repo_id": pid, "body": "first", "reply_to": {"chat": 1}},
        headers=acc,
    ).json()["event_id"]
    e2 = client.post(
        "/v1/_dev/enqueue", json={"repo_id": pid, "body": "second"}, headers=acc
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


def test_loop_publishes_local_activity_snapshot(tmp_path, monkeypatch):
    from brr.run import Run

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, _ = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    daemon_headers = {"Authorization": f"Bearer {token}"}
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    Run(
        id="run-cloud-activity",
        event_id="evt-run",
        body="wire the activity page",
        status="running",
        source="telegram",
        conversation_key="telegram:42:",
        meta={
            "runner_shell": "codex",
            "runner_core": "gpt-5-codex",
            "runner_class": "balanced",
            "publish_status": "coding",
            "branch_name": "brr/activity",
            "pr_number": 205,
        },
    ).save(brr_dir / "runs")
    dom = brr_dir / "dominion"
    dom.mkdir(parents=True)
    (dom / "schedule.md").write_text(
        "## Daily Sweep\nat: 2999-01-01T00:00:00Z\nrun upkeep\n",
        encoding="utf-8",
    )
    respawn = protocol.create_event(
        inbox_dir,
        source="telegram",
        body="retry this on a stronger runner",
        respawned_from_event="evt-parent",
        respawn_reason="quality",
        runner_shell="claude",
        runner_core="claude-opus",
        defer_until="2999-01-01T01:00:00Z",
    )

    cloud._loop_once(brr_dir, inbox_dir, responses_dir)

    listing = client.get("/v1/accounts/activity", headers=acc)
    assert listing.status_code == 200
    rows = {row["id"]: row for row in listing.json()["activity"]}
    assert rows["run:run-cloud-activity"]["runner"]["shell"] == "codex"
    assert rows["run:run-cloud-activity"]["phase"] == "coding"
    assert rows["run:run-cloud-activity"]["branch"] == "brr/activity"
    assert rows["schedule:daily-sweep"]["kind"] == "scheduled"
    assert rows[f"respawn:{respawn.stem}"]["defer_until"].startswith("2999-01-01T01:00:00")


def test_loop_publishes_plans_snapshot(tmp_path, monkeypatch):
    """CS5/CS7 files in the account dominion mirror to the hosted CPS view."""
    from brr import account
    from brnrd.models import Account as AccountModel, Repo as RepoModel

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, _ = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    daemon_headers = {"Authorization": f"Bearer {token}"}
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    # _connected_account_id reads the same cloud.json _save_state just wrote,
    # so this resolves the same "connected" account home the daemon uses.
    repo_root = brr_dir.parent
    ctx = account.resolve_context(repo_root, create=True)
    label = account.repo_label(repo_root)
    plan_path = account.active_plan_path(ctx, label)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("ship the CPS view", encoding="utf-8")
    cross_path = account.cross_repo_plans_path(ctx) / "active.md"
    cross_path.parent.mkdir(parents=True, exist_ok=True)
    cross_path.write_text("coordinate release", encoding="utf-8")
    ledger_path = account.decisions_ledger_path(ctx)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("adopted the ToS posture", encoding="utf-8")

    cloud._loop_once(brr_dir, inbox_dir, responses_dir)

    with client.app.state.SessionLocal() as db:
        repo_row = db.get(RepoModel, pid)
        assert repo_row.plan_md == "ship the CPS view"
        account_row = db.get(AccountModel, repo_row.account_id)
        assert account_row.cross_repo_plan_md == "coordinate release"
        assert account_row.decision_ledger_md == "adopted the ToS posture"


def test_loop_publishes_quota_snapshot(tmp_path, monkeypatch):
    """#237: real per-shell quota windows replace the dashboard's UNKNOWN card."""
    import json as json_mod

    from brnrd.models import Daemon as DaemonModel

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, _ = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    daemon_headers = {"Authorization": f"Bearer {token}"}
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    # Claude's usage scrape only ever caches into a *run's* outbox dir —
    # exercise the "find the freshest one" path the same real collector uses.
    run_outbox = brr_dir / "outbox" / "evt-quota-run"
    run_outbox.mkdir(parents=True)
    (run_outbox / ".claude-usage-levels.json").write_text(
        json_mod.dumps(
            {
                "quota": {"buckets": {"session": {"remaining_percentage": 61.0}, "week": {"remaining_percentage": 48.0}}},
                "session_reset": "resets 9:00PM",
                "week_reset": "resets Jul 10",
                "session_resets_at": 1783360000.0,
                "week_resets_at": 1783900000.0,
            }
        ),
        encoding="utf-8",
    )
    # Codex has no on-disk fixture here; stub its live rollout read instead.
    monkeypatch.setattr(
        cloud.codex_status,
        "load_levels",
        lambda *a, **k: {
            "quota": {
                "primary_remaining_percent": 82.0,
                "secondary_remaining_percent": 70.0,
                "primary_resets_at": 1783350000.0,
                "secondary_resets_at": 1783890000.0,
            }
        },
    )

    cloud._loop_once(brr_dir, inbox_dir, responses_dir)

    with client.app.state.SessionLocal() as db:
        daemon = db.query(DaemonModel).filter(DaemonModel.repo_id == pid).one()
        assert daemon.quota_updated_at is not None
        shells = {row["shell"]: row for row in json_mod.loads(daemon.quota_json)}
    assert shells["claude"]["windows"][0]["percent"] == 61.0
    assert shells["claude"]["windows"][1]["percent"] == 48.0
    assert shells["codex"]["windows"][0]["percent"] == 82.0
    assert shells["codex"]["windows"][1]["percent"] == 70.0
    # Machine-parseable reset instants (2026-07-06) — the window-track
    # visual's time-remaining axis needs an epoch, not just display text.
    assert shells["claude"]["windows"][0]["resets_at"] == 1783360000.0
    assert shells["claude"]["windows"][1]["resets_at"] == 1783900000.0
    assert shells["codex"]["windows"][0]["resets_at"] == 1783350000.0
    assert shells["codex"]["windows"][1]["resets_at"] == 1783890000.0


def test_loop_publishes_live_runs_snapshot(tmp_path, monkeypatch):
    """#258: the local presence registry mirrors into the account-scoped
    live/coexisting-runs view, the same publish shape as quota (#237)."""
    import json as json_mod

    from brnrd.models import Daemon as DaemonModel

    from brr import presence

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, _ = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    daemon_headers = {"Authorization": f"Bearer {token}"}
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    presence.register(
        brr_dir, kind="daemon", stream="telegram:155783668:",
        label="Add live run labels", run_id="run-live-test",
        repo_label="Gurio/brr", pid=os.getpid(),
    )

    cloud._loop_once(brr_dir, inbox_dir, responses_dir)

    with client.app.state.SessionLocal() as db:
        daemon = db.query(DaemonModel).filter(DaemonModel.repo_id == pid).one()
        assert daemon.live_runs_updated_at is not None
        runs = json_mod.loads(daemon.live_runs_json)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-live-test"
    assert runs[0]["label"] == "Add live run labels"
    assert runs[0]["repo_label"] == "Gurio/brr"
    assert runs[0]["kind"] == "daemon"


def test_loop_publishes_pr_review_queue_snapshot(tmp_path, monkeypatch):
    """#259: open PRs from `gh pr list` mirror into the account-scoped review
    queue, the same publish shape as quota/live-runs."""
    import json as json_mod
    import subprocess

    from brnrd.models import Daemon as DaemonModel

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, _ = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    daemon_headers = {"Authorization": f"Bearer {token}"}
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))
    monkeypatch.setattr(cloud, "_pr_review_repo_labels", lambda _brr_dir: ["Gurio/demo"])

    def fake_run(cmd, **kwargs):
        assert cmd == [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,url,createdAt,isDraft,author,headRefName",
            "--repo",
            "Gurio/demo",
        ]
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json_mod.dumps(
                [
                    {
                        "number": 259,
                        "title": "Dashboard: PR-review queue",
                        "url": "https://github.com/Gurio/demo/pull/259",
                        "createdAt": "2026-07-07T09:00:00Z",
                        "isDraft": False,
                        "author": {"login": "gurio"},
                        "headRefName": "brr/pr-review-queue",
                    }
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(cloud.subprocess, "run", fake_run)

    cloud._loop_once(brr_dir, inbox_dir, responses_dir)

    with client.app.state.SessionLocal() as db:
        daemon = db.query(DaemonModel).filter(DaemonModel.repo_id == pid).one()
        assert daemon.pr_review_queue_updated_at is not None
        prs = json_mod.loads(daemon.pr_review_queue_json)
    assert prs == [
        {
            "number": 259,
            "title": "Dashboard: PR-review queue",
            "url": "https://github.com/Gurio/demo/pull/259",
            "repo_label": "Gurio/demo",
            "created_at": "2026-07-07T09:00:00Z",
            "draft": False,
            "author": "gurio",
        }
    ]


def test_drain_preserves_github_origin_metadata(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, _ = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    client.post(
        "/v1/_dev/enqueue",
        json={
            "repo_id": pid,
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
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 0},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    client.post(
        "/v1/_dev/enqueue",
        json={
            "repo_id": pid,
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
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 99},
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
        {"brnrd_url": "http://brnrd", "token": "tok", "repo_id": "p", "since": 0},
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
            "repo_id": "proj_x",
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
