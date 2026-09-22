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
_ASK_ROW_RE = re.compile(
    r"^(return|stage|touched|says|attempts|after|receipt):[ \t]*(.*)$"
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
    names the run so each say's ``at`` is the run's own start time) or a
    live, uncaptured outbox (``outbox/<event>/.asks.jsonl``, whose path
    names no run — its say carries no ``at`` of its own; the disk door
    folds the binding file's mtime into ``touched`` instead, see
    ``list_asks`` / ``_read_binding_files``).
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for path, text in files:
        parts = path.split("/")
        if len(parts) == 4 and parts[0] == "runs" and parts[3] in ASKS_NAMES:
            at = _iso(_run_time(parts[2]))
        elif len(parts) == 3 and parts[0] == "outbox" and parts[2] == ".asks.jsonl":
            at = None
        else:
            continue
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
    runs_dir: Path | None, outbox_root: Path | None
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Say-binding files under *runs_dir* (captured run nodes) and
    *outbox_root* (live, uncaptured) as ``(synthetic path, text)`` pairs
    for :func:`_says_from_run_files`, plus an item id -> ISO mtime map —
    the touch evidence a live outbox binding carries when its path names
    no run (a captured run node's own start time already rides its path).
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
    bindings from a captured run node (*runs_dir*) and a live, uncaptured
    outbox (*outbox_root*) — both optional, since a bare warp read (no
    runs, no live outbox) is still legal. *surface_root* is the account's
    ``surface/`` directory; item files live in ``<surface_root>/warp/``.

    Returns the same ``{"asks", "done", "goals", "stale_after_days"}``
    payload :func:`build_asks` / :func:`asks_from_files` do — this is the
    one row shape both doors produce.
    """
    surface_root = Path(surface_root)
    warp = surface_root / "warp"
    if not warp.is_dir():
        return {"asks": [], "done": [], "goals": [], "stale_after_days": stale_after_days}
    files = _load_warp_files(warp)
    binding_pairs, say_touch = _read_binding_files(runs_dir, outbox_root)
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
    return {k: row[k] for k in (
        "id", "title", "type", "return", "stage", "touched_at", "says", "stale"
    )}


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
