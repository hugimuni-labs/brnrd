"""`GET /v1/machines` — the account's paired daemons, account-keyed.

design-machines-and-guests.md R1, #1365. Every other daemon-facing view in
this codebase (`dashboard.py`'s `/v1/dashboard/repos`, `_session._repo_views`)
starts from a *repo* and asks which daemon serves it — reasonable for a
repo-management page, but it means a machine that has paired with zero
enabled repos has no repo row to hang off, so it never appears anywhere
account-level (the ColdStart bug this R1 also fixes). This endpoint starts
from the account instead: `Daemon.account_id` is already the account-scoped
identity (models.py:181-190) — `repo_id` is only default-routing metadata,
so a machine belongs here whether or not it currently names a repo.

Session-cookie auth, same convention as `dashboard.py`'s dashboard-web GETs
(`_account_id` + a 401 `JSONResponse` on no session) — this is a dashboard
surface, not a daemon-token one, so it does not use `require_daemon`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import get_db
from ._session import _account_id, _machine_views

router = APIRouter(tags=["web"])


def _machine_out(view: dict) -> schemas.MachineOut:
    return schemas.MachineOut(
        daemon_id=view["daemon"].id,
        daemon_name=view["daemon"].daemon_name,
        online=view["online"],
        last_seen=view["last_seen"],
        enabled_repos=[
            schemas.MachineRepoOut(repo_id=repo.id, repo_full_name=repo.repo_full_name)
            for repo in view["enabled_repos"]
        ],
    )


@router.get("/v1/machines")
def machines_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    machines = [_machine_out(view) for view in _machine_views(db, account_id)]
    out = schemas.MachinesOut(generated_at=datetime.now(timezone.utc), machines=machines)
    return JSONResponse(out.model_dump(mode="json"))
