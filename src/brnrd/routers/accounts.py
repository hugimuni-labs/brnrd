"""Account, session, and project endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ids, schemas
from ..auth import Principal, get_db, require_account
from ..models import Account, Project, Token
from ..security import hash_password, hash_token, verify_password

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])

_SESSION_TTL = timedelta(days=30)


def authenticate(db: Session, email: str, password: str) -> Account | None:
    """Return the account if the email/password match, else None.

    Shared by the API login endpoint and the web dashboard login.
    """
    account = db.execute(
        select(Account).where(Account.email == email.strip().lower())
    ).scalar_one_or_none()
    if account is None or not verify_password(password, account.password_hash):
        return None
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
            expires_at=datetime.now(timezone.utc) + _SESSION_TTL,
        )
    )
    db.commit()
    return raw


@router.post("", status_code=status.HTTP_201_CREATED, response_model=schemas.AccountCreated)
def create_account(payload: schemas.AccountCreate, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="invalid email")
    if db.execute(select(Account).where(Account.email == email)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="email already registered")

    account = Account(
        id=ids.account_id(),
        email=email,
        password_hash=hash_password(payload.password),
    )
    db.add(account)

    raw_key = ids.api_key()
    db.add(
        Token(
            id=ids.token_id(),
            account_id=account.id,
            kind=Token.KIND_API_KEY,
            token_hash=hash_token(raw_key),
            label="initial",
        )
    )
    db.commit()
    return schemas.AccountCreated(account_id=account.id, api_key=raw_key)


@router.post("/sessions", response_model=schemas.SessionCreated)
def login(payload: schemas.SessionCreate, db: Session = Depends(get_db)):
    account = authenticate(db, payload.email, payload.password)
    if account is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    raw = issue_session_token(db, account)
    return schemas.SessionCreated(account_id=account.id, session_token=raw)


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
