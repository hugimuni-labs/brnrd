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

Card-state files are the per-run ones owned by ``runtime`` under
``.brr/gates/<gate>/progress/<run>.json``. See
``kb/design-managed-delivery.md`` for the one-driver / two-transports
shape this implements.
"""

from __future__ import annotations

import subprocess
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


def post_gist(content: str, filename: str = "result.md") -> str | None:
    """Create a gist from *content* via the user's own ``gh``; URL or None.

    Runs on the daemon (which holds the user's ``gh``), so large content
    stays on the user's GitHub and only a short link is relayed — brnrd
    never needs gist credentials (see ``kb/design-managed-delivery.md`` →
    "Why gists stay daemon-side"). Returns None if ``gh`` is unavailable
    or fails, leaving the caller to truncate.

    **Secret, not public.** This carries an agent's overflowed final answer —
    code, kb excerpts, whatever the run happened to be holding — and it used
    to pass ``--public``, which contradicts the data-minimization argument
    that section is written to defend (the diffense pack gist has always been
    secret; these two disagreed). A secret gist is unlisted, not private, so
    the chat link still resolves for anyone holding it; the only thing given
    up is being indexed on the user's public profile, which was never wanted.

    **Why this survives the reply archive** (2026-07-12, `knowledge.capture`).
    A run's terminal reply is now persisted into the knowledge repo and linked
    from its relics, which looks like it makes the gist redundant. It doesn't,
    for two reasons — the second is the load-bearing one:

    1. a gist URL must exist *at send time*; a knowledge-repo URL only exists
       after a push, so folding them together would put a ``git push`` on the
       latency path of every over-long reply;
    2. an install whose knowledge repo has **no forge remote** (the default —
       ``brnrd home link`` is an opt-in) has no archive to link *at all*. There,
       a gist is the only durable surface the overflow has. Pastebin-shaped and
       unglamorous, and correct until a more generic offload shape exists.
    """
    try:
        result = subprocess.run(
            ["gh", "gist", "create", "-f", filename, "-"],
            input=content, capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return result.stdout.strip()
    return None


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
    run_id: str,
    text: str,
    *,
    transport: CardTransport,
    reply_to: int | None = None,
    render_tag: str | None = None,
) -> None:
    """Send or edit the live progress card for *run_id*, idempotently.

    Skips the round-trip when the rendered text matches the last one.
    Edits the stored message when present, falling back to a fresh send
    if it has vanished. Transport failures are swallowed — a gate thread
    must keep running even if its platform is briefly unreachable.
    """
    entry = runtime.load_run_card(brr_dir, gate, run_id)

    if entry and entry.get("last_text") == text:
        # Identical to the last rendered message — nothing to send.
        if render_tag is not None:
            entry["last_render"] = render_tag
            runtime.save_run_card(brr_dir, gate, run_id, entry)
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
                runtime.save_run_card(
                    brr_dir, gate, run_id,
                    _card_entry(message_id, text, render_tag),
                )
                return
            entry["last_text"] = text
            if render_tag is not None:
                entry["last_render"] = render_tag
            runtime.save_run_card(brr_dir, gate, run_id, entry)
            return

        message_id = transport.send(text, reply_to=reply_to)
        if message_id is None:
            return
        runtime.save_run_card(
            brr_dir, gate, run_id, _card_entry(message_id, text, render_tag),
        )
    except Exception:
        return


def _card_entry(message_id: object, text: str, render_tag: str | None) -> dict:
    entry: dict = {"message_id": message_id, "last_text": text}
    if render_tag is not None:
        entry["last_render"] = render_tag
    return entry
