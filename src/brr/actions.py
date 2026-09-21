"""The action ledger: what a body did to the world, as rows it did not write.

``.actions.jsonl`` lives in the run's outbox dir beside ``.relics.jsonl`` and
``.promises.jsonl`` and holds only **world-facing** acts — a message sent, a
strand dispatched, a promise to make something. Reading a file is not one;
``boundaries.jsonl`` keeps that, priced. Design and decisions:
``kb/design-the-action-ledger.md``.

One row per *state change*, append-only. A transition is a **new row with the
same id** carrying the full snapshot of the act (the previous row's fields,
overlaid with what changed), so :func:`read` is "the last row per id" and a
crash can never leave a half-updated act — only an older, still-true one.

    {"id": "act-…", "ts": "…", "by": "seat", "verb": "message",
     "target": "gate:telegram", "why": "…", "idempotent": false,
     "state": "attempted", "evidence": "…", "after": ["act-…"], "boundary": "…"}

States, in order: ``requested`` → ``attempted`` → ``observed`` → ``confirmed``,
with ``failed`` / ``ambiguous`` as the two honest dead ends. **Nothing here
asks the model for anything**: the daemon's seams write these rows as they
fire, and no state is ever set from prose. ``observed`` in particular is the
daemon's alone (a ``spawn_completed`` it rendered, a drain that returned).

Best-effort like ``relics.append``: a missing outbox dir, an unwritable file
or a row that will not serialise is a no-op — an unrecorded receipt must
never break the act it would have recorded.
"""

from __future__ import annotations

import json
import secrets
import string
import time
from pathlib import Path
from typing import Any

CONTROL_NAME = ".actions.jsonl"

#: The states, in the order an act moves through them.
STATES = (
    "requested", "attempted", "observed", "confirmed", "failed", "ambiguous",
)

_MAX_LINE_BYTES = 4096
_MAX_RECORDS = 2000
_MAX_TEXT = 800
_ID_ALPHABET = string.ascii_lowercase + string.digits

#: The fields a row may carry, in the order they are written.
_FIELDS = (
    "id", "ts", "by", "verb", "target", "why", "idempotent", "state",
    "evidence", "after", "boundary",
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(4))
    return f"act-{time.time_ns()}-{suffix}"


def _clip(value: Any) -> Any:
    """Bound a free-text field so one long notice cannot cost the whole row."""
    if isinstance(value, str) and len(value) > _MAX_TEXT:
        return value[: _MAX_TEXT - 1].rstrip() + "…"
    return value


def _write(outbox_dir: Path | None, record: dict[str, Any]) -> bool:
    """Serialise and append one row. ``False`` when nothing was written."""
    if outbox_dir is None:
        return False
    # ``idempotent`` is a bool: ``False`` is a real answer, not an absence.
    ordered = {
        k: record[k] for k in _FIELDS
        if k == "idempotent" and k in record
        or record.get(k) not in (None, "", [])
    }
    try:
        line = json.dumps(ordered, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    if len(line.encode("utf-8")) > _MAX_LINE_BYTES:
        return False
    try:
        with (Path(outbox_dir) / CONTROL_NAME).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        return False
    return True


def append(
    outbox_dir: Path | None,
    *,
    verb: str,
    target: str = "",
    state: str = "requested",
    by: str = "seat",
    why: str = "",
    idempotent: bool = False,
    evidence: str | None = None,
    after: list[str] | None = None,
    boundary: str | None = None,
) -> str:
    """Open a new act and return its id (``""`` when nothing was written).

    The id is ``act-<ts-ns>-<4 chars>``. The dir is never created here — an
    outbox that is not there means no run is listening, and the call is a
    no-op, same as ``relics.append``.
    """
    if outbox_dir is None or not verb or state not in STATES:
        return ""
    act_id = _new_id()
    record: dict[str, Any] = {
        "id": act_id,
        "ts": _now(),
        "by": by or "seat",
        "verb": verb,
        "target": _clip(target),
        "why": _clip(why),
        "idempotent": bool(idempotent),
        "state": state,
        "evidence": _clip(evidence),
        "after": list(after) if after else None,
        "boundary": boundary,
    }
    return act_id if _write(outbox_dir, record) else ""


def transition(
    outbox_dir: Path | None,
    act_id: str,
    state: str,
    **fields: Any,
) -> bool:
    """Move act *act_id* to *state* by appending a new row with the same id.

    The new row is the act's latest snapshot overlaid with *fields*
    (``evidence=``, ``target=``, ``why=``, ``after=``, ``boundary=``…); an
    act the file has never seen still gets a row, carrying only what this
    call says. Nothing already written is rewritten. ``False`` when nothing
    was written (no dir, unknown state, empty id).
    """
    if outbox_dir is None or not act_id or state not in STATES:
        return False
    record: dict[str, Any] = dict(read(outbox_dir).get(act_id) or {"id": act_id})
    for key, value in fields.items():
        if key in _FIELDS and key not in ("id", "ts", "state"):
            record[key] = _clip(value)
    record["id"] = act_id
    record["ts"] = _now()
    record["state"] = state
    record.setdefault("verb", "other")
    return _write(outbox_dir, record)


def read_rows(outbox_dir: Path | None) -> list[dict[str, Any]]:
    """Every row in file order — the whole history, transitions included."""
    if outbox_dir is None:
        return []
    try:
        text = (Path(outbox_dir) / CONTROL_NAME).read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("id") and record.get("state") in STATES:
            out.append(record)
        if len(out) >= _MAX_RECORDS:
            break
    return out


def read(outbox_dir: Path | None) -> dict[str, dict[str, Any]]:
    """``{id: latest row}`` — file order is the order of truth, not ``ts``."""
    latest: dict[str, dict[str, Any]] = {}
    for row in read_rows(outbox_dir):
        latest[str(row["id"])] = row
    return latest


def open_attempted(outbox_dir: Path | None) -> list[dict[str, Any]]:
    """Acts whose latest state is ``attempted`` — fired, never resolved.

    The closeout's question: each of these is a POST whose reply was never
    read, which is what ``ambiguous`` is for.
    """
    return [row for row in read(outbox_dir).values() if row.get("state") == "attempted"]


def find(
    outbox_dir: Path | None, verb: str, key: str, *, states: tuple[str, ...] = (),
) -> str:
    """The id of the newest act of *verb* whose latest row names *key*.

    *key* is matched against ``target`` and ``evidence`` — the two fields a
    seam joins on (a spawn's dispatch event id lives in one or the other
    depending on how far the act has travelled). *states* narrows to acts
    currently in one of those states. ``""`` when none.
    """
    if not key:
        return ""
    match = ""
    for act_id, row in read(outbox_dir).items():
        if row.get("verb") != verb:
            continue
        if states and row.get("state") not in states:
            continue
        if key in (row.get("target"), row.get("evidence")):
            match = act_id
    return match
