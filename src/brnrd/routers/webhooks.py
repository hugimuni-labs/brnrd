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
from ..models import Account, ChannelRoute, Daemon, Event, Repo, StripeEvent, TgPairCode
from ..platforms import github as gh
from ..platforms import telegram as tg
from ..platforms import whatsapp as wa

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)
_WA_AUDIT_LOGGER = logging.getLogger("uvicorn.error")

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

# #1242 — the old text ("...then send /repos or /repo owner/name") was a
# closed loop: /repos and /repo from an unpaired chat hit this same reply
# (`_handle_command` requires a route before either does anything), so the
# one command that actually works — /start <code>, or the bare code once
# it's typed — never appeared anywhere in the bot's own replies. Name it
# here instead of only in the dashboard-side instructions.
_UNPAIRED_TEXT = "This chat is not paired to a brnrd account yet. Get a pair code from the dashboard, then send /start <code> (or just paste the code) here to pair."
# WhatsApp has no slash-command surface (`_handle_command`'s /repo, /repos,
# /status are Telegram-only, see the WhatsApp section below) — pointing a
# WhatsApp user at commands that don't work on this channel would be a
# worse answer than a plain one. Already names its one working action
# (text the code), so #1242's loop fix above doesn't apply here — kept in
# sync in tone only.
_WA_UNPAIRED_TEXT = "This chat is not paired to a brnrd account yet. Get a pair code from the dashboard, then text it here to pair."
_UNBOUND_REPO_TEXT = "This repository is not connected to brnrd yet. Open brnrd.dev, connect the repo, then call the bot again."
# #1282 — a genuinely-expired-but-otherwise-valid code gets this instead of
# the generic unknown/consumed text below: the maintainer's own read of the
# raw "stale code" complaint (see the issue's follow-up comment) was that an
# expired TTL is expected mechanically, but the UX should name the fix.
_PAIR_CODE_EXPIRED_TEXT = "This pairing code expired — run brnrd account connect again for a fresh one."
# #1282 — a bound chat whose account has no daemon online at all must say
# so, not go silent: the webhook still enqueues the message (a daemon that
# comes online later drains it normally), this is only the honest reply the
# sender was otherwise never getting. `brnrd account connect` both pairs and
# installs/starts the persistent daemon service (see
# `src/brr/docs/account-daemon.md`); the runner itself is autodetected
# `claude` / `codex` on that machine's PATH, hence the doctor pointer. See
# `_account_daemon_status` for why this checks liveness, not runner
# reporting, and for why it only ever fires this text for an account whose
# daemon has *never* checked in — the "your machine is only asleep" branch
# right below it is #1486's fix for the other case: a paired account whose
# daemon has gone quiet.
_NO_RUNNER_TEXT = (
    "I'm bound to this chat but no daemon of yours is online right now, so "
    "nothing is listening for this message yet. Run brnrd account connect "
    "on the machine you want to run agents from (it pairs and starts the "
    "daemon), with claude or codex installed and logged in so it has a "
    "runner to use — brnrd runners doctor checks that. Your message is "
    "saved; I'll answer once a daemon is online."
)
# #1486 — a *paired* account whose daemon has gone quiet (asleep, offline,
# rebooting) is a completely different situation from never-paired, and
# every remedy `_NO_RUNNER_TEXT` names is destructive here: the maintainer's
# own live incident was re-running `brnrd account connect` on a healthy
# laptop because this branch used to collapse into that text too. State the
# fact (when it was last seen) and stop — no `brnrd account connect`, no
# `brnrd runners doctor`, nothing to run. Waiting is the correct fix, and
# the queue already does that on its own.
_STALE_DAEMON_TEXT = (
    "I'm bound to this chat, but your daemon {last_seen_label} and isn't "
    "reachable right now — could be asleep, offline, or between reboots. "
    "Nothing to do on your end: your message is saved and I'll answer the "
    "moment it checks back in."
)
# #1457 — an account-level pairing succeeded but the account has no repos,
# so a task message has no project to run against. The message is NOT
# queued (an event needs a repo lane for a daemon to drain) — say the drop
# out loud rather than letting a silent success-shape imply delivery.
_NO_REPO_YET_TEXT = (
    "I hear you — but no project is connected to your account yet, so I "
    "have nowhere to run. Connect a repository at brnrd.dev (or install "
    "the CLI and run brnrd in a checkout on your computer). Messages sent "
    "before that aren't queued, so resend once it's connected."
)
_BACKLOG_GRACE = timedelta(seconds=1)


def _wa_audit(trace: str, stage: str, detail: str = "") -> None:
    """Emit one privacy-safe, grep-ready WhatsApp ingress decision.

    A random request-local handle joins the stages without logging the
    sender, message body, pair code, or raw payload.  Meta delivery failures
    otherwise look exactly like a quiet channel: the generic access log says
    only ``POST ... 200`` and every decision inside that response disappears.
    """
    suffix = f" {detail}" if detail else ""
    # Production config filters this module's INFO logger and Scaleway does
    # not ingest raw process prints. Uvicorn's own error logger is the
    # configured operational stream whose startup/shutdown rows are visible
    # in the same log API as its access records.
    _WA_AUDIT_LOGGER.info(
        "[brnrd] whatsapp ingress: trace=%s stage=%s%s",
        trace,
        stage,
        suffix,
    )


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


def _route_target_repo(db: Session, route: ChannelRoute) -> Repo | None:
    """#1457 — the repo a message on this route runs against.

    A chat is paired to an *account*; the repo is routing, not identity.
    Resolution order, first hit wins:

    1. the route's own pin (``/repo owner/name``, or a legacy repo-scoped
       pairing — same row, same meaning),
    2. the account's sole repo,
    3. the repo of the account's most recent event — where the
       conversation already lives,
    4. the most recently updated repo.

    ``None`` only when the account has no repos at all; the caller answers
    with the onboarding nudge, never a silent drop. A pinned repo that no
    longer exists falls through to account-level resolution instead of
    dead-ending the chat — the pairing belongs to the account and outlives
    any one repo.

    This resolution is the wire-side stand-in for the standing direction
    (one persona resident per account, deciding placement itself); when a
    resident-side router exists, rungs 2–4 collapse into it.
    """
    if route.repo_id is not None:
        pinned = db.get(Repo, route.repo_id)
        if pinned is not None:
            return pinned
    repos = _account_repos(db, route.account_id)
    if not repos:
        return None
    if len(repos) == 1:
        return repos[0]
    recent_repo_id = db.execute(
        select(Event.repo_id)
        .where(Event.repo_id.in_([r.id for r in repos]))
        .order_by(Event.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if recent_repo_id is not None:
        for repo in repos:
            if repo.id == recent_repo_id:
                return repo
    return max(repos, key=lambda r: r.updated_at or r.created_at)


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
    # #1389 — `media_group_id` folds an album (a text/caption plus N
    # photos, delivered as N separate webhook calls) into the one event a
    # still-open member of the same group already opened, instead of one
    # event per photo. `None` for an ordinary message: `enqueue` no-ops the
    # merge path entirely in that case.
    inbox_service.enqueue(db, repo_id=repo_id, body=body, source="telegram", reply_to={"platform": "telegram", "chat_id": parsed.chat_id, "topic_id": parsed.topic_id, "message_id": parsed.message_id, "user": parsed.user, "user_id": parsed.user_id, "username": parsed.username}, attachments=parsed.attachments or None, media_group_id=parsed.media_group_id)


# #1282 — matches `capabilities._DAEMON_ONLINE_AFTER`. Duplicated rather
# than imported: pulling in `capabilities.py` here for one threshold would
# also pull its `_Context` account-wide query shape, built for the
# dashboard's batched capability scan, not a per-message check on the
# webhook's synchronous hot path.
_DAEMON_ONLINE_AFTER = timedelta(minutes=2)


def _account_daemon_status(db: Session, account_id: str) -> tuple[bool, datetime | None]:
    """(is a daemon online now, most recent heartbeat across the account's daemons).

    One query, one source of truth, feeding both `_account_has_online_daemon`
    (the liveness boolean, unchanged) and #1486's three-way reply split,
    which needs the *timestamp* the boolean was throwing away — the account
    with a daemon that checked in 6 minutes ago and the account that has
    never had a daemon at all both used to read as plain "not online", and
    the sentence printed for one was destructive advice for the other. The
    second element of the tuple is `None` **iff** the account has never had
    a `Daemon` row at all (no register call, ever) — that is the one honest
    way to tell "never paired" from "paired, gone quiet", and it comes from
    the exact same rows the liveness check already reads, not a second
    signal that could disagree with it.
    """
    now = datetime.now(timezone.utc)
    daemons = db.execute(select(Daemon).where(Daemon.account_id == account_id)).scalars().all()
    online = False
    last_seen_at: datetime | None = None
    for daemon in daemons:
        last_seen = daemon.last_seen_at
        if last_seen is not None and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if last_seen is not None and (last_seen_at is None or last_seen > last_seen_at):
            last_seen_at = last_seen
        if bool(daemon.online) and last_seen is not None and now - last_seen <= _DAEMON_ONLINE_AFTER:
            online = True
    return online, last_seen_at


def _account_has_online_daemon(db: Session, account_id: str) -> bool:
    """True iff the account has a daemon with a fresh heartbeat.

    Deliberately *not* the finer-grained "has a daemon that's online **and**
    reported an unblocked runner" test `capabilities._detect_daemon_live` /
    `_detect_runner_available` make for the dashboard's FUEL/MACHINE rows —
    `Daemon.runners_json` / `quota_json` are last-write-wins mirrors behind
    `publish_scope.lane_permitted(..., lane="runners"/"quota")`, and a fresh
    repo's `publish_layers` defaults to `OFF` (`publish_scope.
    DEFAULT_NEW_CONNECT`) until its owner opts in — `brnrd account connect`
    never sends `publish_layers` today, so nearly every freshly-paired
    account reads as "no quota report" whether or not a runner is actually
    configured. Gating the nudge on that signal would misfire on almost
    every account, working or not. `Daemon.online` / `last_seen_at` are set
    directly by `POST /daemons/register` and every heartbeat-carrying PUT,
    with no publish-scope gate, so "is anything listening at all" is the one
    honest binary signal available here — narrower than the real ask (it
    won't catch "online but genuinely has no runner"), but it's the
    unambiguous, unmistakably-true half: nobody has ever picked this
    account's queue up. Thin wrapper over `_account_daemon_status` so the
    two can't drift; kept as its own name because it's the one other code
    (and #1486's own kept-as-today case 3, "online, no runner") still only
    ever needs the boolean half.
    """
    return _account_daemon_status(db, account_id)[0]


def _last_seen_label(last_seen_at: datetime) -> str:
    """"last checked in 6 minutes ago" — a fact the reader can act on.

    Local rather than importing `_session._age_label`, which renders the
    same shape: that module pulls in oauth/terms/github_marker for the
    web dashboard, and this is the webhook's synchronous request path —
    same reasoning `_DAEMON_ONLINE_AFTER`'s own comment gives for not
    importing `capabilities.py` here for one threshold.
    """
    seconds = max(0, int((datetime.now(timezone.utc) - last_seen_at).total_seconds()))
    minutes = seconds // 60
    if minutes < 1:
        return "last checked in just now"
    if minutes == 1:
        return "last checked in 1 minute ago"
    if minutes < 60:
        return f"last checked in {minutes} minutes ago"
    hours = minutes // 60
    if hours == 1:
        return "last checked in 1 hour ago"
    if hours < 48:
        return f"last checked in {hours} hours ago"
    days = hours // 24
    return "last checked in 1 day ago" if days == 1 else f"last checked in {days} days ago"


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
    try:
        return hmac.compare_digest(signature, expected)
    except TypeError:
        # `hmac.compare_digest` raises TypeError instead of returning False
        # when a str argument carries non-ASCII (e.g. raw latin-1) code
        # points — an attacker-controlled header can trigger it. Fail
        # closed exactly like any other bad signature, never an unhandled
        # 500 (H-4).
        return False


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
    ``from.id``), never text parsed from the message body.

    w-52 pre-alpha teams: with ``telegram_open_rooms`` enabled, a paired
    **group/supergroup** chat also authorizes any identifiable sender —
    the room's admins control who is in the room, so membership is the
    grant. Three deliberate bounds: the flag is default-off; a private
    chat never widens (``chat_type`` must verifiably be a group, read
    from the update's own ``chat.type``, never from message text); and
    an anonymous-admin post (``user_id is None``, #409) stays refused —
    attribution is part of the grant."""
    if parsed.user_id is None:
        return False
    if route.paired_user_id is not None and parsed.user_id == route.paired_user_id:
        return True
    if (
        settings.telegram_open_rooms
        and parsed.chat_type in ("group", "supergroup")
        and route.paired_user_id is not None
    ):
        return True
    return parsed.user_id in settings.telegram_authz_allowlist


def _telegram_display_name(parsed: tg.ParsedMessage) -> str:
    """Best-effort human label for a Telegram principal (#1464): the
    `@username` (stable, and what the paired human recognises themselves
    by) when Telegram supplies one, else the first name it always does.
    Rendering only — never the authorization principal (`paired_user_id`
    stays that)."""
    return f"@{parsed.username}" if parsed.username else parsed.user


def _whatsapp_display_name(parsed: "wa.ParsedMessage") -> str:
    """Same job as `_telegram_display_name`, WhatsApp shape: the Cloud API
    hands over the sender's profile name in `ParsedMessage.user`; the
    number itself (`user_id`/`chat_id`) is the fallback when a contact has
    none set."""
    return parsed.user or parsed.user_id or parsed.chat_id


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
    if pc is not None and not pc.consumed and expires is not None and expires < datetime.now(timezone.utc):
        # #1282 — a code that genuinely existed and genuinely expired earns
        # the specific nudge; unknown/consumed codes fall through to the
        # generic text below (no evidence a real code was ever typed there).
        _reply(settings, parsed, _PAIR_CODE_EXPIRED_TEXT)
        return
    if pc is None or pc.consumed:
        _reply(settings, parsed, "Invalid or expired pair code.")
        return
    topic_id = _topic_key(parsed)
    display = _telegram_display_name(parsed)
    chat_title = parsed.chat_title or None
    existing = db.execute(select(ChannelRoute).where(ChannelRoute.platform == "telegram", ChannelRoute.channel_id == parsed.chat_id, ChannelRoute.topic_id == topic_id)).scalar_one_or_none()
    if existing is not None and existing.account_id != pc.account_id:
        _reply(settings, parsed, "This chat/topic is already paired to another account.")
        return
    if existing is None:
        existing = ChannelRoute(id=ids.channel_route_id(), platform="telegram", channel_id=parsed.chat_id, topic_id=topic_id, account_id=pc.account_id, repo_id=pc.repo_id, paired_user_id=parsed.user_id, paired_user_display=display, chat_title=chat_title)
        db.add(existing)
    else:
        existing.account_id = pc.account_id
        existing.repo_id = pc.repo_id
        existing.paired_user_id = parsed.user_id
        existing.paired_user_display = display
        existing.chat_title = chat_title
    pc.consumed = True
    # #1464 — the minting session's outcome readback: who redeemed *this*
    # code, so a browser panel still open can show it (see
    # `dashboard.dashboard_telegram_pair_status_api`).
    pc.redeemed_display = display
    repo = db.get(Repo, pc.repo_id) if pc.repo_id is not None else None
    account = db.get(Account, pc.account_id)
    db.commit()
    # #1464 — name the bound identity in the very message that creates the
    # bind: a wrong-account/wrong-phone redeem (the maintainer's own live
    # trace) is now visible in-band instead of only discoverable later on
    # the paired-chats surface.
    login = account.github_login if account is not None else "your"
    if pc.repo_id is None:
        # #1457 — account-level pairing. Name what happens next in the very
        # first exchange: either the auto-routing that is now live, or the
        # one step (connect a project) that stands between here and work.
        target = _route_target_repo(db, existing)
        if target is None:
            _reply(settings, parsed, f"Paired with {login}'s brnrd account — this chat now reaches your resident. " + _NO_REPO_YET_TEXT)
        else:
            _reply(settings, parsed, f"Paired with {login}'s brnrd account. Send me tasks anytime — I'll route them to the right project (currently '{target.repo_full_name}'; /repo owner/name pins one, /repo auto un-pins).")
        return
    _reply(settings, parsed, f"Paired with {login}'s brnrd account, repo '{repo.repo_full_name if repo else pc.repo_id}'. Send me tasks anytime.")


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
    if command == "repos":
        _reply(settings, parsed, _repo_list_text(repos, route.repo_id))
        return True
    if command == "status":
        # #1457 — an account-level route has no pin; report the resolution
        # honestly as auto, never as a fixed repo it might not be tomorrow.
        resolved = _route_target_repo(db, route)
        if route.repo_id is None:
            state = f"auto → '{resolved.repo_full_name}'" if resolved else "auto (no repos connected yet)"
            _reply(settings, parsed, f"Active repo: {state}. Use /repo owner/name to pin one.")
        else:
            current = db.get(Repo, route.repo_id)
            _reply(settings, parsed, f"Active repo: {current.repo_full_name if current else '<missing>'}. Use /repo owner/name to switch, /repo auto to let me route.")
        return True
    if args.strip().casefold() == "auto":
        # #1457 — clear the pin: back to account-level resolution.
        route.repo_id = None
        db.commit()
        resolved = _route_target_repo(db, route)
        tail = f" (currently '{resolved.repo_full_name}')" if resolved else ""
        _reply(settings, parsed, f"Un-pinned — I'll route each message to the project that fits{tail}.")
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

# #1242 — shared with the Telegram dispatch below (bare-code parity): a
# bare pair code is recognized the same way on both channels, against the
# shape(s) ``ids.tg_pair_code`` mints or has minted.
#
# #1237 — the mint moved from "TG-" to "PK-" (channel-neutral: this same
# code is texted to WhatsApp, not just Telegram). Both prefixes are
# accepted here for one migration window: a code minted under the old
# prefix, in flight at deploy time, must still pair. Drop the `TG|`
# alternative (back to `^PK-[A-Z0-9]{4}$`) once no such code can still be
# outstanding — codes expire on `settings.pair_ttl_s` (default 600s), so
# one `pair_ttl_s` window after the mint flip ships to production, every
# `TG-` code has already expired and the alternative is dead weight.
_WA_PAIR_CODE_RE = re.compile(r"^(?:TG|PK)-[A-Z0-9]{4}$")


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


def _bare_pair_code_from_text(text: str) -> str | None:
    """The bare pair code a user texts in with no other command syntax, or
    None. Shared by WhatsApp (its only pairing lane) and Telegram (#1242 —
    parity with the ``/start <code>`` lane, for a user who pastes just the
    code).

    Matched against the shape ``ids.tg_pair_code`` produces (``PK-`` + 4
    alphabet chars, plus the legacy ``TG-`` shape during the #1237
    migration window — see ``_WA_PAIR_CODE_RE``), case-insensitively —
    anything else is an ordinary task message, not a pairing attempt.
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
        attachments=parsed.attachments or None,
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
    if pc is not None and not pc.consumed and expires is not None and expires < datetime.now(timezone.utc):
        # #1282 — see the matching Telegram branch in `_handle_start`.
        _wa_audit(trace, "pair_rejected", "reason=expired")
        _wa_reply(settings, parsed, _PAIR_CODE_EXPIRED_TEXT)
        return
    if pc is None or pc.consumed:
        _wa_audit(trace, "pair_rejected", "reason=invalid_or_expired")
        _wa_reply(settings, parsed, "Invalid or expired pair code.")
        return
    existing = _wa_channel_route(db, parsed)
    if existing is not None and existing.account_id != pc.account_id:
        _wa_audit(trace, "pair_rejected", "reason=bound_elsewhere")
        _wa_reply(settings, parsed, "This chat is already paired to another account.")
        return
    display = _whatsapp_display_name(parsed)
    if existing is None:
        existing = ChannelRoute(id=ids.channel_route_id(), platform="whatsapp", channel_id=parsed.chat_id, topic_id=None, account_id=pc.account_id, repo_id=pc.repo_id, paired_user_display=display)
        db.add(existing)
    else:
        existing.account_id = pc.account_id
        existing.repo_id = pc.repo_id
        existing.paired_user_display = display
    pc.consumed = True
    # #1464 — see the matching Telegram branch in `_handle_start`.
    pc.redeemed_display = display
    repo = db.get(Repo, pc.repo_id) if pc.repo_id is not None else None
    account = db.get(Account, pc.account_id)
    db.commit()
    _wa_audit(trace, "paired")
    # #1464 — name the bound identity, same rule as the Telegram arm.
    login = account.github_login if account is not None else "your"
    if pc.repo_id is None:
        # #1457 — account-level pairing, WhatsApp shape: no slash commands
        # here, so the confirmation names the auto-routing only.
        target = _route_target_repo(db, existing)
        if target is None:
            _wa_reply(settings, parsed, f"Paired with {login}'s brnrd account — this chat now reaches your resident. " + _NO_REPO_YET_TEXT)
        else:
            _wa_reply(settings, parsed, f"Paired with {login}'s brnrd account. Send me tasks anytime — I'll route them to the right project (currently '{target.repo_full_name}').")
        return
    _wa_reply(settings, parsed, f"Paired with {login}'s brnrd account, repo '{repo.repo_full_name if repo else pc.repo_id}'. Send me tasks anytime.")


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
        code = _bare_pair_code_from_text(parsed.text)
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
        # #1457 — resolution, not address: pin → sole repo → recency.
        repo = _route_target_repo(db, route)
        if repo is None:
            _wa_audit(trace, "rejected", "reason=no_repo_on_account")
            _wa_reply(settings, parsed, _NO_REPO_YET_TEXT)
            return {"ok": True}
        if not parsed.text and not parsed.attachments:
            _wa_audit(trace, "rejected", "reason=media_without_text")
            _wa_reply(settings, parsed, "I can't see attached media yet — that message had no text I can read. Send it as words.")
            return {"ok": True}
        body = parsed.text
        if parsed.has_media and not parsed.attachments:
            body += "\n\n[attached media not ingested — brnrd received the text only]"
        decision = limits.check_event_admission(
            db,
            settings,
            db.get(Account, route.account_id),
            body=body,
            attachment_count=len(parsed.attachments),
        )
        if not decision.allowed:
            _wa_audit(trace, "rejected", f"reason=limit:{decision.reason}")
            _wa_reply(settings, parsed, decision.message)
            return {"ok": True}
        _enqueue_whatsapp_event(db, parsed, repo_id=repo.id, body=body)
        _wa_audit(trace, "enqueued")
    return {"ok": True}


def _telegram_secret_ok(settings, header_value: str | None) -> bool:
    if not settings.telegram_webhook_secret:
        return False
    try:
        return hmac.compare_digest(header_value or "", settings.telegram_webhook_secret)
    except TypeError:
        # Same non-ASCII-header TypeError as `_hub_signature_ok` — fail
        # closed rather than 500 (H-4).
        return False


@router.post("/telegram")
def telegram_webhook(request: Request, payload: dict, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    settings = request.app.state.settings
    if not _telegram_secret_ok(settings, x_telegram_bot_api_secret_token):
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
        # #1242 — bare-code parity with WhatsApp: `/start <code>` first
        # (the deep-link-driven shape), then a bare `PK-XXXX` (or legacy
        # `TG-XXXX`, #1237 migration window) typed with no command syntax at
        # all — same fallback order as the WhatsApp lane below, same handler
        # either way once a code is found.
        code = tg.pair_code_from_text(parsed.text) or _bare_pair_code_from_text(parsed.text)
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
        # #1457 — resolution, not address: pin → sole repo → recency.
        repo = _route_target_repo(db, route)
        if repo is None:
            _reply(settings, parsed, _NO_REPO_YET_TEXT)
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
        online, last_seen_at = _account_daemon_status(db, route.account_id)
        if not online:
            # #1282 / #1486 — still enqueue either way (a daemon that comes
            # online later drains it normally); the nudge is additive, not a
            # rejection. Which text depends on whether this account has ever
            # had a daemon check in at all: `last_seen_at is None` is the
            # never-paired case `_NO_RUNNER_TEXT` was always meant for;
            # otherwise it's a paired daemon gone quiet, and #1486 is exactly
            # about not handing that case the never-paired remedy.
            if last_seen_at is None:
                _reply(settings, parsed, _NO_RUNNER_TEXT)
            else:
                _reply(settings, parsed, _STALE_DAEMON_TEXT.format(last_seen_label=_last_seen_label(last_seen_at)))
        _enqueue_telegram_event(db, parsed, repo_id=repo.id, body=body)
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
