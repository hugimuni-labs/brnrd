"""Web routes for the brnrd dashboard (GitHub login + approve page).

This first web slice uses packaged Jinja templates and static CSS so the
login/approve flow has the same substrate as the planned dashboard MVP:
server-rendered HTML, no JS build pipeline, and HTMX-ready assets later.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from brnrd import ids, oauth
from brnrd.auth import get_db
from brnrd.models import Account, Project, RepoBinding, Token
from brnrd.routers.accounts import (
    SESSION_TTL,
    account_for_github_identity,
    issue_session_token,
)
from brnrd.routers.pairing import approve_core
from brnrd.security import hash_token

router = APIRouter(tags=["web"])

_TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))


def _render(
    request: Request,
    template: str,
    context: dict | None = None,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    data = {"request": request}
    if context:
        data.update(context)
    return _TEMPLATES.TemplateResponse(
        request=request,
        name=template,
        context=data,
        status_code=status_code,
    )


def _safe_next(value: str) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _oauth_redirect_uri(request: Request) -> str:
    settings = request.app.state.settings
    return f"{settings.public_base_url.rstrip('/')}/auth/github/callback"


def _github_oauth_ready(request: Request) -> bool:
    settings = request.app.state.settings
    return bool(settings.github_oauth_client_id and settings.github_oauth_client_secret)


def _cookie_secure(request: Request) -> bool:
    # Set Secure whenever brnrd is served over HTTPS (production); stays
    # off for local http dev so the cookies still round-trip.
    return request.app.state.settings.public_base_url.lower().startswith("https://")


def _clear_oauth_cookies(resp: RedirectResponse, request: Request) -> None:
    settings = request.app.state.settings
    secure = _cookie_secure(request)
    for name in (
        settings.oauth_state_cookie,
        settings.oauth_pkce_cookie,
        settings.oauth_next_cookie,
    ):
        resp.delete_cookie(name, samesite="lax", secure=secure)


def _account_id(request: Request, db: Session) -> str | None:
    cookie = request.cookies.get(request.app.state.settings.session_cookie)
    if not cookie:
        return None
    token = db.execute(
        select(Token).where(
            Token.token_hash == hash_token(cookie), Token.kind == Token.KIND_SESSION
        )
    ).scalar_one_or_none()
    if token is None or token.revoked:
        return None
    if token.expires_at is not None:
        expires = token.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            return None
    return token.account_id


def _project_rows(db: Session, account_id: str) -> list[Project]:
    return list(
        db.execute(
            select(Project)
            .where(Project.account_id == account_id)
            .order_by(Project.created_at)
        ).scalars()
    )


def _repo_owner(repo_full_name: str) -> str:
    owner, _, _name = repo_full_name.strip().partition("/")
    return owner


def _repo_owner_matches(account: Account, repo_full_name: str) -> bool:
    return _repo_owner(repo_full_name).casefold() == account.github_login.casefold()


def _visible_repo_binding_rows(db: Session, account: Account) -> list[dict]:
    """Return bindings this account owns or can plausibly recover.

    During the prototype, some production bindings were created before the
    GitHub-OAuth account row existed, so their ``account_id`` may point at an
    older local/API account. If the repo owner matches the logged-in GitHub
    login, show it as recoverable instead of hiding a working cloud gate.
    """
    rows = list(db.execute(select(RepoBinding).order_by(RepoBinding.created_at)).scalars())
    out: list[dict] = []
    for binding in rows:
        is_connected = binding.account_id == account.id
        is_recoverable = not is_connected and _repo_owner_matches(account, binding.repo_full_name)
        if not is_connected and not is_recoverable:
            continue
        project = db.get(Project, binding.project_id)
        out.append(
            {
                "binding": binding,
                "status": "connected" if is_connected else "recoverable",
                "project_name": project.name if project else binding.project_id,
                "project_exists": project is not None,
            }
        )
    return out


def _notice_text(value: str | None) -> str | None:
    notices = {
        "repo-bound": "Repository binding saved.",
        "repo-claimed": "Existing repository binding claimed for this account.",
    }
    if not value:
        return None
    return notices.get(value, value)


def _dashboard_context(
    request: Request,
    db: Session,
    account: Account,
    *,
    installation_id: str | None = None,
    setup_action: str | None = None,
    notice: str | None = None,
) -> dict:
    settings = request.app.state.settings
    projects = _project_rows(db, account.id)
    binding_views = _visible_repo_binding_rows(db, account)
    connected_count = sum(1 for row in binding_views if row["status"] == "connected")
    recoverable_count = sum(1 for row in binding_views if row["status"] == "recoverable")
    return {
        "body_class": "dashboard-page",
        "title": "brnrd dashboard",
        "logged_in": True,
        "account": account,
        "projects": projects,
        "binding_views": binding_views,
        "connected_count": connected_count,
        "recoverable_count": recoverable_count,
        "install_url": settings.github_install_url,
        "github_app_slug": settings.github_app_slug,
        "github_bot_login": settings.github_bot_login.strip().lstrip("@"),
        "github_trigger_aliases": settings.github_trigger_aliases,
        "setup_installation_id": installation_id or "",
        "setup_action": setup_action,
        "notice": _notice_text(notice),
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    installation_id: str | None = None,
    setup_action: str | None = None,
    notice: str | None = None,
    db: Session = Depends(get_db),
):
    account_id = _account_id(request, db)
    if account_id is None:
        return _render(
            request,
            "dashboard.html",
            {
                "body_class": "dashboard-page",
                "title": "brnrd dashboard",
                "logged_in": False,
                "signin_url": "/login?next=/",
                "install_url": request.app.state.settings.github_install_url,
                "github_app_slug": request.app.state.settings.github_app_slug,
                "github_bot_login": request.app.state.settings.github_bot_login.strip().lstrip("@"),
            },
        )

    account = db.get(Account, account_id)
    if account is None:
        return RedirectResponse(url="/login?next=/", status_code=303)
    return _render(
        request,
        "dashboard.html",
        _dashboard_context(
            request,
            db,
            account,
            installation_id=installation_id,
            setup_action=setup_action,
            notice=notice,
        ),
    )


@router.post("/bindings/repo", response_class=HTMLResponse)
def bind_repo_submit(
    request: Request,
    repo_full_name: str = Form(...),
    installation_id: str = Form(...),
    project_id: str = Form(...),
    db: Session = Depends(get_db),
):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url="/login?next=/", status_code=303)

    account = db.get(Account, account_id)
    if account is None:
        return RedirectResponse(url="/login?next=/", status_code=303)

    repo = repo_full_name.strip()
    install = installation_id.strip()
    if not repo or "/" not in repo or not install:
        return _render(
            request,
            "dashboard.html",
            _dashboard_context(
                request,
                db,
                account,
                installation_id=install,
                notice="Enter a repository like owner/name and a GitHub App installation id.",
            ),
            status_code=400,
        )

    project = db.execute(
        select(Project).where(Project.id == project_id, Project.account_id == account.id)
    ).scalar_one_or_none()
    if project is None:
        return _render(
            request,
            "dashboard.html",
            _dashboard_context(
                request,
                db,
                account,
                installation_id=install,
                notice="Select one of your brnrd projects before binding a repo.",
            ),
            status_code=404,
        )

    existing = db.execute(
        select(RepoBinding).where(RepoBinding.repo_full_name == repo)
    ).scalar_one_or_none()
    if existing is not None and existing.account_id != account.id:
        if not _repo_owner_matches(account, existing.repo_full_name):
            return _render(
                request,
                "dashboard.html",
                _dashboard_context(
                    request,
                    db,
                    account,
                    installation_id=install,
                    notice=f"{repo} is already bound to another brnrd account.",
                ),
                status_code=409,
            )
        existing.account_id = account.id
    if existing is None:
        existing = RepoBinding(
            id=ids.repo_binding_id(),
            installation_id=install,
            repo_full_name=repo,
            account_id=account.id,
            project_id=project.id,
        )
        db.add(existing)
    else:
        existing.installation_id = install
        existing.project_id = project.id
    db.commit()

    return RedirectResponse(url="/?notice=repo-bound", status_code=303)


@router.post("/bindings/repo/{binding_id}/claim", response_class=HTMLResponse)
def claim_repo_binding(
    binding_id: str,
    request: Request,
    project_id: str = Form(...),
    db: Session = Depends(get_db),
):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url="/login?next=/", status_code=303)
    account = db.get(Account, account_id)
    if account is None:
        return RedirectResponse(url="/login?next=/", status_code=303)

    binding = db.get(RepoBinding, binding_id)
    if binding is None or not _repo_owner_matches(account, binding.repo_full_name):
        return _render(
            request,
            "dashboard.html",
            _dashboard_context(
                request,
                db,
                account,
                notice="That repository binding cannot be claimed by this GitHub account.",
            ),
            status_code=404,
        )

    project = db.execute(
        select(Project).where(Project.id == project_id, Project.account_id == account.id)
    ).scalar_one_or_none()
    if project is None:
        return _render(
            request,
            "dashboard.html",
            _dashboard_context(
                request,
                db,
                account,
                notice="Select one of your brnrd projects before claiming the repo.",
            ),
            status_code=404,
        )

    binding.account_id = account.id
    binding.project_id = project.id
    db.commit()
    return RedirectResponse(url="/?notice=repo-claimed", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    safe_next = _safe_next(next)
    signin = f"/auth/github/start?next={quote(safe_next, safe='/')}"
    return _render(
        request,
        "login.html",
        {
            "body_class": "auth-page",
            "title": "Sign in to brnrd",
            "signin_url": signin,
            "oauth_ready": _github_oauth_ready(request),
        },
    )


@router.get("/auth/github/start")
def github_login_start(request: Request, next: str = "/"):
    if not _github_oauth_ready(request):
        return _render(
            request,
            "message.html",
            {
                "title": "Login unavailable",
                "eyebrow": "Configuration required",
                "heading": "GitHub login is not configured",
                "message": "Set the brnrd GitHub OAuth client id and secret.",
                "action_url": "/login",
                "action_label": "Back to login",
                "severity": "warning",
            },
            status_code=503,
        )

    state = oauth.new_state()
    verifier, challenge = oauth.new_pkce_pair()
    settings = request.app.state.settings
    redirect_uri = _oauth_redirect_uri(request)
    resp = RedirectResponse(
        oauth.authorize_url(
            settings,
            state=state,
            redirect_uri=redirect_uri,
            code_challenge=challenge,
        ),
        status_code=303,
    )
    max_age = settings.oauth_state_ttl_s
    secure = _cookie_secure(request)
    resp.set_cookie(
        settings.oauth_state_cookie,
        state,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=max_age,
    )
    resp.set_cookie(
        settings.oauth_pkce_cookie,
        verifier,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=max_age,
    )
    resp.set_cookie(
        settings.oauth_next_cookie,
        _safe_next(next),
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=max_age,
    )
    return resp


@router.get("/auth/github/callback")
def github_login_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    expected_state = request.cookies.get(settings.oauth_state_cookie)
    verifier = request.cookies.get(settings.oauth_pkce_cookie)
    next_url = _safe_next(request.cookies.get(settings.oauth_next_cookie, "/"))
    if (
        not code
        or not state
        or not expected_state
        or not verifier
        or not hmac.compare_digest(state, expected_state)
    ):
        return _render(
            request,
            "message.html",
            {
                "title": "Login failed",
                "eyebrow": "GitHub verification",
                "heading": "Could not verify GitHub login",
                "message": "The browser session did not match the OAuth callback.",
                "action_url": "/login",
                "action_label": "Try again",
                "severity": "error",
            },
            status_code=400,
        )

    try:
        identity = oauth.resolve_identity(
            settings,
            code=code,
            redirect_uri=_oauth_redirect_uri(request),
            code_verifier=verifier,
        )
    except oauth.OAuthError as exc:
        return _render(
            request,
            "message.html",
            {
                "title": "Login failed",
                "eyebrow": "GitHub provider",
                "heading": "GitHub login failed",
                "message": str(exc),
                "action_url": "/login",
                "action_label": "Try again",
                "severity": "error",
            },
            status_code=502,
        )

    account = account_for_github_identity(db, identity)
    raw = issue_session_token(db, account)
    resp = RedirectResponse(url=next_url, status_code=303)
    resp.set_cookie(
        settings.session_cookie,
        raw,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        max_age=int(SESSION_TTL.total_seconds()),
    )
    _clear_oauth_cookies(resp, request)
    return resp


@router.get("/connect/{code}", response_class=HTMLResponse)
def connect_page(code: str, request: Request, db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url=f"/login?next=/connect/{code}", status_code=303)

    projects = (
        db.execute(
            select(Project)
            .where(Project.account_id == account_id)
            .order_by(Project.created_at)
        )
        .scalars()
        .all()
    )
    if not projects:
        return _render(
            request,
            "message.html",
            {
                "title": "No projects",
                "eyebrow": "Daemon approval",
                "heading": "No projects yet",
                "message": "Create a project first, then reload this approval page.",
                "severity": "warning",
            },
        )

    return _render(
        request,
        "connect.html",
        {
            "title": "Approve daemon",
            "code": code,
            "projects": projects,
        },
    )


@router.post("/connect/{code}", response_class=HTMLResponse)
def connect_submit(
    code: str,
    request: Request,
    project_id: str = Form(...),
    db: Session = Depends(get_db),
):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url=f"/login?next=/connect/{code}", status_code=303)
    try:
        approve_core(db, account_id, code, project_id)
    except HTTPException as exc:
        return _render(
            request,
            "message.html",
            {
                "title": "Approve failed",
                "eyebrow": "Daemon approval",
                "heading": "Could not approve",
                "message": str(exc.detail),
                "severity": "error",
            },
            status_code=exc.status_code,
        )
    return _render(
        request,
        "message.html",
        {
            "title": "Approved",
            "eyebrow": "Daemon approval",
            "heading": "Approved",
            "message": "Your daemon is connected. You can return to your terminal.",
            "severity": "success",
        },
    )
