"""Repo-management JSON actions for the brnrd dashboard (``/v1/repos/*``).

Migrated from ``src/brnrd_web/routes.py`` when ``brnrd_web`` was folded
into ``src/brnrd/routers/``. Route paths and response shapes are
byte-compatible with the previous module.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from brnrd.auth import get_db
from sqlalchemy.orm import Session

from ._session import (
    _connect_repo_core,
    _disconnect_repo_core,
    _json_account,
    _json_body,
    _pair_repo_telegram_core,
    _payload_str,
    _repo_action_response,
    _repo_error_response,
)
from fastapi import HTTPException

router = APIRouter(tags=["web"])


@router.post("/v1/repos/connect")
async def connect_repo_api(request: Request, db: Session = Depends(get_db)):
    account = _json_account(request, db)
    payload = await _json_body(request)
    try:
        notice = _connect_repo_core(
            request,
            db,
            account,
            repo_full_name=_payload_str(payload, "repo_full_name"),
            forge_repo_id=_payload_str(payload, "forge_repo_id"),
            default_branch=_payload_str(payload, "default_branch"),
        )
    except HTTPException as exc:
        return _repo_error_response(exc)
    return _repo_action_response(notice)


@router.post("/v1/repos/{repo_id}/telegram-pair")
def pair_repo_telegram_api(repo_id: str, request: Request, db: Session = Depends(get_db)):
    account = _json_account(request, db)
    try:
        pair = _pair_repo_telegram_core(request, db, account.id, repo_id)
    except HTTPException as exc:
        return _repo_error_response(exc)
    return _repo_action_response(
        "Pair this Telegram chat",
        pairing_code=pair.pair_code,
        instructions=pair.instructions,
        action_url=pair.deep_link,
    )


@router.post("/v1/repos/{repo_id}/disconnect")
def disconnect_repo_api(repo_id: str, request: Request, db: Session = Depends(get_db)):
    account = _json_account(request, db)
    try:
        notice = _disconnect_repo_core(db, account.id, repo_id)
    except HTTPException as exc:
        return _repo_error_response(exc)
    return _repo_action_response(notice)
