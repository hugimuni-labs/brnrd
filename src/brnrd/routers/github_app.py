"""GitHub App setup and webhook endpoints."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import github_marker, github_summons, ids
from ..auth import account_id_from_session_cookie, get_db
from ..models import Account, GitHubInstallation, GitHubInstalledRepo
from ..platforms import github_app as gh_app

router = APIRouter(prefix="/api/github", tags=["github-app"])
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstallationSyncResult:
    synced: int = 0
    skipped: int = 0


def github_sync_notice(result: InstallationSyncResult) -> str:
    if result.skipped:
        return "github-sync-partial" if result.synced else "github-sync-refused"
    return "github-synced" if result.synced else "github-sync-empty"


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

    installation_token = gh_app.installation_access_token(
        settings, installation_id
    )
    repos = gh_app.list_installation_repositories(
        settings, installation_id, token=installation_token
    )
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
        label = str(settings.github_trigger_label or "").strip()
        if label:
            try:
                gh_app.ensure_repository_label(
                    settings,
                    installation_token,
                    full_name,
                    label,
                )
            except Exception as exc:
                logger.warning(
                    "could not create GitHub summons label %r on %s: %s",
                    label,
                    full_name,
                    exc,
                )
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
    if installation.account_id:
        # #874 — the invite can arrive before or after bind; installation
        # sync is the other side of that race (repo bind, in `_session.py`
        # / `routers/accounts.py`, is the first). Best-effort: a marker
        # failure here must never fail an installation sync.
        try:
            bound_repos = github_marker.account_repos(db, installation.account_id)
            github_marker.sync_marker_for_repos(db, settings, bound_repos)
        except Exception as exc:
            logger.warning(
                "brnrd-bot marker sync failed after installation %s sync: %s",
                installation.installation_id,
                exc,
            )
    return installation


def _sync_verified_installations(
    db: Session,
    settings,
    account: Account,
    installations: list[dict],
) -> InstallationSyncResult:
    synced = 0
    skipped = 0
    for installation in installations:
        installation_id = str(installation.get("id") or "")
        existing = (
            db.execute(
                select(GitHubInstallation).where(
                    GitHubInstallation.installation_id == installation_id
                )
            ).scalar_one_or_none()
            if installation_id
            else None
        )
        if (
            existing is not None
            and existing.account_id is not None
            and existing.account_id != account.id
        ):
            skipped += 1
            logger.warning(
                "refused GitHub App installation %s for account %s: "
                "already bound to another brnrd account",
                installation_id,
                account.id,
            )
            continue
        target = installation.get("account") or {}
        target_login = (
            str(target.get("login") or "") if isinstance(target, dict) else ""
        )
        target_type = (
            str(target.get("type") or "") if isinstance(target, dict) else ""
        )
        verified = (
            target_type.casefold() == "user"
            and target_login.casefold() == account.github_login.casefold()
        )
        if (
            target_type.casefold() == "organization"
            and installation_id
            and target_login
        ):
            membership = gh_app.organization_membership(
                settings,
                installation_id,
                target_login,
                account.github_login,
            )
            verified = bool(
                membership
                and str(membership.get("state") or "").casefold() == "active"
                and str(membership.get("role") or "").casefold() == "admin"
            )
        if not installation_id or not verified:
            skipped += 1
            logger.warning(
                "refused GitHub App installation %s for account %s: "
                "target %s %r is not owned by authenticated GitHub user %r",
                installation_id or "<missing>",
                account.id,
                target_type or "<missing>",
                target_login or "<missing>",
                account.github_login,
            )
            continue
        sync_installation(db, settings, installation_id, account.id)
        synced += 1
    return InstallationSyncResult(synced=synced, skipped=skipped)


def sync_app_installations_for_account(
    db: Session,
    settings,
    account_id: str,
    *,
    user_access_token: str | None = None,
) -> InstallationSyncResult:
    """Sync personal installs owned by the user and org installs they own.

    Login-time discovery uses GitHub's user-scoped installation view. Later
    refreshes fetch only installations already bound to this account. Personal
    target equality or active organization-owner membership is still the
    proof before attachment; visibility alone is not ownership.
    """
    account = db.get(Account, account_id)
    if account is None:
        raise RuntimeError(f"GitHub installation sync account not found: {account_id}")
    if user_access_token:
        installations = gh_app.list_user_installations(
            settings, user_access_token
        )
    else:
        bound_ids = db.execute(
            select(GitHubInstallation.installation_id).where(
                GitHubInstallation.account_id == account_id
            )
        ).scalars()
        installations = [
            gh_app.get_app_installation(settings, installation_id)
            for installation_id in bound_ids
        ]
    return _sync_verified_installations(db, settings, account, installations)


def sync_app_installation_for_account(
    db: Session, settings, account_id: str, installation_id: str
) -> InstallationSyncResult:
    """Sync one setup-callback installation after proving its target login."""
    account = db.get(Account, account_id)
    if account is None:
        raise RuntimeError(f"GitHub installation sync account not found: {account_id}")
    try:
        installation = gh_app.get_app_installation(settings, installation_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        logger.warning(
            "refused GitHub App installation %s for account %s: "
            "installation is not visible to the configured App",
            installation_id,
            account_id,
        )
        return InstallationSyncResult(skipped=1)
    return _sync_verified_installations(db, settings, account, [installation])


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
            if account_id is None:
                sync_installation(
                    db, request.app.state.settings, installation_id, None
                )
                notice = "github-synced"
            else:
                result = sync_app_installation_for_account(
                    db, request.app.state.settings, account_id, installation_id
                )
                notice = github_sync_notice(result)
        except Exception as e:
            print(f"[brnrd] github installation sync failed: {e}")
            notice = "github-sync-failed"
    params = {k: v for k, v in {"installation_id": installation_id, "setup_action": setup_action, "notice": notice}.items() if v}
    # Land on /repos, not the bare dashboard (#1084): /repos is the screen
    # that reads `installations` / `installed_repos` and can act on what a
    # GitHub App install just produced (enable a repo) — the dashboard's own
    # cold-start block only ever names /repos as "another page" to go to.
    # This is also what actually happened live: the reporter's own unblock
    # was "setting enable button here https://brnrd.dev/repos". An anonymous
    # arrival (no session cookie yet) still lands here — /repos already
    # degrades to a sign-in link with `next=/repos` for that case.
    return RedirectResponse(url=f"/repos?{urlencode(params)}", status_code=303)


@router.post("/sync")
def github_installation_sync(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    account_id = account_id_from_session_cookie(request, db)
    if account_id is None:
        return RedirectResponse(url="/login?next=/", status_code=303)
    try:
        result = sync_app_installations_for_account(
            db, request.app.state.settings, account_id
        )
        notice = github_sync_notice(result)
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
        x_github_event == "issue_comment"
        or github_summons.resolve_github_summons(
            x_github_event,
            payload,
            github_summons.github_identity_candidates(settings),
            settings.github_trigger_label,
        ) is not None
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

                if x_github_event == "issue_comment":
                    webhooks._handle_github_issue_comment(
                        db,
                        settings,
                        payload,
                        token=token,
                        installation_id=installation_id,
                    )
                else:
                    webhooks._handle_github_summons(
                        db,
                        settings,
                        x_github_event,
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
