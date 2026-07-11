"""Web routes for the brnrd dashboard."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from brnrd import ids, oauth
from brnrd.auth import get_db
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
from brnrd.routers.accounts import SESSION_TTL, account_for_github_identity, issue_session_token
from brnrd.routers.config_approval import decide_core as decide_config_change
from brnrd.routers.github_app import sync_app_installations_for_account
from brnrd.routers.pairing import approve_core, telegram_pair_core
from brnrd.security import hash_token

router = APIRouter(tags=["web"])
_TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))
_GITHUB_AUTO_SYNC_AFTER = timedelta(minutes=15)
_DAEMON_ONLINE_AFTER = timedelta(minutes=2)
_HOSTED_TERMS_VERSION = "2026-07-08"


def _compute_asset_version() -> str:
    """Content hash of the static assets base.html cache-busts with.

    base.html has requested `?v={{ asset_version }}` since it was scaffolded,
    but nothing ever populated the variable, so every deploy served the exact
    same `app.css?v=` URL — a stable cache key an edge CDN (Cloudflare, here)
    is free to hold onto for its full `max-age` regardless of what changed
    server-side. Live-caught 2026-07-08: the login/terms brand-palette fix
    (PR #301) had already merged and deployed, but `/login` kept rendering
    the old mint-green accent because Cloudflare was still serving the
    pre-fix `app.css` bytes under that same unversioned URL (`cf-cache-status:
    HIT`, `cache-control: max-age=14400`). Hashing the served files at import
    time means a real content change mints a new URL/cache key for free.
    """
    h = hashlib.sha256()
    for name in sorted(("app.css", "dashboard.css")):
        try:
            h.update((Path(__file__).with_name("static") / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


_ASSET_VERSION = _compute_asset_version()


def _render(request: Request, template: str, context: dict | None = None, *, status_code: int = 200) -> HTMLResponse:
    data = {"request": request, "asset_version": _ASSET_VERSION}
    if context:
        data.update(context)
    return _TEMPLATES.TemplateResponse(request=request, name=template, context=data, status_code=status_code)


def _safe_next(value: str) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _terms_accept_url(next_url: str) -> str:
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
                "daemon_last_seen_at": _dt(latest.last_seen_at if latest else None),
                "latest_daemon_name": latest.daemon_name if latest else "",
                "setup_command": f"cd {repo.repo_name}\nbrnrd connect https://brnrd.dev\nbrnrd up",
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


def _repo_action_response(notice: str, *, ok: bool = True, status_code: int = 200, **extra: Any) -> JSONResponse:
    body = {"ok": ok, "notice": _notice_text(notice) or notice}
    body.update(extra)
    return JSONResponse(body, status_code=status_code)


def _repo_error_response(exc: HTTPException) -> JSONResponse:
    detail = str(exc.detail or "request failed")
    return _repo_action_response(detail, ok=False, status_code=exc.status_code)


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


@router.post("/v1/repos/connect")
async def connect_repo_api(request: Request, db: Session = Depends(get_db)):
    account = _json_account(request, db)
    payload = await _json_body(request)
    try:
        notice = _connect_repo_core(
            request,
            db,
            account,
            repo_full_name=_payload_str(payload, "repo_full_name"),
            forge_repo_id=_payload_str(payload, "forge_repo_id"),
            default_branch=_payload_str(payload, "default_branch"),
        )
    except HTTPException as exc:
        return _repo_error_response(exc)
    return _repo_action_response(notice)


@router.post("/v1/repos/{repo_id}/invite-bot")
def invite_repo_bot_api(repo_id: str, request: Request, db: Session = Depends(get_db)):
    account = _json_account(request, db)
    try:
        notice = _invite_repo_bot_core(request, db, account.id, repo_id)
    except HTTPException as exc:
        return _repo_error_response(exc)
    return _repo_action_response(notice)


@router.post("/v1/repos/{repo_id}/telegram-pair")
def pair_repo_telegram_api(repo_id: str, request: Request, db: Session = Depends(get_db)):
    account = _json_account(request, db)
    try:
        pair = _pair_repo_telegram_core(request, db, account.id, repo_id)
    except HTTPException as exc:
        return _repo_error_response(exc)
    return _repo_action_response(
        "Pair this Telegram chat",
        pairing_code=pair.pair_code,
        instructions=pair.instructions,
        action_url=pair.deep_link,
    )


@router.post("/v1/repos/{repo_id}/disconnect")
def disconnect_repo_api(repo_id: str, request: Request, db: Session = Depends(get_db)):
    account = _json_account(request, db)
    try:
        notice = _disconnect_repo_core(db, account.id, repo_id)
    except HTTPException as exc:
        return _repo_error_response(exc)
    return _repo_action_response(notice)


@router.get("/v1/dashboard/login-context")
def login_context_api(request: Request, next: str = "/", db: Session = Depends(get_db)) -> JSONResponse:
    """Context for the SPA /login page (#327 Jinja-removal, /login slice).

    ``next`` validation stays server-owned (`_safe_next`), same shape the
    Jinja page used: the SPA never builds its own OAuth start URL, it
    renders the one handed back here. ``authenticated`` lets an
    already-signed-in visitor skip straight to ``next``.
    """
    safe_next = _safe_next(next)
    return JSONResponse(
        {
            "authenticated": _account_id(request, db) is not None,
            "oauth_ready": _github_oauth_ready(request),
            "signin_url": f"/auth/github/start?next={quote(safe_next, safe='/')}",
            "next": safe_next,
        }
    )


@router.get("/login")
def login_redirect() -> RedirectResponse:
    # #327: the SPA owns /login (passthru removed in .upsun/config.yaml and
    # the Vite dev proxy); this backend route only serves bare uvicorn now,
    # same 308 shape as the retired /repos and /activity Jinja pages.
    return RedirectResponse(url="/", status_code=308)


@router.get("/logout")
def logout(request: Request):
    """Clear the session cookie and send the browser back to `/login`.

    Named directly as a real gap (2026-07-08): there was no way to end a
    browser session short of clearing cookies by hand. GET, not POST — this
    is a plain link (the dashboard's own "sign out" affordance), not a form;
    revoking the underlying `Token` row isn't attempted here (the cookie
    stops being sent, which is what actually ends the browser session), the
    same "delete the cookie, not the token" shape `_clear_oauth_cookies`
    already uses for the oauth-flow cookies above.
    """
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(request.app.state.settings.session_cookie, samesite="lax", secure=_cookie_secure(request))
    return resp


@router.get("/v1/dashboard/terms-status")
def terms_status_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    account_id = _account_id(request, db)
    account = db.get(Account, account_id) if account_id is not None else None
    return JSONResponse(_terms_status(account))


@router.post("/v1/terms/accept")
def terms_accept_api(
    request: Request,
    payload: dict[str, object] | None = Body(None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    if (payload or {}).get("accept_terms") != "yes":
        return JSONResponse(
            {"ok": False, "notice": "You need to accept the beta hosted-execution terms before continuing."},
            status_code=400,
        )
    account.hosted_terms_accepted_at = datetime.now(timezone.utc)
    account.hosted_terms_version = _HOSTED_TERMS_VERSION
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/terms/accept")
def terms_accept_redirect(next: str = "/") -> RedirectResponse:
    # In-flight OAuth/login links used the old Jinja acceptance URL; the SPA
    # owns /terms now, with this shim preserving the validated next handoff.
    return RedirectResponse(url=_terms_accept_url(next), status_code=308)


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
    target_url = _terms_accept_url(next_url) if _needs_hosted_terms(account) else next_url
    resp = RedirectResponse(url=target_url, status_code=303)
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
    try:
        pair = telegram_pair_core(db, request.app.state.settings, account_id, repo_id)
    except HTTPException:
        pair = None
    message = "Your daemon is connected. You can return to your terminal."
    if pair is not None:
        message += f" To use Telegram, bind the chat too: {pair.instructions}"
    return _render(request, "message.html", {"title": "Approved", "eyebrow": "Daemon approval", "heading": "Approved", "message": message, "action_url": pair.deep_link if pair else None, "action_label": "Open Telegram and press Start" if pair and pair.deep_link else None, "severity": "success"})


def _config_change_request_view(db: Session, request_id: str) -> ConfigChangeRequest | None:
    return db.get(ConfigChangeRequest, request_id)


@router.get("/config-approve/{request_id}", response_class=HTMLResponse)
def config_approve_page(request_id: str, request: Request, db: Session = Depends(get_db)):
    """Loom-envelope Phase 2's approve/confirm URL — the daemon mints
    ``request_id`` via ``POST /v1/daemons/config-requests`` (see
    ``brnrd.routers.config_approval``) when a resident wants more of an
    allowlisted ceiling than ``.brr/config`` currently grants."""
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url=f"/login?next=/config-approve/{request_id}", status_code=303)
    row = _config_change_request_view(db, request_id)
    if row is None or row.account_id != account_id:
        return _render(request, "message.html", {"title": "Not found", "eyebrow": "Config-change request", "heading": "Request not found", "message": "This config-change link is unknown or belongs to a different account.", "severity": "error"}, status_code=404)
    repo = db.get(Repo, row.repo_id)
    if row.status != ConfigChangeRequest.STATUS_PENDING:
        return _render(request, "message.html", {"title": "Already decided", "eyebrow": "Config-change request", "heading": f"Already {row.status}", "message": f"`{row.config_key}` on {repo.repo_full_name if repo else row.repo_id} was already {row.status}. No further action needed.", "severity": "neutral"})
    return _render(
        request,
        "config_approve.html",
        {
            "title": "Approve config change",
            "request_id": row.id,
            "repo_full_name": repo.repo_full_name if repo else row.repo_id,
            "config_key": row.config_key,
            "current_value": row.current_value,
            "requested_value": row.requested_value,
            "reason": row.reason,
        },
    )


@router.post("/config-approve/{request_id}", response_class=HTMLResponse)
def config_approve_submit(request_id: str, request: Request, decision: str = Form(...), db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url=f"/login?next=/config-approve/{request_id}", status_code=303)
    approve = decision.strip().lower() == "approve"
    try:
        row = decide_config_change(db, account_id, request_id, approve=approve)
    except HTTPException as exc:
        return _render(request, "message.html", {"title": "Could not decide", "eyebrow": "Config-change request", "heading": "Could not record a decision", "message": str(exc.detail), "severity": "error"}, status_code=exc.status_code)
    repo = db.get(Repo, row.repo_id)
    repo_label = repo.repo_full_name if repo else row.repo_id
    if row.status == ConfigChangeRequest.STATUS_EXPIRED:
        message = f"This request to change `{row.config_key}` on {repo_label} expired before a decision was made. No change applied."
        severity = "warning"
    elif row.status == ConfigChangeRequest.STATUS_APPROVED:
        message = f"Approved. Your daemon will set `{row.config_key}` to `{row.requested_value}` on {repo_label} the next time it checks in."
        severity = "success"
    else:
        message = f"Rejected. `{row.config_key}` on {repo_label} stays at `{row.current_value}`."
        severity = "neutral"
    return _render(request, "message.html", {"title": "Config change", "eyebrow": "Config-change request", "heading": row.status.capitalize(), "message": message, "severity": severity})
