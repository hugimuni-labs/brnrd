"""Tests for the brnrd_web dashboard (GitHub login + approve page)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app, terms  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, PairRequest, Repo, TermsAcceptance, TgPairCode  # noqa: E402
from brnrd.oauth import GitHubIdentity, OAuthError  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

_EMAIL = "owner@example.com"
_GITHUB_ID = "12345"
_LOGIN = "octocat"


def _make_client(**settings_overrides):
    kwargs = dict(
        database_url="sqlite:///:memory:",
        public_base_url="https://brnrd.example",
        github_oauth_client_id="gh-client",
        github_oauth_client_secret="gh-secret",
        github_oauth_authorize_url="https://github.example/login/oauth/authorize",
        github_oauth_token_url="https://github.example/login/oauth/access_token",
        github_api_base_url="https://api.github.example",
    )
    kwargs.update(settings_overrides)
    app = create_app(
        Settings(**kwargs)
    )
    # brnrd is served over HTTPS in production (public_base_url is https),
    # so the session/OAuth cookies carry the Secure flag. Model that here
    # so a Secure cookie round-trips back to the app on follow-up requests.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture()
def client():
    return _make_client()


def _account_and_repo(client):
    headers = brnrd_account_headers(
        client.app, github_id=_GITHUB_ID, login=_LOGIN, email=_EMAIL
    )
    repo_id = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/laptop"},
        headers=headers,
    ).json()["repo_id"]
    return repo_id


def _oauth_start(client, *, next="/"):
    return client.get(
        f"/auth/github/start?next={next}", follow_redirects=False
    )


def _login_web(
    client,
    monkeypatch,
    *,
    next="/",
    identity=GitHubIdentity(github_id=_GITHUB_ID, login=_LOGIN, email=_EMAIL),
):
    seen: dict[str, str] = {}

    def fake_resolve(settings, *, code, redirect_uri, code_verifier):
        seen["code"] = code
        seen["redirect_uri"] = redirect_uri
        seen["code_verifier"] = code_verifier
        return identity

    monkeypatch.setattr("brnrd.routers.web_auth.oauth.resolve_identity", fake_resolve)
    start = _oauth_start(client, next=next)
    location = urlparse(start.headers["location"])
    query = parse_qs(location.query)
    state = query["state"][0]
    callback = client.get(
        f"/auth/github/callback?code=ok&state={state}", follow_redirects=False
    )
    return start, callback, seen


def _acceptances(client, kind):
    """Every acceptance row for ``kind``, oldest first.

    Read straight out of the table rather than through the status endpoint:
    a test that only ever asks the API "am I accepted?" cannot see a second
    row being written, and append-only-ness is the property that makes this
    a table instead of two columns.
    """
    with client.app.state.SessionLocal() as db:
        return list(
            db.execute(
                select(TermsAcceptance)
                .where(TermsAcceptance.document == kind)
                .order_by(TermsAcceptance.accepted_at)
            ).scalars()
        )


def _accept_general_terms(client, monkeypatch, *, next="/"):
    """Get a session past the #735 login gate, through the real caller.

    Not a fixture shortcut that writes a row directly: a test whose setup
    forges the acceptance would still pass if the endpoint that is supposed
    to write it stopped working.
    """
    _login_web(client, monkeypatch, next=next)
    accepted = client.post(
        "/v1/terms/accept",
        json={"document": "tos", "accept_terms": "yes"},
        follow_redirects=False,
    )
    assert accepted.status_code == 200, accepted.text
    return accepted


def test_login_context_carries_backend_validated_next(client):
    """#327 /login slice: the SPA renders the OAuth start URL the backend
    hands back — `_safe_next` stays server-owned, exactly like the retired
    Jinja page."""
    r = client.get("/v1/dashboard/login-context?next=/connect/BR-123")
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is False
    assert body["oauth_ready"] is True
    assert body["signin_url"] == "/auth/github/start?next=/connect/BR-123"
    assert body["next"] == "/connect/BR-123"

    hostile = client.get("/v1/dashboard/login-context?next=//evil.example")
    assert hostile.json()["signin_url"] == "/auth/github/start?next=/"


def test_login_context_reports_authenticated_session(client, monkeypatch):
    _login_web(client, monkeypatch)
    r = client.get("/v1/dashboard/login-context?next=/repos")
    body = r.json()
    assert body["authenticated"] is True
    assert body["next"] == "/repos"


def test_login_page_is_spa_owned(client):
    # #847: the 308 bare-uvicorn shim is gone. The app serves the SPA now, so
    # a backend route on /login would beat the page it stood in for — its
    # absence is what keeps `src/frontend/src/routes/login/` reachable.
    assert "login" not in client.app.state.backend_namespaces


def _message_page(client, monkeypatch):
    """A guaranteed non-dashboard Jinja render (message.html via the
    oauth-unready path) — the probe the retired /login page used to be for
    the two regression tests below."""
    monkeypatch.setattr("brnrd.routers.web_auth._github_oauth_ready", lambda _request: False)
    r = client.get("/auth/github/start")
    assert r.status_code == 503
    return r


def test_non_dashboard_pages_do_not_load_the_legacy_dashboard_stylesheet(client, monkeypatch):
    """Live-caught 2026-07-09 (screenshot from the user): the then-Jinja
    /login and /terms pages rendered a green GitHub-identity card and button
    against the amber brand palette PR #301 (2026-07-08) shipped in app.css.
    Root cause was not a caching regression (the cache-busting fix below
    already covers that) but a cascade bug: base.html loaded dashboard.css
    unconditionally on every page, and dashboard.css — the legacy mint/teal
    control-deck sheet for the plans/activity dashboards — defines unscoped
    `.eyebrow`/`.button`/`.button-primary` rules that, loaded after app.css
    with identical specificity, always won the cascade and clobbered the
    amber values on every non-dashboard page. Fixed by only linking
    dashboard.css when body_class is 'dashboard-page'."""
    r = _message_page(client, monkeypatch)
    assert "dashboard.css" not in r.text


def test_static_asset_urls_carry_a_real_cache_busting_version(client, monkeypatch):
    """Live-caught 2026-07-08: base.html's `?v={{ asset_version }}` was never
    wired to a value, so every deploy served the identical `app.css?v=` URL —
    Cloudflare kept serving pre-fix (green) CSS bytes under that stable cache
    key for its full max-age after the brand-palette fix (PR #301) had
    already shipped and deployed. A non-empty version tied to file content
    means a real static-asset change always mints a new URL."""
    r = _message_page(client, monkeypatch)
    assert "app.css?v=" in r.text
    assert "app.css?v=\"" not in r.text
    assert "dashboard.css?v=\"" not in r.text


def test_logout_clears_session_cookie_and_redirects_to_login(client, monkeypatch):
    """Named directly as a real gap (2026-07-08): no way to end a browser
    session short of clearing cookies by hand."""
    _start, callback, _seen = _login_web(client, monkeypatch)
    session_cookie_name = client.app.state.settings.session_cookie
    assert client.cookies.get(session_cookie_name)

    r = client.get("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert client.cookies.get(session_cookie_name) is None


def test_web_static_assets_are_served(client):
    r = client.get("/static/brnrd_web/app.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]
    assert ".state-shell" in r.text
    assert ".panel::after" in r.text
    assert ".auth-shell" not in r.text


def test_github_login_redirect_uses_state_and_pkce(client):
    r = _oauth_start(client, next="/connect/BR-123")
    assert r.status_code == 303
    location = urlparse(r.headers["location"])
    query = parse_qs(location.query)
    assert location.scheme == "https"
    assert location.netloc == "github.example"
    assert query["client_id"] == ["gh-client"]
    assert query["redirect_uri"] == ["https://brnrd.example/auth/github/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["user:email"]
    assert query["state"][0]
    assert query["code_challenge"][0]


def test_terms_status_is_public_but_acceptance_is_unknown_for_anonymous_users(client):
    r = client.get("/v1/dashboard/terms-status")
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is False
    assert sorted(body["documents"]) == ["hosted-execution", "tos"]
    for kind, doc in body["documents"].items():
        # Without an account the acceptance question does not apply. None
        # preserves that unknown state instead of claiming acceptance.
        assert doc["needs_accept"] is None
        assert doc["accepted_at"] is None
        assert doc["version"] == terms.current(kind).version
        assert doc["sha256"] == terms.current(kind).sha256


def test_anonymous_terms_status_cannot_be_mistaken_for_accepted(client):
    body = client.get("/v1/dashboard/terms-status").json()
    assert body["authenticated"] is False
    for doc in body["documents"].values():
        assert doc["needs_accept"] is not False


def test_terms_status_reports_authenticated_acceptance_state(client, monkeypatch):
    _accept_general_terms(client, monkeypatch, next="/connect/BR-123")
    body = client.get("/v1/dashboard/terms-status").json()
    assert body["authenticated"] is True
    # The general terms were just accepted through the real endpoint; the
    # hosted addendum is a different document and stays outstanding. One
    # acceptance must never satisfy the other (#569).
    assert body["documents"]["tos"]["needs_accept"] is False
    assert body["documents"]["tos"]["accepted_sha256"] == terms.current(terms.DOC_TOS).sha256
    assert body["documents"]["hosted-execution"]["needs_accept"] is True
    assert body["documents"]["hosted-execution"]["accepted_at"] is None


def test_github_callback_gates_login_on_the_general_terms(client, monkeypatch):
    """#735: signing in asks for the general Terms of Service.

    The site has been selling since 2026-07-21 with no acceptance record for
    the document that governs the sale. Login is where the condition those
    terms describe — using brnrd.dev at all — becomes true, so login is where
    they are asked for, with the original destination preserved so accepting
    resumes the journey.
    """
    _, callback, _ = _login_web(client, monkeypatch, next="/connect/BR-123")
    assert callback.status_code == 303
    assert callback.headers["location"] == "/terms?next=/connect/BR-123"
    # The session is issued anyway: the accept endpoint needs it, and a gate
    # that logged you out to ask a question could never be answered.
    assert client.get("/v1/dashboard/login-context").json()["authenticated"] is True
    assert client.get("/v1/dashboard/terms-status").json()["documents"]["tos"]["needs_accept"] is True


def test_github_callback_does_not_gate_login_on_hosted_terms(client, monkeypatch):
    """#664: the hosted-execution beta terms are not an authentication gate.

    Driven through the real OAuth callback — the caller the defect lived in —
    not through the predicate. An account that has never accepted the hosted
    addendum (and, per the assertions below, still has not) reaches the
    destination it asked for.

    #735 added a general-ToS gate to this same callback, which is why this
    test now satisfies that gate first, through the real accept endpoint: the
    point being defended is that *hosted* terms hold nobody up, and it would
    be untestable if the general gate were left standing in the way.
    """
    _accept_general_terms(client, monkeypatch, next="/connect/BR-123")

    _, callback, seen = _login_web(client, monkeypatch, next="/connect/BR-123")
    assert callback.status_code == 303
    assert callback.headers["location"] == "/connect/BR-123"
    assert "brnrd_session" in callback.cookies or "brnrd_session" in client.cookies
    assert seen["code"] == "ok"
    assert seen["redirect_uri"] == "https://brnrd.example/auth/github/callback"
    assert seen["code_verifier"]

    with client.app.state.SessionLocal() as db:
        account = db.execute(
            select(Account).where(Account.github_id == _GITHUB_ID)
        ).scalar_one()
        assert account.github_login == _LOGIN
        assert account.email == _EMAIL
        assert _acceptances(client, terms.DOC_HOSTED) == []
        repos = db.execute(
            select(Repo).where(Repo.account_id == account.id)
        ).scalars().all()
        assert repos == []

    # The session the un-accepted account got is a working one — a Location
    # header alone would not prove login "completed".
    context = client.get("/v1/dashboard/login-context?next=/connect/BR-123")
    assert context.status_code == 200
    assert context.json()["authenticated"] is True
    # …and the server still knows acceptance is outstanding, for whatever
    # surface eventually offers hosted execution.
    status = client.get("/v1/dashboard/terms-status").json()
    assert status["documents"]["hosted-execution"]["needs_accept"] is True


def test_hosted_terms_acceptance_still_records_after_ungated_login(client, monkeypatch):
    """#664 removes a gate; it must not remove the acceptance path.

    Same lifecycle a point-of-use surface would drive: authenticate through
    the real OAuth callback without accepting the addendum, then accept it,
    and confirm the record lands and ``needs_accept`` flips.
    """
    _accept_general_terms(client, monkeypatch, next="/repos")
    _, callback, _ = _login_web(client, monkeypatch, next="/repos")
    assert callback.headers["location"] == "/repos"
    status = client.get("/v1/dashboard/terms-status").json()
    assert status["documents"]["hosted-execution"]["needs_accept"] is True

    accept = client.post(
        "/v1/terms/accept",
        json={"document": "hosted-execution", "accept_terms": "yes"},
        follow_redirects=False,
    )
    assert accept.status_code == 200
    assert accept.json()["ok"] is True

    rows = _acceptances(client, terms.DOC_HOSTED)
    assert len(rows) == 1
    assert rows[0].version == terms.current(terms.DOC_HOSTED).version
    # The record reproduces the document, not just its label — the defect
    # `hosted_terms_version` had (#735).
    assert terms.text_for_sha256(rows[0].sha256) == terms.current(terms.DOC_HOSTED).text

    status = client.get("/v1/dashboard/terms-status").json()["documents"]["hosted-execution"]
    assert status["needs_accept"] is False
    assert status["accepted_at"] is not None


def test_terms_acceptance_requires_session(client):
    r = client.post("/v1/terms/accept", json={"accept_terms": "yes"})
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthenticated"}


def test_terms_acceptance_requires_checkbox(client, monkeypatch):
    _login_web(client, monkeypatch, next="/connect/BR-123")
    r = client.post(
        "/v1/terms/accept",
        json={},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert r.json() == {
        "ok": False,
        "notice": "You need to accept the beta hosted-execution terms before continuing.",
    }
    assert _acceptances(client, terms.DOC_HOSTED) == []


def test_terms_acceptance_records_account_and_redirects(client, monkeypatch):
    _login_web(client, monkeypatch, next="/connect/BR-123")
    r = client.post(
        "/v1/terms/accept",
        json={"accept_terms": "yes"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # An omitted ``document`` still means the hosted addendum: that is the
    # payload the pre-#735 widget sends, and defaulting it to the general ToS
    # would be the silent repurpose #569 forbids.
    assert body["document"] == "hosted-execution"
    assert _acceptances(client, terms.DOC_TOS) == []
    rows = _acceptances(client, terms.DOC_HOSTED)
    assert len(rows) == 1
    assert rows[0].version == "2026-07-08"
    status = client.get("/v1/dashboard/terms-status")
    assert status.status_code == 200
    assert status.json()["authenticated"] is True
    hosted = status.json()["documents"]["hosted-execution"]
    assert hosted["needs_accept"] is False
    assert hosted["accepted_at"] is not None


def test_the_backend_does_not_claim_the_terms_namespace(client):
    """#847: `GET /terms/accept` is gone, and `/terms` is the SPA's.

    That route was a 308 covering OAuth links minted before #569 moved the
    hosted-execution document. It was also the single backend path standing
    inside a namespace the SPA owns, which would have forced a hand-written
    exception into the mechanism that replaces hand-written exceptions.

    What it guarded — that each document's accept URL points at the page
    carrying that document's own words — is asserted at the producer by
    `test_hosted_terms_accept_url_is_not_the_general_terms_page` below, which
    is the stronger place for it.
    """
    assert "terms" not in client.app.state.backend_namespaces


def test_hosted_terms_accept_url_is_not_the_general_terms_page(client):
    """Guards the split at its producer, not just at the shim.

    A future caller that reintroduces ``/terms`` as the acceptance target
    would silently make the ToS page the thing a user "accepts" — the exact
    repurpose #569 forbids — without failing the shim test above if the shim
    were also changed to match.
    """
    from brnrd.routers._session import _terms_accept_url

    url = _terms_accept_url("/repos")
    assert url.startswith("/beta-hosted-execution?")
    assert not url.startswith("/terms")
    # The existing safe-next contract is unchanged by the move.
    assert _terms_accept_url("//evil.example").startswith("/beta-hosted-execution?next=/")


def test_github_login_is_not_the_identity_key(client):
    brnrd_account_headers(
        client.app, github_id="1", login="octocat", email="one@example.com"
    )
    brnrd_account_headers(
        client.app, github_id="2", login="octocat", email="two@example.com"
    )
    with client.app.state.SessionLocal() as db:
        accounts = db.execute(
            select(Account).where(Account.github_login == "octocat")
        ).scalars().all()
    assert {account.github_id for account in accounts} == {"1", "2"}


def test_github_callback_rejects_state_mismatch(client, monkeypatch):
    monkeypatch.setattr(
        "brnrd.routers.web_auth.oauth.resolve_identity",
        lambda *a, **k: GitHubIdentity(github_id=_GITHUB_ID, login=_LOGIN),
    )
    _oauth_start(client)
    r = client.get(
        "/auth/github/callback?code=ok&state=wrong", follow_redirects=False
    )
    assert r.status_code == 400


def test_github_callback_surfaces_provider_failure(client, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OAuthError("provider down")

    monkeypatch.setattr("brnrd.routers.web_auth.oauth.resolve_identity", fail)
    start = _oauth_start(client)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    r = client.get(
        f"/auth/github/callback?code=ok&state={state}", follow_redirects=False
    )
    assert r.status_code == 502
    assert "provider down" in r.text


def test_connect_page_is_spa_owned(client):
    # #847: same as /login — the 308 shim is gone and its absence is the
    # guard. The device-flow JSON lives under /v1/connect/{code}, which stays
    # backend-owned; only the HTML deep link belongs to SvelteKit.
    claimed = client.app.state.backend_namespaces
    assert "connect" not in claimed
    assert "v1" in claimed


def test_connect_api_requires_login(client):
    """#327 /connect slice: the session requirement the Jinja page enforced
    with a 303 → /login is a 401 on the JSON transport — no anonymous read
    of the repo list, no anonymous approve."""
    _account_and_repo(client)
    pair = client.post("/v1/accounts/pair").json()
    r = client.get(f"/v1/connect/{pair['pair_code']}")
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthenticated"}
    r = client.post(f"/v1/connect/{pair['pair_code']}", json={"repo_id": "repo_x"})
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthenticated"}


def test_connect_context_lists_repos_and_code_status(client, monkeypatch):
    repo_id = _account_and_repo(client)
    _login_web(client, monkeypatch)
    pair = client.post("/v1/accounts/pair").json()
    r = client.get(f"/v1/connect/{pair['pair_code']}")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == pair["pair_code"]
    assert body["status"] == "pending"
    assert body["repos"] == [{"id": repo_id, "repo_full_name": "Gurio/laptop"}]

    unknown = client.get("/v1/connect/BR-NOPE")
    assert unknown.status_code == 200
    assert unknown.json()["status"] == "unknown"


def test_connect_context_reports_expired_code(client, monkeypatch):
    _account_and_repo(client)
    _login_web(client, monkeypatch)
    pair = client.post("/v1/accounts/pair").json()
    from datetime import datetime, timedelta, timezone

    with client.app.state.SessionLocal() as db:
        row = db.execute(
            select(PairRequest).where(PairRequest.pair_code == pair["pair_code"])
        ).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    r = client.get(f"/v1/connect/{pair['pair_code']}")
    assert r.json()["status"] == "expired"

    approve = client.post(
        f"/v1/connect/{pair['pair_code']}", json={"repo_id": "repo_x"}
    )
    assert approve.status_code == 410
    assert approve.json() == {"ok": False, "notice": "pair code expired"}


def test_connect_approve_rejects_unknown_code(client, monkeypatch):
    _account_and_repo(client)
    _login_web(client, monkeypatch)
    r = client.post("/v1/connect/BR-NOPE", json={"repo_id": "repo_x"})
    assert r.status_code == 404
    assert r.json() == {"ok": False, "notice": "unknown pair code"}


def test_connect_approve_binds_to_the_sessions_account(client, monkeypatch):
    """The repo lookup is scoped to the session's own account (`approve_core`):
    approving with another account's repo id is a 404, and the pair code
    stays pending — the exact scoping the Jinja form had."""
    _account_and_repo(client)
    other_repo_id = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Intruder/box"},
        headers=brnrd_account_headers(
            client.app, github_id="99999", login="intruder", email="other@example.com"
        ),
    ).json()["repo_id"]
    _login_web(client, monkeypatch)  # session for the _GITHUB_ID account
    pair = client.post("/v1/accounts/pair").json()

    r = client.post(
        f"/v1/connect/{pair['pair_code']}", json={"repo_id": other_repo_id}
    )
    assert r.status_code == 404
    assert r.json() == {"ok": False, "notice": "repo not found"}
    with client.app.state.SessionLocal() as db:
        row = db.execute(
            select(PairRequest).where(PairRequest.pair_code == pair["pair_code"])
        ).scalar_one()
        assert row.status == PairRequest.STATUS_PENDING
        assert row.account_id is None


def test_connect_approve_makes_poll_return_token(client, monkeypatch):
    repo_id = _account_and_repo(client)
    _login_web(client, monkeypatch)
    pair = client.post("/v1/accounts/pair").json()

    approve = client.post(
        f"/v1/connect/{pair['pair_code']}", json={"repo_id": repo_id}
    )
    assert approve.status_code == 200
    body = approve.json()
    assert body["ok"] is True
    assert "Your daemon is connected" in body["notice"]
    assert body["telegram"]["pair_code"].startswith("TG-")

    # The CLI's poll now returns the freshly minted daemon token.
    polled = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    assert polled["status"] == "paired"
    assert polled["daemon_token"]
    assert polled["repo_id"] == repo_id
    assert polled["telegram_pair"]["pair_code"].startswith("TG-")
    assert f"/start {polled['telegram_pair']['pair_code']}" in polled["telegram_pair"]["instructions"]


def test_connect_approve_is_single_use_after_poll(client, monkeypatch):
    """Once the daemon's poll consumes the code, a second approve is a 409 —
    the same single-use guarantee the Jinja POST had (`approve_core`)."""
    repo_id = _account_and_repo(client)
    _login_web(client, monkeypatch)
    pair = client.post("/v1/accounts/pair").json()
    assert client.post(
        f"/v1/connect/{pair['pair_code']}", json={"repo_id": repo_id}
    ).json()["ok"] is True
    client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    )

    again = client.post(
        f"/v1/connect/{pair['pair_code']}", json={"repo_id": repo_id}
    )
    assert again.status_code == 409
    assert again.json() == {"ok": False, "notice": "pair code already used"}
    status = client.get(f"/v1/connect/{pair['pair_code']}").json()
    assert status["status"] == "consumed"


def test_connect_approve_offers_telegram_pair_link(monkeypatch):
    client = _make_client(telegram_bot_username="@brnrd_bot")
    repo_id = _account_and_repo(client)
    _login_web(client, monkeypatch)
    pair = client.post("/v1/accounts/pair").json()

    approve = client.post(
        f"/v1/connect/{pair['pair_code']}", json={"repo_id": repo_id}
    )
    assert approve.status_code == 200
    body = approve.json()
    assert "Your daemon is connected" in body["notice"]
    assert body["telegram"]["deep_link"].startswith("https://t.me/brnrd_bot?start=TG-")
    assert "bind this chat" in body["telegram"]["instructions"]

    polled = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()

    with client.app.state.SessionLocal() as db:
        tg_pair = db.execute(select(TgPairCode)).scalar_one()
        assert tg_pair.repo_id == repo_id
        assert polled["telegram_pair"]["pair_code"] == tg_pair.code
        assert polled["telegram_pair"]["deep_link"] == f"https://t.me/brnrd_bot?start={tg_pair.code}"
