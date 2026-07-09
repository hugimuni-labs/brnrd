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
    account_id: str | None = None
    repo_id: str | None = None
    daemon_token: str | None = None
    telegram_pair: TelegramPairStarted | None = None


class ConfigChangeRequestCreate(BaseModel):
    """Daemon-initiated loom-envelope Phase 2 proposal (`POST /v1/daemons/config-requests`).

    ``proposal_id`` is the daemon's own local proposal filename stem — the
    join key `decide_core` writes back into the account dispatch channel so
    the daemon's existing approve/reject reply convention (CS6's
    runner-policy pattern) resolves it without a new lookup mechanism.
    """

    proposal_id: str = Field(min_length=1, max_length=96)
    config_key: str = Field(min_length=1, max_length=128)
    current_value: str = ""
    requested_value: str = Field(min_length=1, max_length=256)
    reason: str = ""


class ConfigChangeRequestOut(BaseModel):
    request_id: str
    status: str
    approve_url: str | None = None


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


class ActivityRecordIn(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="run", min_length=1, max_length=32)
    source: str = Field(default="", max_length=32)
    conversation_key: str = Field(default="", max_length=255)
    summary: str = ""
    runner: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="", max_length=32)
    phase: str = Field(default="", max_length=64)
    branch: str = Field(default="", max_length=255)
    pr_number: str | int | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    scheduled_for: datetime | None = None
    defer_until: datetime | None = None
    links: dict[str, Any] = Field(default_factory=dict)


class ActivityReport(BaseModel):
    records: list[ActivityRecordIn] = Field(default_factory=list)


class ActivityRecordOut(ActivityRecordIn):
    repo_id: str
    reported_at: datetime


class ActivityList(BaseModel):
    activity: list[ActivityRecordOut]


class PlansReport(BaseModel):
    """CPS (Current Planned State) snapshot a daemon pushes for its account.

    ``repo_plan_md`` is this daemon's repo-scoped CS5 active plan;
    ``cross_repo_plan_md`` and ``decision_ledger_md`` are account-wide
    (CS5 cross-repo plan, CS7 decision ledger) and get overwritten by
    whichever connected daemon reports last — same last-write-wins shape
    as the activity snapshot, acceptable because these are single-writer
    per account in practice (one resident's dominion).
    """

    repo_plan_md: str = ""
    cross_repo_plan_md: str = ""
    decision_ledger_md: str = ""


class PlansOut(PlansReport):
    plans_updated_at: datetime | None = None


class QuotaWindowIn(BaseModel):
    """One quota bucket (``5h window`` / ``weekly``) for a shell."""

    label: str = Field(min_length=1, max_length=40)
    used: float | None = None
    limit: float | None = None
    percent: float | None = None
    reset: str | None = None
    # Machine-parseable reset instant (unix epoch seconds) alongside the
    # display-text `reset` above — added for the window-track visual's
    # time-remaining axis (2026-07-06, kb/design-dashboard-live-surface.md
    # "Shipped" gap this closes). Without a declared field here, pydantic's
    # default extra="ignore" would silently drop it from `model_dump()`.
    resets_at: float | None = None


class QuotaCreditsIn(BaseModel):
    """Shell credit evidence: account credit balance plus proven per-run spend.

    Claude's interactive ``/usage`` panel can expose usage credits (amount
    spent / cap / reset). Claude's headless result JSON separately reports
    ``total_cost_usd`` for a completed run; that becomes a real charge once a
    subscription window is exhausted and the account falls through to metered
    credits. See ``src/brr/gates/cloud.py::_claude_credits_block``.
    """

    total_cost_usd: float | None = None
    summary: str | None = None
    updated_at: str | None = None
    enabled: bool | None = None
    used_percentage: float | None = None
    remaining_percentage: float | None = None
    spent_amount: float | None = None
    limit_amount: float | None = None
    currency: str | None = None
    reset: str | None = None
    resets_at: float | None = None
    run_spend_summary: str | None = None


class QuotaShellIn(BaseModel):
    shell: str = Field(min_length=1, max_length=32)
    status: str = Field(default="unknown", max_length=32)
    windows: list[QuotaWindowIn] = Field(default_factory=list)
    # The underlying scrape's own capture time (ISO-8601), distinct from
    # when the daemon last PUT this payload — a cached Claude ``/usage``
    # scrape only refreshes while a run is active, so it can be hours older
    # than the publish itself. Without this, staleness can only be measured
    # against the daemon's publish cadence, which is always "fresh" — the
    # reported "lying Claude usage panel" bug, 2026-07-07.
    updated_at: str | None = None
    credits: QuotaCreditsIn | None = None


class QuotaReport(BaseModel):
    """Runner-quota snapshot a daemon pushes for itself (#237).

    Replaces this daemon token's whole quota list, same last-write-wins
    shape as the Activity/Plans mirrors (`ActivityReport`/`PlansReport`) —
    see `src/brr/gates/cloud.py::_quota_snapshot` for the daemon-side
    collector this feeds from.
    """

    shells: list[QuotaShellIn] = Field(default_factory=list)


class QuotaOut(BaseModel):
    shells: list[QuotaShellIn]
    quota_updated_at: datetime | None = None


class LiveRunIn(BaseModel):
    """One entry from the local presence registry (``src/brr/presence.py``)
    — a thought currently awake on this daemon, or an ad-hoc session
    alongside it (#258)."""

    id: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="", max_length=32)
    stream: str = Field(default="", max_length=256)
    label: str = Field(default="", max_length=256)
    run_id: str = Field(default="", max_length=64)
    repo_label: str = Field(default="", max_length=256)
    started_at: str | None = None
    last_seen: str | None = None
    # Same join key as RunLedgerRowIn's fields below — a concurrent
    # `spawn:` child now carries these while still live (presence.py),
    # not only after it closes into the ledger
    # (kb/design-multi-workstream-concurrency.md "Ranked moves" #1).
    parent_run_id: str | None = Field(default=None, max_length=64)
    is_subspawn: bool = False
    # #200's remaining slice (progress-card richness): the run's current
    # lifecycle phase and live `.card` note text, projected by
    # `src/brr/run_progress.py::project_run` at publish time
    # (`cloud.py::_live_runs_snapshot`). `None` when there's no
    # conversation record yet or no card note has been written.
    phase: str | None = Field(default=None, max_length=32)
    card_text: str | None = Field(default=None, max_length=4096)
    card_updated_at: str | None = None


class LiveRunsReport(BaseModel):
    """Live/coexisting-runs snapshot a daemon pushes for itself (#258).

    Same last-write-wins shape as Activity/Plans/Quota — see
    `src/brr/gates/cloud.py::_live_runs_snapshot` for the daemon-side
    collector this feeds from.
    """

    runs: list[LiveRunIn] = Field(default_factory=list)
    # Configured `spawn:` pool width (`spawn.max_concurrent`), piggybacked
    # here rather than a new endpoint — loom-envelope Phase 1's one piece of
    # data the live-runs publish didn't already carry (the active count is
    # just a count of `is_subspawn` entries in `runs` above). None when the
    # daemon hasn't reported yet.
    spawn_max_concurrent: int | None = None


class LiveRunsOut(BaseModel):
    runs: list[LiveRunIn]
    live_runs_updated_at: datetime | None = None
    spawn_max_concurrent: int | None = None


class PRReviewItemIn(BaseModel):
    """One open PR from the daemon's account-scoped review queue (#259)."""

    number: int = Field(ge=1)
    title: str = Field(default="", max_length=500)
    url: str = Field(default="", max_length=2048)
    repo_label: str = Field(default="", max_length=256)
    created_at: str | None = None
    draft: bool = False
    author: str = Field(default="", max_length=255)


class PRReviewQueueReport(BaseModel):
    """Open-PR review queue a daemon pushes for itself (#259).

    Same last-write-wins mirror as Activity/Plans/Quota/Live-runs — see
    `src/brr/gates/cloud.py::_pr_review_snapshot` for the daemon-side
    collector this feeds from.
    """

    prs: list[PRReviewItemIn] = Field(default_factory=list)


class PRReviewQueueOut(BaseModel):
    prs: list[PRReviewItemIn]
    pr_review_queue_updated_at: datetime | None = None


class RunLedgerRowIn(BaseModel):
    """One closed-run receipt row from ``src/brr/run_ledger.py`` (#271).

    This is a mirrored receipt, not a validation surface: the local ledger is
    best-effort and may leave any field null when the runner or quota source
    cannot prove it.
    """

    run_id: str | None = None
    event_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    wall_clock_seconds: float | None = None
    runner_shell: str | None = None
    runner_core: str | None = None
    # Core attestation: what the config pinned at dispatch vs. what the
    # Shell's own result JSON observed (`runner_core` above holds the
    # observed value once the run closes). `core_mismatch` is the alarm
    # bit; None = unverifiable (no observation / unpinned dispatch).
    core_expected: str | None = None
    core_mismatch: bool | None = None
    repo_label: str | None = None
    source_system: str | None = None
    external_refs: list[Any] | None = None
    task_classification: str | None = None
    parent_run_id: str | None = None
    is_subspawn: bool | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    tokens_cache_read: int | None = None
    tokens_cache_creation: int | None = None
    context_window_used: float | None = None
    weekly_pct_delta: float | None = None
    five_hour_pct_delta: float | None = None
    usd_subscription_attributed: float | None = None
    usd_credits_equivalent: float | None = None
    estimate_vs_actual: str | None = None


class RunLedgerReport(BaseModel):
    """Closed-run receipt rows a daemon pushes for itself (#271)."""

    rows: list[RunLedgerRowIn] = Field(default_factory=list)


class RunLedgerOut(BaseModel):
    rows: list[RunLedgerRowIn]
    run_ledger_updated_at: datetime | None = None


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
