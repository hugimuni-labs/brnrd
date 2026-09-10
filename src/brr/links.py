"""The priced decision chain: what this run decided, and what it cost.

Third file in the family ``.relics.jsonl`` (what a run **made**) and
``.promises.jsonl`` (what it **said it would make**) already form.  This one
is the tense neither carries: what it **chose**, and what the choice cost.

From the maintainer, 2026-09-10:

    you always weave a certain tool call or thinking stream, and you kind of
    should always think of this — its cost and the weighted decision for
    whether to do it or not… you predict how costly it's gonna be, you
    explain why you need it

and, correcting his own first shape one exchange later:

    I think ask you to predict is wrong indeed, but set a boundary, maybe?

He is right, and the reason is the measurement that produced this module.  A
resident held its seat across a 71-minute gate — ten ``brnrd await`` calls,
each a full-context boundary, roughly 250k weighted tokens, **the single
largest expenditure of the run** — and chose it ten separate times without
ever attaching a number to it.  Nothing was hidden; the bar printed the spend
the whole time.  What was missing was **attribution**: which decision the
spend belonged to, and whether anyone had said out loud what it was for.

So this is not an anti-overspend guard.  It is a record that makes the
cheapest-*looking* decision visible, because in a long seat the most expensive
act is the one that produces nothing to notice.

**A boundary, never a prediction.**  A prediction is a guess graded later; a
boundary is a commitment enforced now — and the property that actually paid in
practice was neither the number nor its accuracy.  It was that *having to say
a number out loud changes the plan before any actual arrives*: the first
priced decision in this system started as an in-seat lookup and became a
subagent dispatch while its ``why`` line was being written.

**Nothing is halted, and the ceiling still does something** — his refinement,
2026-09-10, after the first two links closed at +15% and **+367%**:

    what if we rename the --boundary … and would cut the out-of-boundary rest
    into a temp file at some known location

A ceiling that only reports is inert; a ceiling that stops a run gets set high
by everyone who wants to keep working, and then measures nothing.  The third
option is the one that works: **on breach the link projects itself into a
handoff** (:func:`cut_path`, :func:`render_cut`) — what was being done, why,
what it cost, where it stopped — at a known path the run names in its output.
Nothing dies, nothing halts, and the ceiling acquires teeth that are not a
punishment.  Same idea as `cut:` the phase commit, one scale down.

The record can carry itself.  **What *remains* is knowledge only the resident
has**, so `--link-close --cut` writes the projection and the resident adds the
one line a successor actually needs; the breach prompts the cut, it does not
perform it behind anyone's back.

**The item binding is the point** (his steer, same exchange: *"consider the
warp items relation to this… the loom was designed as a shared user/resident
working space"*).  A link bound to a ``.card`` plan row is run-local and
authored by the same hand as the acts it explains — which is why a plan
disagreeing with itself is not evidence of anything.  Bound to a **warp item**
it becomes three things at once: divergence the *user* can see, a per-item
cost that outlives every run that touched it, and the numerator/denominator of
``design-the-yield-per-window.md`` meeting on an object rather than a window.

``--item`` is deliberately **optional, and its absence is the signal**.  A link
with no item renders ``∅``; three of those in a row is course drift, measured
rather than inferred.  Requiring an item would make residents invent one, and
an invented item is worse than a missing one — it is the same failure as a
forecast made defensive by a penalty.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

#: Dot-prefixed, like every sibling control file, and for the reason
#: ``do.ASKS_CONTROL_NAME`` records at length: ``daemon._drain_outbox`` skips a
#: dot-file outright, where a bare ``links.jsonl`` in this directory would be
#: swept up as an undelivered chat message and fail to parse as frontmatter.
CONTROL_NAME = ".links.jsonl"


def _rows(outbox_dir: Path | None) -> list[dict[str, Any]]:
    """Every parsed row, oldest first; malformed lines skipped, never fatal.

    Same tolerance as :func:`do.read_asks` and :func:`relics.read_reported`:
    one unserialisable row must not cost every later reader the file.
    """
    if outbox_dir is None:
        return []
    try:
        text = (Path(outbox_dir) / CONTROL_NAME).read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _write(outbox_dir: Path, rows: list[dict[str, Any]]) -> None:
    """Rewrite the whole file.

    The append-only shape its siblings use cannot express *closing* a link,
    and a closed link is the row a reader wants — an open one has no
    ``spend_close`` and therefore no cost.  Rewriting a run-local file of a
    few dozen lines is cheap and keeps one row per link rather than an open
    event and a close event a reader must pair up.
    """
    body = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    tmp = Path(outbox_dir) / (CONTROL_NAME + ".tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(Path(outbox_dir) / CONTROL_NAME)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def read(outbox_dir: Path | None) -> list[dict[str, Any]]:
    """Every link this run declared, oldest first."""
    return _rows(outbox_dir)


def open_link(
    outbox_dir: Path | None,
    *,
    intent: str,
    why: str,
    boundary: int | None = None,
    item: str | None = None,
    spend: int | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Open a link, closing whichever one was open.

    No nesting, deliberately: a chain is a chain.  Calls made between a close
    and the next open belong to ``∅``, which is the state this whole module
    exists to render.

    ``spend`` is the run's weighted total *at this moment*, supplied by the
    caller because this module must not decide what a token is worth.  The
    row stores endpoints and never a delta — a delta computed at write time
    cannot be recomputed when the weighting changes, and this repo has paid
    for that twice.
    """
    if outbox_dir is None or not str(intent or "").strip():
        return None
    stamp = time.time() if now is None else now
    rows = _rows(outbox_dir)
    for row in rows:
        if row.get("closed_at") is None:
            row["closed_at"] = stamp
            if row.get("spend_close") is None and spend is not None:
                row["spend_close"] = spend
            row.setdefault("note", "closed by the next link")
    record: dict[str, Any] = {
        "opened_at": stamp,
        "closed_at": None,
        "intent": str(intent).strip(),
        "why": str(why or "").strip() or None,
        "item": str(item).strip() if item else None,
        "boundary": int(boundary) if boundary else None,
        "spend_open": spend,
        "spend_close": None,
        "note": None,
    }
    rows.append(record)
    _write(Path(outbox_dir), rows)
    return record


def close_link(
    outbox_dir: Path | None,
    *,
    note: str | None = None,
    spend: int | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Close the open link, if any.  Closing nothing is not an error."""
    if outbox_dir is None:
        return None
    rows = _rows(outbox_dir)
    target = None
    for row in rows:
        if row.get("closed_at") is None:
            target = row
    if target is None:
        return None
    target["closed_at"] = time.time() if now is None else now
    if spend is not None:
        target["spend_close"] = spend
    if note:
        target["note"] = str(note).strip()
    _write(Path(outbox_dir), rows)
    return target


def open_row(outbox_dir: Path | None) -> dict[str, Any] | None:
    """The currently open link, or ``None`` — the ``∅`` state."""
    for row in reversed(_rows(outbox_dir)):
        if row.get("closed_at") is None:
            return row
    return None


def spent(row: dict[str, Any] | None, *, spend_now: int | None = None) -> int | None:
    """Weighted tokens attributed to *row*, or ``None`` when unmeasurable.

    ``None`` rather than ``0`` when an endpoint is missing: a link whose spend
    could not be read has *no measurement*, and rendering that as zero is the
    empty-column failure this repo keeps rediscovering — a field's name is not
    a measurement.
    """
    if not isinstance(row, dict):
        return None
    start = row.get("spend_open")
    end = row.get("spend_close")
    if end is None:
        end = spend_now
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    return max(0, end - start)


def over_boundary(row: dict[str, Any] | None, *, spend_now: int | None = None) -> bool:
    """Whether *row* has passed the ceiling it declared.

    Never halts anything — see the module docstring.  A breach is a prompt to
    :func:`render_cut`, not a stop: the sanction for overrunning is that a
    successor gets handed the shape of what you were doing.
    """
    if not isinstance(row, dict):
        return False
    boundary = row.get("boundary")
    if not isinstance(boundary, int) or boundary <= 0:
        return False
    used = spent(row, spend_now=spend_now)
    return used is not None and used > boundary


def _short(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}m".replace(".0m", "m")
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def chip(outbox_dir: Path | None, *, spend_now: int | None = None) -> str:
    """The boundary-bar chip: the open link's intent, spend and ceiling.

    ``∅`` when nothing is declared — and that is the chip's whole reason to
    hold a slot.  Every other element of the bar reports what happened; this
    one reports whether anybody said what it was *for*.
    """
    row = open_row(outbox_dir)
    if row is None:
        return "link ∅"
    intent = str(row.get("intent") or "").strip() or "unnamed"
    if len(intent) > 32:
        intent = intent[:31] + "…"
    used = spent(row, spend_now=spend_now)
    boundary = row.get("boundary")
    if used is None:
        meter = ""
    elif isinstance(boundary, int) and boundary > 0:
        # Rounded to a tenth of the ceiling, deliberately. A meter that moves
        # on every boundary changes text on every boundary, and a chip that
        # renders every time is the `seen ×N` failure with better arithmetic
        # (#1897). Ten steps over a link's whole life is enough to be
        # interrupted by and few enough to be worth reading.
        pct = int(used * 10 // boundary) * 10
        meter = f" · {pct}%/{_short(boundary)}"
        if used > boundary:
            meter = f" · {_short(used)}/{_short(boundary)} ⚠ over"
    else:
        # No ceiling: round hard, because there is nothing to be a fraction
        # of and the only question this can answer is "is it big yet".
        step = 50_000
        meter = f" · ~{_short(used // step * step)}/∅" if used >= step else " · ∅"
    item = row.get("item")
    return f"link {intent}{meter}" + (f" · {item}" if item else "")


def unbound_streak(outbox_dir: Path | None) -> int:
    """How many of the most recent links named no warp item.

    The course-drift signal, measured rather than inferred: a link that fits
    no open item is the resident working on something the shared graph does
    not know about.  Three in a row does not mean the plan stalled — it means
    the plan is describing a run that stopped happening.
    """
    streak = 0
    for row in reversed(_rows(outbox_dir)):
        if row.get("item"):
            break
        streak += 1
    return streak


CUT_DIRNAME = ".cut"


def cut_path(outbox_dir: Path | None, row: dict[str, Any] | None) -> Path | None:
    """Where a breached link's handoff lands — a known location, not a guess.

    Named off the link's own opening stamp so a run that breaches twice does
    not overwrite its own first handoff, and so the filename sorts in the
    order the decisions were made.
    """
    if outbox_dir is None or not isinstance(row, dict):
        return None
    opened = row.get("opened_at")
    stamp = int(opened) if isinstance(opened, (int, float)) else 0
    slug = "".join(
        ch if ch.isalnum() else "-"
        for ch in str(row.get("intent") or "link").lower()
    ).strip("-")[:40] or "link"
    return Path(outbox_dir) / CUT_DIRNAME / f"{stamp}-{slug}.md"


def render_cut(
    row: dict[str, Any] | None,
    *,
    spend_now: int | None = None,
    rest: str | None = None,
) -> str:
    """The handoff a breached link projects: what, why, what it cost, where it stopped.

    Everything here except *rest* is already on the row — which is the point.
    A successor arriving at a ceiling someone else blew needs the shape of the
    decision, not a stack trace, and the shape was written down when the link
    was opened.

    *rest* is the one thing no record can supply: **what remains**.  Absent, the
    page says so in as many words rather than implying the work was finished —
    an unstated remainder read as completion is the failure this whole file is
    about, one level up.
    """
    row = row if isinstance(row, dict) else {}
    used = spent(row, spend_now=spend_now)
    ceiling = row.get("boundary")
    lines = [
        f"# cut — {row.get('intent') or 'unnamed link'}",
        "",
        f"why: {row.get('why') or '∅ — no reason was recorded'}",
        f"item: {row.get('item') or '∅ — bound to no warp item'}",
        f"declared: {_short(ceiling) if isinstance(ceiling, int) else '∅'}",
        f"spent: {_short(used) if used is not None else '∅ — unmeasurable'}",
        "",
    ]
    if rest:
        lines += ["## What remains", "", rest.strip(), ""]
    else:
        lines += [
            "## What remains",
            "",
            "**Not stated.** The ceiling was passed and the link closed without "
            "a remainder written, so this page carries the decision's shape and "
            "not its unfinished half. Treat the work as *open of unknown size*, "
            "never as done.",
            "",
        ]
    return "\n".join(lines)


def write_cut(
    outbox_dir: Path | None,
    row: dict[str, Any] | None,
    *,
    spend_now: int | None = None,
    rest: str | None = None,
) -> Path | None:
    """Write the handoff and return its path, or ``None`` when it cannot."""
    path = cut_path(outbox_dir, row)
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_cut(row, spend_now=spend_now, rest=rest), encoding="utf-8")
    except OSError:
        return None
    return path
