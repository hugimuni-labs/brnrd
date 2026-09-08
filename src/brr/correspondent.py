"""The correspondent's own presence — the other half of the card.

``design-the-continuous-seat.md`` §Presence. The seat can be reached but
not interrupted, and it never sees the person on the other side. This
module owns the daemon half of closing that: what brnrd can know about
the correspondent *without* asking a platform for something a bot is
never given.

Two facts, one derived and one declared:

- **quiet** — how long since the correspondent last said anything, derived
  from the run's own inbox (``now - the newest inbound correspondent
  event's ``created```). No platform involvement at all: this is brnrd
  reading its own mail. Strand completions, schedule firings and spawn
  bookkeeping are *not* the correspondent talking, so they do not reset
  it — the filter is :func:`brr.conversations.correspondent_key_for_event`
  returning this thread's own key, which is exactly the "who is talking"
  question that function exists to answer.
- **mode** — ``live`` | ``afk`` | ``quiet`` | ``urgent-only``, declared by
  the person through the relay's chat commands (``/afk`` ``/hush``
  ``/urgent-only`` ``/back``) and mirrored onto disk as a per-thread
  record. The relay half writes it; this module only reads.

``read`` (a read receipt for the bot's own message) is platform-given and
stays ``None`` here: verified 2026-09-09, a Telegram bot receives no read
status and no typing — it can only *send* the latter. WhatsApp Business
does give it, so the field exists and the relay fills it where the
platform has it. It is never inferred.

**Where the record lives, and why not where the spec said.** The design
slice named ``.brr/presence/<thread-key>.json``. That directory is the
*run* presence registry (:mod:`brr.presence`), whose ``list_active()``
iterates every ``*.json`` in it, parses each as a live-run entry, and
``unlink()``s the ones that fail or read stale — prune-on-read, by
design, so whoever reads the registry cleans it. A correspondent record
written there is deleted by the next dashboard publish tick. So the
record lives one level down, in ``presence/correspondent/``: the same
place conceptually, and the registry's own ``suffix != ".json"`` filter
skips a directory without needing to learn about this file at all.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import conversations, protocol

#: The declared modes. ``live`` is the absence of a declaration, not a
#: fourth thing to write down — a missing record reads ``live``.
MODES = ("live", "afk", "quiet", "urgent-only")

#: The modes under which an interim chat line is held rather than sent.
#: ``urgent-only`` is deliberately not here: it holds too, but with an
#: escape hatch (see :func:`delivery_verdict`), so the two questions stay
#: separate.
HUSHED_MODES = frozenset({"quiet", "afk"})

#: Directory (under the run's outbox) where a held interim line waits for
#: ``/back`` or the ``until``. Named in ``portal-state.outbound`` so the
#: resident can see what is being held rather than wondering why a line
#: it wrote never landed.
HELD_DIRNAME = ".held"

#: The one-line prefix that carries a message through ``urgent-only``.
URGENT_PREFIX = "urgent:"


# ── The record on disk ───────────────────────────────────────────────


def presence_dir(brr_dir: Path) -> Path:
    """Where per-thread correspondent records live (see module docstring)."""
    return Path(brr_dir) / "presence" / "correspondent"


def record_path(brr_dir: Path, thread_key: str) -> Path:
    """The record for one gate thread.

    Reuses :func:`brr.conversations.safe_dir_name` for the filename so a
    thread key renders identically here and in the conversation store —
    one encoding, not a second one to keep in sync.
    """
    return presence_dir(brr_dir) / f"{conversations.safe_dir_name(thread_key)}.json"


def write_record(
    brr_dir: Path, thread_key: str, mode: str, until: str | None = None
) -> Path:
    """Write one thread's presence record (the relay half's writer).

    Present here so the daemon-side lane is testable end to end now, and
    so both halves agree on the shape by sharing the function rather than
    by both remembering it.
    """
    path = record_path(brr_dir, thread_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"mode": mode}
    if until:
        payload["until"] = until
    protocol._atomic_write(path, json.dumps(payload, indent=2) + "\n")
    return path


def read_record(
    brr_dir: Path | None, thread_key: str | None, *, now: float | None = None
) -> dict[str, Any]:
    """Return ``{"mode": ..., "until": ...}`` for *thread_key*.

    Always answers — a missing, unreadable or unrecognised record reads
    ``{"mode": "live", "until": None}``. An expired ``until`` reads
    ``live`` too: the declaration carried its own end, and honouring it
    past its deadline would be the daemon holding the correspondent to
    something they already un-said.
    """
    blank: dict[str, Any] = {"mode": "live", "until": None}
    if not brr_dir or not thread_key:
        return blank
    try:
        raw = json.loads(
            record_path(brr_dir, thread_key).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return blank
    if not isinstance(raw, dict):
        return blank
    mode = str(raw.get("mode") or "").strip().lower()
    if mode not in MODES:
        return blank
    until = str(raw.get("until") or "").strip() or None
    if until and _expired(until, now):
        return blank
    return {"mode": mode, "until": until}


def _expired(until: str, now: float | None) -> bool:
    """True when *until* is a parseable instant already in the past.

    An unparseable ``until`` is never treated as expired: a hand-written
    ``09:00`` is a declaration the daemon cannot date, and guessing a date
    for it would silently end a quiet the person asked for. It rides as
    display text and the mode stands until ``/back``.
    """
    epoch = protocol.parse_iso_epoch(until)
    if epoch is None:
        return False
    return epoch <= (now if now is not None else time.time())


# ── Quiet, derived from our own mail ─────────────────────────────────


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

    Every status counts. The question is "when did they last speak", and
    an event this run already answered is still something they said; only
    *who sent it* filters, via
    :func:`brr.conversations.correspondent_key_for_event`, which returns
    ``None`` for a schedule firing, a spawn completion or any other event
    brnrd minted for itself.
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


# ── The facet input ──────────────────────────────────────────────────


def facet_input(
    brr_dir: Path | None,
    inbox_dir: Path | None,
    *,
    thread_key: str | None,
    correspondent_key: str | None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Build :func:`brr.facets.build`'s ``correspondent`` input.

    ``None`` when this run has no chat thread at all (a schedule-woken
    run in a repo nobody messaged, a test harness) — which the facet
    renders ``absent``, the affirmative-empty answer, rather than
    claiming a live correspondent who does not exist.
    """
    if not thread_key:
        return None
    record = read_record(brr_dir, thread_key, now=now)
    return {
        "quiet_seconds": quiet_seconds(inbox_dir, correspondent_key, now=now),
        "mode": record["mode"],
        "until": record["until"],
        # Platform-given; the relay fills it where the platform has one.
        # A Telegram bot never receives read status, so on that lane this
        # stays None forever — an honest absence, not a pending feature.
        "read": None,
    }


# ── Delivery ─────────────────────────────────────────────────────────


def delivery_verdict(mode: str, body: str) -> str:
    """``"send"`` or ``"hold"`` for one interim chat line under *mode*.

    Only interim lines reach this: a reply to a pending event and a
    ``gate:`` escalation always go through, and that decision is the
    caller's (``daemon._drain_outbox``) because it is the caller that
    knows which kind of file it is holding.
    """
    mode = str(mode or "live").strip().lower()
    if mode in HUSHED_MODES:
        return "hold"
    if mode == "urgent-only":
        first = (body or "").lstrip().splitlines()[:1]
        if first and first[0].lstrip().lower().startswith(URGENT_PREFIX):
            return "send"
        return "hold"
    return "send"


def held_dir(outbox_dir: Path) -> Path:
    return Path(outbox_dir) / HELD_DIRNAME


def held_files(outbox_dir: Path | None) -> list[Path]:
    """Interim lines currently held for this run, oldest first."""
    if not outbox_dir:
        return []
    hdir = held_dir(outbox_dir)
    if not hdir.exists():
        return []
    try:
        return sorted(
            (p for p in hdir.iterdir() if p.is_file()),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )
    except OSError:
        return []


def hold_file(outbox_dir: Path, fpath: Path) -> Path | None:
    """Move a staged interim line into the held queue. Returns its new path.

    The file keeps its name and its mtime, so the release below can put it
    back in the order the resident wrote it — a queue that reorders a
    person's own sentences is worse than one that drops them.
    """
    hdir = held_dir(outbox_dir)
    try:
        hdir.mkdir(parents=True, exist_ok=True)
        stat = fpath.stat()
        target = hdir / fpath.name
        fpath.replace(target)
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return target
    except OSError:
        return None


def release_held(outbox_dir: Path | None) -> int:
    """Move every held line back into the outbox for the next drain pass.

    Called at the top of each drain: the release condition is simply that
    the mode no longer holds (``/back`` rewrote the record, or the
    ``until`` passed and :func:`read_record` now reads ``live``), so
    nothing here needs to know *which* of those happened.
    """
    files = held_files(outbox_dir)
    if not files or outbox_dir is None:
        return 0
    released = 0
    for path in files:
        try:
            stat = path.stat()
            target = Path(outbox_dir) / path.name
            if target.exists():
                # Name collision with a live staging file: leave it held
                # rather than clobber something the resident just wrote.
                continue
            path.replace(target)
            os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            released += 1
        except OSError:
            continue
    return released
