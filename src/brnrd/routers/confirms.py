"""A logged-in install owner can answer a daemon's confirmation request."""

from __future__ import annotations

import base64
import hmac
import json
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from brr.loom.runtime.confirm import canon, digest, instant, now, request_valid, signed_bytes

from .. import home_access, ids
from ..auth import Principal, get_db
from ..models import Account, Event, LoomConfirmation, Repo
from ._session import _account_id

router = APIRouter(tags=["confirmations"])


@router.post("/v1/daemons/confirms")
def ask(payload: dict, principal: Principal = Depends(home_access.daemon_gate), db: Session = Depends(get_db)):
    req, call = payload.get("request"), payload.get("call")
    shell = isinstance(call, dict) and set(call) == {"command"} and isinstance(call["command"], str)
    tool = (isinstance(call, dict) and set(call) == {"tool", "input"}
            and isinstance(call["tool"], str) and isinstance(call["input"], dict))
    if not request_valid(req) or not (shell or tool):
        raise HTTPException(422, "Invalid confirmation request")
    try:
        encoded = canon(payload)
        expected = digest(req["install"], req["thread"], req["what"], call)
    except (TypeError, ValueError, RecursionError):
        raise HTTPException(422, "Invalid confirmation call") from None
    if len(encoded) > 65536 or not hmac.compare_digest(expected, req["digest"]):
        raise HTTPException(422, "Invalid confirmation call")
    account = principal.account_id
    prior = db.get(LoomConfirmation, (account, req["id"]))
    if prior is None:
        if instant(req["ask_exp"]) <= now():
            raise HTTPException(410, "Confirmation expired")
        db.add(LoomConfirmation(account_id=account, id=req["id"], install=req["install"], thread=req["thread"],
                                digest=req["digest"], request=encoded.decode(), status="pending",
                                created=instant(req["asked_at"]), expires=instant(req["ask_exp"])))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            prior = db.get(LoomConfirmation, (account, req["id"]))
            if prior is None:
                raise
    if prior is not None and prior.request != encoded.decode():
        raise HTTPException(409, "Confirmation id already exists")
    return {"id": req["id"]}


def _row(db, request, ident):
    account = _account_id(request, db)
    if account is None:
        raise HTTPException(401, "Sign in to view this confirmation")
    row = db.get(LoomConfirmation, (account, ident))
    if row is None:
        raise HTTPException(404, "Unknown confirmation")
    return row


def _page(row):
    payload = json.loads(row.request)
    status = row.status
    if status == "pending" and instant(payload["request"]["ask_exp"]) <= now():
        status = "expired"
    return JSONResponse({**payload, "status": status, "pressed_at": row.pressed_at,
                         "can_press": status == "pending"}, headers={"Cache-Control": "no-store"})


@router.get("/v1/dashboard/loom/confirms/{ident}", dependencies=[Depends(home_access.session_gate)])
def read(ident: str, request: Request, db: Session = Depends(get_db)):
    return _page(_row(db, request, ident))


def signing_key():
    try:
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(os.environ["BRNRD_CONFIRM_K1_SECRET"]))
    except (KeyError, ValueError):
        raise HTTPException(503, "The confirmation signing key is unavailable") from None


@router.post("/v1/dashboard/loom/confirms/{ident}", dependencies=[Depends(home_access.session_gate)])
def press(ident: str, request: Request, payload: dict, db: Session = Depends(get_db)):
    row = _row(db, request, ident)
    origin = request.headers.get("origin")
    if (request.headers.get("content-type", "").partition(";")[0] != "application/json"
            or origin and origin != request.app.state.settings.public_base_url.rstrip("/")):
        raise HTTPException(403, "Answer from the dashboard")
    decision = payload.get("decision")
    if decision not in ("approve", "decline"):
        raise HTTPException(422, "Unknown decision")
    moment = now()
    req = json.loads(row.request)["request"]
    if row.status != "pending" or instant(req["ask_exp"]) <= moment:
        raise HTTPException(409, "This request has expired or already been answered")
    repo_id = db.scalar(select(Repo.id).where(Repo.account_id == row.account_id).order_by(Repo.id).limit(1))
    if repo_id is None:
        raise HTTPException(409, "No inbox is connected")
    status = "given" if decision == "approve" else "refused"
    body = {"request": req, "decision": status}
    if status == "given":
        owner = db.get(Account, row.account_id)
        proof = {"verifier": "server-k1", "pressed_by": f"person:{owner.github_login}", "pressed_at": moment.isoformat()}
        sig = signing_key().sign(signed_bytes(req, proof))
        body["proof"] = {**proof, "sig": base64.b64encode(sig).decode()}
    # Taking the decision and queuing its proof are one transaction. A
    # second press can neither overwrite it nor enqueue a second proof.
    updated = db.execute(update(LoomConfirmation).where(
        LoomConfirmation.account_id == row.account_id, LoomConfirmation.id == ident,
        LoomConfirmation.status == "pending", LoomConfirmation.expires > moment.replace(tzinfo=None),
    ).values(status=status, pressed_at=moment.isoformat()))
    if not updated.rowcount:
        raise HTTPException(409, "This request has expired or already been answered")
    db.add(Event(event_id=ids.event_id(), repo_id=repo_id, source="confirm", body=canon(body).decode()))
    db.commit()
    return _page(row)
