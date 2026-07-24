"""Article 17 GDPR erasure — the account-deletion sweep.

Source of truth for *what* gets deleted is ``docs/legal/art-30-record.md``
and ``docs/legal/dpa.md`` Annex I/II, not this module's own judgement —
every store this file touches is walked against those two documents (see
the "matches Art 30" comments below); a store neither document names is a
drafting gap this module surfaces rather than silently invents behaviour
for.

Two exceptions to "delete everything account-keyed":

- ``BillingLedgerEntry`` (the append-only billing audit ledger) is
  **retained**. #704 cut the DPA's earlier fake-retention claims for it —
  its statutory retention period is, in the Art 30 record's own words,
  "to be confirmed with counsel" (``art-30-record.md`` §A "Billing and
  payment"), which this module's response mirrors rather than re-deriving
  a citation neither legal document actually makes.
- ``Account`` itself is **anonymized in place, not dropped**. The ledger's
  ``account_id`` is a ``NOT NULL`` foreign key with no ``ON DELETE``
  clause (``models.py`` — no Alembic, no cascade), so the parent row has
  to keep existing for that FK to stay valid after the ledger survives.
  Every PII-bearing column on the row is cleared instead, and
  ``deleted_at`` marks the tombstone.

Stripe: any live subscription is canceled **immediately** (not
"at period end" — deletion means access stops now), before any local
rows are touched, so a Stripe-side failure aborts the whole request
rather than leaving someone deleted-but-still-billed with no local
record of why. Stripe's own copy of the customer/invoice history is its
own retention, not ours to delete — same convention Annex II already
uses for GitHub's installation-token expiry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from . import stripe_api
from .config import Settings
from .models import (
    Account,
    ActivityRecord,
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
    BillingLedgerEntry,
)

# Every store carrying its own ``account_id`` column (matches Art 30 record
# §A/§B rows: Router identity routing = ChannelRoute, Session tokens =
# Token, Operational telemetry = Daemon; ConfigChangeRequest/TgPairCode/
# PairRequest/RunnerWakeRequest/RunStopRequest are the loom-envelope /
# pairing rows the Art 30 record does not enumerate individually — a real
# gap between the record and the schema, flagged in the PR rather than
# left to grep). Deleted directly by account_id: none of these require
# walking the account's repos first.
#
# Order matters for FK safety: Daemon.token_id is a NOT NULL FK to
# tokens.id, so Daemon must go before Token.
_ACCOUNT_ID_MODELS_BEFORE_TOKEN = (
    ConfigChangeRequest,
    ChannelRoute,
    TgPairCode,
    PairRequest,
    Daemon,
)
_ACCOUNT_ID_MODELS_AFTER_TOKEN = (
    RunnerWakeRequest,
    RunStopRequest,
)

# The tombstone value fits accounts.github_id's VARCHAR(32) exactly:
# "deleted-" (8) + the account id's 24-hex-char suffix (account_id() mints
# "acc_" + token_hex(12)) = 32.
_GITHUB_ID_TOMBSTONE_PREFIX = "deleted-"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RetainedStore:
    store: str
    reason: str


@dataclass(frozen=True)
class DeletionReceipt:
    account_id: str
    deleted_at: datetime
    stripe_subscription_canceled: bool
    retained: list[RetainedStore] = field(default_factory=list)


class ConfirmationMismatch(ValueError):
    """The caller's re-typed confirmation phrase didn't match."""


# Mirrors art-30-record.md §A "Billing and payment" row's own hedge —
# "Statutory retention to be confirmed with counsel" — rather than
# asserting a citation neither the DPA nor the Art 30 record makes.
_LEDGER_RETENTION_REASON = (
    "the append-only billing ledger (invoices, subscription and refund "
    "events) is retained for accounting purposes; its statutory retention "
    "period is to be confirmed with counsel (see the Art 30 record, "
    "§A “Billing and payment”). Stripe separately retains its own "
    "copy of your customer and payment records under Stripe's own "
    "retention policy."
)


def confirm_login_matches(account: Account, confirm_login: str) -> None:
    if confirm_login.strip() != account.github_login:
        raise ConfirmationMismatch(
            "confirmation phrase did not match this account's GitHub login"
        )


def _cancel_live_subscriptions(db: Session, settings: Settings, account_id: str) -> bool:
    """Cancel every non-canceled Stripe subscription for this account, now.

    Runs before any row is touched: a Stripe-side failure raises
    ``stripe_api.StripeError`` and the caller aborts before mutating the
    database, so a failed cancellation never leaves an account deleted
    locally while still billing in Stripe with no local record of it.
    """
    live = list(
        db.execute(
            select(Subscription).where(
                Subscription.account_id == account_id,
                Subscription.status != Subscription.STATUS_CANCELED,
            )
        ).scalars()
    )
    for subscription in live:
        stripe_api.cancel_subscription_now(
            settings, subscription_id=subscription.stripe_subscription_id
        )
    return bool(live)


def delete_account(db: Session, settings: Settings, account: Account) -> DeletionReceipt:
    """Erase ``account`` per Art 17. Caller owns the confirmation check
    (``confirm_login_matches``) and the commit boundary is this
    function's own — it commits once, at the end, after every delete has
    been staged."""
    account_id = account.id

    canceled = _cancel_live_subscriptions(db, settings, account_id)

    repo_ids = list(
        db.execute(select(Repo.id).where(Repo.account_id == account_id)).scalars()
    )
    if repo_ids:
        # Repo-content stores with no account_id column of their own
        # (Art 30 record §B "Message relay" = Event, "Activity record
        # aggregation" = ActivityRecord).
        db.execute(delete(ActivityRecord).where(ActivityRecord.repo_id.in_(repo_ids)))
        db.execute(delete(Event).where(Event.repo_id.in_(repo_ids)))

    for model in _ACCOUNT_ID_MODELS_BEFORE_TOKEN:
        db.execute(delete(model).where(model.account_id == account_id))

    # All tokens for this account — session, API-key, and daemon — not just
    # ones carrying a repo_id. issue_session_token() mints the dashboard's
    # own session cookie with repo_id=None, so a repo-scoped-only sweep
    # would leave the very session performing this deletion still valid.
    db.execute(delete(Token).where(Token.account_id == account_id))

    for model in _ACCOUNT_ID_MODELS_AFTER_TOKEN:
        db.execute(delete(model).where(model.account_id == account_id))

    installation_ids = list(
        db.execute(
            select(GitHubInstallation.id).where(GitHubInstallation.account_id == account_id)
        ).scalars()
    )
    if installation_ids:
        db.execute(
            delete(GitHubInstalledRepo).where(
                GitHubInstalledRepo.github_installation_id.in_(installation_ids)
            )
        )
    db.execute(delete(GitHubInstallation).where(GitHubInstallation.account_id == account_id))

    # CreditBucket is drained wallet state, not the retained audit trail —
    # only BillingLedgerEntry is. Its bucket_id FK is nullable, so null the
    # ledger's references before dropping the buckets they point at.
    bucket_ids = list(
        db.execute(
            select(CreditBucket.id).where(CreditBucket.account_id == account_id)
        ).scalars()
    )
    if bucket_ids:
        db.execute(
            update(BillingLedgerEntry)
            .where(BillingLedgerEntry.bucket_id.in_(bucket_ids))
            .values(bucket_id=None)
        )
        db.execute(delete(CreditBucket).where(CreditBucket.account_id == account_id))

    db.execute(delete(Subscription).where(Subscription.account_id == account_id))

    db.execute(delete(Repo).where(Repo.account_id == account_id))

    now = _utcnow()
    tombstone_suffix = account_id.split("_", 1)[-1][:24]
    account.github_id = f"{_GITHUB_ID_TOMBSTONE_PREFIX}{tombstone_suffix}"
    account.github_login = ""
    account.email = None
    account.surface_json = "[]"
    account.surface_updated_at = now.replace(tzinfo=None)
    account.hosted_terms_accepted_at = None
    account.hosted_terms_version = ""
    account.tier = Account.TIER_FREE
    account.stripe_customer_id = None
    account.deleted_at = now.replace(tzinfo=None)

    db.commit()

    return DeletionReceipt(
        account_id=account_id,
        deleted_at=now,
        stripe_subscription_canceled=canceled,
        retained=[RetainedStore(store="billing_ledger", reason=_LEDGER_RETENTION_REASON)],
    )
