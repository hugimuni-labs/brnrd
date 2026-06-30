"""Web routes for the brnrd dashboard."""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from brnrd import ids, oauth
from brnrd.auth import get_db
from brnrd.models import (
    Account,
    ChannelRoute,
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
from brnrd.routers.accounts import SESSION_TTL, account_for_github_identity, issue_session_token
from brnrd.routers.github_app import sync_app_installations_for_account
from brnrd.routers.pairing import approve_core
from brnrd.security import hash_token

router = APIRouter(tags=["web"])
_TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))
_GITHUB_AUTO_SYNC_AFTER = timedelta(minutes=15)
_DAEMON_ONLINE_AFTER = timedelta(minutes=2)


def _render(request: Request, template: str, context: dict | None = None, *, status_code: int = 200) -> HTMLResponse:
    data = {"request": request}
    if context:
        data.update(context)
    return _TEMPLATES.TemplateResponse(request=request, name=template, context=data, status_code=status_code)


def _safe_next(value: str) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _oauth_redirect_uri(request: Request) -> str:
    return f"{request.app.state.settings.public_base_url.rstrip('/')}/auth/github/callback"


def _github_oauth_ready(request: Request) -> bool:
    s = request.app.state.settings
    return bool(s.github_oauth_client_id and s.github_oauth_client_secret)


def _cookie_secure(request: Request) -> bool:
    return request.app.state.settings.public_base_url.lower().startswith("https://")


def _clear_oauth_cookies(resp: RedirectResponse, request: Request) -> None:
    s = request.app.state.settings
    for name in (s.oauth_state_cookie, s.oauth_pkce_cookie, s.oauth_next_cookie):
        resp.delete_cookie(name, samesite="lax", secure=_cookie_secure(request))


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


def _repo_parts(repo_full_name: str) -> tuple[str, str]:
    owner, sep, name = repo_full_name.strip().partition("/")
    if not sep or not owner or not name:
        raise HTTPException(status_code=400, detail="repo must look like owner/name")
    return owner, name


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
        views.append(
            {
                "repo": repo,
                "daemon_count": len(daemons),
                "daemon_status": daemon_status,
                "daemon_label": daemon_label,
                "daemon_last_seen": _age_label(latest.last_seen_at if latest else None),
                "latest_daemon_name": latest.daemon_name if latest else "",
                "setup_command": f"cd {repo.repo_name}\nbrr brnrd connect --url https://brnrd.dev\nbrr daemon up",
                "sort_time": last_activity or datetime.min.replace(tzinfo=timezone.utc),
            }
        )
    return sorted(views, key=lambda v: (v["daemon_status"] == "online", v["sort_time"], v["repo"].repo_full_name.casefold()), reverse=True)


def _github_sync_configured(request: Request) -> bool:
    settings = request.app.state.settings
    return bool(settings.github_app_id and settings.github_app_private_key_b64)


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
    """Invite the human-facing GitHub bot user into the repo.

    The GitHub App is the backend actor. The bot user is the GitHub UI actor that
    can appear in collaborators, mention autocomplete, assignments, and review
    requests once GitHub grants it repository visibility.
    """
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
        "repo-connected": "Repo enabled. Set up a local brr daemon to start draining work.",
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


@router.post("/repos", response_class=HTMLResponse)
def connect_repo_submit(request: Request, repo_full_name: str = Form(...), forge_repo_id: str = Form(""), default_branch: str = Form(""), db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url="/login?next=/", status_code=303)
    account = db.get(Account, account_id)
    if account is None:
        return RedirectResponse(url="/login?next=/", status_code=303)
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
    return RedirectResponse(url=f"/?notice={notice}", status_code=303)


@router.post("/repos/{repo_id}/invite-bot", response_class=HTMLResponse)
def invite_repo_bot(repo_id: str, request: Request, db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url="/login?next=/", status_code=303)
    repo = db.execute(select(Repo).where(Repo.id == repo_id, Repo.account_id == account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    notice = _ensure_bot_collaborator(request, db, account_id, repo).replace("repo-connected-bot", "repo-bot")
    return RedirectResponse(url=f"/?notice={notice}", status_code=303)


@router.post("/repos/{repo_id}/disconnect", response_class=HTMLResponse)
def disconnect_repo(repo_id: str, request: Request, db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url="/login?next=/", status_code=303)
    repo = db.execute(select(Repo).where(Repo.id == repo_id, Repo.account_id == account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    for model in (Daemon, Event, ChannelRoute, TgPairCode, PairRequest, Token):
        db.execute(delete(model).where(model.repo_id == repo.id))
    db.delete(repo)
    db.commit()
    return RedirectResponse(url="/?notice=repo-disconnected", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    safe_next = _safe_next(next)
    signin = f"/auth/github/start?next={quote(safe_next, safe='/')}"
    return _render(request, "login.html", {"body_class": "auth-page", "title": "Sign in to brnrd", "signin_url": signin, "oauth_ready": _github_oauth_ready(request)})


@router.get("/auth/github/start")
def github_login_start(request: Request, next: str = "/"):
    if not _github_oauth_ready(request):
        return _render(request, "message.html", {"title": "Login unavailable", "eyebrow": "Configuration required", "heading": "GitHub login is not configured", "message": "Set the brnrd GitHub OAuth client id and secret.", "action_url": "/login", "action_label": "Back to login", "severity": "warning"}, status_code=503)
    state = oauth.new_state()
    verifier, challenge = oauth.new_pkce_pair()
    s = request.app.state.settings
    resp = RedirectResponse(oauth.authorize_url(s, state=state, redirect_uri=_oauth_redirect_uri(request), code_challenge=challenge), status_code=303)
    secure = _cookie_secure(request)
    resp.set_cookie(s.oauth_state_cookie, state, httponly=True, samesite="lax", secure=secure, max_age=s.oauth_state_ttl_s)
    resp.set_cookie(s.oauth_pkce_cookie, verifier, httponly=True, samesite="lax", secure=secure, max_age=s.oauth_state_ttl_s)
    resp.set_cookie(s.oauth_next_cookie, _safe_next(next), httponly=True, samesite="lax", secure=secure, max_age=s.oauth_state_ttl_s)
    return resp


@router.get("/auth/github/callback")
def github_login_callback(request: Request, code: str | None = None, state: str | None = None, db: Session = Depends(get_db)):
    s = request.app.state.settings
    expected_state = request.cookies.get(s.oauth_state_cookie)
    verifier = request.cookies.get(s.oauth_pkce_cookie)
    next_url = _safe_next(request.cookies.get(s.oauth_next_cookie, "/"))
    if not code or not state or not expected_state or not verifier or not hmac.compare_digest(state, expected_state):
        return _render(request, "message.html", {"title": "Login failed", "eyebrow": "GitHub verification", "heading": "Could not verify GitHub login", "message": "The browser session did not match the OAuth callback.", "action_url": "/login", "action_label": "Try again", "severity": "error"}, status_code=400)
    try:
        identity = oauth.resolve_identity(s, code=code, redirect_uri=_oauth_redirect_uri(request), code_verifier=verifier)
    except oauth.OAuthError as exc:
        return _render(request, "message.html", {"title": "Login failed", "eyebrow": "GitHub provider", "heading": "GitHub login failed", "message": str(exc), "action_url": "/login", "action_label": "Try again", "severity": "error"}, status_code=502)
    account = account_for_github_identity(db, identity)
    raw = issue_session_token(db, account)
    resp = RedirectResponse(url=next_url, status_code=303)
    resp.set_cookie(s.session_cookie, raw, httponly=True, samesite="lax", secure=_cookie_secure(request), max_age=int(SESSION_TTL.total_seconds()))
    _clear_oauth_cookies(resp, request)
    return resp


@router.get("/connect/{code}", response_class=HTMLResponse)
def connect_page(code: str, request: Request, db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url=f"/login?next=/connect/{code}", status_code=303)
    repos = _repos(db, account_id)
    if not repos:
        return _render(request, "message.html", {"title": "No repos", "eyebrow": "Daemon approval", "heading": "No repos connected yet", "message": "Connect a repo first, then reload this approval page.", "severity": "warning"})
    return _render(request, "connect.html", {"title": "Approve daemon", "code": code, "repos": repos})


@router.post("/connect/{code}", response_class=HTMLResponse)
def connect_submit(code: str, request: Request, repo_id: str = Form(...), db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url=f"/login?next=/connect/{code}", status_code=303)
    try:
        approve_core(db, account_id, code, repo_id)
    except HTTPException as exc:
        return _render(request, "message.html", {"title": "Approve failed", "eyebrow": "Daemon approval", "heading": "Could not approve", "message": str(exc.detail), "severity": "error"}, status_code=exc.status_code)
    return _render(request, "message.html", {"title": "Approved", "eyebrow": "Daemon approval", "heading": "Approved", "message": "Your daemon is connected. You can return to your terminal.", "severity": "success"})
