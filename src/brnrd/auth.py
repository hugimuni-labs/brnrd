"""Bearer-token authentication dependencies.

A single ``tokens`` table backs every credential; the dependency
resolves the token by hash and then enforces the *kind* the
endpoint requires. Account endpoints accept API keys or sessions;
daemon endpoints accept only project-scoped daemon tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Token
from .security import hash_token


@dataclass
class Principal:
    token: Token

    @property
    def account_id(self) -> str:
        return self.token.account_id

    @property
    def project_id(self) -> str | None:
        return self.token.project_id


def get_db(request: Request) -> Session:
    session_factory = request.app.state.SessionLocal
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization[7:].strip()


def _resolve(db: Session, raw: str) -> Token:
    token = db.execute(
        select(Token).where(Token.token_hash == hash_token(raw))
    ).scalar_one_or_none()
    if token is None or token.revoked:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    if token.expires_at is not None and token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="expired token")
    return token


def require_account(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Principal:
    token = _resolve(db, _bearer(authorization))
    if token.kind not in Token.ACCOUNT_KINDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account credential required",
        )
    return Principal(token=token)


def require_daemon(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Principal:
    token = _resolve(db, _bearer(authorization))
    if token.kind != Token.KIND_DAEMON or not token.project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="daemon token required",
        )
    return Principal(token=token)
