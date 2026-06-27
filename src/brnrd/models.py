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
