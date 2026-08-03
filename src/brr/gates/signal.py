"""Signal gate — polls a self-hosted signal-cli-rest-api, delivers responses.

Speaks to **bbernhard/signal-cli-rest-api** (a REST wrapper around
``signal-cli``) rather than to Signal's own servers directly — the operator
runs that container and either links it as a secondary device or registers
a dedicated number, same posture as this repo takes toward Telegram/Slack
bot credentials.

Credentials and runtime state live in ``.brr/gates/signal.json``.

**v1 is direct messages only** — no groups, no attachments, no live
progress card. See ``docs/src/content/docs/concepts/gates.md`` for the
full list of cuts and why.

Required setup:
- Run a signal-cli-rest-api container (``json-rpc`` mode recommended —
  ``ready``/``normal`` modes work too, just without server-side long-poll)
  and either link it as a secondary device or register a dedicated number.
- Run ``brnrd gate setup signal`` to save the API URL, the gate's own
  number, and the paired principal.
- Message the gate's number from the paired principal to start a run.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import requests

from .. import protocol, trust
from . import delivery, runtime

_POLL_INTERVAL = 5
# Passed as a query param to /v1/receive; a json-rpc-mode server holds the
# request open up to this long, giving Signal the same "cheap when idle"
# shape as Telegram's getUpdates long-poll. A normal/ready-mode server
# ignores the param and returns immediately, in which case _POLL_INTERVAL
# above is what keeps this from hammering the container.
_RECEIVE_TIMEOUT_S = 20
_DELIVERY_INTERVAL = 1.0
_MAX_SIGNAL_LEN = 4000
# Bounds the dedupe window (see _loop_once) — a retry aid against the API
# occasionally re-delivering an envelope it already handed us, not an
# archive of everything ever seen.
_SEEN_LIMIT = 500

# One Session for the gate's own HTTP calls: keep-alive reuses the
# connection across polls instead of dialing fresh each time (see
# telegram.py / slack.py for the same reasoning).
_SESSION = requests.Session()


def _coerce_sender(value: object) -> str:
    """Recover an E.164 sender string after an event file round-trip.

    ``protocol``'s frontmatter parser (``_coerce``) opportunistically runs
    every scalar through ``int()`` and keeps the result when it succeeds —
    right for telegram's numeric chat/user ids, but ``int("+15551111111")``
    succeeds too and silently drops the leading ``+``, the one character
    every E.164 number starts with. A value read straight back from a live
    dict (state JSON, a same-call variable) never hits this; only a value
    read from an event *file* via ``protocol.list_pending``/``list_active``
    does — which is exactly where delivery reads ``signal_sender`` from.
    Recompose the ``+`` for anything that came back as a bare int; a
    non-numeric value (already a string) passes through unchanged.
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return f"+{value}"
    return str(value or "").strip()


def _sanitize_meta_str(value: str) -> str:
    """Flatten newlines in a sender-controlled string before it enters frontmatter.

    A Signal profile name is sender-controlled and may contain embedded
    newlines that would otherwise forge extra frontmatter fields via
    ``create_event``'s meta injection path (mirrors telegram.py's guard,
    #413 §7 S3) — flatten here, before the seam call, so the seam's
    ValueError guard is never reached from live traffic.
    """
    return value.replace("\r", " ").replace("\n", " ")


# ── signal-cli-rest-api helpers ─────────────────────────────────────


def _api_get(api_url: str, path: str, *, params: dict | None = None) -> Any:
    response = _SESSION.get(f"{api_url}{path}", params=params, timeout=_RECEIVE_TIMEOUT_S + 15)
    response.raise_for_status()
    if not response.content:
        return None
    return response.json()


def _api_post(api_url: str, path: str, payload: dict) -> Any:
    response = _SESSION.post(f"{api_url}{path}", json=payload, timeout=30)
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


# ── State ────────────────────────────────────────────────────────────


def _load_state(brr_dir: Path) -> dict:
    return runtime.load_state(brr_dir, "signal")


def _save_state(brr_dir: Path, state: dict) -> None:
    runtime.save_state(brr_dir, "signal", state)


# ── Interactive setup ────────────────────────────────────────────────


def auth(brr_dir: Path) -> None:
    state = _load_state(brr_dir)
    api_url = input(
        "signal-cli-rest-api URL (e.g. http://127.0.0.1:8080): "
    ).strip().rstrip("/")
    if not api_url:
        print("[brnrd] No API URL provided.")
        return
    number = input(
        "This gate's Signal number, E.164 (e.g. +15551234567): "
    ).strip()
    if not number:
        print("[brnrd] No number provided.")
        return
    try:
        _api_get(api_url, "/v1/about")
        print("[brnrd] signal-cli-rest-api reachable.")
    except Exception as e:
        print(f"[brnrd] Could not reach signal-cli-rest-api: {e}")
        return
    state["api_url"] = api_url
    state["number"] = number
    _save_state(brr_dir, state)
    print("[brnrd] API URL and number saved.")


def bind(brr_dir: Path) -> None:
    """Bind the paired principal (#409): every inbound message is checked
    against ``state["paired_sender"]`` (this prompt) or ``state["allowlist"]``
    (edited directly in ``.brr/gates/signal.json``, a JSON list of E.164
    numbers) before it becomes an event — default-closed, same policy as
    ``telegram.py``'s ``_sender_tier``.
    """
    state = _load_state(brr_dir)
    if "api_url" not in state or "number" not in state:
        print("[brnrd] Run `brnrd gate auth signal` first.")
        return
    sender = input(
        "Your Signal number, to authorize as the paired principal "
        "(required, E.164 — messages from anyone else are rejected): "
    ).strip()
    if not sender:
        print("[brnrd] A number is required so brr knows who to trust.")
        return
    try:
        _api_post(state["api_url"], "/v2/send", {
            "message": "brnrd bound.",
            "number": state["number"],
            "recipients": [sender],
        })
        print("[brnrd] Test message sent.")
    except Exception as e:
        print(f"[brnrd] Failed: {e}")
        return
    state["paired_sender"] = sender
    _save_state(brr_dir, state)
    print("[brnrd] Binding saved")


def setup(brr_dir: Path) -> None:
    """Configure Signal credentials and the paired principal in one flow."""
    auth(brr_dir)
    if "api_url" in _load_state(brr_dir):
        bind(brr_dir)


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return all(state.get(key) for key in ("api_url", "number"))


# ── Authorization (#409) ─────────────────────────────────────────────


def _sender_tier(state: dict, sender: str | None) -> str | None:
    """The verified sender's trust tier, or ``None`` if denied.

    Default-closed, mirroring ``telegram.py``'s ``_sender_tier`` exactly:
    the sender must be the bound principal (``state['paired_sender']``) or
    listed in ``state['allowlist']``. No sender at all is never authorized.
    The bound principal is the operator -> ``owner`` tier; an
    allowlisted-but-not-bound sender is a known collaborator ->
    ``collaborator`` tier.
    """
    if not sender:
        return None
    paired = state.get("paired_sender")
    if paired is not None and str(paired) == sender:
        return trust.OWNER
    for allowed in state.get("allowlist") or []:
        if str(allowed) == sender:
            return trust.COLLABORATOR
    return None


def _authorized_sender(state: dict, sender: str | None) -> bool:
    """Back-compat boolean wrapper over :func:`_sender_tier`."""
    return _sender_tier(state, sender) is not None


# ── Gate loop ────────────────────────────────────────────────────────


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    """Main gate loop — poll messages, create events, deliver responses.

    Split like ``telegram.py``: ``/v1/receive`` can hold a request open up
    to ``_RECEIVE_TIMEOUT_S`` on a json-rpc-mode server, so letting it own
    response delivery too would make a folded-in reply wait behind the
    poll. The outbound loop scans local response queues on its own short
    cadence and only calls out to signal-cli-rest-api when there is
    something to send.
    """
    threading.Thread(
        target=runtime.run_loop,
        args=(lambda: _delivery_loop_once(brr_dir, inbox_dir, responses_dir),),
        kwargs={
            "label": "signal-delivery",
            "poll_interval": _DELIVERY_INTERVAL,
        },
        daemon=True,
        name="gate-signal-delivery",
    ).start()
    runtime.run_loop(
        lambda: _loop_once(brr_dir, inbox_dir, responses_dir),
        label="signal",
        poll_interval=_POLL_INTERVAL,
        brr_dir=brr_dir,
        gate="signal",
    )


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    api_url = state["api_url"]
    number = state["number"]

    envelopes = _api_get(
        api_url, f"/v1/receive/{number}", params={"timeout": _RECEIVE_TIMEOUT_S},
    )

    seen = list(state.get("seen") or [])
    seen_set = set(seen)
    seen_changed = False

    for item in envelopes if isinstance(envelopes, list) else []:
        envelope = item.get("envelope") if isinstance(item, dict) else None
        if not isinstance(envelope, dict):
            continue
        data_message = envelope.get("dataMessage")
        if not isinstance(data_message, dict):
            # Receipts, typing indicators, sync/reaction messages — none of
            # these are an inbound trigger.
            continue
        if data_message.get("groupInfo") is not None:
            # v1 cut: this is the *direct* gate. A group envelope is
            # recognised and skipped rather than silently mis-addressed as
            # a DM from the group itself.
            continue
        text = str(data_message.get("message") or "").strip()
        if not text:
            continue
        sender = str(envelope.get("sourceNumber") or envelope.get("source") or "").strip()
        if not sender:
            continue
        timestamp = envelope.get("timestamp")
        if timestamp is None:
            timestamp = data_message.get("timestamp")

        # Dedupe on sender+timestamp against gate state (mirrors telegram's
        # offset tracking) — the API is expected to hand back only new
        # messages each poll, but an occasional re-delivery on retry/error
        # must not become a second event.
        dedupe_key = f"{sender}:{timestamp}"
        if dedupe_key in seen_set:
            continue
        seen_set.add(dedupe_key)
        seen.append(dedupe_key)
        seen_changed = True

        sender_tier = _sender_tier(state, sender)
        if sender_tier is None:
            # #409 default-closed audit trail — no reply is sent, so an
            # unauthorized sender can't probe for a valid principal.
            print(f"[brnrd] signal authz denied: sender={sender}")
            continue

        # Delivery fallback for self-originated events (schedule ticks),
        # same role as telegram's last_chat_id — but recorded only for an
        # *authorized* sender. Signal has no group/channel concept standing
        # between a stranger and the operator's inbox the way a shared
        # Telegram chat does, so letting an unauthorized number set the
        # fallback would let it redirect a self-originated reply to itself.
        state["last_recipient"] = sender

        protocol.create_event(
            inbox_dir,
            source="signal",
            body=text,
            signal_sender=sender,
            signal_sender_name=_sanitize_meta_str(str(envelope.get("sourceName") or "")),
            signal_group_id="",
            signal_timestamp=timestamp if timestamp is not None else "",
            trust_tier=sender_tier,
        )

    if seen_changed:
        state["seen"] = seen[-_SEEN_LIMIT:]
    _save_state(brr_dir, state)


def _delivery_loop_once(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
) -> None:
    state = _load_state(brr_dir)
    _deliver_responses(
        brr_dir,
        inbox_dir,
        responses_dir,
        state["api_url"],
        state["number"],
        state.get("last_recipient"),
    )


def _deliver_responses(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
    api_url: str,
    number: str,
    default_recipient: str | None = None,
) -> None:
    overflow_cache = delivery.OverflowCache(brr_dir, "signal")

    def deliver(event: dict, body: str) -> dict:
        recipient = _coerce_sender(event.get("signal_sender")) or str(
            default_recipient or ""
        ).strip()
        if not recipient:
            raise runtime.PermanentDeliveryError(
                "the event carries no signal sender and this gate has no "
                "default recipient configured"
            )
        text = delivery.resolve_overflow(
            body, limit=_MAX_SIGNAL_LEN, gist_fn=delivery.post_gist,
            cache=overflow_cache,
        )
        return _api_post(api_url, "/v2/send", {
            "message": text,
            "number": number,
            "recipients": [recipient],
        })

    runtime.deliver_stream(
        inbox_dir, responses_dir, "signal", deliver, brr_dir=brr_dir,
    )
