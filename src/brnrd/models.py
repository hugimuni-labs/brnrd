"""ORM models for the inbox-as-service spine.

The data minimization stance lives here in the column choices:
``Event.body`` is retained only while an event is queued for its
own daemon to drain; response bodies are never columns at all —
``POST /v1/daemons/responses`` records only metadata
(``response_status`` / ``response_len`` / ``response_ms``) and
forwards the body out without persisting it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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

    projects: Mapped[list["Project"]] = relationship(back_populates="account")


class Token(Base):
    """A bearer credential. One table covers account API keys, web
    sessions, and project-scoped daemon tokens — disambiguated by
    ``kind``. Daemon tokens carry a non-null ``project_id``.
    """

    __tablename__ = "tokens"

    KIND_API_KEY = "account_api_key"
    KIND_SESSION = "session"
    KIND_DAEMON = "daemon"
    ACCOUNT_KINDS = (KIND_API_KEY, KIND_SESSION)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_project_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    account: Mapped["Account"] = relationship(back_populates="projects")


class Daemon(Base):
    """A registered daemon for a project. Register is idempotent on
    ``(project_id, daemon_name)``."""

    __tablename__ = "daemons"
    __table_args__ = (
        UniqueConstraint("project_id", "daemon_name", name="uq_daemon_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    token_id: Mapped[str] = mapped_column(ForeignKey("tokens.id"))
    daemon_name: Mapped[str] = mapped_column(String(128))
    capabilities: Mapped[str] = mapped_column(Text, default="")
    online: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Event(Base):
    """A queued task for a project's daemon to drain.

    ``seq`` is the monotonic cursor the daemon long-polls against.
    ``body`` holds the task text only while queued. ``reply_to`` is
    the opaque routing blob the producer attached (where to send the
    answer); it flows back out unchanged on response and is never
    inspected by the spine.
    """

    __tablename__ = "events"

    STATUS_QUEUED = "queued"
    STATUS_RESPONDED = "responded"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    source: Mapped[str] = mapped_column(String(32), default="dev")
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_to: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default=STATUS_QUEUED)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Response metadata only — never the response body.
    response_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_len: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatBinding(Base):
    """A platform chat bound to a project. Globally unique on
    ``(platform, chat_id)`` so one chat can't fan out to two projects.
    """

    __tablename__ = "chat_bindings"
    __table_args__ = (
        UniqueConstraint("platform", "chat_id", name="uq_chat_binding"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), default="telegram")
    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class TgPairCode(Base):
    """A one-time code issued by an account to bind a Telegram chat to
    a project. Consumed when the user sends ``/start <code>`` to the bot.
    """

    __tablename__ = "tg_pair_codes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class PairRequest(Base):
    """A device-flow connect request.

    Created unauthenticated by the CLI, approved by the logged-in
    account, then polled by the CLI. ``minted_token`` transiently
    holds the daemon token plaintext between approval and the first
    successful poll (TTL-bounded), since only hashes live in
    ``tokens``; it is cleared once handed back.
    """

    __tablename__ = "pair_requests"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_CONSUMED = "consumed"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pair_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    poll_secret_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING)
    account_id: Mapped[str | None] = mapped_column(nullable=True)
    project_id: Mapped[str | None] = mapped_column(nullable=True)
    minted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
