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

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Callable, Protocol

from ..channels.telegram import TRUNCATION_MARKER, trim_to_limit, utf16_len
from . import runtime


# ── Overflow (final-answer offload) ──────────────────────────────────
#
# The offload is the one step in the delivery path with a *side effect on
# another service*, and it used to run once per delivery attempt. On
# 2026-07-31 that cost 1,230 secret gists in nine hours: one over-long reply,
# a server answering 500 after it had already forwarded, and a retry loop with
# no memory. Two separate harms, and the second is the worse one:
#
# 1. every attempt minted a fresh gist, so a permanent failure billed the
#    user's GitHub account per poll;
# 2. every attempt therefore produced a *different body* — the gist URL is in
#    it — which silently defeated the server's own retry dedupe. brnrd's
#    ``inbox.record_response`` recognises a retried terminal post by
#    ``sha256(body)`` and quietly ACKs it; a body that changes every time can
#    never match, so a guard built after two prior delivery incidents was
#    unreachable for every reply that overflowed.
#
# The cache below is what makes the retry idempotent: the same input text
# resolves to the same gist URL, so attempt two is byte-identical to attempt
# one and the server's dedupe can do its job. It is keyed on the *pre-offload*
# text, which is the only stable identity the body has.


class OverflowCache:
    """Remember which gist an over-long body was already offloaded to.

    A tiny JSON map, ``sha256(text) -> url``, beside the gate's other state.
    Bounded to :attr:`LIMIT` newest entries because it is a retry aid, not an
    archive — the durable copy of a reply is the run's message store.

    Every operation is best-effort: a cache that cannot be read or written
    degrades to "mint a new gist", which is exactly the old behaviour and
    never blocks a delivery.
    """

    LIMIT = 64

    def __init__(self, brr_dir: Path, gate: str) -> None:
        self.path = brr_dir / "gates" / f"{gate}.overflow.json"

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def get(self, text: str) -> str | None:
        url = self._load().get(self.key(text))
        return url if isinstance(url, str) and url else None

    def put(self, text: str, url: str) -> None:
        entries = self._load()
        entries[self.key(text)] = url
        if len(entries) > self.LIMIT:
            # dict preserves insertion order; drop the oldest surplus
            for stale in list(entries)[: len(entries) - self.LIMIT]:
                entries.pop(stale, None)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(entries, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except OSError:
            return


def resolve_overflow(
    text: str,
    *,
    limit: int,
    gist_fn: Callable[[str], str | None],
    cache: OverflowCache | None = None,
) -> str:
    """Return platform-ready text that fits within *limit* UTF-16 units.

    Within budget: the text unchanged. Over budget: offload to a gist
    (``gist_fn`` returns a URL or None) and return a short link; if the
    gist can't be created, return a body trimmed at the last line or word
    boundary within budget (never mid-word) with a trailing marker — the
    *whole* returned string, marker included, still fits *limit*. The
    offload keeps large content on the user's own GitHub.

    *limit* is measured the way the chat platforms that call this (Telegram,
    WhatsApp) measure their own caps — UTF-16 code units, not Python
    ``len()`` — via :func:`brr.channels.telegram.utf16_len`; the trim
    helpers are named for their Telegram origin but are plain text-boundary
    logic, reused here so the truncate-fallback and the multi-message
    chunker in ``channels/telegram.py`` don't each grow their own idea of
    "cut cleanly" (#the-wire-that-cuts-at-4096).

    With a *cache*, a repeat call for the same text reuses the gist minted
    the first time — so a retried delivery posts an identical body instead of
    minting a gist and defeating the receiver's dedupe (see the module note).
    """
    if utf16_len(text) <= limit:
        return text
    if cache is not None:
        known = cache.get(text)
        if known:
            return f"Result: {known}"
    url = gist_fn(text)
    if url:
        if cache is not None:
            cache.put(text, url)
        return f"Result: {url}"
    return trim_to_limit(text, limit - utf16_len(TRUNCATION_MARKER)) + TRUNCATION_MARKER


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
    no-op rather than re-sending. See ``CardGone`` for the one *other*
    exception ``update_card`` treats specially — every remaining exception
    is a transport hiccup, not proof the message is gone.
    """


class CardGone(Exception):
    """The platform *confirms* the card message no longer exists.

    A ``CardTransport.edit`` raises this only when the platform positively
    reports the message is gone (deleted, expired, or — for the cloud
    transport — a server-mapped 409). ``update_card`` re-sends on this and
    only this. Before this type existed, *any* edit failure was treated as
    "message gone" and re-sent: on the night of 2026-08-15 a run of server
    502s turned every failed edit into a fresh status message, because a
    transport hiccup and an actually-deleted message rendered identically
    to the generic ``except Exception`` that used to sit here.
    """


class CardTransport(Protocol):
    """How a card reaches its destination — the only per-gate variation.

    Direct transports call the platform API with the user's token; the
    cloud transport relays to brnrd, which posts with the managed token.
    Implementations own all platform formatting (parse mode, escaping,
    threading).
    """

    def send(self, text: str, *, reply_to: int | None = None) -> int | str | None:
        """Post a new card; return its platform message id (or None)."""
        ...

    def edit(self, message_id: int | str, text: str) -> None:
        """Edit the card in place.

        Raise ``CardUnchanged`` on a no-op, ``CardGone`` when the platform
        confirms the message no longer exists. Any other exception is a
        transport hiccup — let it propagate; ``update_card`` keeps the
        stored message id and retries the edit on the next render.
        """
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
    Edits the stored message when present; re-sends only when the
    transport *confirms* it is gone (``CardGone``) — a generic transport
    failure (5xx, timeout, network blip) keeps the stored message id and
    retries the same edit on the next render instead of minting a
    duplicate. Transport failures are otherwise swallowed — a gate thread
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
                transport.edit(entry["message_id"], text)
            except CardUnchanged:
                # Server-side check agrees the body didn't change; a
                # successful no-op, not a reason to send a duplicate.
                pass
            except CardGone:
                # The platform confirms the message is actually gone —
                # the one case that should mint a replacement.
                message_id = transport.send(text, reply_to=reply_to)
                if message_id is None:
                    return
                runtime.save_run_card(
                    brr_dir, gate, run_id,
                    _card_entry(message_id, text, render_tag),
                )
                return
            except Exception:
                # A transport hiccup, not proof the message is gone. Keep
                # the stored message_id — the next render retries this
                # same edit instead of sending a duplicate status message.
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
