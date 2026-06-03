"""Request / response bodies for the brnrd API.

Pydantic models kept deliberately thin — the spine carries task
text and an opaque ``reply_to`` routing blob, nothing platform-
specific. Field names track ``design-brnrd-protocol.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Accounts / projects ─────────────────────────────────────────────


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


class CardPost(BaseModel):
    event_id: str
    text: str
    # None → send a new card and return its id; set → edit that message
    # in place. The daemon's card driver owns this id; brnrd stores none.
    message_id: int | None = None


class CardAck(BaseModel):
    event_id: str
    message_id: int | None = None


class PackRelayPost(BaseModel):
    # The full diffense review pack to render. Held transiently in RAM
    # behind a capability token; never persisted (see pack_relay.py).
    pack: dict[str, Any]
    ttl_s: int | None = None


class PackRelayAck(BaseModel):
    token: str
    render_url: str
    expires_at: float


# ── Dev ingress (webhook stand-in) ──────────────────────────────────


class DevEnqueue(BaseModel):
    project_id: str
    body: str
    source: str = "dev"
    reply_to: dict[str, Any] = Field(default_factory=dict)


class DevEnqueued(BaseModel):
    event_id: str
    seq: int
