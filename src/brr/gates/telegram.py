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

import tempfile
import threading
from pathlib import Path
from typing import Any

import requests

from .. import protocol, run_progress
from ..run import Run, run_manifest_path
from . import delivery, runtime

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_TG_LEN = 3900
_POLL_TIMEOUT = 30
_DELIVERY_INTERVAL = 1.0

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
) -> dict:
    body = delivery.resolve_overflow(
        text, limit=_MAX_TG_LEN, gist_fn=delivery.post_gist
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
    photo = msg.get("photo")
    if isinstance(photo, list) and photo:
        largest = photo[-1]
        file_id = largest.get("file_id") if isinstance(largest, dict) else None
        if file_id:
            return str(file_id), "photo.jpg"
    document = msg.get("document")
    if isinstance(document, dict):
        mime = str(document.get("mime_type") or "")
        if mime.startswith("image/"):
            file_id = document.get("file_id")
            if file_id:
                return str(file_id), str(document.get("file_name") or "image")
    return None


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


def _authorized_sender(state: dict, user_id: int | None) -> bool:
    """#409 — default-closed: the verified sender must be the bound
    principal (``state['paired_user_id']``, set by ``bind``) or listed in
    ``state['allowlist']`` (a JSON array of Telegram user ids, edited
    directly in ``.brr/gates/telegram.json`` — no CLI setter yet). No
    sender id at all (``sender_chat`` / a missing ``from``) is never
    authorized, regardless of either list.
    """
    if user_id is None:
        return False
    paired = state.get("paired_user_id")
    if paired is not None:
        try:
            if int(paired) == int(user_id):
                return True
        except (TypeError, ValueError):
            pass
    for allowed in state.get("allowlist") or []:
        try:
            if int(allowed) == int(user_id):
                return True
        except (TypeError, ValueError):
            continue
    return False


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
    }, poll=True).get("result", [])

    for update in updates:
        offset = update["update_id"] + 1
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
        migrate_to = msg.get("migrate_to_chat_id")
        migrate_from = msg.get("migrate_from_chat_id")
        if migrate_to is not None or migrate_from is not None:
            old_id = chat_id if migrate_to is not None else migrate_from
            new_id = migrate_to if migrate_to is not None else chat_id
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
        # A caption rides on a photo/document message where ``text``
        # never appears; either one is the event body. An image with no
        # caption at all still becomes an event (empty body, the image
        # carries the content) — only a message with neither is skipped.
        text = str(msg.get("text") or msg.get("caption") or "").strip()
        image = _pick_image_file_id(msg)
        if not text and not image:
            continue

        sender = msg.get("from") or {}
        user = sender.get("first_name", "?")
        user_id = sender.get("id")
        # #409 — an anonymous group admin or a channel post carries
        # ``sender_chat`` instead of a personal identity; even if Telegram
        # also populated ``from`` with a generic service account, treat it
        # as unattributable (default-closed) rather than authorizing off
        # a shared/spoofable id. The sender is always this verified
        # ``from.id`` — never text parsed from the message, and never a
        # forwarded message's origin (``forward_from``/``forward_origin``),
        # which is deliberately never read for identity.
        if msg.get("sender_chat") is not None:
            user_id = None
        username = sender.get("username") or ""
        message_id = msg.get("message_id")
        # Telegram's own send-time (`date`, Unix epoch seconds) — captured
        # separately from the event's ingestion-time id. A burst sent while
        # the daemon was offline lands in one getUpdates batch with
        # near-identical ingestion timestamps, which reads as
        # misfiring/reordering unless something carries the real send time
        # (2026-07-04 maintainer report, #53 comment 4883341517; dominion
        # pitfall "Telegram event-id timestamps are ingestion time, not send
        # time" only shallowly verified this, didn't yet capture the fix).
        sent_at = msg.get("date")

        if not _authorized_sender(state, user_id):
            # #409 — default-closed gate audit trail. No reply is sent:
            # telling an unauthorized sender why would let them probe for
            # a valid principal.
            print(f"[brnrd] telegram authz denied: chat={chat_id} user={user_id}")
            continue

        attachment_files: list[Path] = []
        image_tmpdir: tempfile.TemporaryDirectory | None = None
        if image is not None:
            file_id, suggested_name = image
            image_tmpdir = tempfile.TemporaryDirectory()
            dest = Path(image_tmpdir.name) / suggested_name
            if _download_telegram_file(token, file_id, dest):
                attachment_files.append(dest)

        protocol.create_event(
            inbox_dir,
            source="telegram",
            body=text,
            attachment_files=attachment_files or None,
            telegram_chat_id=chat_id,
            telegram_topic_id=topic_id or "",
            telegram_user=user,
            telegram_user_id=user_id if user_id is not None else "",
            telegram_username=username,
            telegram_message_id=message_id if message_id is not None else "",
            telegram_sent_at=sent_at if sent_at is not None else "",
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
    def deliver(event: dict, body: str) -> dict:
        chat_id = _event_int(event, "telegram_chat_id", default_chat_id)
        if chat_id is None:
            raise RuntimeError("missing chat id")
        topic_id = _event_int(event, "telegram_topic_id", default_topic_id)
        reply_to = _event_int(event, "telegram_message_id")
        return _send_with_overflow(
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


def render_update(brr_dir: Path, packet: Any) -> None:
    """Send/edit a Telegram progress card for *packet*.

    On ``run_created`` we send a fresh message in the originating chat
    or topic and store the resulting ``message_id`` so later packets can
    edit the same message via ``editMessageText``. Failures are swallowed
    — the daemon must keep running even if Telegram is misconfigured.
    """
    ptype = getattr(packet, "type", None)
    if ptype != "mirror_card" and ptype not in _RENDERABLE_PACKETS:
        return

    state = _load_state(brr_dir)
    token = state.get("token")
    if not token:
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
