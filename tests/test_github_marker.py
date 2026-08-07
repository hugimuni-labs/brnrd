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
from brnrd.models import Account, GitHubInstallation, GitHubInstalledRepo, Repo  # noqa: E402
from brnrd.platforms import github as gh  # noqa: E402
from brnrd.platforms import github_app as gh_app  # noqa: E402


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


def _bind_installation(db, repo: Repo, *, installation_id: str = "73") -> GitHubInstallation:
    """Give ``repo`` an App installation covering it — the #1141 precondition
    for the collaborator check to run on the App token instead of falling
    back to the bot's own user token. Reuses an existing installation row
    for the same ``installation_id`` (two repos can share one installation),
    same upsert shape as `routers.github_app.sync_installation`."""
    from sqlalchemy import select as _select

    installation = db.execute(
        _select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    ).scalar_one_or_none()
    if installation is None:
        installation = GitHubInstallation(
            id=ids.github_installation_id(),
            account_id=repo.account_id,
            installation_id=installation_id,
            target_login=repo.repo_owner,
            target_type="User",
        )
        db.add(installation)
        db.flush()
    db.add(
        GitHubInstalledRepo(
            id=ids.github_installed_repo_id(),
            github_installation_id=installation.id,
            repo_full_name=repo.repo_full_name,
        )
    )
    db.commit()
    return installation


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


def test_collaborator_check_runs_on_the_app_installation_token_when_one_covers_the_repo(
    monkeypatch,
):
    """#1141 — the principal swap. A repo with an App installation checks on
    the App's own installation token, not brnrd-bot's user token: the App
    only needs `Metadata: read` (already granted) and answers definitively,
    where the user token's 403 is ambiguous with "not a collaborator"."""
    app = _app(github_bot_token="ghs_bot", github_bot_login="brnrd-bot")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: [])
    seen_tokens = []

    def check(token, base, version, repo_name, username):
        seen_tokens.append(token)
        return True

    monkeypatch.setattr(gh, "check_repository_collaborator", check)
    monkeypatch.setattr(
        gh_app, "installation_access_token", lambda settings, installation_id: "ghs_installation"
    )
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)
        _bind_installation(db, repo)
        github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
        db.refresh(repo)
        assert repo.github_bot_collaborator is True
    assert seen_tokens == ["ghs_installation"], "must check with the App token, not the bot's own"


def test_collaborator_check_mints_one_installation_token_for_a_shared_installation(monkeypatch):
    """A batch of repos under the same installation reuses one minted token
    rather than re-minting per repo."""
    app = _app(github_bot_token="ghs_bot", github_bot_login="brnrd-bot")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: [])
    monkeypatch.setattr(gh, "check_repository_collaborator", lambda *a, **k: True)
    mints = []

    def mint(settings, installation_id):
        mints.append(installation_id)
        return f"ghs_{installation_id}"

    monkeypatch.setattr(gh_app, "installation_access_token", mint)
    with app.state.SessionLocal() as db:
        one = _make_repo(db, full_name="owner/one")
        two = _make_repo(db, full_name="owner/two")
        _bind_installation(db, one, installation_id="73")
        _bind_installation(db, two, installation_id="73")
        github_marker.sync_marker_for_repos(db, app.state.settings, [one, two])
    assert mints == ["73"], "one installation covering two repos must mint once, not twice"


def test_collaborator_check_falls_back_to_the_user_token_without_an_installation(monkeypatch):
    """A manually-connected repo (no App installation) has nothing to mint an
    App token from — the check must still run, on the bot's own user token,
    rather than silently going unchecked."""
    app = _app(github_bot_token="ghs_bot", github_bot_login="brnrd-bot")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: [])
    seen_tokens = []

    def check(token, base, version, repo_name, username):
        seen_tokens.append(token)
        return True

    monkeypatch.setattr(gh, "check_repository_collaborator", check)
    monkeypatch.setattr(
        gh_app,
        "installation_access_token",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no installation to mint from")),
    )
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)  # no _bind_installation call — nothing covers it
        github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
        db.refresh(repo)
        assert repo.github_bot_collaborator is True
    assert seen_tokens == ["ghs_bot"]


def test_app_installation_403_is_a_genuine_permission_fault(monkeypatch):
    """On the App token, `Metadata: read` is already granted, so a 403 from
    it (suspended installation, revoked grant) is an honest fault — unlike
    the same status on the user-token fallback (see the sibling test
    below)."""
    app = _app(github_bot_token="ghs_bot", github_bot_login="brnrd-bot")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: [])
    request = httpx.Request("GET", "https://api.github.com/repos/owner/repo/collaborators/brnrd-bot")
    response = httpx.Response(403, request=request)

    def forbidden(*_args, **_kwargs):
        raise httpx.HTTPStatusError("403", request=request, response=response)

    monkeypatch.setattr(gh, "check_repository_collaborator", forbidden)
    monkeypatch.setattr(gh_app, "installation_access_token", lambda *a, **k: "ghs_installation")
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)
        _bind_installation(db, repo)
        github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
        db.refresh(repo)
        assert repo.github_bot_collaborator is None
        assert repo.github_bot_notice == github_marker.MarkerCheckState.PERMISSION_MISSING.value


def test_user_token_fallback_403_is_not_a_collaborator_not_permission_missing(monkeypatch):
    """The measured bug (#1141): GitHub's own docs require a user-token
    caller to already have push access just to *use* the collaborators
    endpoint, so a bot lacking push access 403s regardless of whether it is
    itself a collaborator. On the no-installation fallback path that 403 must
    read as `not-a-collaborator`, not `permission-missing` — the fix this
    whole change exists for."""
    app = _app(github_bot_token="ghs_bot", github_bot_login="brnrd-bot")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: [])
    request = httpx.Request("GET", "https://api.github.com/repos/owner/repo/collaborators/brnrd-bot")
    response = httpx.Response(403, request=request)
    raw = "Client error '403 Forbidden' for url 'https://api.github.com/repos/owner/repo/collaborators/brnrd-bot'"

    def forbidden(*_args, **_kwargs):
        raise httpx.HTTPStatusError(raw, request=request, response=response)

    monkeypatch.setattr(gh, "check_repository_collaborator", forbidden)
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)  # no installation ⇒ the fallback path
        github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
        db.refresh(repo)
        assert repo.github_bot_collaborator is None
        assert repo.github_bot_notice == github_marker.MarkerCheckState.NOT_A_COLLABORATOR.value
        assert raw not in repo.github_bot_notice


def test_user_token_fallback_401_is_still_permission_missing(monkeypatch):
    """401 (bad/expired credential) is unambiguous regardless of principal —
    only 403's push-access quirk is user-token-specific."""
    app = _app(github_bot_token="ghs_bot", github_bot_login="brnrd-bot")
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: [])
    request = httpx.Request("GET", "https://api.github.com/repos/owner/repo/collaborators/brnrd-bot")
    response = httpx.Response(401, request=request)

    def unauthorized(*_args, **_kwargs):
        raise httpx.HTTPStatusError("401", request=request, response=response)

    monkeypatch.setattr(gh, "check_repository_collaborator", unauthorized)
    with app.state.SessionLocal() as db:
        repo = _make_repo(db)
        github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
        db.refresh(repo)
        assert repo.github_bot_notice == github_marker.MarkerCheckState.PERMISSION_MISSING.value


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
    """Sibling of `test_user_token_fallback_403_is_not_a_collaborator_not_permission_missing`,
    focused on the never-leak-transport-copy guarantee rather than the
    classification itself (that fix is #1141; this test predates it and is
    kept for the transport-copy assertion)."""
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
        repo = _make_repo(db)  # no installation ⇒ the user-token fallback path
        github_marker.sync_marker_for_repos(db, app.state.settings, [repo])
        db.refresh(repo)
        assert repo.github_bot_collaborator is None
        assert repo.github_bot_notice == github_marker.MarkerCheckState.NOT_A_COLLABORATOR.value
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
@pytest.mark.parametrize(
    "principal",
    [
        github_marker.MarkerCheckPrincipal.APP_INSTALLATION,
        github_marker.MarkerCheckPrincipal.BOT_USER_INVITATION,
    ],
)
def test_marker_check_http_failure_map(status, expected, principal):
    """401/403 are a genuine permission fault for both of these principals —
    only `BOT_USER_COLLABORATOR_CHECK` (see the dedicated tests above) has
    the push-access-required quirk that turns a 403 into `not-a-collaborator`."""
    request = httpx.Request("GET", "https://api.github.test/check")
    response = httpx.Response(status, request=request)
    exc = httpx.HTTPStatusError("transport copy", request=request, response=response)
    assert github_marker.classify_marker_check_failure(exc, principal=principal) is expected


@pytest.mark.parametrize(
    "principal",
    [
        github_marker.MarkerCheckPrincipal.APP_INSTALLATION,
        github_marker.MarkerCheckPrincipal.BOT_USER_INVITATION,
        github_marker.MarkerCheckPrincipal.BOT_USER_COLLABORATOR_CHECK,
    ],
)
def test_marker_check_network_failure_is_unavailable(principal):
    request = httpx.Request("GET", "https://api.github.test/check")
    exc = httpx.ConnectError("transport copy", request=request)
    assert (
        github_marker.classify_marker_check_failure(exc, principal=principal)
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
    assert text.startswith("brnrd-bot isn't a collaborator")
    assert "Settings → Collaborators" in text
    # A leading "@" (as GitHub renders handles) is stripped so the sentence
    # doesn't read "@brnrd-bot isn't a collaborator" — matches how
    # dashboard.py already normalizes `github_bot_login` for display.
    assert github_marker.marker_absence_text("@brnrd-bot").startswith("brnrd-bot isn't")
