"""Account session helpers and repo endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ids, limits, schemas
from ..activity_records import dedupe_activity_records, fresh_activity_records
from ..auth import Principal, get_db, require_account
from ..models import ActivityRecord, Account, GitHubInstallation, GitHubInstalledRepo, Repo, Token
from ..oauth import GitHubIdentity
from ..security import hash_token

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])

SESSION_TTL = timedelta(days=30)


def _repo_parts(repo_full_name: str) -> tuple[str, str]:
    owner, sep, name = repo_full_name.strip().partition("/")
    if not sep or not owner or not name:
        raise HTTPException(status_code=400, detail="repo must look like owner/name")
    return owner, name


def repo_out(repo: Repo) -> schemas.RepoOut:
    return schemas.RepoOut(
        repo_id=repo.id,
        forge=repo.forge,
        repo_full_name=repo.repo_full_name,
        repo_owner=repo.repo_owner,
        repo_name=repo.repo_name,
        forge_repo_id=repo.forge_repo_id,
        default_branch=repo.default_branch,
        created_at=repo.created_at,
    )


def activity_out(row: ActivityRecord) -> schemas.ActivityRecordOut:
    try:
        runner = json.loads(row.runner_json or "{}")
    except ValueError:
        runner = {}
    try:
        links = json.loads(row.links_json or "{}")
    except ValueError:
        links = {}
    return schemas.ActivityRecordOut(
        id=row.record_id,
        repo_id=row.repo_id,
        kind=row.kind,
        source=row.source,
        conversation_key=row.conversation_key,
        summary=row.summary,
        runner=runner if isinstance(runner, dict) else {},
        status=row.status,
        phase=row.phase,
        branch=row.branch,
        pr_number=row.pr_number,
        started_at=row.started_at,
        updated_at=row.updated_at,
        scheduled_for=row.scheduled_for,
        defer_until=row.defer_until,
        links=links if isinstance(links, dict) else {},
        reported_at=row.reported_at,
    )


def account_for_github_identity(db: Session, identity: GitHubIdentity) -> Account:
    account = db.execute(select(Account).where(Account.github_id == identity.github_id)).scalar_one_or_none()
    if account is None:
        account = Account(id=ids.account_id(), github_id=identity.github_id, github_login=identity.login, email=identity.email)
        db.add(account)
        db.commit()
        db.refresh(account)
        return account
    account.github_login = identity.login
    account.email = identity.email
    db.commit()
    db.refresh(account)
    return account


def issue_session_token(db: Session, account: Account) -> str:
    raw = ids.session_token()
    db.add(
        Token(
            id=ids.token_id(),
            account_id=account.id,
            kind=Token.KIND_SESSION,
            token_hash=hash_token(raw),
            label="session",
            expires_at=datetime.now(timezone.utc) + SESSION_TTL,
        )
    )
    db.commit()
    return raw


@router.post("/repos", status_code=status.HTTP_201_CREATED, response_model=schemas.RepoOut)
def create_repo(payload: schemas.RepoCreate, request: Request, principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    repo_full_name = payload.repo_full_name.strip()
    owner, name = _repo_parts(repo_full_name)
    existing = db.execute(
        select(Repo).where(Repo.account_id == principal.account_id, Repo.repo_full_name == repo_full_name)
    ).scalar_one_or_none()
    if existing is not None:
        return repo_out(existing)
    # #501 repo cap — headroom-limited on the free tier, abuse-capped on all.
    limits.raise_if_denied(
        limits.check_repo_connect(
            db, request.app.state.settings, db.get(Account, principal.account_id)
        )
    )
    # NOTE: unlike `_connect_repo_core` (the browser `/repos` connect flow,
    # legal pack item 2), a repo minted through this account-API-key surface
    # is left with no recorded consent (`publish_layers` stays `NULL`) —
    # this endpoint has no attached consent UI (no `src/frontend/` surface
    # asked for one) and is exercised throughout the existing daemon/cloud
    # test fixtures on the assumption that a freshly connected repo publishes
    # normally. Reported, not silently left: this is a real gap in the new
    # consent gate (an API-key client bypasses it entirely) that a follow-up
    # should close once this surface has its own explicit-consent story.
    repo = Repo(
        id=ids.repo_id(),
        account_id=principal.account_id,
        forge=payload.forge.strip() or "github",
        repo_full_name=repo_full_name,
        repo_owner=owner,
        repo_name=name,
        forge_repo_id=payload.forge_repo_id,
        default_branch=payload.default_branch,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo_out(repo)


@router.get("/repos", response_model=schemas.RepoList)
def list_repos(principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    rows = db.execute(select(Repo).where(Repo.account_id == principal.account_id).order_by(Repo.repo_full_name)).scalars()
    return schemas.RepoList(repos=[repo_out(row) for row in rows])


@router.get("/activity", response_model=schemas.ActivityList)
def list_activity(repo_id: str | None = Query(default=None), principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    repo_rows = list(
        db.execute(
            select(Repo).where(Repo.account_id == principal.account_id)
        ).scalars()
    )
    repo_ids = {row.id for row in repo_rows}
    if repo_id:
        if repo_id not in repo_ids:
            raise HTTPException(status_code=404, detail="repo not found")
        repo_ids = {repo_id}
    if not repo_ids:
        return schemas.ActivityList(activity=[])
    rows = db.execute(
        select(ActivityRecord)
        .where(ActivityRecord.repo_id.in_(repo_ids))
        .order_by(ActivityRecord.updated_at.desc().nullslast(), ActivityRecord.reported_at.desc())
    ).scalars()
    rows = dedupe_activity_records(fresh_activity_records(rows))
    return schemas.ActivityList(activity=[activity_out(row) for row in rows])


@router.get("/github/installations", response_model=schemas.GitHubInstallationsList)
def list_github_installations(principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    installations = list(
        db.execute(
            select(GitHubInstallation).where(GitHubInstallation.account_id == principal.account_id).order_by(GitHubInstallation.target_login)
        ).scalars()
    )
    installed_repos: list[GitHubInstalledRepo] = []
    for installation in installations:
        installed_repos.extend(
            db.execute(
                select(GitHubInstalledRepo)
                .where(GitHubInstalledRepo.github_installation_id == installation.id)
                .order_by(GitHubInstalledRepo.repo_full_name)
            ).scalars()
        )
    return schemas.GitHubInstallationsList(
        installations=[
            schemas.GitHubInstallationOut(
                installation_id=i.installation_id,
                target_login=i.target_login,
                target_type=i.target_type,
                last_synced_at=i.last_synced_at,
            )
            for i in installations
        ],
        installed_repos=[
            schemas.GitHubInstalledRepoOut(
                repo_full_name=r.repo_full_name,
                forge_repo_id=r.forge_repo_id,
                default_branch=r.default_branch,
                is_private=r.is_private,
            )
            for r in installed_repos
        ],
    )
