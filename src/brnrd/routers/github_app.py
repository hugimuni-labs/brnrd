"""GitHub App setup and webhook endpoints."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ids
from ..auth import get_db
from ..models import GitHubInstallation, GitHubInstalledRepo, Token
from ..platforms import github_app as gh_app
from ..security import hash_token

router = APIRouter(prefix="/api/github", tags=["github-app"])


def _signature_ok(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature)


def _account_id_from_cookie(request: Request, db: Session) -> str | None:
    cookie = request.cookies.get(request.app.state.settings.session_cookie)
    if not cookie:
        return None
    token = db.execute(select(Token).where(Token.token_hash == hash_token(cookie), Token.kind == Token.KIND_SESSION)).scalar_one_or_none()
    if token is None or token.revoked:
        return None
    return token.account_id


def sync_installation(db: Session, settings, installation_id: str, account_id: str | None = None) -> GitHubInstallation:
    installation = db.execute(select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)).scalar_one_or_none()
    if installation is None:
        installation = GitHubInstallation(id=ids.github_installation_id(), installation_id=installation_id, account_id=account_id)
        db.add(installation)
        db.flush()
    elif account_id and not installation.account_id:
        installation.account_id = account_id

    repos = gh_app.list_installation_repositories(settings, installation_id)
    target_login = ""
    target_type = ""
    seen: set[str] = set()
    for item in repos:
        full_name = str(item.get("full_name") or "")
        if not full_name:
            continue
        seen.add(full_name)
        owner = item.get("owner") or {}
        if not target_login:
            target_login = str(owner.get("login") or "")
            target_type = str(owner.get("type") or "")
        row = db.execute(select(GitHubInstalledRepo).where(GitHubInstalledRepo.github_installation_id == installation.id, GitHubInstalledRepo.repo_full_name == full_name)).scalar_one_or_none()
        if row is None:
            row = GitHubInstalledRepo(id=ids.github_installed_repo_id(), github_installation_id=installation.id, repo_full_name=full_name)
            db.add(row)
        row.forge_repo_id = str(item.get("id") or "") or None
        row.is_private = bool(item.get("private"))
        row.default_branch = str(item.get("default_branch") or "") or None
        row.last_seen_at = datetime.now(timezone.utc)
    installation.target_login = target_login or installation.target_login
    installation.target_type = target_type or installation.target_type
    installation.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return installation


def sync_app_installations_for_account(db: Session, settings, account_id: str) -> int:
    """Sync installations visible to this GitHub App for the logged-in account.

    GitHub does not always redirect an already-installed App through the setup
    callback. This manual sync lets the dashboard recover by asking GitHub for
    App installations directly. Pre-launch, discovered installations are attached
    to the current brnrd account; org-membership-aware filtering can replace that
    once multiple external users exist.
    """
    count = 0
    for installation in gh_app.list_app_installations(settings):
        installation_id = str(installation.get("id") or "")
        if not installation_id:
            continue
        sync_installation(db, settings, installation_id, account_id)
        count += 1
    return count


@router.get("/callback")
def github_app_callback(code: str | None = None, state: str | None = None, error: str | None = None, error_description: str | None = None) -> dict[str, str | None]:
    if error:
        raise HTTPException(status_code=400, detail=error_description or error)
    return {"status": "ok", "code": code, "state": state}


@router.get("/setup")
def github_app_setup(request: Request, installation_id: str | None = None, setup_action: str | None = None, db: Session = Depends(get_db)) -> RedirectResponse:
    account_id = _account_id_from_cookie(request, db)
    notice = "github-installed"
    if installation_id:
        try:
            sync_installation(db, request.app.state.settings, installation_id, account_id)
            notice = "github-synced"
        except Exception as e:
            print(f"[brnrd] github installation sync failed: {e}")
            notice = "github-sync-failed"
    params = {k: v for k, v in {"installation_id": installation_id, "setup_action": setup_action, "notice": notice}.items() if v}
    return RedirectResponse(url=f"/?{urlencode(params)}", status_code=303)


@router.post("/sync")
def github_installation_sync(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    account_id = _account_id_from_cookie(request, db)
    if account_id is None:
        return RedirectResponse(url="/login?next=/", status_code=303)
    try:
        count = sync_app_installations_for_account(db, request.app.state.settings, account_id)
        notice = "github-synced" if count else "github-sync-empty"
    except Exception as e:
        print(f"[brnrd] github manual installation sync failed: {e}")
        notice = "github-sync-failed"
    return RedirectResponse(url=f"/?notice={notice}", status_code=303)


@router.post("/webhook")
async def github_app_webhook(request: Request, x_hub_signature_256: Annotated[str | None, Header()] = None, x_github_event: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)) -> dict[str, str | None]:
    body = await request.body()
    settings = request.app.state.settings
    if settings.github_webhook_secret and not _signature_ok(settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid GitHub signature")
    if x_github_event in {"installation", "installation_repositories"}:
        try:
            payload = await request.json()
            installation_id = str(((payload or {}).get("installation") or {}).get("id") or "")
            if installation_id:
                sync_installation(db, settings, installation_id)
        except Exception as e:
            print(f"[brnrd] github installation webhook sync failed: {e}")
    return {"status": "ok", "event": x_github_event}
