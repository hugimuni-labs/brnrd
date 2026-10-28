"""The board's data in the cloud: the loom's parts, read by a signed-in person.

The same contract the loom's loopback serves (``brr.loom.runtime.board.handler``),
so one SPA reads either: GET ``/v1/loom/{index,beads,thread/<id>}`` with ETag,
GET ``/v1/loom/events`` (SSE of the index hash), POST ``/v1/loom/letters``.
The parts come from ``PUT /v1/daemons/loom/parts``; every read is scoped to the
session's account, and the account's loom is the daemon whose index moved last.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from brnrd import schemas
from brnrd.auth import get_db
from brnrd.models import Account, LoomPart

from ._session import _account_id, _json_body
from .dashboard import enqueue_loom_letter

router = APIRouter(prefix="/v1/loom", tags=["loom"])

#: The stream's poll of the index hash, and how long one stream lives before the
#: browser's EventSource reconnects (under the container's request timeout).
EVENTS_POLL_S = 1.0
EVENTS_LIFE_S = 240.0
EVENTS_KEEPALIVE_S = 15.0
#: Where a board letter lands, as the loopback's ``home.main_thread()``.
MAIN_THREAD = "inbox"


def _loom_daemon(db: Session, account_id: str) -> str | None:
    return db.execute(
        select(LoomPart.daemon_id)
        .where(LoomPart.account_id == account_id, LoomPart.key == "index")
        .order_by(LoomPart.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _part(db: Session, account_id: str, key: str) -> LoomPart | None:
    daemon_id = _loom_daemon(db, account_id)
    if daemon_id is None:
        return None
    return db.execute(
        select(LoomPart).where(
            LoomPart.daemon_id == daemon_id, LoomPart.account_id == account_id, LoomPart.key == key
        )
    ).scalar_one_or_none()


def _not_modified(request: Request, etag: str) -> Response | None:
    if request.headers.get("if-none-match") == f'"{etag}"':
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})
    return None


def _read(request: Request, db: Session, key: str, before: str | None = None, until: str | None = None) -> Response:
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    row = _part(db, account_id, key)
    if row is None:
        return JSONResponse({"error": f"no part {key}"}, status_code=404)
    headers = {"Cache-Control": "no-cache"}
    if not key.startswith("thread/"):
        if (held := _not_modified(request, row.hash)) is not None:
            return held
        headers["ETag"] = f'"{row.hash}"'
        if "gzip" in (request.headers.get("accept-encoding") or ""):
            return Response(row.body, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
        return Response(gzip.decompress(row.body), media_type="application/json", headers=headers)
    from brr.loom.runtime.board import digest, window

    part = json.loads(gzip.decompress(row.body))
    if before is not None or until is not None:
        return JSONResponse(window(part, before, until), headers=headers)
    cut = window(part)
    etag = digest(cut)
    if (held := _not_modified(request, etag)) is not None:
        return held
    return JSONResponse(cut, headers={**headers, "ETag": f'"{etag}"'})


@router.get("/index")
def loom_index(request: Request, db: Session = Depends(get_db)) -> Response:
    return _read(request, db, "index")


@router.get("/beads")
def loom_beads(request: Request, db: Session = Depends(get_db)) -> Response:
    return _read(request, db, "beads")


@router.get("/thread/{thread_id}")
def loom_thread(
    thread_id: str, request: Request, before: str | None = None, until: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    return _read(request, db, f"thread/{thread_id}", before, until)


@router.get("/events")
async def loom_events(request: Request) -> Response:
    """The index hash whenever it moves, polled from the store each second.

    No request-scoped session: one would hold a pooled connection for the
    stream's whole life. Each poll opens and closes its own.
    """
    factory = request.app.state.SessionLocal
    with factory() as db:
        account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    def current() -> str | None:
        with factory() as fresh:
            row = _part(fresh, account_id, "index")
            return row.hash if row is not None else None

    async def stream():
        yield b": " + b" " * 2048 + b"\n\n"  # past a proxy's first buffer
        seen, quiet, began = None, time.monotonic(), time.monotonic()
        while time.monotonic() - began < EVENTS_LIFE_S:
            if await request.is_disconnected():
                return
            now = await asyncio.to_thread(current)
            if now and now != seen:
                seen, quiet = now, time.monotonic()
                yield f"event: index\ndata: {now}\n\n".encode()
            elif time.monotonic() - quiet >= EVENTS_KEEPALIVE_S:
                quiet = time.monotonic()
                yield b": keepalive\n\n"
            await asyncio.sleep(EVENTS_POLL_S)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/letters")
async def loom_letter(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """The board's composer, as the loopback's: ``{body, wish?, verdict?}`` into the main thread, via the relay."""
    account_id = _account_id(request, db)
    account = db.get(Account, account_id) if account_id else None
    if account is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    data = await _json_body(request)
    if not isinstance(data, dict):
        return JSONResponse({"error": "a JSON object"}, status_code=400)
    try:
        payload = schemas.LoomLetterPost(
            thread=MAIN_THREAD, text=str(data.get("body") or ""),
            wish=data.get("wish") or None, verdict=data.get("verdict") or None,
        )
    except ValidationError as exc:
        return JSONResponse({"error": exc.errors(include_url=False, include_context=False)[0]["msg"]}, status_code=400)
    queued = enqueue_loom_letter(request, db, account, payload)
    if isinstance(queued, JSONResponse):
        return JSONResponse({"error": json.loads(queued.body)["detail"]}, status_code=queued.status_code)
    return JSONResponse({"id": f"event:{queued.event_id}", "thread": MAIN_THREAD})
