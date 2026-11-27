"""The operators' page-view read-out: listed logins read it, everyone else gets a 404."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from brnrd.app import create_app
from brnrd.config import Settings
from brnrd.oauth import GitHubIdentity
from brnrd.page_views import PageViewCount
from brnrd.routers.accounts import account_for_github_identity, issue_session_token

URL = "/v1/admin/page-views"


def _client(tmp_path, logins: tuple[str, ...]) -> TestClient:
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/a.db", admin_github_logins=logins))
    today = dt.datetime.now(dt.timezone.utc).date()
    with app.state.SessionLocal() as db:
        for day, path, host, ua, n in [
            (today, "/", "t.co", "chrome", 5),
            (today, "/", "t.co", "safari", 2),
            (today, "/", "t.co", "bot", 9),
            (today, "/", "", "firefox", 1),
            (today, "/pricing", "t.co", "chrome", 3),
            (today - dt.timedelta(days=1), "/", "news.ycombinator.com", "chrome", 4),
            (today - dt.timedelta(days=30), "/", "old.example", "chrome", 7),  # day 31: outside
        ]:
            db.add(PageViewCount(day=day, path=path, referer_host=host, ua_family=ua, status=200, views=n))
        db.commit()
    return TestClient(app)


def _sign_in(client: TestClient, login: str, github_id: str = "1") -> None:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(db, GitHubIdentity(github_id=github_id, login=login, email=None))
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)


def test_a_listed_login_reads_the_last_30_days(tmp_path):
    client = _client(tmp_path, ("gurio", "someone"))
    _sign_in(client, "Gurio")  # the list is lowercased; GitHub logins are not
    r = client.get(URL)
    assert r.status_code == 200, r.text
    body = r.json()
    today = dt.datetime.now(dt.timezone.utc).date()
    assert body["until"] == today.isoformat()
    assert body["since"] == (today - dt.timedelta(days=29)).isoformat()
    rows = [(x["day"], x["path"], x["referer_host"], x["views"], x["bot_views"]) for x in body["rows"]]
    t, y = today.isoformat(), (today - dt.timedelta(days=1)).isoformat()
    assert rows == [
        (t, "/", "t.co", 7, 9),
        (t, "/", "", 1, 0),
        (t, "/pricing", "t.co", 3, 0),
        (y, "/", "news.ycombinator.com", 4, 0),
    ]


def test_a_second_listed_login_reads_too(tmp_path):
    client = _client(tmp_path, ("gurio", "someone"))
    _sign_in(client, "someone", github_id="2")
    assert client.get(URL).status_code == 200


def test_an_unlisted_login_gets_404(tmp_path):
    client = _client(tmp_path, ("gurio",))
    _sign_in(client, "stranger", github_id="3")
    r = client.get(URL)
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_anonymous_gets_404(tmp_path):
    client = _client(tmp_path, ("gurio",))
    r = client.get(URL)
    assert r.status_code == 404
    assert r.json() == client.get("/v1/admin/no-such-thing").json()


def test_an_empty_list_means_nobody(tmp_path):
    client = _client(tmp_path, ())
    _sign_in(client, "Gurio")
    assert client.get(URL).status_code == 404


def test_the_env_list_is_read_comma_separated_and_lowercased(monkeypatch):
    from brnrd import config

    monkeypatch.setenv("BRNRD_ADMIN_GITHUB_LOGINS", " Gurio, SomeOne ,,")
    assert config._env_csv_lower("BRNRD_ADMIN_GITHUB_LOGINS") == ("gurio", "someone")
    monkeypatch.delenv("BRNRD_ADMIN_GITHUB_LOGINS")
    assert config._env_csv_lower("BRNRD_ADMIN_GITHUB_LOGINS") == ()
    assert Settings.__dataclass_fields__["admin_github_logins"].default == ()


def test_metrics_uses_operator_gate_and_renders_fourteen_utc_days(tmp_path):
    client = _client(tmp_path, ("gurio",))
    assert client.get("/metrics").status_code == 404
    assert client.get("/v1/admin/metrics").status_code == 404
    _sign_in(client, "stranger", github_id="3")
    assert client.get("/metrics").status_code == 404
    assert client.get("/v1/admin/metrics").status_code == 404

    _sign_in(client, "Gurio")
    today = dt.datetime.now(dt.timezone.utc).date()
    oldest = today - dt.timedelta(days=13)
    with client.app.state.SessionLocal() as db:
        db.add(PageViewCount(day=oldest, path="/learn/oldest", referer_host="", ua_family="chrome", status=200, views=1))
        db.add(PageViewCount(day=oldest - dt.timedelta(days=1), path="/learn/outside", referer_host="", ua_family="chrome", status=200, views=1))
        db.commit()
    for ua in ("Chrome/120", "Twitterbot/1.0"):
        client.get(
            "/hi", headers={"referer": "https://t.co/private", "user-agent": ua},
            follow_redirects=False,
        )

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    # 14 days and 8 ISO weeks.
    assert response.text.count("<th scope='row'>") == 22
    year, week, _ = today.isocalendar()
    assert f"{year}-W{week:02d}" in response.text
    # Landing 8 (bots left out), /hi 1, signups 1 (the operator's login is left out).
    assert f"<th scope='row'>{today}</th><td>8</td><td>1</td><td>1</td><td>0</td><td>0</td><td>0</td><td>0</td>" in response.text
    assert f"<th scope='row'>{oldest}</th><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td>" in response.text
    assert "<td>/hi</td><td>t.co</td><td>1</td><td>1</td>" in response.text
    body = client.get("/v1/admin/metrics")
    assert body.status_code == 200
    assert body.headers["cache-control"] == "private, no-store"
    payload = body.json()
    assert payload["tag_on_account"] is False
    assert payload["campaigns"] == ["/hi"]
    assert len(payload["days"]) == 14 and len(payload["weeks"]) == 8
    today_row = next(row for row in payload["days"] if row["period"] == today.isoformat())
    assert today_row["landing"] == 8 and today_row["campaigns"]["/hi"] == 1 and today_row["signups"] == 1
    assert "<td>/learn/oldest</td><td>(direct)</td>" in response.text
    assert "/learn/outside" not in response.text


def test_metrics_counts_script_downloads_and_first_connected_computers(tmp_path):
    from _helpers import brnrd_repo_and_daemon

    script = tmp_path / "install.sh"
    script.write_text("#!/bin/sh\n")
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path}/a.db", admin_github_logins=("gurio",), installer_path=str(script),
    ))
    client = TestClient(app, base_url="https://testserver")
    today = dt.datetime.now(dt.timezone.utc).date()
    yesterday = today - dt.timedelta(days=1)

    for path, ua in [("/install", "curl/8.4.0"), ("/install.sh", "Wget/1.21"), ("/install", "Googlebot/2.1")]:
        assert client.get(path, headers={"user-agent": ua}).status_code == 200
    assert client.head("/install", headers={"user-agent": "curl/8.4.0"}).status_code == 200

    # A real device-flow pairing connects octocat's first computer today;
    # a second computer and a reconnect on the same account add nothing.
    _, daemon, _ = brnrd_repo_and_daemon(client)
    for name in ("computer-one", "computer-two", "computer-one"):
        assert client.post("/v1/daemons/register", headers=daemon, json={"daemon_name": name}).status_code == 200
    # An account that connected yesterday, and one that never connected.
    with app.state.SessionLocal() as db:
        early = account_for_github_identity(db, GitHubIdentity(github_id="7", login="early", email=None))
        early.home_trial_started_at = dt.datetime.combine(yesterday, dt.time(23, 59))
        account_for_github_identity(db, GitHubIdentity(github_id="8", login="idle", email=None))
        db.commit()

    _sign_in(client, "Gurio", github_id="9")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "<th scope=\"col\">Script downloads</th><th scope=\"col\">Computers connected</th>" in response.text
    # Landing | /hi | signups (octocat, early, idle; gurio is the operator and is left out) | downloads | connected | day 3 | paid
    assert f"<th scope='row'>{today}</th><td>0</td><td>0</td><td>3</td><td>2</td><td>1</td><td>0</td><td>0</td>" in response.text
    assert f"<th scope='row'>{yesterday}</th><td>0</td><td>0</td><td>0</td><td>0</td><td>1</td><td>0</td><td>0</td>" in response.text


def test_day3_counts_a_later_sighting_or_letter_and_leaves_out_marked_logins(tmp_path):
    from brnrd.funnel import week_label
    from brnrd.models import BillingLedgerEntry, Daemon, Event, Repo, Token

    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path}/a.db",
        admin_github_logins=("gurio",),
        home_gate_member_github_logins=("quiet",),
        first_subscribers_exclude=("lettered",),
        funnel_exclude_github_logins=("team",),
    ))
    client = TestClient(app)
    today = dt.datetime.now(dt.timezone.utc).date()
    connected = dt.datetime.combine(today - dt.timedelta(days=4), dt.time(12, 0))
    with app.state.SessionLocal() as db:
        def person(github_id: str, login: str):
            return account_for_github_identity(db, GitHubIdentity(github_id=github_id, login=login, email=None))

        returned = person("1", "returned")
        noted = person("2", "noted")
        quiet = person("3", "quiet")
        team = person("4", "Team")
        lettered = person("5", "lettered")
        for account in (returned, noted, quiet, team, lettered):
            account.home_trial_started_at = connected
        db.add(Repo(id="repo-noted", account_id=noted.id, repo_full_name="n/n", repo_owner="n", repo_name="n"))
        db.add(Repo(id="repo-lettered", account_id=lettered.id, repo_full_name="l/l", repo_owner="l", repo_name="l"))
        db.flush()
        # Exactly 72h later counts. An hour later does not. Marked logins would pass and must not.
        db.add(Token(id="tok-r", account_id=returned.id, kind=Token.KIND_DAEMON, token_hash="hr"))
        db.add(Token(id="tok-n", account_id=noted.id, kind=Token.KIND_DAEMON, token_hash="hn"))
        db.add(Token(id="tok-q", account_id=quiet.id, kind=Token.KIND_DAEMON, token_hash="hq"))
        db.add(Token(id="tok-t", account_id=team.id, kind=Token.KIND_DAEMON, token_hash="ht"))
        db.add(Daemon(id="d-r", account_id=returned.id, token_id="tok-r", daemon_name="r",
                      last_seen_at=connected + dt.timedelta(days=3)))
        db.add(Daemon(id="d-n", account_id=noted.id, token_id="tok-n", daemon_name="n",
                      last_seen_at=connected + dt.timedelta(hours=1)))
        db.add(Daemon(id="d-q", account_id=quiet.id, token_id="tok-q", daemon_name="q",
                      last_seen_at=connected + dt.timedelta(days=5)))
        db.add(Daemon(id="d-t", account_id=team.id, token_id="tok-t", daemon_name="t",
                      last_seen_at=connected + dt.timedelta(days=5)))
        db.add(Event(event_id="e-noted", repo_id="repo-noted", created_at=connected + dt.timedelta(days=3)))
        db.add(Event(event_id="e-lettered", repo_id="repo-lettered", created_at=connected + dt.timedelta(days=4)))
        db.add(BillingLedgerEntry(id="led-r", account_id=returned.id, op="grant_subscriber_monthly", created_at=connected))
        db.add(BillingLedgerEntry(id="led-t", account_id=team.id, op="grant_subscriber_monthly", created_at=connected))
        db.commit()

    assert client.get("/v1/admin/metrics").status_code == 404
    _sign_in(client, "Gurio", github_id="9")
    payload = client.get("/v1/admin/metrics").json()
    assert payload["excluded_logins"] == 4
    day = (today - dt.timedelta(days=4)).isoformat()
    row = next(item for item in payload["days"] if item["period"] == day)
    # returned (sighting) and noted (letter). quiet, Team and lettered are marked.
    assert row == {
        "period": day, "landing": 0, "campaigns": {"/hi": 0}, "signups": 0,
        "installs_started": 0, "installs_finished": 2, "active_day3": 2, "paid": 1,
    }
    today_row = next(item for item in payload["days"] if item["period"] == today.isoformat())
    assert today_row["signups"] == 2
    week = next(item for item in payload["weeks"] if item["period"] == week_label(today - dt.timedelta(days=4)))
    assert week["installs_finished"] >= 2 and week["active_day3"] >= 2 and week["paid"] >= 1


def test_week_window_is_eight_mondays_ending_this_week():
    from brnrd.funnel import week_mondays

    today = dt.date(2026, 11, 21)
    mondays = week_mondays(today)
    assert len(mondays) == 8
    assert all(day.weekday() == 0 for day in mondays)
    assert mondays[0] == today - dt.timedelta(days=today.weekday())
    assert mondays[0] - mondays[-1] == dt.timedelta(weeks=7)


def test_metrics_is_closed_when_operator_list_is_empty(tmp_path):
    client = _client(tmp_path, ())
    _sign_in(client, "Gurio")
    response = client.get("/metrics")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "private, no-store"
