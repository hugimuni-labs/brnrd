"""Hosted Signal transport over brnrd's authenticated Signal bridge.

The bridge owns signal-cli's persistent cryptographic state.  This module
stays transport-only: normalize the bridge webhook and send a reply through
its authenticated HTTP surface. Pairing and routing live in webhooks.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

MAX_BODY_LEN = 4000


@dataclass(frozen=True)
class ParsedMessage:
    chat_id: str
    text: str
    message_date: datetime | None
    message_id: str | None
    user: str
    user_id: str
    is_edit: bool = False
    has_media: bool = False
    attachments: tuple[()] = ()


def parse_update(payload: dict) -> ParsedMessage | None:
    """Normalize signal-cli's JSON-RPC webhook envelope.

    Upstream emits ``params.envelope`` in json-rpc mode. Accepting a bare
    ``envelope`` as well keeps this seam compatible with the REST receive
    representation and makes replay fixtures provider-native.
    """
    params = payload.get("params") if isinstance(payload, dict) else None
    envelope = params.get("envelope") if isinstance(params, dict) else None
    if not isinstance(envelope, dict):
        envelope = payload.get("envelope") if isinstance(payload, dict) else None
    if not isinstance(envelope, dict):
        return None
    data = envelope.get("dataMessage")
    if not isinstance(data, dict) or data.get("groupInfo") is not None:
        return None
    text = str(data.get("message") or "").strip()
    if not text:
        return None
    sender = str(envelope.get("sourceNumber") or envelope.get("source") or "").strip()
    if not sender:
        return None
    stamp = envelope.get("timestamp") or data.get("timestamp")
    message_date = None
    try:
        message_date = datetime.fromtimestamp(int(stamp) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        pass
    return ParsedMessage(
        chat_id=sender,
        text=text,
        message_date=message_date,
        message_id=str(stamp) if stamp is not None else None,
        user=str(envelope.get("sourceName") or sender).replace("\r", " ").replace("\n", " "),
        user_id=sender,
    )


def send_message(
    api_url: str,
    api_token: str,
    number: str,
    to: str,
    text: str,
    *,
    timeout: float = 30.0,
) -> str | None:
    """Send through the authenticated bridge; return its provider receipt."""
    response = httpx.post(
        f"{api_url.rstrip('/')}/v2/send",
        headers={"Authorization": f"Bearer {api_token}"},
        json={"message": text, "number": number, "recipients": [to]},
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        return None
    stamp = payload.get("timestamp") if isinstance(payload, dict) else None
    return str(stamp) if stamp is not None else None
