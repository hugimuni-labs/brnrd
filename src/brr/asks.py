"""The list of asks — the warp read as the user's LRU (design-the-ask.md).

An ask *is* a warp item (``surface/warp/<id>.md``) carrying five more rows,
added lazily by the seat as it works: ``return:`` · ``stage:`` · ``touched:``
· ``says:`` (event ids) · ``attempts:`` (run ids), plus ``after:`` for a
sprouted chain. No migration: an item without them still lists, with the
fields blank.

One reader, two doors — the same rows either way:

- :func:`asks_from_files` — over ``(path, markdown)`` pairs, which is what the
  hosted dashboard holds (the corpus mirror, ``Account.surface_json``);
- :func:`list_asks` — over a surface repo on disk, where the last-touch time
  is the newest git commit of the item file.

Touch time, best evidence first: an explicit ``touched:`` row · the newest of
the item's ``.asks.jsonl`` says · the newest run id it names in ``taken:`` /
``attempts:`` (run ids are ``run-YYMMDD-HHMM-xxxx``, so they carry a time) ·
a ``done:`` / ``retired:`` date · else unknown, and an unknown touch sinks.

Rows ``.asks.jsonl`` binds (``do.append_ask``) reach this reader only when a
run node carries them (``runs/<slug>/<run>/asks.jsonl``); until the closeout
capture preserves that file the ``says`` come from the item's own ``says:``
row. Never raises on a malformed file — a bad row is skipped.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_STALE_AFTER_DAYS = 60

_TITLE_RE = re.compile(r"^#[ \t]+(.*)$")
#: The recognized-row block — items.py's rows plus the ask rows. Kept a
#: superset here (not edited into items.py's grammar) so the warp's own
#: parser and the frontend graph are untouched by this reader.
_ROW_RE = re.compile(
    r"^(type|topics|needs|advances|done|retired|refs|prompt|taken"
    r"|metric|target|horizon"
    r"|return|stage|touched|says|attempts|after|receipt):[ \t]*(.*)$"
)
_RUN_ID_RE = re.compile(r"^run-(\d{2})(\d{2})(\d{2})-(\d{2})(\d{2})")
_ITEM_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_WARP_PREFIX = "surface/warp/"
_GOAL_RE = re.compile(r"^g-\d+$")


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
        match = _ROW_RE.match(lines[i])
        if not match:
            break
        rows.setdefault(match.group(1), match.group(2).strip())
        i += 1
    return {"title": title, "rows": rows}


def _says_from_run_files(files: Iterable[tuple[str, str]]) -> dict[str, list[dict[str, Any]]]:
    """``item id -> says`` from mirrored ``runs/<slug>/<run>/asks.jsonl``."""
    out: dict[str, list[dict[str, Any]]] = {}
    for path, text in files:
        parts = path.split("/")
        if len(parts) != 4 or parts[0] != "runs" or parts[3] != "asks.jsonl":
            continue
        at = _iso(_run_time(parts[2]))
        for raw in text.splitlines():
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("event") and record.get("item"):
                out.setdefault(str(record["item"]), []).append(
                    {"event": str(record["event"]), "at": at, "excerpt": None}
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
    ``touched`` maps item id -> ISO time (the git door supplies it).
    Returns ``{"asks", "done", "goals", "stale_after_days"}``: open rows in
    LRU order (live before stale), done/retired apart, goals apart.
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
        seen = set(events)
        for extra in run_says.get(item_id, []):
            if extra["event"] not in seen:
                says.append(extra)
                seen.add(extra["event"])
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
        is_done = bool(rows.get("done") or rows.get("retired"))
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
            "topics": _split(rows.get("topics", "")),
            "stale": bool(not is_done and (last is None or last < horizon)),
            "done": is_done,
            "after": (_split(rows.get("after", "")) or [None])[0],
        }
        if _GOAL_RE.fullmatch(item_id) or row["type"] == "goal":
            if not is_done:
                goals.append({"id": item_id, "title": row["title"], "touched_at": row["touched_at"]})
            continue
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
    """The hosted door: the corpus mirror's ``{path, markdown}`` file list."""
    pairs = [
        (str(f.get("path", "")), str(f.get("markdown", "")))
        for f in surface_files
        if isinstance(f, Mapping)
    ]
    return build_asks(pairs, stale_after_days=stale_after_days, now=now)


def _git_touch_times(repo: Path, warp_dir: Path) -> dict[str, str]:
    """``item id -> newest commit time`` of its file, one ``git log`` call."""
    try:
        rel = warp_dir.relative_to(repo).as_posix()
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "--name-only", "--format=@@%cI", "--", rel],
            capture_output=True, text=True, timeout=30, check=False,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(Path.home())},
        ).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    times: dict[str, str] = {}
    current = ""
    for line in out.splitlines():
        if line.startswith("@@"):
            current = line[2:]
        elif line.endswith(".md") and current:
            times.setdefault(Path(line).stem, current)  # log is newest-first
    return times


def list_asks(
    surface_root: Path,
    *,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    include_done: bool = False,
) -> list[dict[str, Any]]:
    """Open asks in LRU order (``include_done`` appends the done set).

    *surface_root* is the account's ``surface/`` directory; item files live
    in ``<surface_root>/warp/``. Rows: ``id, title, type, return, stage,
    touched_at, says, stale, done, after`` (+ ``attempts, receipt, topics``).
    """
    surface_root = Path(surface_root)
    warp = surface_root / "warp"
    if not warp.is_dir():
        return []
    files: list[tuple[str, str]] = []
    for path in sorted(warp.glob("*.md")):
        if path.is_symlink():
            continue
        try:
            files.append((path.name, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    repo = surface_root
    for _ in range(4):
        if (repo / ".git").exists():
            break
        repo = repo.parent
    touched = _git_touch_times(repo, warp) if (repo / ".git").exists() else {}
    payload = build_asks(files, touched=touched, stale_after_days=stale_after_days)
    return payload["asks"] + (payload["done"] if include_done else [])
