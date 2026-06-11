"""Platform webhook ingress.

``POST /v1/webhooks/telegram`` is the real producer side of the spine
(superseding ``_dev/enqueue``). It is authenticated by the
secret-token header Telegram echoes from ``setWebhook`` — not a bearer
— and multiplexes a single managed bot across accounts by chat_id:

- ``/start <code>`` binds the chat to a project (Telegram pairing).
- ``/project <name>`` selects the active project for the chat.
- ``/project <name> <task>`` routes one task without changing the
  active project.
- a message from a bound chat is enqueued for that project's daemon,
  carrying an opaque ``reply_to`` so the response routes home.
- a message from an unbound chat gets a friendly setup error.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ids
from .. import inbox as inbox_service
from ..models import ChatBinding, Project, TgPairCode
from ..platforms import telegram as tg

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

_UNPAIRED_TEXT = (
    "This chat is not paired to a brnrd account yet. Pair it from brnrd, "
    "then send /projects or /project <name>."
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
        },
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
