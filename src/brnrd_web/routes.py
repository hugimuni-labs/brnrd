"""Web routes for the brnrd dashboard (login + daemon approve page).

Hand-rolled HTML for this thin slice (no template engine dependency
yet); the approve page reuses the same ``approve_core`` the API uses,
so the device-flow connect handshake is human-completable end-to-end.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from brnrd.auth import get_db
from brnrd.models import Project, Token
from brnrd.routers.accounts import authenticate, issue_session_token
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
    body = (
        "<div class='card'><h2>brnrd login</h2>"
        "<form method='post' action='/login'>"
        f"<input type='hidden' name='next' value='{html.escape(next)}'>"
        "<input name='email' type='email' placeholder='email' required>"
        "<input name='password' type='password' placeholder='password' required>"
        "<button type='submit'>Log in</button></form></div>"
    )
    return _page("brnrd login", body)


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    account = authenticate(db, email, password)
    if account is None:
        return HTMLResponse(
            _page(
                "login failed",
                "<div class='card'><p>Invalid credentials.</p>"
                "<a href='/login'>Try again</a></div>",
            ),
            status_code=401,
        )
    raw = issue_session_token(db, account)
    resp = RedirectResponse(url=next or "/", status_code=303)
    resp.set_cookie(
        request.app.state.settings.session_cookie,
        raw,
        httponly=True,
        samesite="lax",
    )
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
