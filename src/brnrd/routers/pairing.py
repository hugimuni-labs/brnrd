"""Device-flow connect handshake for local repo daemons and Telegram routes."""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ids, schemas
from ..auth import Principal, get_db, require_account
from ..models import PairRequest, Repo, TgPairCode, Token
from ..security import hash_token

router = APIRouter(prefix="/v1/accounts/pair", tags=["pairing"])


def _get_pair(db: Session, code: str) -> PairRequest:
    pair = db.execute(select(PairRequest).where(PairRequest.pair_code == code)).scalar_one_or_none()
    if pair is None:
        raise HTTPException(status_code=404, detail="unknown pair code")
    expires = pair.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="pair code expired")
    return pair


@router.post("", response_model=schemas.PairStarted)
def start_pair(request: Request, db: Session = Depends(get_db)):
    settings = request.app.state.settings
    for _ in range(8):
        code = ids.pair_code()
        if not db.execute(select(PairRequest).where(PairRequest.pair_code == code)).scalar_one_or_none():
            break
    else:
        raise HTTPException(status_code=503, detail="could not allocate pair code")

    secret = ids.poll_secret()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.pair_ttl_s)
    db.add(PairRequest(id=ids.pair_request_id(), pair_code=code, poll_secret_hash=hash_token(secret), status=PairRequest.STATUS_PENDING, expires_at=expires_at))
    db.commit()
    return schemas.PairStarted(pair_code=code, pair_url=f"{settings.public_base_url.rstrip()}/connect/{code}", poll_secret=secret, expires_at=expires_at)


def approve_core(db: Session, account_id: str, code: str, repo_id: str) -> str:
    pair = _get_pair(db, code)
    if pair.status == PairRequest.STATUS_CONSUMED:
        raise HTTPException(status_code=409, detail="pair code already used")
    repo = db.execute(select(Repo).where(Repo.id == repo_id, Repo.account_id == account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    raw = ids.daemon_token()
    db.add(Token(id=ids.token_id(), account_id=account_id, repo_id=repo.id, kind=Token.KIND_DAEMON, token_hash=hash_token(raw), label="daemon (paired)"))
    pair.status = PairRequest.STATUS_APPROVED
    pair.account_id = account_id
    pair.repo_id = repo.id
    pair.minted_token = raw
    db.commit()
    return repo.id


@router.post("/{code}/approve", response_model=schemas.PairStatus)
def approve_pair(code: str, payload: schemas.PairApprove, principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    repo_id = approve_core(db, principal.account_id, code, payload.repo_id)
    return schemas.PairStatus(status="approved", repo_id=repo_id)


@router.post("/telegram", response_model=schemas.TelegramPairStarted)
def start_telegram_pair(payload: schemas.TelegramPairStart, request: Request, principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    repo = db.execute(select(Repo).where(Repo.id == payload.repo_id, Repo.account_id == principal.account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    for _ in range(8):
        code = ids.tg_pair_code()
        if not db.execute(select(TgPairCode).where(TgPairCode.code == code)).scalar_one_or_none():
            break
    else:
        raise HTTPException(status_code=503, detail="could not allocate pair code")
    settings = request.app.state.settings
    db.add(TgPairCode(id=ids.tg_pair_code_id(), code=code, account_id=principal.account_id, repo_id=repo.id, expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.pair_ttl_s)))
    db.commit()
    username = settings.telegram_bot_username.lstrip("@")
    deep_link = f"https://t.me/{username}?start={code}" if username else None
    if deep_link:
        instructions = f"Open {deep_link} or send `/start {code}` to bind this chat to repo '{repo.repo_full_name}'."
    else:
        instructions = f"Send `/start {code}` to your brnrd Telegram bot to bind this chat to repo '{repo.repo_full_name}'."
    return schemas.TelegramPairStarted(pair_code=code, instructions=instructions, deep_link=deep_link)


@router.get("/{code}", response_model=schemas.PairStatus)
def poll_pair(code: str, poll_secret: str = Query(...), db: Session = Depends(get_db)):
    pair = _get_pair(db, code)
    if not hmac.compare_digest(hash_token(poll_secret), pair.poll_secret_hash):
        raise HTTPException(status_code=401, detail="bad poll secret")
    if pair.status == PairRequest.STATUS_PENDING:
        return schemas.PairStatus(status="pending")
    if pair.status == PairRequest.STATUS_APPROVED:
        token = pair.minted_token
        pair.status = PairRequest.STATUS_CONSUMED
        pair.minted_token = None
        db.commit()
        return schemas.PairStatus(status="paired", repo_id=pair.repo_id, daemon_token=token)
    return schemas.PairStatus(status="paired", repo_id=pair.repo_id)
