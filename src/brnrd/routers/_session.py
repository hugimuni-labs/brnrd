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

from brnrd import ids, oauth
from brnrd.auth import get_db  # noqa: F401  re-exported so callers can import from here
from brnrd.models import (
    Account,
    ChannelRoute,
    ConfigChangeRequest,
    Daemon,
    Event,
    GitHubInstallation,
    GitHubInstalledRepo,
    PairRequest,
    Repo,
    TgPairCode,
    Token,
)
from brnrd.platforms import github_app as gh_app_client
from brnrd.routers.accounts import SESSION_TTL, account_for_github_identity, issue_session_token  # noqa: F401
from brnrd.routers.github_app import sync_app_installations_for_account
from brnrd.routers.pairing import approve_core, telegram_pair_core
from brnrd.security import hash_token

_GITHUB_AUTO_SYNC_AFTER = timedelta(minutes=15)
_DAEMON_ONLINE_AFTER = timedelta(minutes=2)
_HOSTED_TERMS_VERSION = "2026-07-08"

# Re-export for callers that previously imported from brnrd_web.routes
__all__ = [
    "_account_id",
    "_age_label",
    "_clear_oauth_cookies",
    "_connect_repo_core",
    "_cookie_secure",
    "_disconnect_repo_core",
    "_dt",
    "_ensure_bot_collaborator",
    "_github_auto_sync_if_needed",
    "_github_oauth_ready",
    "_github_sync_configured",
    "_installation_id_for_repo",
    "_installations",
    "_installed_repos",
    "_invite_repo_bot_core",
    "_json_account",
    "_json_body",
    "_needs_hosted_terms",
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
    "_terms_accept_url",
    "_terms_status",
    "_time_label",
    "_DAEMON_ONLINE_AFTER",
    "_GITHUB_AUTO_SYNC_AFTER",
    "_HOSTED_TERMS_VERSION",
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
    if repo_ids:
        for daemon in db.execute(select(Daemon).where(Daemon.repo_id.in_(repo_ids))).scalars():
            daemons_by_repo.setdefault(daemon.repo_id, []).append(daemon)

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
        views.append(
            {
                "repo": repo,
                "daemon_count": len(daemons),
                "daemon_status": daemon_status,
                "daemon_label": daemon_label,
                "daemon_last_seen": _age_label(latest.last_seen_at if latest else None),
                "daemon_last_seen_at": _dt(latest.last_seen_at if latest else None),
                "latest_daemon_name": latest.daemon_name if latest else "",
                "gates": gate_health,
                "setup_command": f"cd {repo.repo_name}\nbrnrd connect https://brnrd.dev\nbrnrd up",
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


def _installation_id_for_repo(db: Session, account_id: str, repo_full_name: str) -> str | None:
    return db.execute(
        select(GitHubInstallation.installation_id)
        .join(GitHubInstalledRepo, GitHubInstalledRepo.github_installation_id == GitHubInstallation.id)
        .where(GitHubInstallation.account_id == account_id, GitHubInstalledRepo.repo_full_name == repo_full_name)
    ).scalar_one_or_none()


def _ensure_bot_collaborator(request: Request, db: Session, account_id: str, repo: Repo) -> str:
    """Invite the human-facing GitHub bot user into the repo."""
    if repo.forge != "github":
        return "repo-connected"
    settings = request.app.state.settings
    username = settings.github_bot_user_login.strip().lstrip("@")
    if not username:
        return "repo-connected-bot-invite-skipped"
    installation_id = _installation_id_for_repo(db, account_id, repo.repo_full_name)
    if not installation_id:
        return "repo-connected-bot-invite-skipped"
    permission = (settings.github_bot_collaborator_permission or "triage").strip() or "triage"
    try:
        result = gh_app_client.invite_collaborator(settings, installation_id, repo.repo_full_name, username, permission=permission)
    except Exception as e:
        print(f"[brnrd] github bot user invite failed for {repo.repo_full_name}: {e}")
        return "repo-connected-bot-invite-failed"
    if result.get("status_code") == 204:
        return "repo-connected-bot-present"
    return "repo-connected-bot-invited"


def _notice_text(value: str | None) -> str | None:
    return {
        "repo-connected": "Repo enabled. Set up a local brnrd daemon to start draining work.",
        "repo-connected-bot-invited": "Repo enabled. brnrd invited the bot user for native GitHub mentions; accept the invitation as the bot user if needed.",
        "repo-connected-bot-present": "Repo enabled. The bot user is already visible to this repo.",
        "repo-connected-bot-invite-skipped": "Repo enabled. Could not find a synced installation for the bot-user invite.",
        "repo-connected-bot-invite-failed": "Repo enabled, but brnrd could not invite the bot user. Check GitHub App administration permission and logs.",
        "repo-bot-invited": "brnrd invited the bot user for this repo; accept the invitation as the bot user if needed.",
        "repo-bot-present": "The bot user is already visible to this repo.",
        "repo-bot-invite-skipped": "Could not find a synced installation for the bot-user invite.",
        "repo-bot-invite-failed": "Could not invite the bot user. Check GitHub App administration permission and logs.",
        "repo-disconnected": "Repo disconnected from brnrd.",
        "github-synced": "GitHub installations synced.",
        "github-installed": "GitHub installation received.",
        "github-sync-empty": "No GitHub App installations were found for this app.",
        "github-sync-failed": "GitHub installation sync failed. Check app id/private-key config and logs.",
    }.get(value or "", value)


def _account_id(request: Request, db: Session) -> str | None:
    cookie = request.cookies.get(request.app.state.settings.session_cookie)
    if not cookie:
        return None
    token = db.execute(select(Token).where(Token.token_hash == hash_token(cookie), Token.kind == Token.KIND_SESSION)).scalar_one_or_none()
    if token is None or token.revoked:
        return None
    if token.expires_at is not None:
        expires = token.expires_at if token.expires_at.tzinfo else token.expires_at.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            return None
    return token.account_id


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
    detail = str(exc.detail or "request failed")
    return _repo_action_response(detail, ok=False, status_code=exc.status_code)


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
) -> str:
    repo_full_name = repo_full_name.strip()
    owner, name = _repo_parts(repo_full_name)
    repo = db.execute(select(Repo).where(Repo.account_id == account.id, Repo.repo_full_name == repo_full_name)).scalar_one_or_none()
    created = repo is None
    if repo is None:
        repo = Repo(id=ids.repo_id(), account_id=account.id, forge="github", repo_full_name=repo_full_name, repo_owner=owner, repo_name=name)
        db.add(repo)
    repo.forge_repo_id = forge_repo_id or repo.forge_repo_id
    repo.default_branch = default_branch or repo.default_branch
    repo.updated_at = datetime.now(timezone.utc)
    db.commit()
    notice = _ensure_bot_collaborator(request, db, account.id, repo) if created else "repo-connected"
    return notice


def _invite_repo_bot_core(request: Request, db: Session, account_id: str, repo_id: str) -> str:
    repo = db.execute(select(Repo).where(Repo.id == repo_id, Repo.account_id == account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    notice = _ensure_bot_collaborator(request, db, account_id, repo).replace("repo-connected-bot", "repo-bot")
    return notice


def _pair_repo_telegram_core(request: Request, db: Session, account_id: str, repo_id: str):
    return telegram_pair_core(db, request.app.state.settings, account_id, repo_id)


def _disconnect_repo_core(db: Session, account_id: str, repo_id: str) -> str:
    repo = db.execute(select(Repo).where(Repo.id == repo_id, Repo.account_id == account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    for model in (Daemon, Event, ChannelRoute, TgPairCode, PairRequest, Token):
        db.execute(delete(model).where(model.repo_id == repo.id))
    db.delete(repo)
    db.commit()
    return "repo-disconnected"


def _safe_next(value: str) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _terms_accept_url(next_url: str) -> str:
    from urllib.parse import quote

    return f"/terms?next={quote(_safe_next(next_url), safe='/')}"


def _needs_hosted_terms(account: Account) -> bool:
    return account.hosted_terms_accepted_at is None or account.hosted_terms_version != _HOSTED_TERMS_VERSION


def _terms_status(account: Account | None) -> dict:
    accepted_at = account.hosted_terms_accepted_at if account is not None else None
    if accepted_at is not None and accepted_at.tzinfo is None:
        accepted_at = accepted_at.replace(tzinfo=timezone.utc)
    return {
        "authenticated": account is not None,
        "needs_accept": _needs_hosted_terms(account) if account is not None else False,
        "terms_version": _HOSTED_TERMS_VERSION,
        "accepted_at": accepted_at.isoformat() if accepted_at is not None else None,
    }


def _oauth_redirect_uri(request: Request) -> str:
    return f"{request.app.state.settings.public_base_url.rstrip('/')}/auth/github/callback"


def _cookie_secure(request: Request) -> bool:
    return request.app.state.settings.public_base_url.lower().startswith("https://")


def _clear_oauth_cookies(resp, request: Request) -> None:
    s = request.app.state.settings
    for name in (s.oauth_state_cookie, s.oauth_pkce_cookie, s.oauth_next_cookie):
        resp.delete_cookie(name, samesite="lax", secure=_cookie_secure(request))
