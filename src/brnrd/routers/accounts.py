"""Account session helpers and project endpoints.

GitHub OAuth is the only brnrd account-creation path. These API
endpoints keep using brnrd bearer credentials for account-scoped
actions, but there is no email/password signup or login surface.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ids, schemas
from ..auth import Principal, get_db, require_account
from ..models import Account, Project, RepoBinding, Token
from ..oauth import GitHubIdentity
from ..security import hash_token

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])

SESSION_TTL = timedelta(days=30)


def _ensure_default_project(db: Session, account: Account) -> Project:
    project = db.execute(
        select(Project).where(
            Project.account_id == account.id, Project.name == "default"
        )
    ).scalar_one_or_none()
    if project is not None:
        return project
    project = Project(id=ids.project_id(), account_id=account.id, name="default")
    db.add(project)
    return project


def account_for_github_identity(db: Session, identity: GitHubIdentity) -> Account:
    """Return the brnrd account for a GitHub user, creating it if needed."""
    account = db.execute(
        select(Account).where(Account.github_id == identity.github_id)
    ).scalar_one_or_none()
    if account is None:
        account = Account(
            id=ids.account_id(),
            github_id=identity.github_id,
            github_login=identity.login,
            email=identity.email,
        )
        db.add(account)
        _ensure_default_project(db, account)
        db.commit()
        db.refresh(account)
        return account

    # GitHub logins and primary emails can change; GitHub id is the stable
    # identity key. Keep the display/contact fields fresh at login time.
    account.github_login = identity.login
    account.email = identity.email
    _ensure_default_project(db, account)
    db.commit()
    db.refresh(account)
    return account


def issue_session_token(db: Session, account: Account) -> str:
    """Mint + persist a session token, returning the plaintext once."""
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


@router.post(
    "/projects", status_code=status.HTTP_201_CREATED, response_model=schemas.ProjectOut
)
def create_project(
    payload: schemas.ProjectCreate,
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    # Idempotent on (account_id, name): re-running connect with the same
    # project name returns the existing project rather than erroring.
    existing = db.execute(
        select(Project).where(
            Project.account_id == principal.account_id, Project.name == name
        )
    ).scalar_one_or_none()
    if existing is not None:
        return schemas.ProjectOut(
            project_id=existing.id, name=existing.name, created_at=existing.created_at
        )

    project = Project(id=ids.project_id(), account_id=principal.account_id, name=name)
    db.add(project)
    db.commit()
    db.refresh(project)
    return schemas.ProjectOut(
        project_id=project.id, name=project.name, created_at=project.created_at
    )


@router.get("/projects", response_model=schemas.ProjectList)
def list_projects(
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Project)
        .where(Project.account_id == principal.account_id)
        .order_by(Project.created_at)
    ).scalars()
    return schemas.ProjectList(
        projects=[
            schemas.ProjectOut(project_id=p.id, name=p.name, created_at=p.created_at)
            for p in rows
        ]
    )


@router.post(
    "/bindings/repo",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.RepoBindingOut,
)
def bind_repo(
    payload: schemas.RepoBindingCreate,
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    repo_full_name = payload.repo_full_name.strip()
    installation_id = payload.installation_id.strip()
    project = db.execute(
        select(Project).where(
            Project.id == payload.project_id,
            Project.account_id == principal.account_id,
        )
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    existing = db.execute(
        select(RepoBinding).where(RepoBinding.repo_full_name == repo_full_name)
    ).scalar_one_or_none()
    if existing is not None and existing.account_id != principal.account_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="repository is already bound to another account",
        )
    if existing is None:
        existing = RepoBinding(
            id=ids.repo_binding_id(),
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            account_id=principal.account_id,
            project_id=project.id,
        )
        db.add(existing)
    else:
        existing.installation_id = installation_id
        existing.project_id = project.id
    db.commit()
    db.refresh(existing)
    return schemas.RepoBindingOut(
        binding_id=existing.id,
        installation_id=existing.installation_id,
        repo_full_name=existing.repo_full_name,
        project_id=existing.project_id,
    )


@router.get("/bindings/repo", response_model=schemas.RepoBindingList)
def list_repo_bindings(
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(RepoBinding)
        .where(RepoBinding.account_id == principal.account_id)
        .order_by(RepoBinding.created_at)
    ).scalars()
    return schemas.RepoBindingList(
        bindings=[
            schemas.RepoBindingOut(
                binding_id=b.id,
                installation_id=b.installation_id,
                repo_full_name=b.repo_full_name,
                project_id=b.project_id,
            )
            for b in rows
        ]
    )
