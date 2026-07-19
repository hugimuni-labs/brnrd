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
from brr import usage_samples  # noqa: E402
from brr.gates import cloud  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402


class _StopLoop(BaseException):
    """Sentinel to break `run_loop`'s `while True`.

    Deliberately a BaseException: the loop catches `Exception` broadly (that is
    the point of it), so a RuntimeError sentinel would be swallowed and retried
    forever — a hang, not a test.
    """


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


def test_managed_publishing_credential_stays_in_memory(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("BRNRD_MANAGED_GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(cloud, "_publishing_token_expires_at", 0.0)
    calls = []

    def fake_request(base_url, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "token": "ghs_app",
            "expires_at": "2099-01-01T00:00:00Z",
            "login": "brnrd-dev[bot]",
        }

    monkeypatch.setattr(cloud, "_request", fake_request)
    state = {"brnrd_url": "https://brnrd.dev", "token": "bd_daemon"}

    cloud._refresh_publishing_credential(state, force=True)

    assert os.environ["BRNRD_MANAGED_GITHUB_TOKEN"] == "ghs_app"
    assert calls == [
        (
            "POST",
            "/v1/daemons/publishing-credential",
            {"token": "bd_daemon", "timeout": 20},
        )
    ]
    assert "ghs_app" not in state.values()


def test_explicit_gh_token_skips_managed_credential(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "operator-token")
    monkeypatch.setattr(
        cloud,
        "_request",
        lambda *args, **kwargs: pytest.fail("managed credential should not be requested"),
    )

    cloud._refresh_publishing_credential(
        {"brnrd_url": "https://brnrd.dev", "token": "bd_daemon"},
        force=True,
    )


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
            {},  # /v1/daemons/register
        ]
    )
    seen = []

    def fake_request(base_url, method, path, **kwargs):
        seen.append((method, path, kwargs))
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
    assert ("POST", "/v1/accounts/pair") in [call[:2] for call in seen]
    register = seen[-1]
    assert register[:2] == ("POST", "/v1/daemons/register")
    assert register[2]["token"] == "bd_tok"
    assert register[2]["json"]["daemon_name"] == "laptop"
    assert output == [
        "[brnrd] Approve this daemon at: u",
        "[brnrd] Connected to brnrd repo proj_x.",
        "[brnrd] Pair Telegram chat: https://t.me/brnrd_bot?start=TG-TEST",
        "[brnrd] If Telegram only opens the chat, send: /start TG-TEST",
    ]


def test_connect_registers_token_for_dashboard_publishes(tmp_path, monkeypatch):
    """The completed pairing handshake must create the Daemon row too.

    Pairing and Telegram only need the minted token, so they can look healthy
    while every dashboard mirror returns "no daemon registered for this
    token".  Drive the real API boundary and pin the first quota publish that
    exposed that split in production.
    """
    brr_dir = tmp_path / ".brr"
    client, _ = _make_brnrd()
    account_headers, repo_id = _account_and_project(client)
    routed = _route_to(client)
    pair: dict[str, str] = {}
    approved = False

    def approve_then_route(base_url, method, path, **kwargs):
        nonlocal approved
        if method == "GET" and path.startswith("/v1/accounts/pair/") and not approved:
            approved = True
            response = client.post(
                f"/v1/accounts/pair/{pair['pair_code']}/approve",
                json={"repo_id": repo_id},
                headers=account_headers,
            )
            assert response.status_code == 200
        result = routed(base_url, method, path, **kwargs)
        if method == "POST" and path == "/v1/accounts/pair":
            pair.update(result)
        return result

    monkeypatch.setattr(cloud, "_request", approve_then_route)
    state = cloud.connect(
        brr_dir,
        brnrd_url="http://brnrd",
        daemon_name="already-running",
        poll_interval_s=0,
        timeout_s=5,
        out=lambda _message: None,
    )

    publish = client.put(
        "/v1/daemons/quota",
        json={"shells": [], "gates": []},
        headers={"Authorization": f"Bearer {state['token']}"},
    )
    assert publish.status_code == 200
    assert publish.json()["shells"] == []


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
    # The pagination cursor advanced and persisted.
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


def test_streaming_interims_and_terminal_all_reach_the_platform(tmp_path, monkeypatch):
    """Regression (2026-07-18): the gate posted every delivery as ``done``,
    so the server closed the event on the first interim and silently skipped
    every later forward — the run's final reply vanished while the daemon
    cleaned it up as delivered. Interims post ``processing`` now; the close
    belongs to the terminal alone."""
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

    client.post(
        "/v1/_dev/enqueue",
        json={"repo_id": pid, "body": "task", "reply_to": {"chat": 1}},
        headers=acc,
    )
    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    (event,) = protocol.list_pending(inbox_dir)

    # Mid-run: an interim lands while the event is still processing.
    protocol.set_status(event, "processing")
    protocol.write_partial(responses_dir, event["id"], "interim: found something")
    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    assert [item.body for item in forwarder.items] == ["interim: found something"]

    # Closeout: the terminal reply must still reach the platform.
    protocol.set_status(event, "done")
    protocol.write_response(responses_dir, event["id"], "final: the whole story")
    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    assert [item.body for item in forwarder.items] == [
        "interim: found something",
        "final: the whole story",
    ]
    assert protocol.list_done(inbox_dir, "cloud") == []


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
            "has_new_commit": True,
            "pr_number": 205,
        },
    ).save(brr_dir / "runs")
    Run(
        id="run-cloud-audit",
        event_id="evt-audit",
        body="read-only audit",
        status="running",
        source="telegram",
        conversation_key="telegram:42:",
        meta={
            "publish_status": "nothing",
            "branch_name": "brr/run-cloud-audit",
            "has_new_commit": False,
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

    cloud._dashboard_publish_tick(brr_dir, inbox_dir)

    listing = client.get("/v1/accounts/activity", headers=acc)
    assert listing.status_code == 200
    rows = {row["id"]: row for row in listing.json()["activity"]}
    assert rows["run:run-cloud-activity"]["runner"]["shell"] == "codex"
    assert rows["run:run-cloud-activity"]["phase"] == "coding"
    assert rows["run:run-cloud-activity"]["branch"] == "brr/activity"
    assert rows["run:run-cloud-audit"]["branch"] == ""
    assert rows["schedule:daily-sweep"]["kind"] == "scheduled"
    assert rows[f"respawn:{respawn.stem}"]["defer_until"].startswith("2999-01-01T01:00:00")


def test_loop_publishes_discovered_surface_snapshot(tmp_path, monkeypatch):
    """Every authored surface page mirrors without a code-level mount."""
    import json

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
    page = account.work_surface_path(ctx) / "something-new.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    (page.parent / "index.md").write_text("# Work surface", encoding="utf-8")
    page.write_text("discovered without a new mount", encoding="utf-8")

    cloud._dashboard_publish_tick(brr_dir, inbox_dir)

    with client.app.state.SessionLocal() as db:
        repo_row = db.get(RepoModel, pid)
        account_row = db.get(AccountModel, repo_row.account_id)
        files = json.loads(account_row.surface_json)
        # Corpus join: paths are now home-relative and carry their layer.
        assert {item["path"] for item in files} == {"surface/index.md", "surface/something-new.md"}
        page_row = next(item for item in files if item["path"] == "surface/something-new.md")
        assert page_row["markdown"] == "discovered without a new mount"
        assert page_row["layer"] == "authored"


def test_corpus_publish_is_change_gated(tmp_path, monkeypatch):
    """The corpus re-PUTs only when it changes — the big knowledge layer is not
    resent every tick."""
    import json

    from brr import account
    from brnrd.models import Account as AccountModel, Repo as RepoModel

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    client, _ = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    daemon_headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers).status_code == 200
    cloud._save_state(brr_dir, {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 0})
    cloud._corpus_publish_hash.pop(str(brr_dir), None)

    puts: list[str] = []
    routed = _route_to(client)

    def _counting(url, method, path, **kw):
        if path == "/v1/daemons/surface":
            puts.append(path)
        return routed(url, method, path, **kw)

    monkeypatch.setattr(cloud, "_request", _counting)

    ctx = account.resolve_context(brr_dir.parent, create=True)
    index = account.work_surface_path(ctx) / "index.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("# Work surface", encoding="utf-8")

    cloud._dashboard_publish_tick(brr_dir, inbox_dir)
    cloud._dashboard_publish_tick(brr_dir, inbox_dir)  # unchanged → no second PUT
    assert puts.count("/v1/daemons/surface") == 1

    index.write_text("# Work surface — edited", encoding="utf-8")
    cloud._dashboard_publish_tick(brr_dir, inbox_dir)  # changed → PUT again
    assert puts.count("/v1/daemons/surface") == 2


def test_corpus_fingerprint_tolerates_missing_knowledge_root(tmp_path):
    """Best-effort posture: a home with no linked knowledge repo still hashes."""
    digest = cloud._corpus_fingerprint([], tmp_path / "missing-knowledge")
    assert digest


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
    # And pin the app-server probe to "unavailable" (#315) — a unit test must
    # never spawn `codex app-server` or depend on a logged-in Codex, and this
    # also exercises the degraded path: probe down ⇒ the passive rollout read
    # still publishes, rather than the merge blanking the row.
    monkeypatch.setattr(cloud.codex_usage, "probe_rate_limits", lambda **kw: None)
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

    cloud._dashboard_publish_tick(brr_dir, inbox_dir)

    with client.app.state.SessionLocal() as db:
        daemon = db.query(DaemonModel).filter(DaemonModel.repo_id == pid).one()
        assert daemon.quota_updated_at is not None
        shells = {row["shell"]: row for row in json_mod.loads(daemon.quota_json)}
        gates = json_mod.loads(daemon.gate_health_json)
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
    assert gates == [
        {
            "gate": "cloud",
            "last_poll_ok": None,
            "age_seconds": None,
            "last_error": None,
            "status": "never",
        }
    ]


def test_claude_quota_shell_carries_scrape_updated_at_and_credits(tmp_path):
    """2026-07-07 fix ('the lying Claude usage panel'): the published shell
    payload must forward the underlying scrape's own timestamp (so the
    dashboard can flag staleness against real data age, not the daemon's
    always-fresh publish cadence) and a real per-run USD figure when Claude's
    result JSON proved one — the credits/metered-overage exposure the
    maintainer asked for after confirming live that a run keeps working (and
    billing) straight through an exhausted 5h window."""
    import json as json_mod

    brr_dir = tmp_path / ".brr"
    run_outbox = brr_dir / "outbox" / "evt-credits-run"
    run_outbox.mkdir(parents=True)
    (run_outbox / ".claude-usage-levels.json").write_text(
        json_mod.dumps(
            {
                "quota": {"buckets": {"session": {"remaining_percentage": 1.0}, "week": {"remaining_percentage": 9.0}}},
                "session_reset": "resets 12:20am (Europe/Berlin)",
                "week_reset": "resets Jul 10, 12am (Europe/Berlin)",
                "updated_at": "2026-07-07T20:17:03Z",
            }
        ),
        encoding="utf-8",
    )
    (run_outbox / ".claude-result-levels.json").write_text(
        json_mod.dumps(
            {
                "spend": {"summary": "$1.15 this session (estimated)", "total_cost_usd": 1.15},
                "updated_at": "2026-07-07T20:20:00Z",
            }
        ),
        encoding="utf-8",
    )

    shell = cloud._claude_quota_shell(brr_dir)
    assert shell is not None
    assert shell["updated_at"] == "2026-07-07T20:17:03Z"
    assert shell["credits"] == {
        "total_cost_usd": 1.15,
        "summary": "$1.15 this session (estimated)",
        "updated_at": "2026-07-07T20:20:00Z",
    }


def test_claude_quota_shell_publishes_burn_and_samples_the_reading(tmp_path):
    """Burn was Codex-only because only Codex left a timestamped series on disk.
    Claude — the Shell doing most of the spending — had no instrument at all.
    The published row now carries the same reading, and the publish path itself
    feeds the store it is measured from."""
    import json as json_mod

    brr_dir = tmp_path / ".brr"
    run_outbox = brr_dir / "outbox" / "evt-burn-run"
    run_outbox.mkdir(parents=True)
    (run_outbox / ".claude-usage-levels.json").write_text(
        json_mod.dumps(
            {
                "quota": {
                    "buckets": {
                        "session": {"remaining_percentage": 40.0},
                        "week": {"remaining_percentage": 60.0},
                    }
                },
                "session_used_percentage": 60.0,
                "session_resets_at": 1784300000.0,
                "week_used_percentage": 40.0,
                "week_resets_at": 1784490642.0,
                "updated_at": "2026-07-13T18:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    shell = cloud._claude_quota_shell(brr_dir)
    assert shell is not None
    # Thin evidence on a cold store: absent, not invented.
    assert "burn" in shell
    assert shell["burn"] is None
    # …but the read that produced the row was itself sampled, which is the only
    # way the series ever starts.
    rows = [
        json_mod.loads(line)
        for line in usage_samples.log_path(brr_dir).read_text().splitlines()
    ]
    assert {r["window_minutes"] for r in rows} == {300.0, 10080.0}
    assert all(r["shell"] == "claude" for r in rows)


def test_claude_quota_shell_refreshes_stale_idle_cache(tmp_path, monkeypatch):
    """The dashboard publisher must not keep scavenging a stale run cache
    forever once no Claude run is actively heartbeating."""
    import json as json_mod
    import os
    import time

    brr_dir = tmp_path / ".brr"
    run_outbox = brr_dir / "outbox" / "evt-stale-run"
    run_outbox.mkdir(parents=True)
    usage_path = run_outbox / ".claude-usage-levels.json"
    usage_path.write_text(
        json_mod.dumps(
            {
                "quota": {"buckets": {"session": {"remaining_percentage": 100.0}}},
                "session_reset": "old",
                "updated_at": "2026-07-07T06:54:17Z",
            }
        ),
        encoding="utf-8",
    )
    old = time.time() - cloud._CLAUDE_QUOTA_PUBLISH_MAX_AGE_SECONDS - 30
    os.utime(usage_path, (old, old))

    monkeypatch.setattr(
        cloud.claude_usage,
        "capture_levels",
        lambda *a, **k: {
            "quota": {"buckets": {"session": {"remaining_percentage": 0.0}, "week": {"remaining_percentage": 9.0}}},
            "session_reset": "12:20am (Europe/Berlin)",
            "week_reset": "Jul 10, 12am (Europe/Berlin)",
            "updated_at": "2026-07-07T20:58:59Z",
        },
    )

    shell = cloud._claude_quota_shell(brr_dir)

    assert shell is not None
    assert shell["updated_at"] == "2026-07-07T20:58:59Z"
    assert shell["windows"][0]["percent"] == 0.0
    assert shell["windows"][1]["percent"] == 9.0


def test_claude_quota_shell_publishes_usage_credit_balance(tmp_path):
    import json as json_mod

    brr_dir = tmp_path / ".brr"
    run_outbox = brr_dir / "outbox" / "evt-credits-run"
    run_outbox.mkdir(parents=True)
    (run_outbox / ".claude-usage-levels.json").write_text(
        json_mod.dumps(
            {
                "quota": {"buckets": {"session": {"remaining_percentage": 0.0}}},
                "usage_credits": {
                    "enabled": True,
                    "used_percentage": 21.0,
                    "remaining_percentage": 79.0,
                    "spent_amount": 8.69,
                    "limit_amount": 40.0,
                    "currency": "\u20ac",
                    "reset": "Aug 1 (Europe/Berlin)",
                    "summary": "usage credits 79% left; \u20ac8.69 / \u20ac40.00 spent; resets Aug 1 (Europe/Berlin)",
                },
                "updated_at": "2026-07-07T20:58:59Z",
            }
        ),
        encoding="utf-8",
    )

    shell = cloud._claude_quota_shell(brr_dir)

    assert shell is not None
    assert shell["credits"]["summary"].startswith("usage credits 79% left")
    assert shell["credits"]["remaining_percentage"] == 79.0
    assert shell["credits"]["spent_amount"] == 8.69
    assert shell["credits"]["limit_amount"] == 40.0
    assert shell["credits"]["currency"] == "\u20ac"


def test_claude_quota_shell_credits_absent_without_a_spend_snapshot(tmp_path):
    import json as json_mod

    brr_dir = tmp_path / ".brr"
    run_outbox = brr_dir / "outbox" / "evt-no-spend-run"
    run_outbox.mkdir(parents=True)
    (run_outbox / ".claude-usage-levels.json").write_text(
        json_mod.dumps(
            {
                "quota": {"buckets": {"session": {"remaining_percentage": 100.0}}},
                "updated_at": "2026-07-07T20:17:03Z",
            }
        ),
        encoding="utf-8",
    )

    shell = cloud._claude_quota_shell(brr_dir)
    assert shell is not None
    assert shell["credits"] is None


def test_claude_quota_shell_surfaces_per_model_weekly_windows(tmp_path):
    """brnrd.dev live-run dashboard posture (2026-07-13): `claude_usage.py`
    already parses a per-model weekly pool ("Current week (Fable)")
    alongside the primary week bucket, but until now `_claude_quota_shell`
    never read `levels["week_models"]`/`buckets["week_models"]` — that real,
    known number was silently dropped, which reads identically to
    "unknown" from the dashboard side. It must now ride along as its own
    window, not collapse into nothing."""
    import json as json_mod

    brr_dir = tmp_path / ".brr"
    run_outbox = brr_dir / "outbox" / "evt-fable-week"
    run_outbox.mkdir(parents=True)
    (run_outbox / ".claude-usage-levels.json").write_text(
        json_mod.dumps(
            {
                "quota": {
                    "buckets": {
                        "session": {"remaining_percentage": 61.0},
                        "week": {"remaining_percentage": 41.0},
                        "week_models": {"Fable": {"remaining_percentage": 25.0}},
                    }
                },
                "week_models": {
                    "Fable": {
                        "used_percentage": 75.0,
                        "reset": "resets Jul 16 (Europe/Berlin)",
                        "resets_at": 1784390000.0,
                    }
                },
                "updated_at": "2026-07-13T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    shell = cloud._claude_quota_shell(brr_dir)

    assert shell is not None
    labels = {w["label"]: w for w in shell["windows"]}
    assert labels["5h window"]["percent"] == 61.0
    assert labels["weekly"]["percent"] == 41.0
    assert labels["weekly (Fable)"]["percent"] == 25.0
    assert labels["weekly (Fable)"]["reset"] == "resets Jul 16 (Europe/Berlin)"
    assert labels["weekly (Fable)"]["resets_at"] == 1784390000.0


def test_claude_quota_shell_reports_when_only_per_model_week_is_known(tmp_path):
    """A Fable-only capture (no bare "Current week" line ever parsed, no
    session bucket, no credits) used to return `None` entirely — dropping
    real, known data — because the early-exit guard only ever checked the
    primary session/week percentages. It must still publish a shell so the
    dashboard can show what's actually known instead of nothing."""
    import json as json_mod

    brr_dir = tmp_path / ".brr"
    run_outbox = brr_dir / "outbox" / "evt-fable-only"
    run_outbox.mkdir(parents=True)
    (run_outbox / ".claude-usage-levels.json").write_text(
        json_mod.dumps(
            {
                "quota": {
                    "buckets": {"week_models": {"Fable": {"remaining_percentage": 25.0}}}
                },
                "week_models": {"Fable": {"used_percentage": 75.0, "reset": None, "resets_at": None}},
                "updated_at": "2026-07-13T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    shell = cloud._claude_quota_shell(brr_dir)

    assert shell is not None
    labels = {w["label"]: w for w in shell["windows"]}
    assert labels["5h window"]["percent"] is None
    assert labels["weekly"]["percent"] is None
    assert labels["weekly (Fable)"]["percent"] == 25.0


def test_codex_quota_shell_reports_spend_as_unimplemented(tmp_path, monkeypatch):
    """The Codex CLI's result JSON carries no per-run cost figure the way
    Claude's does (`_claude_credits_block`) — this must read as an explicit
    "we don't track this yet, here's why" rather than a field that's just
    absent, which looks identical to "unknown" on the dashboard (brnrd.dev
    live-run dashboard posture, 2026-07-13: "render that as unimplemented
    with its reason")."""
    monkeypatch.setattr(cloud.codex_usage, "probe_rate_limits", lambda **kw: None)
    monkeypatch.setattr(
        cloud.codex_status,
        "load_levels",
        lambda *a, **k: {
            "quota": {
                "primary_remaining_percent": 82.0,
                "secondary_remaining_percent": 70.0,
            }
        },
    )

    shell = cloud._codex_quota_shell(tmp_path / ".brr")

    assert shell is not None
    assert shell["spend"]["status"] == "unimplemented"
    assert shell["spend"]["reason"]


def test_live_runs_snapshot_carries_selected_shell_and_core(tmp_path):
    """brnrd.dev live-run dashboard posture (2026-07-13): the live-runs
    card previously had no way to say which Shell+Core a running thought
    was on — only the closed-run ledger recorded it. `presence.register`
    now carries the same runner_* fields `daemon.py` already persists on
    the run manifest, and the publish snapshot must surface them the same
    shape `_runner_payload` already produces for Activity rows."""
    from brr import presence

    brr_dir = tmp_path / ".brr"
    presence.register(
        brr_dir, kind="daemon", stream="telegram:1:", run_id="run-with-runner",
        repo_label="Gurio/brr", pid=os.getpid(),
        runner_name="claude-sonnet", runner_shell="claude",
        runner_core="claude-sonnet-4-6", runner_class="balanced",
    )
    presence.register(
        brr_dir, kind="session", stream="cursor:1:", run_id="run-without-runner",
        repo_label="Gurio/brr", pid=os.getpid(), entry_id="no-runner-entry",
    )

    rows = {row["run_id"]: row for row in cloud._live_runs_snapshot(brr_dir)}
    assert rows["run-with-runner"]["runner"] == {
        "name": "claude-sonnet", "shell": "claude",
        "core": "claude-sonnet-4-6", "class": "balanced",
    }
    # An entry with no runner selected (or from before this field shipped)
    # gets the empty dict `_runner_payload` already returns for that case,
    # not a missing key or a fabricated default.
    assert rows["run-without-runner"]["runner"] == {}


def test_loop_publishes_live_runs_snapshot(tmp_path, monkeypatch):
    """#258: the local presence registry mirrors into the account-scoped
    live/coexisting-runs view, the same publish shape as quota (#237)."""
    import json as json_mod

    from brnrd.models import Daemon as DaemonModel

    from brr import presence, updates

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
        runner_name="codex-terra", runner_shell="codex",
        runner_core="gpt-5.6-terra", runner_class="balanced",
    )
    # #200's remaining slice: a live run with real conversation records
    # (phase-advancing lifecycle packets + a `.card` note) should fold its
    # current phase and card text into this same publish tick.
    updates.emit(
        brr_dir,
        updates.UpdatePacket(
            type="attempt_started",
            conversation_key="telegram:155783668:",
            payload={"run_id": "run-live-test", "attempt": 1},
        ),
    )
    updates.emit(
        brr_dir,
        updates.UpdatePacket(
            type="card_composed",
            conversation_key="telegram:155783668:",
            payload={"run_id": "run-live-test", "text": "scoping the remaining #200 slice"},
        ),
    )
    # A concurrent `spawn:` child (kb/design-multi-workstream-concurrency.md
    # "Ranked moves" #1: parent_run_id/is_subspawn joined into the live
    # view, not only the closed-run ledger) — a second, distinct pid so it
    # doesn't collide with the resident entry above.
    presence.register(
        brr_dir, kind="daemon", stream="telegram:155783668:",
        label="spawned work", run_id="run-live-spawn",
        repo_label="Gurio/brr", pid=os.getpid(), entry_id="spawn-entry",
        parent_run_id="run-live-test", is_subspawn=True,
    )
    # Loom envelope Phase 1 (kb/design-multi-workstream-concurrency.md
    # §"Loom envelope"): the configured spawn pool width piggybacks on this
    # same publish tick.
    (brr_dir / "config").write_text("spawn.max_concurrent=6\n")

    cloud._dashboard_publish_tick(brr_dir, inbox_dir)

    with client.app.state.SessionLocal() as db:
        daemon = db.query(DaemonModel).filter(DaemonModel.repo_id == pid).one()
        assert daemon.live_runs_updated_at is not None
        assert daemon.spawn_max_concurrent == 6
        runs = json_mod.loads(daemon.live_runs_json)
    assert len(runs) == 2
    by_run_id = {row["run_id"]: row for row in runs}
    resident = by_run_id["run-live-test"]
    assert resident["label"] == "Add live run labels"
    assert resident["repo_label"] == "Gurio/brr"
    assert resident["kind"] == "daemon"
    assert resident["is_subspawn"] is False
    assert resident["parent_run_id"] is None
    assert resident["runner"] == {
        "name": "codex-terra", "shell": "codex",
        "core": "gpt-5.6-terra", "class": "balanced",
    }
    assert resident["phase"] == "running"
    assert resident["card_text"] == "scoping the remaining #200 slice"
    assert resident["card_updated_at"] is not None
    spawn = by_run_id["run-live-spawn"]
    assert spawn["is_subspawn"] is True
    assert spawn["parent_run_id"] == "run-live-test"
    # No packets recorded for this run_id specifically (only the resident's
    # own run_id has records in this shared conversation) — the projection
    # comes back with the view's own "no info yet" default, not the
    # resident's phase/card leaking across run_ids.
    assert spawn["phase"] == "queued"
    assert spawn["card_text"] is None


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

    cloud._dashboard_publish_tick(brr_dir, inbox_dir)

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


def test_run_ledger_snapshot_tails_recent_rows_and_skips_malformed(tmp_path):
    import json as json_mod

    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    ledger = brr_dir / "run-ledger.jsonl"
    rows = [{"run_id": f"run-{i}", "ended_at": "2026-07-07T19:00:00Z"} for i in range(300)]
    ledger.write_text(
        "\n".join(json_mod.dumps(row) for row in rows[:50])
        + "\nnot json\n"
        + "\n".join(json_mod.dumps(row) for row in rows[50:])
        + "\n",
        encoding="utf-8",
    )

    snapshot = cloud._run_ledger_snapshot(brr_dir)

    assert len(snapshot) == 256
    assert snapshot[0]["run_id"] == "run-44"
    assert snapshot[-1]["run_id"] == "run-299"


def test_run_ledger_snapshot_missing_file_is_empty(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()

    assert cloud._run_ledger_snapshot(brr_dir) == []


def test_loop_publishes_run_ledger_snapshot(tmp_path, monkeypatch):
    import json as json_mod

    from brnrd.models import Daemon as DaemonModel

    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
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
    (brr_dir / "run-ledger.jsonl").write_text(
        json_mod.dumps(
            {
                "run_id": "run-ledger-cloud",
                "ended_at": "2026-07-07T19:30:00Z",
                "task_classification": "dashboard-slice",  # retired 2026-07-19; on-disk rows still carry it
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))
    monkeypatch.setattr(cloud, "_pr_review_repo_labels", lambda _brr_dir: [])

    cloud._dashboard_publish_tick(brr_dir, inbox_dir)

    with client.app.state.SessionLocal() as db:
        daemon = db.query(DaemonModel).filter(DaemonModel.repo_id == pid).one()
        assert daemon.run_ledger_updated_at is not None
        rows = json_mod.loads(daemon.run_ledger_json)
    assert rows[0]["run_id"] == "run-ledger-cloud"
    assert rows[0]["ended_at"] == "2026-07-07T19:30:00Z"
    # The retired key is tolerated on read and dropped on publish, never a crash.
    assert "task_classification" not in rows[0]
    assert rows[0]["tokens_input"] is None


def test_dashboard_publish_tick_publishes_all_seven_snapshots(tmp_path, monkeypatch):
    """kb/plan-loom-realtime-build.md slice 0: dashboard snapshots must not
    wait on the inbox long-poll (`_POLL_WAIT_S = 25`) to publish — a single
    ``_dashboard_publish_tick`` call (what the background loop calls every
    ``_DASHBOARD_PUBLISH_INTERVAL_S``) has to move all seven, the same set
    ``_loop_once`` publishes, without needing an inbox event at all."""
    from brnrd.models import Daemon as DaemonModel

    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
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
    monkeypatch.setattr(cloud, "_pr_review_repo_labels", lambda _brr_dir: [])

    cloud._dashboard_publish_tick(brr_dir, inbox_dir)

    listing = client.get("/v1/accounts/activity", headers=acc)
    assert listing.status_code == 200
    with client.app.state.SessionLocal() as db:
        daemon = db.query(DaemonModel).filter(DaemonModel.repo_id == pid).one()
        assert daemon.quota_updated_at is not None
        assert daemon.runners_updated_at is not None
        assert daemon.live_runs_updated_at is not None
        assert daemon.pr_review_queue_updated_at is not None
        assert daemon.run_ledger_updated_at is not None


def test_runners_snapshot_reads_local_catalog(tmp_path, monkeypatch):
    """#328 spool rack: the collector publishes the *discovered* catalog
    (`runner.available_runner_catalog`) plus the resolved default pin, and
    degrades to an empty rack — never a raise — when resolution fails (a
    daemon whose config pins a profile that isn't installed must still
    publish everything else)."""
    from brr import runner as runner_mod

    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    catalog = [
        {"name": "claude-fable", "shell": "claude", "model": "claude-fable-5", "class": "economy", "cost_rank": 15, "selected": True},
        {"name": "codex", "shell": "codex", "class": "balanced", "cost_rank": 25},
    ]
    monkeypatch.setattr(runner_mod, "resolve_runner", lambda root, overrides=None: "claude-fable")
    monkeypatch.setattr(
        runner_mod, "available_runner_catalog", lambda root, selected=None: catalog
    )
    snapshot = cloud._runners_snapshot(brr_dir)
    assert snapshot == {"profiles": catalog, "default": "claude-fable"}

    def _boom(root, overrides=None):
        raise RuntimeError("no profile")

    monkeypatch.setattr(runner_mod, "resolve_runner", _boom)
    snapshot = cloud._runners_snapshot(brr_dir)
    assert snapshot["default"] is None
    assert snapshot["profiles"] == catalog


def test_dashboard_publish_tick_noop_without_configured_state(tmp_path):
    """No token/URL yet (not paired) → skip quietly, don't raise — this runs
    unattended on a background thread with no caller to surface an error to."""
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    cloud._save_state(brr_dir, {})

    cloud._dashboard_publish_tick(brr_dir, inbox_dir)  # must not raise


def test_run_loop_starts_dashboard_publish_thread(tmp_path, monkeypatch):
    """The fast publish loop has to actually be wired into `run_loop`, not
    just exist as a dead function — assert the thread it spawns runs
    `_dashboard_publish_loop` with this run's `brr_dir`/`inbox_dir`."""
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    cloud._save_state(brr_dir, {"brnrd_url": "http://brnrd", "token": "t", "repo_id": 1})

    started: list[tuple] = []

    class _StubThread:
        def __init__(self, *, target, args, daemon, name):
            started.append((target, args, daemon, name))

        def start(self):
            pass

    monkeypatch.setattr(cloud.threading, "Thread", _StubThread)
    monkeypatch.setattr(cloud, "_register", lambda *_a, **_k: None)
    monkeypatch.setattr(cloud, "_try_refresh_publishing_credential", lambda *_a, **_k: None)

    def stop_after_one(*_a, **_k):
        # NB: not a BrnrdAuthError — a 401 is retried now, not fatal.
        raise _StopLoop

    monkeypatch.setattr(cloud, "_loop_once", stop_after_one)

    with pytest.raises(_StopLoop):
        cloud.run_loop(brr_dir, inbox_dir, responses_dir)

    assert len(started) == 1
    target, args, daemon, name = started[0]
    assert target is cloud._dashboard_publish_loop
    assert args == (brr_dir, inbox_dir)
    assert daemon is True
    assert name == "cloud-dashboard-publish"


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


def _auth_error_state(tmp_path):
    brr_dir = tmp_path / ".brr"
    cloud._save_state(
        brr_dir,
        {
            "brnrd_url": "http://brnrd",
            "token": "bd_bad",
            "repo_id": "proj_x",
            "since": 0,
        },
    )
    return brr_dir, brr_dir / "inbox", brr_dir / "responses"


def test_run_loop_survives_auth_error_instead_of_exiting(tmp_path, monkeypatch):
    """A 401 must not end the cloud gate.

    It used to `return`, which killed cloud ingestion for the life of the
    process: chat messages to the cloud bot vanished with no error on any
    surface the user can see, while the daemon went on reporting itself
    healthy. Live failure 2026-07-12 — a restart *during* the outage hit the
    same exit on the register path, so restarting didn't help either.
    """
    brr_dir, inbox_dir, responses_dir = _auth_error_state(tmp_path)
    polls = []

    def fail_request(*_a, **_k):
        raise cloud.BrnrdAuthError("invalid token")

    def loop_once(*_a, **_k):
        polls.append(1)
        if len(polls) >= 3:
            raise _StopLoop  # sentinel: we got this far
        raise cloud.BrnrdAuthError("invalid token")

    monkeypatch.setattr(cloud, "_request", fail_request)  # register 401s too
    monkeypatch.setattr(cloud, "_try_refresh_publishing_credential", lambda *_a, **_k: None)
    monkeypatch.setattr(cloud, "_loop_once", loop_once)
    monkeypatch.setattr(cloud.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cloud, "_dashboard_publish_loop", lambda *_a, **_k: None)

    with pytest.raises(_StopLoop):
        cloud.run_loop(brr_dir, inbox_dir, responses_dir)

    assert len(polls) == 3  # a 401 on register and on poll no longer ends the gate
    health = cloud.runtime.load_health(brr_dir, "cloud")
    assert health["last_poll_ok"] is None
    assert health["last_error"] == "invalid token"


def test_run_loop_retries_auth_then_recovers_and_registers(tmp_path, monkeypatch):
    """The transient case, end to end: register 401s (server mid-deploy), the
    first poll 401s, then the server comes back — the gate keeps draining and
    re-registers its capabilities instead of needing a manual restart."""
    brr_dir, inbox_dir, responses_dir = _auth_error_state(tmp_path)
    registers = []
    polls = []

    def register(*_a, **_k):
        registers.append(1)
        if len(registers) == 1:
            raise cloud.BrnrdAuthError("invalid token")

    def loop_once(*_a, **_k):
        polls.append(1)
        if len(polls) == 1:
            raise cloud.BrnrdAuthError("invalid token")
        if len(polls) > 2:
            raise _StopLoop

    monkeypatch.setattr(cloud, "_register", register)
    monkeypatch.setattr(cloud, "_try_refresh_publishing_credential", lambda *_a, **_k: None)
    monkeypatch.setattr(cloud, "_loop_once", loop_once)
    monkeypatch.setattr(cloud.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cloud, "_dashboard_publish_loop", lambda *_a, **_k: None)

    with pytest.raises(_StopLoop):
        cloud.run_loop(brr_dir, inbox_dir, responses_dir)

    assert len(registers) == 2  # retried after the first successful poll
    assert len(polls) == 3  # 401 → retry → drained
    health = cloud.runtime.load_health(brr_dir, "cloud")
    assert health["last_poll_ok"] is not None
    assert health["last_error"] == "invalid token"


def test_stale_cursor_from_older_db_epoch_heals_end_to_end(tmp_path, monkeypatch):
    """Daemon cursor outlives a brnrd DB reset (since=999 vs fresh table):
    the server detects the epoch break, redelivers the queued backlog, and
    the client accepts the *lower* healed cursor instead of staying stale
    forever. Live failure 2026-07-09."""
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    client, forwarder = _make_brnrd()
    acc, pid = _account_and_project(client)
    token = _handshake(client, acc, pid)
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": token, "repo_id": pid, "since": 999},
    )
    monkeypatch.setattr(cloud, "_request", _route_to(client))

    client.post(
        "/v1/_dev/enqueue", json={"repo_id": pid, "body": "do you hear me?"}, headers=acc
    )
    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    pending = protocol.list_pending(inbox_dir)
    assert [ev["body"] for ev in pending] == ["do you hear me?"]
    # Healed cursor persisted: the next poll resumes from the real seq.
    assert cloud._load_state(brr_dir)["since"] == 1
    cloud._loop_once(brr_dir, inbox_dir, responses_dir)
    assert len(protocol.list_pending(inbox_dir)) == 1


def test_request_retries_gateway_statuses_then_succeeds(monkeypatch):
    """502/503/504 are deploy-window blips from the hosted router (main
    auto-deploys on merge): `_request` rides them out with short paced
    retries instead of tracebacking `brnrd connect` (live failure
    2026-07-09). The router refused the request, so the upstream never saw
    it — retrying non-idempotent methods is safe here."""
    statuses = iter([502, 503, 200])
    calls = []

    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.text = "gateway"
            self.content = b'{"ok": true}'

        def json(self):
            return {"ok": True}

    def fake_request(method, url, **kwargs):
        status = next(statuses)
        calls.append(status)
        return _Resp(status)

    monkeypatch.setattr(cloud._SESSION, "request", fake_request)
    monkeypatch.setattr(cloud.time, "sleep", lambda s: None)
    out = cloud._request("http://brnrd", "POST", "/v1/x", token="t")
    assert out == {"ok": True}
    assert calls == [502, 503, 200]


def test_request_gives_up_after_retry_budget(monkeypatch):
    """A real outage still raises: retries smooth a blip, never mask an
    error — the final 502 surfaces as the RuntimeError it always was."""
    class _Resp:
        status_code = 502
        text = "bad gateway"
        content = b""

    monkeypatch.setattr(cloud._SESSION, "request", lambda *a, **k: _Resp())
    monkeypatch.setattr(cloud.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="502"):
        cloud._request("http://brnrd", "GET", "/v1/x")


def test_propose_config_change_reports_mint_failure(tmp_path, monkeypatch):
    """Connected-but-mint-failed returns {'error': ...} (not None): the
    daemon's user-facing park message must not tell a connected account to
    run `brnrd connect` when the real story is a 422/5xx — the detail is
    the actionable part (observed live 2026-07-11: an out-of-lockstep
    server allowlist read back as "isn't cloud-connected")."""
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    cloud._save_state(brr_dir, {"brnrd_url": "http://brnrd.example", "token": "bd_tok"})

    def fake_request(base_url, method, path, **kwargs):
        raise RuntimeError("HTTP 422: config key not agent-proposable")

    monkeypatch.setattr(cloud, "_request", fake_request)
    result = cloud.propose_config_change(
        brr_dir,
        proposal_id="cfgchg-x",
        config_key="dominion.ledger_inject_budget_bytes",
        current_value=None,
        requested_value=4096,
    )
    assert result == {"error": "HTTP 422: config key not agent-proposable"}

    # Not-connected still reports None — the caller's `brnrd connect` hint
    # stays for the case where it's actually true.
    empty_dir = tmp_path / "empty" / ".brr"
    empty_dir.mkdir(parents=True)
    assert (
        cloud.propose_config_change(
            empty_dir,
            proposal_id="cfgchg-y",
            config_key="spawn.max_concurrent",
            current_value=4,
            requested_value=8,
        )
        is None
    )


def test_codex_quota_publishes_the_weekly_window_when_it_arrives_in_the_primary_slot(
    tmp_path, monkeypatch
):
    """The reported bug (2026-07-13, live Plus account, codex-cli 0.144.1):
    the dashboard showed Codex's weekly quota as unavailable while the number
    was right there. `account/rateLimits/read` returned the **weekly** window
    (`windowDurationMins: 10080`) in the `primary` slot with `secondary: null`,
    and this publish labelled windows *positionally* — so 59%-left-weekly was
    published as "5h window", and "weekly" was published with `percent: None`,
    which the dashboard draws as unknown.

    A window is now named by its own duration, and a slot the account doesn't
    report is omitted rather than published as a null-percent window (absent
    and unknown are different claims, and only one of them is true)."""
    monkeypatch.setattr(cloud.codex_usage, "probe_rate_limits", lambda **kw: None)
    monkeypatch.setattr(
        cloud.codex_status,
        "load_levels",
        lambda *a, **k: {
            "updated_at": "2026-07-13T15:17:15Z",
            "quota": {
                "primary_remaining_percent": 59.0,
                "primary_window_minutes": 10080,
                "primary_resets_at": 1784490643.0,
                "secondary_remaining_percent": None,
                "secondary_window_minutes": None,
                "secondary_resets_at": None,
            },
        },
    )

    shell = cloud._codex_quota_shell(tmp_path / ".brr")

    assert shell is not None
    labels = {w["label"]: w for w in shell["windows"]}
    assert labels["weekly"]["percent"] == 59.0
    assert labels["weekly"]["resets_at"] == 1784490643.0
    assert "5h window" not in labels
    assert [w["label"] for w in shell["windows"]] == ["weekly"]


def test_codex_quota_keeps_labelling_the_classic_two_window_layout(tmp_path, monkeypatch):
    """The historical shape (primary = 5h, secondary = weekly) must still
    render exactly as before — the fix is duration-driven labelling, not a
    special case for one account's layout."""
    monkeypatch.setattr(cloud.codex_usage, "probe_rate_limits", lambda **kw: None)
    monkeypatch.setattr(
        cloud.codex_status,
        "load_levels",
        lambda *a, **k: {
            "quota": {
                "primary_remaining_percent": 67.0,
                "primary_window_minutes": 300,
                "secondary_remaining_percent": 94.0,
                "secondary_window_minutes": 10080,
            }
        },
    )

    shell = cloud._codex_quota_shell(tmp_path / ".brr")

    labels = {w["label"]: w["percent"] for w in shell["windows"]}
    assert labels == {"5h window": 67.0, "weekly": 94.0}


def test_codex_quota_falls_back_to_slot_labels_when_no_duration_is_known(tmp_path, monkeypatch):
    """A snapshot cached by an older brr (or a rollout event that omitted
    `window_minutes`) carries no duration at all. There the slot really is the
    only evidence there is, so the historical positional labels stand — and a
    warm cache from before this change must not blank the panel."""
    monkeypatch.setattr(cloud.codex_usage, "probe_rate_limits", lambda **kw: None)
    monkeypatch.setattr(
        cloud.codex_status,
        "load_levels",
        lambda *a, **k: {
            "quota": {
                "primary_remaining_percent": 82.0,
                "secondary_remaining_percent": 70.0,
            }
        },
    )

    shell = cloud._codex_quota_shell(tmp_path / ".brr")

    labels = {w["label"]: w["percent"] for w in shell["windows"]}
    assert labels == {"5h window": 82.0, "weekly": 70.0}


def test_codex_quota_names_an_unrecognized_window_after_itself(tmp_path, monkeypatch):
    """OpenAI has changed this shape once already. An unknown duration is still
    a real, known number — publish it under a self-describing label rather than
    dropping it or forcing it into one of the two labels we happen to know."""
    monkeypatch.setattr(cloud.codex_usage, "probe_rate_limits", lambda **kw: None)
    monkeypatch.setattr(
        cloud.codex_status,
        "load_levels",
        lambda *a, **k: {
            "quota": {
                "primary_remaining_percent": 50.0,
                "primary_window_minutes": 1440,
                "secondary_remaining_percent": None,
            }
        },
    )

    shell = cloud._codex_quota_shell(tmp_path / ".brr")

    assert [w["label"] for w in shell["windows"]] == ["1d window"]


def test_codex_quota_row_carries_the_trailing_burn(tmp_path, monkeypatch):
    """With the 5h window gone from OpenAI's payload (2026-07-12), the weekly
    percentage is the *only* number left — and a percentage alone can't say
    whether the account is drifting or sprinting. The row carries the derived
    burn rate so the dashboard can answer the question the 5h bar used to."""
    monkeypatch.setattr(cloud.codex_usage, "probe_rate_limits", lambda **kw: None)
    monkeypatch.setattr(
        cloud.codex_status,
        "load_levels",
        lambda *a, **k: {
            "updated_at": "2026-07-13T18:14:40Z",
            "quota": {
                "primary_remaining_percent": 53.0,
                "primary_window_minutes": 10080,
                "primary_resets_at": 1784490643.0,
                "secondary_remaining_percent": None,
            },
        },
    )
    burn = {"window_minutes": 10080.0, "burned_percent": 22.0, "sustainable": False}
    monkeypatch.setattr(cloud.usage_samples, "recent_burn", lambda *a, **k: burn)

    shell = cloud._codex_quota_shell(tmp_path / ".brr")

    assert shell is not None
    assert shell["burn"] == burn


def test_codex_quota_burn_is_absent_when_the_evidence_is_too_thin(tmp_path, monkeypatch):
    """No rollouts, one sample, or a span too short to project from: the row
    ships `burn: None` rather than a rate invented from noise."""
    monkeypatch.setattr(cloud.codex_usage, "probe_rate_limits", lambda **kw: None)
    monkeypatch.setattr(
        cloud.codex_status,
        "load_levels",
        lambda *a, **k: {
            "updated_at": "2026-07-13T18:14:40Z",
            "quota": {"primary_remaining_percent": 53.0, "primary_window_minutes": 10080},
        },
    )
    monkeypatch.setattr(cloud.usage_samples, "recent_burn", lambda *a, **k: None)

    shell = cloud._codex_quota_shell(tmp_path / ".brr")

    assert shell is not None
    assert shell["burn"] is None
