"""Request / response bodies for the brnrd API."""

from __future__ import annotations

from datetime import datetime
from types import UnionType
from typing import Any, ClassVar, Literal, Union, get_args, get_origin

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class RepoCreate(BaseModel):
    repo_full_name: str = Field(min_length=1, max_length=255)
    forge: str = Field(default="github", min_length=1, max_length=32)
    forge_repo_id: str | None = Field(default=None, max_length=64)
    default_branch: str | None = Field(default=None, max_length=255)
    # Explicit publish-scope consent, same vocabulary as the browser connect
    # flow (`publish_scope.normalize_publish_layers`, which 422s on a typo).
    # Omitting it is not a way to get the permissive state: absent records the
    # explicit opt-out sentinel `publish_scope.OFF`, never NULL. An API-key
    # client that wants mirroring has to say which lanes, in as many words.
    publish_layers: str | None = Field(default=None, max_length=255)


class RepoOut(BaseModel):
    repo_id: str
    forge: str
    repo_full_name: str
    repo_owner: str
    repo_name: str
    forge_repo_id: str | None = None
    default_branch: str | None = None
    created_at: datetime
    # Surfaced so an API-key client can see what it consented to — and see a
    # `null` (legacy row, publishing paused) instead of having to infer it.
    publish_layers: str | None = None


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


class PairStartRequest(BaseModel):
    """What the connecting daemon already knows about its own checkout.

    Sent once, unauthenticated (there's no token yet — pairing is how one
    gets minted), on the initial `POST /v1/accounts/pair`. Every field is
    optional and best-effort: a daemon run outside a git checkout, or an
    older CLI that predates this, sends none of it, and the handshake falls
    back to the pre-existing pick-from-a-list approval flow unchanged.
    """

    repo_full_name: str = ""
    git_remote: str = ""
    branch: str = ""
    default_branch: str = ""
    # "github" (the historical implicit default) or "local" — a checkout
    # with no forge behind it, `owner/name` synthesized client-side from the
    # folder (`gates.cloud.local_repo_identity`). Never trusted alone: it
    # only ever labels a `repo_full_name` sent in the same payload.
    forge: str = ""


class PairStarted(BaseModel):
    pair_code: str
    pair_url: str
    poll_secret: str
    # The initiating daemon's proof, to be presented back at approve. Already
    # embedded in `pair_url`'s fragment — returned separately only so a
    # client that builds its own approval surface doesn't have to parse the
    # URL apart. Never leaves the machine that ran the pairing command
    # except through the link its human opens.
    approve_secret: str
    expires_at: datetime


class PairApprove(BaseModel):
    repo_id: str
    # Defaulted rather than required so a client that sends none gets the
    # explicit 403 ("open the full link your terminal printed") from
    # `approve_core` instead of a 422 that reads like a malformed request.
    # Absent and wrong are the same answer either way.
    approve_secret: str = ""


class TelegramPairStart(BaseModel):
    repo_id: str


class TelegramPairStarted(BaseModel):
    pair_code: str
    instructions: str
    deep_link: str | None = None


class MessengerPairStarted(BaseModel):
    """`POST /v1/dashboard/pair`'s response — the registry-generalized
    twin of `TelegramPairStarted` above, carrying which platform it minted
    for since the endpoint itself is no longer platform-specific. The
    request body has no schema of its own: `dashboard_pair_api` parses
    `{"platform": str}` by hand (`await request.json()`), matching this
    router's own established convention for POST bodies
    (`dashboard_runners_wake_request` does the same) rather than
    `pairing.py`'s pydantic-`Body` one."""

    pair_code: str
    instructions: str
    deep_link: str | None = None
    platform: str


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
    repo_id: str | None = None


class PublishingCredential(BaseModel):
    token: str
    expires_at: datetime
    login: str


class DaemonDeregister(BaseModel):
    daemon_name: str = Field(min_length=1, max_length=128)


class MachineRepoOut(BaseModel):
    """One repo a machine is currently the default-routing daemon for.

    Not a membership record (see ``_session._machine_views``) — a machine
    that has re-registered against a different repo since carries neither
    row here for the one it left.
    """

    repo_id: str
    repo_full_name: str


class MachineOut(BaseModel):
    """`GET /v1/machines` row — design-machines-and-guests.md R1, #1365."""

    daemon_id: str
    daemon_name: str
    online: bool
    last_seen: datetime | None = None
    enabled_repos: list[MachineRepoOut] = Field(default_factory=list)


class MachinesOut(BaseModel):
    generated_at: datetime
    machines: list[MachineOut]


class EventOut(BaseModel):
    event_id: str
    seq: int
    source: str
    repo_label: str | None = None
    body: str | None
    reply_to: dict[str, Any] = Field(default_factory=dict)
    # #525 — image-attachment pointers ({file_id, filename, kind[, file_size]});
    # the daemon fetches bytes through the attachment proxy at ingestion time.
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class ServerBuildOut(BaseModel):
    """``version_info.build_info()``, straight through — never fabricated."""

    commit: str | None = None
    built_at: str | None = None
    started_at: str | None = None


class ServerGithubOut(BaseModel):
    """Effective GitHub-trigger config off ``settings`` — booleans for secrets.

    A leaked webhook secret or bot token is not a fingerprint, so those two
    fields never carry the value, only whether one is configured. Everything
    else here is already visible to anyone who reads an issue the bot
    commented on.

    ``app_id_set`` / ``app_key_set`` are the *publishing* credential, added
    2026-07-31 after a whole night was spent inferring them. This surface
    answered "what GitHub config is prod running" with the webhook secret and
    the fallback bot PAT — and stayed silent about the App identity, which is
    the only credential a managed runner can push with. Prod read
    ``webhook secret set · bot token set`` while
    ``POST /v1/daemons/publishing-credential`` had been 500ing for six hours.
    Booleans, same policy as the two above: the id is not a secret but pairing
    it with the key here keeps one clause answering one question.
    """

    bot_login: str
    app_slug: str
    trigger_label: str
    trigger_aliases: list[str]
    webhook_secret_set: bool
    bot_token_set: bool
    app_id_set: bool = False
    app_key_set: bool = False


class ServerFingerprint(BaseModel):
    """What prod is actually running — carried on the channel the daemon
    already polls (``GET /v1/daemons/inbox``) so the wake can answer "is my
    merge live?" without a new request (2026-07-30 incident)."""

    build: ServerBuildOut
    github: ServerGithubOut


class InboxResponse(BaseModel):
    events: list[EventOut]
    cursor: int
    # Optional: older daemons ignore an unknown key; a daemon that reads it
    # persists it locally (`brr.gates.cloud`) for the wake to render.
    server: ServerFingerprint | None = None


class ResponsePost(BaseModel):
    event_id: str
    body_markdown: str
    status: str = "done"
    # #61 — the daemon's conversation_key; optional so pre-#61 daemons that
    # omit it keep working unchanged.
    conversation_id: str | None = Field(default=None, max_length=255)


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


class MessagePost(BaseModel):
    """#1205's fresh-send primitive: an unaddressed send, no inbound event.

    ``platform`` defaults to ``telegram`` — the only wired lane today; an
    unresolvable/unsupported one is an honest 501 at the router, not a
    silently-ignored field.
    """

    body_markdown: str = Field(min_length=1)
    platform: str = Field(default="telegram", min_length=1, max_length=32)


class MessageAck(BaseModel):
    platform: str
    message_id: str


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
    # Abuse ceiling (limits.py family): a snapshot is a full replacement of
    # this daemon's live activity — 1000 rows is far beyond any real daemon
    # and bounds a runaway publisher. 422s with the field named, not silent.
    records: list[ActivityRecordIn] = Field(default_factory=list, max_length=1000)


class ActivityRecordOut(ActivityRecordIn):
    repo_id: str
    reported_at: datetime


class ActivityList(BaseModel):
    activity: list[ActivityRecordOut]


class SurfaceFileIn(BaseModel):
    """One discovered Markdown page in the unified corpus, home-relative.

    ``path`` is relative to the brnrd home (``surface/index.md``,
    ``knowledge/repos/<slug>/foo.md``) so cross-layer links resolve. ``layer``
    places it in the authored surface, the knowledge base, or the durable run
    nodes; ``truncated`` marks a mirror capped for payload size (the file
    still appears in the listing — see the cloud gate's corpus publisher).
    """

    path: str = Field(min_length=1, max_length=512)
    markdown: str = Field(default="", max_length=300_000)
    layer: str = Field(default="authored", max_length=32)
    truncated: bool = False


class SurfaceReport(BaseModel):
    """The complete corpus (surface + knowledge + runs) from one daemon."""

    files: list[SurfaceFileIn] = Field(default_factory=list, max_length=4000)


class SurfaceOut(SurfaceReport):
    surface_updated_at: datetime | None = None


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


class GateHealthIn(BaseModel):
    gate: str = Field(min_length=1, max_length=32)
    last_poll_ok: str | None = None
    age_seconds: int | None = Field(default=None, ge=0)
    last_error: str | None = None
    status: Literal["ok", "degraded", "never"]


class QuotaReport(BaseModel):
    """Runner-quota snapshot a daemon pushes for itself (#237).

    Replaces this daemon token's whole quota list, same last-write-wins
    shape as the Activity/Surface mirrors (`ActivityReport`/`SurfaceReport`) —
    see `src/brr/gates/cloud.py::_quota_snapshot` for the daemon-side
    collector this feeds from.
    """

    shells: list[QuotaShellIn] = Field(default_factory=list)
    gates: list[GateHealthIn] = Field(default_factory=list)
    # #1268: this daemon's own boot-kernel measurement for the repo it's
    # paired to (`bootscore.BootHost.agents_md_missing`/`.kb_missing`,
    # #1261) — piggybacked on this same tick, not a new endpoint. `None`
    # (the default) is what an older daemon build's payload looks like: the
    # field is simply absent, same as a pre-#360 payload had no `gates`.
    repo_agents_md_missing: bool | None = None
    repo_kb_missing: bool | None = None


class QuotaOut(BaseModel):
    shells: list[QuotaShellIn]
    gates: list[GateHealthIn] = Field(default_factory=list)
    repo_agents_md_missing: bool | None = None
    repo_kb_missing: bool | None = None
    quota_updated_at: datetime | None = None


class RunnerProfileIn(BaseModel):
    """One selectable Shell+Core profile from a daemon's local catalog (#328).

    Mirrors `src/brr/runner.py::_catalog_record` — the same projection the
    Run Context Bundle's "Runner catalog" block injects into every wake.
    ``class`` is the wire name for the cost class (economy/balanced/strong);
    pydantic can't use the keyword, hence the alias.
    """

    name: str = Field(min_length=1, max_length=64)
    shell: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    provider: str | None = Field(default=None, max_length=64)
    owner: str | None = Field(default=None, max_length=32)
    cost_class: str | None = Field(default=None, max_length=32, alias="class")
    cost_rank: int | None = None
    quota_source: str | None = Field(default=None, max_length=64)
    capability_score: float | None = None
    capability_source: str | None = Field(default=None, max_length=255)
    capability_freshness: str | None = Field(default=None, max_length=64)
    generated_core: bool | None = None
    availability: str | None = Field(default=None, max_length=32)
    available: bool | None = None
    on_path: bool | None = None
    stale: bool | None = None
    pin: str | None = Field(default=None, max_length=128)
    selected: bool | None = None

    model_config = {"populate_by_name": True}


class EnvironmentOptionIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    available: bool = True
    reason: str | None = Field(default=None, max_length=255)


class RunnerStickyIn(BaseModel):
    """#932's conversation-sticky, mirrored so the rack can render it.

    A claimed tap binds its profile to the claiming conversation for a TTL
    (daemon-owned, `src/brr/wake_request.py`). Until this rode the runners
    lane the record decided every wake in the bound thread while the rack
    kept showing the config pin as `selected` — the 2026-08-08 "the core tap
    is lying" defect. ``expires_at`` is computed daemon-side from the same
    TTL dispatch honours, so the dashboard's timer and dispatch agree.
    """

    profile: str = Field(min_length=1, max_length=64)
    claimed_at: datetime | None = None
    expires_at: datetime | None = None
    correspondent_key: str | None = Field(default=None, max_length=255)
    conversation_key: str | None = Field(default=None, max_length=255)
    request_id: str | None = Field(default=None, max_length=64)


class RunnersReport(BaseModel):
    """Runner-catalog snapshot a daemon pushes for itself (#328 spool rack).

    Same last-write-wins mirror shape as `QuotaReport`; see
    `src/brr/gates/cloud.py::_runners_snapshot` for the daemon-side collector.
    ``default`` names the profile `resolve_runner` would pick for the next
    default wake (the config pin, or the cost-aware choice when unpinned).
    """

    profiles: list[RunnerProfileIn] = Field(default_factory=list)
    default: str | None = Field(default=None, max_length=64)
    environment_default: str | None = Field(default=None, max_length=32)
    environments: list[EnvironmentOptionIn] = Field(default_factory=list)
    # #932: the live conversation-sticky, or None when none is in force.
    sticky: RunnerStickyIn | None = None
    # No `consumed_wake_request_ids` here any more (#733). Retiring a tap was
    # a piggybacked *ack* — the daemon deciding locally that a row was spent
    # and telling the server one publish tick later. The claim endpoint
    # decides and retires in the same transaction, so there is nothing left
    # to acknowledge. An older daemon still sending the field is ignored, and
    # correctly so: its opinion was never the one that mattered.


class RunnerWakeRequestOut(BaseModel):
    """A spool-rack tap (#328): "next wake on this profile"."""

    request_id: str
    profile: str
    repo_label: str | None = None
    environment: str | None = None
    requested_at: datetime | None = None
    status: str


class WakeRequestClaim(BaseModel):
    """A dispatching daemon asking the server to decide a tap's fate (#733).

    Sent at most once per dispatched wake, and only when the daemon's
    presence-bit mirror says a tap exists at all — so the common dispatch,
    and every local-only account, sends this never. Everything the server
    needs to run the whole guard ladder is here: which tap, which wake, what
    woke it, and when that wake's event came into being.
    """

    request_id: str = Field(max_length=64)
    event_id: str | None = Field(default=None, max_length=128)
    source: str | None = Field(default=None, max_length=64)
    event_created: datetime | None = None
    # The daemon's own clock at claim time. Sent so the parked-after-the-event
    # rung can compare two *durations* — how old the tap is by the server's
    # clock against how old the event is by the daemon's — instead of two
    # absolute stamps taken from different machines. Absolute comparison was
    # skew-sensitive in exactly the direction that refuses real taps: a daemon
    # clock a few seconds behind the server made every tap look parked after
    # the event it was parked for, every time, not once.
    daemon_now: datetime | None = None


class WakeRequestClaimOut(BaseModel):
    """The server's verdict on a claim, with the row already at its final
    status (#733) — there is no second round trip and no ack.

    ``apply`` is the only bit dispatch acts on. ``reason`` is what the
    daemon writes into ``.brr/wake-request-receipt.json`` and onto the run's
    ``resources.runner.wake_request`` facet, so "you asked for X, you got Y,
    because Z" survives the machine that decided it. ``status`` is the row's
    status *after* the transaction: anything but ``pending`` means the
    daemon may drop its mirror now rather than wait a publish tick.
    """

    apply: bool
    reason: str | None = None
    request_id: str
    status: str
    profile: str | None = None
    repo_label: str | None = None
    environment: str | None = None


class RunnersOut(BaseModel):
    profiles: list[RunnerProfileIn]
    default: str | None = None
    environment_default: str | None = None
    environments: list[EnvironmentOptionIn] = Field(default_factory=list)
    runners_updated_at: datetime | None = None
    # Piggyback channel: the account's pending wake request, if any, rides
    # back on the daemon's own catalog publish tick — no extra polling loop.
    pending_wake_request: RunnerWakeRequestOut | None = None
    # #932 release ask piggyback: the dashboard asked, at this stamp, for
    # the conversation-sticky to be dropped. The daemon honours it only
    # against a record claimed at or before the stamp — a newer tap wins.
    sticky_release_at: datetime | None = None


# The visible mark a truncated display value ends with (#685 ask 2). Mirrors
# the *idea* of `src/brr/card.py::_TRUNCATION_MARK` without importing across
# the package boundary — `src/brr` (daemon) and `src/brnrd` (API) ship
# separately. A value that merely stops reads as a value that ended; this is
# how a reader can tell the difference.
LIVE_RUN_TRUNCATION_MARK = "…"

# Values something is **matched** against, where a truncated value is *wrong
# data* rather than shortened data. These keep hard rejection; every other
# bounded string on `LiveRunIn` is only ever *shown* and truncates instead.
#
# The split is **matched vs shown**, not display vs identity. `id`/`run_id`/
# `parent_run_id` are row and parent-child joins. `repo_label` reads like
# display and is matched: `publish_scope._subject_permits` resolves a row's
# consent through it, and an unresolvable label falls back to the *publisher's*
# consent — so truncating it here publishes an opted-out repo's row under
# someone else's permission, which is #714 through a new door. `stream` is
# deliberately absent: it looks like a key and nothing in `src/brnrd` matches
# on it.
#
# Deliberately the *closed* class, so the open one is the default (#685). This
# repo has paid four times (#417, #674, #709, #721) for a class defined by
# listing its members: the member nobody listed shows up later. The polarity
# chosen here makes a new *shown* field safe with no edit; the cost is that a
# new *matched* field defaults to the wrong side, and
# `test_every_matched_field_rejects_rather_than_truncates` is what charges it.
LIVE_RUN_IDENTITY_FIELDS = frozenset({"id", "run_id", "parent_run_id", "repo_label"})


def _is_str_annotation(annotation: Any) -> bool:
    """True for ``str`` and ``str | None``, false for ``list``/``dict`` fields.

    `max_length` means *characters* on a `str` and *items* on a `list`, and
    `LiveRunIn` carries both (`mood_frames` is `max_length=4` sequences). A
    bound table that failed to tell them apart would tell the daemon to
    truncate `mood_frames` to four characters.
    """
    if annotation is str:
        return True
    if get_origin(annotation) in (Union, UnionType):
        return any(arg is str for arg in get_args(annotation))
    return False


def string_bounds(model: type[BaseModel]) -> dict[str, int]:
    """``{field: max_length}`` for every ``str``-typed field of ``model``.

    Read off the model itself rather than hand-listed, so a field that gains
    a bound later joins with no edit here. This is the generator behind
    `tests/fixtures/live_run_bounds.json`, which pins the daemon-side copy in
    `src/brr/gates/cloud.py` (#723: pin duplications to an external fixture
    table; parity tests are the wrong pin, since copies that agree can be
    wrong together).
    """
    bounds: dict[str, int] = {}
    for name, field in model.model_fields.items():
        if not _is_str_annotation(field.annotation):
            continue
        for meta in field.metadata:
            cap = getattr(meta, "max_length", None)
            if cap is not None:
                bounds[name] = cap
                break
    return bounds


def truncate_marked(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters with the truncation mark visible."""
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(LIVE_RUN_TRUNCATION_MARK))
    return text[:keep] + LIVE_RUN_TRUNCATION_MARK[: limit]


class LiveRunIn(BaseModel):
    """One entry from the local presence registry (``src/brr/presence.py``)
    — a thought currently awake on this daemon, or an ad-hoc session
    alongside it (#258)."""

    id: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="", max_length=32)
    stream: str = Field(default="", max_length=256)
    label: str = Field(default="", max_length=256)
    name: str = Field(default="", max_length=60)
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
    # Shell+Core identity from the daemon presence registry. Keep the
    # existing cloud payload's small, sparse shape (``{}`` when a runner has
    # not been selected) so the API does not discard the fields before the
    # dashboard reads the stored snapshot.
    runner: dict[str, str] = Field(default_factory=dict)
    # #200's remaining slice (progress-card richness): the run's current
    # lifecycle phase and live `.card` note text, projected by
    # `src/brr/run_progress.py::project_run` at publish time
    # (`cloud.py::_live_runs_snapshot`). `None` when there's no
    # conversation record yet or no card note has been written.
    phase: str | None = Field(default=None, max_length=32)
    card_text: str | None = Field(default=None, max_length=4096)
    card_updated_at: str | None = None
    # #342 relics-so-far: counts of the run's attested produce mid-flight
    # (`{"commit": 2, "kb": 1}`), read by the daemon from its own
    # heartbeat-refreshed portal capsule (`brr.relics.live_portal_counts`)
    # at publish time. `None` = nothing attested (ad-hoc session, no
    # capsule yet); `{}` = known, no produce yet. Kind names are gated to
    # a conservative identifier shape daemon-side; the cap here only
    # bounds a hostile payload.
    relics_counts: dict[str, int] | None = None
    # #566 slice 0: resident-authored mood. `mood` is the raw handle from
    # the run's `.mood` control file; glyph/pitch are resolved daemon-side
    # against `brr.emotes` (`cloud.py::_mood_payload`) so this API and the
    # frontend never own an emote table. All three `None` when unset; an
    # unknown handle arrives name-only (glyph/pitch stay None) and renders
    # as a bare name, never a guessed face.
    mood: str | None = Field(default=None, max_length=64)
    mood_glyph: str | None = Field(default=None, max_length=16)
    # Every breath the face can take: a list of base->expression->base
    # sequences (`Emote.sequences` - primary cycle first, then alternates).
    # `mood_glyph` is only the *resting* frame, which is why it could never
    # animate and, across 98 situational emotes, collapsed onto 15 distinct
    # values; a renderer with `mood_frames` should prefer these and keep
    # `mood_glyph` for surfaces that cannot move. None when unset or when the
    # handle didn't resolve daemon-side - absent resolution stays absent data.
    mood_frames: list[list[str]] | None = Field(default=None, max_length=4)
    # The frame a resting surface holds — per emote, unlike `mood_glyph`
    # (= `frames[0]`, shared across a face family by design).
    mood_rest: str | None = Field(default=None, max_length=16)
    mood_pitch: float | None = Field(default=None, ge=0.0, le=1.0)
    # The run's claimed topic slugs (the-run-that-claims-its-thread): raw
    # from the resident's `.topics` control via the presence heartbeat
    # (`presence.py` → `cloud.py::_live_runs_snapshot`), no resolution
    # here — the dashboard resolves against its own warp graph, same
    # "this API owns no table" stance as `mood`. Empty for an unclaimed
    # run; bounded against a hostile payload only (the daemon already
    # slug-filters and caps at 32; 8 is generous for honest use).
    topics: list[str] = Field(default_factory=list, max_length=8)

    @classmethod
    def string_bounds(cls) -> dict[str, int]:
        """This model's ``{field: max_length}`` for its ``str`` fields."""
        return string_bounds(cls)

    @model_validator(mode="before")
    @classmethod
    def _truncate_display_fields(cls, data: Any) -> Any:
        """Cut over-long *display* strings to their bound instead of 422ing.

        #685 ask 2, the truncate half. A truncated `name` on the dashboard is
        strictly better than a dark dashboard, and until this existed one
        over-long field on one row rejected the daemon's entire live-runs
        publish — every run on that daemon went dark, re-attempted every 3s
        (`src/brr/gates/cloud.py::_DASHBOARD_PUBLISH_INTERVAL_S`).

        It lives here, not on the caller, because a caller can forget. It runs
        off `string_bounds(cls)` rather than a list of fields, because a list
        of fields can go stale: add a bounded display field to this model and
        it is covered the moment it exists. `LIVE_RUN_IDENTITY_FIELDS` is the
        only exception and stays hard-rejecting — see its comment.
        """
        if not isinstance(data, dict):
            return data
        out: dict[str, Any] | None = None
        for field, cap in cls.string_bounds().items():
            if field in LIVE_RUN_IDENTITY_FIELDS:
                continue
            value = data.get(field)
            if isinstance(value, str) and len(value) > cap:
                if out is None:
                    out = dict(data)
                out[field] = truncate_marked(value, cap)
        return data if out is None else out

    @field_validator("mood_frames")
    @classmethod
    def _bound_mood_frames(
        cls, value: list[list[str]] | None,
    ) -> list[list[str]] | None:
        """Clamp a hostile payload; an empty result is no frames at all.

        Nested lists need their own bound - `max_length=4` only counts the
        outer one, so without this a daemon could hand over four sequences
        of a million glyphs each. Same shape as `DaemonMoodIn._bound_frames`
        one level down. A sequence that clamps to nothing is dropped rather
        than kept as `[]`, and no sequences left means `None`: the whole
        mood contract is that absent data renders as nothing, never as a
        default face, and `[]` is a value a renderer has to remember to
        treat as absent.
        """
        if value is None:
            return None
        bounded = [
            [str(frame)[:16] for frame in seq[:8]]
            for seq in value[:4]
            if isinstance(seq, list) and seq
        ]
        bounded = [seq for seq in bounded if seq]
        return bounded or None

    @field_validator("relics_counts")
    @classmethod
    def _bound_relics_counts(
        cls, value: dict[str, int] | None,
    ) -> dict[str, int] | None:
        if value is None:
            return None
        return {
            str(kind)[:32]: count
            for kind, count in list(value.items())[:24]
        }


class LiveRunsReport(BaseModel):
    """Live/coexisting-runs snapshot a daemon pushes for itself (#258).

    Same last-write-wins shape as Activity/Plans/Quota — see
    `src/brr/gates/cloud.py::_live_runs_snapshot` for the daemon-side
    collector this feeds from.
    """

    # Deliberately *not* `list[LiveRunIn]` (#685 ask 2). FastAPI validates the
    # whole request body before the handler runs, so a typed list here means
    # one unparseable row 422s the entire batch and every live run on that
    # daemon goes dark. Rows are validated one at a time by
    # `isolate_live_runs` below, which the handler calls.
    #
    # `Any` and not `dict[str, Any]`, which was the first cut here: a `null` or
    # a bare string in the list fails `dict` at the *report* level and 422s the
    # whole batch again — the very class this change exists to close. The row
    # type has to be unconstrained for the isolation below to be the only thing
    # that can reject a row.
    #
    # The cost is that OpenAPI describes this as a list of anything rather than
    # of `LiveRunIn`; the response model still documents the row shape in full,
    # and this is a daemon-token-authenticated machine lane, not a browsable
    # one.
    runs: list[Any] = Field(default_factory=list)
    # The row model `runs` is validated against, declared rather than inferred.
    #
    # `runs` had to lose its item type for per-row isolation to be possible at
    # all (see above), and that erased the row shape from the one place a
    # *different* guard was reading it: #714's per-row-consent test derives
    # "does this lane's payload name its own repo" by walking the request
    # model's item types for a `repo_label` field. Typing this list as `Any`
    # blinded that derivation — the lane still gated correctly, and the test
    # that proves it stopped being able to see the lane. Exactly #722's shape:
    # a guard anchored one level off reads like no guard at all.
    #
    # So the row type stays machine-readable here. A lane that adopts per-row
    # isolation later must declare this too, and
    # `test_a_row_isolating_report_declares_its_row_model` fails it if not.
    ROW_MODEL: ClassVar[type[BaseModel]] = LiveRunIn
    # Stop-request ids this daemon has dispatched into the kill path since
    # its last publish (#476 wyrd §3) — same ack economics as
    # `RunnersReport.consumed_wake_request_ids`.
    consumed_run_stop_request_ids: list[str] = Field(default_factory=list)
    # Configured `spawn:` pool width (`spawn.max_concurrent`), piggybacked
    # here rather than a new endpoint — loom-envelope Phase 1's one piece of
    # data the live-runs publish didn't already carry (the active count is
    # just a count of `is_subspawn` entries in `runs` above). None when the
    # daemon hasn't reported yet.
    spawn_max_concurrent: int | None = None
    # #566 slice 0: the daemon-level telemetry face for the board at rest
    # (`cloud.py::_daemon_mood_payload`) — the NOW seam / wordmark carrier
    # when no run is live. Same piggyback economics as
    # `spawn_max_concurrent` above.
    daemon_mood: DaemonMoodIn | None = None


class DaemonMoodIn(BaseModel):
    """The daemon's derived telemetry face (#566 slice 0)."""

    state: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    glyph: str = Field(min_length=1, max_length=16)
    frames: list[str] = Field(default_factory=list, max_length=8)
    # The alternates, on the same shape `LiveRunIn.mood_frames` uses. `frames`
    # stays the primary cycle for a dashboard deployed before this existed.
    sequences: list[list[str]] | None = Field(default=None, max_length=4)
    rest: str | None = Field(default=None, max_length=16)
    pitch: float = Field(ge=0.0, le=1.0)

    @field_validator("frames")
    @classmethod
    def _bound_frames(cls, value: list[str]) -> list[str]:
        return [str(frame)[:16] for frame in value[:8]]

    @field_validator("sequences")
    @classmethod
    def _bound_sequences(
        cls, value: list[list[str]] | None,
    ) -> list[list[str]] | None:
        if value is None:
            return None
        bounded = [
            [str(frame)[:16] for frame in seq[:8]]
            for seq in value[:4]
            if isinstance(seq, list) and seq
        ]
        return [seq for seq in bounded if seq] or None


class RunStopRequestOut(BaseModel):
    """A user-issued "stop that run" (#476 wyrd §3)."""

    request_id: str
    run_id: str
    requested_at: datetime | None = None
    status: str


class LiveRunRejection(BaseModel):
    """One live-run row the server refused, and why (#685 ask 2, guard C).

    An absent reading renders as "fine" (#632): a silently dropped row is how
    this comes back. The daemon prints these on its own success path
    (`src/brr/gates/cloud.py::_publish_live_runs`).
    """

    index: int
    # Best-effort — the row's own `id` is often the thing that failed.
    id: str | None = None
    fields: list[str] = Field(default_factory=list)
    detail: str = ""


class LiveRunsIntake(BaseModel):
    """What per-row validation kept, cut, and threw away."""

    runs: list[LiveRunIn] = Field(default_factory=list)
    rejected: list[LiveRunRejection] = Field(default_factory=list)
    # `"<row id>.<field>"` for every display value the server shortened.
    truncated: list[str] = Field(default_factory=list)


def isolate_live_runs(rows: list[Any]) -> LiveRunsIntake:
    """Validate live-run rows one at a time, so one bad row costs one row.

    #685 ask 2, the isolation half — the load-bearing one. Display fields are
    already truncated by `LiveRunIn._truncate_display_fields`, so the only
    rows that fail here are ones whose *identity* is unusable, and a row
    without a usable join key is a row nothing can render anyway.

    Truncation is detected by diffing the raw row against the validated model
    over `LiveRunIn.string_bounds()`, not by a second copy of the cap list.
    """
    kept: list[LiveRunIn] = []
    rejected: list[LiveRunRejection] = []
    truncated: list[str] = []
    bounds = LiveRunIn.string_bounds()
    for index, row in enumerate(rows):
        try:
            model = LiveRunIn.model_validate(row)
        except ValidationError as exc:
            raw_id = row.get("id") if isinstance(row, dict) else None
            rejected.append(
                LiveRunRejection(
                    index=index,
                    id=str(raw_id)[:64] if isinstance(raw_id, str) else None,
                    fields=sorted({
                        str(error["loc"][0])
                        for error in exc.errors()
                        if error.get("loc")
                    }),
                    detail="; ".join(
                        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                        for error in exc.errors()[:4]
                    )[:500],
                )
            )
            continue
        kept.append(model)
        if isinstance(row, dict):
            for field in bounds:
                before = row.get(field)
                if isinstance(before, str) and before != getattr(model, field, before):
                    truncated.append(f"{model.id}.{field}")
    return LiveRunsIntake(runs=kept, rejected=rejected, truncated=truncated)


class LiveRunsOut(BaseModel):
    runs: list[LiveRunIn]
    live_runs_updated_at: datetime | None = None
    spawn_max_concurrent: int | None = None
    # #685 ask 2, guard C — additive, so a dashboard deployed before this
    # existed is unaffected. Rows the server refused and display values it
    # shortened, said out loud on the success path rather than left for a
    # reader to infer from a row that quietly stopped appearing.
    runs_rejected: list[LiveRunRejection] = Field(default_factory=list)
    fields_truncated: list[str] = Field(default_factory=list)
    # Piggyback channel: the account's pending run stops ride back on the
    # daemon's own live-runs publish tick, so a tap reaches the kill path
    # within one tick without a new polling loop.
    pending_run_stop_requests: list[RunStopRequestOut] = Field(default_factory=list)


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
    name: str | None = None
    external_refs: list[Any] | None = None
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
    # Which Runner substitution happened at dispatch and why — the rack's
    # substitution display reads this (`runLedger.ts`); a row without it
    # renders as "dispatched as asked" even when it wasn't.
    substitution_reason: str | None = None
    # #743's five-value channel attribution ("gate-sole" / "dispatch-edge" /
    # …) — debug detail, mirrored so the receipt a human drills into agrees
    # with the local ledger.
    terminal_route: str | None = None
    # The bolt (design-the-bolt.md, fork 4 signed): "accepted" / "annotated"
    # / absent. The dashboard's summons strip and cloth-head lane key on
    # this field — a model that drops it renders every cut run as boltless
    # (the 2026-08-08 defect: writer and reader both shipped, this schema
    # silently stripped the field at the PUT).
    bolt: str | None = None
    # The bounded declaration accepted by ``cut:``.  Absent on legacy rows;
    # an explicit ``{"omitted": true, ...}`` means the source declaration
    # exceeded persistence caps and was skipped whole, never truncated.
    bolt_declaration: dict[str, Any] | None = None

    # Deliberately NOT mirrored from the local row (tests/test_run_ledger_
    # mirror_parity.py enforces that this omission stays a decision, not a
    # hole):
    #   reply_archive — a host-local filesystem path; no cloud reader, and
    #   mirroring host paths into the dashboard store leaks layout for
    #   nothing.


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
    # #525 — attachment pointers, same shape the telegram webhook captures.
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class DevEnqueued(BaseModel):
    event_id: str
    seq: int


# --- billing (#53) -----------------------------------------------------------


class SubscriptionOut(BaseModel):
    tier: str
    status: str | None = None
    cohort: str | None = None
    cadence: str | None = None
    cancel_at_period_end: bool = False
    current_period_end: datetime | None = None


class SubscriptionCheckoutIn(BaseModel):
    cadence: str = Field(default="monthly", pattern="^(monthly|annual)$")


class CheckoutOut(BaseModel):
    checkout_url: str
    cohort: str | None = None


class PortalOut(BaseModel):
    portal_url: str


class WalletOut(BaseModel):
    balances: dict[str, int] = Field(default_factory=dict)
    total_credits: int = 0
    cumulative_purchased_credits_lifetime: int = 0


class TopupCheckoutIn(BaseModel):
    amount_usd: int


class BillingLedgerEntryOut(BaseModel):
    seq: int
    op: str
    credits_delta: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class BillingLedgerList(BaseModel):
    entries: list[BillingLedgerEntryOut]


# --- account deletion (Art 17) ------------------------------------------------


class AccountDeletionConfirm(BaseModel):
    """The re-typed confirmation step — the account's own GitHub login,
    matched exactly (case-sensitive) server-side. No token round-trip: the
    typed match *is* the confirmation, the same convention GitHub itself
    uses for "type the repo name to confirm"."""

    confirm_login: str = Field(min_length=1, max_length=255)


class RetainedStoreOut(BaseModel):
    store: str
    reason: str


class AccountDeletionOut(BaseModel):
    ok: bool = True
    deleted_at: datetime
    stripe_subscription_canceled: bool
    retained: list[RetainedStoreOut] = Field(default_factory=list)
