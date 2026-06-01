"""Request / response bodies for the brnrd API.

Pydantic models kept deliberately thin — the spine carries task
text and an opaque ``reply_to`` routing blob, nothing platform-
specific. Field names track ``design-brnrd-protocol.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Accounts / sessions / projects ──────────────────────────────────


class AccountCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8)


class AccountCreated(BaseModel):
    account_id: str
    api_key: str
    # A "default" project is created with the account so the user can pair
    # a daemon immediately, without a separate project-create call first.
    default_project_id: str


class SessionCreate(BaseModel):
    email: str
    password: str


class SessionCreated(BaseModel):
    account_id: str
    session_token: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class ProjectOut(BaseModel):
    project_id: str
    name: str
    created_at: datetime


class ProjectList(BaseModel):
    projects: list[ProjectOut]


# ── Device-flow connect ─────────────────────────────────────────────


class PairStarted(BaseModel):
    pair_code: str
    pair_url: str
    poll_secret: str
    expires_at: datetime


class PairApprove(BaseModel):
    project_id: str


class TelegramPairStart(BaseModel):
    project_id: str


class TelegramPairStarted(BaseModel):
    pair_code: str
    instructions: str
    # ``https://t.me/<bot>?start=<code>`` when the bot username is
    # configured; None otherwise (fall back to the ``/start`` instructions).
    deep_link: str | None = None


class PairStatus(BaseModel):
    status: str
    project_id: str | None = None
    daemon_token: str | None = None


# ── Daemon-facing ───────────────────────────────────────────────────


class DaemonRegister(BaseModel):
    daemon_name: str = Field(min_length=1, max_length=128)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class DaemonRegistered(BaseModel):
    daemon_id: str
    project_id: str


class DaemonDeregister(BaseModel):
    daemon_name: str = Field(min_length=1, max_length=128)


class EventOut(BaseModel):
    event_id: str
    seq: int
    source: str
    body: str | None
    reply_to: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class InboxResponse(BaseModel):
    events: list[EventOut]
    cursor: int


class ResponsePost(BaseModel):
    event_id: str
    body_markdown: str
    status: str = "done"


class ResponseAck(BaseModel):
    event_id: str
    forwarded: bool


# ── Dev ingress (webhook stand-in) ──────────────────────────────────


class DevEnqueue(BaseModel):
    project_id: str
    body: str
    source: str = "dev"
    reply_to: dict[str, Any] = Field(default_factory=dict)


class DevEnqueued(BaseModel):
    event_id: str
    seq: int
