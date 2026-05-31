"""Platform webhook ingress.

``POST /v1/webhooks/telegram`` is the real producer side of the spine
(superseding ``_dev/enqueue``). It is authenticated by the
secret-token header Telegram echoes from ``setWebhook`` — not a bearer
— and multiplexes a single managed bot across accounts by chat_id:

- ``/start <code>`` binds the chat to a project (Telegram pairing).
- a message from a bound chat is enqueued for that project's daemon,
  carrying an opaque ``reply_to`` so the response routes home.
- a message from an unbound chat is ignored.
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

        binding = db.execute(
            select(ChatBinding).where(
                ChatBinding.platform == "telegram",
                ChatBinding.chat_id == parsed.chat_id,
            )
        ).scalar_one_or_none()
        if binding is None:
            # Unbound chat — ignore silently.
            return {"ok": True}

        inbox_service.enqueue(
            db,
            project_id=binding.project_id,
            body=parsed.text,
            source="telegram",
            reply_to={
                "platform": "telegram",
                "chat_id": parsed.chat_id,
                "topic_id": parsed.topic_id,
                "message_id": parsed.message_id,
            },
        )
    return {"ok": True}
