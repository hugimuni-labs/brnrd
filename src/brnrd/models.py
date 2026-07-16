from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    github_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    github_login: Mapped[str] = mapped_column(String(255), index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    hosted_terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hosted_terms_version: Mapped[str] = mapped_column(String(32), default="")
    repos: Mapped[list["Repo"]] = relationship(back_populates="account")
    # Current Planned State (CPS) — account-level slices of the account
    # dominion's CS5/CS7 files (cross-repo plan + decision ledger); see
    # kb/plan-brnrd-dashboard-mvp.md "Gap: Current Planned State view".
    cross_repo_plan_md: Mapped[str] = mapped_column(Text, default="")
    decision_ledger_md: Mapped[str] = mapped_column(Text, default="")
    # CS8 — workflow preferences (account-dominion workflow.md), the
    # user↔resident pace-and-flow contract, rendered on the dashboard.
    workflow_md: Mapped[str] = mapped_column(Text, default="")
    plans_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Billing (#53, kb design-billing.md). ``tier`` flips only from Stripe
    # webhook state transitions; the Stripe subscription is source of truth.
    TIER_FREE = "free"
    TIER_SUBSCRIBED = "subscribed"
    tier: Mapped[str] = mapped_column(String(32), default=TIER_FREE)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = (UniqueConstraint("account_id", "repo_full_name", name="uq_repo_name"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    forge: Mapped[str] = mapped_column(String(32), default="github")
    repo_full_name: Mapped[str] = mapped_column(String(255), index=True)
    repo_owner: Mapped[str] = mapped_column(String(255), index=True)
    repo_name: Mapped[str] = mapped_column(String(255), index=True)
    forge_repo_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    default_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    account: Mapped["Account"] = relationship(back_populates="repos")
    # CPS — this repo's inter-run plan (CS5 `plans/<repo-slug>/active.md`),
    # mirrored from the account dominion via `PUT /v1/daemons/plans`.
    plan_md: Mapped[str] = mapped_column(Text, default="")
    plan_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Token(Base):
    __tablename__ = "tokens"
    KIND_API_KEY = "account_api_key"
    KIND_SESSION = "session"
    KIND_DAEMON = "daemon"
    ACCOUNT_KINDS = (KIND_API_KEY, KIND_SESSION)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    repo_id: Mapped[str | None] = mapped_column(ForeignKey("repos.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Daemon(Base):
    __tablename__ = "daemons"
    __table_args__ = (UniqueConstraint("repo_id", "daemon_name", name="uq_daemon_name"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repos.id"), index=True)
    token_id: Mapped[str] = mapped_column(ForeignKey("tokens.id"))
    daemon_name: Mapped[str] = mapped_column(String(128))
    capabilities: Mapped[str] = mapped_column(Text, default="")
    online: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # Runner-quota snapshot (5h/weekly windows per local Shell), mirrored from
    # this daemon's own `.brr/` cache via `PUT /v1/daemons/quota` — the
    # dashboard-side half of #237; see kb/design-dashboard-live-surface.md.
    quota_json: Mapped[str] = mapped_column(Text, default="[]")
    quota_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Per-ingestion-path liveness (#360), piggybacked on the quota publish.
    # Each row carries the source poll timestamp so a quiet gate remains
    # distinguishable from a dead one.
    gate_health_json: Mapped[str] = mapped_column(Text, default="[]")
    # Live/coexisting-runs snapshot (#258), mirrored from the local presence
    # registry via `PUT /v1/daemons/live-runs` — see
    # kb/design-dashboard-live-surface.md §"Reconsidered 2026-07-06".
    live_runs_json: Mapped[str] = mapped_column(Text, default="[]")
    live_runs_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Configured `spawn:` pool width (`spawn.max_concurrent`), piggybacked on
    # the same live-runs publish tick — the loom-envelope Phase 1 "limits"
    # panel's one piece of data slice 1 didn't already emit (the *active*
    # count is just `is_subspawn` runs in live_runs_json above).
    # kb/design-multi-workstream-concurrency.md §"Loom envelope".
    spawn_max_concurrent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # PR-review queue snapshot (#259), mirrored from `gh pr list` via
    # `PUT /v1/daemons/pr-review-queue`. Calendar age, not runner quota, is
    # the meaningful clock for this lane.
    pr_review_queue_json: Mapped[str] = mapped_column(Text, default="[]")
    pr_review_queue_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Closed-run cost ledger snapshot (#271), mirrored from local
    # `.brr/run-ledger.jsonl` rows via `PUT /v1/daemons/run-ledger`.
    run_ledger_json: Mapped[str] = mapped_column(Text, default="[]")
    run_ledger_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Runner-catalog snapshot (#328 spool rack): the locally-discovered
    # Shell+Core profiles this daemon can dispatch, plus its current default
    # pin, mirrored via `PUT /v1/daemons/runners`. Discovery is daemon-owned
    # and network-free (`src/brr/runner.py::available_runner_catalog`).
    runners_json: Mapped[str] = mapped_column(Text, default="[]")
    runners_default: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runners_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActivityRecord(Base):
    __tablename__ = "activity_records"
    __table_args__ = (
        UniqueConstraint(
            "repo_id", "token_id", "record_id",
            name="uq_activity_repo_token_record",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repos.id"), index=True)
    token_id: Mapped[str] = mapped_column(ForeignKey("tokens.id"), index=True)
    daemon_id: Mapped[str | None] = mapped_column(ForeignKey("daemons.id"), nullable=True, index=True)
    record_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="run", index=True)
    source: Mapped[str] = mapped_column(String(32), default="")
    conversation_key: Mapped[str] = mapped_column(String(255), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    runner_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="")
    phase: Mapped[str] = mapped_column(String(64), default="")
    branch: Mapped[str] = mapped_column(String(255), default="")
    pr_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    defer_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    links_json: Mapped[str] = mapped_column(Text, default="{}")
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Event(Base):
    __tablename__ = "events"
    STATUS_QUEUED = "queued"
    STATUS_RESPONDED = "responded"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repos.id"), index=True)
    runtime_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="dev")
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_to: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default=STATUS_QUEUED)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    response_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_len: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChannelRoute(Base):
    __tablename__ = "channel_routes"
    __table_args__ = (UniqueConstraint("platform", "channel_id", "topic_id", name="uq_channel_route"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), default="telegram")
    channel_id: Mapped[str] = mapped_column(String(64), index=True)
    topic_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repos.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # #409 — the Telegram user id who paired this chat/topic via `/start`.
    # The sole authorization principal for enqueueing a run from this route
    # (see routers/webhooks.py `_authorized`); nullable only because rows
    # created before the security fix landed predate the column — a route
    # with no principal authorizes nobody (default-closed), so those chats
    # must be re-paired.
    paired_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class GitHubInstallation(Base):
    __tablename__ = "github_installations"
    __table_args__ = (UniqueConstraint("installation_id", name="uq_github_installation"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    installation_id: Mapped[str] = mapped_column(String(64), index=True)
    target_login: Mapped[str] = mapped_column(String(255), default="", index=True)
    target_type: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GitHubInstalledRepo(Base):
    __tablename__ = "github_installed_repos"
    __table_args__ = (UniqueConstraint("github_installation_id", "repo_full_name", name="uq_github_installed_repo"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    github_installation_id: Mapped[str] = mapped_column(ForeignKey("github_installations.id"), index=True)
    repo_full_name: Mapped[str] = mapped_column(String(255), index=True)
    forge_repo_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    default_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_pushed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    github_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class TgPairCode(Base):
    __tablename__ = "tg_pair_codes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    repo_id: Mapped[str] = mapped_column(ForeignKey("repos.id"))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class PairRequest(Base):
    __tablename__ = "pair_requests"
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_CONSUMED = "consumed"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pair_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    poll_secret_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING)
    account_id: Mapped[str | None] = mapped_column(nullable=True)
    repo_id: Mapped[str | None] = mapped_column(nullable=True)
    minted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class ConfigChangeRequest(Base):
    """Loom-envelope Phase 2 — a daemon-proposed ``.brr/config`` key change,
    parked until the account owner approves it from a browser.

    Same device-flow shape as ``PairRequest`` above (mint -> approve-page
    click while logged in -> outcome observed), but daemon-initiated rather
    than account-initiated, and carrying a structured key/value instead of
    a repo binding. See ``kb/design-multi-workstream-concurrency.md``
    §"Named forks - round 2" for why this exists: the agent asking for more
    of a user-tunable ceiling than it currently has must never apply the
    change itself or accept a chat-typed approval - it has to ride the same
    auth boundary as everything else that touches account state.
    """

    __tablename__ = "config_change_requests"
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_EXPIRED = "expired"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repos.id"), index=True)
    # The daemon's own local proposal id (``account.config_change_proposals_path``
    # filename stem) - the join key between this server-side row and the
    # local proposal file the daemon applies against on approval.
    proposal_id: Mapped[str] = mapped_column(String(96), index=True)
    config_key: Mapped[str] = mapped_column(String(128))
    current_value: Mapped[str] = mapped_column(String(256), default="")
    requested_value: Mapped[str] = mapped_column(String(256), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class RunnerWakeRequest(Base):
    """#328 spool-rack tap — "next wake on this Shell+Core, please".

    The inversion of ``ConfigChangeRequest``: browser-initiated (the account
    owner taps a rack row while logged in), daemon-consumed. Deliberately a
    *one-shot* request — the next dispatched wake runs on the requested
    profile and the request is spent; a durable default change stays on the
    conversational config-change path. No approve step: the tapper is the
    account owner approving their own ask, and a second confirm would be
    ceremony (thread decision, 2026-07-11).

    Delivery rides the existing catalog publish tick: the daemon's
    ``PUT /v1/daemons/runners`` response carries the account's pending
    request, and the daemon acks consumption in its next PUT payload — no
    new polling loop, the same piggyback economics as the config-approval
    flow riding the inbox long-poll. Cancelable (chip tap) until a wake
    actually consumes it; cancellation propagates within one tick.
    """

    __tablename__ = "runner_wake_requests"
    STATUS_PENDING = "pending"
    STATUS_CONSUMED = "consumed"
    STATUS_CANCELED = "canceled"
    STATUS_EXPIRED = "expired"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    # Profile name as published in the rack (`RunnerProfileIn.name`).
    profile: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Subscription(Base):
    """#53 — local mirror of the account's Stripe subscription.

    One row per Stripe subscription id; at most one non-canceled row per
    account in practice. ``cohort`` pins which price pair the subscriber
    signed up on (supporter vs public) — grandfathering is Stripe-native
    (existing subscriptions keep their ``Price``), this column just makes
    the cohort countable without a Stripe API call.
    """

    __tablename__ = "subscriptions"
    STATUS_ACTIVE = "active"
    STATUS_PAST_DUE = "past_due"
    STATUS_CANCELED = "canceled"
    COHORT_SUPPORTER = "supporter"
    COHORT_PUBLIC = "public"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    stripe_subscription_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    stripe_price_id: Mapped[str] = mapped_column(String(64), default="")
    cohort: Mapped[str] = mapped_column(String(16), default=COHORT_SUPPORTER)
    cadence: Mapped[str] = mapped_column(String(16), default="monthly")  # monthly | annual
    status: Mapped[str] = mapped_column(String(16), default=STATUS_ACTIVE, index=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class CreditBucket(Base):
    """#53/#54 — one wallet grant/purchase, drained in place.

    The bucketed-ledger contract from kb design-billing.md §"Credit buckets
    and expiry policy": append-only rows, ``remaining_credits`` drained by
    debits (#54's machinery), ``stripe_ref`` carries the idempotency key —
    ``payment_intent`` id for purchases, invoice id for subscriber grants.
    """

    __tablename__ = "credit_buckets"
    SOURCE_FREE_SIGNUP_BONUS = "free_signup_bonus"
    SOURCE_SUBSCRIBER_MONTHLY = "subscriber_monthly"
    SOURCE_PURCHASED = "purchased"
    SOURCE_PROMOTIONAL = "promotional"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    granted_credits: Mapped[int] = mapped_column(Integer)
    remaining_credits: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stripe_ref: Mapped[str | None] = mapped_column(String(96), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class BillingLedgerEntry(Base):
    """#53 — append-only billing audit ledger (kb design-billing.md §"Audit
    log entries"). ``credits_delta`` is signed; pure-audit ops carry 0."""

    __tablename__ = "billing_ledger"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    op: Mapped[str] = mapped_column(String(64), index=True)
    credits_delta: Mapped[int] = mapped_column(Integer, default=0)
    bucket_id: Mapped[str | None] = mapped_column(ForeignKey("credit_buckets.id"), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class StripeEvent(Base):
    """#53 — processed Stripe webhook event ids (idempotency guard)."""

    __tablename__ = "stripe_events"
    stripe_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), default="")
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
