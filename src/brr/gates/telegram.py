"""Telegram gate — polls Bot API for messages, delivers responses.

Runs as a thread inside the daemon (or standalone).  Communicates
with brr exclusively through the filesystem:

- Incoming messages → ``.brr/inbox/`` event files
- Outgoing replies  ← ``.brr/responses/`` response files

Credentials and runtime state live in ``.brr/gates/telegram.json``.
Telegram only requires a bot token; chat IDs are discovered from
incoming messages and stored on each event.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from .. import protocol, run_progress
from ..task import Task
from . import delivery, runtime

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_TG_LEN = 3900
_POLL_TIMEOUT = 30


# ── Bot API helpers ──────────────────────────────────────────────────


class _TelegramNotModified(Exception):
    """Telegram returned 400 "message is not modified" on editMessageText.

    Surfaces as a typed exception so render_update can treat it as a
    successful no-op instead of falling through to send a duplicate.
    """


def _api_call(token: str, method: str, params: dict | None = None) -> dict:
    url = _API.format(token=token, method=method)
    try:
        response = requests.post(url, json=params or {}, timeout=90)
    except requests.RequestException as exc:
        message = str(exc).replace(token, "<token>")
        raise RuntimeError(f"Telegram API request failed: {message}") from exc
    payload = _response_json(response)
    if response.status_code == 400 and method == "editMessageText":
        description = str(payload.get("description", ""))
        if "message is not modified" in description.lower():
            raise _TelegramNotModified(description) from None
    if not 200 <= response.status_code < 300:
        message = _telegram_error_message(response, payload)
        raise RuntimeError(f"Telegram API error {response.status_code}: {message}")
    if payload.get("ok") is False:
        description = str(payload.get("description") or "unknown")
        raise RuntimeError(f"Telegram API error: {description}")
    return payload


def _response_json(response: requests.Response) -> dict:
    """Decode a Telegram JSON envelope, best-effort."""
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _telegram_error_message(response: requests.Response, payload: dict) -> str:
    description = payload.get("description")
    if description:
        return str(description)
    if response.text:
        return response.text[:500]
    return response.reason or "unknown"


def _send_message(
    token: str,
    chat_id: int,
    text: str,
    topic_id: int | None = None,
    *,
    parse_mode: str | None = None,
    reply_to_message_id: int | None = None,
) -> dict:
    params: dict = {"chat_id": chat_id, "text": text}
    if topic_id:
        params["message_thread_id"] = topic_id
    if parse_mode:
        params["parse_mode"] = parse_mode
    if reply_to_message_id:
        # ``allow_sending_without_reply`` keeps delivery resilient when
        # the originating message has been deleted by the user before
        # the runner finished — Telegram would otherwise return a 400
        # and the response would be dropped.
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True
    return _api_call(token, "sendMessage", params)


def _edit_message(
    token: str,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
) -> dict:
    params: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if parse_mode:
        params["parse_mode"] = parse_mode
    return _api_call(token, "editMessageText", params)


def _send_with_overflow(
    token: str,
    chat_id: int,
    topic_id: int | None,
    text: str,
    *,
    reply_to_message_id: int | None = None,
) -> None:
    body = delivery.resolve_overflow(
        text, limit=_MAX_TG_LEN, gist_fn=delivery.post_gist
    )
    _send_message(
        token, chat_id, body, topic_id,
        reply_to_message_id=reply_to_message_id,
    )


# ── State ────────────────────────────────────────────────────────────


def _load_state(brr_dir: Path) -> dict:
    return runtime.load_state(brr_dir, "telegram")


def _save_state(brr_dir: Path, state: dict) -> None:
    runtime.save_state(brr_dir, "telegram", state)


def _load_progress_for_task(brr_dir: Path, task_id: str) -> dict | None:
    """Return this task's previously-rendered card state, or None.

    Test-facing accessor for the per-task card file; the live write
    path now lives in the shared ``delivery.update_card`` driver.
    """
    return runtime.load_task_card(brr_dir, "telegram", task_id)


def _save_progress_for_task(brr_dir: Path, task_id: str, entry: dict) -> None:
    """Write this task's card state file (test-facing accessor).

    Tests seed card state through this; the live write path goes
    through ``delivery.update_card``.
    """
    runtime.save_task_card(brr_dir, "telegram", task_id, entry)


# ── Interactive setup ────────────────────────────────────────────────


def auth(brr_dir: Path) -> None:
    """Prompt for bot token, validate, save."""
    state = _load_state(brr_dir)
    token = input("Telegram bot token (from @BotFather): ").strip()
    if not token:
        print("[brr] No token provided.")
        return
    try:
        resp = _api_call(token, "getMe")
        bot = resp.get("result", {})
        print(f"[brr] Authenticated as @{bot.get('username', '?')}")
    except Exception as e:
        print(f"[brr] Authentication failed: {e}")
        return
    state["token"] = token
    _save_state(brr_dir, state)
    print("[brr] Token saved. Start the daemon, then send the bot a message.")


def bind(brr_dir: Path) -> None:
    """Optionally restrict Telegram to a single chat/topic."""
    state = _load_state(brr_dir)
    if "token" not in state:
        print("[brr] Run `brr auth telegram` first.")
        return
    print("[brr] Telegram works with just `brr auth telegram`.")
    chat_id = input(
        "Optional chat ID to restrict to (leave empty to accept all): "
    ).strip()
    if not chat_id:
        state.pop("chat_id", None)
        state.pop("topic_id", None)
        _save_state(brr_dir, state)
        print("[brr] Telegram will accept messages from any chat.")
        return
    try:
        state["chat_id"] = int(chat_id)
    except ValueError:
        print("[brr] Chat ID must be a number.")
        return
    topic_id = input("Topic/thread ID (leave empty for none): ").strip()
    if topic_id:
        try:
            state["topic_id"] = int(topic_id)
        except ValueError:
            print("[brr] Topic ID must be a number.")
            return
    else:
        state.pop("topic_id", None)
    try:
        _send_message(state["token"], state["chat_id"], "brr bound.", state.get("topic_id"))
        print("[brr] Test message sent.")
    except Exception as e:
        print(f"[brr] Failed: {e}")
        return
    _save_state(brr_dir, state)
    print("[brr] Binding saved")


def setup(brr_dir: Path) -> None:
    """Configure Telegram credentials and optional chat/topic restriction."""
    auth(brr_dir)
    if "token" in _load_state(brr_dir):
        bind(brr_dir)


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return "token" in state


# ── Gate loop ────────────────────────────────────────────────────────


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    """Main gate loop — poll messages, create events, deliver responses.

    Designed to run in a daemon thread. Crashes are caught and retried
    with exponential backoff. No post-success pause: ``getUpdates``
    long-polls for ``_POLL_TIMEOUT`` seconds itself.
    """
    runtime.run_loop(
        lambda: _loop_once(brr_dir, inbox_dir, responses_dir),
        label="telegram",
    )


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    token = state["token"]
    configured_chat_id = state.get("chat_id")
    configured_topic_id = state.get("topic_id")
    offset = state.get("offset", 0)

    updates = _api_call(token, "getUpdates", {
        "offset": offset,
        "timeout": _POLL_TIMEOUT,
        "allowed_updates": ["message"],
    }).get("result", [])

    for update in updates:
        offset = update["update_id"] + 1
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        if chat_id is None:
            continue
        if configured_chat_id is not None and chat_id != configured_chat_id:
            continue
        topic_id = msg.get("message_thread_id")
        if configured_topic_id and topic_id != configured_topic_id:
            continue
        text = msg.get("text", "").strip()
        if not text:
            continue

        user = msg.get("from", {}).get("first_name", "?")
        message_id = msg.get("message_id")
        protocol.create_event(
            inbox_dir,
            source="telegram",
            body=text,
            telegram_chat_id=chat_id,
            telegram_topic_id=topic_id or "",
            telegram_user=user,
            telegram_message_id=message_id if message_id is not None else "",
        )

    state["offset"] = offset
    _save_state(brr_dir, state)

    _deliver_responses(
        brr_dir, inbox_dir, responses_dir, token,
        configured_chat_id, configured_topic_id,
    )


def _deliver_responses(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
    token: str,
    default_chat_id: int | None = None,
    default_topic_id: int | None = None,
) -> None:
    def deliver(event: dict, body: str) -> None:
        chat_id = _event_int(event, "telegram_chat_id", default_chat_id)
        if chat_id is None:
            raise RuntimeError("missing chat id")
        topic_id = _event_int(event, "telegram_topic_id", default_topic_id)
        reply_to = _event_int(event, "telegram_message_id")
        _send_with_overflow(
            token, chat_id, topic_id, body, reply_to_message_id=reply_to,
        )

    runtime.deliver_responses(inbox_dir, responses_dir, "telegram", deliver)


def _event_int(event: dict, key: str, default: int | None = None) -> int | None:
    if key not in event:
        return default
    return _coerce_optional_int(event.get(key))


def _coerce_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Live progress card ──────────────────────────────────────────────


# Card-worthy lifecycle packets; the canonical set lives in run_progress
# so the cloud gate renders exactly the same moments.
_RENDERABLE_PACKETS = run_progress.CARD_PACKETS


def _escape_html(text: str) -> str:
    """Minimal HTML entity escape for Telegram's HTML parse_mode.

    Telegram parses ``<`` / ``>`` / ``&`` and only the small allow-list
    of formatting tags (``<b>``, ``<i>``, ``<s>``, ``<u>``, ``<code>``,
    ``<pre>``, ``<a>``). Errors that surface in the failure detail can
    contain arbitrary characters from runner stderr — escape them so
    Telegram doesn't reject the edit with ``can't parse entities``.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_card_text(brr_dir: Path, conv_key: str, task_id: str) -> str | None:
    """Render the Telegram-flavoured progress card for a task, if any.

    Returns None when the conversation has no record of the task yet
    (e.g. heartbeat fired before task_created was persisted).
    """
    view = run_progress.project_task(brr_dir, conv_key, task_id)
    if view is None:
        return None
    # Escape user-controlled content (errors, branch names, runner names)
    # before render so the strike-through markers themselves stay valid
    # HTML. Tags are inserted post-escape by the renderer's style.
    sanitized = _sanitize_view_for_html(view)
    return run_progress.render_text(
        sanitized,
        compact=True,
        style=run_progress.TELEGRAM_HTML_STYLE,
    )


def card_text(brr_dir: Path, conv_key: str, task_id: str) -> str | None:
    """Render the Telegram-flavoured progress card for a task.

    Public seam so the managed ``cloud`` gate can reuse Telegram's
    presentation for telegram-origin events (see
    ``kb/design-managed-delivery.md`` → per-platform presentation), so a
    managed card looks identical to a self-hosted one.
    """
    return _build_card_text(brr_dir, conv_key, task_id)


def _sanitize_view_for_html(view):
    """Return a shallow copy of *view* with string fields HTML-escaped."""
    from dataclasses import replace

    def _esc(value):
        return _escape_html(value) if isinstance(value, str) else value

    new_history = [
        run_progress.PhaseEntry(
            name=entry.name,
            started_at=entry.started_at,
            ended_at=entry.ended_at,
            attempt=entry.attempt,
            detail=_esc(entry.detail),
        )
        for entry in view.phase_history
    ]
    return replace(
        view,
        runner_name=_esc(view.runner_name),
        env=_esc(view.env),
        branch_name=_esc(view.branch_name),
        display_base=_esc(view.display_base),
        detail=_esc(view.detail) if isinstance(view.detail, str) else view.detail,
        error=_esc(view.error),
        phase_history=new_history,
    )


class _CardTransport:
    """Direct Telegram transport for the shared card driver."""

    def __init__(self, token: str, chat_id: int, topic_id: int | None) -> None:
        self._token = token
        self._chat_id = chat_id
        self._topic_id = topic_id

    def send(self, text: str, *, reply_to: int | None = None) -> int | None:
        resp = _send_message(
            self._token, self._chat_id, text, self._topic_id,
            parse_mode="HTML", reply_to_message_id=reply_to,
        )
        return (resp.get("result") or {}).get("message_id")

    def edit(self, message_id: int, text: str) -> None:
        try:
            _edit_message(
                self._token, self._chat_id, message_id, text, parse_mode="HTML",
            )
        except _TelegramNotModified:
            raise delivery.CardUnchanged from None


def render_update(brr_dir: Path, packet: Any) -> None:
    """Send/edit a Telegram progress card for *packet*.

    On ``task_created`` we send a fresh message in the originating chat
    or topic and store the resulting ``message_id`` so later packets can
    edit the same message via ``editMessageText``. Failures are swallowed
    — the daemon must keep running even if Telegram is misconfigured.
    """
    ptype = getattr(packet, "type", None)
    if ptype not in _RENDERABLE_PACKETS:
        return

    state = _load_state(brr_dir)
    token = state.get("token")
    if not token:
        return

    conv_key = getattr(packet, "conversation_key", "") or ""
    task_id = run_progress.task_id_from_packet(packet)
    if not conv_key or not task_id:
        return

    task = Task.from_file(brr_dir / "tasks" / f"{task_id}.md")
    if task is None or task.source != "telegram":
        return
    chat_id = _coerce_optional_int(task.meta.get("telegram_chat_id"))
    if chat_id is None:
        return
    topic_id = _coerce_optional_int(task.meta.get("telegram_topic_id"))
    # Thread the initial card under the user's message. Subsequent edits
    # ride on the stored ``message_id`` (Telegram has no way to change a
    # message's reply target after the fact, so this only matters once).
    reply_to = _coerce_optional_int(task.meta.get("telegram_message_id"))

    text = _build_card_text(brr_dir, conv_key, task_id)
    if text is None:
        return

    transport = _CardTransport(token, chat_id, topic_id)
    delivery.update_card(
        brr_dir, "telegram", task_id, text,
        transport=transport, reply_to=reply_to, render_tag=ptype,
    )
