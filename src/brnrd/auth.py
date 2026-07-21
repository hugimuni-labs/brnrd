"""Bearer-token authentication dependencies."""

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
    def repo_id(self) -> str | None:
        return self.token.repo_id


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
    token = db.execute(select(Token).where(Token.token_hash == hash_token(raw))).scalar_one_or_none()
    if token is None or token.revoked:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    expires_at = token.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="expired token")
    return token


def require_account(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Principal:
    token = _resolve(db, _bearer(authorization))
    if token.kind not in Token.ACCOUNT_KINDS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account credential required")
    return Principal(token=token)


def require_account_or_session(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Principal:
    """`require_account`, extended to the browser session cookie.

    Bearer wins when present (API clients keep their exact contract). With no
    Authorization header, the ``brnrd_session`` cookie — the same ``Token`` row
    ``routers/_session.py`` resolves for the dashboard — yields the account
    principal, so the SPA can call the billing surface through the seam it
    already authenticates with.

    CSRF posture, deliberate: the session cookie is issued ``HttpOnly`` +
    ``SameSite=Lax`` (routers/web_auth.py) and the app registers no CORS
    middleware, so a cross-site page can neither read responses nor attach the
    cookie to a POST. Only session-kind tokens ride this path — an API-key
    token in a cookie is refused rather than silently honored.
    """
    if authorization is None:
        cookie = request.cookies.get(request.app.state.settings.session_cookie)
        if cookie:
            token = _resolve(db, cookie)
            if token.kind != Token.KIND_SESSION:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="session credential required")
            return Principal(token=token)
    return require_account(authorization=authorization, db=db)


def require_daemon(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Principal:
    token = _resolve(db, _bearer(authorization))
    if token.kind != Token.KIND_DAEMON:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="daemon token required")
    return Principal(token=token)
