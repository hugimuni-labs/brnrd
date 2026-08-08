"""Platform webhook ingress."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from brr.gates.github import parse as gh_parse

from .. import (
    billing,
    github_summons,
    ids,
    inbox as inbox_service,
    limits,
    stripe_api,
)
from ..models import Account, ChannelRoute, Repo, StripeEvent, TgPairCode
from ..platforms import github as gh
from ..platforms import telegram as tg
from ..platforms import whatsapp as wa

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

# #408 — associations that count as "trusted" for the default-closed
# authorization gate. Everything else (NONE, CONTRIBUTOR,
# FIRST_TIME_CONTRIBUTOR, FIRST_TIMER, MANNEQUIN, ...) is denied unless
# the login is separately allowlisted.
# ``author_association`` has **no permission grain** — it answers "how is
# this person related to the repo", not "what may they do here". This set is
# therefore only the cheap *negative*: anything outside it is denied without
# an API call. Membership is not admission; see `_github_authorized`.
_AUTHORIZED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

# The population both lanes admit, read from the same endpoint. The
# self-hosted gate has always used this set (`brr.gates.github.polling`);
# the managed lane used to stop at the association above, which let in any
# read-only invited collaborator and any member of the owning org.
_AUTHORIZED_PERMISSIONS = frozenset({"admin", "write", "maintain"})

_UNPAIRED_TEXT = "This chat is not paired to a brnrd account yet. Pair a repo from the dashboard, then send /repos or /repo owner/name."
# WhatsApp has no slash-command surface (`_handle_command`'s /repo, /repos,
# /status are Telegram-only, see the WhatsApp section below) — pointing a
# WhatsApp user at commands that don't work on this channel would be a
# worse answer than a plain one.
_WA_UNPAIRED_TEXT = "This chat is not paired to a brnrd account yet. Pair a repo from the dashboard, then text the pair code here."
_UNBOUND_REPO_TEXT = "This repository is not connected to brnrd yet. Open brnrd.dev, connect the repo, then call the bot again."
_BACKLOG_GRACE = timedelta(seconds=1)


def _wa_audit(trace: str, stage: str, detail: str = "") -> None:
    """Emit one privacy-safe, grep-ready WhatsApp ingress decision.

    A random request-local handle joins the stages without logging the
    sender, message body, pair code, or raw payload.  Meta delivery failures
    otherwise look exactly like a quiet channel: the generic access log says
    only ``POST ... 200`` and every decision inside that response disappears.
    """
    suffix = f" {detail}" if detail else ""
    logger.info("[brnrd] whatsapp ingress: trace=%s stage=%s%s", trace, stage, suffix)


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


def _message_precedes_route(parsed: "tg.ParsedMessage | wa.ParsedMessage", route: ChannelRoute) -> bool:
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
    inbox_service.enqueue(db, repo_id=repo_id, body=body, source="telegram", reply_to={"platform": "telegram", "chat_id": parsed.chat_id, "topic_id": parsed.topic_id, "message_id": parsed.message_id, "user": parsed.user, "user_id": parsed.user_id, "username": parsed.username}, attachments=parsed.attachments or None)


def _github_mention_candidates(settings) -> list[str]:
    return [f"@{login}" for login in github_summons.github_identity_candidates(settings)]


def _github_command_candidates(settings) -> list[str]:
    return [a.strip().lstrip("/").rstrip(":") for a in str(settings.github_trigger_aliases or "").split(",") if a.strip()]


def _github_trigger(settings, body: str) -> tuple[str, str] | None:
    mention = gh_parse.find_mention(body, _github_mention_candidates(settings))
    if mention is not None:
        return "mention", mention
    stripped = (body or "").strip().casefold()
    for alias in _github_command_candidates(settings):
        a = alias.casefold()
        if stripped == f"/{a}" or stripped.startswith(f"/{a} ") or stripped == f"{a}:" or stripped.startswith(f"{a}:"):
            return "command", alias
    return None


def _hub_signature_ok(secret: str, body: bytes, signature: str | None) -> bool:
    """``X-Hub-Signature-256`` check — the same HMAC-SHA256-over-the-raw-
    body scheme GitHub and Meta's WhatsApp Cloud API both use (Meta's docs
    name the header identically), so one check serves both webhooks."""
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _coerce_int(value: object) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _github_reply(
    settings,
    reply_to: dict[str, Any],
    text: str,
    *,
    token: str | None = None,
) -> None:
    token = token or settings.github_bot_token
    if not token:
        return
    repo = str(reply_to.get("repo") or "")
    issue_number = _coerce_int(reply_to.get("issue_number"))
    if not repo or issue_number is None:
        return
    try:
        gh.post_issue_comment(token, settings.github_api_base_url, settings.github_api_version, repo, issue_number, text)
    except Exception as e:
        print(f"[brnrd] github reply failed: {e}")


def _github_react(
    settings,
    repo: str,
    *,
    target: str,
    target_id: int,
    token: str | None = None,
) -> None:
    """Post the ``:eyes:`` acknowledgment — feedback, never delivery.

    Called only after a trigger is accepted and its event recorded, so a
    reaction never appears for a refused (unauthorized) trigger. Never lets
    an exception escape: a reaction failure must not block or fail the
    dispatch that already happened.
    """
    token = token or settings.github_bot_token
    if not token:
        return
    try:
        ok = gh.add_reaction(
            token,
            settings.github_api_base_url,
            settings.github_api_version,
            repo,
            target=target,
            target_id=target_id,
        )
    except Exception as e:
        logger.warning(
            "github reaction failed repo=%s target=%s target_id=%s: %s",
            repo, target, target_id, e,
        )
        return
    if not ok:
        logger.warning(
            "github reaction rejected repo=%s target=%s target_id=%s",
            repo, target, target_id,
        )


def _maybe_pr_branch(
    settings,
    repo: str,
    pr_number: int | None,
    *,
    token: str | None = None,
) -> str | None:
    token = token or settings.github_bot_token
    if pr_number is None or not token:
        return None
    try:
        return gh.fetch_pull_head_ref(token, settings.github_api_base_url, settings.github_api_version, repo, pr_number)
    except Exception as e:
        print(f"[brnrd] github branch lookup failed: {e}")
        return None


def _github_authorized(
    settings,
    association: str,
    login: str,
    *,
    repo: str = "",
    token: str | None = None,
) -> tuple[bool, str]:
    """Default-closed authorization gate (#408) for the managed webhook.

    The HMAC signature already proves the payload came from GitHub; this
    decides whether *this particular commenter* may enqueue an autonomous
    run — which spends the operator's quota, on the operator's machine.

    **Two lanes, one population.** The self-hosted gate has always read the
    collaborator-permission API and admitted ``{admin, write, maintain}``.
    This lane stopped at ``author_association``, which has no permission
    grain at all: ``COLLABORATOR`` covers a *read-only* invited
    collaborator and ``MEMBER`` covers any member of the owning org. So the
    multi-tenant lane — the one facing public org repos — admitted a
    strictly wider population than the single-tenant one, for the same
    ticket number. It now reads the same fact from the same endpoint.

    The association survives as the **cheap negative**: outside the set, the
    answer is no and no API call is made. Inside it, the permission decides.
    ``OWNER`` is the one association that carries its own answer.

    A lookup that cannot be made is **unknown, and unknown is denied** —
    #408's "no warn-but-allow grace", applied to the failure it now has.
    The rejection is logged with a distinct reason so a token or rate-limit
    problem is never mistaken for a hostile stranger.
    """
    if login and login.casefold() in settings.github_authz_allowlist:
        return True, "allowlisted"
    if association not in _AUTHORIZED_ASSOCIATIONS:
        return False, f"unauthorized: association={association or 'NONE'}"
    if association == "OWNER":
        return True, "association=OWNER"
    if not repo or not login:
        return False, f"unverified: no repo/login to check (association={association})"
    token = token or settings.github_bot_token
    if not token:
        return False, f"unverified: no token for permission read (association={association})"
    try:
        permission = gh.fetch_collaborator_permission(
            token,
            settings.github_api_base_url,
            settings.github_api_version,
            repo,
            login,
        )
    except Exception as exc:  # noqa: BLE001 — any failure is "unknown"
        return False, f"unverified: permission lookup failed ({type(exc).__name__})"
    if permission is None:
        return False, "unverified: permission lookup returned nothing"
    if permission in _AUTHORIZED_PERMISSIONS:
        return True, f"permission={permission}"
    return False, (
        f"unauthorized: permission={permission} (association={association})"
    )


def _handle_github_issue_comment(
    db: Session,
    settings,
    payload: dict[str, Any],
    *,
    token: str | None = None,
    installation_id: str | None = None,
) -> None:
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
    authorized, reason = _github_authorized(
        settings, association, author, repo=repo_name, token=token,
    )
    if not authorized:
        logger.warning(
            "github authz reject repo=%s author=%s trigger=%s reason=%s",
            repo_name, author, trigger_kind, reason,
        )
        return
    is_pr = bool(issue.get("pull_request")) or "/pull/" in str(comment.get("html_url") or "")
    reply_to: dict[str, Any] = {"platform": "github", "repo": repo_name, "issue_number": issue_number, "comment_id": comment_id, "kind": "pr-comment" if is_pr else "issue-comment", "author": author, "html_url": str(comment.get("html_url") or ""), "trigger": trigger_kind, "mention": trigger_text}
    if installation_id:
        reply_to["installation_id"] = installation_id
    repo = db.execute(select(Repo).where(Repo.repo_full_name == repo_name)).scalar_one_or_none()
    if repo is None:
        _github_reply(settings, reply_to, _UNBOUND_REPO_TEXT, token=token)
        return
    # Free-tier headroom throttle + abuse ceilings (limits.py): a webhook
    # can't 429 GitHub, so this is the platform-appropriate polite drop —
    # logged reason + one-line reply naming the limit, never silent.
    decision = limits.check_event_admission(
        db, settings, db.get(Account, repo.account_id), body=body
    )
    if not decision.allowed:
        logger.warning(
            "github limit reject repo=%s author=%s reason=%s",
            repo_name, author, decision.reason,
        )
        _github_reply(settings, reply_to, decision.message, token=token)
        return
    if is_pr:
        reply_to["pr_number"] = issue_number
        branch = _maybe_pr_branch(
            settings, repo_name, issue_number, token=token
        )
        if branch:
            reply_to["branch_target"] = branch
    inbox_service.enqueue(db, repo_id=repo.id, body=gh_parse._format_event_body("", body), source="github", reply_to=reply_to)
    _github_react(settings, repo_name, target="issue_comment", target_id=comment_id, token=token)


def _handle_github_summons(
    db: Session,
    settings,
    x_github_event: str | None,
    payload: dict[str, Any],
    *,
    token: str | None = None,
    installation_id: str | None = None,
) -> None:
    """Turn a GitHub summons to the marker account into an inbox event."""

    summons = github_summons.resolve_github_summons(
        x_github_event,
        payload,
        github_summons.github_identity_candidates(settings),
        settings.github_trigger_label,
    )
    if summons is None:
        return

    repo_name = str(
        ((payload.get("repository") or {}).get("full_name") or "")
    ).strip()

    # #879 members 3+4 — a mention-sourced summons (review body, or a newly
    # opened issue/PR body) needs the #408 authorization gate the
    # assign/label/review-request specs don't: those actions already require
    # GitHub triage-or-higher, so the signed event is its own proof: a
    # mention only requires the ability to comment/review/open, which any
    # drive-by account has on a public repo.
    if summons.requires_authz:
        if gh_parse._skip_mention_comment_author(
            summons.actor_login, summons.login, settings.github_bot_login
        ):
            return
        authorized, reason = _github_authorized(
            settings, summons.actor_association, summons.actor_login,
            repo=repo_name, token=token,
        )
        if not authorized:
            logger.warning(
                "github authz reject repo=%s author=%s trigger=%s reason=%s",
                repo_name, summons.actor_login, summons.kind, reason,
            )
            return

    item = summons.item
    issue_number = _coerce_int(item.get("number") or payload.get("number"))
    if not repo_name or issue_number is None:
        return

    sender = str(
        ((payload.get("sender") or {}).get("login") or "")
    ).strip()
    # The signed summons event is itself the authorization proof: GitHub
    # requires triage-or-higher to assign and write access to request a
    # review. Judge the sender, not the issue/PR author — a maintainer must be
    # able to summon the resident on a drive-by contribution.
    reply_to: dict[str, Any] = {
        "platform": "github",
        "repo": repo_name,
        "issue_number": issue_number,
        "kind": summons.kind,
        "author": sender,
        "html_url": summons.html_url_override or str(item.get("html_url") or ""),
        "trigger": summons.trigger,
        summons.reply_key: summons.login,
    }
    if installation_id:
        reply_to["installation_id"] = installation_id

    repo = db.execute(
        select(Repo).where(Repo.repo_full_name == repo_name)
    ).scalar_one_or_none()
    if repo is None:
        _github_reply(settings, reply_to, _UNBOUND_REPO_TEXT, token=token)
        return

    body = (
        gh_parse._format_event_body("", summons.content_override)
        if summons.content_override is not None
        else gh_parse._format_event_body(
            str(item.get("title") or "").strip(),
            str(item.get("body") or "").strip(),
        )
    )
    decision = limits.check_event_admission(
        db,
        settings,
        db.get(Account, repo.account_id),
        body=body,
    )
    if not decision.allowed:
        logger.warning(
            "github limit reject repo=%s author=%s trigger=%s reason=%s",
            repo_name,
            sender,
            summons.trigger,
            decision.reason,
        )
        _github_reply(settings, reply_to, decision.message, token=token)
        return

    if summons.is_pull_request:
        reply_to["pr_number"] = issue_number
        branch = _maybe_pr_branch(
            settings,
            repo_name,
            issue_number,
            token=token,
        )
        if branch:
            reply_to["branch_target"] = branch
    inbox_service.enqueue(
        db,
        repo_id=repo.id,
        body=body,
        source="github",
        reply_to=reply_to,
    )
    _github_react(settings, repo_name, target="issue", target_id=issue_number, token=token)


def _audit_reject(parsed: tg.ParsedMessage, *, reason: str) -> None:
    # #409 — default-closed gate audit trail. Deliberately server-side
    # only (no chat reply): telling an unauthorized sender *why* they
    # were rejected would let them probe for a valid principal.
    print(f"[brnrd] telegram authz denied: chat={parsed.chat_id} user={parsed.user_id} reason={reason}")


def _authorized(settings, parsed: tg.ParsedMessage, route: ChannelRoute) -> bool:
    """#409 — default-closed: enqueue iff the verified sender is the
    chat's paired principal or sits in the configured allowlist. The
    sender is always ``parsed.user_id`` (the update's verified
    ``from.id``), never text parsed from the message body."""
    if parsed.user_id is None:
        return False
    if route.paired_user_id is not None and parsed.user_id == route.paired_user_id:
        return True
    return parsed.user_id in settings.telegram_authz_allowlist


def _handle_start(db: Session, settings, parsed: tg.ParsedMessage, code: str) -> None:
    if parsed.user_id is None:
        # Anonymous admin / channel-post sender_chat — no personal
        # identity to bind as the route's principal (#409, default-closed).
        _audit_reject(parsed, reason="start_no_sender_id")
        _reply(settings, parsed, "Pairing requires an identifiable Telegram account (not an anonymous admin or channel post).")
        return
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
        existing = ChannelRoute(id=ids.channel_route_id(), platform="telegram", channel_id=parsed.chat_id, topic_id=topic_id, account_id=pc.account_id, repo_id=pc.repo_id, paired_user_id=parsed.user_id)
        db.add(existing)
    else:
        existing.account_id = pc.account_id
        existing.repo_id = pc.repo_id
        existing.paired_user_id = parsed.user_id
    pc.consumed = True
    repo = db.get(Repo, pc.repo_id)
    db.commit()
    _reply(settings, parsed, f"Paired with repo '{repo.repo_full_name if repo else pc.repo_id}'. Send me tasks anytime.")


def _apply_chat_migration(db: Session, migration: tuple[str, str]) -> None:
    """#409 — a group->supergroup migration changes the chat's numeric id;
    follow it on any paired route so the chat doesn't silently fall out of
    its pairing (and, worse, so a *new* chat that later reuses the old id
    doesn't inherit someone else's route)."""
    old_id, new_id = migration
    routes = db.execute(select(ChannelRoute).where(ChannelRoute.platform == "telegram", ChannelRoute.channel_id == old_id)).scalars().all()
    for route in routes:
        route.channel_id = new_id
    if routes:
        db.commit()


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


# ── WhatsApp (Meta Business Cloud API) ───────────────────────────────
#
# Deliberately *not* a byte-for-byte mirror of the Telegram helpers above —
# two real differences in the platform shape:
#
# 1. Pairing has no ``/start <code>`` deep-link convention (WhatsApp has no
#    bot-command syntax), so a chat pairs by texting the bare code. It
#    reuses the *same* ``TgPairCode`` table Telegram pairing already writes
#    (that table has no platform column — a code is valid wherever it's
#    typed); minting a WhatsApp-specific code from the dashboard is
#    frontend work, out of scope here (see the PR body).
# 2. There is no default-closed authz gate mirroring Telegram's
#    ``_authorized``/``paired_user_id``. A WhatsApp "chat" *is* one
#    customer's own number (``ParsedMessage.chat_id == wa_id``, no
#    group-chat concept, no forwarded-message spoofing surface) — the
#    channel-route lookup below is keyed on that same number, so a route
#    match already proves the sender is the one who paired it. Adding a
#    second principal check would be a check with nothing left to catch.

_WA_PAIR_CODE_RE = re.compile(r"^TG-[A-Z0-9]{4}$")


def _wa_reply(settings, parsed: "wa.ParsedMessage", text: str) -> None:
    if not (settings.whatsapp_access_token and settings.whatsapp_phone_number_id):
        return
    try:
        wa.send_message(
            settings.whatsapp_access_token,
            settings.whatsapp_phone_number_id,
            parsed.chat_id,
            text,
            api_base_url=settings.whatsapp_api_base_url,
            api_version=settings.whatsapp_api_version,
            reply_to_message_id=parsed.message_id,
        )
    except Exception as e:
        print(f"[brnrd] whatsapp reply failed: {e}")


def _wa_pair_code_from_text(text: str) -> str | None:
    """The bare pair code a WhatsApp user texts in, or None.

    Matched against the exact shape ``ids.tg_pair_code`` produces
    (``TG-`` + 4 alphabet chars), case-insensitively — anything else is an
    ordinary task message, not a pairing attempt.
    """
    candidate = (text or "").strip().upper()
    return candidate if _WA_PAIR_CODE_RE.match(candidate) else None


def _wa_channel_route(db: Session, parsed: "wa.ParsedMessage") -> ChannelRoute | None:
    return db.execute(
        select(ChannelRoute).where(
            ChannelRoute.platform == "whatsapp",
            ChannelRoute.channel_id == parsed.chat_id,
        )
    ).scalar_one_or_none()


def _enqueue_whatsapp_event(db: Session, parsed: "wa.ParsedMessage", *, repo_id: str, body: str) -> None:
    inbox_service.enqueue(
        db,
        repo_id=repo_id,
        body=body,
        source="whatsapp",
        reply_to={"platform": "whatsapp", "chat_id": parsed.chat_id, "message_id": parsed.message_id},
    )


def _handle_whatsapp_pair(
    db: Session,
    settings,
    parsed: "wa.ParsedMessage",
    code: str,
    *,
    trace: str,
) -> None:
    pc = db.execute(select(TgPairCode).where(TgPairCode.code == code)).scalar_one_or_none()
    expires = pc.expires_at if pc else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if pc is None or pc.consumed or (expires and expires < datetime.now(timezone.utc)):
        _wa_audit(trace, "pair_rejected", "reason=invalid_or_expired")
        _wa_reply(settings, parsed, "Invalid or expired pair code.")
        return
    existing = _wa_channel_route(db, parsed)
    if existing is not None and existing.account_id != pc.account_id:
        _wa_audit(trace, "pair_rejected", "reason=bound_elsewhere")
        _wa_reply(settings, parsed, "This chat is already paired to another account.")
        return
    if existing is None:
        existing = ChannelRoute(id=ids.channel_route_id(), platform="whatsapp", channel_id=parsed.chat_id, topic_id=None, account_id=pc.account_id, repo_id=pc.repo_id)
        db.add(existing)
    else:
        existing.account_id = pc.account_id
        existing.repo_id = pc.repo_id
    pc.consumed = True
    repo = db.get(Repo, pc.repo_id)
    db.commit()
    _wa_audit(trace, "paired")
    _wa_reply(settings, parsed, f"Paired with repo '{repo.repo_full_name if repo else pc.repo_id}'. Send me tasks anytime.")


@router.get("/whatsapp")
def whatsapp_webhook_verify(request: Request):
    """Meta's subscription handshake — ``GET`` with ``hub.*`` query params,
    answered by echoing ``hub.challenge`` back as plain text iff the mode
    and verify token match what's configured (see ``whatsapp.verify_subscription``).
    """
    settings = request.app.state.settings
    params = request.query_params
    challenge = wa.verify_subscription(
        mode=params.get("hub.mode"),
        verify_token=params.get("hub.verify_token"),
        challenge=params.get("hub.challenge"),
        configured_verify_token=settings.whatsapp_verify_token,
    )
    if challenge is None:
        _wa_audit(secrets.token_hex(4), "verify_rejected")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad verify token")
    _wa_audit(secrets.token_hex(4), "verify_accepted")
    return PlainTextResponse(challenge)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, x_hub_signature_256: str | None = Header(default=None)):
    settings = request.app.state.settings
    trace = secrets.token_hex(4)
    raw = await request.body()
    _wa_audit(
        trace,
        "received",
        f"bytes={len(raw)} signature={'present' if x_hub_signature_256 else 'missing'}",
    )
    if not _hub_signature_ok(settings.whatsapp_app_secret, raw, x_hub_signature_256):
        _wa_audit(trace, "rejected", "reason=bad_signature")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad signature")
    try:
        payload = await request.json()
    except Exception:
        _wa_audit(trace, "ignored", "reason=invalid_json")
        return {"ok": True}
    if not isinstance(payload, dict):
        _wa_audit(trace, "ignored", "reason=non_object_payload")
        return {"ok": True}
    # ``statuses`` deliveries (sent/delivered/read receipts for our own
    # outbound sends) and any payload with no inbound message parse to
    # None here — never a trigger, same as a captionless-and-textless
    # Telegram update falling out of ``tg.parse_update``.
    parsed = wa.parse_update(payload)
    if parsed is None:
        _wa_audit(trace, "ignored", "reason=no_inbound_message")
        return {"ok": True}
    _wa_audit(trace, "message_parsed", f"kind={'media' if parsed.has_media else 'text'}")
    with request.app.state.SessionLocal() as db:
        code = _wa_pair_code_from_text(parsed.text)
        if code:
            _wa_audit(trace, "pair_attempt")
            _handle_whatsapp_pair(db, settings, parsed, code, trace=trace)
            return {"ok": True}
        route = _wa_channel_route(db, parsed)
        if route is not None and _message_precedes_route(parsed, route):
            _wa_audit(trace, "ignored", "reason=predates_route")
            return {"ok": True}
        if route is None:
            _wa_audit(trace, "unpaired")
            _wa_reply(settings, parsed, _WA_UNPAIRED_TEXT)
            return {"ok": True}
        repo = db.get(Repo, route.repo_id)
        if repo is None:
            _wa_audit(trace, "rejected", "reason=missing_repo")
            _wa_reply(settings, parsed, "This chat's active repo no longer exists. Pair again from the dashboard.")
            return {"ok": True}
        if not parsed.text and not parsed.attachments:
            # v1 doesn't ingest WhatsApp media at all (no attachment
            # pointers, unlike Telegram's image path) — a media message
            # with no text carries nothing brnrd can act on.
            _wa_audit(trace, "rejected", "reason=media_without_text")
            _wa_reply(settings, parsed, "I can't see attached media yet — that message had no text I can read. Send it as words.")
            return {"ok": True}
        body = parsed.text
        if parsed.has_media:
            body += "\n\n[attached media not ingested — brnrd received the text only]"
        decision = limits.check_event_admission(
            db,
            settings,
            db.get(Account, route.account_id),
            body=body,
        )
        if not decision.allowed:
            _wa_audit(trace, "rejected", f"reason=limit:{decision.reason}")
            _wa_reply(settings, parsed, decision.message)
            return {"ok": True}
        _enqueue_whatsapp_event(db, parsed, repo_id=route.repo_id, body=body)
        _wa_audit(trace, "enqueued")
    return {"ok": True}


@router.post("/telegram")
def telegram_webhook(request: Request, payload: dict, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    settings = request.app.state.settings
    if not settings.telegram_webhook_secret or not hmac.compare_digest(x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad secret")
    # #409 — a group->supergroup migration service message carries no
    # text, so `parse_update` would already drop it silently; check for it
    # first so the route's chat id still follows the migration instead of
    # just going quiet. Never a trigger either way.
    migration = tg.parse_migration(payload)
    if migration is not None:
        with request.app.state.SessionLocal() as db:
            _apply_chat_migration(db, migration)
        return {"ok": True}
    parsed = tg.parse_update(payload)
    if parsed is None:
        return {"ok": True}
    if parsed.is_edit:
        # #409 — an edit never (re)triggers pairing, a command, or a run.
        _audit_reject(parsed, reason="edited_message")
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
        # #409 — default-closed authorization gate: the sender must be the
        # chat's paired principal or explicitly allowlisted. This is the
        # last check before enqueueing an autonomous run.
        if not _authorized(settings, parsed, route):
            _audit_reject(parsed, reason="not_authorized")
            return {"ok": True}
        if not parsed.text and not parsed.attachments:
            # Non-image media with no caption: nothing brnrd can ingest.
            # Say so — a silent drop reads as "the agent ignored me"
            # (2026-07-21). A captionless *image* does enqueue below: the
            # image carries the content, same as the local gate.
            _audit_reject(parsed, reason="media_without_text")
            _reply(settings, parsed, "I can't see attached media yet — that message had no text I can read. Add a caption or send it as words.")
            return {"ok": True}
        body = parsed.text
        if parsed.has_media and not parsed.attachments:
            # #525 — images now ride as pointers; only non-image media
            # (video, voice, non-image documents) stays annotated-not-fetched.
            body += "\n\n[attached media not ingested — brnrd received the text only]"
        # Free-tier headroom throttle + abuse ceilings (limits.py): polite
        # drop with a logged reason + one-line reply naming the limit —
        # never a silent loss, never a crash.
        decision = limits.check_event_admission(
            db,
            settings,
            db.get(Account, route.account_id),
            body=body,
            attachment_count=len(parsed.attachments or []),
        )
        if not decision.allowed:
            _audit_reject(parsed, reason=f"limit:{decision.reason}")
            _reply(settings, parsed, decision.message)
            return {"ok": True}
        _enqueue_telegram_event(db, parsed, repo_id=route.repo_id, body=body)
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
    if not _hub_signature_ok(settings.github_webhook_secret, raw, x_hub_signature_256):
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
    elif github_summons.resolve_github_summons(
        x_github_event,
        payload,
        github_summons.github_identity_candidates(settings),
        settings.github_trigger_label,
    ) is not None:
        with request.app.state.SessionLocal() as db:
            _handle_github_summons(
                db,
                settings,
                x_github_event,
                payload,
            )
    return {"ok": True}
