"""Browser-session auth and HTML-flow routes for the brnrd dashboard.

Migrated from ``src/brnrd_web/routes.py`` when ``brnrd_web`` was folded
into ``src/brnrd/routers/``. Route paths, response shapes, and cookie
semantics are byte-compatible with the previous module.

``message.html`` (Jinja) is replaced by the inline ``_message_response``
helper below — no Jinja dependency for error/outcome pages.  The
``connect.html`` and ``config_approve.html`` form pages retain their Jinja
templates (moved to ``src/brnrd/routers/templates/``).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from brnrd import oauth
from brnrd.auth import get_db
from brnrd.models import Account, ConfigChangeRequest, Repo
from brnrd.routers.accounts import SESSION_TTL, account_for_github_identity, issue_session_token
from brnrd.routers.config_approval import decide_core as decide_config_change
from brnrd.routers.pairing import approve_core, telegram_pair_core

from ._session import (
    _account_id,
    _clear_oauth_cookies,
    _cookie_secure,
    _github_oauth_ready,
    _needs_hosted_terms,
    _oauth_redirect_uri,
    _repos,
    _safe_next,
    _terms_accept_url,
    _terms_status,
)

router = APIRouter(tags=["web"])

_TEMPLATES_DIR = Path(__file__).with_name("templates")
_TEMPLATES = Jinja2Templates(directory=_TEMPLATES_DIR)

# Static files live at src/brnrd/static/ (one package level up from routers/).
_STATIC_DIR = Path(__file__).parent.parent / "static"


def _compute_asset_version() -> str:
    """Content hash for cache-busting static asset URLs.

    Same contract as the original ``brnrd_web/routes.py::_compute_asset_version``:
    a real content change mints a new URL/cache key; an empty ``v=`` would let
    Cloudflare keep serving stale bytes across deployments.
    """
    h = hashlib.sha256()
    for name in sorted(("app.css", "dashboard.css")):
        try:
            h.update((_STATIC_DIR / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


_ASSET_VERSION = _compute_asset_version()


def _render(request: Request, template: str, context: dict | None = None, *, status_code: int = 200) -> HTMLResponse:
    """Render a Jinja template with the standard brnrd context."""
    data = {"request": request, "asset_version": _ASSET_VERSION}
    if context:
        data.update(context)
    return _TEMPLATES.TemplateResponse(request=request, name=template, context=data, status_code=status_code)


def _esc(value: str) -> str:
    """Minimal HTML-escape for inline rendering."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _message_response(
    *,
    title: str,
    heading: str,
    message: str,
    severity: str = "neutral",
    eyebrow: str = "",
    action_url: str = "",
    action_label: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    """Inline HTML response replacing ``message.html`` Jinja renders.

    Preserves: status codes, CSS cache-busting, no ``dashboard.css`` on
    non-dashboard pages (the live cascade bug ``test_non_dashboard_pages``
    guards against).
    """
    css_url = f"/static/brnrd_web/app.css?v={_ASSET_VERSION}"
    eyebrow_html = f'<p class="eyebrow">{_esc(eyebrow)}</p>' if eyebrow else ""
    action_html = ""
    if action_url and action_label:
        action_html = f'<a class="button button-secondary" href="{_esc(action_url)}">{_esc(action_label)}</a>'
    flow_lockup = (
        '<header class="flow-lockup" aria-label="brnrd">'
        '<a class="flow-wordmark" href="/">brnrd</a>'
        '<span class="flow-context">local daemon / cloud account</span>'
        "</header>"
    )
    body = (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"    <title>{_esc(title)}</title>\n"
        f'    <link rel="stylesheet" href="{css_url}">\n'
        "  </head>\n"
        '  <body class="app-page">\n'
        '    <main class="state-shell" aria-labelledby="state-title">\n'
        f"      {flow_lockup}\n"
        f'      <section class="panel state-panel state-{_esc(severity)}">\n'
        f"        {eyebrow_html}\n"
        f'        <h1 id="state-title">{_esc(heading)}</h1>\n'
        f'        <p class="panel-copy">{_esc(message)}</p>\n'
        f"        {action_html}\n"
        "      </section>\n"
        "    </main>\n"
        "  </body>\n"
        "</html>"
    )
    return HTMLResponse(content=body, status_code=status_code)


@router.get("/v1/dashboard/login-context")
def login_context_api(request: Request, next: str = "/", db: Session = Depends(get_db)) -> JSONResponse:
    """Context for the SPA /login page (#327 Jinja-removal, /login slice)."""
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
    # SPA owns /login; backend shim for bare uvicorn only.
    return RedirectResponse(url="/", status_code=308)


@router.get("/logout")
def logout(request: Request):
    """Clear the session cookie and redirect to ``/login``."""
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
    from brnrd.routers._session import _HOSTED_TERMS_VERSION

    account.hosted_terms_version = _HOSTED_TERMS_VERSION
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/terms/accept")
def terms_accept_redirect(next: str = "/") -> RedirectResponse:
    # In-flight OAuth/login links used the old Jinja acceptance URL.
    return RedirectResponse(url=_terms_accept_url(next), status_code=308)


@router.get("/auth/github/start")
def github_login_start(request: Request, next: str = "/"):
    if not _github_oauth_ready(request):
        return _message_response(
            title="Login unavailable",
            eyebrow="Configuration required",
            heading="GitHub login is not configured",
            message="Set the brnrd GitHub OAuth client id and secret.",
            action_url="/login",
            action_label="Back to login",
            severity="warning",
            status_code=503,
        )
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
        return _message_response(
            title="Login failed",
            eyebrow="GitHub verification",
            heading="Could not verify GitHub login",
            message="The browser session did not match the OAuth callback.",
            action_url="/login",
            action_label="Try again",
            severity="error",
            status_code=400,
        )
    try:
        identity = oauth.resolve_identity(s, code=code, redirect_uri=_oauth_redirect_uri(request), code_verifier=verifier)
    except oauth.OAuthError as exc:
        return _message_response(
            title="Login failed",
            eyebrow="GitHub provider",
            heading="GitHub login failed",
            message=str(exc),
            action_url="/login",
            action_label="Try again",
            severity="error",
            status_code=502,
        )
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
        return _message_response(
            title="No repos",
            eyebrow="Daemon approval",
            heading="No repos connected yet",
            message="Connect a repo first, then reload this approval page.",
            severity="warning",
        )
    return _render(request, "connect.html", {"title": "Approve daemon", "code": code, "repos": repos})


@router.post("/connect/{code}", response_class=HTMLResponse)
def connect_submit(code: str, request: Request, repo_id: str = Form(...), db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url=f"/login?next=/connect/{code}", status_code=303)
    from fastapi import HTTPException

    try:
        approve_core(db, account_id, code, repo_id)
    except HTTPException as exc:
        return _message_response(
            title="Approve failed",
            eyebrow="Daemon approval",
            heading="Could not approve",
            message=str(exc.detail),
            severity="error",
            status_code=exc.status_code,
        )
    try:
        pair = telegram_pair_core(db, request.app.state.settings, account_id, repo_id)
    except Exception:
        pair = None
    message = "Your daemon is connected. You can return to your terminal."
    if pair is not None:
        message += f" To use Telegram, bind the chat too: {pair.instructions}"
    return _message_response(
        title="Approved",
        eyebrow="Daemon approval",
        heading="Approved",
        message=message,
        action_url=pair.deep_link if pair else "",
        action_label="Open Telegram and press Start" if pair and pair.deep_link else "",
        severity="success",
    )


def _config_change_request_view(db: Session, request_id: str) -> ConfigChangeRequest | None:
    return db.get(ConfigChangeRequest, request_id)


@router.get("/config-approve/{request_id}", response_class=HTMLResponse)
def config_approve_page(request_id: str, request: Request, db: Session = Depends(get_db)):
    """Loom-envelope Phase 2 approve/confirm URL."""
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url=f"/login?next=/config-approve/{request_id}", status_code=303)
    row = _config_change_request_view(db, request_id)
    if row is None or row.account_id != account_id:
        return _message_response(
            title="Not found",
            eyebrow="Config-change request",
            heading="Request not found",
            message="This config-change link is unknown or belongs to a different account.",
            severity="error",
            status_code=404,
        )
    repo = db.get(Repo, row.repo_id)
    if row.status != ConfigChangeRequest.STATUS_PENDING:
        return _message_response(
            title="Already decided",
            eyebrow="Config-change request",
            heading=f"Already {row.status}",
            message=f"`{row.config_key}` on {repo.repo_full_name if repo else row.repo_id} was already {row.status}. No further action needed.",
            severity="neutral",
        )
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
    from fastapi import HTTPException

    try:
        row = decide_config_change(db, account_id, request_id, approve=approve)
    except HTTPException as exc:
        return _message_response(
            title="Could not decide",
            eyebrow="Config-change request",
            heading="Could not record a decision",
            message=str(exc.detail),
            severity="error",
            status_code=exc.status_code,
        )
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
    return _message_response(
        title="Config change",
        eyebrow="Config-change request",
        heading=row.status.capitalize(),
        message=message,
        severity=severity,
    )
