"""Shared delivery primitives for poll/deliver gates.

Two pieces every chat-style gate needs, lifted out so the OSS gates
(telegram, slack) and the managed ``cloud`` gate share one
implementation and differ only in their *transport*:

- ``resolve_overflow`` — the gist/truncate decision for an over-long
  final answer, so the body always fits one platform message. The
  daemon runs this (it owns ``gh``), never brnrd.
- ``update_card`` — the live progress-card lifecycle (send once, then
  edit in place, skipping no-op re-renders), driven through a
  ``CardTransport`` so the same logic backs a direct platform call or
  a relay to brnrd.

Card-state files are the per-task ones owned by ``runtime`` under
``.brr/gates/<gate>/progress/<task>.json``. See
``kb/design-managed-delivery.md`` for the one-driver / two-transports
shape this implements.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from . import runtime


# ── Overflow (final-answer offload) ──────────────────────────────────


def resolve_overflow(
    text: str,
    *,
    limit: int,
    gist_fn: Callable[[str], str | None],
) -> str:
    """Return platform-ready text that fits within *limit* characters.

    Within budget: the text unchanged. Over budget: offload to a gist
    (``gist_fn`` returns a URL or None) and return a short link; if the
    gist can't be created, return a hard-truncated body with a marker.
    The offload keeps large content on the user's own GitHub.
    """
    if len(text) <= limit:
        return text
    url = gist_fn(text)
    if url:
        return f"Result: {url}"
    return text[:limit] + "\n\n[truncated]"


# ── Progress-card lifecycle ──────────────────────────────────────────


class CardUnchanged(Exception):
    """The platform reports the card body is unchanged (edit is a no-op).

    A ``CardTransport.edit`` raises this when the platform's own
    not-modified check fires; ``update_card`` treats it as a successful
    no-op rather than re-sending. Any *other* exception from ``edit``
    means the message is gone, and ``update_card`` falls through to send
    a replacement.
    """


class CardTransport(Protocol):
    """How a card reaches its destination — the only per-gate variation.

    Direct transports call the platform API with the user's token; the
    cloud transport relays to brnrd, which posts with the managed token.
    Implementations own all platform formatting (parse mode, escaping,
    threading).
    """

    def send(self, text: str, *, reply_to: int | None = None) -> int | None:
        """Post a new card; return its platform message id (or None)."""
        ...

    def edit(self, message_id: int, text: str) -> None:
        """Edit the card in place; raise ``CardUnchanged`` on a no-op."""
        ...


def update_card(
    brr_dir: Path,
    gate: str,
    task_id: str,
    text: str,
    *,
    transport: CardTransport,
    reply_to: int | None = None,
    render_tag: str | None = None,
) -> None:
    """Send or edit the live progress card for *task_id*, idempotently.

    Skips the round-trip when the rendered text matches the last one.
    Edits the stored message when present, falling back to a fresh send
    if it has vanished. Transport failures are swallowed — a gate thread
    must keep running even if its platform is briefly unreachable.
    """
    entry = runtime.load_task_card(brr_dir, gate, task_id)

    if entry and entry.get("last_text") == text:
        # Identical to the last rendered message — nothing to send.
        if render_tag is not None:
            entry["last_render"] = render_tag
            runtime.save_task_card(brr_dir, gate, task_id, entry)
        return

    try:
        if entry and entry.get("message_id"):
            try:
                transport.edit(int(entry["message_id"]), text)
            except CardUnchanged:
                # Server-side check agrees the body didn't change; a
                # successful no-op, not a reason to send a duplicate.
                pass
            except Exception:
                # The message is gone (deleted, expired). Send anew.
                message_id = transport.send(text, reply_to=reply_to)
                if message_id is None:
                    return
                runtime.save_task_card(
                    brr_dir, gate, task_id,
                    _card_entry(message_id, text, render_tag),
                )
                return
            entry["last_text"] = text
            if render_tag is not None:
                entry["last_render"] = render_tag
            runtime.save_task_card(brr_dir, gate, task_id, entry)
            return

        message_id = transport.send(text, reply_to=reply_to)
        if message_id is None:
            return
        runtime.save_task_card(
            brr_dir, gate, task_id, _card_entry(message_id, text, render_tag),
        )
    except Exception:
        return


def _card_entry(message_id: object, text: str, render_tag: str | None) -> dict:
    entry: dict = {"message_id": message_id, "last_text": text}
    if render_tag is not None:
        entry["last_render"] = render_tag
    return entry
