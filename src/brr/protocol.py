"""Protocol — event and response file CRUD for the ``.brr/`` filesystem API.

All writes use atomic temp-file-then-rename to prevent races between
gate threads and the daemon main thread.  Reads silently skip files
that fail to parse (transient state during rename).

An inbox event's ``status:`` field belongs to exactly one state machine:
the letter's own lifecycle (``pending`` -> ``processing`` -> ``done`` ->
``delivered``/``noted`` — see :data:`LETTER_STATUSES`). A run's *outcome*
(``run.py``'s ``STATUSES`` plus the daemon's ``stopped`` result) is a
different machine and is not a letter state; ``daemon.py`` records it in
the event's own ``run_outcome:`` key instead of writing it here (see
``daemon.py``'s ``_set_event_run_outcome``). Event files written before
this split still carry ``error``/``conflict``/``stopped``/``cancelled`` in
``status`` — they stay readable and terminal (:data:`TERMINAL_EVENT_STATUSES`
below still names them so retention keeps collecting them) but nothing
writes those values into ``status`` anymore.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
import random
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Inbox wake signal ────────────────────────────────────────────────

# A process-local edge-trigger the daemon loop waits on so a fresh
# in-process event (a gate thread enqueuing a message, a self-scheduled
# thought firing) is picked up promptly instead of sleeping out a full
# poll tick. ``create_event`` sets it whenever it writes a ``pending``
# event in this process; cross-process writers (the ``brnrd run`` CLI) can't
# reach it, so the daemon's periodic poll stays the backstop for those.
_inbox_wake = threading.Event()


def inbox_wake() -> threading.Event:
    """Return the process-local inbox wake signal (see module note)."""
    return _inbox_wake


# ── Frontmatter parsing ─────────────────────────────────────────────


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse YAML-like frontmatter (``---`` delimited) into a flat dict.

    Handles the restricted subset used by brr: simple ``key: value``
    pairs and one level of nesting (for runners.md profiles).
    No dependency on pyyaml.
    """
    m = re.match(r"^---\n(.*?\n)---", text, re.DOTALL)
    if not m:
        return {}
    lines = m.group(1).splitlines()
    return _parse_block(lines, 0)[0]


def frontmatter_body(text: str) -> str:
    """Return the body text after the frontmatter."""
    m = re.match(r"^---\n.*?\n---\n?", text, re.DOTALL)
    if m:
        return text[m.end():]
    return text


# Routing selectors that may lead an outbox message's frontmatter. Used
# only to gate the lenient (missing-opening-fence) parse below — see
# ``parse_outbox_message``.
_OUTBOX_ROUTING_KEYS = (
    "event", "gate", "respawn", "spawn", "stop", "to", "runner_policy",
    "config_change", "note", "await", "cut",
)


def is_outbox_routing_selector_line(line: str) -> bool:
    """Whether *line* is the exact selector shape the lenient parser accepts."""
    stripped = line.strip()
    if ":" not in stripped:
        return False
    key, value = (part.strip() for part in stripped.split(":", 1))
    return (
        key in _OUTBOX_ROUTING_KEYS
        and bool(value)
        and re.fullmatch(r"\S+", value) is not None
    )


_CODE_FENCE_RE = re.compile(r"^(`{3,}|~{3,})[ \t]*[\w.+-]*[ \t]*$")


def _strip_leading_code_fence(text: str) -> str:
    """Unwrap a *leading* Markdown code fence around a routing block.

    Every doc page renders outbox directives inside a code fence, so a
    resident reproducing the rendered shape writes exactly that — and
    both the canonical and lenient parsers read it as prose, delivering
    the raw directive to a human instead of arming it (live misfire,
    2026-08-12: a full ``spawn:`` spec shipped to the maintainer's chat).

    Engages only when the message *opens* with a fence line and the
    first non-empty line inside is routing — a ``---`` frontmatter
    opener, or a recognised selector whose value is a single bare token
    (the same guard the lenient path applies). A fenced block quoted
    mid-message, a fenced code sample, or an unclosed fence is left
    untouched. On a match, both fence lines are removed and the result
    is handed to the normal two-shape parse below.
    """
    lines = text.splitlines(keepends=True)
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return text
    fence_match = _CODE_FENCE_RE.match(lines[idx].strip())
    if not fence_match:
        return text
    fence = fence_match.group(1)
    j = idx + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j >= len(lines):
        return text
    inner = lines[j].strip()
    if inner != "---":
        if not is_outbox_routing_selector_line(inner):
            return text
    for k in range(j + 1, len(lines)):
        closing = lines[k].strip()
        if (closing and set(closing) == {fence[0]}
                and len(closing) >= len(fence)):
            return "".join(lines[idx + 1:k]) + "".join(lines[k + 1:])
    return text


def parse_outbox_message(text: str) -> tuple[dict[str, Any], str]:
    """Parse an outbox message's routing frontmatter and body, tolerantly.

    Returns ``(meta, body)``. Accepts three shapes:

    - **Canonical** — a ``---``-fenced frontmatter block, exactly as
      :func:`parse_frontmatter` / :func:`frontmatter_body` handle it.
    - **Lenient** — a leading ``key: value`` block with *no opening
      fence*, ended by a ``---`` line, a blank line, or the first
      non-``key: value`` line: e.g. ``event: <id>\\n---\\nbody``,
      ``event: <id>\\n\\nbody``, or ``spawn: true\\n# Task``.
    - **Code-fenced** — either of the above wrapped in a leading
      Markdown code fence, the way every doc page renders these
      directives (see :func:`_strip_leading_code_fence`).

    The lenient shape exists because the resident reaches for it
    naturally — the delivery contract names ``event:`` / ``gate:`` /
    ``respawn:`` as
    "frontmatter" without showing the fences, and writing the selector
    line then a separator reads as obviously correct. Under the strict
    parser that silently failed: the routing was dropped, the literal
    ``event:`` line leaked into the delivered message, and the reply
    attached to the run's *lead* event instead of its target (the
    "messed-up quotes" failure). The original lenient parse accepted only
    a ``---`` terminator — and a blank-line-terminated block (the most
    natural Markdown-adjacent shape of all) degraded exactly the same
    silent way (found live, 2026-07-18). Tolerating these moves the
    lesson off the "remember the exact fences" rung of the robustness
    ladder.

    To avoid mistaking a plain message for routing, the lenient path
    engages **only** when the first non-empty line is a recognised
    routing selector (``event:`` / ``gate:`` / ``respawn:`` /
    ``spawn:`` / ``stop:`` / ``to:`` / ``runner_policy:``) *and* its
    value is a single bare token (``evt-…``, ``true``, a gate name).
    Prose that happens to open with ``event: the meeting is moved``
    fails the token test and stays a plain body; a normal message that
    merely contains ``---`` dividers (a PLAN, say) is never touched.
    Misparses still degrade safely: the drain drops an unknown
    ``event:`` target or unconfigured ``gate:`` with a notice rather
    than misdelivering.
    """
    text = _strip_leading_code_fence(text)
    if text.startswith("---\n"):
        return parse_frontmatter(text), frontmatter_body(text)

    lines = text.splitlines(keepends=True)
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return {}, text

    first = lines[idx].strip()
    if not is_outbox_routing_selector_line(first):
        # ``event: the meeting is moved`` — a routing key leading prose,
        # not a routing selector. Leave the message intact.
        return {}, text

    block: list[str] = []
    j = idx
    while j < len(lines):
        stripped = lines[j].strip()
        if stripped == "---":
            meta = _parse_block(block, 0)[0] if block else {}
            body = "".join(lines[j + 1:])
            return meta, body
        if stripped == "":
            # Blank line ends the key-block. A ``---`` fence directly
            # below (blank lines between) still counts as the terminator
            # so the fence line never leaks into the body.
            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k < len(lines) and lines[k].strip() == "---":
                j = k
                continue
            meta = _parse_block(block, 0)[0] if block else {}
            body = "".join(lines[k:])
            return meta, body
        if ":" in stripped and not stripped.startswith("#"):
            block.append(lines[j].rstrip("\n"))
            j += 1
            continue
        # First non-``key: value`` line (a heading, prose) ends the
        # block: the validated leading selector already settled intent.
        meta = _parse_block(block, 0)[0] if block else {}
        body = "".join(lines[j:])
        return meta, body
    meta = _parse_block(block, 0)[0] if block else {}
    return meta, ""


def _parse_block(lines: list[str], base_indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = len(line) - len(stripped)
        if indent < base_indent:
            break
        if ":" not in stripped:
            i += 1
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if val:
            result[key] = _coerce(val)
            i += 1
        else:
            child_indent = indent + 2
            if i + 1 < len(lines):
                next_stripped = lines[i + 1].lstrip()
                next_indent = len(lines[i + 1]) - len(next_stripped)
                if next_indent >= child_indent and next_stripped:
                    child, consumed = _parse_block(lines[i + 1:], child_indent)
                    result[key] = child
                    i += 1 + consumed
                    continue
            result[key] = ""
            i += 1
    return result, i


def _coerce(val: str) -> Any:
    if val in ("true", "True"):
        return True
    if val in ("false", "False"):
        return False
    if val in ("null", "None", "~"):
        return None
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    try:
        return int(val)
    except ValueError:
        return val


# ── Atomic file I/O ──────────────────────────────────────────────────


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via temp + rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.rename(tmp, path)
    except BaseException:
        os.close(fd) if not os.get_inheritable(fd) else None
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _format_meta_value(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value)


# Keys that ``create_event`` itself writes — a caller supplying one would
# produce a duplicate frontmatter line whose parse-time precedence is
# undefined.  ``trust_tier`` is deliberately *not* in this set: gates and
# the spawn/respawn inheritance path legitimately pass it, and the newline
# rule below is what closes the value-injection route.
_RESERVED_META_KEYS: frozenset[str] = frozenset(
    {"id", "source", "status", "created", "attachments"}
)

# Plain identifier: starts with a letter or underscore, followed by
# letters, digits, or underscores.  All existing meta keys match this.
_SAFE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_meta(meta: dict[str, object]) -> None:
    """Raise ``ValueError`` if *meta* contains an unsafe key or value.

    Guards the frontmatter injection path: a value containing ``\\n`` or
    ``\\r`` emits extra lines that ``parse_frontmatter`` reads as real
    fields — enough to forge ``trust_tier: owner`` through an unrelated
    key.  A caller-supplied reserved key would duplicate a field
    ``create_event`` already writes, with undefined parse precedence.

    This is a programming-error guard, not a sanitizer.  Gates that
    accept sender-controlled strings (Telegram display names, etc.) must
    flatten newlines *before* the call so this raise is never reached
    from live traffic.
    """
    for k, v in meta.items():
        k_str = str(k)
        if not _SAFE_KEY_RE.match(k_str):
            raise ValueError(
                f"create_event: meta key {k!r} is not a valid frontmatter "
                "identifier (must match [A-Za-z_][A-Za-z0-9_]*)"
            )
        if k_str in _RESERVED_META_KEYS:
            raise ValueError(
                f"create_event: meta key {k!r} is structurally reserved "
                "(create_event already writes it)"
            )
        sv = str(v)
        if "\n" in sv or "\r" in sv:
            raise ValueError(
                f"create_event: meta key {k!r} value contains a newline; "
                "newlines in frontmatter values forge additional fields"
            )


def _generate_id() -> str:
    ts = time.time_ns()
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"evt-{ts}-{rand}"


# ── Event sources ────────────────────────────────────────────────────

#: Sources brnrd mints for **itself** — no ingress path can produce one,
#: so no stranger can forge one. Every event whose ``source`` is in this
#: set was written by brnrd's own code on the operator's machine: a
#: schedule firing, a CLI invocation, a respawn/spawn dispatch, a child's
#: completion note back to its parent, a steer to a child, a bench
#: scenario, an install wake.
#:
#: This lives here, next to :func:`create_event`, because *minting is the
#: property*: the only way onto this list is to write a ``create_event``
#: call, and ``tests/test_trust.py::test_every_minted_source_is_declared``
#: AST-parses this package for exactly those calls and fails on a source
#: string that is neither declared here nor owned by a gate. The previous
#: spelling was a hand-list in ``trust.py`` that named five of the eight
#: (#1118): ``spawn`` was owner and ``spawn_completed`` — the *same*
#: dispatch edge, walked backwards — was a stranger, so a parent woken to
#: collect its own child's result was jailed in ``solitary`` with no
#: forge egress and no credential for a substituted Shell.
INTERNAL_SOURCES: frozenset[str] = frozenset({
    "bench",
    "cli",
    "dispatch_message",
    "init",
    "respawn",
    "schedule",
    "spawn",
    "spawn_completed",
})


# ── Event files ──────────────────────────────────────────────────────


def attachments_dir_for_event(inbox_dir: Path, event_id: str) -> Path:
    """Local directory holding *event_id*'s downloaded image attachments.

    Sibling to the event file itself (``<inbox_dir>/<event_id>.attachments/``
    next to ``<inbox_dir>/<event_id>.md``) rather than nested under a
    daemon-owned state dir — attachments live and die with the event that
    named them, so retention (``retention._plan_inbox_dir``) finds and
    collects them from the event path alone with no extra bookkeeping.
    """
    return inbox_dir / f"{event_id}.attachments"


def create_event(
    inbox_dir: Path,
    source: str,
    body: str,
    *,
    status: str = "pending",
    attachment_files: list[Path] | None = None,
    **meta: object,
) -> Path:
    """Create a new event file in *inbox_dir*. Returns the file path.

    *status* defaults to ``pending`` (a normal inbound event the daemon
    will wake on). Pass ``status="done"`` to inject an outbound-only event
    a gate delivers but the daemon never processes — the mechanism behind
    agent-initiated out-of-bound / scheduled delivery (the event is born
    ``done`` in one atomic write so the inbox poll can never grab it as
    pending and spawn a stray thought).

    *attachment_files*, when given, are local files the calling gate has
    already downloaded (Telegram's ``getFile``, GitHub's inline
    ``user-attachments`` image links — see ``gates/telegram.py`` and
    ``gates/github/attachments.py``). Each is moved (not copied — the
    caller's temp file is consumed) into
    ``attachments_dir_for_event(inbox_dir, event_id)`` and the event
    records their bare filenames as a comma-joined ``attachments:``
    frontmatter field, resolvable back to paths via
    :func:`event_attachment_paths`. Both gates converge on this one
    mechanism so an inbound screenshot reads the same way — a local file
    the resident's ``Read`` tool can open directly — regardless of which
    channel it arrived on.
    """
    _validate_meta(meta)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    eid = _generate_id()
    attachment_names: list[str] = []
    if attachment_files:
        adir = attachments_dir_for_event(inbox_dir, eid)
        adir.mkdir(parents=True, exist_ok=True)
        multiple = len(attachment_files) > 1
        for i, src in enumerate(attachment_files):
            src = Path(src)
            # Index-prefix only when disambiguation is actually needed —
            # one attachment keeps its own descriptive name.
            name = f"{i:02d}-{src.name}" if multiple else src.name
            dest = adir / name
            shutil.move(str(src), str(dest))
            attachment_names.append(name)
    lines = [
        "---",
        f"id: {eid}",
        f"source: {source}",
        f"status: {status}",
    ]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    if attachment_names:
        lines.append(f"attachments: {','.join(attachment_names)}")
    lines.append(f"created: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    lines.append("---")
    lines.append(body)
    path = inbox_dir / f"{eid}.md"
    _atomic_write(path, "\n".join(lines) + "\n")
    if status == "pending":
        # Nudge a waiting daemon loop so it reacts to this event without
        # waiting out a full poll tick. Outbound-only (``done``) events
        # are delivered by gate threads, not the spawn loop, so they
        # don't wake it. Harmless no-op outside the daemon process.
        _inbox_wake.set()
    return path


def event_attachment_names(event: dict[str, Any]) -> list[str]:
    """Announced attachment filenames for *event*, whether or not the bytes
    are still on disk.

    Companion to :func:`event_attachment_paths`, which silently drops a
    name with no local file (a cleanup race, a hand-edited event file,
    retention, or — for a run that only ever saw this event *folded in*
    rather than as its own waking event — a source drawer this reader never
    downloaded into in the first place). That silence is correct for a
    caller that only wants openable paths, but it collapses two different
    facts into the same empty list: *no attachment was ever announced* and
    *one was announced and never became bytes*. A caller that needs to tell
    those apart — render "announced, not fetched" instead of nothing — reads
    this one first.
    """
    raw = event.get("attachments")
    if not raw:
        return []
    return [n.strip() for n in str(raw).split(",") if n.strip()]


def event_attachment_paths(event: dict[str, Any]) -> list[Path]:
    """Resolve an event's ``attachments:`` field to local file paths.

    Derives the attachments directory from the event's own ``_path``
    (every event dict :func:`_read_event` produces carries one) rather
    than taking a separate ``inbox_dir`` argument, so callers holding
    just the event dict — ``prompts.py``, ``run_context.py`` — don't need
    to thread the inbox path through to reach it. Filters out names no
    longer on disk (a cleanup race, a hand-edited event file) instead of
    handing back a dangling path for ``Read`` to fail on.
    """
    names = event_attachment_names(event)
    event_path = event.get("_path")
    eid = event.get("id")
    if not names or not event_path or not eid:
        return []
    adir = attachments_dir_for_event(Path(event_path).parent, str(eid))
    return [p for p in (adir / n for n in names) if p.is_file()]


def _read_event(path: Path) -> dict[str, Any] | None:
    """Parse an event file, returning metadata + body. None on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    fm = parse_frontmatter(text)
    if not fm.get("id"):
        return None
    fm["body"] = frontmatter_body(text).strip()
    fm["_path"] = path
    return fm


def _event_sort_key(entry: os.DirEntry) -> tuple[int, str]:
    """``os.DirEntry`` "last touched" order — a filesystem-walk primitive,
    not an age proof. Answers "which file did the scandir sweep see most
    recently modified", nothing about when the event arrived. Still correct
    for :func:`list_done` and :func:`list_active` (see their own comments);
    :func:`list_pending` and :func:`list_noted` moved to
    :func:`_event_queue_sort_key` instead (#1497 sweep — mtime used as a
    proof of age)."""
    try:
        mtime = entry.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return (mtime, entry.name)


def _event_mtime(event: dict) -> float:
    """File mtime of a pending event ≈ its arrival time. 0.0 when unknown,
    so an unstattable event reads as old and never holds the burst window.

    Moved here from ``daemon.py`` (#1497 sweep) so :func:`list_pending` and
    :func:`list_noted` can share the same age semantics as the daemon's
    dispatch sort and resident pending view instead of each keeping its own
    copy; ``daemon.py`` imports this rather than redefining it.
    """
    path = event.get("_path")
    try:
        return path.stat().st_mtime
    except (OSError, AttributeError):
        return 0.0


def _event_created_epoch(event: dict) -> float | None:
    """Epoch seconds for an event's own ``created`` stamp, or ``None``.

    ``created`` is written once, at ingestion (:func:`create_event`), so
    unlike :func:`_event_mtime` — file mtime, which *any* later status/meta
    write bumps — it is a stable proxy for arrival order. ``None`` on a
    missing or unparseable stamp; callers fall back to :func:`_event_mtime`
    rather than treating "no signal" as either oldest or newest. Built on
    :func:`parse_iso_epoch`, the same ``Z``-suffix ISO-8601 parse every
    other stamp in this module already uses.
    """
    return parse_iso_epoch(event.get("created"))


def _event_queue_sort_key(event: dict) -> tuple[float, float]:
    """Age order for a pending/noted event queue — oldest first.

    #1496/#1497: file mtime alone is a proxy for age that any status write
    invalidates — a re-queue, a defer, a plain meta update all bump it,
    sending an event to the back of its own line (measured live: an event
    dispatched at 14:58:06Z sorted *after* one created 15:33:51Z because the
    earlier one's mtime was bumped by an unrelated ``status: processing``
    write at 18:34Z). Sort by the event's own ``created`` instead — written
    once, never touched again — with :func:`_event_mtime` as the tiebreaker
    for equal ``created`` values *and* the fallback for any event that
    predates the field or fails to parse it, so a missing stamp degrades to
    the old mtime ordering rather than crashing or jumping the queue.

    Shared by the daemon's dispatch sort (``daemon.py``'s
    ``_dispatchable_targets``), its resident pending view
    (``_pending_events_for_agent``), and this module's :func:`list_pending`
    / :func:`list_noted` — one derivation of "how old is this event",
    reused rather than copied at each site (#1506/#1507 is what a second
    derivation costs).
    """
    mtime = _event_mtime(event)
    created = _event_created_epoch(event)
    return (created if created is not None else mtime, mtime)


#: The letter's own lifecycle — the one state machine ``status:`` belongs
#: to (module docstring above). Every value a current writer puts in an
#: event's ``status:`` field:
#:
#: * ``"pending"`` — arrived, nobody has picked it up.
#: * ``"processing"`` — a wake holds it (``protocol.set_status(event,
#:   "processing")`` at dispatch).
#: * ``"done"`` — the daemon's own closeout: a successful run, the
#:   birth-state a gate uses for an outbound-only event (``create_event(...,
#:   status="done")``), and — since the split this set documents — where a
#:   run's outcome settles too (``daemon.py``'s ``_set_event_run_outcome``,
#:   which records the outcome itself in the sibling ``run_outcome:`` key
#:   instead of here).
#: * ``"delivered"`` — a gate, after a successful send
#:   (``gates/runtime.py``'s poll loop, ``protocol.set_status(event,
#:   "delivered")``).
#: * ``"noted"`` — the resident retired the event deliberately, no reply
#:   owed (the ``note:`` outbox verb — ``daemon.py``'s drain sets it with
#:   ``noted_by``/``noted_at`` provenance; no gate ever delivers one).
#:
#: A status-less or unparseable event is never assumed terminal.
LETTER_STATUSES = frozenset({
    "pending", "processing", "done", "delivered", "noted",
})

#: Values a run's *outcome* used to write straight into ``status:`` before
#: the split above — ``run.py``'s ``STATUSES`` (``error``/``conflict``) plus
#: the daemon's own ``stopped`` result and its retired ``cancelled``
#: translation for a parent- or dashboard-initiated stop. No current writer
#: puts these in ``status:`` (see ``daemon.py``'s ``_set_event_run_outcome``
#: — the outcome lands in ``run_outcome:`` and the letter settles at
#: ``"done"`` instead), but event files written before the split still carry
#: them, and they must stay terminal for retention and every "already
#: handled?" reader.
_LEGACY_RUN_OUTCOME_STATUSES = frozenset({
    "error", "conflict", "stopped", "cancelled",
})

#: Statuses under which an event's lifecycle is over, so it is safe to
#: collect once it also ages past a retention window. Derived from
#: :data:`LETTER_STATUSES` (the terminal ones — ``"pending"`` and
#: ``"processing"`` are exactly the two statuses ``list_pending`` above and
#: every still-eligible-work check still treat as unhandled) plus the
#: legacy run-outcome values above, kept for on-disk compatibility.
TERMINAL_EVENT_STATUSES = frozenset(
    (LETTER_STATUSES - {"pending", "processing"}) | _LEGACY_RUN_OUTCOME_STATUSES
)


def list_pending(inbox_dir: Path) -> list[dict[str, Any]]:
    """Return events with status pending or processing, oldest first.

    "Oldest first" is an age question — when did this event arrive — not a
    filesystem-touch question, so this sorts on :func:`_event_queue_sort_key`
    (recorded ``created``, mtime tiebreak/fallback) rather than raw mtime
    (#1497 sweep). Load-bearing: ``daemon.py``'s
    ``_defer_pending_siblings_after_failure`` stages sibling backoff
    directly off this order ("oldest first ⇒ released first"), so an event
    whose mtime got bumped by an unrelated write (a re-queue, a defer, a
    ``status: processing`` stamp) used to jump the stagger queue. Sorting
    happens after the read (not on the raw ``DirEntry`` scan, unlike
    :func:`list_done` / :func:`list_active` below) because ``created`` lives
    in the event body, not in anything ``os.scandir`` exposes.
    """
    if not inbox_dir.exists():
        return []
    events = []
    for entry in os.scandir(inbox_dir):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        if ev and ev.get("status") in ("pending", "processing"):
            events.append(ev)
    events.sort(key=_event_queue_sort_key)
    return events


def known_origin_ids(inbox_dir: Path, meta_key: str) -> set[str]:
    """Every value of *meta_key* already present in *inbox_dir*, any status.

    An upstream event id is an *identity*; a poll cursor is a *position*.
    Position is the cheaper index and the one the daemon keeps, but it lives
    in a single local JSON file — lose it, or have a re-pair write a zero
    over it, and the server replays history the daemon has already answered.
    Identity survives that, because the answered copies are still on disk.

    Read whole rather than incrementally on purpose: this is called only when
    a poll actually returned events, and the set it builds is the one thing
    that can say "seen this" after the cursor has forgotten.
    """
    seen: set[str] = set()
    if not inbox_dir.exists():
        return seen
    for entry in os.scandir(inbox_dir):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        value = str((ev or {}).get(meta_key) or "").strip()
        if value:
            seen.add(value)
    return seen


def known_origin_events(inbox_dir: Path, meta_key: str) -> dict[str, dict[str, Any]]:
    """Like :func:`known_origin_ids`, keyed to the local event dict itself.

    #1396 — a caller that only needs *whether* an upstream id was seen wants
    :func:`known_origin_ids`; a caller that needs to act on the local copy
    (fold in attachments an upstream merge added after this daemon already
    ingested the event once) needs the event dict too, so it isn't re-read
    from disk a second time. Same one-id-per-file assumption as
    :func:`known_origin_ids` — the ingestion path this pairs with
    (``gates/cloud.py``'s ``_loop_once``) never creates a second local file
    for one upstream id, so "last one wins" on a duplicate never triggers in
    practice.
    """
    seen: dict[str, dict[str, Any]] = {}
    if not inbox_dir.exists():
        return seen
    for entry in os.scandir(inbox_dir):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        if not ev:
            continue
        value = str(ev.get(meta_key) or "").strip()
        if value:
            seen[value] = ev
    return seen


def append_event_attachments(
    inbox_dir: Path, event: dict[str, Any], new_files: list[Path],
) -> list[str]:
    """Move *new_files* into *event*'s attachment dir and extend its
    ``attachments:`` field. Returns the filenames actually added.

    Companion to :func:`create_event`'s own attachment handling, for the
    #1396 reconcile: an event already ingested from an upstream source
    (Telegram, via ``gates/cloud.py``) can grow more attachments on a later
    poll when an album item merges into it server-side after this daemon
    already ingested an earlier item. Appends only — existing names are
    never touched or renumbered, so anything already resolved via
    :func:`event_attachment_paths` keeps resolving.
    """
    if not new_files:
        return []
    eid = str(event.get("id") or "")
    adir = attachments_dir_for_event(inbox_dir, eid)
    adir.mkdir(parents=True, exist_ok=True)
    existing_names = event_attachment_names(event)
    added: list[str] = []
    for offset, src in enumerate(new_files):
        src = Path(src)
        # Always index-prefixed from the running count — disambiguates from
        # whatever is already on disk (including a single un-prefixed
        # original) without needing to rename anything that's already there.
        name = f"{len(existing_names) + offset:02d}-{src.name}"
        dest = adir / name
        shutil.move(str(src), str(dest))
        added.append(name)
    update_event_meta(event, attachments=",".join(existing_names + added))
    return added


def parse_iso_epoch(value: object) -> float | None:
    """Parse an ISO-8601 stamp (``Z`` or offset variant) to epoch seconds.

    Originally private to this module's own ``defer_until`` handling;
    promoted (reconciling #1495/#1500) because ``bootscore.event_age_seconds``
    needed the identical ``Z``-suffix-to-``+00:00`` parse for the event's
    ``created`` stamp — same frontmatter, same shape, no reason for a second
    copy. Missing/unparseable ⇒ ``None``, never a guess or a crash.
    """
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def event_is_deferred(event: dict[str, Any], now: float | None = None) -> bool:
    """Return true when an event has a future ``defer_until`` timestamp.

    Invalid timestamps degrade to "not deferred" so a malformed event does
    not disappear from dispatch forever.
    """
    until = parse_iso_epoch(event.get("defer_until"))
    if until is None:
        return False
    return until > (time.time() if now is None else now)


def list_dispatchable(
    inbox_dir: Path,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Return pending/processing events whose deferral has expired.

    ``list_pending`` intentionally keeps returning deferred events so a
    fresh wake can still see and fold them from the live inbox. The daemon
    dispatch loop uses this narrower view for choosing a lead event.
    """
    return [
        event for event in list_pending(inbox_dir)
        if not event_is_deferred(event, now=now)
    ]


def list_done(inbox_dir: Path, source: str) -> list[dict[str, Any]]:
    """Return done events matching *source*, oldest first.

    #1497 sweep classification: no production call site in this tree today
    (only test assertions, mostly single-event or empty-list checks that
    are order-insensitive either way — swept 2026-08-19). Left on
    :func:`_event_sort_key` (last-touched) rather than moved to
    :func:`_event_queue_sort_key` (age) — there is no live consumer to be
    wrong for, so there is no defect to fix, only a docstring claim
    ("oldest first") nothing currently exercises. Flagged, not changed: a
    future caller that starts relying on true arrival order should switch
    this to :func:`_event_queue_sort_key` at that point rather than assume
    the existing sort already means that.
    """
    if not inbox_dir.exists():
        return []
    events = []
    for entry in sorted(os.scandir(inbox_dir), key=_event_sort_key):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        if ev and ev.get("status") == "done" and ev.get("source") == source:
            events.append(ev)
    return events


def list_active(inbox_dir: Path, source: str) -> list[dict[str, Any]]:
    """Return processing+done events matching *source*, oldest first.

    The delivery surface for the streaming (multi-response) protocol: a
    *processing* event may already have interim responses queued, and a
    *done* event additionally has its terminal response ready. A plain
    single-response run shows up here only once it reaches ``done`` (it
    has no partials while processing), so this stays behaviourally
    identical to ``list_done`` for that case.

    #1497 sweep classification: ``gates/runtime.py``'s ``deliver_stream``
    (the only production caller) sweeps **every** active event matching
    *source* on **every** poll tick — no batch cap, no early-exit. Cross-
    event order here only affects which of two due events posts its
    message microseconds before the other *within the same tick*; it
    never gates *whether* or *how soon* an event's turn comes; a bumped
    mtime costs nothing an unbumped one wouldn't also cost next tick. That
    is a genuine "when was this file last touched, for one sweep pass"
    question, not an age proof — :func:`_event_sort_key` is correct here
    and stays. Contrast :func:`list_noted` below, whose sweep *is* capped
    and where the same mtime skew would starve the genuinely-oldest row.
    """
    if not inbox_dir.exists():
        return []
    events = []
    for entry in sorted(os.scandir(inbox_dir), key=_event_sort_key):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        if ev and ev.get("status") in ("processing", "done") \
                and ev.get("source") == source:
            events.append(ev)
    return events


def list_noted(inbox_dir: Path, source: str) -> list[dict[str, Any]]:
    """Return ``noted`` events matching *source*, oldest first.

    ``noted`` is terminal locally — the resident retired the letter without
    speaking — but the origin that *sent* the letter has no way to know
    that unless someone tells it. This lister is the sweep surface for
    telling it; ``list_active`` deliberately excludes ``noted`` because
    nothing is ever *delivered* for one.

    #1497 sweep classification: this **is** an age question, and unlike
    :func:`list_active` its sweep (``gates/cloud.py``'s
    ``_close_noted_events``) *is* batch-capped (``_NOTED_CLOSE_BATCH``) —
    so a wrong order doesn't just cost microseconds, it can push the
    genuinely-oldest queued row past this poll's cutoff. That row is the
    one pinning the server's ``clamp_since`` floor (``oldest_queued - 1``
    in ``src/brnrd/inbox.py``), so delaying its close delays the cursor
    heal, not just this one event's own delivery — the same fairness
    argument :func:`list_pending` already makes, crossing the local/server
    boundary via ``cloud_event_id``. Sorted on
    :func:`_event_queue_sort_key` accordingly.
    """
    if not inbox_dir.exists():
        return []
    events = []
    for entry in os.scandir(inbox_dir):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        if ev and ev.get("status") == "noted" and ev.get("source") == source:
            events.append(ev)
    events.sort(key=_event_queue_sort_key)
    return events


def set_status(event: dict[str, Any], status: str) -> None:
    """Update the status field of an event file atomically."""
    path: Path = event["_path"]
    text = path.read_text(encoding="utf-8")
    old_status = event.get("status", "pending")
    new_text = text.replace(f"status: {old_status}", f"status: {status}", 1)
    _atomic_write(path, new_text)
    event["status"] = status


def update_event_meta(event: dict[str, Any], **updates: object) -> None:
    """Set or clear flat event frontmatter keys atomically.

    Passing ``None`` removes a key. Event frontmatter is intentionally flat
    today; nested blocks are preserved if present but not edited.
    """
    path: Path = event["_path"]
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?\n)---\n?", text, re.DOTALL)
    if not m:
        return

    seen: set[str] = set()
    lines: list[str] = []
    for line in m.group(1).splitlines():
        stripped = line.lstrip()
        if ":" not in stripped or line.startswith(" "):
            lines.append(line)
            continue
        key = stripped.split(":", 1)[0].strip()
        if key in updates:
            seen.add(key)
            value = updates[key]
            if value is None:
                continue
            lines.append(f"{key}: {_format_meta_value(value)}")
        else:
            lines.append(line)

    insert_at = len(lines)
    for idx, line in enumerate(lines):
        if line.split(":", 1)[0].strip() == "created":
            insert_at = idx
            break
    additions = [
        f"{key}: {_format_meta_value(value)}"
        for key, value in updates.items()
        if key not in seen and value is not None
    ]
    lines[insert_at:insert_at] = additions

    body = text[m.end():]
    _atomic_write(path, "---\n" + "\n".join(lines) + "\n---\n" + body)
    for key, value in updates.items():
        if value is None:
            event.pop(key, None)
        else:
            event[key] = value


# ── Response files ───────────────────────────────────────────────────


def response_path(responses_dir: Path, event_id: str) -> Path:
    """Return the expected response file path for an event."""
    return responses_dir / f"{event_id}.md"


def response_exists(responses_dir: Path, event_id: str) -> bool:
    """Check if a response file exists for the given event."""
    return response_path(responses_dir, event_id).exists()


def read_response(responses_dir: Path, event_id: str) -> str | None:
    """Read the response body, or None if missing.

    Responses are plain markdown — what the runner printed on stdout.
    For backwards compatibility we still strip a leading frontmatter
    block if one happens to be present (it never is in normal flow).
    """
    path = response_path(responses_dir, event_id)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    return frontmatter_body(text).strip()


def write_response(
    responses_dir: Path,
    event_id: str,
    body: str,
    *,
    message_path: Path | None = None,
) -> Path:
    """Write a plain-text response file. Returns the file path.

    The wire format is just the body — there is no frontmatter
    contract on response files. ``event_id`` is preserved in the
    filename so the daemon and gates can correlate without parsing.
    """
    responses_dir.mkdir(parents=True, exist_ok=True)
    if message_path is not None:
        body = f"---\nmessage_path: {message_path}\n---\n\n{body}"
    if not body.endswith("\n"):
        body += "\n"
    path = response_path(responses_dir, event_id)
    _atomic_write(path, body)
    return path


# ── Interim response partials (the streaming queue) ─────────────────
# A per-event queue of interim responses the resident ships mid-flight
# (the multi-response protocol, see kb/design-multi-response.md). The
# terminal response stays ``<eid>.md``; partials live in
# ``<eid>.partials/`` as ordered compatibility carriers, delivered before the
# terminal. Durable status lives in the referenced run message. Absent any partials, delivery is exactly the
# single-response flow — this surface no-ops when unused.


def partials_dir(responses_dir: Path, event_id: str) -> Path:
    """Return the interim-response queue directory for an event."""
    return responses_dir / f"{event_id}.partials"


def list_partials(responses_dir: Path, event_id: str) -> list[Path]:
    """Return pending interim response files for an event, oldest first.

    Names are zero-padded sequence numbers, so a lexical sort is a
    chronological sort. The durable message status decides whether a retained
    carrier is pending; legacy carriers move to a ``.delivered`` suffix.
    """
    pdir = partials_dir(responses_dir, event_id)
    if not pdir.exists():
        return []
    return sorted(
        (p for p in pdir.iterdir() if p.suffix == ".md"),
        key=lambda p: p.name,
    )


def write_partial(
    responses_dir: Path,
    event_id: str,
    body: str,
    *,
    message_path: Path | None = None,
) -> Path:
    """Append an interim response to an event's queue. Returns the path.

    Sequence numbers continue past the current max so ordering survives
    across retained carriers.
    """
    pdir = partials_dir(responses_dir, event_id)
    pdir.mkdir(parents=True, exist_ok=True)
    existing = [int(p.stem) for p in pdir.glob("*.md") if p.stem.isdigit()]
    seq = (max(existing) + 1) if existing else 1
    if message_path is not None:
        body = f"---\nmessage_path: {message_path}\n---\n\n{body}"
    if not body.endswith("\n"):
        body += "\n"
    path = pdir / f"{seq:06d}.md"
    _atomic_write(path, body)
    return path


def attach_message_path(path: Path, message_path: Path) -> None:
    """Point an already-written response carrier at its durable message."""

    text = path.read_text(encoding="utf-8")
    meta = parse_frontmatter(text)
    body = frontmatter_body(text)
    meta["message_path"] = str(message_path)
    lines = ["---"]
    lines.extend(f"{key}: {_format_meta_value(value)}" for key, value in meta.items())
    lines.extend(["---", "", body.lstrip("\n")])
    _atomic_write(path, "\n".join(lines))


def read_partial(path: Path) -> str | None:
    """Read an interim response body, or None if it can't be read."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return frontmatter_body(text).strip()
