"""The list of asks — the warp read as the user's LRU (design-the-ask.md).

An ask *is* a warp item (``surface/warp/<id>.md``) carrying five more rows,
added lazily by the seat as it works: ``return:`` · ``stage:`` · ``touched:``
· ``says:`` (event ids) · ``attempts:`` (run ids), plus ``after:`` for a
sprouted chain. No migration: an item without them still lists, with the
fields blank. The row grammar itself is items.py's (``type:`` ``topics:``
``needs:`` ``advances:`` ``done:`` ``retired:`` ``refs:`` ``prompt:``
``taken:`` ``metric:`` ``target:`` ``horizon:``) plus the ask rows above —
recognized by trying the ask regex, then reusing ``items_mod._ROW_RE``
directly, so the two grammars can never drift apart the way two
hand-copied field lists would.

One core, two doors, the same rows either way:

- :func:`asks_from_files` — over ``(path, markdown)`` pairs, which is what
  the hosted dashboard holds (the corpus mirror, ``Account.surface_json``);
- :func:`list_asks` — over a surface repo on disk: the warp's item files,
  folded with say bindings from a captured run node (``runs_dir``) and a
  live, uncaptured outbox (``outbox_root``) — both optional. This is the
  door ``brnrd asks`` reads.

Touch time, best evidence combined (the newest of all of it, never a
first-match precedence — a later say outranks an earlier explicit row):
an explicit ``touched:`` row · an external touch map (the disk door's git
commit time, or a live outbox binding's own mtime) · the newest of the
item's says · the newest run id it names in ``taken:``/``attempts:`` · a
``done:``/``retired:`` date · else unknown, and an unknown touch sinks.

Rows ``.asks.jsonl`` binds (``do.append_ask``) reach the hosted door only
when a run node carries them (``runs/<slug>/<run>/asks.jsonl``); until the
closeout capture preserves that file the ``says`` come from the item's own
``says:`` row. The disk door also reads a live outbox's own binding file
directly (``.brr/outbox/<event>/.asks.jsonl``), so a say lands before its
run is ever captured. Never raises on a malformed file — a bad row is
skipped.

Goals (``type: goal``, or a ``g-<N>`` id) are counted apart while open —
never in the LRU — but a *closed* goal is a done row like any other item's:
"done rows sink below a rule; they never vanish" (design-the-ask.md
§"Done, reopened, linked") applies to every node kind.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import items as items_mod

DEFAULT_STALE_AFTER_DAYS = 60
STALE_CONFIG_KEY = "asks.stale_after_days"
TITLE_WIDTH = 72

_TITLE_RE = re.compile(r"^#[ \t]+(.*)$")
#: The ask-only rows — everything else recognized comes from reusing
#: ``items_mod._ROW_RE`` directly (see the module docstring): a superset
#: kept by composition, not by hand-copying items.py's field list.
#:
#: ``sign`` (design-the-ask.md §Build cut, step 3) — a callsign short
#: enough to say in chat (``accept mira``), resolved through
#: :func:`resolve_sign`. ``reroute`` (step 1) — the why text an inbound
#: ``reroute w-N: <why>`` directive stamps, written by
#: :func:`apply_inbound_directive`. Neither joins ``items_mod._ROW_RE``
#: (the frontend's ``warpGraph.ts`` grammar stays in lockstep with that one
#: alone — see items.py's module docstring); both are ask-only, same as
#: ``stage``/``says`` before them.
_ASK_ROW_RE = re.compile(
    r"^(return|stage|touched|says|attempts|after|receipt|sign|reroute):[ \t]*(.*)$"
)
_RUN_ID_RE = re.compile(r"^run-(\d{2})(\d{2})(\d{2})-(\d{2})(\d{2})")
_ITEM_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_WARP_PREFIX = "surface/warp/"
_GOAL_RE = re.compile(r"^g-\d+$")
#: Where a say binding can live on disk: a captured run node
#: (``runs/<repo>/<run>/{asks,.asks}.jsonl`` — both names have shipped)
#: or a live, uncaptured outbox (``outbox/<event>/.asks.jsonl``). Both
#: feed the same core through the ``files`` pairs — see
#: ``_says_from_run_files`` / ``_read_binding_files``.
ASKS_NAMES = ("asks.jsonl", ".asks.jsonl")


def stale_after_days(cfg: dict[str, Any] | None) -> int:
    raw = (cfg or {}).get(STALE_CONFIG_KEY)
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_STALE_AFTER_DAYS
    return days if days > 0 else DEFAULT_STALE_AFTER_DAYS


def _split(value: str) -> list[str]:
    return [part for part in re.split(r"[\s·,]+", value.strip()) if part]


def _parse_iso(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:  # a bare date, the `done:` row's usual shape
            parsed = _dt.datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def _run_time(run_id: str) -> _dt.datetime | None:
    match = _RUN_ID_RE.match(run_id)
    if not match:
        return None
    yy, mo, dd, hh, mi = (int(part) for part in match.groups())
    try:
        return _dt.datetime(2000 + yy, mo, dd, hh, mi, tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def _iso(value: _dt.datetime | None) -> str | None:
    return value.astimezone(_dt.timezone.utc).isoformat() if value else None


def _mtime(path: Path) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime, _dt.timezone.utc)
    except OSError:
        return None


def _clip(text: str, width: int = TITLE_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _parse_markdown(item_id: str, text: str) -> dict[str, Any]:
    lines = text.replace("\r\n", "\n").split("\n")
    title = item_id
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines):
        match = _TITLE_RE.match(lines[i])
        if match:
            title = match.group(1).strip() or item_id
            i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    rows: dict[str, str] = {}
    while i < len(lines):
        match = _ASK_ROW_RE.match(lines[i]) or items_mod._ROW_RE.match(lines[i])
        if not match:
            break
        rows.setdefault(match.group(1), match.group(2).strip())
        i += 1
    return {"title": title, "rows": rows}


def _says_from_run_files(files: Iterable[tuple[str, str]]) -> dict[str, list[dict[str, Any]]]:
    """``item id -> says`` from say-binding files mixed into *files*: a
    captured run node (``runs/<repo>/<run>/{asks,.asks}.jsonl``, whose path
    names the run so each say's ``at`` is the run's own start time), a
    live, uncaptured outbox (``outbox/<event>/.asks.jsonl``, whose path
    names no run — its say carries no ``at`` of its own; the disk door
    folds the binding file's mtime into ``touched`` instead, see
    ``list_asks`` / ``_read_binding_files``), or the account-level
    ``warp/.asks.jsonl`` an inbound ``accept``/``reroute`` directive writes
    (``asks.apply_inbound_directive``) — no run owns that row either, same
    ``at: None`` + mtime-touch treatment as the outbox case.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for path, text in files:
        parts = path.split("/")
        if len(parts) == 4 and parts[0] == "runs" and parts[3] in ASKS_NAMES:
            at = _iso(_run_time(parts[2]))
        elif len(parts) == 3 and parts[0] == "outbox" and parts[2] == ".asks.jsonl":
            at = None
        elif path == f"warp/{DIRECTIVES_CONTROL_NAME}":
            at = None
        else:
            continue
        for raw in text.splitlines():
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("event") and record.get("item"):
                # Rung 3 (design-the-ask.md §Build cut, step 2): a `--part`
                # bound alongside `--item` at reply time rides this same
                # `.asks.jsonl` row as `part`; the reader surfaces it as
                # `excerpt` — the wire word the console reads, the file word
                # the CLI writes, one join, no renaming pass over old rows
                # (a row with no `part` simply carries `excerpt: None`, same
                # as before this existed).
                part = record.get("part")
                out.setdefault(str(record["item"]), []).append(
                    {
                        "event": str(record["event"]), "at": at,
                        "excerpt": str(part) if part else None,
                    }
                )
    return out


def build_asks(
    files: Iterable[tuple[str, str]],
    *,
    touched: Mapping[str, str] | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """The whole payload from ``(path, markdown)`` pairs.

    Paths are corpus-relative (``surface/warp/w-42.md``) or bare item file
    names; anything not under the warp, or not slug-shaped, is skipped.
    ``touched`` maps item id -> ISO time (a disk door's git/mtime evidence;
    the corpus-mirror door has none, and passes nothing).
    Returns ``{"asks", "done", "goals", "stale_after_days"}``: open rows in
    LRU order (live before stale), done/retired apart (any node kind,
    including a closed goal — a done row never vanishes), open goals apart.
    """
    files = list(files)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    horizon = now - _dt.timedelta(days=stale_after_days)
    run_says = _says_from_run_files(files)
    asks: list[dict[str, Any]] = []
    done: list[dict[str, Any]] = []
    goals: list[dict[str, Any]] = []
    for path, text in files:
        if path.startswith(_WARP_PREFIX):
            rel = path[len(_WARP_PREFIX):]
        elif "/" not in path:
            rel = path
        else:
            continue
        if "/" in rel or not rel.endswith(".md"):
            continue
        item_id = rel[:-3]
        if item_id == "index" or not _ITEM_RE.fullmatch(item_id):
            continue
        parsed = _parse_markdown(item_id, text)
        rows = parsed["rows"]
        events = _split(rows.get("says", ""))
        says = [{"event": e, "at": None, "excerpt": None} for e in events]
        header_events = set(events)
        for extra in run_says.get(item_id, []):
            # Only dedupe against what the header already declares — two
            # binding rows naming the same event (a repeated `--item`
            # bind) are two says, not one; `brnrd asks` counts the acts.
            if extra["event"] not in header_events:
                says.append(extra)
        attempts = list(dict.fromkeys(_split(rows.get("attempts", "")) + _split(rows.get("taken", ""))))
        stamps = [
            _parse_iso(rows.get("touched")),
            _parse_iso((touched or {}).get(item_id)),
            *[_parse_iso(s["at"]) for s in says],
            *[_run_time(run) for run in attempts],
            _parse_iso(rows.get("done")),
            _parse_iso(rows.get("retired")),
        ]
        stamps = [s for s in stamps if s]
        last = max(stamps) if stamps else None
        is_retired = bool(rows.get("retired"))
        is_done = bool(rows.get("done")) or is_retired
        row = {
            "id": item_id,
            "title": parsed["title"],
            "type": (rows.get("type") or "").lower() or None,
            "return": rows.get("return") or None,
            "stage": rows.get("stage") or ("accepted" if is_done else None),
            "touched_at": _iso(last),
            "says": says,
            "attempts": attempts,
            "receipt": rows.get("receipt") or rows.get("refs") or None,
            "sign": rows.get("sign") or None,
            "topics": _split(rows.get("topics", "")),
            "stale": bool(not is_done and (last is None or last < horizon)),
            "done": is_done,
            "retired": is_retired,
            "after": (_split(rows.get("after", "")) or [None])[0],
        }
        is_goal = bool(_GOAL_RE.fullmatch(item_id) or row["type"] == "goal")
        if is_goal and not is_done:
            goals.append({
                "id": item_id,
                "title": row["title"],
                "touched_at": row["touched_at"],
                "metric": rows.get("metric") or None,
                "target": rows.get("target") or None,
                "horizon": rows.get("horizon") or None,
            })
            continue
        # A closed goal is not special-cased further — it lands in `done`
        # like any other closed item (design-the-ask.md: done rows never
        # vanish; the old hosted-door reader dropped them here instead).
        (done if is_done else asks).append(row)

    floor = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc).isoformat()

    def lru(row: dict[str, Any]) -> tuple[bool, str]:
        # stale rows sink below live ones; within a band newest touch first
        return (row["stale"], _invert(row["touched_at"] or floor))

    asks.sort(key=lru)
    done.sort(key=lambda row: _invert(row["touched_at"] or floor))
    return {
        "asks": asks,
        "done": done,
        "goals": sorted(goals, key=lambda g: _invert(g["touched_at"] or floor)),
        "stale_after_days": stale_after_days,
    }


def _invert(iso: str) -> str:
    """A sort key that orders ISO strings descending (newest first)."""
    return "".join(chr(0x10FFFF - ord(ch)) for ch in iso)


def asks_from_files(
    surface_files: Iterable[Mapping[str, Any]],
    *,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """The hosted door: the corpus mirror's ``{path, markdown, committed_at}``
    file list.

    ``committed_at`` (the publisher's git-time stamp, ``cloud_publisher.py``
    §``_corpus_payload``) becomes the same ``touched`` rung :func:`list_asks`
    already feeds :func:`build_asks` from its own ``git log`` — the one
    difference between the two doors is *who* runs git, not what the row
    means, so the hosted order matches ``brnrd asks`` on disk.
    """
    pairs: list[tuple[str, str]] = []
    touched: dict[str, str] = {}
    for f in surface_files:
        if not isinstance(f, Mapping):
            continue
        path = str(f.get("path", ""))
        pairs.append((path, str(f.get("markdown", ""))))
        committed_at = f.get("committed_at")
        if not committed_at or not path.startswith(_WARP_PREFIX) or not path.endswith(".md"):
            continue
        item_id = path[len(_WARP_PREFIX):-3]
        if _ITEM_RE.fullmatch(item_id):
            touched[item_id] = str(committed_at)
    return build_asks(pairs, touched=touched, stale_after_days=stale_after_days, now=now)


# ── the inbound directive: "accept w-N" / "reroute w-N: <why>" ────────────
#
# design-the-ask.md §Build cut, step 1. Parsed by the daemon at the point an
# inbound message becomes a pending event (``gates/cloud.py``'s ingest loop
# — the parse belongs where the event is *created*, so the row moves even
# when no run is awake to fold it in), never by a resident reading the
# event later — by the time a resident sees the event it has already
# reached the item file, and the resident's own reply is a second, ordinary
# act on top.

#: `accept w-N` / `reroute w-N: <why>` / the callsign form (`accept mira`),
#: first line only, case-insensitive. A bare `w-\d+` needs no existence
#: check to *parse* — that's :func:`resolve_item`'s job, applied by the
#: caller; a lowercase-letter run of 3-8 chars is read as a callsign and
#: resolved through :func:`resolve_sign` instead. Anything else on the
#: first line (a normal message, "I accept your offer", "reroute the
#: server") simply doesn't match — :func:`parse_accept_reroute` returns
#: ``None``, the common case, silently.
_ACCEPT_REROUTE_RE = re.compile(
    r"^(accept|reroute)\s+(w-\d+|[a-z]{3,8})(?::\s*(.+))?$", re.IGNORECASE,
)

#: The callsign shape a `sign:` row must have to ever be reachable through
#: the directive grammar above — its own callsign alternative
#: (``[a-z]{3,8}``), lifted out so ``brnrd item new --sign`` can refuse an
#: unreachable one (``w-1``, too short, a hyphen) before it is ever written,
#: rather than minting a row `accept`/`reroute` can never match.
SIGN_RE = re.compile(r"^[a-z]{3,8}$", re.IGNORECASE)


def parse_accept_reroute(body: str) -> tuple[str, str, str | None] | None:
    """The first non-blank line of *body*, matched against the accept/
    reroute grammar. Returns ``(verb, target, why)`` — *verb* lowercased
    (``"accept"``/``"reroute"``), *target* exactly as typed (a `w-N` id or a
    callsign, case preserved so :func:`resolve_sign` can fold case itself),
    *why* the text after the colon (``None`` when absent — legal for
    either verb, though only ``reroute`` writes it anywhere). ``None`` when
    the first line isn't this grammar at all."""
    first_line = next((ln.strip() for ln in (body or "").splitlines() if ln.strip()), "")
    match = _ACCEPT_REROUTE_RE.match(first_line)
    if not match:
        return None
    why = match.group(3).strip() if match.group(3) else None
    return match.group(1).lower(), match.group(2), (why or None)


def resolve_sign(warp_root: Path | None, sign: str) -> str | None:
    """The item id whose ``sign:`` row equals *sign*, case-insensitive exact
    match — never guessed, never fuzzy, same stance as
    ``items_mod.resolve_item``. The door ``accept <sign>``/``reroute
    <sign>: …`` resolves a callsign through. ``None`` when *warp_root* has
    no such row, doesn't exist, or *sign* is blank."""
    needle = (sign or "").strip().lower()
    if warp_root is None or not needle or not warp_root.is_dir():
        return None
    for path in sorted(warp_root.glob("*.md")):
        if path.is_symlink():
            continue
        item_id = path.stem
        if item_id == "index" or not _ITEM_RE.fullmatch(item_id):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        candidate = (_parse_markdown(item_id, text)["rows"].get("sign") or "").strip().lower()
        if candidate and candidate == needle:
            return item_id
    return None


def _combined_rows_span(lines: list[str]) -> tuple[int, int]:
    """Like ``items_mod._rows_span``, but the recognized set is the union of
    items.py's own row grammar and the ask-only rows (:data:`_ASK_ROW_RE`)
    — the same superset :func:`_parse_markdown` already reads. Needed
    because ``items_mod._rows_span`` alone stops at the first ask-only row
    (``stage:``, ``says:``, …), which would misplace an inserted row ahead
    of an item's existing ask rows instead of after them."""
    i = 0
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i < len(lines) and _TITLE_RE.match(lines[i]):
        i += 1
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    start = i
    while i < len(lines) and (_ASK_ROW_RE.match(lines[i]) or items_mod._ROW_RE.match(lines[i])):
        i += 1
    return start, i


def _ask_row_value(lines: list[str], key: str) -> str | None:
    row_re = re.compile(rf"^{key}:[ \t]*(.*)$")
    start, end = _combined_rows_span(lines)
    for i in range(start, end):
        match = row_re.match(lines[i])
        if match:
            return match.group(1).strip()
    return None


def _insert_ask_row(lines: list[str], row: str) -> None:
    _, end = _combined_rows_span(lines)
    block = [row]
    if end < len(lines) and lines[end].strip():
        block.append("")
    lines[end:end] = block


def _set_ask_row(lines: list[str], key: str, value: str) -> bool:
    """Set (overwrite) a single-value row, inserting it when absent.
    Returns whether the file changed."""
    row_re = re.compile(rf"^{key}:[ \t]*(.*)$")
    start, end = _combined_rows_span(lines)
    for i in range(start, end):
        if row_re.match(lines[i]):
            new_line = f"{key}: {value}"
            if lines[i] == new_line:
                return False
            lines[i] = new_line
            return True
    _insert_ask_row(lines, f"{key}: {value}")
    return True


def _append_to_ask_list_row(lines: list[str], key: str, value: str) -> bool:
    """Append *value* to the ``<key>:`` row's space-separated list,
    idempotently — same grammar ``items_mod._append_to_list_row`` uses for
    ``taken:``/``needs:``, reimplemented against :func:`_combined_rows_span`
    so it never lands ahead of an existing ask row."""
    row_re = re.compile(rf"^{key}:[ \t]*(.*)$")
    start, end = _combined_rows_span(lines)
    for i in range(start, end):
        match = row_re.match(lines[i])
        if match:
            parts = match.group(1).split()
            if value in parts:
                return False
            lines[i] = f"{key}: " + " ".join([*parts, value])
            return True
    _insert_ask_row(lines, f"{key}: {value}")
    return True


#: Same control filename ``do.ASKS_CONTROL_NAME`` uses for a run's own
#: outbox binding file — the inbound directive's rung-2 row lands beside
#: it, at the account level (``surface/warp/.asks.jsonl``), because at
#: ingestion time no run outbox exists yet to own it: the event has not
#: been dispatched to anyone, so "the run's `.asks.jsonl`" is not a
#: resolvable address yet — the account-level file is the only address
#: that already exists (design-the-ask.md §Build cut, step 1, "pick one and
#: say why").
DIRECTIVES_CONTROL_NAME = ".asks.jsonl"


def _append_directive_row(path: Path, event_id: str, item_id: str, verb: str) -> None:
    """Append one ``{"event", "item", "verb"}`` row, best-effort — same
    never-raise shape as ``do.append_ask``'s sibling writer for the run-side
    file; a bug here must not cost the item mutation that already landed."""
    record = {"event": event_id, "item": item_id, "verb": verb}
    try:
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def apply_inbound_directive(
    warp_root: Path | None,
    event_id: str,
    body: str,
    *,
    asks_jsonl_path: Path | None = None,
    date: str | None = None,
) -> dict[str, Any] | None:
    """Parse *body*'s first line as an accept/reroute directive and, when it
    resolves to a real item, apply it — the whole of design-the-ask.md
    §Build cut, step 1.

    Returns ``None`` when the first line isn't the grammar at all (an
    ordinary message — the overwhelmingly common case; no-op, silently, no
    caller-visible distinction from "nothing to do here"). Otherwise a
    dict: ``{"verb", "target", "item", "why"}`` on success (``item`` the
    resolved id), or ``{"verb", "target", "item": None, "error": "..."}``
    when the target doesn't resolve — an unknown ``w-N`` or an unrecognized
    callsign — which the caller turns into a daemon-side notice per the
    design ("unknown ⇒ no-op + a notice"); the item file is untouched
    either way in that branch.

    Idempotent (design's own requirement — a redelivered event must not
    double-stamp): detected via *event_id* already present in the item's
    ``says:`` row, in which case this returns the same success shape with
    ``idempotent: True`` and writes nothing further.

    Both verbs stamp *event_id* into ``says:``. ``accept`` additionally
    writes ``stage: accepted`` and ``done: <date>`` — the same write
    ``brnrd item done`` (``items_mod.mark_done``) makes; best-effort (an
    already-done/retired item simply keeps its existing receipt — the
    acceptance itself, ``stage: accepted``, still lands). ``reroute``
    writes ``stage: reshaped`` and, when *why* was given, a ``reroute:``
    row carrying it.
    """
    parsed = parse_accept_reroute(body)
    if parsed is None:
        return None
    verb, target, why = parsed
    if warp_root is None:
        return {"verb": verb, "target": target, "item": None, "error": "no warp"}
    item_id = (
        target if items_mod.ALLOCATED_ID_RE.fullmatch(target)
        else resolve_sign(warp_root, target)
    )
    path = items_mod.resolve_item(warp_root, item_id) if item_id else None
    if path is None:
        return {
            "verb": verb, "target": target, "item": None,
            "error": f"no item resolves {target!r} (not a known w-N id or sign)",
        }

    lines = items_mod._edit_lines(path)
    if lines is None:
        return {
            "verb": verb, "target": target, "item": item_id,
            "error": "item file unreadable",
        }
    already = event_id in (_ask_row_value(lines, "says") or "").split()
    if already:
        return {"verb": verb, "target": target, "item": item_id, "why": why, "idempotent": True}

    if verb == "accept":
        items_mod.mark_done(path, date=date or _today())
        lines = items_mod._edit_lines(path)  # re-read: mark_done wrote its own draft
        if lines is None:
            return {
                "verb": verb, "target": target, "item": item_id,
                "error": "item file unreadable after done: write",
            }
        _set_ask_row(lines, "stage", "accepted")
    else:
        _set_ask_row(lines, "stage", "reshaped")
        if why:
            _set_ask_row(lines, "reroute", why)
    _append_to_ask_list_row(lines, "says", event_id)
    items_mod._write_lines(path, lines)

    jsonl_path = asks_jsonl_path or (warp_root / DIRECTIVES_CONTROL_NAME)
    _append_directive_row(jsonl_path, event_id, item_id, verb)
    return {"verb": verb, "target": target, "item": item_id, "why": why}


def _today() -> str:
    return _dt.date.today().isoformat()


# ── Derived, not written: `stage:`/`attempts:`/`return:` folded from a
# run's own claim, never hand-typed (his 2026-09-22 steer, folding design-
# the-ask.md §Build cut step 4 in: "make in-hand derived, never typed —
# hand-written `stage: making` is a poor resident-facing design, prone to
# forgetfulness errors"). ``run_item.py`` is the read side (the `.item`
# control file, at the daemon's heartbeat cadence); these two functions are
# the write side, shared by both derivations below so the row grammar has
# one owner. ──────────────────────────────────────────────────────────────

#: The ask lifecycle, ranked (design-the-ask.md §The stages: heard →
#: understood → shaped → making → delivered → accepted | reshaped |
#: sprouted). ``None``/absent ranks below every named stage. Used only to
#: answer "has this ask reached at least X" so a derived write never moves
#: a stage *backward* — never to render a progress bar, and the three
#: terminal siblings rank equal since none of them is "ahead" of another.
STAGE_RANK: dict[str | None, int] = {
    None: 0, "": 0, "heard": 1, "understood": 2, "shaped": 3, "making": 4,
    "delivered": 5, "accepted": 6, "reshaped": 6, "sprouted": 6,
}


def mark_in_hand(warp_root: Path | None, item_id: str, *, run_id: str) -> bool:
    """The derived half of "in hand" (design-the-ask.md's console spec:
    "'in hand' needs no design — only the seat's discipline: `stage:
    making` + the attempt written the moment work starts"). Stamps
    *run_id* into ``attempts:`` (idempotent — the list-row grammar already
    dedupes) and advances ``stage:`` to ``making`` only when it hadn't
    reached that far yet (never backward, never past ``making`` from here
    — delivery is :func:`mark_delivered`, a separate act at close).
    ``True`` when anything changed; ``False`` on an unresolvable item, a
    blank *run_id*, or a genuine no-op."""
    from . import items as items_mod

    path = items_mod.resolve_item(warp_root, item_id) if warp_root else None
    if path is None or not run_id:
        return False
    lines = items_mod._edit_lines(path)
    if lines is None:
        return False
    changed = _append_to_ask_list_row(lines, "attempts", run_id)
    current = _ask_row_value(lines, "stage")
    if STAGE_RANK.get(current, 0) < STAGE_RANK["making"]:
        if _set_ask_row(lines, "stage", "making"):
            changed = True
    if changed:
        items_mod._write_lines(path, lines)
    return changed


def mark_delivered(warp_root: Path | None, item_id: str, *, receipt: str) -> bool:
    """The derived half of "delivered" (his 2026-09-22 steer: "at run end,
    if the run's produce names the item … the daemon sets `stage:
    delivered` and adds the PR to `return:`"). Advances ``stage:`` to
    ``delivered`` only when it hadn't reached that far, and gives
    ``return:`` its first value only when the row is still unset — a
    hand-authored return *type* (``in chat``, ``a page``, …) is never
    overwritten by a receipt address; the row only ever fills once.
    ``True`` when anything changed."""
    from . import items as items_mod

    path = items_mod.resolve_item(warp_root, item_id) if warp_root else None
    if path is None or not receipt:
        return False
    lines = items_mod._edit_lines(path)
    if lines is None:
        return False
    changed = False
    current = _ask_row_value(lines, "stage")
    if STAGE_RANK.get(current, 0) < STAGE_RANK["delivered"]:
        if _set_ask_row(lines, "stage", "delivered"):
            changed = True
    if not _ask_row_value(lines, "return"):
        if _set_ask_row(lines, "return", receipt):
            changed = True
    if changed:
        items_mod._write_lines(path, lines)
    return changed


def _git_touch_times(target_dir: Path) -> dict[str, str]:
    """Newest commit ISO time per item id, from one ``git log`` over
    *target_dir* (e.g. the warp directory) — git auto-discovers the repo
    root upward, so no manual walk is needed. Empty when *target_dir* is
    not inside a git repo, or git fails.

    ``GIT_DIR``/``GIT_WORK_TREE``/``GIT_INDEX_FILE`` are stripped from the
    child's env: a strand's own worktree pin would otherwise redirect this
    bare ``git`` at the wrong tree (daemon-substrate.md, "your git is
    pinned to your worktree").
    """
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    try:
        proc = subprocess.run(
            ["git", "-C", str(target_dir), "log", "--relative", "--name-only",
             "--format=%x01%cI", "--", "."],
            capture_output=True, text=True, env=env, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    times: dict[str, str] = {}
    current: str | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("\x01"):
            current = line[1:]
            continue
        if line.endswith(".md") and current and Path(line).stem not in times:
            times[Path(line).stem] = current  # log is newest-first
    return times


def _load_warp_files(warp: Path) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for path in sorted(warp.glob("*.md")):
        if path.is_symlink():
            continue
        try:
            files.append((path.name, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return files


def _read_binding_files(
    runs_dir: Path | None, outbox_root: Path | None, warp_dir: Path | None = None,
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Say-binding files under *runs_dir* (captured run nodes),
    *outbox_root* (live, uncaptured) and *warp_dir* (the account-level
    ``.asks.jsonl`` an inbound ``accept``/``reroute`` directive writes —
    :func:`apply_inbound_directive`) as ``(synthetic path, text)`` pairs
    for :func:`_says_from_run_files`, plus an item id -> ISO mtime map —
    the touch evidence a binding with no run of its own carries (a
    captured run node's own start time already rides its path).
    """
    pairs: list[tuple[str, str]] = []
    touch: dict[str, _dt.datetime] = {}

    def _add(path: Path, synthetic: str) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        pairs.append((synthetic, text))
        stamp = _mtime(path)
        if stamp is None:
            return
        for raw in text.splitlines():
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if isinstance(record, dict) and isinstance(record.get("item"), str) and record["item"]:
                item = record["item"]
                if item not in touch or stamp > touch[item]:
                    touch[item] = stamp

    if runs_dir is not None and runs_dir.is_dir():
        for name in ASKS_NAMES:
            for path in sorted(runs_dir.glob(f"*/*/{name}")):
                if path.is_file() and not path.is_symlink():
                    _add(path, f"runs/{path.parent.parent.name}/{path.parent.name}/{name}")
    if outbox_root is not None and outbox_root.is_dir():
        for path in sorted(outbox_root.glob("*/.asks.jsonl")):
            if path.is_file() and not path.is_symlink():
                _add(path, f"outbox/{path.parent.name}/.asks.jsonl")
    if warp_dir is not None:
        directive_path = warp_dir / DIRECTIVES_CONTROL_NAME
        if directive_path.is_file() and not directive_path.is_symlink():
            _add(directive_path, f"warp/{DIRECTIVES_CONTROL_NAME}")

    return pairs, {item: _iso(stamp) for item, stamp in touch.items()}


def _merge_touch(*maps: Mapping[str, str]) -> dict[str, str]:
    """The newest ISO time per item across several evidence maps, compared
    as parsed instants — a git commit's ``%cI`` keeps its own UTC offset,
    so two ISO strings for the same instant need not sort the same
    lexically."""
    best: dict[str, tuple[_dt.datetime, str]] = {}
    for m in maps:
        for item, iso in m.items():
            parsed = _parse_iso(iso)
            if parsed is None:
                continue
            if item not in best or parsed > best[item][0]:
                best[item] = (parsed, iso)
    return {item: iso for item, (_when, iso) in best.items()}


def list_asks(
    surface_root: Path,
    *,
    runs_dir: Path | None = None,
    outbox_root: Path | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """The disk door: a surface repo's warp items, folded with say
    bindings from a captured run node (*runs_dir*), a live, uncaptured
    outbox (*outbox_root*) — both optional, since a bare warp read (no
    runs, no live outbox) is still legal — and the account-level
    ``warp/.asks.jsonl`` an inbound ``accept``/``reroute`` directive writes
    (always read when present; no run or outbox owns that row).
    *surface_root* is the account's ``surface/`` directory; item files live
    in ``<surface_root>/warp/``.

    Returns the same ``{"asks", "done", "goals", "stale_after_days"}``
    payload :func:`build_asks` / :func:`asks_from_files` do — this is the
    one row shape both doors produce.
    """
    surface_root = Path(surface_root)
    warp = surface_root / "warp"
    if not warp.is_dir():
        return {"asks": [], "done": [], "goals": [], "stale_after_days": stale_after_days}
    files = _load_warp_files(warp)
    binding_pairs, say_touch = _read_binding_files(runs_dir, outbox_root, warp)
    files += binding_pairs
    touched = _merge_touch(_git_touch_times(warp), say_touch)
    return build_asks(files, touched=touched, stale_after_days=stale_after_days, now=now)


def goal_row(g: dict[str, Any]) -> dict[str, Any]:
    """A goal normalized to the regular row shape — used only for the
    CLI's flat ``--json`` list, where a goal interleaves with regular rows
    in display order (the payload keeps goals apart everywhere else)."""
    return {
        "id": g["id"], "title": g["title"], "type": "goal",
        "return": None, "stage": None, "touched_at": g["touched_at"],
        "says": [], "stale": False,
    }


def ordered_rows(payload: dict[str, Any], *, include_done: bool = True) -> list[dict[str, Any]]:
    """Display order: goals, then open asks (live before stale), then
    (``include_done``) the closed bucket — used for ``brnrd asks --json``,
    which renders one flat list; :func:`render` walks the same payload in
    its own sectioned form."""
    live = [r for r in payload["asks"] if not r["stale"]]
    stale = [r for r in payload["asks"] if r["stale"]]
    closed = payload["done"] if include_done else []
    return [goal_row(g) for g in payload["goals"]] + live + stale + closed


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    """The narrow projection ``brnrd asks --json`` has always returned —
    ``says`` here is a count (the chat door's own convention); the richer
    ``says[]`` list (event id + at + excerpt) rides the full payload
    :func:`build_asks` / :func:`list_asks` / the dashboard route return
    untouched, so the two ``--json`` surfaces compute identical values for
    every field they share without being forced onto one wire shape."""
    row = dict(row)
    row["says"] = len(row.get("says") or [])
    row.setdefault("sign", None)
    return {k: row[k] for k in (
        "id", "title", "type", "return", "stage", "touched_at", "says", "stale", "sign"
    )}


# Receipt fields can also contain prose; only these shapes denote references.
_HASH_NUMBER_RE = re.compile(r"^#([1-9]\d*)$")
_MD_NAME_RE = re.compile(r"^[\w.-]+\.md$")
_WARP_ITEM_REF_RE = re.compile(r"^w-\d+$")


def resolve_receipts(row: Mapping[str, Any], bases: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Resolve recognized return/receipt tokens; missing or ambiguous bases stay unlinked.

    Ask rows carry no repo attribution. Forge refs need one repo; knowledge
    refs need one distinct base (several repos can share the same knowledge).
    """
    from . import forges

    base = next(iter(bases.values())) if len(bases) == 1 else None
    kb_bases = {
        value["kb"].rstrip("/") for value in bases.values()
        if isinstance(value, Mapping) and isinstance(value.get("kb"), str) and value["kb"]
    }
    kb_base = next(iter(kb_bases)) if len(kb_bases) == 1 else None
    tokens = dict.fromkeys(
        token for raw in (row.get("return"), row.get("receipt"))
        for token in _split(raw or "")
    )
    receipts = []
    for token in tokens:
        url = None
        if match := _HASH_NUMBER_RE.fullmatch(token):
            if isinstance(base, Mapping) and base.get("forge") and base.get("forge_kind"):
                forge = forges.detect_forge(base["forge"], override_kind=base["forge_kind"])
                if forge is not None:
                    # A repo label may be an alias; the remote owns the URL path.
                    url = forges.pull_request_url(
                        base["forge"], f"{forge.owner}/{forge.repo}", match.group(1),
                        override_kind=forge.kind,
                    )
        elif _MD_NAME_RE.fullmatch(token):
            url = f"{kb_base}/{token}" if kb_base else None
        elif _WARP_ITEM_REF_RE.fullmatch(token):
            # There is no dashboard route for an individual warp item yet.
            pass
        else:
            continue
        receipts.append({"ref": token, "url": url})
    return receipts


def relative(then: str | None, now: _dt.datetime) -> str:
    parsed = _parse_iso(then) if then else None
    if parsed is None:
        return "?"
    secs = max(0, int((now - parsed).total_seconds()))
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit}"
    return "now"


def render(
    payload: dict[str, Any],
    *,
    now: _dt.datetime | None = None,
    include_done: bool = True,
) -> str:
    """One screen: goals, then open asks by last touch (newest first),
    stale below a rule, done/retired last (when *include_done*)."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    stale_days = payload["stale_after_days"]
    goals = payload["goals"]
    live = [r for r in payload["asks"] if not r["stale"]]
    stale = [r for r in payload["asks"] if r["stale"]]
    closed = payload["done"] if include_done else []
    lines: list[str] = []

    def line(r: dict[str, Any]) -> str:
        return (
            f"{r['id']} · {_clip(r['title'])} · {r['type'] or 'untyped'} · {r['return'] or '-'}"
            f" · touched {relative(r['touched_at'], now)} · says {len(r['says'])}"
        )

    for g in goals:
        lines.append(f"◎ {g['id']} · {_clip(g['title'])}")
        goal_line = " · ".join(v for v in (g.get("metric"), g.get("target"), g.get("horizon")) if v)
        lines.append(f"    {goal_line or '(no metric · target · horizon)'}")
    if goals:
        lines.append("")
    lines.extend(line(r) for r in live)
    if not live and not goals and not stale:
        lines.append("no open asks")
    if stale:
        lines.append(f"── stale · untouched > {stale_days}d ──")
        lines.extend(line(r) for r in stale)
    if closed:
        lines.append("── done / retired ──")
        lines.extend(f"{'✕' if r['retired'] else '✓'} {line(r)}" for r in closed)
    return "\n".join(lines)
