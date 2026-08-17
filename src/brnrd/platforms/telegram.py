"""Hosted Telegram glue over the transport shared with the local gate."""

from __future__ import annotations

import httpx

from brr.channels import telegram as transport

_API = "https://api.telegram.org/bot{token}/{method}"
_FILE_API = "https://api.telegram.org/file/bot{token}/{file_path}"
_MAX_LEN = 4000
_MAX_CHUNKS = 12

ParsedMessage = transport.ParsedMessage
parse_update = transport.parse_update
extract_attachments = transport.extract_attachments
parse_migration = transport.parse_migration
pair_code_from_text = transport.pair_code_from_text
redact_secrets = transport.redact_secrets
_safe_filename = transport._safe_filename


def split_message(text: str, limit: int = _MAX_LEN) -> list[str]:
    """Hosted policy: at most twelve 4000-character Telegram messages."""
    return transport.split_message(text, limit, max_chunks=_MAX_CHUNKS)


def _redact_http_error(exc: httpx.HTTPError) -> None:
    """Sanitize an HTTPX error in place so its concrete error class survives."""
    exc.args = (redact_secrets(str(exc)),)
    request = getattr(exc, "_request", None)
    if request is not None:
        request.url = httpx.URL(redact_secrets(str(request.url)))


def _post_json(token: str, method: str, params: dict, timeout: float) -> dict:
    """Adapt HTTPX to the shared transport's client-injected API seam."""
    try:
        response = httpx.post(
            _API.format(token=token, method=method), json=params, timeout=timeout
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        _redact_http_error(exc)
        raise
    try:
        payload = response.json()
    except (AttributeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


class FileTooLarge(RuntimeError):
    """A Telegram file exceeds the configured proxy size cap."""


def resolve_file(token: str, file_id: str, *, timeout: float = 30.0) -> dict:
    """Resolve *file_id* via ``getFile`` — fresh per request, never cached."""
    try:
        resp = httpx.post(
            _API.format(token=token, method="getFile"),
            json={"file_id": file_id},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        _redact_http_error(exc)
        raise
    payload = {}
    try:
        payload = resp.json() if resp.content else {}
    except ValueError:
        pass
    result = payload.get("result") if isinstance(payload, dict) else None
    if (
        resp.status_code != 200
        or not isinstance(result, dict)
        or not result.get("file_path")
    ):
        detail = redact_secrets(
            str((payload or {}).get("description") or f"HTTP {resp.status_code}")
        )
        raise RuntimeError(f"telegram getFile failed: {detail}")
    return result


def fetch_file_bytes(
    token: str,
    file_path: str,
    *,
    max_bytes: int,
    timeout: float = 60.0,
) -> bytes:
    """Stream a resolved Telegram file through without storing it at rest."""
    chunks: list[bytes] = []
    size = 0
    try:
        with httpx.stream(
            "GET", _FILE_API.format(token=token, file_path=file_path), timeout=timeout
        ) as resp:
            if resp.status_code != 200:
                raise RuntimeError(
                    f"telegram file fetch failed: HTTP {resp.status_code}"
                )
            for chunk in resp.iter_bytes(65536):
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLarge(f"file exceeds {max_bytes} bytes")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        _redact_http_error(exc)
        raise RuntimeError(f"telegram file fetch failed: {exc}") from exc
    return b"".join(chunks)


def send_message(
    token: str,
    chat_id: str | int,
    text: str,
    *,
    topic_id: int | None = None,
    reply_to_message_id: int | None = None,
    timeout: float = 30.0,
) -> None:
    transport.send_message(
        lambda method, params, call_timeout: _post_json(
            token, method, params, call_timeout
        ),
        chat_id,
        text,
        policy=transport.MessagePolicy(limit=_MAX_LEN, max_chunks=_MAX_CHUNKS),
        topic_id=topic_id,
        reply_to_message_id=reply_to_message_id,
        timeout=timeout,
    )


def send_fresh_message(
    token: str,
    chat_id: str | int,
    text: str,
    *,
    topic_id: int | None = None,
    timeout: float = 30.0,
) -> str | None:
    """Unaddressed send (#1205's fresh-send primitive) — no reply target.

    Same chunking policy as :func:`send_message`, but that function returns
    ``None``: it exists only to *forward* an already-addressed reply, where
    nothing downstream reads the platform id back. A fresh send has no
    inbound event to attach a receipt to, so its caller (the daemon
    endpoint) needs the id to hand back — the last chunk's, the one Telegram
    actually rendered as the tail of what the correspondent sees.
    """
    results = transport.send_message(
        lambda method, params, call_timeout: _post_json(
            token, method, params, call_timeout
        ),
        chat_id,
        text,
        policy=transport.MessagePolicy(limit=_MAX_LEN, max_chunks=_MAX_CHUNKS),
        topic_id=topic_id,
        timeout=timeout,
    )
    if not results:
        return None
    last_result = results[-1].get("result") if isinstance(results[-1], dict) else None
    message_id = (
        last_result.get("message_id") if isinstance(last_result, dict) else None
    )
    return None if message_id is None else str(message_id)


def set_webhook(
    token: str,
    url: str,
    *,
    secret_token: str,
    timeout: float = 30.0,
) -> None:
    """Register the hosted Telegram webhook for this bot token."""
    try:
        resp = httpx.post(
            _API.format(token=token, method="setWebhook"),
            json={"url": url, "secret_token": secret_token},
            timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        _redact_http_error(exc)
        raise


class CardGone(RuntimeError):
    """A progress card cannot be edited (deleted or expired)."""


def send_card(
    token: str,
    chat_id: str | int,
    text: str,
    *,
    topic_id: int | None = None,
    reply_to_message_id: int | None = None,
    timeout: float = 30.0,
) -> int | None:
    results = transport.send_message(
        lambda method, params, call_timeout: _post_json(
            token, method, params, call_timeout
        ),
        chat_id,
        text,
        policy=transport.MessagePolicy(limit=None),
        topic_id=topic_id,
        reply_to_message_id=reply_to_message_id,
        parse_mode="HTML",
        timeout=timeout,
    )
    return ((results[0].get("result") or {}).get("message_id"))


def edit_card(
    token: str,
    chat_id: str | int,
    message_id: int,
    text: str,
    *,
    timeout: float = 30.0,
) -> None:
    """Edit a progress card, translating a stale message to ``CardGone``."""

    def call(method: str, params: dict, call_timeout: float) -> dict:
        try:
            resp = httpx.post(
                _API.format(token=token, method=method),
                json=params,
                timeout=call_timeout,
            )
        except httpx.HTTPError as exc:
            _redact_http_error(exc)
            raise
        if resp.status_code == 400:
            try:
                description = str((resp.json() or {}).get("description", ""))
            except ValueError:
                description = resp.text
            description = redact_secrets(description)
            if "not modified" in description.lower():
                return {}
            raise CardGone(description or "card not editable")
        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            _redact_http_error(exc)
            raise
        return {}

    transport.edit_message(
        call,
        chat_id,
        message_id,
        text,
        parse_mode="HTML",
        timeout=timeout,
    )
