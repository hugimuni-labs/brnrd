"""ORM models for the brnrd repo-routing spine."""

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
