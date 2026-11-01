"""The board's data in the cloud: the loom's parts, read by a signed-in person.

The same contract the loom's loopback serves (``brr.loom.runtime.board.handler``),
so one SPA reads either: GET ``/v1/loom/{index,beads,thread/<id>}`` with ETag,
GET ``/v1/loom/events`` (SSE of the index hash), POST ``/v1/loom/letters``. GET/POST ``/v1/loom/consent``
is the board prompt: whether the account's loom may send its board here, asked once on brnrd.dev.
The parts come from ``PUT /v1/daemons/loom/parts``; every read is scoped to the
session's account, and the account's loom is the daemon whose index moved last.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time
from urllib.parse import quote
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from brnrd import publish_scope, schemas
from brnrd.auth import get_db
from brnrd.looms import loom_repos as _loom_repos
from brnrd.models import Account, BoardConsent, Daemon, LoomPart, Repo

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
    # The id the loom's ledger letter ends with (relay `message_id`: platform:login:message_id), so the
    # board's pending echo resolves into it wherever routing lands it, not only on the thread it was sent to.
    origin = json.loads(queued.reply_to or "{}")
    native = ":".join(quote(str(origin.get(k) or ""), safe="") for k in ("platform", "user_id", "message_id"))
    return JSONResponse({"id": native, "thread": MAIN_THREAD})


#: The one publish lane a loom's board rides (`PUT /v1/daemons/loom{,/parts}`).
BOARD_LANE = "activity"


def _iso(at: datetime | None) -> str | None:
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat().replace("+00:00", "Z")


@router.get("/consent")
def loom_consent(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """``{state}``: ``none`` (no loom), ``ask`` (never answered), ``declined``, or ``granted``.

    Granted is read from the scope itself, so a repo whose person ticked the lane
    before this prompt existed is not asked again.
    """
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    looms = _loom_repos(db, account_id)
    if not looms:
        return JSONResponse({"state": "none"})
    repo, daemon = looms[0]
    if publish_scope.lane_permitted(db, repo_id=repo.id, lane=BOARD_LANE):
        state = "granted"
    elif db.get(BoardConsent, repo.id) is None:
        state = "ask"
    else:
        state = "declined"
    return JSONResponse({"state": state, "repo": repo.repo_full_name, "last_seen_at": _iso(daemon.last_seen_at)})


@router.post("/consent")
async def loom_consent_answer(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """``{send: bool}`` for every loom repo of the account: add or drop the board lane, stamp the answer.

    A no also deletes the copy held here, through the same purge a settings
    withdrawal runs, in the same commit as the scope.
    """
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    data = await _json_body(request)
    send = data.get("send")
    if not isinstance(send, bool):
        return JSONResponse({"error": "send: true or false"}, status_code=400)
    looms = _loom_repos(db, account_id)
    if not looms:
        return JSONResponse({"error": "no loom on this account"}, status_code=404)
    for repo in {repo.id: repo for repo, _ in looms}.values():
        answer_board(db, repo, send)
    db.commit()
    return JSONResponse({"state": "granted" if send else "declined"})


def answer_board(db: Session, repo: Repo, send: bool) -> None:
    """Add or drop the board lane on ``repo`` and stamp the answer; the caller commits.

    A signed-in person's answer only: the consent prompt here, and the box on
    the approve page a loom pair opens (``connect_approve_api``).
    """
    now = datetime.now(timezone.utc)
    lanes, slices = publish_scope._repo_scopes(repo.publish_layers)
    lanes = set(lanes) | {BOARD_LANE} if send else set(lanes) - {BOARD_LANE}
    # The slices stand for the corpus lane; a bare "corpus" token would widen a partial consent to every slice.
    tokens = sorted(lanes - {"corpus"}) + sorted(slices)
    layers = publish_scope.normalize_publish_layers(",".join(tokens) or None)
    publish_scope.purge_removed_scope(db, repo=repo, new_publish_layers=layers)
    repo.publish_layers, repo.updated_at = layers, now
    db.merge(BoardConsent(repo_id=repo.id, answered_at=now))
