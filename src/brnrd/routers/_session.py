"""Shared session-auth and helper functions for the web-facing routers.

Extracted from ``brnrd_web/routes.py`` when ``src/brnrd_web`` was merged
into ``src/brnrd/routers/`` — the auth cookie contract, datetime helpers,
and repo-action cores are used by both ``dashboard.py`` and ``web_auth.py``.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from brnrd import github_marker, ids, limits, oauth, publish_scope, terms
from brnrd.auth import account_id_from_session_cookie, get_db  # noqa: F401  re-exported so callers can import from here
from brnrd.models import (
    Account,
    ActivityRecord,
    ChannelRoute,
    ConfigChangeRequest,
    Daemon,
    Event,
    GitHubInstallation,
    GitHubInstalledRepo,
    PairRequest,
    Repo,
    TermsAcceptance,
    TgPairCode,
    Token,
)
from brnrd.routers.accounts import SESSION_TTL, account_for_github_identity, issue_session_token  # noqa: F401
from brnrd.routers.github_app import (
    github_sync_notice,
    sync_app_installations_for_account,
)
from brnrd.routers.pairing import approve_core, telegram_pair_core
from brnrd.security import hash_token

_GITHUB_AUTO_SYNC_AFTER = timedelta(minutes=15)
# #874 — the coarse re-check: no new scheduler, piggybacked on the same
# staleness-gated recheck-on-dashboard-load pattern the installation sync
# below uses, so an invite that arrives after both bind and installation
# sync still gets caught within one dashboard visit's staleness window.
_GITHUB_MARKER_RECHECK_AFTER = timedelta(minutes=15)
_DAEMON_ONLINE_AFTER = timedelta(minutes=2)
# `_HOSTED_TERMS_VERSION` used to live here as a literal. It is gone (#735):
# a version now belongs to its document in `brnrd.terms`, next to the pinned
# text it labels, so the two cannot drift. Read `terms.current(kind).version`.

# Re-export for callers that previously imported from brnrd_web.routes
__all__ = [
    "_account_id",
    "_accepted_terms",
    "_age_label",
    "_clear_oauth_cookies",
    "_connect_repo_core",
    "_cookie_secure",
    "_disconnect_repo_core",
    "_document_accept_url",
    "_document_status",
    "_dt",
    "_general_terms_accept_url",
    "_github_background_refresh_needed",
    "_github_oauth_ready",
    "_github_sync_configured",
    "_installations",
    "_installed_repos",
    "_json_account",
    "_json_body",
    "_needs_terms",
    "_notice_text",
    "_oauth_redirect_uri",
    "_pair_repo_telegram_core",
    "_payload_str",
    "_repo_action_response",
    "_repo_error_response",
    "_repo_parts",
    "_repo_views",
    "_repos",
    "_resolve_or_create_repo_for_pair",
    "_safe_next",
    "_set_repo_publish_layers_core",
    "_start_github_background_refresh",
    "_terms_accept_url",
    "_terms_status",
    "_time_label",
    "_DAEMON_ONLINE_AFTER",
    "_GITHUB_AUTO_SYNC_AFTER",
    "_GITHUB_MARKER_RECHECK_AFTER",
]


def _dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _age_label(value: datetime | None) -> str:
    value = _dt(value)
    if value is None:
        return "never"
    seconds = max(0, int((datetime.now(timezone.utc) - value).total_seconds()))
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _time_label(value: datetime | None) -> str:
    value = _dt(value)
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _repos(db: Session, account_id: str) -> list[Repo]:
    return list(db.execute(select(Repo).where(Repo.account_id == account_id)).scalars())


def _installations(db: Session, account_id: str) -> list[GitHubInstallation]:
    return list(
        db.execute(
            select(GitHubInstallation)
            .where(GitHubInstallation.account_id == account_id)
            .order_by(GitHubInstallation.target_login)
        ).scalars()
    )


def _installed_repos(db: Session, account_id: str) -> list[GitHubInstalledRepo]:
    out: list[GitHubInstalledRepo] = []
    for installation in _installations(db, account_id):
        out.extend(db.execute(select(GitHubInstalledRepo).where(GitHubInstalledRepo.github_installation_id == installation.id)).scalars())
    return sorted(
        out,
        key=lambda r: (_dt(r.github_pushed_at) or _dt(r.github_updated_at) or _dt(r.last_seen_at) or datetime.min.replace(tzinfo=timezone.utc), r.repo_full_name.casefold()),
        reverse=True,
    )


def _account_channel_directory(db: Session, account_id: str) -> list[dict[str, Any]]:
    """The channel directory for one *account* — one query, no N+1, and
    account-first rather than repo-first (2026-08-05 steer, mid-flight on
    brr/the-directory-reaches-the-wire: "one directory list... the per
    project approach as it is now is quite dumb").

    ``ChannelRoute`` carries both ``account_id`` and ``repo_id`` — a route is
    an account-level address that *optionally* names a repo, not a row filed
    under one. This reads every route for the account, unfiltered by which
    repos are "in play", so ``repo_id`` rides through per row (``None`` for
    an account-wide route bound to no particular repo) rather than being the
    key rows are grouped under before a caller ever sees them; every other
    read in this module that needs a channel view derives from this same
    list (see ``_paired_channels_by_repo``) instead of re-querying.

    A ``ChannelRoute`` with a NULL ``paired_user_id`` authorizes nobody
    (``models.py`` ~line 339: rows created before #409 shipped predate the
    principal column) and must not count as paired (#885) — that rule is a
    property of *pairing*, not of Telegram, so it applies identically to
    every platform here, not just the one #885 was filed against.

    The platform vocabulary is never enumerated by this function — each
    row's ``platform`` is exactly whatever ``ChannelRoute.platform`` already
    holds, so a new transport shows up the moment its first route is
    written, with no edit here.

    **Schema note, checked rather than assumed:** ``ChannelRoute.repo_id``
    is ``Mapped[str]`` (NOT NULL) today, and no migration has relaxed it —
    compare ``daemons.repo_id``, which *did* get an explicit
    ``ALTER COLUMN ... DROP NOT NULL`` (#migrating account-scoped daemons)
    that ``channel_routes`` never received. Both write sites
    (``webhooks.py`` — ``_handle_telegram_pair``, ``_handle_whatsapp_pair``)
    always pass a ``repo_id`` sourced from a repo-scoped ``TgPairCode``, so
    no row with ``repo_id IS NULL`` exists in practice and every row read
    here is 100% repo-bound today. The account-first shape below is
    forward-compatible with a future schema change, not a response to data
    that exists yet — flagged rather than silently assumed either way.
    """
    return [
        {"platform": platform, "paired": paired_user_id is not None, "repo_id": repo_id}
        for repo_id, platform, paired_user_id in db.execute(
            select(ChannelRoute.repo_id, ChannelRoute.platform, ChannelRoute.paired_user_id).where(
                ChannelRoute.account_id == account_id
            )
        )
    ]


def _paired_channels_by_repo(directory: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group an account channel directory (``_account_channel_directory``) by
    repo — the per-repo ``channels`` shape ``_repo_views`` needs, derived
    from the one account-level list rather than a second query.

    Rows are aggregated to one ``paired`` bit per ``(repo, platform)`` pair.
    A repo/platform pair with *no* row at all is simply absent from that
    repo's list — distinct from a row that exists with ``paired: False`` —
    so a caller can tell "never attempted on this platform" from
    "attempted, not (yet) paired".

    A row with ``repo_id is None`` (account-wide, bound to no particular
    repo) is not filed under any repo here — it belongs to the account-level
    list a future consumer would read from ``_account_channel_directory``
    directly, not to any one repo's directory. See that function's schema
    note: no such row exists today, so this branch is currently a no-op,
    not a silent drop of live data.
    """
    paired_by_repo_platform: dict[tuple[str, str], bool] = {}
    for row in directory:
        repo_id = row["repo_id"]
        if repo_id is None:
            continue
        key = (repo_id, row["platform"])
        paired_by_repo_platform[key] = paired_by_repo_platform.get(key, False) or row["paired"]
    out: dict[str, list[dict[str, Any]]] = {}
    for (repo_id, platform), paired in paired_by_repo_platform.items():
        out.setdefault(repo_id, []).append({"platform": platform, "paired": paired})
    for channels in out.values():
        channels.sort(key=lambda c: c["platform"])
    return out


# The daemon-pairing command, spelled once (2026-08-03). It is printed on
# two surfaces now — per-repo behind `setup command` on /repos, and in the
# dashboard's cold-start block, which an account with nothing connected sees
# and which therefore has no repo row to carry a `setup_command`. Two copies
# of one command line drift apart the first time the CLI renames a verb, so
# the zero-repo spelling is served from here too (`pairing_command` on
# `GET /v1/dashboard/repos`) rather than re-typed in the frontend.
#
# The endpoint stays the literal it has always been rather than
# `settings.public_base_url`: that default is `http://localhost:8000`, and a
# self-hosted control plane printing its own loopback address as the thing to
# pair against would be a regression on the one string that has to be right.
_PAIR_ENDPOINT = "https://brnrd.dev"

# What the cold-start block puts where a real checkout's name would go.
PAIR_REPO_PLACEHOLDER = "<repo>"


def pairing_command(repo_dir: str) -> str:
    """The two lines that pair a local daemon to this control plane.

    Used to be three: a trailing ``brnrd up``. Dropped 2026-08-04 (#1084) —
    ``cmd_brnrd_connect`` (``src/brr/cli.py``) already calls
    ``daemon_install.install(no_start=False, ...)`` unless ``--no-service``,
    which installs *and starts* the native service and prints its own
    "Connected and listening in the background." ``brnrd up`` on the
    default path only re-starts what line 2 just started — a redundant
    third step in the one command a first-time reader has to get right.
    """
    return f"cd {repo_dir}\nbrnrd account connect {_PAIR_ENDPOINT}"


def _repo_views(db: Session, repos: list[Repo]) -> list[dict]:
    import json

    repo_ids = [r.id for r in repos]
    daemons_by_repo: dict[str, list[Daemon]] = {r.id: [] for r in repos}
    daemon_rows: list[Daemon] = []
    if repo_ids:
        daemon_rows = list(
            db.execute(select(Daemon).where(Daemon.repo_id.in_(repo_ids))).scalars()
        )
        for daemon in daemon_rows:
            daemons_by_repo.setdefault(daemon.repo_id, []).append(daemon)

    # Account-first (see `_account_channel_directory`): one query for the
    # whole account's channel directory, then grouped by repo in Python —
    # never a second, repo-scoped query. `repos` is always one account's
    # repos (the sole caller is `dashboard_repos_api`, `_repos(db, account.id)`
    # straight into this function), so any repo's `account_id` names it.
    channels_by_repo = (
        _paired_channels_by_repo(_account_channel_directory(db, repos[0].account_id))
        if repos
        else {}
    )

    reported_daemons = [daemon for daemon in daemon_rows if _dt(daemon.runners_updated_at)]
    dispatch_default_repo_id = (
        max(reported_daemons, key=lambda daemon: _dt(daemon.runners_updated_at)).repo_id
        if reported_daemons
        else None
    )

    now = datetime.now(timezone.utc)
    views: list[dict] = []
    for repo in repos:
        channels = channels_by_repo.get(repo.id, [])
        daemons = daemons_by_repo.get(repo.id, [])
        latest = max(daemons, key=lambda d: _dt(d.last_seen_at) or datetime.min.replace(tzinfo=timezone.utc), default=None)
        online = any(d.online and _dt(d.last_seen_at) and now - _dt(d.last_seen_at) <= _DAEMON_ONLINE_AFTER for d in daemons)
        # #1243 — `last_seen_at` is stamped at *registration* (`daemons.py`
        # `register()`), not at a daemon's first successful publish cycle,
        # so a daemon that paired and then crash-looped without ever
        # breathing (`daemon.start` refuses without `AGENTS.md`, #1238's
        # exact trace) reads no differently from one that ran fine and went
        # quiet: both show `latest is not None`, and the label below would
        # call registration a "heartbeat" it never sent. `runners_updated_at`
        # is only ever written by `PUT /v1/daemons/runners`, from inside the
        # publish loop that a crash-looping process never reaches (same
        # signal `dispatch_default_repo_id` above already trusts for "has
        # this daemon ever really run") — so its presence is the fact that
        # tells the two states apart.
        ever_ran = latest is not None and _dt(latest.runners_updated_at) is not None
        if online:
            daemon_status = "online"
            daemon_label = "Local daemon online"
        elif latest is not None and ever_ran:
            daemon_status = "offline"
            daemon_label = "Local daemon not running"
        elif latest is not None:
            daemon_status = "never_started"
            daemon_label = "Paired, never started"
        else:
            daemon_status = "missing"
            daemon_label = "Waiting for local daemon"
        last_activity = _dt(latest.last_seen_at if latest else None) or _dt(repo.updated_at) or _dt(repo.created_at)
        gate_health: list[dict] = []
        if latest is not None:
            try:
                parsed_health = json.loads(latest.gate_health_json or "[]")
                if isinstance(parsed_health, list):
                    gate_health = [row for row in parsed_health if isinstance(row, dict)]
            except (TypeError, ValueError):
                pass
        environments: list[dict] = []
        if latest is not None:
            try:
                parsed_environments = json.loads(latest.environments_json or "[]")
                if isinstance(parsed_environments, list):
                    environments = [row for row in parsed_environments if isinstance(row, dict)]
            except (TypeError, ValueError):
                pass
        views.append(
            {
                "repo": repo,
                "dispatch_default": repo.id == dispatch_default_repo_id,
                "channels": channels,
                # DEPRECATED (brr/the-directory-reaches-the-wire): superseded
                # by `channels` above, which carries every platform rather
                # than collapsing the directory to one Telegram-shaped bit.
                # Kept, not removed: `dashboard.py`'s `_repo_view_out` and the
                # frontend (`src/frontend/src/lib/repos.ts`,
                # `/repos/+page.svelte`) both still read this exact key and
                # neither is in scope for this branch (backend-only,
                # frontend owned by a sibling strand) — removing it here
                # would silently break both without anyone touching them.
                # Safe to derive rather than track separately: identical to
                # the old query's own definition, "the telegram platform has
                # at least one paired route".
                "telegram_paired": any(c["platform"] == "telegram" and c["paired"] for c in channels),
                "daemon_count": len(daemons),
                "daemon_status": daemon_status,
                "daemon_label": daemon_label,
                "daemon_last_seen": _age_label(latest.last_seen_at if latest else None),
                "daemon_last_seen_at": _dt(latest.last_seen_at if latest else None),
                "latest_daemon_name": latest.daemon_name if latest else "",
                "gates": gate_health,
                "environment_default": latest.environment_default if latest else None,
                "environments": environments,
                "setup_command": pairing_command(repo.repo_name),
                "sort_time": last_activity or datetime.min.replace(tzinfo=timezone.utc),
            }
        )
    return sorted(views, key=lambda v: (v["daemon_status"] == "online", v["sort_time"], v["repo"].repo_full_name.casefold()), reverse=True)


def _github_sync_configured(request: Request) -> bool:
    settings = request.app.state.settings
    return bool(settings.github_app_id and settings.github_app_private_key_b64)


def _github_oauth_ready(request: Request) -> bool:
    s = request.app.state.settings
    return bool(s.github_oauth_client_id and s.github_oauth_client_secret)


# #885 — the repos GET used to run installation sync (paginated App-API repo
# listing) and per-repo collaborator checks inline, whenever either was stale
# by `_GITHUB_AUTO_SYNC_AFTER` / `_GITHUB_MARKER_RECHECK_AFTER`; on an account
# with dozens of repos that is the reported ~minute page load, and it also
# smuggled an unrelated "GitHub installations synced." notice onto a plain
# load. The GET now only *decides* staleness (cheap reads on the request's
# own session, below) and hands the actual sync work to a background thread
# with its own DB session — the response carries current DB state and
# never waits on GitHub.
_GITHUB_BACKGROUND_SYNC_LOCK = threading.Lock()
_GITHUB_BACKGROUND_SYNC_IN_PROGRESS: set[str] = set()


def _github_installation_sync_stale(db: Session, account_id: str) -> bool:
    installations = _installations(db, account_id)
    installed_repos = _installed_repos(db, account_id)
    if not installed_repos or not installations:
        return True
    now = datetime.now(timezone.utc)
    return any(
        _dt(installation.last_synced_at) is None
        or now - _dt(installation.last_synced_at) > _GITHUB_AUTO_SYNC_AFTER
        for installation in installations
    )


def _github_marker_sync_stale(db: Session, account_id: str) -> bool:
    repos = _repos(db, account_id)
    if not repos:
        return False
    now = datetime.now(timezone.utc)
    return any(
        _dt(repo.github_bot_checked_at) is None
        or now - _dt(repo.github_bot_checked_at) > _GITHUB_MARKER_RECHECK_AFTER
        for repo in repos
    )


def _github_background_refresh_needed(request: Request, db: Session, account_id: str) -> bool:
    """Whether the dashboard GET should kick a background refresh thread.

    Reads only — the actual sync happens in `_run_github_background_refresh`,
    off-thread, with its own session.
    """
    settings = request.app.state.settings
    installation_stale = _github_sync_configured(request) and _github_installation_sync_stale(db, account_id)
    marker_stale = bool(settings.github_bot_token) and _github_marker_sync_stale(db, account_id)
    return installation_stale or marker_stale


def _run_github_background_refresh(session_factory, settings, account_id: str) -> None:
    """Daemon-thread body: installation sync + the #874 marker re-check.

    Its own DB session from `session_factory` (`app.state.SessionLocal`) —
    never the request's, which has already returned a response by the time
    this runs. Failures log and stop; nothing is waiting on this thread to
    surface them.
    """
    try:
        with session_factory() as db:
            if _github_installation_sync_stale(db, account_id):
                try:
                    sync_app_installations_for_account(db, settings, account_id)
                except Exception as e:
                    print(f"[brnrd] github dashboard background sync failed: {e}")
            if settings.github_bot_token:
                repos = _repos(db, account_id)
                now = datetime.now(timezone.utc)
                stale = [
                    r
                    for r in repos
                    if _dt(r.github_bot_checked_at) is None
                    or now - _dt(r.github_bot_checked_at) > _GITHUB_MARKER_RECHECK_AFTER
                ]
                if stale:
                    try:
                        github_marker.sync_marker_for_repos(db, settings, stale)
                    except Exception as e:
                        print(f"[brnrd] github marker background recheck failed: {e}")
    finally:
        with _GITHUB_BACKGROUND_SYNC_LOCK:
            _GITHUB_BACKGROUND_SYNC_IN_PROGRESS.discard(account_id)


def _start_github_background_refresh(request: Request, account_id: str) -> None:
    """Fire-and-forget refresh, single-flight per account.

    A second GET for the same account while a sync is already in flight is a
    no-op — the module-level set is the single-flight lock, checked and set
    atomically so two concurrent requests can't both pass the gate.
    """
    with _GITHUB_BACKGROUND_SYNC_LOCK:
        if account_id in _GITHUB_BACKGROUND_SYNC_IN_PROGRESS:
            return
        _GITHUB_BACKGROUND_SYNC_IN_PROGRESS.add(account_id)
    thread = threading.Thread(
        target=_run_github_background_refresh,
        args=(request.app.state.SessionLocal, request.app.state.settings, account_id),
        name=f"github-refresh-{account_id}",
        daemon=True,
    )
    thread.start()


def _notice_text(value: str | None) -> str | None:
    return {
        "repo-connected": "Repo enabled. Set up a local brnrd daemon to start draining work.",
        "repo-disconnected": "Repo disconnected from brnrd.",
        "repo-publish-scope-updated": "Publish scope updated.",
        "github-synced": "GitHub installations synced.",
        "github-installed": "GitHub installation received.",
        "github-install-requested": "Installation requested — waiting on an organization admin to approve it before repos can appear.",
        "github-sync-empty": "No GitHub App installation found yet — install the GitHub App to sync repos.",
        "github-sync-partial": "Owned GitHub installations synced; other installations were refused.",
        "github-sync-refused": "No GitHub installations could be verified for this account.",
        "github-sync-failed": "GitHub installation sync failed. Check app id/private-key config and logs.",
    }.get(value or "", value)


def _account_id(request: Request, db: Session) -> str | None:
    """Thin alias for the shared cookie resolver in ``brnrd.auth``.

    Kept as a name because this module's callers (and tests) reach for
    ``_session._account_id``; the predicate itself lives in one place now.
    """
    return account_id_from_session_cookie(request, db)


def _json_account(request: Request, db: Session) -> Account:
    account_id = _account_id(request, db)
    if account_id is None:
        raise HTTPException(status_code=401, detail="unauthenticated")
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=401, detail="unauthenticated")
    return account


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _payload_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else str(value or "").strip()


def _repo_action_response(notice: str, *, ok: bool = True, status_code: int = 200, **extra: Any):
    from fastapi.responses import JSONResponse

    body = {"ok": ok, "notice": _notice_text(notice) or notice}
    body.update(extra)
    return JSONResponse(body, status_code=status_code)


def _repo_error_response(exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        # limits.raise_if_denied carries {"reason", "message"} — keep the
        # machine-readable reason in the JSON body, show the message.
        text = str(detail.get("message") or detail.get("reason") or "request failed")
        return _repo_action_response(
            text, ok=False, status_code=exc.status_code, reason=detail.get("reason")
        )
    return _repo_action_response(str(detail or "request failed"), ok=False, status_code=exc.status_code)


def _repo_parts(repo_full_name: str) -> tuple[str, str]:
    owner, sep, name = repo_full_name.strip().partition("/")
    if not sep or not owner or not name:
        raise HTTPException(status_code=400, detail="repo must look like owner/name")
    return owner, name


def _connect_repo_core(
    request: Request,
    db: Session,
    account: Account,
    *,
    repo_full_name: str,
    forge: str = "github",
    forge_repo_id: str = "",
    default_branch: str = "",
    publish_layers: str | None = None,
) -> str:
    repo_full_name = repo_full_name.strip()
    owner, name = _repo_parts(repo_full_name)
    repo = db.execute(select(Repo).where(Repo.account_id == account.id, Repo.repo_full_name == repo_full_name)).scalar_one_or_none()
    if repo is None:
        # #501 repo cap — new connections only; reconnects stay idempotent.
        limits.raise_if_denied(
            limits.check_repo_connect(db, request.app.state.settings, account)
        )
        # Explicit publish-scope consent (legal pack item 2, #417 follow-on):
        # captured once, at creation, never silently touched by a later
        # idempotent reconnect. The product default for a brand-new connect
        # is off (`publish_scope.DEFAULT_NEW_CONNECT`) — a client that omits
        # the field gets the safe default, not the daemon-config "absent
        # means everything" rule, which is a legacy convenience, not consent.
        # `normalize_publish_layers` 4xxes on an unrecognised token rather
        # than silently accepting it — the whole point of a schema-validated
        # consent is that a typo cannot pass as a choice.
        repo = Repo(
            id=ids.repo_id(),
            account_id=account.id,
            forge=forge or "github",
            repo_full_name=repo_full_name,
            repo_owner=owner,
            repo_name=name,
            publish_layers=publish_scope.normalize_publish_layers(
                publish_layers if publish_layers is not None else publish_scope.DEFAULT_NEW_CONNECT
            ),
        )
        db.add(repo)
    repo.forge_repo_id = forge_repo_id or repo.forge_repo_id
    repo.default_branch = default_branch or repo.default_branch
    repo.updated_at = datetime.now(timezone.utc)
    db.commit()
    # #874 — bind is one of the two moments an invite can already be
    # sitting pending (the other is installation sync, `github_app.py`).
    # Best-effort: a marker failure must never fail the connect itself.
    # A non-github forge has no GitHub repo behind it to check — calling
    # this anyway would spend an API round trip probing a name GitHub was
    # never going to recognise and could leave a "not found"-shaped notice
    # sitting on a repo row that was never supposed to have an opinion
    # about GitHub at all.
    if repo.forge == "github":
        try:
            github_marker.sync_marker_for_repos(db, request.app.state.settings, [repo])
        except Exception as exc:
            print(f"[brnrd] github marker sync failed for {repo.repo_full_name}: {exc}")
    return "repo-connected"


def _resolve_or_create_repo_for_pair(
    request: Request, db: Session, account: Account, pair: PairRequest
) -> Repo | None:
    """Bind a pairing daemon to its own checkout's repo, creating it if this
    account has never connected it before — the local half of "running the
    pairing command *is* the enable step" (no more separate website click).

    Returns ``None`` when the pair carries no usable ``repo_full_name``
    (older CLI, or `brnrd account connect` run outside a git checkout) —
    the caller falls back to the pre-existing dropdown-of-connected-repos
    flow in that case, unchanged.
    """
    from .pairing import pair_capabilities, pair_suggested_repo_full_name

    repo_full_name = pair_suggested_repo_full_name(pair)
    if not repo_full_name:
        return None
    try:
        _repo_parts(repo_full_name)
    except HTTPException:
        return None
    existing = db.execute(
        select(Repo).where(Repo.account_id == account.id, Repo.repo_full_name == repo_full_name)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    caps = pair_capabilities(pair)
    default_branch = caps.get("default_branch", "")
    # The connecting checkout names its own forge ("github" / "local");
    # anything else sent (an older CLI never will, but a forged payload
    # could) falls back to "github" rather than persisting an unrecognised
    # word — `_repo_parts` already requires `owner/name` regardless, so a
    # bogus forge label is the only thing at stake here.
    forge = caps.get("forge", "")
    forge = forge if forge in ("github", "local") else "github"
    # Same cap check, same default (safe/private) publish scope as the
    # retired manual "enable" click — creating a repo through the pairing
    # handshake is not a wider door than creating one by hand ever was.
    _connect_repo_core(
        request,
        db,
        account,
        repo_full_name=repo_full_name,
        forge=forge,
        default_branch=default_branch if isinstance(default_branch, str) else "",
        publish_layers=None,
    )
    return db.execute(
        select(Repo).where(Repo.account_id == account.id, Repo.repo_full_name == repo_full_name)
    ).scalar_one_or_none()


def _set_repo_publish_layers_core(db: Session, account_id: str, repo_id: str, publish_layers: str) -> str:
    """Revisit a repo's publish-scope consent after connect (settings surface).

    Same validator as connect, so the same token vocabulary and the same
    loud 4xx on a typo apply whether the choice is made at connect time or
    later. Account-scoped lookup: a 404 here, not a silent no-op, on any
    other account's repo id.
    """
    repo = db.execute(select(Repo).where(Repo.id == repo_id, Repo.account_id == account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    normalized = publish_scope.normalize_publish_layers(publish_layers)
    # No commit inside the purge: the removed mirror content and the consent
    # column move together or neither does (#734, GDPR Art 7(3)).
    publish_scope.purge_removed_scope(
        db,
        repo=repo,
        new_publish_layers=normalized,
    )
    repo.publish_layers = normalized
    repo.updated_at = datetime.now(timezone.utc)
    db.commit()
    return "repo-publish-scope-updated"


def _pair_repo_telegram_core(request: Request, db: Session, account_id: str, repo_id: str):
    return telegram_pair_core(db, request.app.state.settings, account_id, repo_id)


def _disconnect_repo_core(db: Session, account_id: str, repo_id: str) -> str:
    repo = db.execute(select(Repo).where(Repo.id == repo_id, Repo.account_id == account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    # Every repo-FK table, ordered so nothing is deleted while a surviving row
    # still references it: ActivityRecord points at tokens/daemons, Daemon at
    # tokens (#502 — ActivityRecord and ConfigChangeRequest were missing here,
    # so a disconnect with live activity rows died on the FK).
    for model in (ActivityRecord, ConfigChangeRequest, Daemon, Event, ChannelRoute, TgPairCode, PairRequest, Token):
        db.execute(delete(model).where(model.repo_id == repo.id))
    db.delete(repo)
    # #502: the corpus mirror is account-level; when the last repo disconnects
    # nothing legitimately renders it anymore, so the copy must not outlive
    # the connection.
    remaining = db.execute(
        select(Repo.id).where(Repo.account_id == account_id, Repo.id != repo.id).limit(1)
    ).scalar_one_or_none()
    if remaining is None:
        account = db.get(Account, account_id)
        if account is not None:
            account.surface_json = "[]"
            account.surface_updated_at = datetime.now(timezone.utc)
    db.commit()
    return "repo-disconnected"


def _safe_next(value: str) -> str:
    """A destination that stays on this site.

    ``//host`` is the protocol-relative form everyone guards; ``/\\host`` is
    the one that gets missed. Browsers normalise a backslash to a forward
    slash in the authority position, so ``new URL('/\\evil.example', origin)``
    resolves to ``https://evil.example/`` — off-site, from a value that passes
    a naive ``startswith("/")`` check. The backend's own ``RedirectResponse``
    happens to survive it (Starlette percent-encodes the Location header),
    but this value is also handed to the frontend as a ``next=`` parameter and
    fed to ``window.location.assign``, which does not. Guard it here, at the
    single producer, rather than at each sink (#735).

    A percent-encoded control character in the query string (`/%0A/evil`)
    arrives here already decoded — FastAPI/Starlette resolve `%0A` to a
    literal newline before this function ever sees the value — so a
    same-site-looking `"/\n/evil.example"` passes the two checks above
    unscathed. This value later reaches a raw `RedirectResponse(url=...)`
    Location header and a cookie value with no further validation; reject
    any control character here, at the source, rather than at either sink.
    """
    if not value or not value.startswith("/"):
        return "/"
    if value[1:2] in ("/", "\\"):
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return "/"
    return value


def _document_accept_url(kind: str, next_url: str) -> str:
    """Where a caller sends someone to read and accept one document.

    Each document's acceptance widget lives on the page carrying that
    document's text, and nowhere else — ``/terms`` for the general Terms of
    Service, ``/beta-hosted-execution`` for the addendum. #569 is the rule
    this encodes: a checkbox may only record acceptance of the words next to
    it. That is why one URL producer takes the document as an argument rather
    than a caller picking a path.
    """
    from urllib.parse import quote

    return f"{terms.current(kind).accept_path}?next={quote(_safe_next(next_url), safe='/')}"


def _terms_accept_url(next_url: str) -> str:
    """Accept-URL for the hosted-execution addendum. See ``_document_accept_url``."""
    return _document_accept_url(terms.DOC_HOSTED, next_url)


def _general_terms_accept_url(next_url: str) -> str:
    """Accept-URL for the general Terms of Service. See ``_document_accept_url``."""
    return _document_accept_url(terms.DOC_TOS, next_url)


def _accepted_terms(db: Session, account_id: str, kind: str) -> TermsAcceptance | None:
    """This account's acceptance of the *current* version of ``kind``, if any.

    Scoped to the current version on purpose: superseded rows stay in the
    table as the evidence of what was in force then, and must not answer
    "has this user accepted what is on the page today".
    """
    return db.execute(
        select(TermsAcceptance)
        .where(
            TermsAcceptance.account_id == account_id,
            TermsAcceptance.document == kind,
            TermsAcceptance.version == terms.current(kind).version,
        )
        .order_by(TermsAcceptance.accepted_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _needs_terms(db: Session, account: Account, kind: str) -> bool:
    """Whether ``account`` still owes acceptance of the current ``kind``.

    Version-triggered, not hash-triggered. The ToS itself promises that the
    version at the top of the page identifies the current text (§15), so a
    typo fix must not re-prompt every account; the hash records what was
    actually shown, and the pinning test makes a text change a deliberate
    act. See ``brnrd.terms``.

    For ``DOC_HOSTED`` this stays the *point-of-use* predicate #664 made it:
    it says nothing about whether the account can reach hosted compute, so
    only a surface offering that feature should read it. ``DOC_TOS`` is the
    opposite and that difference is the whole distinction #664 drew — the
    general terms govern using brnrd.dev at all, which is a condition login
    can evaluate.
    """
    return _accepted_terms(db, account.id, kind) is None


def _document_status(db: Session, account: Account | None, kind: str) -> dict:
    """Current acceptance state for one document.

    ``needs_accept`` is meaningful only when the enclosing
    ``_terms_status`` says ``authenticated``.  Anonymous callers receive
    ``None`` — *unknown*, not *accepted* — because nobody is present to owe
    acceptance and a falsy answer to "do you still owe me a signature?" is
    indistinguishable from "no, you are clear" at every JS consumer (#690,
    merged as #807; this docstring predates it and said ``False``).
    """
    doc = terms.current(kind)
    row = _accepted_terms(db, account.id, kind) if account is not None else None
    accepted_at = row.accepted_at if row is not None else None
    if accepted_at is not None and accepted_at.tzinfo is None:
        accepted_at = accepted_at.replace(tzinfo=timezone.utc)
    return {
        "version": doc.version,
        # Published so a user can check for themselves that the page in front
        # of them is the text their record points at.
        "sha256": doc.sha256,
        "accept_url": doc.accept_path,
        "needs_accept": row is None if account is not None else None,
        "accepted_at": accepted_at.isoformat() if accepted_at is not None else None,
        "accepted_sha256": row.sha256 if row is not None else None,
    }


def _terms_status(db: Session, account: Account | None) -> dict:
    """Per-document acceptance state for the session's account.

    A map rather than the old flat ``needs_accept``/``terms_version`` pair:
    there are two documents now and a privacy notice plus a mentions légales
    are already named as owed, so "the terms" has stopped being one thing.

    Each document's ``needs_accept`` is meaningful only when
    ``authenticated`` is true. ``null`` means no account was present on this
    request, so the question does not apply. The correct consumer predicate
    is ``authenticated && needs_accept === false`` allows; anything else does
    not.
    """
    return {
        "authenticated": account is not None,
        "documents": {kind: _document_status(db, account, kind) for kind in terms.kinds()},
    }


def _oauth_redirect_uri(request: Request) -> str:
    return f"{request.app.state.settings.public_base_url.rstrip('/')}/auth/github/callback"


def _cookie_secure(request: Request) -> bool:
    return request.app.state.settings.public_base_url.lower().startswith("https://")


def _clear_oauth_cookies(resp, request: Request) -> None:
    s = request.app.state.settings
    for name in (s.oauth_state_cookie, s.oauth_pkce_cookie, s.oauth_next_cookie):
        resp.delete_cookie(name, samesite="lax", secure=_cookie_secure(request))
