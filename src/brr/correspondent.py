"""The correspondent's own presence — the other half of the card.

``design-the-continuous-seat.md`` §Presence. The seat can be reached but
not interrupted, and it never sees the person on the other side. This
module owns the daemon half of closing that, and it is deliberately small:
what brnrd can *measure* about the correspondent without asking a platform
for something a bot is never given, and nothing it would have to parse
from a person's words.

- **quiet** — how long since the correspondent last said anything: now
  minus the newest inbound correspondent event's ``created``. No platform
  involvement at all; this is brnrd reading its own mail. Strand
  completions, schedule firings and spawn bookkeeping are not the
  correspondent talking, so they do not reset it — the filter is
  :func:`brr.conversations.correspondent_key_for_event` returning this
  thread's own key, which is exactly the "who is talking" question that
  function exists to answer.
- **read** — a read receipt for our own last message. Platform-given and
  ``None`` here: verified 2026-09-09, a Telegram bot receives no read
  status and no typing (it can only *send* the latter). WhatsApp Business
  does give it, so the field exists and the relay fills it where the
  platform has it. It is never inferred.

Deliberately **not** here: a presence *mode*. An earlier cut of this slice
carried ``live``/``afk``/``quiet``/``urgent-only`` as a parsed record that
gated delivery. His call, 2026-09-09: no parsed presence modes anywhere.
What survives is the measurement; what a run does with it is judgement, not
a state machine.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import conversations, protocol


def quiet_seconds(
    inbox_dir: Path | None,
    correspondent_key: str | None,
    *,
    now: float | None = None,
) -> float | None:
    """Seconds since this correspondent's newest inbound event.

    ``None`` when the correspondent has no event in this inbox at all —
    absence of a reading, never a fabricated zero (a fresh inbox and "they
    just spoke" are not the same fact).

    Every status counts. The question is "when did they last speak", and an
    event this run already answered is still something they said; only *who
    sent it* filters.
    """
    if not inbox_dir or not correspondent_key:
        return None
    try:
        entries = list(Path(inbox_dir).iterdir())
    except OSError:
        return None
    newest: float | None = None
    for path in entries:
        if path.suffix != ".md":
            continue
        event = protocol._read_event(path)
        if not event:
            continue
        if conversations.correspondent_key_for_event(event) != correspondent_key:
            continue
        created = protocol._event_created_epoch(event)
        if created is None:
            continue
        if newest is None or created > newest:
            newest = created
    if newest is None:
        return None
    return max(0.0, (now if now is not None else time.time()) - newest)


def facet_input(
    inbox_dir: Path | None,
    *,
    thread_key: str | None,
    correspondent_key: str | None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Build :func:`brr.facets.build`'s ``correspondent`` input.

    ``None`` when this run has no chat thread at all (a schedule-woken run
    in a repo nobody messaged, a test harness) — which the facet renders
    ``absent``, the affirmative-empty answer, rather than claiming a
    correspondent who does not exist.
    """
    if not thread_key:
        return None
    return {
        "quiet_seconds": quiet_seconds(inbox_dir, correspondent_key, now=now),
        # Platform-given; the relay fills it where the platform has one. On
        # the Telegram lane this stays None forever — an honest absence,
        # not a pending feature.
        "read": None,
    }
