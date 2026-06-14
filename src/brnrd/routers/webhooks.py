"""Platform webhook ingress.

``POST /v1/webhooks/telegram`` and ``POST /v1/webhooks/github`` are the
real producer side of the spine (superseding ``_dev/enqueue``).
Telegram is authenticated by the
secret-token header Telegram echoes from ``setWebhook`` — not a bearer
— and multiplexes a single managed bot across accounts by chat_id:

- ``/start <code>`` binds the chat to a project (Telegram pairing).
- ``/project <name>`` selects the active project for the chat.
- ``/project <name> <task>`` routes one task without changing the
  active project.
- a message from a bound chat is enqueued for that project's daemon,
  carrying an opaque ``reply_to`` so the response routes home.
- a message from an unbound chat gets a friendly setup error.

GitHub is authenticated by ``X-Hub-Signature-256`` and routes an
addressed issue comment through a repo binding:

- an unbound but addressed repo gets a setup comment instead of a silent
  drop.
- a bound repo comment is enqueued for that project's daemon, carrying
  GitHub reply metadata in ``reply_to`` so the managed response posts
  back to the issue/PR thread.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from brr.gates.github import parse as gh_parse

from .. import ids
from .. import inbox as inbox_service
from ..models import ChatBinding, Project, RepoBinding, TgPairCode
from ..platforms import github as gh
from ..platforms import telegram as tg

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

_UNPAIRED_TEXT = (
    "This chat is not paired to a brnrd account yet. Pair it from brnrd, "
    "then send /projects or /project <name>."
)
_UNBOUND_REPO_TEXT = (
    "This repository is not connected to a brnrd project yet. "
    "Connect it from brnrd, then mention the bot again."
)


def _reply(settings, parsed: tg.ParsedMessage, text: str) -> None:
    if not settings.telegram_bot_token:
        return
    try:
        tg.send_message(
            settings.telegram_bot_token,
            parsed.chat_id,
            text,
            topic_id=parsed.topic_id,
            reply_to_message_id=parsed.message_id,
        )
    except Exception as e:  # noqa: BLE001 - a failed reply must not 500 the webhook
        print(f"[brnrd] telegram reply failed: {e}")


def _slash_command(text: str) -> tuple[str, str] | None:
    """Return a normalized Telegram slash command and its argument text."""
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    name = head[1:].split("@", 1)[0].lower()
    return name, rest.strip()


def _chat_binding(db: Session, parsed: tg.ParsedMessage) -> ChatBinding | None:
    return db.execute(
        select(ChatBinding).where(
            ChatBinding.platform == "telegram",
            ChatBinding.chat_id == parsed.chat_id,
        )
    ).scalar_one_or_none()


def _account_projects(db: Session, account_id: str) -> list[Project]:
    return list(
        db.execute(
            select(Project)
            .where(Project.account_id == account_id)
            .order_by(Project.created_at)
        ).scalars()
    )


def _find_project(projects: list[Project], name: str) -> Project | None:
    wanted = name.strip()
    if not wanted:
        return None
    for project in projects:
        if project.name == wanted:
            return project
    matches = [p for p in projects if p.name.casefold() == wanted.casefold()]
    return matches[0] if len(matches) == 1 else None


def _split_project_task(
    projects: list[Project], arg: str
) -> tuple[Project | None, str | None]:
    """Resolve the longest project-name prefix from ``arg``.

    Project names are account-scoped and can contain spaces. Treating the
    longest matching project name as the selector lets
    ``/project work laptop check logs`` route to project ``work laptop``
    with task ``check logs``.
    """
    text = arg.strip()
    if not text:
        return None, None

    candidates: list[tuple[int, Project, str]] = []
    folded = text.casefold()
    for project in projects:
        name = project.name
        name_folded = name.casefold()
        if folded == name_folded:
            candidates.append((len(name), project, ""))
        elif folded.startswith(name_folded + " "):
            candidates.append((len(name), project, text[len(name) :].strip()))
    if not candidates:
        return None, None

    _, project, task = max(candidates, key=lambda item: item[0])
    return project, task


def _project_list_text(projects: list[Project], current_id: str | None) -> str:
    if not projects:
        return "No projects are available for this account yet."
    lines = ["Projects:"]
    for project in projects:
        suffix = " (current)" if project.id == current_id else ""
        lines.append(f"- {project.name}{suffix}")
    lines.append("")
    lines.append("Use /project <name> to select one.")
    return "\n".join(lines)


def _enqueue_telegram_event(
    db: Session,
    parsed: tg.ParsedMessage,
    *,
    project_id: str,
    body: str,
) -> None:
    inbox_service.enqueue(
        db,
        project_id=project_id,
        body=body,
        source="telegram",
        reply_to={
            "platform": "telegram",
            "chat_id": parsed.chat_id,
            "topic_id": parsed.topic_id,
            "message_id": parsed.message_id,
            "user": parsed.user,
            "user_id": parsed.user_id,
            "username": parsed.username,
        },
    )


def _github_mention(settings) -> str:
    login = settings.github_bot_login.strip().lstrip("@")
    return f"@{login}" if login else ""


def _github_signature_ok(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(signature, expected)


def _github_repo(payload: dict[str, Any]) -> tuple[str, str]:
    repo = ((payload.get("repository") or {}).get("full_name") or "").strip()
    installation = payload.get("installation") or {}
    installation_id = str(installation.get("id") or "").strip()
    return repo, installation_id


def _repo_binding(
    db: Session, repo_full_name: str, installation_id: str
) -> RepoBinding | None:
    binding = db.execute(
        select(RepoBinding).where(RepoBinding.repo_full_name == repo_full_name)
    ).scalar_one_or_none()
    if binding is None:
        return None
    if binding.installation_id and installation_id \
            and binding.installation_id != installation_id:
        return None
    return binding


def _coerce_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
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
        gh.post_issue_comment(
            settings.github_bot_token,
            settings.github_api_base_url,
            settings.github_api_version,
            repo,
            issue_number,
            text,
        )
    except Exception as e:  # noqa: BLE001 - a failed reply must not 500 webhook
        print(f"[brnrd] github reply failed: {e}")


def _maybe_pr_branch(settings, repo: str, pr_number: int | None) -> str | None:
    if pr_number is None or not settings.github_bot_token:
        return None
    try:
        return gh.fetch_pull_head_ref(
            settings.github_bot_token,
            settings.github_api_base_url,
            settings.github_api_version,
            repo,
            pr_number,
        )
    except Exception as e:  # noqa: BLE001 - branch hint is helpful, not required
        print(f"[brnrd] github branch lookup failed: {e}")
        return None


def _enqueue_github_event(
    db: Session,
    *,
    project_id: str,
    body: str,
    reply_to: dict[str, Any],
) -> None:
    inbox_service.enqueue(
        db,
        project_id=project_id,
        body=body,
        source="github",
        reply_to=reply_to,
    )


def _handle_github_issue_comment(
    db: Session, settings, payload: dict[str, Any]
) -> None:
    if payload.get("action") not in {"created", "edited"}:
        return

    repo, installation_id = _github_repo(payload)
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    issue_number = _coerce_int(issue.get("number"))
    comment_id = _coerce_int(comment.get("id"))
    body = str(comment.get("body") or "")
    mention = _github_mention(settings)
    if not repo or issue_number is None or comment_id is None:
        return
    if mention and mention not in body:
        return

    author = str(((comment.get("user") or {}).get("login") or "")).strip()
    if gh_parse._skip_mention_comment_author(author, mention, settings.github_bot_login):
        return

    is_pr = bool(issue.get("pull_request")) or "/pull/" in str(
        comment.get("html_url") or ""
    )
    pr_number = issue_number if is_pr else None
    kind = "pr-comment" if is_pr else "issue-comment"
    reply_to: dict[str, Any] = {
        "platform": "github",
        "repo": repo,
        "issue_number": issue_number,
        "comment_id": comment_id,
        "kind": kind,
        "author": author,
        "html_url": str(comment.get("html_url") or ""),
        "trigger": "mention",
        "mention": mention,
    }
    binding = _repo_binding(db, repo, installation_id)
    if binding is None:
        _github_reply(settings, reply_to, _UNBOUND_REPO_TEXT)
        return

    if pr_number is not None:
        reply_to["pr_number"] = pr_number
        branch = _maybe_pr_branch(settings, repo, pr_number)
        if branch:
            reply_to["branch_target"] = branch

    _enqueue_github_event(
        db,
        project_id=binding.project_id,
        body=gh_parse._format_event_body("", body),
        reply_to=reply_to,
    )


def _handle_start(db: Session, settings, parsed: tg.ParsedMessage, code: str) -> None:
    pc = db.execute(
        select(TgPairCode).where(TgPairCode.code == code)
    ).scalar_one_or_none()
    expires = pc.expires_at if pc else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if pc is None or pc.consumed or (expires and expires < datetime.now(timezone.utc)):
        _reply(settings, parsed, "Invalid or expired pair code.")
        return

    existing = db.execute(
        select(ChatBinding).where(
            ChatBinding.platform == "telegram", ChatBinding.chat_id == parsed.chat_id
        )
    ).scalar_one_or_none()
    if existing is not None and existing.account_id != pc.account_id:
        _reply(
            settings,
            parsed,
            "This chat is already paired to another account. "
            "Have the current owner unbind it first.",
        )
        return

    if existing is not None:
        existing.account_id = pc.account_id
        existing.project_id = pc.project_id
    else:
        db.add(
            ChatBinding(
                id=ids.chat_binding_id(),
                platform="telegram",
                chat_id=parsed.chat_id,
                account_id=pc.account_id,
                project_id=pc.project_id,
            )
        )
    pc.consumed = True
    project = db.get(Project, pc.project_id)
    db.commit()
    name = project.name if project else pc.project_id
    _reply(settings, parsed, f"Paired with project '{name}'. Send me tasks anytime.")


def _handle_command(
    db: Session,
    settings,
    parsed: tg.ParsedMessage,
    command: str,
    args: str,
    binding: ChatBinding | None,
) -> bool:
    """Handle a chat-management command.

    Returns True when the message was consumed as a command; False lets the
    caller route it as a normal task.
    """
    if command not in {"connect", "project", "projects", "status"}:
        return False
    if binding is None:
        _reply(settings, parsed, _UNPAIRED_TEXT)
        return True

    projects = _account_projects(db, binding.account_id)
    current = db.get(Project, binding.project_id)

    if command == "projects":
        _reply(settings, parsed, _project_list_text(projects, binding.project_id))
        return True

    if command == "status":
        if current is None:
            _reply(
                settings,
                parsed,
                "This chat's selected project no longer exists. "
                "Use /project <name> to select another one.",
            )
        else:
            _reply(
                settings,
                parsed,
                f"Current project: {current.name}. Use /project <name> to switch.",
            )
        return True

    if command == "connect":
        project = _find_project(projects, args)
        if project is None:
            _reply(
                settings,
                parsed,
                f"Project '{args or '<missing>'}' was not found for this account. "
                "Send /projects to see available projects.",
            )
            return True
        binding.project_id = project.id
        db.commit()
        _reply(
            settings,
            parsed,
            f"Selected project '{project.name}' for this chat. "
            "Send me tasks anytime.",
        )
        return True

    project, task = _split_project_task(projects, args)
    if project is None:
        if args:
            text = (
                f"No project matched '{args}'. "
                "Send /projects to see available projects."
            )
        else:
            text = (
                "Usage: /project <project-name> [task]. "
                "Send /projects to see available projects."
            )
        _reply(settings, parsed, text)
        return True

    if task:
        _enqueue_telegram_event(db, parsed, project_id=project.id, body=task)
        return True

    binding.project_id = project.id
    db.commit()
    _reply(
        settings,
        parsed,
        f"Selected project '{project.name}' for this chat. Send me tasks anytime.",
    )
    return True


@router.post("/telegram")
def telegram_webhook(
    request: Request,
    payload: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    settings = request.app.state.settings
    secret = settings.telegram_webhook_secret
    if not secret or not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "", secret
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad secret")

    parsed = tg.parse_update(payload)
    if parsed is None:
        return {"ok": True}

    session_factory = request.app.state.SessionLocal
    with session_factory() as db:
        code = tg.pair_code_from_text(parsed.text)
        if code:
            _handle_start(db, settings, parsed, code)
            return {"ok": True}

        binding = _chat_binding(db, parsed)
        command = _slash_command(parsed.text)
        if command is not None and _handle_command(
            db, settings, parsed, command[0], command[1], binding
        ):
            return {"ok": True}

        if binding is None:
            _reply(settings, parsed, _UNPAIRED_TEXT)
            return {"ok": True}

        project = db.get(Project, binding.project_id)
        if project is None:
            _reply(
                settings,
                parsed,
                "This chat's selected project no longer exists. "
                "Use /project <name> to select another one.",
            )
            return {"ok": True}

        _enqueue_telegram_event(
            db, parsed, project_id=binding.project_id, body=parsed.text
        )
    return {"ok": True}


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    settings = request.app.state.settings
    raw = await request.body()
    if not _github_signature_ok(
        settings.github_webhook_secret, raw, x_hub_signature_256
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad secret")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed webhook body is a no-op
        return {"ok": True}
    if not isinstance(payload, dict):
        return {"ok": True}

    if x_github_event == "ping":
        return {"ok": True}
    if x_github_event != "issue_comment":
        return {"ok": True}

    session_factory = request.app.state.SessionLocal
    with session_factory() as db:
        _handle_github_issue_comment(db, settings, payload)
    return {"ok": True}
