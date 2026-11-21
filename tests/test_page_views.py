"""Server-side funnel counters: what is counted, and what can never be stored."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from brnrd.app import create_app
from brnrd.config import Settings
from brnrd.page_views import PageViewCount, counted_path, referer_host, ua_family

_spec = importlib.util.spec_from_file_location(
    "funnel_count", Path(__file__).resolve().parents[1] / "scripts" / "funnel-count.py"
)
funnel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(funnel)

CHROME = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def _app(tmp_path):
    # A stand-in SPA build, so public routes answer 200 as they do in prod.
    build = tmp_path / "build"
    build.mkdir(exist_ok=True)
    (build / "index.html").write_text("<!doctype html><title>brnrd</title>")
    return create_app(Settings(database_url=f"sqlite:///{tmp_path}/pv.db", frontend_dir=str(build)))


def _rows(app):
    with app.state.SessionLocal() as db:
        return db.execute(select(PageViewCount)).scalars().all()


def test_path_allowlist():
    assert counted_path("/") == "/"
    assert counted_path("/pricing/") == "/pricing"
    assert counted_path("/pricing?utm=x") == "/pricing"
    assert counted_path("/learn/install") == "/learn/install"
    assert counted_path("/log/2026-09-20") == "/log/2026-09-20"
    assert counted_path("/install") == "/install"
    assert counted_path("/install.sh") == "/install"
    for nope in ("/dashboard", "/v1/accounts", "/login", "/runs", "/learn/a/b/c/d", "/learn/<x>", "/" + "a" * 80):
        assert counted_path(nope) is None


def test_referer_host_drops_path_query_and_credentials():
    assert referer_host("https://News.Example.com/a/b?token=secret#x") == "news.example.com"
    assert referer_host("") == ""
    assert referer_host(None) == ""
    assert referer_host("http://[bad") == ""


def test_ua_families():
    assert ua_family(CHROME) == "chrome"
    assert ua_family("Mozilla/5.0 Version/17 Safari/605") == "safari"
    assert ua_family("Mozilla/5.0 Firefox/120.0") == "firefox"
    assert ua_family("Googlebot/2.1") == "bot"
    assert ua_family("curl/8.4.0") == "curl"
    assert ua_family(None) == "none"


def test_public_get_is_counted_and_only_as_a_counter(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        for _ in range(2):
            c.get("/pricing?email=a@b.c", headers={"referer": "https://hn.example/item?id=1", "user-agent": CHROME})
        c.get("/pricing", headers={"user-agent": CHROME})
    rows = {(r.referer_host, r.views) for r in _rows(app)}
    assert rows == {("hn.example", 2), ("", 1)}, rows
    # Nothing identifying anywhere in the table: only the declared columns exist.
    assert {c.name for c in PageViewCount.__table__.columns} == {
        "day", "path", "referer_host", "ua_family", "status", "views",
    }
    dumped = repr([vars(r) for r in _rows(app)])
    assert "a@b.c" not in dumped and "email" not in dumped and "item?id" not in dumped


def test_installer_downloads_fold_into_one_install_row(tmp_path):
    script = tmp_path / "install.sh"
    script.write_text("#!/bin/sh\necho brnrd\n")
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("<!doctype html><title>brnrd</title>")
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path}/pv.db", frontend_dir=str(build), installer_path=str(script),
    ))
    with TestClient(app) as c:
        assert c.get("/install", headers={"user-agent": "curl/8.4.0"}).text.startswith("#!/bin/sh")
        assert c.get("/install.sh?v=1", headers={"user-agent": "curl/8.4.0"}).status_code == 200
        assert c.get("/install", headers={"user-agent": CHROME, "referer": "https://brnrd.dev/pricing"}).status_code == 200
        assert c.get("/install", headers={"user-agent": "Googlebot/2.1"}).status_code == 200
        assert c.head("/install", headers={"user-agent": "curl/8.4.0"}).status_code == 200
        assert c.head("/install.sh", headers={"user-agent": "curl/8.4.0"}).status_code == 200
    rows = {(r.path, r.referer_host, r.ua_family, r.status, r.views) for r in _rows(app)}
    assert rows == {
        ("/install", "", "curl", 200, 2),
        ("/install", "brnrd.dev", "chrome", 200, 1),
        ("/install", "", "bot", 200, 1),
    }, rows


def test_uncounted_requests_leave_no_row(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        c.get("/healthz")
        c.get("/login")
        c.post("/pricing")
        c.get("/v1/stats/public")
    assert _rows(app) == []


def test_a_counter_failure_never_fails_the_page(tmp_path, monkeypatch):
    app = _app(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("brnrd.page_views.record_view", boom)
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        c.get("/pricing")  # whatever the SPA answers, no exception escapes


def test_funnel_script_table(tmp_path, capsys):
    app = _app(tmp_path)
    day = dt.date(2026, 9, 19)
    from brnrd.page_views import record_view

    f = app.state.SessionLocal
    for path, ref, ua, n in [
        ("/", "hn.example", "chrome", 3), ("/", "", "firefox", 2), ("/pricing", "hn.example", "chrome", 2),
        ("/learn/install", "", "safari", 1), ("/", "", "bot", 9),
    ]:
        for _ in range(n):
            record_view(f, day=day, path=path, referer=ref, ua=ua, status=200)
    assert funnel.main(["--database-url", f"sqlite:///{tmp_path}/pv.db", "--day", "2026-09-19"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1].split()[:3] == ["2026-09-19", "/", "5"] and "hn.example×3" in out[1] and "(direct)×2" in out[1]
    assert out[2].split()[:3] == ["2026-09-19", "/pricing", "2"]
    assert out[3].split()[:3] == ["2026-09-19", "/learn/install", "1"]
    funnel.main(["--database-url", f"sqlite:///{tmp_path}/pv.db", "--day", "2026-09-19", "--include-bots"])
    assert " 14 " in capsys.readouterr().out


def test_campaign_paths_drop_real_pages_and_keep_hi():
    from brnrd.config import Settings, campaign_paths_from

    assert campaign_paths_from(None) == ("/hi",)
    assert campaign_paths_from(" /XPost, /pricing, /new, /a-, /nope/two, /hi ") == ("/hi", "/xpost")
    assert campaign_paths_from("/pricing") == ("/hi",)
    assert campaign_paths_from("/x") == ("/hi", "/x")
    assert Settings(campaign_paths=("/pricing", "/xpost")).campaign_paths == ("/hi", "/xpost")


def test_hi_stays_when_campaign_paths_names_another(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_CAMPAIGN_PATHS", "/x")
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("<!doctype html><title>brnrd</title>")
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path}/pv.db",
        frontend_dir=str(build),
    ))
    assert app.state.settings.campaign_paths == ("/hi", "/x")
    with TestClient(app) as c:
        response = c.get("/hi", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/"
        assert c.get("/x", follow_redirects=False).status_code == 302


def test_a_configured_campaign_path_counts_and_sets_no_cookie(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("<!doctype html><title>brnrd</title>")
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path}/pv.db",
        frontend_dir=str(build),
        campaign_paths=("/hi", "/xpost"),
    ))
    with TestClient(app) as c:
        headers = {"referer": "https://t.co/secret?q=1", "user-agent": CHROME}
        response = c.get("/xpost?from=x", headers=headers, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/"
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
    rows = _rows(app)
    assert [(r.path, r.referer_host, r.ua_family, r.status, r.views) for r in rows] == [
        ("/xpost", "t.co", "chrome", 302, 1),
    ]
    assert "from=x" not in repr([vars(r) for r in rows])


def test_promo_redirect_counts_get_once_and_keeps_only_referrer_host(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as c:
        headers = {"referer": "https://t.co/private?query=secret", "user-agent": CHROME}
        response = c.get("/hi?click=secret", headers=headers, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/"
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        assert c.head("/hi", headers=headers, follow_redirects=False).status_code == 302
        assert c.post("/hi", headers=headers).status_code >= 400

    assert [(r.path, r.referer_host, r.ua_family, r.status, r.views) for r in _rows(app)] == [
        ("/hi", "t.co", "chrome", 302, 1),
    ]
