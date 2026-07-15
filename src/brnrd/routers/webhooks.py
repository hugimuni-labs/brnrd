"""Platform webhook ingress."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from brr.gates.github import parse as gh_parse

from .. import billing, ids, inbox as inbox_service, stripe_api
from ..models import ChannelRoute, Repo, StripeEvent, TgPairCode
from ..platforms import github as gh
from ..platforms import telegram as tg

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

# #408 — associations that count as "trusted" for the default-closed
# authorization gate. Everything else (NONE, CONTRIBUTOR,
# FIRST_TIME_CONTRIBUTOR, FIRST_TIMER, MANNEQUIN, ...) is denied unless
# the login is separately allowlisted.
_AUTHORIZED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

_UNPAIRED_TEXT = "This chat is not paired to a brnrd account yet. Pair a repo from the dashboard, then send /repos or /repo owner/name."
_UNBOUND_REPO_TEXT = "This repository is not connected to brnrd yet. Open brnrd.dev, connect the repo, then call the bot again."
_BACKLOG_GRACE = timedelta(seconds=1)


def _reply(settings, parsed: tg.ParsedMessage, text: str) -> None:
    if not settings.telegram_bot_token:
        return
    try:
        tg.send_message(settings.telegram_bot_token, parsed.chat_id, text, topic_id=parsed.topic_id, reply_to_message_id=parsed.message_id)
    except Exception as e:
        print(f"[brnrd] telegram reply failed: {e}")


def _slash_command(text: str) -> tuple[str, str] | None:
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    return head[1:].split("@", 1)[0].lower(), rest.strip()


def _topic_key(parsed: tg.ParsedMessage) -> str | None:
    return None if parsed.topic_id in (None, "") else str(parsed.topic_id)


def _channel_route(db: Session, parsed: tg.ParsedMessage) -> ChannelRoute | None:
    topic_id = _topic_key(parsed)
    if topic_id is not None:
        route = db.execute(select(ChannelRoute).where(ChannelRoute.platform == "telegram", ChannelRoute.channel_id == parsed.chat_id, ChannelRoute.topic_id == topic_id)).scalar_one_or_none()
        if route is not None:
            return route
    return db.execute(select(ChannelRoute).where(ChannelRoute.platform == "telegram", ChannelRoute.channel_id == parsed.chat_id, ChannelRoute.topic_id.is_(None))).scalar_one_or_none()


def _message_precedes_route(parsed: tg.ParsedMessage, route: ChannelRoute) -> bool:
    if parsed.message_date is None:
        return False
    created = route.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return parsed.message_date < created - _BACKLOG_GRACE


def _account_repos(db: Session, account_id: str) -> list[Repo]:
    return list(db.execute(select(Repo).where(Repo.account_id == account_id).order_by(Repo.repo_full_name)).scalars())


def _find_repo(repos: list[Repo], name: str) -> Repo | None:
    wanted = name.strip()
    if not wanted:
        return None
    matches = [r for r in repos if r.repo_full_name.casefold() == wanted.casefold()]
    if len(matches) == 1:
        return matches[0]
    matches = [r for r in repos if r.repo_name.casefold() == wanted.casefold()]
    return matches[0] if len(matches) == 1 else None


def _repo_list_text(repos: list[Repo], current_id: str | None) -> str:
    if not repos:
        return "No repos are connected to this brnrd account yet. Open brnrd.dev to connect one."
    lines = ["Repos:"]
    for repo in repos:
        suffix = " (active)" if repo.id == current_id else ""
        lines.append(f"- {repo.repo_full_name}{suffix}")
    lines.append("")
    lines.append("Use /repo owner/name to select the active repo for this chat or topic.")
    return "\n".join(lines)


def _enqueue_telegram_event(db: Session, parsed: tg.ParsedMessage, *, repo_id: str, body: str) -> None:
    inbox_service.enqueue(db, repo_id=repo_id, body=body, source="telegram", reply_to={"platform": "telegram", "chat_id": parsed.chat_id, "topic_id": parsed.topic_id, "message_id": parsed.message_id, "user": parsed.user, "user_id": parsed.user_id, "username": parsed.username})


def _github_mention_candidates(settings) -> list[str]:
    out, seen = [], set()
    for handle in [settings.github_bot_login, getattr(settings, "github_app_slug", ""), "brr-bot"]:
        login = str(handle or "").strip().lstrip("@")
        if login:
            mention = f"@{login}"
            key = mention.casefold()
            if key not in seen:
                out.append(mention)
                seen.add(key)
    return out


def _github_command_candidates(settings) -> list[str]:
    return [a.strip().lstrip("/").rstrip(":") for a in str(settings.github_trigger_aliases or "").split(",") if a.strip()]


def _github_trigger(settings, body: str) -> tuple[str, str] | None:
    folded = (body or "").casefold()
    for mention in _github_mention_candidates(settings):
        if mention.casefold() in folded:
            return "mention", mention
    stripped = (body or "").strip().casefold()
    for alias in _github_command_candidates(settings):
        a = alias.casefold()
        if stripped == f"/{a}" or stripped.startswith(f"/{a} ") or stripped == f"{a}:" or stripped.startswith(f"{a}:"):
            return "command", alias
    return None


def _github_signature_ok(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _coerce_int(value: object) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _github_reply(settings, reply_to: dict[str, Any], text: str) -> None:
    if not settings.github_bot_token:
        return
    repo = str(reply_to.get("repo") or "")
    issue_number = _coerce_int(reply_to.get("issue_number"))
    if not repo or issue_number is None:
        return
    try:
        gh.post_issue_comment(settings.github_bot_token, settings.github_api_base_url, settings.github_api_version, repo, issue_number, text)
    except Exception as e:
        print(f"[brnrd] github reply failed: {e}")


def _maybe_pr_branch(settings, repo: str, pr_number: int | None) -> str | None:
    if pr_number is None or not settings.github_bot_token:
        return None
    try:
        return gh.fetch_pull_head_ref(settings.github_bot_token, settings.github_api_base_url, settings.github_api_version, repo, pr_number)
    except Exception as e:
        print(f"[brnrd] github branch lookup failed: {e}")
        return None


def _github_authorized(settings, association: str, login: str) -> tuple[bool, str]:
    """Default-closed authorization gate (#408) for the managed webhook.

    The HMAC signature already proves the payload came from GitHub; this
    decides whether *this particular commenter* may enqueue an
    autonomous run. Allowed iff the comment's ``author_association`` is
    OWNER/MEMBER/COLLABORATOR, or the login is on the configured
    allowlist. Everything else is denied — no warn-but-allow grace.
    """
    if association in _AUTHORIZED_ASSOCIATIONS:
        return True, f"association={association}"
    if login and login.casefold() in settings.github_authz_allowlist:
        return True, "allowlisted"
    return False, f"unauthorized: association={association or 'NONE'}"


def _handle_github_issue_comment(db: Session, settings, payload: dict[str, Any]) -> None:
    if payload.get("action") != "created":
        return
    repo_name = ((payload.get("repository") or {}).get("full_name") or "").strip()
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    issue_number = _coerce_int(issue.get("number"))
    comment_id = _coerce_int(comment.get("id"))
    body = str(comment.get("body") or "")
    trigger = _github_trigger(settings, body)
    if not repo_name or issue_number is None or comment_id is None or trigger is None:
        return
    trigger_kind, trigger_text = trigger
    author = str(((comment.get("user") or {}).get("login") or "")).strip()
    if gh_parse._skip_mention_comment_author(author, trigger_text, settings.github_bot_login):
        return
    association = str(comment.get("author_association") or "").strip().upper()
    authorized, reason = _github_authorized(settings, association, author)
    if not authorized:
        logger.warning(
            "github authz reject repo=%s author=%s trigger=%s reason=%s",
            repo_name, author, trigger_kind, reason,
        )
        return
    is_pr = bool(issue.get("pull_request")) or "/pull/" in str(comment.get("html_url") or "")
    reply_to: dict[str, Any] = {"platform": "github", "repo": repo_name, "issue_number": issue_number, "comment_id": comment_id, "kind": "pr-comment" if is_pr else "issue-comment", "author": author, "html_url": str(comment.get("html_url") or ""), "trigger": trigger_kind, "mention": trigger_text}
    repo = db.execute(select(Repo).where(Repo.repo_full_name == repo_name)).scalar_one_or_none()
    if repo is None:
        _github_reply(settings, reply_to, _UNBOUND_REPO_TEXT)
        return
    if is_pr:
        reply_to["pr_number"] = issue_number
        branch = _maybe_pr_branch(settings, repo_name, issue_number)
        if branch:
            reply_to["branch_target"] = branch
    inbox_service.enqueue(db, repo_id=repo.id, body=gh_parse._format_event_body("", body), source="github", reply_to=reply_to)


def _handle_start(db: Session, settings, parsed: tg.ParsedMessage, code: str) -> None:
    pc = db.execute(select(TgPairCode).where(TgPairCode.code == code)).scalar_one_or_none()
    expires = pc.expires_at if pc else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if pc is None or pc.consumed or (expires and expires < datetime.now(timezone.utc)):
        _reply(settings, parsed, "Invalid or expired pair code.")
        return
    topic_id = _topic_key(parsed)
    existing = db.execute(select(ChannelRoute).where(ChannelRoute.platform == "telegram", ChannelRoute.channel_id == parsed.chat_id, ChannelRoute.topic_id == topic_id)).scalar_one_or_none()
    if existing is not None and existing.account_id != pc.account_id:
        _reply(settings, parsed, "This chat/topic is already paired to another account.")
        return
    if existing is None:
        existing = ChannelRoute(id=ids.channel_route_id(), platform="telegram", channel_id=parsed.chat_id, topic_id=topic_id, account_id=pc.account_id, repo_id=pc.repo_id)
        db.add(existing)
    else:
        existing.account_id = pc.account_id
        existing.repo_id = pc.repo_id
    pc.consumed = True
    repo = db.get(Repo, pc.repo_id)
    db.commit()
    _reply(settings, parsed, f"Paired with repo '{repo.repo_full_name if repo else pc.repo_id}'. Send me tasks anytime.")


def _handle_command(db: Session, settings, parsed: tg.ParsedMessage, command: str, args: str, route: ChannelRoute | None) -> bool:
    if command not in {"repo", "repos", "status"}:
        return False
    if route is None:
        _reply(settings, parsed, _UNPAIRED_TEXT)
        return True
    repos = _account_repos(db, route.account_id)
    current = db.get(Repo, route.repo_id)
    if command == "repos":
        _reply(settings, parsed, _repo_list_text(repos, route.repo_id))
        return True
    if command == "status":
        _reply(settings, parsed, f"Active repo: {current.repo_full_name if current else '<missing>'}. Use /repo owner/name to switch.")
        return True
    repo = _find_repo(repos, args)
    if repo is None:
        _reply(settings, parsed, f"Repo '{args or '<missing>'}' was not found. Send /repos to see connected repos.")
        return True
    route.repo_id = repo.id
    db.commit()
    _reply(settings, parsed, f"Active repo set to '{repo.repo_full_name}'. Send me tasks anytime.")
    return True


@router.post("/telegram")
def telegram_webhook(request: Request, payload: dict, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    settings = request.app.state.settings
    if not settings.telegram_webhook_secret or not hmac.compare_digest(x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad secret")
    parsed = tg.parse_update(payload)
    if parsed is None:
        return {"ok": True}
    with request.app.state.SessionLocal() as db:
        code = tg.pair_code_from_text(parsed.text)
        if code:
            _handle_start(db, settings, parsed, code)
            return {"ok": True}
        route = _channel_route(db, parsed)
        if route is not None and _message_precedes_route(parsed, route):
            return {"ok": True}
        command = _slash_command(parsed.text)
        if command is not None and _handle_command(db, settings, parsed, command[0], command[1], route):
            return {"ok": True}
        if route is None:
            _reply(settings, parsed, _UNPAIRED_TEXT)
            return {"ok": True}
        repo = db.get(Repo, route.repo_id)
        if repo is None:
            _reply(settings, parsed, "This chat's active repo no longer exists. Use /repo owner/name to select another one.")
            return {"ok": True}
        _enqueue_telegram_event(db, parsed, repo_id=route.repo_id, body=parsed.text)
    return {"ok": True}


@router.post("/stripe")
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(default=None)):
    """#53 — signed Stripe webhook for both billing legs.

    Signature-verified (manual HMAC, kb design-billing.md §"Stripe
    integration shape"), idempotent on Stripe event ids. Design drafts named
    ``/v1/internal/stripe/webhook`` / ``/webhooks/stripe``; the existing
    ``/v1/webhooks/*`` ingress prefix wins.
    """
    settings = request.app.state.settings
    raw = await request.body()
    if not stripe_api.verify_webhook_signature(raw, stripe_signature or "", settings.stripe_webhook_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bad payload")
    if not isinstance(event, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bad payload")
    event_id = event.get("id") or ""
    with request.app.state.SessionLocal() as db:
        if event_id and db.get(StripeEvent, event_id) is not None:
            return {"ok": True, "disposition": "duplicate"}
        disposition = billing.handle_stripe_event(db, settings, event)
        if event_id:
            db.add(StripeEvent(stripe_event_id=event_id, event_type=event.get("type") or ""))
        db.commit()
    return {"ok": True, "disposition": disposition}


@router.post("/github")
async def github_webhook(request: Request, x_hub_signature_256: str | None = Header(default=None), x_github_event: str | None = Header(default=None)):
    settings = request.app.state.settings
    raw = await request.body()
    if not _github_signature_ok(settings.github_webhook_secret, raw, x_hub_signature_256):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad secret")
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}
    if not isinstance(payload, dict) or x_github_event == "ping":
        return {"ok": True}
    if x_github_event == "issue_comment":
        with request.app.state.SessionLocal() as db:
            _handle_github_issue_comment(db, settings, payload)
    return {"ok": True}
