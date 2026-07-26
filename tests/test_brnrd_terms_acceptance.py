"""#735 — the acceptance record for the general Terms of Service.

The defect these cover: brnrd.dev has been selling since 2026-07-21 under a
Terms of Service no user was ever asked to accept and no row ever recorded,
while the login page told them the document applied. The record that *did*
exist — ``accounts.hosted_terms_accepted_at`` / ``hosted_terms_version`` —
could not reproduce what was accepted, because a version string names a
Svelte component git can change underneath it.

So the properties asserted here are evidentiary, not CRUD:

* a version bump writes a **second** row and leaves the first intact, because
  the earlier acceptance is what governed the earlier conduct;
* an acceptance of one document never satisfies another (#569);
* a stored hash resolves back to the exact document;
* the migration manufactures **nothing**.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app, migrations, terms  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import TermsAcceptance  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402

_GITHUB_ID = "12345"
_LOGIN = "octocat"


@pytest.fixture()
def client():
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            public_base_url="https://brnrd.example",
            github_oauth_client_id="gh-client",
            github_oauth_client_secret="gh-secret",
            github_oauth_authorize_url="https://github.example/login/oauth/authorize",
            github_oauth_token_url="https://github.example/login/oauth/access_token",
            github_api_base_url="https://api.github.example",
        )
    )
    return TestClient(app, base_url="https://testserver")


def _login(client, monkeypatch, *, next="/"):
    """Sign in through the real OAuth callback and return its redirect."""
    monkeypatch.setattr(
        "brnrd.routers.web_auth.oauth.resolve_identity",
        lambda settings, *, code, redirect_uri, code_verifier: GitHubIdentity(
            github_id=_GITHUB_ID, login=_LOGIN, email="owner@example.com"
        ),
    )
    start = client.get(f"/auth/github/start?next={next}", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    return client.get(f"/auth/github/callback?code=ok&state={state}", follow_redirects=False)


def _accept(client, kind):
    return client.post(
        "/v1/terms/accept",
        json={"document": kind, "accept_terms": "yes"},
        follow_redirects=False,
    )


def _rows(client, kind=None):
    with client.app.state.SessionLocal() as db:
        stmt = select(TermsAcceptance).order_by(TermsAcceptance.accepted_at)
        if kind is not None:
            stmt = stmt.where(TermsAcceptance.document == kind)
        return list(db.execute(stmt).scalars())


@pytest.fixture()
def bumped_tos(monkeypatch, tmp_path):
    """Publish a new ToS version, the way a real bump would.

    A new file beside the old one — never an edit in place, because every
    ``sha256`` already written has to stay resolvable.
    """
    for existing in terms._LEGAL_DIR.glob("*.txt"):
        (tmp_path / existing.name).write_text(existing.read_text(encoding="utf-8"), encoding="utf-8")
    successor = tmp_path / "tos-2027-01-01.txt"
    successor.write_text(
        terms.current(terms.DOC_TOS).text + "\n\n17. A clause that did not exist before.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(terms, "_LEGAL_DIR", tmp_path)
    monkeypatch.setitem(terms._CURRENT, terms.DOC_TOS, ("2027-01-01", successor.name))
    terms._by_sha256.cache_clear()
    yield
    terms._by_sha256.cache_clear()


def test_login_asks_for_the_general_terms_and_carries_the_destination(client, monkeypatch):
    callback = _login(client, monkeypatch, next="/connect/BR-123")
    assert callback.headers["location"] == "/terms?next=/connect/BR-123"
    # Accepting resumes the journey rather than ending it: the second login
    # goes where the first one was headed.
    assert _accept(client, terms.DOC_TOS).status_code == 200
    assert _login(client, monkeypatch, next="/connect/BR-123").headers["location"] == "/connect/BR-123"


@pytest.mark.parametrize("hostile", ["//evil.example", "/\\evil.example"])
def test_the_gate_refuses_an_open_redirect_smuggled_through_next(client, monkeypatch, hostile):
    """Both authority-position forms, not just the famous one.

    ``//host`` is the protocol-relative redirect everybody guards. ``/\\host``
    is the one that gets missed: it starts with ``/``, so a naive check passes
    it, and then the browser normalises the backslash and
    ``new URL('/\\evil.example', origin)`` resolves to ``https://evil.example/``.

    This matters *here* specifically. The backend's own ``RedirectResponse``
    survives it by accident (Starlette percent-encodes ``Location``), so before
    #735 nothing carried the value to a vulnerable sink. #735 routes every
    un-accepted user to ``/terms?next=…``, where the frontend reads the
    parameter and calls ``window.location.assign`` on it — which does not
    percent-encode. The gate turned a dormant weakness into a reachable one,
    so the gate is where it gets closed.
    """
    callback = _login(client, monkeypatch, next=hostile)
    assert callback.headers["location"] == "/terms?next=/"


@pytest.mark.parametrize("hostile", ["", None, 0, False])
def test_a_malformed_document_is_refused_rather_than_defaulted(client, monkeypatch, hostile):
    """Omitted means the addendum; malformed means nothing at all.

    The pre-#735 widget sends no ``document`` field, so an absent one has to
    keep meaning ``hosted-execution``. But a *present* field that is empty,
    null or zero is a caller bug or an attack, and folding it into the default
    would write a real consent record for a document the caller never named —
    the silent repurpose #569 exists to forbid.
    """
    _login(client, monkeypatch)
    r = client.post(
        "/v1/terms/accept",
        json={"document": hostile, "accept_terms": "yes"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert _rows(client) == []


def test_omitting_the_document_still_means_the_addendum(client, monkeypatch):
    """The compatibility half of the rule above, pinned so it cannot drift."""
    _login(client, monkeypatch)
    r = client.post("/v1/terms/accept", json={"accept_terms": "yes"}, follow_redirects=False)
    assert r.status_code == 200
    assert r.json()["document"] == terms.DOC_HOSTED
    assert _rows(client, terms.DOC_TOS) == []
    assert len(_rows(client, terms.DOC_HOSTED)) == 1


def test_a_racing_second_accept_reports_the_truth_rather_than_500(client, monkeypatch):
    """Two tabs, one acceptance, and no lie in either direction.

    ``_accepted_terms`` reads before it inserts, so under concurrency both
    requests can see "not accepted" and both can insert. ``uq_terms_acceptance``
    is what actually holds the line, and it fires on the loser. The row the
    loser wanted is on disk — the winner wrote it — so answering 500 would
    report failure for a click that succeeded.

    Simulated by inserting the winning row behind the endpoint's back after it
    has already read, which is exactly the interleaving the constraint sees.
    """
    _login(client, monkeypatch)
    from brnrd import ids
    from brnrd.routers import web_auth

    doc = terms.current(terms.DOC_TOS)
    real_accepted_terms = web_auth._accepted_terms

    def racing(db, account_id, kind):
        result = real_accepted_terms(db, account_id, kind)
        if result is None:
            # The other tab commits between our read and our insert.
            with client.app.state.SessionLocal() as other:
                other.add(
                    TermsAcceptance(
                        id=ids.terms_acceptance_id(),
                        account_id=account_id,
                        document=kind,
                        version=doc.version,
                        sha256=doc.sha256,
                    )
                )
                other.commit()
        return result

    monkeypatch.setattr(web_auth, "_accepted_terms", racing)
    assert _accept(client, terms.DOC_TOS).status_code == 200

    # One acceptance, not two, and not zero.
    assert len(_rows(client, terms.DOC_TOS)) == 1


def test_the_record_reproduces_the_document_it_recorded(client, monkeypatch):
    _login(client, monkeypatch)
    body = _accept(client, terms.DOC_TOS).json()

    (row,) = _rows(client, terms.DOC_TOS)
    assert row.version == terms.current(terms.DOC_TOS).version
    assert row.sha256 == body["sha256"]
    # The whole point of the change: from the stored hash alone, the exact
    # accepted text comes back.
    assert terms.text_for_sha256(row.sha256) == terms.current(terms.DOC_TOS).text
    assert "Terms of Service for brnrd.dev" in terms.text_for_sha256(row.sha256)


def test_a_version_bump_asks_again_and_keeps_the_earlier_acceptance(client, monkeypatch, bumped_tos):
    """The reason this is a table and not two more columns on ``Account``."""
    _login(client, monkeypatch)
    # Record acceptance of the *old* version directly — the state a real
    # account is already in when a new version is published under it, which
    # no endpoint can produce any more now that the new version is current.
    from brnrd import ids
    from brnrd.models import Account

    with client.app.state.SessionLocal() as db:
        account_id = db.execute(select(Account.id)).scalar_one()
        db.add(
            TermsAcceptance(
                id=ids.terms_acceptance_id(),
                account_id=account_id,
                document=terms.DOC_TOS,
                version="2026-07-24",
                sha256="e" * 64,
            )
        )
        db.commit()

    status = client.get("/v1/dashboard/terms-status").json()["documents"]["tos"]
    assert status["version"] == "2027-01-01"
    # A superseded acceptance does not answer "have you accepted what is on
    # the page today".
    assert status["needs_accept"] is True

    assert _accept(client, terms.DOC_TOS).status_code == 200
    rows = _rows(client, terms.DOC_TOS)
    assert [r.version for r in rows] == ["2026-07-24", "2027-01-01"]
    # The old row is untouched — it is the evidence of the contract that was
    # in force before the bump, and overwriting it would destroy it.
    assert rows[0].sha256 == "e" * 64
    assert terms.text_for_sha256(rows[1].sha256).endswith(
        "17. A clause that did not exist before.\n"
    )
    assert client.get("/v1/dashboard/terms-status").json()["documents"]["tos"]["needs_accept"] is False


def test_accepting_one_document_never_satisfies_the_other(client, monkeypatch):
    """#569, asserted rather than trusted to naming discipline."""
    _login(client, monkeypatch)
    assert _accept(client, terms.DOC_HOSTED).status_code == 200

    documents = client.get("/v1/dashboard/terms-status").json()["documents"]
    assert documents["hosted-execution"]["needs_accept"] is False
    assert documents["tos"]["needs_accept"] is True
    assert _rows(client, terms.DOC_TOS) == []
    # …and the login gate agrees with the status endpoint.
    assert _login(client, monkeypatch, next="/repos").headers["location"] == "/terms?next=/repos"


def test_accepting_twice_does_not_disturb_the_first_record(client, monkeypatch):
    _login(client, monkeypatch)
    assert _accept(client, terms.DOC_TOS).status_code == 200
    (first,) = _rows(client, terms.DOC_TOS)

    assert _accept(client, terms.DOC_TOS).status_code == 200
    rows = _rows(client, terms.DOC_TOS)
    assert len(rows) == 1
    assert rows[0].accepted_at == first.accepted_at


def test_an_unknown_document_is_refused_rather_than_recorded(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.post(
        "/v1/terms/accept",
        json={"document": "privacy", "accept_terms": "yes"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "privacy" in r.json()["notice"]
    assert _rows(client) == []


def test_an_unticked_box_records_nothing_for_the_general_terms(client, monkeypatch):
    _login(client, monkeypatch)
    r = client.post("/v1/terms/accept", json={"document": "tos"}, follow_redirects=False)
    assert r.status_code == 400
    assert r.json()["notice"] == "You need to accept the Terms of Service before continuing."
    assert _rows(client) == []


def test_accepting_the_general_terms_requires_a_session(client):
    r = client.post("/v1/terms/accept", json={"document": "tos", "accept_terms": "yes"})
    assert r.status_code == 401
    assert _rows(client) == []


def test_the_status_endpoint_publishes_the_hash_a_reader_can_check(client):
    """A user has to be able to verify the page equals what they will sign."""
    documents = client.get("/v1/dashboard/terms-status").json()["documents"]
    for kind, doc in documents.items():
        assert doc["sha256"] == terms.current(kind).sha256
        assert terms.text_for_sha256(doc["sha256"]) == terms.current(kind).text
        assert doc["accept_url"] == terms.current(kind).accept_path


def test_the_migration_never_manufactures_a_general_terms_acceptance():
    """Existing accounts have not accepted, and no INSERT may pretend they did.

    Asserted against the migration's own SQL because the alternative — a live
    Postgres — is not available in this suite, and the property is worth more
    than the fidelity: an acceptance row nobody clicked for is forged
    evidence, which is the defect #735 exists to remove rather than reproduce.
    """
    import inspect

    source = inspect.getsource(migrations._migrate_terms_acceptances)
    insert = source[source.index("INSERT INTO terms_acceptances") :]
    assert "'hosted-execution'" in insert, "the legacy column pair is what gets carried across"
    assert "'tos'" not in insert, "no general-ToS row may be backfilled, ever"
    # And what it does carry across carries no hash: that text was never
    # pinned, so claiming one would be worse than admitting the gap.
    assert "''," in insert


def test_a_legacy_acceptance_reports_its_text_as_unrecoverable():
    """The honest answer for a pre-pinning row, asserted as a contract."""
    assert terms.text_for_sha256("") is None
