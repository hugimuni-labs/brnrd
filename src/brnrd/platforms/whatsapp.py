"""Meta WhatsApp Business Cloud API client — parse webhook payloads, send replies.

Mirrors ``platforms/telegram.py``'s shape (``parse_update`` normalizes an
inbound webhook payload; ``send_message`` posts a reply), transport-only —
webhook route mechanics (hub-challenge verification, signature check,
pairing, authz) live in ``routers.webhooks``, same split as GitHub.

v1 scope, deliberately: plain free-form text sends only. WhatsApp's Cloud
API only allows a business to freely message a user inside the 24-hour
window after that user's last message (the "customer service window");
outside it, a send must use a pre-approved message template. Template
support is a deliberate non-goal here (see the PR this shipped in) — a
send outside the window fails, and this module raises that failure as a
distinct, identifiable exception (``WindowClosed``) rather than a generic
transport error, so the caller (``brnrd.inbox``) can record an honest
"outside 24h window" message instead of a bare HTTP failure.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

# Meta's documented error code for "message failed to send because more
# than 24 hours have passed since the customer last replied to this
# number, and the message uses a template that either doesn't exist or
# outside the window a non-template send is used instead" — the Cloud API
# surfaces this as HTTP 470 with this ``error.code``. This is the one
# failure mode this client distinguishes from an ordinary transport error.
WINDOW_CLOSED_ERROR_CODE = 131047

# WhatsApp rejects a text message body over 4096 chars; overflow past this
# is a trim-or-gist policy, never Telegram's multi-message fan-out, so this
# client itself never splits. Two callers apply that policy ahead of this
# one, each for the leg it can see: a self-hosted daemon relaying through
# the cloud gate trims before the body ever leaves the machine
# (``brr/gates/cloud.py`` ``_RESPONSE_LIMITS``); a fully hosted resident has
# no such daemon in front of it, so ``brnrd.inbox``'s own forwarder trims
# right before this call. Same limit, same boundary-safe trim
# (``brr.channels.telegram.trim_to_limit``), two vantage points.
MAX_BODY_LEN = 4096


class WindowClosed(RuntimeError):
    """A send was rejected because the 24h customer-service window is
    closed and no approved template was used (v1 doesn't send templates,
    so this is the expected shape of that limitation, not a bug)."""


def _messages_url(api_base_url: str, api_version: str, phone_number_id: str) -> str:
    return f"{api_base_url.rstrip('/')}/{api_version}/{phone_number_id}/messages"


def _raise_for_response(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    payload: dict = {}
    try:
        payload = resp.json() if resp.content else {}
    except ValueError:
        pass
    error = payload.get("error") if isinstance(payload, dict) else None
    code = (error or {}).get("code") if isinstance(error, dict) else None
    if code == WINDOW_CLOSED_ERROR_CODE:
        raise WindowClosed(
            "outside 24h window — template messages not yet supported"
        )
    detail = (error or {}).get("message") or f"HTTP {resp.status_code}"
    raise RuntimeError(f"whatsapp send failed: {detail}")


def send_message(
    access_token: str,
    phone_number_id: str,
    to: str,
    text: str,
    *,
    api_base_url: str = "https://graph.facebook.com",
    api_version: str = "v22.0",
    reply_to_message_id: str | None = None,
    timeout: float = 30.0,
) -> None:
    """Send one free-form text message via the Cloud API.

    No chunking — a body over ``MAX_BODY_LEN`` is the daemon's overflow
    concern (gist-or-truncate), not this client's; it posts whatever it is
    given and lets Meta's own length validation answer.

    Raises ``WindowClosed`` when the 24h window is the reason the send was
    refused, ``RuntimeError`` for any other failure.
    """
    params: dict = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text or " "},
    }
    if reply_to_message_id:
        params["context"] = {"message_id": reply_to_message_id}
    resp = httpx.post(
        _messages_url(api_base_url, api_version, phone_number_id),
        headers={"Authorization": f"Bearer {access_token}"},
        json=params,
        timeout=timeout,
    )
    _raise_for_response(resp)


@dataclass
class ParsedMessage:
    """Normalized shape of one inbound WhatsApp message — parallels
    ``telegram.ParsedMessage``. WhatsApp has no topic/thread concept (no
    ``topic_id``) and no separate numeric account id distinct from the
    chat itself: the sender's E.164 phone number (``wa_id``) is both the
    conversation's identity and the reply-to address, so ``chat_id`` and
    ``user_id`` carry the same value."""

    chat_id: str
    text: str
    message_date: datetime | None
    message_id: str | None
    user: str
    user_id: str | None
    # No edit concept surfaces on the Cloud API's inbound webhook (a status
    # update for an outbound message is a distinct payload shape entirely —
    # see ``parse_update`` — never confused with an inbound text edit).
    is_edit: bool = False
    # v1 does not ingest media (image/audio/document/etc.) — annotated only,
    # same "not ingested" treatment Telegram gives non-image attachments.
    has_media: bool = False
    attachments: list[dict] = field(default_factory=list)


_MEDIA_TYPES = {"image", "audio", "video", "document", "sticker", "location", "contacts"}


def parse_update(payload: dict) -> ParsedMessage | None:
    """Normalize one Cloud API webhook POST body into a message, or None.

    A webhook delivery batches ``entry[].changes[].value`` objects; each
    value carries either ``messages`` (inbound) or ``statuses`` (delivery/
    read receipts for an outbound send — never a trigger, always ignored
    here) or neither (other subscribed fields this integration doesn't
    use). Only the first inbound text/media message found is returned —
    Meta does not batch more than one new message into a single delivery
    in practice, and normalizing to "at most one" mirrors
    ``telegram.parse_update``'s one-message-per-call contract.
    """
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue
            messages = value.get("messages")
            if not isinstance(messages, list) or not messages:
                continue
            msg = messages[0]
            if not isinstance(msg, dict):
                continue
            wa_id = msg.get("from")
            if not wa_id:
                continue
            msg_type = str(msg.get("type") or "")
            text = ""
            if msg_type == "text":
                text = str((msg.get("text") or {}).get("body") or "").strip()
            has_media = msg_type in _MEDIA_TYPES
            if not text and not has_media:
                return None
            contacts = value.get("contacts") or []
            name = ""
            if contacts and isinstance(contacts[0], dict):
                name = str((contacts[0].get("profile") or {}).get("name") or "")
            raw_ts = msg.get("timestamp")
            try:
                message_date = datetime.fromtimestamp(int(raw_ts), timezone.utc)
            except (TypeError, ValueError, OSError):
                message_date = None
            return ParsedMessage(
                chat_id=str(wa_id),
                text=text,
                message_date=message_date,
                message_id=str(msg.get("id")) if msg.get("id") else None,
                user=name,
                user_id=str(wa_id),
                has_media=has_media,
            )
    return None


def verify_subscription(
    *,
    mode: str | None,
    verify_token: str | None,
    challenge: str | None,
    configured_verify_token: str,
) -> str | None:
    """Answer Meta's ``GET`` hub-challenge handshake, or None to refuse.

    Meta's documented contract: echo ``hub.challenge`` back as plain text
    iff ``hub.mode == "subscribe"`` and ``hub.verify_token`` matches the
    value configured when the webhook was registered in the Meta App
    Dashboard. A mismatch or missing piece answers None so the router can
    403 rather than confirm a subscription to an unconfigured token.
    """
    if mode != "subscribe" or not configured_verify_token:
        return None
    if not hmac.compare_digest(verify_token or "", configured_verify_token):
        return None
    return challenge
