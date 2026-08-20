"""The warp item space, daemon half (2026-08-11 round; ``goal`` node kind
added 2026-08-12, ``design-goal-oriented-engineering.md``).

One work item = one authored markdown file ``surface/warp/<id>.md``; one
topic = one file ``surface/topics/<slug>.md``. Topics are properties items
wear (the filter axis), never storage roots — this module supersedes the
layer-file convention (``surface/layers/``) the weld used to write into.

The grammar is the frontend's (``src/frontend/src/lib/warpGraph.ts``), one
computation in two languages, deliberately kept in lockstep:

- one ``# `` title line → the headline
- a contiguous recognized-row block: ``type:`` ``topics:`` ``needs:``
  ``advances:`` ``metric:`` ``target:`` ``horizon:`` ``done:`` ``retired:``
  ``refs:`` ``prompt:`` ``taken:``
- everything after the first unrecognized line is the body, never parsed

Lifecycle is **derived, never authored**: a ``done:`` row makes an item
done, a ``retired:`` row retires it, absence is open. There is no
``state:`` row on purpose — the receipt row *is* the state, one fact in
one place. Blocked/ready likewise derive from the ``needs:`` edges.

**The goal node kind** (``type: goal``) is the same file grammar, one more
legal ``type:`` value, allocated from its own ``g-<N>`` counter instead of
``w-<N>``. Goals carry three more free-text rows (``metric:`` ``target:``
``horizon:`` — no parsing beyond the row grammar, per the design) and, like
any item, may carry ``advances:`` (the same list grammar as ``needs:``,
naming the goal ids this item advances — legal on a goal itself, for
sub-goals, though nothing gives that case special treatment yet). A goal's
*contributing cone* and *blockers-on-you* are derived from ``advances:`` +
``needs:`` (``contributing_cone`` / ``blockers_on_you`` below) — never
authored, same rule as blocked/ready. Goals are not items in the
ready/held sense: ``render_index`` and the CLI list them in their own
band, never folded into the dispatchable bands.

Item files are authored surface (both hands hold the pen), so every edit
here is minimal and row-scoped — never a rewrite of prose. Surface
commits ride the existing capture net (``daemon._capture_dominion``);
this module never commits or pushes.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import account

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .account import AccountContext

WARP_DIRNAME = "warp"
TOPICS_DIRNAME = "topics"
#: A goal's readings store, beside its item file: ``g-<N>.readings.jsonl``.
#: Append-only, one JSON object per line — no schema migration, a goal
#: without readings is simply unread (design-goal-oriented-engineering.md
#: §"a metrics block in the wake"). See ``append_reading``/``load_readings``.
READINGS_SUFFIX = ".readings.jsonl"

#: Any slug-shaped basename is a legal item id (a hand-authored name is an
#: item, not a silent skip); the *allocator* only ever mints ``w-<N>`` (or
#: ``g-<N>`` for goals — see ``allocate_id``).
ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ALLOCATED_ID_RE = re.compile(r"^w-(\d+)$")
#: Goal ids, minted by the same allocator under a separate counter — a
#: goal never collides with an item's ``w-<N>`` regardless of mint order.
GOAL_ID_RE = re.compile(r"^g-(\d+)$")
#: Either allocated shape, prefix captured — the shared ordering key
#: ``load_items``/``render_index`` use so goals sort numerically among
#: themselves instead of falling into the lexical "named id" bucket.
_ANY_ALLOCATED_ID_RE = re.compile(r"^(w|g)-(\d+)$")

ITEM_TYPES = ("decision", "preparation", "action")
#: The one node kind outside the three above — user-declared, its own id
#: space, its own render band. ``type: goal`` is otherwise the same row
#: grammar; see the module docstring.
GOAL_TYPE = "goal"
ALL_TYPES = ITEM_TYPES + (GOAL_TYPE,)

_TITLE_RE = re.compile(r"^#[ \t]+(.*)$")
_ROW_RE = re.compile(
    r"^(type|topics|needs|advances|done|retired|refs|prompt|taken"
    r"|metric|target|horizon):[ \t]*(.*)$"
)

#: Scan grammar for free text (event bodies): allocated ids are safe as
#: bare tokens (``w-42`` collides with nothing English); a hand-named item
#: is addressed explicitly on its own ``item: <id>`` line — the same line
#: the dashboard's copy-prompt affordance appends.
#:
#: `/` is genuinely ambiguous and is the whole reason bare-regex boundary
#: exclusion (#1436 facet 2's first pass) doesn't work: it is both the
#: enumeration separator (`w-45/w-46/w-47`, which must widen) *and* the
#: path separator this system's own vocabulary uses for an item's real
#: file (`surface/warp/w-45.md` — the exact shape the dispatching
#: schedule entry and the issue's own spec use to refer to an item, so
#: this is not a hypothetical). Excluding `/` outright (the pre-#1436
#: shape) broke the enumeration; allowing it outright (facet 2's first
#: landed fix) re-opened ignition on a correspondent typing a path — a
#: regression a follow-up review caught by running the scanner against
#: this system's own reference shape, not by re-reading the regex.
#:
#: The discriminator, `_scan_bare_tokens` below: a leading `/` is a
#: separator *only when the id immediately before it was itself accepted*
#: (`w-45/w-46` — the first `w-45` stands on its own, so the second reads
#: as an enumeration member); otherwise it is a path segment
#: (`surface/warp/w-45.md`, `kb/w-45.md`, `https://x/w-12` — nothing
#: before the `/` was itself an accepted id) and the candidate is
#: rejected regardless of what follows. Independently, a trailing
#: `.<ext>`-shaped suffix (dot, then a letter, then a short alnum run —
#: `.md`, `.py`, `.jsonl`, not `.2`/`.45`, which read as a decimal, not an
#: extension) always disqualifies: a filename never becomes an item id no
#: matter how it was reached. This also flips `w-45.md` (no path prefix)
#: from matching to not — it matched before this change (the lookahead
#: never excluded `.`), but a "sometimes filename, sometimes id" split
#: that depends only on an accidental leading path segment is worse than
#: one consistent rule, and the corpus this scan actually reads
#: (schedule agendas, correspondent messages, spec bodies) never refers
#: to an item by a bare `<id>.md` with intent to ignite it — every real
#: reference either omits the extension or carries the `surface/warp/`
#: prefix this rule already excludes.
#:
#: `\w` and `-` stay excluded on both sides — what still blocks an id
#: embedded in a longer token (`xw-45`, `w-450`). Left deliberately
#: unhandled: a shorthand enumeration that drops the repeated prefix
#: (`w-45/46/47` matching only `w-45`) — nothing in the issue or its
#: acceptance list asks for it, and guessing a prefix onto a bare number
#: is exactly the kind of guess this module's docstring says an id is
#: never subject to.
_SCAN_CANDIDATE_RE = re.compile(r"w-\d+")
_SCAN_LINE_RE = re.compile(r"^item:[ \t]*([a-z0-9][a-z0-9-]*)[ \t]*$", re.MULTILINE)
#: A trailing filename-extension shape: dot, a letter, then a short
#: alnum run, not itself followed by more word chars or another dot (so
#: `.md` qualifies; `.2`, `.45` — a decimal, not an extension — do not,
#: since they start with a digit rather than a letter).
_FILENAME_EXT_RE = re.compile(r"^\.[A-Za-z][A-Za-z0-9]{0,7}(?![\w.])")


def _scan_bare_tokens(text: str) -> list[str]:
    """Bare ``w-<N>`` ids in *text*, unique, first-mention order — the
    boundary-aware half of :func:`scan_item_ids`. See the comment above
    ``_SCAN_CANDIDATE_RE`` for the discriminator this implements; a single
    lookaround regex cannot express it because whether a leading ``/`` is
    a separator depends on whether the *previous* candidate was itself
    accepted — a variable-width condition Python's ``re`` lookbehind
    cannot carry, so this scans candidates left to right instead and
    threads that one bit of state (``last_accepted_end``) through.
    """
    seen: list[str] = []
    last_accepted_end: int | None = None
    for match in _SCAN_CANDIDATE_RE.finditer(text):
        start, end = match.span()
        prev_char = text[start - 1] if start > 0 else ""
        if prev_char and (prev_char.isalnum() or prev_char in "_-"):
            continue  # embedded in a longer token, e.g. `xw-45`
        if prev_char == "/" and last_accepted_end != start - 1:
            continue  # a path segment, e.g. `surface/warp/w-45.md`
        next_char = text[end] if end < len(text) else ""
        if next_char and (next_char.isalnum() or next_char in "_-"):
            continue  # embedded in a longer token, e.g. `w-450`, `w-45-notes`
        if next_char == "." and _FILENAME_EXT_RE.match(text[end:]):
            continue  # a filename, e.g. `w-45.md`
        last_accepted_end = end
        token = match.group(0)
        if token not in seen:
            seen.append(token)
    return seen


@dataclass
class WarpItem:
    """One parsed item file. ``state`` is derived — see the module doc."""

    id: str
    path: Path
    headline: str
    type: str | None
    topics: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    #: Goal ids this item advances (same list grammar as ``needs``). Legal
    #: on any item, including a goal itself (a sub-goal edge) — see the
    #: module docstring for what that case does and does not do yet.
    advances: list[str] = field(default_factory=list)
    taken: list[str] = field(default_factory=list)
    done: str | None = None
    retired: str | None = None
    refs: str = ""
    prompt: str | None = None
    #: Goal-only free-text rows (design's own words: "no parsing beyond
    #: the row grammar"). ``None`` when absent, on any item type — nothing
    #: here enforces they only appear on a ``goal``.
    metric: str | None = None
    target: str | None = None
    horizon: str | None = None
    body: str = ""

    @property
    def state(self) -> str:
        if self.done is not None:
            return "done"
        if self.retired is not None:
            return "retired"
        return "open"


def warp_dir(ctx: "AccountContext | None") -> Path | None:
    """The account's ``surface/warp/`` directory, or ``None`` when there is
    no enabled account home or no item has ever been authored."""
    if ctx is None or not getattr(ctx, "enabled", False):
        return None
    path = account.work_surface_path(ctx) / WARP_DIRNAME
    return path if path.is_dir() else None


def topics_dir(ctx: "AccountContext | None") -> Path | None:
    if ctx is None or not getattr(ctx, "enabled", False):
        return None
    path = account.work_surface_path(ctx) / TOPICS_DIRNAME
    return path if path.is_dir() else None


def _split_ids(value: str) -> list[str]:
    return [part for part in re.split(r"[\s·]+", value.strip()) if part]


def _rows_span(lines: list[str]) -> tuple[int, int]:
    """``(start, end)`` of the recognized-row block: after the title line
    and its blank separator, through every contiguous recognized row.
    ``start == end`` when the file has no rows yet — the insertion point."""
    i = 0
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i < len(lines) and _TITLE_RE.match(lines[i]):
        i += 1
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    start = i
    while i < len(lines) and _ROW_RE.match(lines[i]):
        i += 1
    return start, i


def parse_item(path: Path) -> WarpItem | None:
    """Parse one item file; ``None`` on an unreadable file or illegal id."""
    item_id = path.stem
    if item_id == "index" or not ITEM_ID_RE.fullmatch(item_id):
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.replace("\r\n", "\n").split("\n")
    headline = item_id
    for line in lines:
        match = _TITLE_RE.match(line)
        if match:
            headline = match.group(1).strip() or item_id
            break
        if line.strip():
            break
    start, end = _rows_span(lines)
    rows: dict[str, str] = {}
    for line in lines[start:end]:
        match = _ROW_RE.match(line)
        if match and match.group(1) not in rows:
            rows[match.group(1)] = match.group(2).strip()
    body = "\n".join(lines[end:]).strip()
    item_type = rows.get("type", "").lower()
    return WarpItem(
        id=item_id,
        path=path,
        headline=headline,
        type=item_type if item_type in ALL_TYPES else None,
        topics=_split_ids(rows.get("topics", "")),
        needs=_split_ids(rows.get("needs", "")),
        advances=_split_ids(rows.get("advances", "")),
        taken=_split_ids(rows.get("taken", "")),
        done=rows.get("done"),
        retired=rows.get("retired"),
        refs=rows.get("refs", ""),
        prompt=rows.get("prompt") or None,
        metric=rows.get("metric") or None,
        target=rows.get("target") or None,
        horizon=rows.get("horizon") or None,
        body=body,
    )


def load_items(warp_root: Path | None) -> list[WarpItem]:
    """Every parseable item in the warp, numeric-aware id order."""
    if warp_root is None or not warp_root.is_dir():
        return []
    items: list[WarpItem] = []
    for path in sorted(warp_root.glob("*.md")):
        if path.is_symlink():
            continue
        item = parse_item(path)
        if item is not None:
            items.append(item)

    items.sort(key=_id_sort_key)
    return items


def _id_sort_key(item: WarpItem) -> tuple:
    """Numeric-aware id order: ``w-<N>``/``g-<N>`` sort by prefix then
    number (so goals sort numerically among themselves too), any other
    slug-shaped id sorts lexically after, in its own bucket."""
    match = _ANY_ALLOCATED_ID_RE.fullmatch(item.id)
    if match:
        return (0, match.group(1), int(match.group(2)))
    return (1, item.id, 0)


def resolve_item(warp_root: Path | None, item_id: str) -> Path | None:
    """The item file an id resolves to, or ``None`` — never guessed."""
    if warp_root is None or not ITEM_ID_RE.fullmatch(item_id or ""):
        return None
    path = warp_root / f"{item_id}.md"
    return path if path.is_file() and not path.is_symlink() else None


def scan_item_ids(text: str) -> list[str]:
    """Candidate item ids in free text, unique, first-mention order.

    Two doors: bare ``w-<N>`` tokens anywhere, and explicit ``item: <id>``
    lines (any legal id — the address line the copy-prompt affordance
    appends). Grammar-level only; resolution is the caller's second gate.
    """
    seen = _scan_bare_tokens(text or "")
    for match in _SCAN_LINE_RE.finditer(text or ""):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def allocate_id(warp_root: Path, item_type: str | None = None) -> str:
    """The next never-used allocated id, scanning every file — including
    done/retired ones — so an id is never reused. ``item_type == "goal"``
    mints off the separate ``g-<N>`` counter; every other type (including
    the default) keeps the original ``w-<N>`` counter, so the two spaces
    never collide regardless of mint order."""
    id_re = GOAL_ID_RE if item_type == GOAL_TYPE else ALLOCATED_ID_RE
    prefix = "g" if item_type == GOAL_TYPE else "w"
    highest = 0
    if warp_root.is_dir():
        for path in warp_root.glob("*.md"):
            match = id_re.fullmatch(path.stem)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1}"


def new_item_text(
    headline: str,
    *,
    item_type: str,
    topics: list[str] | None = None,
    needs: list[str] | None = None,
    advances: list[str] | None = None,
    metric: str | None = None,
    target: str | None = None,
    horizon: str | None = None,
    prompt: str | None = None,
    refs: str | None = None,
    body: str | None = None,
) -> str:
    """Serialize a fresh item file in the canonical row order. ``metric``/
    ``target``/``horizon`` are the goal-only rows (free text, no parsing);
    ``advances`` is legal on any item type, including a goal (sub-goals)."""
    lines = [f"# {headline}", ""]
    lines.append(f"type: {item_type}")
    if topics:
        lines.append(f"topics: {' '.join(topics)}")
    if needs:
        lines.append(f"needs: {' '.join(needs)}")
    if advances:
        lines.append(f"advances: {' '.join(advances)}")
    if metric:
        lines.append(f"metric: {metric}")
    if target:
        lines.append(f"target: {target}")
    if horizon:
        lines.append(f"horizon: {horizon}")
    if refs:
        lines.append(f"refs: {refs}")
    if prompt:
        lines.append(f"prompt: {prompt}")
    if body:
        lines.extend(["", body.strip()])
    return "\n".join(lines) + "\n"


def _edit_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return None


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines), encoding="utf-8")


def _insert_row(lines: list[str], row: str) -> None:
    """Insert a row at the end of the recognized block, keeping the body
    visually separated when it follows with no blank line of its own."""
    _, end = _rows_span(lines)
    block = [row]
    if end < len(lines) and lines[end].strip():
        block.append("")
    lines[end:end] = block


def _append_to_list_row(lines: list[str], key: str, value: str) -> bool:
    """Append *value* to the ``<key>:`` row, idempotently; insert the row
    when absent. Returns whether the lines changed."""
    row_re = re.compile(rf"^{key}:[ \t]*(.*)$")
    start, end = _rows_span(lines)
    for i in range(start, end):
        match = row_re.match(lines[i])
        if match:
            parts = match.group(1).split()
            if value in parts:
                return False
            lines[i] = f"{key}: " + " ".join([*parts, value])
            return True
    _insert_row(lines, f"{key}: {value}")
    return True


def mark_taken(path: Path, run_id: str) -> bool:
    """Add ``taken: <run_id>``, idempotently. Returns whether the file changed."""
    lines = _edit_lines(path)
    if lines is None:
        return False
    if _append_to_list_row(lines, "taken", run_id):
        _write_lines(path, lines)
        return True
    return False


def mark_done(path: Path, *, date: str, run_id: str | None = None) -> bool:
    """Stamp the completion receipt. Refuses an item already done or
    retired — a second receipt would silently rewrite history."""
    item = parse_item(path)
    if item is None or item.state != "open":
        return False
    lines = _edit_lines(path)
    if lines is None:
        return False
    value = f"done: {date}" + (f" {run_id}" if run_id else "")
    _insert_row(lines, value)
    _write_lines(path, lines)
    return True


def mark_retired(path: Path, *, date: str, why: str | None = None) -> bool:
    item = parse_item(path)
    if item is None or item.state != "open":
        return False
    lines = _edit_lines(path)
    if lines is None:
        return False
    value = f"retired: {date}" + (f" {why}" if why else "")
    _insert_row(lines, value)
    _write_lines(path, lines)
    return True


def append_refs(path: Path, refs: list[str]) -> list[str]:
    """Append qualified forge refs onto the ``refs:`` row, deduped against
    both the qualified grammar and equivalent forge links. Returns the refs
    actually added."""
    lines = _edit_lines(path)
    if lines is None:
        return []
    row_re = re.compile(r"^refs:[ \t]*(.*)$")
    start, end = _rows_span(lines)
    refs_idx = None
    for i in range(start, end):
        if row_re.match(lines[i]):
            refs_idx = i
            break
    row_value = row_re.match(lines[refs_idx]).group(1) if refs_idx is not None else ""  # type: ignore[union-attr]
    missing = [ref for ref in refs if not _ref_present(row_value, ref)]
    if not missing:
        return []
    if refs_idx is not None:
        current = row_value.strip()
        parts = ([current] if current else []) + missing
        lines[refs_idx] = "refs: " + " · ".join(parts)
    else:
        _insert_row(lines, "refs: " + " · ".join(missing))
    _write_lines(path, lines)
    return missing


_QUALIFIED_REF_RE = re.compile(r"^([\w.-]+/[\w.-]+)#(\d+)$")


def _ref_present(row_value: str, ref: str) -> bool:
    qualified = _QUALIFIED_REF_RE.match(ref)
    if qualified is None:
        return ref in row_value
    repo, number = qualified.groups()
    pattern = re.compile(
        re.escape(repo) + "#" + number + r"(?!\d)"
        + "|" + re.escape(repo) + r"/(?:issues|pull)/" + number + r"(?!\d)"
    )
    return bool(pattern.search(row_value))


# ── the wake slice: the graph as an index, never the pages ────────────────


def open_blockers(item: WarpItem, by_id: dict[str, WarpItem]) -> list[str]:
    """Ids of needed items that exist and are still open. A dangling id
    never blocks — a deleted blocker frees its dependents, and the drift
    audit (not a silent hold) is what catches a typo."""
    out = []
    for needed in item.needs:
        other = by_id.get(needed)
        if other is not None and other.state == "open":
            out.append(needed)
    return out


def contributing_cone(goal_id: str, items: list[WarpItem]) -> list[WarpItem]:
    """A goal's contributing cone, derived — never authored (the design's
    own rule): every item that directly ``advances:`` this goal id, plus
    the transitive ``needs:`` closure of those items. An item advancing a
    *different* goal that happens to be itself a sub-goal of this one is
    **not** pulled in — ``advances:`` on a goal is legal grammar (sub-
    goals) but nothing gives it special recursive treatment yet, per the
    design's own "nothing renders it specially yet." Numeric-aware id
    order, deterministic for a given graph."""
    by_id = {item.id: item for item in items}
    cone_ids: set[str] = {item.id for item in items if goal_id in item.advances}
    frontier = list(cone_ids)
    while frontier:
        current = by_id.get(frontier.pop())
        if current is None:
            continue
        for needed in current.needs:
            if needed in by_id and needed not in cone_ids:
                cone_ids.add(needed)
                frontier.append(needed)
    return sorted((by_id[i] for i in cone_ids), key=_id_sort_key)


def blockers_on_you(goal_id: str, items: list[WarpItem]) -> list[WarpItem]:
    """The callback channel for one goal — a *query, not a list*: every
    open decision/preparation item inside its contributing cone. Nobody
    curates this; it falls out of the cone the same way blocked/ready
    falls out of ``needs:``."""
    return [
        item
        for item in contributing_cone(goal_id, items)
        if item.state == "open" and item.type in ("decision", "preparation")
    ]


_TYPE_MARK = {"decision": "◆", "preparation": "◇", "action": "●", None: "▫"}
_TYPE_ORDER = {"decision": 0, "preparation": 1, "action": 2, None: 3}


def render_index(
    warp_root: Path | None,
    *,
    done_tail: int = 5,
) -> str | None:
    """The compact open-items index a wake carries in place of the item
    pages — goals first (their own band, never folded into ready/held: a
    goal is a container, not a dispatchable/decidable item), then one line
    per open item, ready before held, decisions first, plus a short
    done-tail for continuity. ``None`` when there is no warp."""
    items = load_items(warp_root)
    if not items:
        return None
    by_id = {item.id: item for item in items}
    goals = [item for item in items if item.type == GOAL_TYPE and item.state == "open"]
    open_items = [
        item for item in items if item.state == "open" and item.type != GOAL_TYPE
    ]

    def line(item: WarpItem) -> str:
        parts = [f"- {item.id} {_TYPE_MARK[item.type]} {item.type or 'untyped'}"]
        if item.topics:
            parts.append("· " + " ".join(item.topics))
        blockers_now = open_blockers(item, by_id)
        if blockers_now:
            parts.append("· needs " + " ".join(blockers_now))
        parts.append(f"— {item.headline}")
        if item.taken:
            parts.append(f"[taken: {' '.join(item.taken[-2:])}]")
        return " ".join(parts)

    def goal_line(goal: WarpItem) -> str:
        parts = [f"- {goal.id} ◎ goal — {goal.headline}"]
        spine = " ".join(
            f"{key}: {value}"
            for key, value in (
                ("metric", goal.metric),
                ("target", goal.target),
                ("horizon", goal.horizon),
            )
            if value
        )
        if spine:
            parts.append(f"[{spine}]")
        callback = blockers_on_you(goal.id, items)
        if callback:
            parts.append("· needs-you " + " ".join(item.id for item in callback))
        line = " ".join(parts)
        readings = readings_index_line(goal.id, warp_root)
        if readings:
            line += "\n  " + readings
        return line

    def order(item: WarpItem) -> tuple:
        return (_TYPE_ORDER[item.type], _id_sort_key(item))

    ready = sorted(
        (item for item in open_items if not open_blockers(item, by_id)), key=order
    )
    held = sorted(
        (item for item in open_items if open_blockers(item, by_id)), key=order
    )
    out: list[str] = []
    if goals:
        out.append("goals:")
        out.extend(goal_line(goal) for goal in sorted(goals, key=_id_sort_key))
    if ready:
        out.append("ready:")
        out.extend(line(item) for item in ready)
    if held:
        out.append("held:")
        out.extend(line(item) for item in held)
    finished = [
        item
        for item in items
        if item.state == "done" and item.done and item.type != GOAL_TYPE
    ]
    if finished:
        finished.sort(key=lambda item: item.done or "", reverse=True)
        tail = " · ".join(
            f"{item.id} ({(item.done or '').split(' ')[0]})"
            for item in finished[:done_tail]
        )
        out.append(f"done recently: {tail}")
    return "\n".join(out) if out else None


# ── goal readings store ─────────────────────────────────────────────────
#
# "a metrics block in the wake — the goal's current numbers injected as
# perception, not polled per run" (design-goal-oriented-engineering.md).
# One append-only file per goal, beside its item file: ``g-<N>.readings.jsonl``,
# one JSON object per line — never parsed as item-file row grammar, never
# migrated. A goal with no readings file is simply unread, not an error.


@dataclass(frozen=True)
class Reading:
    """One parsed sample line. ``note`` is ``None`` when the line omitted it.

    ``basis`` names the measurement population a value was drawn from — a
    5-item rolling window vs a lifetime sum, replies-included vs
    replies-excluded, whatever makes two same-``key`` values arithmetically
    incompatible. It is distinct from ``source`` (the transport/collector
    that produced the number): two readings can share a source and still
    measure different populations (issue: a lifetime sum and a 5-item
    window both recorded as ``source: x-api``), so ``source`` alone cannot
    gate a delta. ``None`` when the writer didn't set one — comparisons then
    fall back to ``source`` (see ``reading_basis``), which is a superset of
    the old, no-``basis`` behaviour, not a new default that reopens old
    data. A caller wanting a delta to render must give both readings the
    same explicit ``basis`` whenever they are not already drawn from the
    same collector."""

    ts: str
    key: str
    value: float
    source: str
    note: str | None = None
    basis: str | None = None


@dataclass(frozen=True)
class ReadingWithdrawal:
    """One append-only tombstone for a sample identified by key + timestamp."""

    ts: str
    key: str
    withdrawn_ts: str
    why: str


ReadingRow = Reading | ReadingWithdrawal


def reading_basis(reading: Reading) -> str:
    """The value two readings must share for a Δ between them to be
    constructible. Explicit ``basis`` wins; absent one, ``source`` is the
    best available population signal — it's what already separated the
    ``x-api`` / ``local-post-log`` post-count crossover, and it costs
    nothing for a writer who never set ``basis`` to keep comparing the way
    they always did."""
    return reading.basis if reading.basis else reading.source


def _now_iso() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def reading_ts_order_key(ts: str) -> str:
    """Order two reading stamps that may differ in fractional-second width.

    Readings were whole-second (``…:56Z``) until withdrawal handles needed
    to address one sample unambiguously, and are microsecond (``…:56.4Z``)
    after.  A raw string sort mixes the two wrongly: ``.`` (0x2E) sorts
    before ``Z`` (0x5A), so a *later* microsecond sample sorts *before* an
    earlier whole-second one recorded in the same second — and ``latest``
    is what every goal surface publishes.  Normalising the fraction to a
    fixed width makes the lexicographic order the chronological one again,
    with no parsing and no dependency on how the row was written.

    These stamps are always UTC-``Z``; an offset form would need real
    parsing, and none is ever written here.
    """
    head, dot, tail = ts.partition(".")
    if not dot:
        return head[:-1] + ".000000" if head.endswith("Z") else head + ".000000"
    return head + "." + tail.rstrip("Z").ljust(6, "0")[:6]


def _parse_iso(value: str) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def readings_path(warp_root: Path, goal_id: str) -> Path:
    """The readings file for *goal_id* — always beside the item file,
    regardless of whether either exists yet."""
    return warp_root / f"{goal_id}{READINGS_SUFFIX}"


def append_reading(
    warp_root: Path,
    goal_id: str,
    key: str,
    value: float,
    *,
    source: str = "",
    note: str | None = None,
    basis: str | None = None,
    ts: str | None = None,
) -> Reading:
    """Append one sample line; mints the file on first use. The caller
    resolves ``goal_id`` against a real goal first (``brnrd goal record``
    refuses an unknown id the way the item verbs do) — this function itself
    does not check, so it stays the one place both the CLI and tests can
    write a reading without round-tripping through argument parsing.

    ``basis`` — see ``Reading`` — is only written when given, same as
    ``note``, so a plain reading stays the same three-or-four-field line it
    always was."""
    reading = Reading(
        ts=ts or _now_iso(),
        key=key,
        value=float(value),
        source=source,
        note=note or None,
        basis=basis or None,
    )
    record = {"ts": reading.ts, "key": reading.key, "value": reading.value, "source": reading.source}
    if reading.note:
        record["note"] = reading.note
    if reading.basis:
        record["basis"] = reading.basis
    path = readings_path(warp_root, goal_id)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return reading


def append_reading_withdrawal(
    warp_root: Path,
    goal_id: str,
    key: str,
    withdrawn_ts: str,
    *,
    why: str,
    ts: str | None = None,
) -> ReadingWithdrawal:
    """Append a tombstone for one explicitly addressed sample."""
    withdrawal = ReadingWithdrawal(
        ts=ts or _now_iso(), key=key, withdrawn_ts=withdrawn_ts, why=why
    )
    record = {
        "ts": withdrawal.ts,
        "key": withdrawal.key,
        "withdrawn_ts": withdrawal.withdrawn_ts,
        "withdrawn": True,
        "why": withdrawal.why,
    }
    with readings_path(warp_root, goal_id).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return withdrawal


def load_readings(warp_root: Path, goal_id: str) -> list[ReadingRow]:
    """Every parseable sample or withdrawal for a goal, in file order.

    Withdrawal rows deliberately omit ``value``. Older loaders therefore
    skip them as non-samples while still parsing the file as valid JSONL.

    The file is append-only, so this
    is chronological unless hand-edited). A malformed line is skipped, not
    fatal — one bad line does not lose a goal's whole history, same stance
    ``parse_item`` takes on an unreadable file."""
    path = readings_path(warp_root, goal_id)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[ReadingRow] = []
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        try:
            if record.get("withdrawn") is True:
                why = str(record["why"]).strip()
                withdrawn_ts = str(record["withdrawn_ts"]).strip()
                if not why or not withdrawn_ts:
                    continue
                out.append(
                    ReadingWithdrawal(
                        ts=str(record["ts"]),
                        key=str(record["key"]),
                        withdrawn_ts=withdrawn_ts,
                        why=why,
                    )
                )
                continue
            out.append(
                Reading(
                    ts=str(record["ts"]),
                    key=str(record["key"]),
                    value=float(record["value"]),
                    source=str(record.get("source", "")),
                    note=(str(record["note"]) if record.get("note") else None),
                    basis=(str(record["basis"]) if record.get("basis") else None),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def active_readings(rows: list[ReadingRow]) -> list[Reading]:
    """Samples not superseded by a later withdrawal row."""
    withdrawn = {
        (row.key, row.withdrawn_ts)
        for row in rows
        if isinstance(row, ReadingWithdrawal)
    }
    return [
        row for row in rows
        if isinstance(row, Reading) and (row.key, row.ts) not in withdrawn
    ]


def reading_withdrawal_counts(rows: list[ReadingRow]) -> dict[str, int]:
    """Number of audit-trail withdrawal rows per metric key."""
    counts: dict[str, int] = {}
    for row in rows:
        if isinstance(row, ReadingWithdrawal):
            counts[row.key] = counts.get(row.key, 0) + 1
    return counts


@dataclass(frozen=True)
class ReadingSummary:
    """One key's summary — the shape both ``brnrd goal show`` and the
    ``/goals/[id]`` trajectory table's per-key line need.

    ``delta`` is ``None`` in two distinct situations a renderer must not
    conflate: no ``previous`` sample exists yet (nothing to diff against —
    ``basis_mismatch`` is ``False``), or a ``previous`` sample exists but
    measures a different population (``basis_mismatch`` is ``True`` — the
    comparison was refused, not merely unavailable)."""

    latest: Reading
    previous: Reading | None
    delta: float | None
    count: int
    min: float
    max: float
    basis_mismatch: bool = False


def reading_summary(readings: list[ReadingRow]) -> dict[str, ReadingSummary]:
    """Per key: latest sample, previous sample, delta, sample count, min,
    max — chronological by ``ts`` (a hand-edited out-of-order file sorts
    itself back into place here rather than trusting append order).

    A Δ is only constructed when ``latest`` and ``previous`` share a
    measurement basis (``reading_basis``) — grouping by ``key`` alone put
    a lifetime sum and a rolling-window sum, both keyed ``impressions``,
    into the same subtraction (issue: the wake rendered ``Δ-186`` across
    two denominators that were never comparable). A same-``key``,
    different-``basis`` pair now renders with ``delta=None`` and
    ``basis_mismatch=True`` instead of a number that looks real."""
    by_key: dict[str, list[Reading]] = {}
    for reading in active_readings(readings):
        by_key.setdefault(reading.key, []).append(reading)
    out: dict[str, ReadingSummary] = {}
    for key, samples in by_key.items():
        ordered = sorted(samples, key=lambda r: reading_ts_order_key(r.ts))
        latest = ordered[-1]
        previous = ordered[-2] if len(ordered) > 1 else None
        values = [r.value for r in ordered]
        comparable = previous is not None and reading_basis(previous) == reading_basis(latest)
        out[key] = ReadingSummary(
            latest=latest,
            previous=previous,
            delta=(latest.value - previous.value) if comparable else None,
            count=len(ordered),
            min=min(values),
            max=max(values),
            basis_mismatch=(previous is not None and not comparable),
        )
    return out


def format_value(value: float) -> str:
    """Compact numeric rendering — integers stay bare, fractions trim
    trailing zeros. Shared by the CLI and the wake-line composer so a
    number reads the same everywhere it appears."""
    if value == int(value):
        return str(int(value))
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def format_delta(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{format_value(value)}"


def _days_between(earlier: str, later: str) -> int:
    a, b = _parse_iso(earlier), _parse_iso(later)
    if a is None or b is None:
        return 0
    return max(0, round((b - a).total_seconds() / 86400))


#: Soft byte cap for the wake-line composer's readings row — the first key
#: always renders (a goal with one chunky reading still gets a line); later
#: keys stop being added once the running total would cross the cap, so the
#: overflow is a dropped trailing key, never a mid-key hard cut.
_READINGS_LINE_BUDGET = 200


def readings_index_line(goal_id: str, warp_root: Path | None) -> str | None:
    """The compact second line the wake's open-items index renders under a
    goal with readings — latest-per-key only, capped ~200B. ``None`` when
    the goal has no readings yet, or there is no warp root to read from."""
    if warp_root is None:
        return None
    readings = load_readings(warp_root, goal_id)
    if not readings:
        return None
    summary = reading_summary(readings)
    prefix = "readings:"
    used = len(prefix.encode("utf-8"))
    segments: list[str] = []
    for key in sorted(summary):
        info = summary[key]
        segment = f"{key} {format_value(info.latest.value)}"
        if info.delta is not None:
            days = _days_between(info.previous.ts, info.latest.ts)
            segment += f" (Δ{format_delta(info.delta)} since {days}d)"
        elif info.basis_mismatch:
            # Silence here reads as "no change to report" — the exact
            # surface-narrows-invisibly failure this repo already knows
            # (AGENTS.md "An analysis names its own edges"). A refused
            # comparison says so instead of just not showing a number.
            segment += " (Δ refused: basis differs)"
        joiner = " · " if segments else " "
        cost = len((joiner + segment).encode("utf-8"))
        if segments and used + cost > _READINGS_LINE_BUDGET:
            break
        used += cost
        segments.append(segment)
    return prefix + " " + " · ".join(segments)
