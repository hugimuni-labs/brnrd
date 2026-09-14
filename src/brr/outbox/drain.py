"""The drain: read the outbox, hand each file to the verb table.

``daemon._drain_outbox`` keeps its name and signature and is a call into
:func:`drain`. What stayed here is what ran *before* any verb on ``main``
(``daemon.py:8555–8613``, moved verbatim): the oldest-first listing, the
staging/dotfile/control-name skip, the guarded tolerant parse, and the
``do-*-cut-*`` staging-casualty drop. What moved out is the dispatch — one
handler per key in ``verbs.py``, chosen in ``table.py``'s precedence.
"""

from __future__ import annotations

from pathlib import Path

from .. import daemon
from .. import portals
from .. import protocol
from . import table
from .shapes import DrainContext, OutboxFile


def drain(
    emit,
    task,
    responses_dir: Path,
    event_id: str,
    outbox_dir: Path | None,
    inbox_dir: Path | None = None,
    *,
    repo_root: Path | None = None,
    account_context=None,
    stats: dict[str, int] | None = None,
) -> int:
    """See ``daemon._drain_outbox`` — the contract is unchanged."""
    if not outbox_dir or not outbox_dir.exists():
        return 0
    try:
        entries = sorted(
            (p for p in outbox_dir.iterdir() if p.is_file()),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )
    except OSError:
        return 0
    promoted = 0
    # The union of inboxes an ``event:`` / ``note:`` target may live in
    # (#936) — the dirs are enumerated once per drain; the pending sets
    # themselves are re-read from disk at each resolution, so an earlier
    # file's retire is visible to the next.
    address_sources = daemon._outbox_address_sources(
        inbox_dir, responses_dir, account_context, repo_root,
    )
    for fpath in entries:
        # ``.tmp`` anywhere in the suffix chain is the agent's atomic-write
        # staging name (``portals.is_staging_name`` — a bare ``.suffix``
        # check missed ``note.md.tmp.<pid>.<rand>`` and delivered a message
        # mid-write, #590); dotfiles are reserved as control channels
        # (e.g. ``.keepalive`` for the liveness budget), and the live JSON
        # files are daemon-owned control state. None are deliverable.
        if (
            portals.is_staging_name(fpath.name)
            or fpath.name.startswith(".")
            or fpath.name in portals.CONTROL_NAMES
        ):
            continue
        try:
            text = fpath.read_text(encoding="utf-8")
        except OSError:
            continue
        # Tolerant parse: accept both a ``---``-fenced block and the common
        # resident slip of a leading ``event:`` / ``gate:`` line + ``---``
        # with no opening fence. The strict parser silently misrouted the
        # latter (leaked selector text, reply on the lead event); see
        # ``protocol.parse_outbox_message``.
        #
        # Guarded (#1379): a malformed staging file must cost itself, not
        # the tick — see ``_OutboxEntryGuard``. ``fm``/``body`` are unset on
        # a tripped parse, so this one explicitly re-checks and continues
        # rather than falling into the dispatch below with half state.
        parse_guard = daemon._OutboxEntryGuard(outbox_dir, fpath)
        with parse_guard:
            fm, body = protocol.parse_outbox_message(text)
            body = body.strip()
        if parse_guard.tripped:
            continue
        if not fm and fpath.match("do-*-cut-*.md"):
            daemon._record_outbox_notice(
                outbox_dir,
                "cut dropped: empty frontmatter in staged cut — "
                "staging casualty, not a reply",
                kind="dropped", lifetime="run", source_file=fpath.name, verb="cut",
            )
            daemon._retire_outbox_staging(fpath)
            continue

        f = OutboxFile(
            path=fpath,
            frontmatter=fm,
            body=body,
            run=task,
            ctx=DrainContext(
                emit=emit,
                responses_dir=responses_dir,
                event_id=event_id,
                outbox_dir=outbox_dir,
                inbox_dir=inbox_dir,
                repo_root=repo_root,
                account_context=account_context,
                stats=stats,
                address_sources=address_sources,
            ),
        )
        for result in table.dispatch(f):
            promoted += result.promoted
    return promoted
