"""The warp item space, daemon half (2026-08-11 round).

One work item = one authored markdown file ``surface/warp/<id>.md``; one
topic = one file ``surface/topics/<slug>.md``. Topics are properties items
wear (the filter axis), never storage roots — this module supersedes the
layer-file convention (``surface/layers/``) the weld used to write into.

The grammar is the frontend's (``src/frontend/src/lib/warpGraph.ts``), one
computation in two languages, deliberately kept in lockstep:

- one ``# `` title line → the headline
- a contiguous recognized-row block: ``type:`` ``topics:`` ``needs:``
  ``done:`` ``retired:`` ``refs:`` ``prompt:`` ``taken:``
- everything after the first unrecognized line is the body, never parsed

Lifecycle is **derived, never authored**: a ``done:`` row makes an item
done, a ``retired:`` row retires it, absence is open. There is no
``state:`` row on purpose — the receipt row *is* the state, one fact in
one place. Blocked/ready likewise derive from the ``needs:`` edges.

Item files are authored surface (both hands hold the pen), so every edit
here is minimal and row-scoped — never a rewrite of prose. Surface
commits ride the existing capture net (``daemon._capture_dominion``);
this module never commits or pushes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import account

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .account import AccountContext

WARP_DIRNAME = "warp"
TOPICS_DIRNAME = "topics"

#: Any slug-shaped basename is a legal item id (a hand-authored name is an
#: item, not a silent skip); the *allocator* only ever mints ``w-<N>``.
ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ALLOCATED_ID_RE = re.compile(r"^w-(\d+)$")

ITEM_TYPES = ("decision", "preparation", "action")

_TITLE_RE = re.compile(r"^#[ \t]+(.*)$")
_ROW_RE = re.compile(r"^(type|topics|needs|done|retired|refs|prompt|taken):[ \t]*(.*)$")

#: Scan grammar for free text (event bodies): allocated ids are safe as
#: bare tokens (``w-42`` collides with nothing English); a hand-named item
#: is addressed explicitly on its own ``item: <id>`` line — the same line
#: the dashboard's copy-prompt affordance appends.
_SCAN_TOKEN_RE = re.compile(r"(?<![\w/-])(w-\d+)(?![\w-])")
_SCAN_LINE_RE = re.compile(r"^item:[ \t]*([a-z0-9][a-z0-9-]*)[ \t]*$", re.MULTILINE)


@dataclass
class WarpItem:
    """One parsed item file. ``state`` is derived — see the module doc."""

    id: str
    path: Path
    headline: str
    type: str | None
    topics: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    taken: list[str] = field(default_factory=list)
    done: str | None = None
    retired: str | None = None
    refs: str = ""
    prompt: str | None = None
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
        type=item_type if item_type in ITEM_TYPES else None,
        topics=_split_ids(rows.get("topics", "")),
        needs=_split_ids(rows.get("needs", "")),
        taken=_split_ids(rows.get("taken", "")),
        done=rows.get("done"),
        retired=rows.get("retired"),
        refs=rows.get("refs", ""),
        prompt=rows.get("prompt") or None,
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

    def key(item: WarpItem) -> tuple:
        match = ALLOCATED_ID_RE.fullmatch(item.id)
        return (0, int(match.group(1))) if match else (1, item.id)

    items.sort(key=key)
    return items


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
    seen: list[str] = []
    for match in _SCAN_TOKEN_RE.finditer(text or ""):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    for match in _SCAN_LINE_RE.finditer(text or ""):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def allocate_id(warp_root: Path) -> str:
    """The next never-used allocated id (``w-<N>``), scanning every file —
    including done/retired ones — so an id is never reused."""
    highest = 0
    if warp_root.is_dir():
        for path in warp_root.glob("*.md"):
            match = ALLOCATED_ID_RE.fullmatch(path.stem)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"w-{highest + 1}"


def new_item_text(
    headline: str,
    *,
    item_type: str,
    topics: list[str] | None = None,
    needs: list[str] | None = None,
    prompt: str | None = None,
    refs: str | None = None,
    body: str | None = None,
) -> str:
    """Serialize a fresh item file in the canonical row order."""
    lines = [f"# {headline}", ""]
    lines.append(f"type: {item_type}")
    if topics:
        lines.append(f"topics: {' '.join(topics)}")
    if needs:
        lines.append(f"needs: {' '.join(needs)}")
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


_TYPE_MARK = {"decision": "◆", "preparation": "◇", "action": "●", None: "▫"}
_TYPE_ORDER = {"decision": 0, "preparation": 1, "action": 2, None: 3}


def render_index(
    warp_root: Path | None,
    *,
    done_tail: int = 5,
) -> str | None:
    """The compact open-items index a wake carries in place of the item
    pages — one line per open item, ready before held, decisions first;
    a short done-tail for continuity. ``None`` when there is no warp."""
    items = load_items(warp_root)
    if not items:
        return None
    by_id = {item.id: item for item in items}
    open_items = [item for item in items if item.state == "open"]

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

    def order(item: WarpItem) -> tuple:
        return (_TYPE_ORDER[item.type], item.id)

    ready = sorted(
        (item for item in open_items if not open_blockers(item, by_id)), key=order
    )
    held = sorted(
        (item for item in open_items if open_blockers(item, by_id)), key=order
    )
    out: list[str] = []
    if ready:
        out.append("ready:")
        out.extend(line(item) for item in ready)
    if held:
        out.append("held:")
        out.extend(line(item) for item in held)
    finished = [item for item in items if item.state == "done" and item.done]
    if finished:
        finished.sort(key=lambda item: item.done or "", reverse=True)
        tail = " · ".join(
            f"{item.id} ({(item.done or '').split(' ')[0]})"
            for item in finished[:done_tail]
        )
        out.append(f"done recently: {tail}")
    return "\n".join(out) if out else None
