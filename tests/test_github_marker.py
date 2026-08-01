"""Tests for brnrd-bot's own auto-accept + collaborator-status sync (#874).

The rescoped shape (issue comment, 2026-07-29): no App permission change, no
invite-on-bind — the human invites `brnrd-bot` by hand, and this module is
just the backend auto-accept + honest collaborator-state reporting half.
"""

from __future__ import annotations

import pytest
import httpx

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from brnrd import create_app, github_marker, ids  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, Repo  # noqa: E402
from brnrd.platforms import github as gh  # noqa: E402


def _app(**overrides):
    settings = Settings(database_url="sqlite:///:memory:", **overrides)
    return create_app(settings)


def _make_repo(db, *, account_id: str = "acc-1", full_name: str = "owner/repo") -> Repo:
    if db.get(Account, account_id) is None:
        db.add(Account(id=account_id, github_id=account_id, github_login=account_id))
        db.commit()
    owner, name = full_name.split("/")
    repo = Repo(
        id=ids.repo_id(),
        account_id=account_id,
        forge="github",
        repo_full_name=full_name,
        repo_owner=owner,
        repo_name=name,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


def test_no_token_is_a_no_op(monkeypatch):
    app = _app(github_bot_token="")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)
        result = github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
    assert result == github_marker.MarkerSyncResult()


def test_no_repos_is_a_no_op(monkeypatch):
    app = _app(github_bot_token="ghs_bot")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    with app.state.SessionLocal() as db:
        result = github_marker.sync_marker_for_repos(db, app.state.settings, [])
    assert result == github_marker.MarkerSyncResult()


def test_accepts_a_matching_invitation_and_marks_collaborator_true(monkeypatch):
    app = _app(github_bot_token="ghs_bot", github_bot_login="brnrd-bot")
    accepted = []
    monkeypatch.setattr(
        gh,
        "list_repository_invitations",
        lambda *a, **k: [{"id": 999, "repository": {"full_name": "owner/repo"}}],
    )
    monkeypatch.setattr(gh, "accept_repository_invitation", lambda *a, **k: accepted.append(a[-1]))
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)
        result = github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
        db.refresh(repo)
        assert repo.github_bot_collaborator is True
        assert repo.github_bot_notice is None
        assert repo.github_bot_checked_at is not None
    assert accepted == [999]
    assert result == github_marker.MarkerSyncResult(accepted=1, checked=0, failed=0)


def test_never_accepts_an_invitation_for_a_repo_it_was_not_handed(monkeypatch):
    """Scope discipline: the bot only ever acts on repos the caller resolved
    as account-bound, never "every invitation this token can see" — even
    when a stray invite is sitting right there in the same listing."""
    app = _app(github_bot_token="ghs_bot")
    accepted = []
    monkeypatch.setattr(
        gh,
        "list_repository_invitations",
        lambda *a, **k: [
            {"id": 1, "repository": {"full_name": "owner/repo"}},
            {"id": 2, "repository": {"full_name": "someone-else/unrelated"}},
        ],
    )
    monkeypatch.setattr(gh, "accept_repository_invitation", lambda *a, **k: accepted.append(a[-1]))
    monkeypatch.setattr(gh, "check_repository_collaborator", lambda *a, **k: None)
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)
        github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
    assert accepted == [1], "the unrelated repo's invitation must never be touched"


def test_accept_failure_records_a_specific_notice_and_skips_the_recheck(monkeypatch):
    """An accept failure must not be silently overwritten by a subsequent
    ambiguous collaborator check — the specific reason is what the repo view
    needs to show (#874 ask 2, never silence)."""
    app = _app(github_bot_token="ghs_bot")
    monkeypatch.setattr(
        gh,
        "list_repository_invitations",
        lambda *a, **k: [{"id": 5, "repository": {"full_name": "owner/repo"}}],
    )

    def _boom(*a, **k):
        raise RuntimeError("422 Unprocessable Entity")

    monkeypatch.setattr(gh, "accept_repository_invitation", _boom)
    monkeypatch.setattr(
        gh,
        "check_repository_collaborator",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-check a repo just processed")),
    )
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)
        result = github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
        db.refresh(repo)
        assert repo.github_bot_collaborator is None, "an accept failure proves nothing about collaborator state"
        assert repo.github_bot_notice == github_marker.MarkerCheckState.UNKNOWN.value
    assert result.accepted == 0
    assert result.failed == 1


def test_collaborator_check_covers_true_false_and_unknown(monkeypatch):
    app = _app(github_bot_token="ghs_bot", github_bot_login="brnrd-bot")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: [])

    def check(token, base, version, repo_name, username):
        if repo_name == "owner/yes":
            return True
        if repo_name == "owner/no":
            return False
        raise RuntimeError("500 Server Error")

    monkeypatch.setattr(gh, "check_repository_collaborator", check)

    with app.state.SessionLocal() as db:
        yes = _make_repo(db, full_name="owner/yes")
        no = _make_repo(db, full_name="owner/no")
        error = _make_repo(db, full_name="owner/error")
        result = github_marker.sync_marker_for_repos(db, app.state.settings, [yes, no, error])
        for repo in (yes, no, error):
            db.refresh(repo)
        assert yes.github_bot_collaborator is True
        assert yes.github_bot_notice is None
        assert no.github_bot_collaborator is False
        assert no.github_bot_notice is None
        assert error.github_bot_collaborator is None, "an ambiguous check must render unknown, never a guess"
        assert error.github_bot_notice == github_marker.MarkerCheckState.UNKNOWN.value
    assert result.checked == 3
    assert result.failed == 1


def test_empty_bot_login_never_queries_collaborators_and_says_why(monkeypatch):
    """An empty `github_bot_login` would query `/collaborators/` (trailing
    slash, empty username) — GitHub's 404 there would read as a false "not a
    collaborator" for what is actually a config gap. This must be named as
    the config gap it is, and never touch the network to find out."""
    app = _app(github_bot_token="ghs_bot", github_bot_login="")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: [])
    monkeypatch.setattr(
        gh,
        "check_repository_collaborator",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not query with an empty login")),
    )
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)
        github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
        db.refresh(repo)
        assert repo.github_bot_collaborator is None
        # "and says why": the config gap is a classifiable state with a named
        # remedy — labeling it unknown would bury the one fact we do know.
        assert repo.github_bot_notice == github_marker.MarkerCheckState.NOT_CONFIGURED.value


def test_403_collaborator_check_is_classified_without_persisting_transport_copy(monkeypatch):
    app = _app(github_bot_token="ghs_bot", github_bot_login="brnrd-bot")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: [])
    request = httpx.Request(
        "GET", "https://api.github.com/repos/owner/repo/collaborators/brnrd-bot/permission"
    )
    response = httpx.Response(403, request=request)
    raw = (
        "Client error '403 Forbidden' for url "
        "'https://api.github.com/repos/owner/repo/collaborators/brnrd-bot' "
        "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/403"
    )

    def forbidden(*_args, **_kwargs):
        raise httpx.HTTPStatusError(raw, request=request, response=response)

    monkeypatch.setattr(gh, "check_repository_collaborator", forbidden)
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)
        github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
        db.refresh(repo)
        assert repo.github_bot_collaborator is None
        assert repo.github_bot_notice == github_marker.MarkerCheckState.PERMISSION_MISSING.value
        assert raw not in repo.github_bot_notice


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, github_marker.MarkerCheckState.PERMISSION_MISSING),
        (403, github_marker.MarkerCheckState.PERMISSION_MISSING),
        (408, github_marker.MarkerCheckState.CHECK_UNAVAILABLE),
        (429, github_marker.MarkerCheckState.CHECK_UNAVAILABLE),
        (500, github_marker.MarkerCheckState.CHECK_UNAVAILABLE),
        (422, github_marker.MarkerCheckState.UNKNOWN),
    ],
)
def test_marker_check_http_failure_map(status, expected):
    request = httpx.Request("GET", "https://api.github.test/check")
    response = httpx.Response(status, request=request)
    exc = httpx.HTTPStatusError("transport copy", request=request, response=response)
    assert github_marker.classify_marker_check_failure(exc) is expected


def test_marker_check_network_failure_is_unavailable():
    request = httpx.Request("GET", "https://api.github.test/check")
    exc = httpx.ConnectError("transport copy", request=request)
    assert (
        github_marker.classify_marker_check_failure(exc)
        is github_marker.MarkerCheckState.CHECK_UNAVAILABLE
    )


@pytest.mark.parametrize(("status", "expected"), [(204, True), (404, False)])
def test_collaborator_check_uses_the_membership_endpoint(monkeypatch, status, expected):
    """The bare collaborators endpoint (204/404) answers *membership*.

    The tempting `.../permission` endpoint answers *effective access* and
    200s with `permission: read` for a complete stranger on a public repo
    (driven live, #976 review) — a 200-means-member reading makes everyone
    a collaborator. Pin the endpoint and its documented contract.
    """
    seen = []

    def get(url, **_kwargs):
        seen.append(url)
        return httpx.Response(status, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    assert (
        gh.check_repository_collaborator(
            "token", "https://api.github.test", "2026-03-10", "owner/repo", "brnrd-bot"
        )
        is expected
    )
    assert seen == ["https://api.github.test/repos/owner/repo/collaborators/brnrd-bot"]


def test_collaborator_check_refuses_an_unexpected_200(monkeypatch):
    """A 200 from the membership endpoint is contract drift, never a yes —
    the guard that keeps a future endpoint swap from silently flipping the
    check optimistic (#800's direction rule)."""

    def get(url, **_kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(RuntimeError, match="unexpected collaborator-check status 200"):
        gh.check_repository_collaborator(
            "token", "https://api.github.test", "2026-03-10", "owner/repo", "brnrd-bot"
        )


def test_marker_absence_text_names_the_effective_configured_login():
    text = github_marker.marker_absence_text("brnrd-bot")
    assert text.startswith("brnrd-bot not a collaborator")
    assert "Settings → Collaborators" in text
    # A leading "@" (as GitHub renders handles) is stripped so the sentence
    # doesn't read "@brnrd-bot not a collaborator" — matches how
    # dashboard.py already normalizes `github_bot_login` for display.
    assert github_marker.marker_absence_text("@brnrd-bot").startswith("brnrd-bot not")
