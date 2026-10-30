"""Account session helpers and repo endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import account_deletion, github_marker, ids, limits, publish_scope, schemas, stripe_api
from ..activity_records import dedupe_activity_records, fresh_activity_records
from ..auth import Principal, get_db, require_account, require_account_or_session
from ..models import ActivityRecord, Account, GitHubInstallation, GitHubInstalledRepo, Repo, Token
from ..oauth import GitHubIdentity
from ..security import hash_token

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])

SESSION_TTL = timedelta(days=30)


def _repo_parts(repo_full_name: str) -> tuple[str, str]:
    owner, sep, name = repo_full_name.strip().partition("/")
    if not sep or not owner or not name:
        raise HTTPException(status_code=400, detail="repo must look like owner/name")
    return owner, name


def repo_out(repo: Repo) -> schemas.RepoOut:
    return schemas.RepoOut(
        repo_id=repo.id,
        forge=repo.forge,
        repo_full_name=repo.repo_full_name,
        repo_owner=repo.repo_owner,
        repo_name=repo.repo_name,
        forge_repo_id=repo.forge_repo_id,
        default_branch=repo.default_branch,
        created_at=repo.created_at,
        publish_layers=repo.publish_layers,
    )


def account_for_github_identity(db: Session, identity: GitHubIdentity) -> Account:
    # w-57 (2026-08-16): never writes `Account.email` — brnrd stopped
    # collecting it. Deliberately not even ``account.email = identity.email``
    # (identity.email is always ``None`` now, see oauth.fetch_identity): an
    # existing row's column is left exactly as it was, so a pre-migration
    # residual value is never re-synced or "resurrected" by a later login.
    account = db.execute(select(Account).where(Account.github_id == identity.github_id)).scalar_one_or_none()
    if account is None:
        account = Account(id=ids.account_id(), github_id=identity.github_id, github_login=identity.login)
        db.add(account)
        db.commit()
        db.refresh(account)
        return account
    account.github_login = identity.login
    db.commit()
    db.refresh(account)
    return account


def issue_session_token(db: Session, account: Account) -> str:
    raw = ids.session_token()
    db.add(
        Token(
            id=ids.token_id(),
            account_id=account.id,
            kind=Token.KIND_SESSION,
            token_hash=hash_token(raw),
            label="session",
            expires_at=datetime.now(timezone.utc) + SESSION_TTL,
        )
    )
    db.commit()
    return raw


@router.post("/repos", status_code=status.HTTP_201_CREATED, response_model=schemas.RepoOut)
def create_repo(payload: schemas.RepoCreate, request: Request, principal: Principal = Depends(require_account), db: Session = Depends(get_db)):
    repo_full_name = payload.repo_full_name.strip()
    owner, name = _repo_parts(repo_full_name)
    existing = db.execute(
        select(Repo).where(Repo.account_id == principal.account_id, Repo.repo_full_name == repo_full_name)
    ).scalar_one_or_none()
    if existing is not None:
        return repo_out(existing)
    # #501 repo cap — headroom-limited on the free tier, abuse-capped on all.
    limits.raise_if_denied(
        limits.check_repo_connect(
            db, request.app.state.settings, db.get(Account, principal.account_id)
        )
    )
    # Publish-scope consent is recorded here exactly as `_connect_repo_core`
    # records it for the browser flow — same validator, same vocabulary, same
    # loud 4xx on a typo. An omitted `publish_layers` records the explicit
    # opt-out (`publish_scope.DEFAULT_NEW_CONNECT`), never `NULL`: this
    # surface used to leave the column unset, which the gate then read as
    # "publish everything", so an API-key client bypassed the consent gate
    # entirely just by not mentioning it.
    repo = Repo(
        id=ids.repo_id(),
        account_id=principal.account_id,
        forge=payload.forge.strip() or "github",
        repo_full_name=repo_full_name,
        repo_owner=owner,
        repo_name=name,
        forge_repo_id=payload.forge_repo_id,
        default_branch=payload.default_branch,
        publish_layers=publish_scope.normalize_publish_layers(
            payload.publish_layers if payload.publish_layers is not None else publish_scope.DEFAULT_NEW_CONNECT
        ),
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    # #874 — this is the CLI/daemon-pairing repo-bind path (`brnrd account
    # connect`), the other half of `_connect_repo_core`'s browser flow.
    # Best-effort: a marker failure must never fail the connect itself.
    try:
        github_marker.sync_marker_for_repos(db, request.app.state.settings, [repo])
    except Exception as exc:
        print(f"[brnrd] github marker sync failed for {repo.repo_full_name}: {exc}")
    return repo_out(repo)


@router.post("/delete", response_model=schemas.AccountDeletionOut)
def delete_account(
    payload: schemas.AccountDeletionConfirm,
    request: Request,
    principal: Principal = Depends(require_account_or_session),
    db: Session = Depends(get_db),
):
    """Art 17 erasure — see ``account_deletion.py`` for the sweep and what
    it retains. ``POST``, not ``DELETE``: the re-typed GitHub-login
    confirmation lives in the JSON body, and this codebase's other
    destructive account actions (``/v1/repos/{id}/disconnect``,
    ``/v1/accounts/subscription/cancel``) are POST for the same reason —
    an explicit body, never a bare verb a link or a GET could trigger.
    """
    account = db.get(Account, principal.account_id)
    if account is None or account.deleted_at is not None:
        raise HTTPException(status_code=404, detail="account not found")
    try:
        account_deletion.confirm_login_matches(account, payload.confirm_login)
    except account_deletion.ConfirmationMismatch as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        receipt = account_deletion.delete_account(db, request.app.state.settings, account)
    except stripe_api.StripeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return schemas.AccountDeletionOut(
        deleted_at=receipt.deleted_at,
        stripe_subscription_canceled=receipt.stripe_subscription_canceled,
        retained=[
            schemas.RetainedStoreOut(store=r.store, reason=r.reason) for r in receipt.retained
        ],
    )
