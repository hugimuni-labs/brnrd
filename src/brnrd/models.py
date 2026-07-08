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
    repos: Mapped[list["Repo"]] = relationship(back_populates="account")
    # Current Planned State (CPS) — account-level slices of the account
    # dominion's CS5/CS7 files (cross-repo plan + decision ledger); see
    # kb/plan-brnrd-dashboard-mvp.md "Gap: Current Planned State view".
    cross_repo_plan_md: Mapped[str] = mapped_column(Text, default="")
    decision_ledger_md: Mapped[str] = mapped_column(Text, default="")
    plans_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    # Live/coexisting-runs snapshot (#258), mirrored from the local presence
    # registry via `PUT /v1/daemons/live-runs` — see
    # kb/design-dashboard-live-surface.md §"Reconsidered 2026-07-06".
    live_runs_json: Mapped[str] = mapped_column(Text, default="[]")
    live_runs_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # PR-review queue snapshot (#259), mirrored from `gh pr list` via
    # `PUT /v1/daemons/pr-review-queue`. Calendar age, not runner quota, is
    # the meaningful clock for this lane.
    pr_review_queue_json: Mapped[str] = mapped_column(Text, default="[]")
    pr_review_queue_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Closed-run cost ledger snapshot (#271), mirrored from local
    # `.brr/run-ledger.jsonl` rows via `PUT /v1/daemons/run-ledger`.
    run_ledger_json: Mapped[str] = mapped_column(Text, default="[]")
    run_ledger_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
