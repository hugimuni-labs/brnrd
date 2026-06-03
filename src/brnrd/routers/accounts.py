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
from ..models import Account, Project, Token
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
