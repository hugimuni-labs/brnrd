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

import hashlib
import json
import tempfile
import threading
from pathlib import Path
from typing import Any

import requests

from .. import conversations, menus, protocol, run_progress, trust
from ..channels import telegram as transport
from ..run import Run, run_manifest_path
from . import delivery, runtime

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_TG_LEN = 3900
_POLL_TIMEOUT = 30
_DELIVERY_INTERVAL = 1.0


_sanitize_meta_str = transport.sanitize_meta_str

# Telegram long-polling can hold one HTTP request open for up to
# _POLL_TIMEOUT seconds. Keep it on a separate session from outbound
# sends/edits so a progress card or folded-in reply never queues behind
# getUpdates on shared connection state.
_POLL_SESSION = requests.Session()
_SESSION = requests.Session()
_SESSION_LOCK = threading.Lock()


# ── Bot API helpers ──────────────────────────────────────────────────


class _TelegramNotModified(Exception):
    """Telegram returned 400 "message is not modified" on editMessageText.

    Surfaces as a typed exception so render_update can treat it as a
    successful no-op instead of falling through to send a duplicate.
    """


class _TelegramMessageGone(Exception):
    """Telegram returned 400 "message to edit not found" on editMessageText.

    The message was deleted or expired — the one edit failure that should
    map to ``delivery.CardGone`` and trigger a re-send. Kept distinct from
    every other API error (rate limit, 5xx, timeout) so those keep retrying
    the same edit instead of being mistaken for a gone message.
    """


def _api_call(
    token: str,
    method: str,
    params: dict | None = None,
    *,
    poll: bool = False,
) -> dict:
    url = _API.format(token=token, method=method)
    try:
        session = _POLL_SESSION if poll else _SESSION
        if poll:
            response = session.post(url, json=params or {}, timeout=90)
        else:
            with _SESSION_LOCK:
                response = session.post(url, json=params or {}, timeout=90)
    except requests.RequestException as exc:
        message = str(exc).replace(token, "<token>")
        raise RuntimeError(f"Telegram API request failed: {message}") from exc
    payload = _response_json(response)
    if response.status_code == 400 and method == "editMessageText":
        description = str(payload.get("description", ""))
        lowered = description.lower()
        if "message is not modified" in lowered:
            raise _TelegramNotModified(description) from None
        if "message to edit not found" in lowered:
            raise _TelegramMessageGone(description) from None
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
    reply_markup: dict[str, Any] | None = None,
) -> dict:
    results = transport.send_message(
        lambda method, params, timeout: _api_call(token, method, params),
        chat_id,
        text,
        policy=transport.MessagePolicy(limit=None),
        topic_id=topic_id,
        parse_mode=parse_mode,
        reply_to_message_id=reply_to_message_id,
        reply_markup=reply_markup,
        timeout=90,
    )
    return results[0]


def _edit_message(
    token: str,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> dict:
    return transport.edit_message(
        lambda method, params, timeout: _api_call(token, method, params),
        chat_id,
        message_id,
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        timeout=90,
    )


def _answer_callback(token: str, callback_id: str, text: str = "") -> None:
    params: dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        params["text"] = text
    try:
        _api_call(token, "answerCallbackQuery", params)
    except RuntimeError:
        # The event is already durable. A transient failure to dismiss the
        # Telegram spinner must not abort offset persistence and replay the
        # same tap into a second event.
        pass


def _send_with_overflow(
    token: str,
    chat_id: int,
    topic_id: int | None,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    overflow_cache: delivery.OverflowCache | None = None,
) -> dict:
    body = delivery.resolve_overflow(
        text, limit=_MAX_TG_LEN, gist_fn=delivery.post_gist,
        cache=overflow_cache,
    )
    return _send_message(
        token, chat_id, body, topic_id,
        reply_to_message_id=reply_to_message_id,
    )


# ── Image attachments ────────────────────────────────────────────────
# Telegram photos/documents become local files a downloaded event
# references, the same shape GitHub's inline image links resolve to (see
# ``gates/github/attachments.py`` and ``protocol.create_event``'s
# ``attachment_files``) — one convention, both gates.

_FILE_API = "https://api.telegram.org/file/bot{token}/{file_path}"
# Telegram's own bot-API file-download cap; enforced here too so a
# pathological response can't be streamed indefinitely into a tmp file.
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _pick_image_file_id(msg: dict) -> tuple[str, str] | None:
    """Return ``(file_id, suggested_filename)`` for an image in *msg*.

    A ``photo`` arrives as an ascending-resolution ``PhotoSize`` array
    with no filename of its own (Telegram always transcodes photos to
    JPEG), so the largest size is taken and named generically. A
    ``document`` (drag-and-drop, or "compress: off" in the client) keeps
    its original filename and MIME type — only image documents qualify.
    Anything else (voice, video, sticker, a non-image document) returns
    ``None``: this is image support, not a general attachment pipeline.
    """
    attachments = transport.extract_attachments(msg)
    if not attachments:
        return None
    pointer = attachments[0]
    return str(pointer["file_id"]), str(pointer["filename"])


def _download_telegram_file(token: str, file_id: str, dest: Path) -> bool:
    """Download a Telegram file by id into *dest*. Returns success.

    Two calls: ``getFile`` resolves the id to a server-side path, then a
    plain GET against Telegram's separate file-serving host (not
    ``_api_call`` — that endpoint returns raw bytes, not a JSON
    envelope). Best-effort throughout: any failure (expired file,
    network hiccup, oversized response) returns ``False`` rather than
    raising, so a flaky download degrades to "message arrived with no
    attachment" instead of dropping the whole inbound message.
    """
    try:
        info = _api_call(token, "getFile", {"file_id": file_id})
    except RuntimeError:
        return False
    file_path = (info.get("result") or {}).get("file_path")
    if not file_path:
        return False
    url = _FILE_API.format(token=token, file_path=file_path)
    try:
        with _SESSION_LOCK:
            response = _SESSION.get(url, timeout=90, stream=True)
        if not 200 <= response.status_code < 300:
            return False
        size = 0
        with open(dest, "wb") as fh:
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > _MAX_ATTACHMENT_BYTES:
                    return False
                fh.write(chunk)
    except requests.RequestException:
        return False
    return True


# ── State ────────────────────────────────────────────────────────────


def _load_state(brr_dir: Path) -> dict:
    return runtime.load_state(brr_dir, "telegram")


def _save_state(brr_dir: Path, state: dict) -> None:
    runtime.save_state(brr_dir, "telegram", state)


def _load_progress_for_run(brr_dir: Path, run_id: str) -> dict | None:
    """Return this run's previously-rendered card state, or None.

    Test-facing accessor for the per-run card file; the live write
    path now lives in the shared ``delivery.update_card`` driver.
    """
    return runtime.load_run_card(brr_dir, "telegram", run_id)


def _save_progress_for_run(brr_dir: Path, run_id: str, entry: dict) -> None:
    """Write this run's card state file (test-facing accessor).

    Tests seed card state through this; the live write path goes
    through ``delivery.update_card``.
    """
    runtime.save_run_card(brr_dir, "telegram", run_id, entry)


# ── Interactive setup ────────────────────────────────────────────────


def auth(brr_dir: Path) -> None:
    """Prompt for bot token, validate, save."""
    state = _load_state(brr_dir)
    token = input("Telegram bot token (from @BotFather): ").strip()
    if not token:
        print("[brnrd] No token provided.")
        return
    try:
        resp = _api_call(token, "getMe")
        bot = resp.get("result", {})
        print(f"[brnrd] Authenticated as @{bot.get('username', '?')}")
    except Exception as e:
        print(f"[brnrd] Authentication failed: {e}")
        return
    state["token"] = token
    _save_state(brr_dir, state)
    print("[brnrd] Token saved. Start the daemon, then send the bot a message.")


def bind(brr_dir: Path) -> None:
    """Optionally restrict Telegram to a single chat/topic.

    Also records the authorizing principal (#409): every inbound message
    is checked against ``state["paired_user_id"]`` (this prompt) or
    ``state["allowlist"]`` (edited directly in ``.brr/gates/telegram.json``,
    a JSON list of Telegram user ids) before it becomes an event — a
    default-closed gate independent of the optional chat/topic
    restriction below.
    """
    state = _load_state(brr_dir)
    if "token" not in state:
        print("[brnrd] Run `brnrd gate auth telegram` first.")
        return
    print("[brnrd] Telegram works with just `brnrd gate auth telegram`.")
    user_id_raw = input(
        "Your Telegram user ID, to authorize as the paired principal "
        "(required — see e.g. @userinfobot; messages from anyone else "
        "are rejected): "
    ).strip()
    if not user_id_raw:
        print("[brnrd] A user ID is required so brr knows who to trust.")
        return
    try:
        state["paired_user_id"] = int(user_id_raw)
    except ValueError:
        print("[brnrd] User ID must be a number.")
        return
    chat_id = input(
        "Optional chat ID to restrict to (leave empty to accept all): "
    ).strip()
    if not chat_id:
        state.pop("chat_id", None)
        state.pop("topic_id", None)
        _save_state(brr_dir, state)
        print("[brnrd] Telegram will accept messages from any chat.")
        return
    try:
        state["chat_id"] = int(chat_id)
    except ValueError:
        print("[brnrd] Chat ID must be a number.")
        return
    topic_id = input("Topic/thread ID (leave empty for none): ").strip()
    if topic_id:
        try:
            state["topic_id"] = int(topic_id)
        except ValueError:
            print("[brnrd] Topic ID must be a number.")
            return
    else:
        state.pop("topic_id", None)
    try:
        _send_message(state["token"], state["chat_id"], "brnrd bound.", state.get("topic_id"))
        print("[brnrd] Test message sent.")
    except Exception as e:
        print(f"[brnrd] Failed: {e}")
        return
    _save_state(brr_dir, state)
    print("[brnrd] Binding saved")


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

    Designed to run in a daemon thread. Inbound polling and outbound
    delivery are deliberately split: Telegram ``getUpdates`` is a long
    poll, so letting it own response delivery would make folded-in
    replies wait behind the poll timeout. The outbound loop scans local
    response queues once per second and only hits Telegram when there is
    a message to send.
    """
    threading.Thread(
        target=runtime.run_loop,
        args=(lambda: _delivery_loop_once(brr_dir, inbox_dir, responses_dir),),
        kwargs={
            "label": "telegram-delivery",
            "poll_interval": _DELIVERY_INTERVAL,
        },
        daemon=True,
        name="gate-telegram-delivery",
    ).start()
    runtime.run_loop(
        lambda: _loop_once(brr_dir, inbox_dir, responses_dir),
        label="telegram",
        brr_dir=brr_dir,
        gate="telegram",
    )


def _delivery_loop_once(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
) -> None:
    state = _load_state(brr_dir)
    token = state["token"]
    # An explicit `bind` sets state["chat_id"]; absent that, fall back to
    # the most recently seen inbound chat (state["last_chat_id"], updated
    # in _loop_once) so a self-originated event (schedule/director-tick —
    # no telegram_chat_id of its own) still has somewhere to deliver.
    _deliver_responses(
        brr_dir,
        inbox_dir,
        responses_dir,
        token,
        state.get("chat_id", state.get("last_chat_id")),
        state.get("topic_id"),
    )


def _sender_tier(state: dict, user_id: int | None) -> str | None:
    """#409 / #517 — the verified sender's trust tier, or ``None`` if denied.

    Default-closed: the sender must be the bound principal
    (``state['paired_user_id']``, set by ``bind``) or listed in
    ``state['allowlist']`` (a JSON array of Telegram user ids, edited
    directly in ``.brr/gates/telegram.json`` — no CLI setter yet). No
    sender id at all (``sender_chat`` / a missing ``from``) is never
    authorized, regardless of either list.

    The bound principal is the operator → ``owner`` tier (configured
    default env, today's behaviour). An allowlisted-but-not-bound sender
    is a known collaborator → ``collaborator`` tier, whose env the
    resolver can tighten via ``trust.collaborator_env`` (#517).
    """
    if user_id is None:
        return None
    paired = state.get("paired_user_id")
    if paired is not None:
        try:
            if int(paired) == int(user_id):
                return trust.OWNER
        except (TypeError, ValueError):
            pass
    for allowed in state.get("allowlist") or []:
        try:
            if int(allowed) == int(user_id):
                return trust.COLLABORATOR
        except (TypeError, ValueError):
            continue
    return None


def _authorized_sender(state: dict, user_id: int | None) -> bool:
    """Back-compat boolean wrapper over :func:`_sender_tier`."""
    return _sender_tier(state, user_id) is not None


def _handle_menu_callback(
    brr_dir: Path,
    inbox_dir: Path,
    state: dict,
    callback: dict[str, Any],
    *,
    configured_chat_id: int | None,
    configured_topic_id: int | None,
) -> None:
    """Resolve one inline-keyboard tap into a pending ``menu_answer`` event."""
    token = str(state["token"])
    callback_id = str(callback.get("id") or "")
    parsed = menus.parse_callback_data(callback.get("data"))
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    topic_id = message.get("message_thread_id")
    sender = callback.get("from") or {}
    user_id = sender.get("id")
    sender_tier = _sender_tier(state, user_id)

    if (
        parsed is None
        or chat_id is None
        or sender_tier is None
        or (
            configured_chat_id is not None
            and chat_id != configured_chat_id
        )
        or (
            configured_topic_id is not None
            and topic_id != configured_topic_id
        )
    ):
        if sender_tier is None:
            print(
                f"[brnrd] telegram callback authz denied: "
                f"chat={chat_id} user={user_id}"
            )
        if callback_id:
            _answer_callback(token, callback_id, "That menu answer is not valid.")
        return

    menu_id, option = parsed
    thread = f"telegram:{chat_id}:{topic_id or ''}"
    correspondent_key = conversations.correspondent_key_for_event(
        {
            "source": "telegram",
            "telegram_user": sender.get("first_name") or "?",
            "telegram_user_id": user_id,
            "telegram_username": sender.get("username") or "",
        }
    )
    legacy_threads = conversations.conversation_keys_for_correspondent(
        brr_dir,
        correspondent_key,
        include_key=thread,
    )
    state["last_chat_id"] = chat_id
    path = menus.create_answer_event(
        brr_dir,
        inbox_dir,
        source="telegram",
        thread=thread,
        menu_id=menu_id,
        option=option,
        correspondent_key=correspondent_key,
        legacy_threads=legacy_threads,
        telegram_chat_id=chat_id,
        telegram_topic_id=topic_id or "",
        telegram_user=_sanitize_meta_str(str(sender.get("first_name") or "?")),
        telegram_user_id=user_id,
        telegram_username=_sanitize_meta_str(
            str(sender.get("username") or "")
        ),
        telegram_message_id=message.get("message_id") or "",
        trust_tier=sender_tier,
    )
    event = protocol._read_event(path) or {}
    status = str(event.get("menu_status") or "unknown")
    callback_text = {
        "live": "Got it.",
        "stale": "That menu was superseded; your answer was still sent.",
        "expired": "That menu expired; your answer was still sent.",
        "unknown": "That menu is no longer known; your answer was still sent.",
    }.get(status, "Your answer was sent.")
    if callback_id:
        _answer_callback(token, callback_id, callback_text)


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    token = state["token"]
    configured_chat_id = state.get("chat_id")
    configured_topic_id = state.get("topic_id")
    offset = state.get("offset", 0)

    updates = _api_call(token, "getUpdates", {
        "offset": offset,
        "timeout": _POLL_TIMEOUT,
        "allowed_updates": ["message", "callback_query"],
    }, poll=True).get("result", [])

    for update in updates:
        offset = update["update_id"] + 1
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            _handle_menu_callback(
                brr_dir,
                inbox_dir,
                state,
                callback,
                configured_chat_id=configured_chat_id,
                configured_topic_id=configured_topic_id,
            )
            continue
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        if chat_id is None:
            continue
        # #409 — a group->supergroup migration service message carries no
        # text/sender and must never be treated as a trigger; but the
        # bound chat id (if any) should follow the migration rather than
        # going silently stale. Two shapes arrive, one per chat:
        # ``migrate_to_chat_id`` on the old chat, ``migrate_from_chat_id``
        # on the new one.
        migration = transport.parse_migration(update)
        if migration is not None:
            old_id, new_id = (int(value) for value in migration)
            if configured_chat_id is not None and configured_chat_id == old_id:
                state["chat_id"] = new_id
                configured_chat_id = new_id
            continue
        if configured_chat_id is not None and chat_id != configured_chat_id:
            continue
        # Track the most recently seen chat as a delivery fallback —
        # distinct from ``configured_chat_id`` (the inbound *filter*,
        # deliberately left unset so any chat's messages become events;
        # see test_loop_accepts_any_chat_and_records_message_chat). A
        # schedule-originated event (a director tick, say) carries no
        # telegram_chat_id of its own, so its response has nowhere to go
        # without a default — and without an explicit `bind` ever having
        # been run, state had no default at all: `_deliver_responses`
        # raised "missing chat id" on every delivery-loop tick, forever,
        # since nothing marks a failed delivery done (see
        # deliver_stream's per-event try/except). Caught live 2026-07-06
        # via two director-tick responses stuck spamming the daemon log
        # (evt-...-hzyc, evt-...-zb04).
        state["last_chat_id"] = chat_id
        topic_id = msg.get("message_thread_id")
        if configured_topic_id and topic_id != configured_topic_id:
            continue
        parsed = transport.parse_update(update)
        # The hosted home annotates unsupported media so it can answer it;
        # the local file protocol only carries downloaded images. Keep that
        # home policy here while sharing the normalization and qualification.
        if parsed is None or (not parsed.text and not parsed.attachments):
            continue

        sender_tier = _sender_tier(state, parsed.user_id)
        if sender_tier is None:
            # #409 — default-closed gate audit trail. No reply is sent:
            # telling an unauthorized sender why would let them probe for
            # a valid principal.
            print(
                f"[brnrd] telegram authz denied: "
                f"chat={chat_id} user={parsed.user_id}"
            )
            continue

        attachment_files: list[Path] = []
        image_tmpdir: tempfile.TemporaryDirectory | None = None
        if parsed.attachments:
            pointer = parsed.attachments[0]
            file_id = str(pointer["file_id"])
            suggested_name = str(pointer["filename"])
            image_tmpdir = tempfile.TemporaryDirectory()
            dest = Path(image_tmpdir.name) / suggested_name
            if _download_telegram_file(token, file_id, dest):
                attachment_files.append(dest)

        protocol.create_event(
            inbox_dir,
            source="telegram",
            body=parsed.text,
            attachment_files=attachment_files or None,
            telegram_chat_id=chat_id,
            telegram_topic_id=topic_id or "",
            # Sanitize sender-controlled strings: display name and username
            # are attacker-reachable and may contain embedded newlines that
            # would forge extra frontmatter fields (#413 §7 S3).
            telegram_user=parsed.user,
            telegram_user_id=parsed.user_id if parsed.user_id is not None else "",
            telegram_username=parsed.username,
            telegram_message_id=(
                parsed.message_id if parsed.message_id is not None else ""
            ),
            telegram_sent_at=parsed.sent_at if parsed.sent_at is not None else "",
            trust_tier=sender_tier,
        )
        if image_tmpdir is not None:
            image_tmpdir.cleanup()

    state["offset"] = offset
    _save_state(brr_dir, state)


def _deliver_responses(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
    token: str,
    default_chat_id: int | None = None,
    default_topic_id: int | None = None,
) -> None:
    overflow_cache = delivery.OverflowCache(brr_dir, "telegram")

    def deliver(event: dict, body: str) -> dict:
        chat_id = _event_int(event, "telegram_chat_id", default_chat_id)
        if chat_id is None:
            # There is no chat to send to and no later poll will invent one.
            raise runtime.PermanentDeliveryError(
                "the event carries no telegram chat id and this gate has no "
                "default chat configured"
            )
        topic_id = _event_int(event, "telegram_topic_id", default_topic_id)
        reply_to = _event_int(event, "telegram_message_id")
        return _send_with_overflow(
            token, chat_id, topic_id, body, reply_to_message_id=reply_to,
            overflow_cache=overflow_cache,
        )

    runtime.deliver_stream(
        inbox_dir, responses_dir, "telegram", deliver, brr_dir=brr_dir,
    )


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


def _build_card_text(brr_dir: Path, conv_key: str, run_id: str) -> str | None:
    """Render the Telegram-flavoured progress card for a run, if any.

    Returns None when the conversation has no record of the run yet
    (e.g. heartbeat fired before run_created was persisted).
    """
    view = run_progress.project_run(brr_dir, conv_key, run_id)
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


def card_text(brr_dir: Path, conv_key: str, run_id: str) -> str | None:
    """Render the Telegram-flavoured progress card for a run.

    Public seam so the managed ``cloud`` gate can reuse Telegram's
    presentation for telegram-origin events (see
    ``kb/design-managed-delivery.md`` → per-platform presentation), so a
    managed card looks identical to a self-hosted one.
    """
    return _build_card_text(brr_dir, conv_key, run_id)


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
        agent_card_text=_esc(view.agent_card_text),
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
        except _TelegramMessageGone:
            raise delivery.CardGone from None


def _menu_render_state_path(brr_dir: Path, thread: str) -> Path:
    digest = hashlib.sha256(thread.encode("utf-8")).hexdigest()[:24]
    return brr_dir / "gates" / "telegram" / "menus" / f"{digest}.json"


def _load_menu_render_state(brr_dir: Path, thread: str) -> dict[str, Any]:
    path = _menu_render_state_path(brr_dir, thread)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_menu_render_state(
    brr_dir: Path,
    thread: str,
    state: dict[str, Any],
) -> None:
    path = _menu_render_state_path(brr_dir, thread)
    path.parent.mkdir(parents=True, exist_ok=True)
    protocol._atomic_write(
        path,
        json.dumps(state, indent=2, sort_keys=True) + "\n",
    )


def _telegram_thread_target(thread: str) -> tuple[int, int | None] | None:
    _, thread = conversations.split_conversation_key(thread)
    parts = thread.split(":", 2)
    if len(parts) != 3 or parts[0] != "telegram":
        return None
    chat_id = _coerce_optional_int(parts[1])
    if chat_id is None:
        return None
    return chat_id, _coerce_optional_int(parts[2])


def _menu_reply_markup(menu: dict[str, Any]) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    menu_id = str(menu.get("menu_id") or "")
    for option in menu.get("options", []):
        if not isinstance(option, dict):
            continue
        handle = str(option.get("handle") or "")
        label = str(option.get("label") or handle)
        if option.get("rec"):
            label = f"★ {label}"
        rows.append([
            {
                "text": label,
                "callback_data": menus.callback_data(menu_id, handle),
            }
        ])
    return {"inline_keyboard": rows}


def _menu_message_text(menu: dict[str, Any]) -> str:
    rendered = menus.render_numbered(menu).replace("`", "")
    if rendered:
        return rendered
    return "No standing options."


def _render_live_menu(brr_dir: Path, token: str, packet: Any) -> None:
    payload = getattr(packet, "payload", None) or {}
    menu = payload.get("menu")
    if not isinstance(menu, dict):
        return
    thread = str(menu.get("thread") or "").strip()
    target = _telegram_thread_target(thread)
    if target is None:
        return
    chat_id, topic_id = target
    # Render receipts belong to the actual Telegram destination. This keeps
    # the existing native-thread receipt reusable when a cloud-wrapped run
    # promotes the next correspondent-keyed generation, avoiding a duplicate
    # keyboard during migration.
    render_thread = f"telegram:{chat_id}:{topic_id or ''}"
    markup = (
        {"inline_keyboard": []}
        if menus.is_expired(menu)
        else _menu_reply_markup(menu)
    )
    text = (
        "This menu has expired."
        if menus.is_expired(menu)
        else _menu_message_text(menu)
    )
    state = _load_menu_render_state(brr_dir, render_thread)
    message_id = _coerce_optional_int(state.get("message_id"))
    if message_id is not None:
        try:
            _edit_message(
                token,
                chat_id,
                message_id,
                text,
                reply_markup=markup,
            )
        except _TelegramNotModified:
            pass
    elif menu.get("options") and not menus.is_expired(menu):
        sent = _send_message(
            token,
            chat_id,
            text,
            topic_id,
            reply_markup=markup,
        )
        message_id = _coerce_optional_int(
            (sent.get("result") or {}).get("message_id")
        )
    if message_id is not None:
        _save_menu_render_state(
            brr_dir,
            render_thread,
            {
                "thread": thread,
                "menu_id": menu.get("menu_id"),
                "message_id": message_id,
                "chat_id": chat_id,
                "topic_id": topic_id,
            },
        )


def render_update(brr_dir: Path, packet: Any) -> None:
    """Send/edit a Telegram progress card for *packet*.

    On ``run_created`` we send a fresh message in the originating chat
    or topic and store the resulting ``message_id`` so later packets can
    edit the same message via ``editMessageText``. Failures are swallowed
    — the daemon must keep running even if Telegram is misconfigured.
    """
    ptype = getattr(packet, "type", None)
    if (
        ptype not in {"mirror_card", "menu_composed"}
        and ptype not in _RENDERABLE_PACKETS
    ):
        return

    state = _load_state(brr_dir)
    token = state.get("token")
    if not token:
        return

    if ptype == "menu_composed":
        _render_live_menu(brr_dir, str(token), packet)
        return

    if ptype == "mirror_card":
        _render_mirror_card(brr_dir, str(token), packet)
        return

    conv_key = getattr(packet, "conversation_key", "") or ""
    run_id = run_progress.run_id_from_packet(packet)
    if not conv_key or not run_id:
        return

    task = Run.from_file(run_manifest_path(brr_dir / "runs", run_id))
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

    text = _build_card_text(brr_dir, conv_key, run_id)
    if text is None:
        return

    transport = _CardTransport(token, chat_id, topic_id)
    delivery.update_card(
        brr_dir, "telegram", run_id, text,
        transport=transport, reply_to=reply_to, render_tag=ptype,
    )


def _render_mirror_card(brr_dir: Path, token: str, packet: Any) -> None:
    """Render the correspondent-thread stub for a ``mirror_card`` packet.

    A run's real card lives in its *origin* thread; this stub sits under a
    waiting correspondent's own message so the chat whose message is being
    actively worked never looks silent (#341). The daemon emits one packet
    per foreign pending chat event (``_emit_mirror_cards``); the card state
    is keyed per (run, event) so several folded-in messages each keep their
    own stub, edited in place through the same shared card driver.
    """
    payload = getattr(packet, "payload", None) or {}
    if str(payload.get("source") or "") != "telegram":
        return
    meta = payload.get("event_meta") or {}
    if not isinstance(meta, dict):
        return
    chat_id = _coerce_optional_int(meta.get("telegram_chat_id"))
    if chat_id is None:
        return
    topic_id = _coerce_optional_int(meta.get("telegram_topic_id"))
    # Thread the stub under the correspondent's own waiting message.
    reply_to = _coerce_optional_int(meta.get("telegram_message_id"))
    run_id = run_progress.run_id_from_packet(packet) or ""
    event_id = getattr(packet, "event_id", "") or ""
    if not run_id or not event_id:
        return
    status = str(payload.get("status") or "active")
    if status == "answered":
        text = "✅ folded into the running thought — answered"
    elif status == "queued":
        text = "⏸ still queued — the next thought picks this up"
    else:
        text = "⏳ folded into a running thought"
        narration = str(payload.get("agent_card_text") or "").strip()
        if narration:
            text += f"\n<i>{_escape_html(narration)}</i>"
    transport = _CardTransport(token, chat_id, topic_id)
    delivery.update_card(
        brr_dir, "telegram", f"{run_id}.mirror.{event_id}", text,
        transport=transport, reply_to=reply_to,
        render_tag=f"mirror:{status}",
    )
