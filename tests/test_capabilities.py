"""Tests for the capability registry (design-capability-panel.md build step 1).

The test this exists to make possible per the spec: a test that
parametrizes over the same list the implementation uses cannot catch the
list being wrong. Every "does the catalog have a row for id X" assertion
below reads the ids from `CAPABILITY_CATALOG` itself (the same list
`evaluate_capabilities` walks) — the catalog's *own* sanity assertion
(non-empty, >=1 row per scope) is the independent check that keeps a
future rename from silently turning that into a no-op over an empty set.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.capabilities import (  # noqa: E402
    CAPABILITY_CATALOG,
    SCOPES,
    STATE_DARK,
    STATE_LIT,
    STATE_UNOBSERVABLE,
    STATE_WAITING,
    CapabilityCatalogError,
    CapabilityCycleError,
    CapabilityDef,
    evaluate_capabilities,
    validate_catalog,
)
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, Daemon, Token  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.routers.accounts import account_for_github_identity, issue_session_token  # noqa: E402

from _helpers import PUBLISH_EVERYTHING  # noqa: E402


def _client(**overrides) -> TestClient:
    kwargs = dict(
        database_url="sqlite:///:memory:",
        public_base_url="https://brnrd.example",
        github_oauth_client_id="gh-client",
        github_oauth_client_secret="gh-secret",
    )
    kwargs.update(overrides)
    app = create_app(Settings(**kwargs))
    return TestClient(app, base_url="https://testserver")


def _login(client: TestClient, *, github_id: str = "1", login: str = "Gurio") -> str:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id=github_id, login=login, email=None)
        )
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)
    return token


def _create_repo(client: TestClient, token: str, repo: str = "Gurio/brr") -> str:
    r = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": repo, "default_branch": "main", "publish_layers": PUBLISH_EVERYTHING},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["repo_id"]


def _account_id(client: TestClient, login: str = "Gurio") -> str:
    with client.app.state.SessionLocal() as db:
        return db.query(Account).filter(Account.github_login == login).one().id


def _evaluate(client: TestClient, account_id: str):
    with client.app.state.SessionLocal() as db:
        account = db.get(Account, account_id)
        return evaluate_capabilities(db, account, client.app.state.settings)


# --------------------------------------------------------------------------
# Catalog completeness
# --------------------------------------------------------------------------


def test_catalog_is_non_empty_with_at_least_one_row_per_scope():
    """The independent sanity assertion the spec asks for, so a rename that
    empties the catalog can't silently pass the coverage test below."""
    assert len(CAPABILITY_CATALOG) > 0
    for scope in SCOPES:
        assert any(c.scope == scope for c in CAPABILITY_CATALOG), scope


def test_every_catalog_id_appears_for_an_account_with_repos_and_a_daemon():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token, repo="Gurio/brr")
    _create_repo(client, token, repo="Gurio/second")
    account_id = _account_id(client)
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id="dmn-cov",
                account_id=account_id,
                repo_id=repo_id,
                token_id="tok-cov",
                daemon_name="laptop",
            )
        )
        db.commit()

    caps = _evaluate(client, account_id)
    seen_ids = {c.id for c in caps}
    assert seen_ids == {c.id for c in CAPABILITY_CATALOG}


# --------------------------------------------------------------------------
# Frontier invariant
# --------------------------------------------------------------------------


def test_frontier_is_never_true_off_a_dark_state():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    account_id = _account_id(client)
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(id="dmn-frontier", account_id=account_id, repo_id=repo_id, token_id="tok-frontier", daemon_name="laptop")
        )
        db.commit()

    caps = _evaluate(client, account_id)
    for cap in caps:
        if cap.state != STATE_DARK:
            assert cap.frontier is False, (cap.id, cap.subject, cap.state)


# --------------------------------------------------------------------------
# Waiting: transitive, and never claimed by a detector directly
# --------------------------------------------------------------------------


def test_waiting_is_transitive_through_a_revoked_pairing():
    """machine-paired -> daemon-live -> runner-available -> runner-quota is a
    four-deep same-subject chain. Revoking the daemon's token darkens
    machine-paired; everything downstream must read `waiting`, not its own
    raw `dark`/`lit` reading, and the darkened link itself is the one
    `frontier` row in the chain."""
    # `waiting` only ever overrides a *raw* `dark` reading (§ design doc:
    # "a prerequisite is dark, so this one is not actionable yet" —
    # presupposing the row would otherwise present as a task). A row that is
    # independently observed `lit` never needs suppressing, so every
    # downstream fixture below is built to read raw-`dark` on its own
    # detector, not merely stale/absent data (which would read
    # `unobservable` and, correctly, stay there regardless of the
    # prerequisite chain).
    from datetime import datetime, timedelta, timezone

    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    account_id = _account_id(client)
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    with client.app.state.SessionLocal() as db:
        db.add(Token(id="tok-revoked", account_id=account_id, kind=Token.KIND_DAEMON, token_hash="h", revoked=True))
        db.add(
            Daemon(
                id="dmn-revoked",
                account_id=account_id,
                repo_id=repo_id,
                token_id="tok-revoked",
                daemon_name="laptop",
                online=False,
                last_seen_at=stale,
                runners_json='[{"name": "claude", "available": false}]',
                runners_updated_at=stale,
                quota_json='[{"shell": "claude", "windows": [{"percent": 0.5}]}]',
                quota_updated_at=stale,
            )
        )
        db.commit()

    caps = {(c.id, c.subject): c for c in _evaluate(client, account_id)}
    machine_paired = caps[("machine-paired", "dmn-revoked")]
    assert machine_paired.state == STATE_DARK
    assert machine_paired.frontier is True
    for downstream_id in ("daemon-live", "runner-available", "runner-quota"):
        cap = caps[(downstream_id, "dmn-revoked")]
        assert cap.state == STATE_WAITING, (downstream_id, cap.state)
        assert cap.frontier is False


def test_no_detector_ever_returns_waiting_directly():
    """`waiting` is the evaluator's own override, never a raw detector
    reading — enforced here on a healthy account (nothing should be waiting
    on a freshly connected repo with no daemon at all, since every repo-scope
    `requires` in the v1 catalog points at `repo-enabled`, which is lit by
    construction whenever the row exists)."""
    client = _client()
    token = _login(client)
    _create_repo(client, token)
    account_id = _account_id(client)
    caps = _evaluate(client, account_id)
    assert not any(c.state == STATE_WAITING for c in caps)


# --------------------------------------------------------------------------
# Optional never blocks — enforced structurally
# --------------------------------------------------------------------------


def test_optional_capability_is_never_a_requires_target_in_the_real_catalog():
    optional_ids = {c.id for c in CAPABILITY_CATALOG if c.heat == "optional"}
    for cdef in CAPABILITY_CATALOG:
        assert not (set(cdef.requires) & optional_ids), cdef.id


def test_validate_catalog_raises_if_something_required_an_optional_id():
    broken = (
        CapabilityDef("base", "account", "optional"),
        CapabilityDef("dependent", "account", "required", requires=("base",)),
    )
    with pytest.raises(CapabilityCatalogError):
        validate_catalog(broken)


# --------------------------------------------------------------------------
# Cycle detection
# --------------------------------------------------------------------------


def test_validate_catalog_raises_on_a_requires_cycle():
    broken = (
        CapabilityDef("a", "account", "required", requires=("b",)),
        CapabilityDef("b", "account", "required", requires=("a",)),
    )
    with pytest.raises(CapabilityCycleError):
        validate_catalog(broken)


def test_validate_catalog_raises_on_a_self_cycle():
    broken = (CapabilityDef("a", "account", "required", requires=("a",)),)
    with pytest.raises(CapabilityCycleError):
        validate_catalog(broken)


def test_validate_catalog_accepts_the_real_catalog():
    order = validate_catalog(CAPABILITY_CATALOG)
    assert set(order) == {c.id for c in CAPABILITY_CATALOG}


# --------------------------------------------------------------------------
# Unobservable vs dark — the whole point of the fourth state
# --------------------------------------------------------------------------


def test_cli_installed_is_unobservable_not_dark_with_no_daemon_ever():
    client = _client()
    token = _login(client)
    _create_repo(client, token)
    account_id = _account_id(client)

    caps = _evaluate(client, account_id)
    cli = [c for c in caps if c.id == "cli-installed"]
    assert len(cli) == 1
    assert cli[0].state == STATE_UNOBSERVABLE
    assert cli[0].subject is None
    assert cli[0].evidence.source == "none"
    # Per-daemon machine-scope ids simply don't appear yet — there is no
    # daemon subject to attach them to, which is distinct from claiming
    # they are "dark".
    assert not any(c.scope == "machine" and c.id != "cli-installed" for c in caps)


def test_repo_initialised_is_always_unobservable():
    """#874-adjacent honesty constraint from the spec: no detector reaches
    this fact today, regardless of how paired/lit everything else is."""
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    account_id = _account_id(client)
    with client.app.state.SessionLocal() as db:
        db.add(Daemon(id="dmn-init", account_id=account_id, repo_id=repo_id, token_id="tok-init", daemon_name="laptop"))
        db.commit()

    caps = _evaluate(client, account_id)
    repo_init = [c for c in caps if c.id == "repo-initialised"]
    assert repo_init and all(c.state == STATE_UNOBSERVABLE for c in repo_init)


def test_bot_collaborator_unknown_is_unobservable_never_checked_yet():
    client = _client()
    token = _login(client)
    _create_repo(client, token)
    account_id = _account_id(client)
    caps = _evaluate(client, account_id)
    bot = next(c for c in caps if c.id == "bot-collaborator")
    assert bot.state == STATE_UNOBSERVABLE
    assert bot.heat == "optional"


# --------------------------------------------------------------------------
# publish-scope: unrecorded (null) is dark; explicit "none" is lit
# --------------------------------------------------------------------------


def test_publish_scope_null_is_dark_explicit_none_is_lit():
    """`None` (never recorded — a repo connected before the consent column
    existed) is the frontier-eligible `dark`; an explicit "none" (all lanes
    off, but *stated*) is `lit` — the design's own distinction, since a
    boolean 'is publish_layers falsy' would conflate the two."""
    client = _client()
    _login(client)
    account_id = _account_id(client)
    with client.app.state.SessionLocal() as db:
        from brnrd.ids import repo_id as new_repo_id
        from brnrd.models import Repo

        db.add(
            Repo(
                id=new_repo_id(),
                account_id=account_id,
                forge="github",
                repo_full_name="Gurio/legacy",
                repo_owner="Gurio",
                repo_name="legacy",
                publish_layers=None,
            )
        )
        db.commit()

    caps = _evaluate(client, account_id)
    publish_scope_cap = next(c for c in caps if c.id == "publish-scope")
    assert publish_scope_cap.state == STATE_DARK

    with client.app.state.SessionLocal() as db:
        from brnrd import publish_scope as ps
        from brnrd.models import Repo

        repo = db.query(Repo).filter(Repo.repo_full_name == "Gurio/legacy").one()
        repo.publish_layers = ps.normalize_publish_layers("none")
        db.commit()

    caps = _evaluate(client, account_id)
    publish_scope_cap = next(c for c in caps if c.id == "publish-scope")
    assert publish_scope_cap.state == STATE_LIT


# --------------------------------------------------------------------------
# channel-bound: platform-general, not Telegram-only
# (brr/the-directory-reaches-the-wire)
# --------------------------------------------------------------------------


def test_channel_bound_lights_on_a_paired_non_telegram_route():
    """The old `_Context.build` read filtered `ChannelRoute.platform ==
    "telegram"`, so a repo reachable only over a paired WhatsApp route read
    `channel-bound: dark` forever — the same narrowing bug fixed in
    `_session._telegram_paired_repo_ids` (now `_paired_channels_by_repo`).
    `channel-bound`'s own detector asks "can this repo be reached by chat at
    all" — nothing about its id, evidence, or wire shape
    (`Capability.to_wire` carries no platform) is Telegram-specific, so a
    paired WhatsApp route must light it exactly like a paired Telegram one
    would."""
    from brnrd.models import ChannelRoute

    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    account_id = _account_id(client)

    with client.app.state.SessionLocal() as db:
        db.add(
            ChannelRoute(
                id="cr-wa-bound",
                platform="whatsapp",
                channel_id="wa-chat-1",
                account_id=account_id,
                repo_id=repo_id,
                paired_user_id=999,
            )
        )
        db.commit()

    caps = {(c.id, c.subject): c for c in _evaluate(client, account_id)}
    channel_bound = caps[("channel-bound", repo_id)]
    assert channel_bound.state == STATE_LIT


def test_channel_bound_stays_dark_for_a_null_principal_non_telegram_route():
    """#885 generalised: a `ChannelRoute` with no principal authorizes
    nobody regardless of platform, so it must not light `channel-bound`
    either — same rule as the paired case above, opposite fixture."""
    from brnrd.models import ChannelRoute

    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    account_id = _account_id(client)

    with client.app.state.SessionLocal() as db:
        db.add(
            ChannelRoute(
                id="cr-wa-null-principal",
                platform="whatsapp",
                channel_id="wa-chat-2",
                account_id=account_id,
                repo_id=repo_id,
                paired_user_id=None,
            )
        )
        db.commit()

    caps = {(c.id, c.subject): c for c in _evaluate(client, account_id)}
    channel_bound = caps[("channel-bound", repo_id)]
    assert channel_bound.state == STATE_DARK


# --------------------------------------------------------------------------
# Wire shape on the real endpoint
# --------------------------------------------------------------------------


def test_dashboard_repos_api_carries_a_flat_capabilities_array():
    client = _client()
    token = _login(client)
    _create_repo(client, token)

    body = client.get("/v1/dashboard/repos").json()
    caps = body["capabilities"]
    assert isinstance(caps, list) and caps
    for row in caps:
        assert set(row) == {"id", "scope", "subject", "state", "evidence", "requires", "heat", "act", "frontier"}
        assert set(row["evidence"]) == {"source", "as_of"}
        assert set(row["act"]) == {"kind", "target"}
    # Flat, not grouped: every row already carries its own scope/subject.
    assert not any(isinstance(v, dict) and "capabilities" in v for v in body.values() if isinstance(v, dict))
