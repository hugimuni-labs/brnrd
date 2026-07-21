"""Dev ingress — a webhook stand-in for the prototype."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import inbox as inbox_service, limits, schemas
from ..auth import Principal, get_db, require_account
from ..models import Account, Repo

router = APIRouter(prefix="/v1/_dev", tags=["dev"])


@router.post("/enqueue", status_code=status.HTTP_201_CREATED, response_model=schemas.DevEnqueued)
def enqueue(payload: schemas.DevEnqueue, request: Request, principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    repo = db.execute(select(Repo).where(Repo.id == payload.repo_id, Repo.account_id == principal.account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    account = db.get(Account, principal.account_id)
    limits.raise_if_denied(
        limits.check_event_admission(
            db,
            request.app.state.settings,
            account,
            body=payload.body,
            attachment_count=len(payload.attachments or []),
        )
    )
    event = inbox_service.enqueue(db, repo_id=repo.id, body=payload.body, source=payload.source, reply_to=payload.reply_to, attachments=payload.attachments or None)
    return schemas.DevEnqueued(event_id=event.event_id, seq=event.seq)
