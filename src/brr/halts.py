"""The halt ledger — one account-scoped row per seat that ended.

design-the-four-stops.md §"The display, which is the condition of his
signature": the maintainer signed ``halt:`` *"as long as we clearly
display it on the main dashboard"*, and what he signed for is **not a
badge** — it is *a list a reader can work from: what stopped, and what
would restart it*. This module is that list's storage. The rendering is
someone else's build; this is the file it reads.

Three properties the design asks for, and where each lives here:

- **permanent** — append-only JSONL under the account home
  (``account/halts.jsonl``), never rewritten, never pruned by a run. A
  halt is a fact about the account's history, not a transient state that
  a later boot may tidy away.
- **counted per account**, and **carried counted apart from stopped**
  (:func:`counts`). *One halt is a decision; four in a week is a pattern,
  and a pattern is what no approval dialog could ever have shown.* A halt
  with a successor is ordinary metabolism; a halt without one is a claim
  about the work.
- **a queue, not a graveyard** (:func:`open_queue`) — every halt with no
  carry whose open items were not empty, with the ``resumable:`` text that
  says what would revive it. That is the whole return on requiring the
  field.

Everything here is best-effort in the posture the rest of the ledger
surfaces use: an unreadable or malformed file degrades to fewer rows and
no read path raises into a boundary. A halt must never fail because its
bookkeeping did.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import halt_verb

#: Account-relative path. ``account/`` is the account-scoped state dir the
#: cloud gate already uses (``gates/cloud.py``), so this needs no new
#: directory contract.
LEDGER_RELPATH = ("account", "halts.jsonl")

#: Defensive read cap — a ledger longer than this is a bug upstream, and a
#: boundary must not pay to find out. The newest rows are the ones every
#: consumer wants, so the tail is what survives the cap.
MAX_ROWS_READ = 2000


def ledger_path(account_home: Path | None) -> Path | None:
    """The ledger file under *account_home*, or ``None`` with no home."""
    if account_home is None:
        return None
    return Path(account_home).joinpath(*LEDGER_RELPATH)


def record(
    account_home: Path | None,
    *,
    run_id: str,
    repo_label: str = "",
    conversation_key: str = "",
    kind: str,
    reason: str,
    resumable: str | None = None,
    carry: str | None = None,
    successor_event: str = "",
    open_items: "list[str] | tuple[str, ...]" = (),
    at: str | None = None,
) -> dict[str, Any] | None:
    """Append one halt row. Returns the row written, or ``None``.

    Never raises: a ledger that cannot be written must not be able to stop
    a seat from ending — the halt already happened, and the record is the
    *display's* dependency, not the verb's.
    """
    path = ledger_path(account_home)
    if path is None:
        return None
    row = {
        "at": at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": str(run_id or ""),
        "repo": str(repo_label or ""),
        "thread": str(conversation_key or ""),
        "kind": (
            kind if kind in (halt_verb.KIND_CARRIED, halt_verb.KIND_STOPPED)
            else halt_verb.KIND_STOPPED
        ),
        "reason": str(reason or ""),
        "carry": str(carry) if carry else None,
        "resumable": str(resumable) if resumable else None,
        "successor_event": str(successor_event or ""),
        "open_items": [str(item) for item in (open_items or ())],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return row


def read(account_home: Path | None) -> list[dict[str, Any]]:
    """Every recorded halt, oldest first. Best-effort; never raises."""
    path = ledger_path(account_home)
    if path is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-MAX_ROWS_READ:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def counts(rows: "list[dict[str, Any]] | None") -> dict[str, int]:
    """``{"carried": n, "stopped": m, "total": n + m}``.

    Two numbers, never one: *a body wearing out* and *work stopping* are
    different events, and collapsing them would hide exactly the pattern
    the count exists to show.
    """
    carried = stopped = 0
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        if str(row.get("kind") or "") == halt_verb.KIND_CARRIED:
            carried += 1
        else:
            stopped += 1
    return {
        halt_verb.KIND_CARRIED: carried,
        halt_verb.KIND_STOPPED: stopped,
        "total": carried + stopped,
    }


def open_queue(rows: "list[dict[str, Any]] | None") -> list[dict[str, Any]]:
    """The standing list: halts with no carry whose open items were not empty.

    *Abandoned work with a stated revival path is a queue, not a
    graveyard.* Newest first — a reader works the top.
    """
    queue = [
        row for row in (rows or ())
        if isinstance(row, dict)
        and str(row.get("kind") or "") == halt_verb.KIND_STOPPED
        and row.get("open_items")
    ]
    queue.reverse()
    return queue
