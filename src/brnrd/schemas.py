"""Request / response bodies for the brnrd API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RepoCreate(BaseModel):
    repo_full_name: str = Field(min_length=1, max_length=255)
    forge: str = Field(default="github", min_length=1, max_length=32)
    forge_repo_id: str | None = Field(default=None, max_length=64)
    default_branch: str | None = Field(default=None, max_length=255)


class RepoOut(BaseModel):
    repo_id: str
    forge: str
    repo_full_name: str
    repo_owner: str
    repo_name: str
    forge_repo_id: str | None = None
    default_branch: str | None = None
    created_at: datetime


class RepoList(BaseModel):
    repos: list[RepoOut]


class GitHubInstallationOut(BaseModel):
    installation_id: str
    target_login: str
    target_type: str
    last_synced_at: datetime | None = None


class GitHubInstalledRepoOut(BaseModel):
    repo_full_name: str
    forge_repo_id: str | None = None
    default_branch: str | None = None
    is_private: bool = False


class GitHubInstallationsList(BaseModel):
    installations: list[GitHubInstallationOut]
    installed_repos: list[GitHubInstalledRepoOut]


class PairStarted(BaseModel):
    pair_code: str
    pair_url: str
    poll_secret: str
    expires_at: datetime


class PairApprove(BaseModel):
    repo_id: str


class TelegramPairStart(BaseModel):
    repo_id: str


class TelegramPairStarted(BaseModel):
    pair_code: str
    instructions: str
    deep_link: str | None = None


class PairStatus(BaseModel):
    status: str
    repo_id: str | None = None
    daemon_token: str | None = None


class DaemonRegister(BaseModel):
    daemon_name: str = Field(min_length=1, max_length=128)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class DaemonRegistered(BaseModel):
    daemon_id: str
    repo_id: str


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
    message_id: int | None = None


class CardAck(BaseModel):
    event_id: str
    message_id: int | None = None


class PackRelayPost(BaseModel):
    pack: dict[str, Any]
    ttl_s: int | None = None


class PackRelayAck(BaseModel):
    token: str
    render_url: str
    expires_at: float


class DevEnqueue(BaseModel):
    repo_id: str
    body: str
    source: str = "dev"
    reply_to: dict[str, Any] = Field(default_factory=dict)


class DevEnqueued(BaseModel):
    event_id: str
    seq: int
