"""Device-flow connect handshake for local repo daemons and Telegram routes."""

from __future__ import annotations

import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import ids, schemas
from ..auth import Principal, get_db, require_account
from ..config import telegram_effective_bot_username
from ..models import PairRequest, Repo, TgPairCode, Token
from ..security import hash_token

router = APIRouter(prefix="/v1/accounts/pair", tags=["pairing"])


def pair_capabilities(pair: PairRequest) -> dict[str, str]:
    """Decode ``pair.capabilities_json`` — ``{}`` for any pair predating the
    column, sent with none, or holding unparseable JSON (never raise on a
    field that was always advisory)."""
    if not pair.capabilities_json:
        return {}
    try:
        data = json.loads(pair.capabilities_json)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pair_suggested_repo_full_name(pair: PairRequest) -> str:
    """The repo name the connecting checkout reported, or ``""``.

    Only ``repo_full_name`` (already ``owner/name``, parsed locally from the
    git remote — see ``gates/cloud._repo_capabilities``) is trusted as a
    binding target; the other capability fields are context, not identity.
    """
    value = pair_capabilities(pair).get("repo_full_name", "")
    return value.strip() if isinstance(value, str) else ""


def pair_suggested_forge(pair: PairRequest) -> str:
    """``"github"`` or ``"local"`` for the suggested repo above, or ``""``
    when there is no suggestion at all. Display-only — `_resolve_or_create_
    repo_for_pair` re-derives and validates its own copy rather than
    trusting this one back."""
    value = pair_capabilities(pair).get("forge", "")
    return value.strip() if isinstance(value, str) else ""


def initiator_proof_ok(pair: PairRequest, approve_secret: str) -> bool:
    """Does ``approve_secret`` prove this approve came from the initiator?

    The one comparison, so the two approve surfaces (the bearer route and
    the browser's ``POST /v1/connect/{code}``) cannot drift into disagreeing
    about what a valid proof is — and so the browser route can decline to
    *materialize a repo* for an approve it already knows will be refused.

    A row with no stored proof (``""``) answers ``False``: see
    ``models.PairRequest.approve_secret_hash`` for why that direction is the
    safe one.
    """
    expected = pair.approve_secret_hash or ""
    if not expected or not approve_secret:
        return False
    return hmac.compare_digest(hash_token(approve_secret), expected)


def _require_initiator_proof(pair: PairRequest, approve_secret: str) -> None:
    if not initiator_proof_ok(pair, approve_secret):
        raise HTTPException(
            status_code=403,
            detail=(
                "this approval link is incomplete or doesn't match this pair "
                "code — open the full link your terminal printed, or re-run "
                "`brnrd account connect` for a fresh one"
            ),
        )


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
def start_pair(
    request: Request,
    payload: schemas.PairStartRequest | None = Body(None),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    for _ in range(8):
        code = ids.pair_code()
        if not db.execute(select(PairRequest).where(PairRequest.pair_code == code)).scalar_one_or_none():
            break
    else:
        raise HTTPException(status_code=503, detail="could not allocate pair code")

    secret = ids.poll_secret()
    approve_secret = ids.pair_approve_secret()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.pair_ttl_s)
    # No auth yet at this point in the handshake (that's what pairing mints)
    # — capabilities ride in unauthenticated, exactly like the pair code
    # itself, and are trusted no further than "a repo name to suggest on the
    # approval page", never as a binding claim on their own (the approving
    # human's own account is still what `approve_core` scopes the lookup
    # to).
    capabilities_json = None
    if payload is not None:
        caps = {
            k: v
            for k, v in payload.model_dump().items()
            if isinstance(v, str) and v.strip()
        }
        if caps:
            capabilities_json = json.dumps(caps)
    db.add(
        PairRequest(
            id=ids.pair_request_id(),
            pair_code=code,
            poll_secret_hash=hash_token(secret),
            approve_secret_hash=hash_token(approve_secret),
            status=PairRequest.STATUS_PENDING,
            expires_at=expires_at,
            capabilities_json=capabilities_json,
        )
    )
    db.commit()
    # The approval proof rides the **fragment**, not the query string: a
    # fragment is never sent to a server, so it stays out of access logs,
    # out of `Referer` on any onward navigation, and out of the JSON the
    # approval page's own context endpoint returns. The browser reads it
    # from `location.hash` and posts it back over TLS.
    pair_url = f"{settings.public_base_url.rstrip()}/connect/{code}#{approve_secret}"
    return schemas.PairStarted(pair_code=code, pair_url=pair_url, poll_secret=secret, approve_secret=approve_secret, expires_at=expires_at)


def approve_core(db: Session, account_id: str, code: str, repo_id: str, approve_secret: str) -> str:
    """Bind a pairing to ``account_id`` and mint its daemon token.

    Two rules decide this, in order, and both used to be missing:

    1. **The approver must prove it is the initiator.** A pair code is not a
       bearer capability for approval. Before this check existed, any
       authenticated account that knew a live code could approve it into
       *itself*, and the victim's daemon then polled back a token scoped to
       the attacker — agent-authority code execution on the victim's host.
       The check runs before the repo lookup so a failed approve has no
       side effects at all, and before the status read so a caller with no
       proof learns nothing about the code beyond what `_get_pair` already
       says.
    2. **A pair code is approved exactly once**, claimed with a conditional
       `UPDATE ... WHERE status='pending'`. Refusing only `consumed` (the
       old shape) left an already-approved pair re-bindable — and left
       check-then-use between the status read and the write, so two
       concurrent approves could both mint a token and the last writer
       decided who the daemon belonged to. The daemon token is minted only
       after the row is claimed.
    """
    pair = _get_pair(db, code)
    _require_initiator_proof(pair, approve_secret)
    if pair.status != PairRequest.STATUS_PENDING:
        raise HTTPException(status_code=409, detail="pair code already used")
    repo = db.execute(select(Repo).where(Repo.id == repo_id, Repo.account_id == account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    raw = ids.daemon_token()
    claimed = db.execute(
        update(PairRequest)
        .where(PairRequest.id == pair.id, PairRequest.status == PairRequest.STATUS_PENDING)
        .values(
            status=PairRequest.STATUS_APPROVED,
            account_id=account_id,
            repo_id=repo.id,
            minted_token=raw,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if claimed != 1:
        # Lost the race. Nothing was written and no token exists yet — the
        # session is discarded by `get_db`'s teardown, so there is nothing
        # to unwind either.
        raise HTTPException(status_code=409, detail="pair code already used")
    # repo_id remains the initial/default routing repo for compatibility with
    # the one-repo connect UI. The token principal itself is account-scoped.
    db.add(Token(id=ids.token_id(), account_id=account_id, repo_id=repo.id, kind=Token.KIND_DAEMON, token_hash=hash_token(raw), label="daemon (paired)"))
    db.commit()
    return repo.id


@router.post("/{code}/approve", response_model=schemas.PairStatus)
def approve_pair(code: str, payload: schemas.PairApprove, principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    repo_id = approve_core(db, principal.account_id, code, payload.repo_id, payload.approve_secret)
    return schemas.PairStatus(status="approved", account_id=principal.account_id, repo_id=repo_id)


def _telegram_pair_response(settings: Any, repo: Repo | None, code: str, expires_at: datetime) -> schemas.TelegramPairStarted:
    # #1463 — token-derived (getMe) when available, env as fallback. #1242 —
    # an invalid-shape username (e.g. the hyphenated GitHub login spelling)
    # resolves to no Telegram entity at all; a deep link built on it is
    # worse than no link, so an unusable value mints none and the `else`
    # branch below leads with the manual `/start <code>` path instead.
    username = telegram_effective_bot_username(settings)
    deep_link = f"https://t.me/{username}?start={code}" if username else None
    # #1457 — repo is None for an account-level code: the chat binds to the
    # account itself; which project answers is resolved per message.
    target = f"repo '{repo.repo_full_name}'" if repo is not None else "your account"
    if deep_link:
        instructions = (
            f"Open {deep_link}, then press Start if Telegram prompts. "
            f"If Telegram only opens the chat, send `/start {code}` to "
            f"bind this chat to {target}."
        )
    else:
        instructions = (
            f"Send `/start {code}` to your brnrd Telegram bot to bind this "
            f"chat to {target}."
        )
    instructions += (
        f" For WhatsApp, text `{code}` by itself — no `/start` and no other "
        "words — to your brnrd WhatsApp number."
    )
    return schemas.TelegramPairStarted(
        pair_code=code,
        instructions=instructions,
        deep_link=deep_link,
        expires_at=expires_at,
    )


def _active_telegram_pair(db: Session, account_id: str, repo_id: str) -> TgPairCode | None:
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(TgPairCode)
        .where(
            TgPairCode.account_id == account_id,
            TgPairCode.repo_id == repo_id,
            TgPairCode.consumed.is_(False),
        )
        .order_by(TgPairCode.expires_at.desc())
    ).scalars()
    for row in rows:
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires >= now:
            return row
    return None


def telegram_pair_core(db: Session, settings: Any, account_id: str, repo_id: str | None) -> schemas.TelegramPairStarted:
    """Mint a pair code. ``repo_id=None`` mints an account-level code
    (#1457): consuming it binds the chat to the account with no repo pin,
    so it works for an account that has no repos yet — the mobile
    cold-start deep link's whole reason to exist."""
    repo = None
    if repo_id is not None:
        repo = db.execute(select(Repo).where(Repo.id == repo_id, Repo.account_id == account_id)).scalar_one_or_none()
        if repo is None:
            raise HTTPException(status_code=404, detail="repo not found")
    for _ in range(8):
        code = ids.tg_pair_code()
        if not db.execute(select(TgPairCode).where(TgPairCode.code == code)).scalar_one_or_none():
            break
    else:
        raise HTTPException(status_code=503, detail="could not allocate pair code")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.messenger_pair_ttl_s)
    db.add(TgPairCode(id=ids.tg_pair_code_id(), code=code, account_id=account_id, repo_id=repo.id if repo is not None else None, expires_at=expires_at))
    db.commit()
    return _telegram_pair_response(settings, repo, code, expires_at)


def telegram_pair_for_connect(
    db: Session, settings: Any, account_id: str, repo_id: str
) -> schemas.TelegramPairStarted:
    repo = db.execute(select(Repo).where(Repo.id == repo_id, Repo.account_id == account_id)).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    existing = _active_telegram_pair(db, account_id, repo_id)
    if existing is not None:
        expires = existing.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return _telegram_pair_response(settings, repo, existing.code, expires)
    return telegram_pair_core(db, settings, account_id, repo_id)


@router.post("/telegram", response_model=schemas.TelegramPairStarted)
def start_telegram_pair(payload: schemas.TelegramPairStart, request: Request, principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    return telegram_pair_core(db, request.app.state.settings, principal.account_id, payload.repo_id)


@router.get("/{code}", response_model=schemas.PairStatus)
def poll_pair(code: str, request: Request, poll_secret: str = Query(...), db: Session = Depends(get_db)):
    pair = _get_pair(db, code)
    if not hmac.compare_digest(hash_token(poll_secret), pair.poll_secret_hash):
        raise HTTPException(status_code=401, detail="bad poll secret")
    if pair.status == PairRequest.STATUS_PENDING:
        return schemas.PairStatus(status="pending")
    if pair.status == PairRequest.STATUS_APPROVED:
        token = pair.minted_token
        telegram_pair = None
        if pair.account_id and pair.repo_id:
            telegram_pair = telegram_pair_for_connect(
                db,
                request.app.state.settings,
                pair.account_id,
                pair.repo_id,
            )
        pair.status = PairRequest.STATUS_CONSUMED
        pair.minted_token = None
        db.commit()
        return schemas.PairStatus(
            status="paired",
            account_id=pair.account_id,
            repo_id=pair.repo_id,
            daemon_token=token,
            telegram_pair=telegram_pair,
        )
    return schemas.PairStatus(status="paired", account_id=pair.account_id, repo_id=pair.repo_id)
