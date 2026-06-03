"""Web routes for the brnrd dashboard (GitHub login + approve page).

Hand-rolled HTML for this thin slice (no template engine dependency
yet); the approve page reuses the same ``approve_core`` the API uses,
so the device-flow connect handshake is human-completable end-to-end.
"""

from __future__ import annotations

import html
import hmac
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from brnrd import oauth
from brnrd.auth import get_db
from brnrd.models import Project, Token
from brnrd.routers.accounts import (
    SESSION_TTL,
    account_for_github_identity,
    issue_session_token,
)
from brnrd.routers.pairing import approve_core
from brnrd.security import hash_token

router = APIRouter(tags=["web"])

_STYLE = (
    "body{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;max-width:34rem;"
    "margin:3rem auto;padding:0 1rem;background:#0b0e14;color:#cbd5e1;line-height:1.5}"
    "a{color:#7dd3fc}code{color:#fbbf24}"
    "input,select,button{font:inherit;padding:.55rem;margin:.35rem 0;width:100%;"
    "box-sizing:border-box;background:#111827;color:#e5e7eb;border:1px solid #334155;"
    "border-radius:6px}button{cursor:pointer;background:#1e293b}"
    ".button{display:block;text-align:center;text-decoration:none;font:inherit;padding:.65rem;"
    "margin:.6rem 0;width:100%;box-sizing:border-box;background:#1e293b;"
    "color:#e5e7eb;border:1px solid #334155;border-radius:6px}"
    ".card{border:1px solid #334155;border-radius:10px;padding:1.3rem}"
    ".muted{color:#64748b}h2{margin-top:0}"
)


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body>{body}</body></html>"
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


@router.get("/login", response_class=HTMLResponse)
def login_form(next: str = "/"):
    signin = f"/auth/github/start?next={quote(_safe_next(next), safe='/')}"
    body = (
        "<div class='card'><h2>brnrd login</h2>"
        "<p class='muted'>Use your GitHub account to continue.</p>"
        f"<a class='button' href='{html.escape(signin)}'>Sign in with GitHub</a>"
        "</div>"
    )
    return _page("brnrd login", body)


@router.get("/auth/github/start")
def github_login_start(request: Request, next: str = "/"):
    if not _github_oauth_ready(request):
        return HTMLResponse(
            _page(
                "login unavailable",
                "<div class='card'><h2>GitHub login is not configured</h2>"
                "<p class='muted'>Set the brnrd GitHub OAuth client id and secret.</p>"
                "</div>",
            ),
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
    resp.set_cookie(settings.oauth_state_cookie, state, httponly=True,
                    samesite="lax", secure=secure, max_age=max_age)
    resp.set_cookie(settings.oauth_pkce_cookie, verifier, httponly=True,
                    samesite="lax", secure=secure, max_age=max_age)
    resp.set_cookie(settings.oauth_next_cookie, _safe_next(next), httponly=True,
                    samesite="lax", secure=secure, max_age=max_age)
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
        return HTMLResponse(
            _page(
                "login failed",
                "<div class='card'><h2>Could not verify GitHub login</h2>"
                "<a href='/login'>Try again</a></div>",
            ),
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
        return HTMLResponse(
            _page(
                "login failed",
                "<div class='card'><h2>GitHub login failed</h2>"
                f"<p>{html.escape(str(exc))}</p>"
                "<a href='/login'>Try again</a></div>",
            ),
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
        return _page(
            "connect",
            "<div class='card'><h2>No projects yet</h2>"
            "<p class='muted'>Create one first, then reload this page.</p></div>",
        )

    options = "".join(
        f"<option value='{html.escape(p.id)}'>{html.escape(p.name)}</option>"
        for p in projects
    )
    body = (
        "<div class='card'><h2>Approve daemon</h2>"
        f"<p class='muted'>Pair code <code>{html.escape(code)}</code></p>"
        f"<form method='post' action='/connect/{html.escape(code)}'>"
        "<label>Bind this daemon to project</label>"
        f"<select name='project_id'>{options}</select>"
        "<button type='submit'>Approve</button></form></div>"
    )
    return _page("approve daemon", body)


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
        return HTMLResponse(
            _page(
                "approve failed",
                f"<div class='card'><h2>Could not approve</h2>"
                f"<p>{html.escape(str(exc.detail))}</p></div>",
            ),
            status_code=exc.status_code,
        )
    return _page(
        "approved",
        "<div class='card'><h2>Approved &#10003;</h2>"
        "<p>Your daemon is connected. You can return to your terminal.</p></div>",
    )
