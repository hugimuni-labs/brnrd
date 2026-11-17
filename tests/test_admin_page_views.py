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
