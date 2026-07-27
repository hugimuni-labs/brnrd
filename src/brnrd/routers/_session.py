"""Shared session-auth and helper functions for the web-facing routers.

Extracted from ``brnrd_web/routes.py`` when ``src/brnrd_web`` was merged
into ``src/brnrd/routers/`` — the auth cookie contract, datetime helpers,
and repo-action cores are used by both ``dashboard.py`` and ``web_auth.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from brnrd import ids, limits, oauth, publish_scope, terms
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
from brnrd.routers.github_app import sync_app_installations_for_account
from brnrd.routers.pairing import approve_core, telegram_pair_core
from brnrd.security import hash_token

_GITHUB_AUTO_SYNC_AFTER = timedelta(minutes=15)
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
    "_github_auto_sync_if_needed",
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
    "_safe_next",
    "_set_repo_publish_layers_core",
    "_terms_accept_url",
    "_terms_status",
    "_time_label",
    "_DAEMON_ONLINE_AFTER",
    "_GITHUB_AUTO_SYNC_AFTER",
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

    reported_daemons = [daemon for daemon in daemon_rows if _dt(daemon.runners_updated_at)]
    dispatch_default_repo_id = (
        max(reported_daemons, key=lambda daemon: _dt(daemon.runners_updated_at)).repo_id
        if reported_daemons
        else None
    )

    now = datetime.now(timezone.utc)
    views: list[dict] = []
    for repo in repos:
        daemons = daemons_by_repo.get(repo.id, [])
        latest = max(daemons, key=lambda d: _dt(d.last_seen_at) or datetime.min.replace(tzinfo=timezone.utc), default=None)
        online = any(d.online and _dt(d.last_seen_at) and now - _dt(d.last_seen_at) <= _DAEMON_ONLINE_AFTER for d in daemons)
        if online:
            daemon_status = "online"
            daemon_label = "Local daemon online"
        elif latest is not None:
            daemon_status = "offline"
            daemon_label = "Local daemon not running"
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
                "daemon_count": len(daemons),
                "daemon_status": daemon_status,
                "daemon_label": daemon_label,
                "daemon_last_seen": _age_label(latest.last_seen_at if latest else None),
                "daemon_last_seen_at": _dt(latest.last_seen_at if latest else None),
                "latest_daemon_name": latest.daemon_name if latest else "",
                "gates": gate_health,
                "environment_default": latest.environment_default if latest else None,
                "environments": environments,
                "setup_command": f"cd {repo.repo_name}\nbrnrd account connect https://brnrd.dev\nbrnrd up",
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


def _github_auto_sync_if_needed(request: Request, db: Session, account_id: str) -> str | None:
    if not _github_sync_configured(request):
        return None
    installations = _installations(db, account_id)
    installed_repos = _installed_repos(db, account_id)
    now = datetime.now(timezone.utc)
    needs_sync = not installed_repos or not installations
    if not needs_sync:
        needs_sync = any(_dt(i.last_synced_at) is None or now - _dt(i.last_synced_at) > _GITHUB_AUTO_SYNC_AFTER for i in installations)
    if not needs_sync:
        return None
    try:
        count = sync_app_installations_for_account(db, request.app.state.settings, account_id)
    except Exception as e:
        print(f"[brnrd] github dashboard auto-sync failed: {e}")
        return "github-sync-failed"
    return "github-synced" if count else "github-sync-empty"


def _notice_text(value: str | None) -> str | None:
    return {
        "repo-connected": "Repo enabled. Set up a local brnrd daemon to start draining work.",
        "repo-disconnected": "Repo disconnected from brnrd.",
        "repo-publish-scope-updated": "Publish scope updated.",
        "github-synced": "GitHub installations synced.",
        "github-installed": "GitHub installation received.",
        "github-sync-empty": "No GitHub App installations were found for this app.",
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
            forge="github",
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
    return "repo-connected"


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
    """
    if not value or not value.startswith("/"):
        return "/"
    if value[1:2] in ("/", "\\"):
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
