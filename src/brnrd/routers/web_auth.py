"""Browser-session auth and HTML-flow routes for the brnrd dashboard.

Migrated from ``src/brnrd_web/routes.py`` when ``brnrd_web`` was folded
into ``src/brnrd/routers/``. Route paths, response shapes, and cookie
semantics are byte-compatible with the previous module.

``message.html`` (Jinja) is replaced by the inline ``_message_response``
helper below — no Jinja dependency for error/outcome pages.  The
The final Jinja content page, ``config_approve.html``, is ported to the
SvelteKit ``/config-approve/[request_id]`` route (#327), following the
earlier ``/connect/[code]`` port.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from brnrd import ids, oauth, terms
from brnrd.auth import get_db
from brnrd.models import Account, ConfigChangeRequest, Repo, TermsAcceptance
from brnrd.routers.accounts import SESSION_TTL, account_for_github_identity, issue_session_token
from brnrd.routers.config_approval import decide_core as decide_config_change
from brnrd.routers.pairing import approve_core, telegram_pair_core

from ._session import (
    _account_id,
    _accepted_terms,
    _clear_oauth_cookies,
    _cookie_secure,
    _general_terms_accept_url,
    _github_oauth_ready,
    _needs_terms,
    _oauth_redirect_uri,
    _repos,
    _safe_next,
    _terms_status,
)

router = APIRouter(tags=["web"])

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


# `/login` used to 308 to "/" here (bare-uvicorn shim, dead in production
# where Upsun's router owned the path). Removed with #847 — the app serves the
# SPA now, and `src/frontend/src/routes/login/` is the real page.


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
    return JSONResponse(_terms_status(db, account))


_ACCEPT_NOTICE = {
    terms.DOC_TOS: "You need to accept the Terms of Service before continuing.",
    terms.DOC_HOSTED: "You need to accept the beta hosted-execution terms before continuing.",
}


@router.post("/v1/terms/accept")
def terms_accept_api(
    request: Request,
    payload: dict[str, object] | None = Body(None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Record that this account accepted one named document (#735).

    The row carries the sha256 of the text as pinned right now, not a version
    string alone — the acceptance has to be able to reproduce what was
    accepted, and ``hosted_terms_version`` could not. ``document`` defaults to
    the hosted addendum so the existing widget's payload keeps working; a
    caller that omits it is, by construction, the page that predates the ToS
    gate.

    Re-accepting the same version is idempotent rather than an error: the
    first row is the evidence and a second click must not disturb its
    timestamp.
    """
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    body = payload or {}
    # Omitted means the addendum — that payload is the pre-#735 widget, which
    # predates the field. *Present but malformed* is not the same thing and
    # must not silently become a consent record for a document the caller
    # never named (#569).
    #
    # The discriminator is key presence, not the value: `.get()` cannot tell
    # `{}` from `{"document": null}`, and `or` would additionally fold "", 0
    # and false into the default. Anything present falls through to the
    # whitelist below and is refused there.
    kind = terms.DOC_HOSTED if "document" not in body else str(body["document"])
    if kind not in terms.kinds():
        return JSONResponse({"ok": False, "notice": f"Unknown document: {kind}."}, status_code=400)
    if body.get("accept_terms") != "yes":
        return JSONResponse({"ok": False, "notice": _ACCEPT_NOTICE[kind]}, status_code=400)

    doc = terms.current(kind)
    existing = _accepted_terms(db, account_id, kind)
    if existing is None:
        db.add(
            TermsAcceptance(
                id=ids.terms_acceptance_id(),
                account_id=account_id,
                document=kind,
                version=doc.version,
                sha256=doc.sha256,
                accepted_at=datetime.now(timezone.utc),
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # Read-then-insert is not atomic, and `uq_terms_acceptance` is the
            # thing that actually enforces one row per (account, document,
            # version). Two tabs racing means the constraint fires on the
            # loser — but the acceptance it was trying to record is on disk,
            # written by the winner. Reporting 500 for a click that succeeded
            # would be a lie about the record's state, so the loser reports
            # what is true: accepted. This is what makes the endpoint
            # idempotent under concurrency and not just under sequence.
            db.rollback()
    return JSONResponse({"ok": True, "document": kind, "version": doc.version, "sha256": doc.sha256})


# `GET /terms/accept` used to 308 to the hosted-execution page here, covering
# OAuth links minted before #569 moved that document. Removed with #847: those
# links expired weeks ago, and it was the one backend route standing inside a
# namespace the SPA owns (`/terms`), which would have forced a hand-written
# exception into the very mechanism that replaces hand-written exceptions.
# `_terms_accept_url` (routers/_session.py) still mints the live URL.


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
    # Authentication does not gate on the hosted-execution beta terms (#664).
    # Those terms apply "when HugiMuni SAS operates brnrd-hosted compute for
    # your account" — a condition login cannot evaluate and, for a
    # local-execution account, never satisfies. Acceptance belongs at the
    # surface that offers hosted execution; the `hosted-execution` entry of
    # `_terms_status()` is what that surface reads when it exists.
    #
    # The *general* Terms of Service are the opposite case, and #735 is where
    # the distinction earns its keep: they govern using brnrd.dev at all, so
    # login is precisely the moment the condition is satisfiable, and until
    # now nothing asked. A user who owes acceptance is routed to /terms with
    # their original destination carried in `next=`, so accepting resumes the
    # journey rather than ending it. The session cookie is still set — the
    # accept endpoint needs it, and a gate that logged you out to ask you a
    # question could never be answered.
    if _needs_terms(db, account, terms.DOC_TOS):
        next_url = _general_terms_accept_url(next_url)
    resp = RedirectResponse(url=next_url, status_code=303)
    resp.set_cookie(s.session_cookie, raw, httponly=True, samesite="lax", secure=_cookie_secure(request), max_age=int(SESSION_TTL.total_seconds()))
    _clear_oauth_cookies(resp, request)
    return resp


def _pair_code_status(db: Session, code: str) -> str:
    """Classify a pair code the way ``pairing._get_pair`` + ``approve_core`` would.

    Mirrors the exact check order of the POST path (unknown → expired →
    consumed → live status) so the GET context and a subsequent approve
    can never disagree about the same code.
    """
    from sqlalchemy import select

    from brnrd.models import PairRequest

    pair = db.execute(select(PairRequest).where(PairRequest.pair_code == code)).scalar_one_or_none()
    if pair is None:
        return "unknown"
    expires = pair.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return "expired"
    return pair.status


@router.get("/v1/connect/{code}")
def connect_context_api(code: str, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Context for the SPA /connect/[code] page (#327 Jinja-removal, /connect slice).

    Session-authenticated exactly like the retired Jinja ``connect_page``:
    no session → 401 (the SPA renders the sign-in link with
    ``next=/connect/{code}``, replacing the old 303 redirect). The code
    status only reveals distinctions the POST path already exposed to any
    signed-in browser (404 unknown / 410 expired / 409 used).
    """
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account_id)
    return JSONResponse(
        {
            "code": code,
            "status": _pair_code_status(db, code),
            "repos": [{"id": repo.id, "repo_full_name": repo.repo_full_name} for repo in repos],
        }
    )


@router.post("/v1/connect/{code}")
def connect_approve_api(
    code: str,
    request: Request,
    payload: dict[str, object] | None = Body(None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Approve a daemon pair code (#327 Jinja-removal, /connect slice).

    JSON transport for the retired Jinja ``connect_submit`` — the auth and
    approval semantics are ``approve_core``'s, unchanged: session required,
    code expiry (410), single-use after the daemon polls (409), and the repo
    lookup is scoped to the session's own account (404 on any other
    account's repo).
    """
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repo_id = str((payload or {}).get("repo_id") or "")
    from fastapi import HTTPException

    try:
        approve_core(db, account_id, code, repo_id)
    except HTTPException as exc:
        return JSONResponse({"ok": False, "notice": str(exc.detail)}, status_code=exc.status_code)
    try:
        pair = telegram_pair_core(db, request.app.state.settings, account_id, repo_id)
    except Exception:
        pair = None
    telegram = None
    if pair is not None:
        telegram = {
            "pair_code": pair.pair_code,
            "instructions": pair.instructions,
            "deep_link": pair.deep_link,
        }
    return JSONResponse(
        {
            "ok": True,
            "notice": "Your daemon is connected. You can return to your terminal.",
            "telegram": telegram,
        }
    )


# `/connect/{code}` used to 308 to "/" here — same bare-uvicorn shim as
# `/login`, removed with #847. `src/frontend/src/routes/connect/[code]/` is
# the page, and the app now serves it.


def _config_change_request_view(db: Session, request_id: str) -> ConfigChangeRequest | None:
    return db.get(ConfigChangeRequest, request_id)


def _config_change_response(row: ConfigChangeRequest, repo: Repo | None) -> dict[str, object]:
    """The shared SPA view of an account-owned config-change request."""
    return {
        "id": row.id,
        "repo_label": repo.repo_full_name if repo else row.repo_id,
        "config_key": row.config_key,
        "current_value": row.current_value,
        "requested_value": row.requested_value,
        "reason": row.reason,
        "status": row.status,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def _config_change_notice(row: ConfigChangeRequest, repo: Repo | None) -> str:
    repo_label = repo.repo_full_name if repo else row.repo_id
    if row.status == ConfigChangeRequest.STATUS_EXPIRED:
        return f"This request to change `{row.config_key}` on {repo_label} expired before a decision was made. No change applied."
    if row.status == ConfigChangeRequest.STATUS_APPROVED:
        return f"Approved. Your daemon will set `{row.config_key}` to `{row.requested_value}` on {repo_label} the next time it checks in."
    return f"Rejected. `{row.config_key}` on {repo_label} stays at `{row.current_value}`."


@router.get("/v1/config-approve/{request_id}")
def config_approve_context_api(request_id: str, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Session-scoped context for the SPA config-approval page.

    The 404 deliberately covers both absent and cross-account rows, as the
    Jinja flow did, so a signed-in account cannot learn another account's
    request details. The SPA supplies the safe ``next=`` path on a 401.
    """
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    row = _config_change_request_view(db, request_id)
    if row is None or row.account_id != account_id:
        return JSONResponse({"detail": "unknown config-change request"}, status_code=404)
    repo = db.get(Repo, row.repo_id)
    return JSONResponse(_config_change_response(row, repo))


@router.post("/v1/config-approve/{request_id}")
def config_approve_decide_api(
    request_id: str,
    request: Request,
    payload: dict[str, object] | None = Body(None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """JSON transport for the SPA; ``decide_core`` retains all decisions."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    approve = str((payload or {}).get("decision") or "").strip().lower() == "approve"
    from fastapi import HTTPException

    try:
        row = decide_config_change(db, account_id, request_id, approve=approve)
    except HTTPException as exc:
        return JSONResponse({"ok": False, "notice": str(exc.detail)}, status_code=exc.status_code)
    repo = db.get(Repo, row.repo_id)
    return JSONResponse({"ok": True, "notice": _config_change_notice(row, repo), "request": _config_change_response(row, repo)})


# `/config-approve/{request_id}` used to 308 to "/" here — the last of the
# bare-uvicorn shims, removed with #847.
# `src/frontend/src/routes/config-approve/[request_id]/` is the page.
