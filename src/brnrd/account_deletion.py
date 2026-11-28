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

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from . import stripe_api
from .config import Settings
from .models import (
    Account,
    ActivityRecord,
    BoardConsent,
    ChannelRoute,
    ConfigChangeRequest,
    CreditBucket,
    Daemon,
    DaemonRepo,
    Event,
    TelegramHold,
    GitHubInstallation,
    GitHubInstalledRepo,
    LoomPart,
    PairRequest,
    Repo,
    RunReleaseRequest,
    RunRespawnRequest,
    RunStopRequest,
    RunnerWakeRequest,
    Subscription,
    TermsAcceptance,
    TgPairCode,
    Token,
    BillingLedgerEntry,
)

# Every row is reached through one of four id sets, and the order below is
# the foreign-key order: a child goes before the row it points at.
#
#   repos   <- tokens.repo_id, daemons.repo_id, events, activity_records,
#              loom_parts, daemon_repos, channel_routes, tg_pair_codes,
#              config_change_requests
#   tokens  <- daemons.token_id, activity_records.token_id
#   daemons <- loom_parts.daemon_id, daemon_repos.daemon_id,
#              activity_records.daemon_id
#   accounts <- everything with an account_id (the account row itself is
#              kept as a tombstone, see the module docstring)
#
# ``tests/test_brnrd_account_deletion.py`` walks ``Base.metadata`` and fails
# when a table gains a foreign key into this graph that the sweep does not
# name, so a new store cannot silently turn deletion back into a 500.
#
# Matches the Art 30 record §A/§B rows (ChannelRoute = router identity,
# Token = session tokens, Daemon/LoomPart/DaemonRepo = operational
# telemetry, Event = message relay, ActivityRecord = activity aggregation).
# The loom-envelope and pairing rows (ConfigChangeRequest, TgPairCode,
# PairRequest, Runner*/Run*Request, BoardConsent) are not enumerated there
# individually; that gap is the record's, not this sweep's.
_ACCOUNT_ID_REQUEST_MODELS = (
    RunnerWakeRequest,
    RunStopRequest,
    RunReleaseRequest,
    RunRespawnRequest,
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
    # Coordinate with Telegram overflow writers before deleting their rows.
    from .telegram_holds import lock_account

    lock_account(db, account_id)

    repo_ids = list(
        db.execute(select(Repo.id).where(Repo.account_id == account_id)).scalars()
    )
    owned_token = Token.account_id == account_id
    if repo_ids:
        owned_token = or_(owned_token, Token.repo_id.in_(repo_ids))
    token_ids = list(db.execute(select(Token.id).where(owned_token)).scalars())
    # A daemon is the account's by its own account_id (nullable, for rows
    # paired before connection identity moved to the account) or by the token
    # it authenticates with. Another account's daemon that merely names one
    # of these repos as its default routing repo keeps running; its pointer
    # is cleared below instead.
    owned_daemon = Daemon.account_id == account_id
    if token_ids:
        owned_daemon = or_(owned_daemon, Daemon.token_id.in_(token_ids))
    daemon_ids = list(db.execute(select(Daemon.id).where(owned_daemon)).scalars())

    def _delete_where_any(model, *clauses):
        live = [c for c in clauses if c is not None]
        if live:
            db.execute(delete(model).where(or_(*live)))

    def _in(column, values):
        return column.in_(values) if values else None

    # Children of daemons, tokens and repos.
    _delete_where_any(
        ActivityRecord,
        _in(ActivityRecord.repo_id, repo_ids),
        _in(ActivityRecord.token_id, token_ids),
        _in(ActivityRecord.daemon_id, daemon_ids),
    )
    db.execute(delete(TelegramHold).where(TelegramHold.account_id == account_id))
    if repo_ids:
        db.execute(delete(Event).where(Event.repo_id.in_(repo_ids)))
        db.execute(delete(BoardConsent).where(BoardConsent.repo_id.in_(repo_ids)))
    _delete_where_any(
        LoomPart,
        LoomPart.account_id == account_id,
        _in(LoomPart.daemon_id, daemon_ids),
        _in(LoomPart.repo_id, repo_ids),
    )
    _delete_where_any(
        DaemonRepo,
        _in(DaemonRepo.daemon_id, daemon_ids),
        _in(DaemonRepo.repo_id, repo_ids),
    )
    for model in (ConfigChangeRequest, ChannelRoute, TgPairCode, PairRequest):
        _delete_where_any(model, model.account_id == account_id, _in(model.repo_id, repo_ids))

    if daemon_ids:
        db.execute(delete(Daemon).where(Daemon.id.in_(daemon_ids)))
    if repo_ids:
        db.execute(update(Daemon).where(Daemon.repo_id.in_(repo_ids)).values(repo_id=None))

    # All tokens for this account — session, API-key, and daemon — not just
    # ones carrying a repo_id. issue_session_token() mints the dashboard's
    # own session cookie with repo_id=None, so a repo-scoped-only sweep
    # would leave the very session performing this deletion still valid.
    if token_ids:
        db.execute(delete(Token).where(Token.id.in_(token_ids)))

    for model in _ACCOUNT_ID_REQUEST_MODELS:
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

    # Terms acceptances (#735) are erased, not retained. They replace the
    # ``hosted_terms_*`` columns this function already cleared, so this keeps
    # erasure exactly as wide as it was rather than quietly narrowing it when
    # the fact moved to its own table. Note that this is a *choice*: an
    # acceptance record is arguably retainable under Art 17(3)(e) as evidence
    # for a legal claim, the way ``billing_ledger`` is. It is not retained
    # here because nothing has decided that it should be, and inventing a
    # retention basis inside a deletion path is the wrong place to decide it.
    db.execute(delete(TermsAcceptance).where(TermsAcceptance.account_id == account_id))

    db.execute(delete(Repo).where(Repo.account_id == account_id))

    now = _utcnow()
    tombstone_suffix = account_id.split("_", 1)[-1][:24]
    account.github_id = f"{_GITHUB_ID_TOMBSTONE_PREFIX}{tombstone_suffix}"
    account.github_login = ""
    account.email = None
    account.surface_json = "[]"
    account.surface_updated_at = now.replace(tzinfo=None)
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
