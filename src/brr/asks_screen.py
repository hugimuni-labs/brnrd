"""``brnrd asks`` — the LRU list of asks (design-the-ask.md §The list).

An ask is a warp item with optional extra rows (``return:`` ``stage:``
``touched:`` ``says:`` ``attempts:``). This module reads the warp and the
``.asks.jsonl`` bindings and renders one screen: goals first, then open
items by last touch (newest first), then a ``stale`` bucket below the
horizon. Read-only, no network; the item verbs' own parser is untouched —
the optional rows are read here, never required.

Last touch of an item = the newest of
  * its file's last commit time in the surface repo (one batched
    ``git log --name-only`` over ``warp/``; mtime only when git has no
    record, e.g. an uncommitted new file), and
  * the newest ``.asks.jsonl`` row naming it. Rows carry no timestamp
    (``do.append_ask`` writes ``{"event","item"}``), so a row's time is its
    file's mtime.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import items as items_mod

DEFAULT_STALE_AFTER_DAYS = 60
STALE_CONFIG_KEY = "asks.stale_after_days"
TITLE_WIDTH = 72

_EXTRA_ROW_RE = re.compile(r"^(return|stage|touched|says|attempts):[ \t]*(.*)$")

#: Where ``.asks.jsonl`` rows can live. The live outbox is where
#: ``do.append_ask`` writes; the run-node names are where a capture would
#: put it (``daemon.PRESERVED`` names none today — see the report).
ASKS_NAMES = ("asks.jsonl", ".asks.jsonl")


def stale_after_days(cfg: dict[str, Any] | None) -> int:
    raw = (cfg or {}).get(STALE_CONFIG_KEY)
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_STALE_AFTER_DAYS
    return days if days > 0 else DEFAULT_STALE_AFTER_DAYS


def _parse_ts(value: str) -> _dt.datetime | None:
    try:
        parsed = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _mtime(path: Path) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime, _dt.timezone.utc)
    except OSError:
        return None


def git_touch_times(warp_root: Path) -> dict[str, _dt.datetime]:
    """Newest commit time per file under *warp_root*, one ``git log``.

    Keyed by file name relative to *warp_root*. Empty when the directory is
    not in a git repo or git fails — the caller falls back to mtime.
    """
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
    }
    try:
        proc = subprocess.run(
            ["git", "-C", str(warp_root), "log", "--relative", "--name-only",
             "--format=%x01%cI", "--", "."],
            capture_output=True, text=True, env=env, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    out: dict[str, _dt.datetime] = {}
    current: _dt.datetime | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("\x01"):
            current = _parse_ts(line[1:])
            continue
        name = line.strip()
        if name and current is not None and name not in out:
            out[name] = current  # log is newest-first: first sighting wins
    return out


def asks_files(runs_dir: Path | None, outbox_root: Path | None) -> list[Path]:
    found: list[Path] = []
    if runs_dir is not None and runs_dir.is_dir():
        for name in ASKS_NAMES:
            found.extend(sorted(runs_dir.glob(f"*/*/{name}")))
    if outbox_root is not None and outbox_root.is_dir():
        found.extend(sorted(outbox_root.glob("*/.asks.jsonl")))
    return [p for p in found if p.is_file() and not p.is_symlink()]


def read_says(
    files: list[Path],
) -> tuple[dict[str, int], dict[str, _dt.datetime]]:
    """Count every binding row and its newest file timestamp per item."""
    count: dict[str, int] = {}
    newest: dict[str, _dt.datetime] = {}
    for path in files:
        stamp = _mtime(path)
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    event, item = row.get("event"), row.get("item")
                    if not isinstance(event, str) or not isinstance(item, str):
                        continue
                    if not event or not item:
                        continue
                    count[item] = count.get(item, 0) + 1
                    if stamp is not None and (item not in newest or stamp > newest[item]):
                        newest[item] = stamp
        except OSError:
            continue
    return count, newest


def header_rows(path: Path) -> dict[str, str]:
    """Read the item header, allowing optional ask rows between item rows.

    Keep this extension local: the item verbs retain their existing grammar.
    Unknown keys and body text still end the header.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    rows: dict[str, str] = {}
    in_rows = False
    for line in text.replace("\r\n", "\n").split("\n"):
        if not in_rows:
            if not line.strip() or line.startswith("#"):
                continue
            in_rows = True
        match = _EXTRA_ROW_RE.match(line) or items_mod._ROW_RE.match(line)
        if not match:
            break
        if match and match.group(1) not in rows:
            rows[match.group(1)] = match.group(2).strip()
    return rows


def _clip(text: str, width: int = TITLE_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def build_rows(
    warp_root: Path,
    *,
    runs_dir: Path | None = None,
    outbox_root: Path | None = None,
    stale_days: int = DEFAULT_STALE_AFTER_DAYS,
    now: _dt.datetime | None = None,
    include_all: bool = False,
) -> list[dict[str, Any]]:
    """Ordered rows: goals, open LRU, stale, then (``include_all``) done."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    git_times = git_touch_times(warp_root)
    says, says_newest = read_says(asks_files(runs_dir, outbox_root))
    horizon = _dt.timedelta(days=stale_days)
    rows: list[dict[str, Any]] = []
    for item in items_mod.load_items(warp_root):
        extra = header_rows(item.path)
        state = "done" if "done" in extra else "retired" if "retired" in extra else "open"
        item_type = extra.get("type", "").lower()
        item_type = item_type if item_type in items_mod.ALL_TYPES else "untyped"
        if state != "open" and not include_all:
            continue
        touches = [
            t for t in (
                git_times.get(item.path.name) or _mtime(item.path),
                says_newest.get(item.id),
            ) if t is not None
        ]
        touched = max(touches) if touches else None
        is_goal = item_type == items_mod.GOAL_TYPE
        rows.append({
            "id": item.id,
            "title": _clip(item.headline),
            "type": item_type,
            "return": extra.get("return") or None,
            "stage": extra.get("stage") or None,
            "touched_at": touched.isoformat() if touched else None,
            "says": says.get(item.id, 0),
            "stale": (
                state == "open" and not is_goal
                and touched is not None and now - touched > horizon
            ),
            "state": state,
            "_goal_line": " · ".join(
                extra[k] for k in ("metric", "target", "horizon") if extra.get(k)
            ),
        })

    def bucket(row: dict[str, Any]) -> int:
        if row["state"] != "open":
            return 3
        if row["type"] == items_mod.GOAL_TYPE:
            return 0
        return 2 if row["stale"] else 1

    rows.sort(key=lambda r: (bucket(r), _neg_ts(r["touched_at"]), r["id"]))
    return rows


def _neg_ts(value: str | None) -> float:
    parsed = _parse_ts(value) if value else None
    return -parsed.timestamp() if parsed else float("inf")


def relative(then: str | None, now: _dt.datetime) -> str:
    parsed = _parse_ts(then) if then else None
    if parsed is None:
        return "?"
    secs = max(0, int((now - parsed).total_seconds()))
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit}"
    return "now"


def render(
    rows: list[dict[str, Any]],
    *,
    now: _dt.datetime | None = None,
    stale_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    lines: list[str] = []
    goals = [r for r in rows if r["type"] == items_mod.GOAL_TYPE and r["state"] == "open"]
    live = [r for r in rows if r["state"] == "open" and r not in goals and not r["stale"]]
    stale = [r for r in rows if r["state"] == "open" and r["stale"]]
    closed = [r for r in rows if r["state"] != "open"]

    def line(r: dict[str, Any]) -> str:
        return (
            f"{r['id']} · {r['title']} · {r['type']} · {r['return'] or '-'}"
            f" · touched {relative(r['touched_at'], now)} · says {r['says']}"
        )

    for r in goals:
        lines.append(f"◎ {r['id']} · {r['title']}")
        lines.append(f"    {r['_goal_line'] or '(no metric · target · horizon)'}")
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
        lines.extend(f"{'✓' if r['state'] == 'done' else '✕'} {line(r)}" for r in closed)
    return "\n".join(lines)


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in (
        "id", "title", "type", "return", "stage", "touched_at", "says", "stale"
    )}
