"""GitHub App setup and webhook endpoints."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import ids
from ..auth import account_id_from_session_cookie, get_db
from ..models import GitHubInstallation, GitHubInstalledRepo
from ..platforms import github_app as gh_app

router = APIRouter(prefix="/api/github", tags=["github-app"])


def _signature_ok(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature)


def _github_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    listed_names: set[str] = set()
    for item in repos:
        full_name = str(item.get("full_name") or "")
        if not full_name:
            continue
        listed_names.add(full_name)
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
        row.github_pushed_at = _github_dt(item.get("pushed_at"))
        row.github_updated_at = _github_dt(item.get("updated_at"))
        row.last_seen_at = datetime.now(timezone.utc)
    # Prune rows this installation no longer covers: a transferred or
    # uninstalled repo otherwise lingers as a stale name-match candidate
    # for credential minting (#transfer incident 2026-07-22).
    db.execute(
        delete(GitHubInstalledRepo).where(
            GitHubInstalledRepo.github_installation_id == installation.id,
            GitHubInstalledRepo.repo_full_name.not_in(listed_names),
        )
    )
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
    account_id = account_id_from_session_cookie(request, db)
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
    account_id = account_id_from_session_cookie(request, db)
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
    # No leading `settings.github_webhook_secret and` conjunct: with the
    # secret unset that made an unsigned request *skip* verification, so
    # anyone could POST an `installation` event and drive `sync_installation`.
    # `_signature_ok` already returns False on an empty secret, so the
    # unconfigured case refuses here — before the body is parsed or synced —
    # exactly like the three siblings (`routers/webhooks.py::github_webhook`,
    # `::telegram_webhook`, `::stripe_webhook`), which all fail closed with 403.
    if not _signature_ok(settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad secret")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if x_github_event in {"installation", "installation_repositories"}:
        try:
            installation_id = str(((payload or {}).get("installation") or {}).get("id") or "")
            if installation_id:
                sync_installation(db, settings, installation_id)
        except Exception as e:
            print(f"[brnrd] github installation webhook sync failed: {e}")
    elif (
        x_github_event in {"issues", "pull_request"}
        and payload.get("action") == "assigned"
        and str(
            ((payload.get("assignee") or {}).get("login") or "")
        ).casefold() == settings.github_bot_login.casefold()
    ):
        installation_id = str(
            ((payload or {}).get("installation") or {}).get("id") or ""
        )
        if installation_id:
            try:
                repository = (payload or {}).get("repository") or {}
                repository_id = repository.get("id")
                repo_name = str(repository.get("name") or "").strip()
                credential = gh_app.installation_access_credential(
                    settings,
                    installation_id,
                    repository_ids=(
                        [int(repository_id)] if repository_id else None
                    ),
                    repositories=[repo_name] if not repository_id and repo_name else None,
                )
                token = credential["token"]
                from . import webhooks

                webhooks._handle_github_assignment(
                    db,
                    settings,
                    payload,
                    token=token,
                    installation_id=installation_id,
                )
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="GitHub App event handling failed",
                ) from e
    return {"status": "ok", "event": x_github_event}
