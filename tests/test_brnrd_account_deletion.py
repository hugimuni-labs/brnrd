"""Tests for the Art 17 account-deletion sweep (account_deletion.py +
``POST /v1/accounts/delete``).

Walks every account-keyed store named in ``docs/legal/art-30-record.md`` /
``docs/legal/dpa.md`` plus the operational rows those documents don't
individually enumerate (ConfigChangeRequest, TgPairCode, PairRequest,
RunnerWakeRequest, RunStopRequest), asserting each is empty for the
deleted account afterward — except ``BillingLedgerEntry``, asserted to
survive, and the ``Account`` row itself, asserted anonymized rather than
gone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import account_deletion, create_app, ids, stripe_api  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import (  # noqa: E402
    Account,
    ActivityRecord,
    BillingLedgerEntry,
    ChannelRoute,
    ConfigChangeRequest,
    CreditBucket,
    Daemon,
    Event,
    GitHubInstallation,
    GitHubInstalledRepo,
    PairRequest,
    Repo,
    RunStopRequest,
    RunnerWakeRequest,
    Subscription,
    TgPairCode,
    Token,
)
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.routers.accounts import account_for_github_identity, issue_session_token  # noqa: E402


def _client(**overrides) -> TestClient:
    kwargs = dict(
        database_url="sqlite:///:memory:",
        public_base_url="https://brnrd.example",
        stripe_api_key="sk_test_x",
    )
    kwargs.update(overrides)
    return TestClient(create_app(Settings(**kwargs)), base_url="https://testserver")


def _login(client: TestClient, *, github_id: str = "1", login: str = "octocat") -> tuple[str, str]:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id=github_id, login=login, email=f"{login}@example.com")
        )
        token = issue_session_token(db, account)
        account_id = account.id
    client.cookies.set(client.app.state.settings.session_cookie, token)
    return account_id, token


def _seed_everything(client: TestClient, account_id: str) -> str:
    """Populates one row in every account-keyed store, plus a repo-scoped
    daemon/activity/event trio, and returns the repo id."""
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    with client.app.state.SessionLocal() as db:
        repo = Repo(
            id=ids.repo_id(),
            account_id=account_id,
            repo_full_name="octocat/one",
            repo_owner="octocat",
            repo_name="one",
        )
        db.add(repo)
        db.flush()

        daemon_token = Token(
            id=ids.token_id(),
            account_id=account_id,
            repo_id=repo.id,
            kind=Token.KIND_DAEMON,
            token_hash="hash-daemon",
            label="daemon",
        )
        api_key_token = Token(
            id=ids.token_id(),
            account_id=account_id,
            kind=Token.KIND_API_KEY,
            token_hash="hash-api-key",
            label="api key",
        )
        db.add_all([daemon_token, api_key_token])
        db.flush()

        db.add(
            Daemon(
                id=ids.daemon_id(),
                account_id=account_id,
                repo_id=repo.id,
                token_id=daemon_token.id,
                daemon_name="daemon-1",
            )
        )
        db.add(
            ActivityRecord(
                id=ids.activity_id(),
                repo_id=repo.id,
                token_id=daemon_token.id,
                record_id="run:x",
            )
        )
        db.add(Event(event_id=ids.event_id(), repo_id=repo.id, body="hello"))
        db.add(
            ChannelRoute(
                id=ids.channel_route_id(),
                platform="telegram",
                channel_id="chan-1",
                account_id=account_id,
                repo_id=repo.id,
            )
        )
        db.add(
            TgPairCode(
                id=ids.tg_pair_code_id(),
                code=ids.tg_pair_code(),
                account_id=account_id,
                repo_id=repo.id,
                expires_at=expires,
            )
        )
        db.add(
            PairRequest(
                id=ids.pair_request_id(),
                pair_code=ids.pair_code(),
                poll_secret_hash="secret-hash",
                status=PairRequest.STATUS_CONSUMED,
                account_id=account_id,
                repo_id=repo.id,
                expires_at=expires,
            )
        )
        db.add(
            ConfigChangeRequest(
                id=ids.config_change_request_id(),
                account_id=account_id,
                repo_id=repo.id,
                proposal_id="prop-1",
                config_key="spawn.max_concurrent",
                expires_at=expires,
            )
        )
        db.add(
            RunnerWakeRequest(
                id=ids.runner_wake_request_id(),
                account_id=account_id,
                profile="claude-sonnet",
                expires_at=expires,
            )
        )
        db.add(
            RunStopRequest(
                id=ids.run_stop_request_id(),
                account_id=account_id,
                run_id="run-1",
                expires_at=expires,
            )
        )
        installation = GitHubInstallation(
            id=ids.github_installation_id(),
            account_id=account_id,
            installation_id="42",
            target_login="octocat",
            target_type="User",
        )
        db.add(installation)
        db.flush()
        db.add(
            GitHubInstalledRepo(
                id=ids.github_installed_repo_id(),
                github_installation_id=installation.id,
                repo_full_name="octocat/one",
            )
        )
        subscription = Subscription(
            id=ids.subscription_id(),
            account_id=account_id,
            stripe_subscription_id="sub_live_1",
            status=Subscription.STATUS_ACTIVE,
        )
        db.add(subscription)
        bucket = CreditBucket(
            id=ids.credit_bucket_id(),
            account_id=account_id,
            source=CreditBucket.SOURCE_PURCHASED,
            granted_credits=500,
            remaining_credits=500,
        )
        db.add(bucket)
        db.flush()
        db.add(
            BillingLedgerEntry(
                id=ids.billing_ledger_id(),
                account_id=account_id,
                op="topup",
                credits_delta=500,
                bucket_id=bucket.id,
            )
        )
        db.commit()
        return repo.id


def _counts(db, account_id: str, repo_id: str) -> dict[str, int]:
    from sqlalchemy import select

    return {
        "ActivityRecord": db.query(ActivityRecord).filter(ActivityRecord.repo_id == repo_id).count(),
        "Event": db.query(Event).filter(Event.repo_id == repo_id).count(),
        "ChannelRoute": db.query(ChannelRoute).filter(ChannelRoute.account_id == account_id).count(),
        "TgPairCode": db.query(TgPairCode).filter(TgPairCode.account_id == account_id).count(),
        "PairRequest": db.query(PairRequest).filter(PairRequest.account_id == account_id).count(),
        "ConfigChangeRequest": db.query(ConfigChangeRequest).filter(ConfigChangeRequest.account_id == account_id).count(),
        "RunnerWakeRequest": db.query(RunnerWakeRequest).filter(RunnerWakeRequest.account_id == account_id).count(),
        "RunStopRequest": db.query(RunStopRequest).filter(RunStopRequest.account_id == account_id).count(),
        "Daemon": db.query(Daemon).filter(Daemon.account_id == account_id).count(),
        "Token": db.query(Token).filter(Token.account_id == account_id).count(),
        "GitHubInstallation": db.query(GitHubInstallation).filter(GitHubInstallation.account_id == account_id).count(),
        "GitHubInstalledRepo": db.execute(
            select(GitHubInstalledRepo).where(GitHubInstalledRepo.repo_full_name == "octocat/one")
        ).scalars().all().__len__(),
        "Subscription": db.query(Subscription).filter(Subscription.account_id == account_id).count(),
        "CreditBucket": db.query(CreditBucket).filter(CreditBucket.account_id == account_id).count(),
        "Repo": db.query(Repo).filter(Repo.account_id == account_id).count(),
        "BillingLedgerEntry": db.query(BillingLedgerEntry).filter(BillingLedgerEntry.account_id == account_id).count(),
    }


def test_delete_account_sweeps_every_store_but_retains_the_ledger(monkeypatch):
    client = _client()
    account_id, token = _login(client)
    repo_id = _seed_everything(client, account_id)

    canceled_ids = []
    monkeypatch.setattr(
        "brnrd.account_deletion.stripe_api.cancel_subscription_now",
        lambda settings, *, subscription_id: canceled_ids.append(subscription_id),
    )

    r = client.post("/v1/accounts/delete", json={"confirm_login": "octocat"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["stripe_subscription_canceled"] is True
    assert canceled_ids == ["sub_live_1"]
    assert body["retained"] == [
        {
            "store": "billing_ledger",
            "reason": account_deletion._LEDGER_RETENTION_REASON,
        }
    ]

    with client.app.state.SessionLocal() as db:
        counts = _counts(db, account_id, repo_id)
        for store, count in counts.items():
            if store == "BillingLedgerEntry":
                assert count == 1, "the retained ledger must survive"
                continue
            assert count == 0, f"{store} still has {count} row(s) after deletion"

        account = db.get(Account, account_id)
        assert account is not None, "the account row is anonymized, not dropped"
        assert account.deleted_at is not None
        assert account.github_login == ""
        assert account.email is None
        assert account.stripe_customer_id is None
        assert account.surface_json == "[]"
        assert account.tier == Account.TIER_FREE
        assert not account.github_id.startswith("acc_")

        # The ledger's bucket_id pointed at a CreditBucket row that no
        # longer exists — it must be nulled, not left dangling.
        ledger_row = db.query(BillingLedgerEntry).filter(
            BillingLedgerEntry.account_id == account_id
        ).one()
        assert ledger_row.bucket_id is None

    # The session token used to *make* this request must not survive it —
    # the Token sweep has to catch tokens with no repo_id (issue_session_token
    # never sets one), not just repo-scoped ones. Bearer, not the cookie:
    # /v1/accounts/repos reads only Authorization (`require_account`), so this
    # actually proves the token row is gone rather than just that no cookie
    # was sent.
    denied = client.get("/v1/accounts/repos", headers={"Authorization": f"Bearer {token}"})
    assert denied.status_code == 401
    # And the cookie-based dashboard seam agrees.
    denied_cookie = client.get("/v1/dashboard/repos")
    assert denied_cookie.status_code == 401


def test_delete_account_rejects_a_wrong_confirmation_phrase():
    client = _client()
    account_id, _ = _login(client)
    _seed_everything(client, account_id)

    r = client.post("/v1/accounts/delete", json={"confirm_login": "not-octocat"})
    assert r.status_code == 400

    with client.app.state.SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account.deleted_at is None
        assert account.github_login == "octocat"


def test_delete_account_requires_credentials():
    client = _client()
    r = client.post("/v1/accounts/delete", json={"confirm_login": "whoever"})
    assert r.status_code == 401


def test_delete_account_aborts_on_stripe_failure_without_touching_rows(monkeypatch):
    """A Stripe cancellation failure must not leave someone deleted locally
    while still billed with no local record of the subscription."""
    client = _client()
    account_id, _ = _login(client)
    repo_id = _seed_everything(client, account_id)

    def _boom(settings, *, subscription_id):
        raise stripe_api.StripeError("stripe is down", status_code=502)

    monkeypatch.setattr("brnrd.account_deletion.stripe_api.cancel_subscription_now", _boom)

    r = client.post("/v1/accounts/delete", json={"confirm_login": "octocat"})
    assert r.status_code == 502

    with client.app.state.SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account.deleted_at is None
        counts = _counts(db, account_id, repo_id)
        assert counts["Repo"] == 1
        assert counts["Subscription"] == 1


def test_delete_account_core_respects_fk_order_under_enforcement(monkeypatch):
    """Same sweep, run directly against a foreign-keys-ON sqlite engine —
    sqlite's default off mode would hide a bad delete order the way it did
    for #53's grant_bucket ordering bug (see test_brnrd_billing.py)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from brnrd.config import Settings
    from brnrd.models import Base

    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    account = Account(id="acc_fkpin", github_id="fk-1", github_login="fkpin")
    db.add(account)
    db.commit()

    daemon_token = Token(
        id="tok_fk", account_id=account.id, kind=Token.KIND_DAEMON, token_hash="h1"
    )
    db.add(daemon_token)
    db.flush()
    db.add(
        Daemon(
            id="dmn_fk", account_id=account.id, token_id=daemon_token.id, daemon_name="d1"
        )
    )
    bucket = CreditBucket(
        id="bkt_fk", account_id=account.id, source="purchased", granted_credits=10, remaining_credits=10
    )
    db.add(bucket)
    db.flush()
    db.add(
        BillingLedgerEntry(
            id="blg_fk", account_id=account.id, op="topup", credits_delta=10, bucket_id=bucket.id
        )
    )
    db.commit()

    monkeypatch.setattr("brnrd.account_deletion.stripe_api.cancel_subscription_now", lambda *a, **k: None)
    receipt = account_deletion.delete_account(db, Settings(stripe_api_key="sk_test"), account)
    assert receipt.retained[0].store == "billing_ledger"
    assert db.query(Daemon).count() == 0
    assert db.query(Token).count() == 0
    assert db.query(CreditBucket).count() == 0
    assert db.query(BillingLedgerEntry).one().bucket_id is None
    db.close()
