"""Dev ingress — a webhook stand-in for the prototype.

``POST /v1/_dev/enqueue`` queues an event into a project the way a
real ``/v1/webhooks/{telegram,github}`` dispatch would, so the
queue / drain / respond loop is exercisable end-to-end without a
live platform. Disabled in production (``BRNRD_ENABLE_DEV=0``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import inbox as inbox_service
from .. import schemas
from ..auth import Principal, get_db, require_account
from ..models import Project

router = APIRouter(prefix="/v1/_dev", tags=["dev"])


@router.post("/enqueue", status_code=status.HTTP_201_CREATED, response_model=schemas.DevEnqueued)
def enqueue(
    payload: schemas.DevEnqueue,
    principal: Principal = Depends(require_account),
    db: Session = Depends(get_db),
):
    project = db.execute(
        select(Project).where(
            Project.id == payload.project_id,
            Project.account_id == principal.account_id,
        )
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    event = inbox_service.enqueue(
        db,
        project_id=project.id,
        body=payload.body,
        source=payload.source,
        reply_to=payload.reply_to,
    )
    return schemas.DevEnqueued(event_id=event.event_id, seq=event.seq)
