"""Retention windows and GC over daemon-accumulated state (#501, #500).

Everything brnrd accumulates on its own — conversation event logs, the
per-run message store, dispatch inbox/response archives, run bundles'
copied thread history, stale worktrees, the closed-run ledger — grows
forever unless something prunes it. This module is that something: a
config-driven window per store, one planner that walks the stores and
names what is past its window, and one executor that deletes exactly the
planned set. ``brnrd gc`` and the daemon's periodic sweep share this code
path, so a dry run prints the same counts and bytes a real run deletes.

Scope discipline (the #320 boundary): GC touches only daemon-accumulated
state. It never walks the user-owned git surfaces — home dominion memory,
knowledge repos, repo checkouts. The stores live under two roots:

* the repo's shared ``.brr`` dir — ``conversations/``, ``runs/<id>/history``,
  ``worktrees/``, ``run-ledger.jsonl``;
* the brnrd home — ``runs/<repo>/<id>/messages``, ``dispatch/inbox``,
  ``dispatch/responses``.

Windows are days in ``.brr/config``; ``0`` or absent = keep forever, which
is exactly today's behavior — an existing install changes nothing until it
opts in. Fresh installs get :data:`FRESH_INSTALL_DEFAULTS` seeded by
``brnrd init`` (adopt seeds config only when no config file exists yet).

Live-run protection: anything belonging to a run that the presence
registry reports as live — its run bundle, its worktree, its message
store — is skipped regardless of age. Conversation event logs are only
deleted past their window measured by mtime, and a live pipeline's log is
by definition freshly written; the copies a wake actually reads live in
its own run bundle, which the live-run skip protects.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import account
from . import gitops
from . import presence
from . import protocol
from . import run_ledger

# ── Config keys and defaults ─────────────────────────────────────────

#: Days per store; seeded into fresh-install config by ``brnrd init``.
#: Conservative on purpose: conversations and messages carry the account's
#: correspondence (90d), run-bundle history copies are pure duplication of
#: the conversation store (30d), worktrees past 30d are abandoned bodies,
#: and the ledger is the cost record — kept a full year.
FRESH_INSTALL_DEFAULTS: dict[str, float] = {
    "retention.conversations_days": 90,
    "retention.messages_days": 90,
    "retention.inbox_days": 90,
    "retention.run_history_days": 30,
    "retention.worktrees_days": 30,
    "retention.ledger_days": 365,
}

#: Daemon sweep cadence (hours); 0 disables the periodic pass.
SWEEP_INTERVAL_KEY = "retention.sweep_interval_hours"
SWEEP_INTERVAL_DEFAULT_HOURS = 24.0

_DAY_SECONDS = 86_400.0


def _window_seconds(cfg: dict[str, Any], key: str) -> float | None:
    """A window in seconds, or ``None`` for keep-forever (0/absent/bad)."""
    raw = cfg.get(key)
    if raw is None:
        return None
    try:
        days = float(raw)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    return days * _DAY_SECONDS


@dataclass(frozen=True)
class Windows:
    """Retention windows in seconds; ``None`` = keep forever."""

    conversations: float | None = None
    messages: float | None = None
    inbox: float | None = None
    run_history: float | None = None
    worktrees: float | None = None
    ledger: float | None = None

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "Windows":
        return cls(
            conversations=_window_seconds(cfg, "retention.conversations_days"),
            messages=_window_seconds(cfg, "retention.messages_days"),
            inbox=_window_seconds(cfg, "retention.inbox_days"),
            run_history=_window_seconds(cfg, "retention.run_history_days"),
            worktrees=_window_seconds(cfg, "retention.worktrees_days"),
            ledger=_window_seconds(cfg, "retention.ledger_days"),
        )

    def all_disabled(self) -> bool:
        return all(
            getattr(self, name) is None
            for name in (
                "conversations", "messages", "inbox",
                "run_history", "worktrees", "ledger",
            )
        )


# ── Plan model ───────────────────────────────────────────────────────

#: Plan action kinds.
FILE = "file"          # delete one file
TREE = "tree"          # delete a directory tree
WORKTREE = "worktree"  # remove a git worktree (git-aware, rmtree fallback)
LEDGER = "ledger"      # rewrite run-ledger.jsonl keeping ``keep_lines``


@dataclass(frozen=True)
class Action:
    """One planned deletion. ``bytes`` is what the deletion reclaims."""

    store: str
    kind: str
    path: Path
    bytes: int
    items: int = 1
    #: LEDGER only: the raw lines the rewrite keeps, in order.
    keep_lines: tuple[str, ...] = ()
    #: LEDGER only: the raw lines the rewrite drops. The executor re-reads
    #: the ledger and subtracts *these* from the fresh content, so a row
    #: appended between plan and execute survives (see ``_rewrite_ledger``).
    drop_lines: tuple[str, ...] = ()
    #: WORKTREE only: the run id, for ``git worktree remove``.
    run_id: str = ""


@dataclass
class StoreReport:
    """Per-store counts a dry run prints and a real run deletes."""

    store: str
    items: int = 0
    bytes: int = 0
    errors: int = 0


@dataclass
class Plan:
    """The full deletion plan; dry-run and execution share this object."""

    actions: list[Action] = field(default_factory=list)
    live_run_ids: frozenset[str] = frozenset()

    def report(self) -> dict[str, StoreReport]:
        out: dict[str, StoreReport] = {}
        for action in self.actions:
            rep = out.setdefault(action.store, StoreReport(store=action.store))
            rep.items += action.items
            rep.bytes += action.bytes
        return out


# ── Fs helpers ───────────────────────────────────────────────────────

def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _tree_stats(root: Path) -> tuple[int, int, float]:
    """(files, bytes, newest_mtime) over a tree, symlinks not followed."""
    files = 0
    total = 0
    newest = _mtime(root) or 0.0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            files += 1
            total += st.st_size
            newest = max(newest, st.st_mtime)
    return files, total, newest


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _parse_iso(value: object) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ── Live-run protection ──────────────────────────────────────────────

def live_run_ids(repo_root: Path) -> frozenset[str]:
    """Run ids the presence registry reports as live right now.

    Includes both the raw id and its filesystem-sanitized form so the
    guard holds against either spelling on disk.
    """
    brr_dir = gitops.shared_brr_dir(repo_root)
    ids: set[str] = set()
    try:
        entries = presence.list_active(brr_dir)
    except Exception:
        return frozenset()
    for entry in entries:
        run_id = str(entry.get("run_id") or "").strip()
        if not run_id:
            continue
        ids.add(run_id)
        sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-._")
        if sanitized:
            ids.add(sanitized)
    return frozenset(ids)


# ── Planners, one per store ──────────────────────────────────────────

def _plan_conversations(
    brr_dir: Path, window: float, now: float, actions: list[Action],
) -> None:
    root = brr_dir / "conversations"
    if not root.is_dir():
        return
    cutoff = now - window
    for conv_dir in sorted(root.iterdir()):
        if not conv_dir.is_dir():
            continue
        for log in sorted(conv_dir.glob("*.jsonl")):
            mtime = _mtime(log)
            if mtime is not None and mtime < cutoff:
                actions.append(Action(
                    store="conversations", kind=FILE,
                    path=log, bytes=_size(log),
                ))


def _plan_run_history(
    brr_dir: Path, window: float, now: float,
    live: frozenset[str], actions: list[Action],
) -> None:
    """Run bundles keep card/body/relics; only ``history/`` copies go (#500)."""
    runs_root = brr_dir / "runs"
    if not runs_root.is_dir():
        return
    cutoff = now - window
    for run_dir in sorted(runs_root.iterdir()):
        if not run_dir.is_dir() or run_dir.name in live:
            continue
        history = run_dir / "history"
        if not history.is_dir():
            continue
        files, size, newest = _tree_stats(history)
        if newest < cutoff:
            actions.append(Action(
                store="run-history", kind=TREE,
                path=history, bytes=size, items=max(files, 1),
            ))


def _plan_worktrees(
    brr_dir: Path, window: float, now: float,
    live: frozenset[str], actions: list[Action],
) -> None:
    root = brr_dir / "worktrees"
    if not root.is_dir():
        return
    cutoff = now - window
    for wt in sorted(root.iterdir()):
        if not wt.is_dir() or wt.name in live:
            continue
        files, size, newest = _tree_stats(wt)
        if newest < cutoff:
            actions.append(Action(
                store="worktrees", kind=WORKTREE,
                path=wt, bytes=size, items=1, run_id=wt.name,
            ))


def _plan_ledger(
    brr_dir: Path, window: float, now: float, actions: list[Action],
) -> None:
    """Rewrite keeping rows newer than the window; unparseable rows stay."""
    path = brr_dir / run_ledger.LEDGER_NAME
    if not path.is_file():
        return
    cutoff = now - window
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    keep: list[str] = []
    drop: list[str] = []
    dropped_bytes = 0
    for line in raw_lines:
        if not line.strip():
            continue
        stamp: float | None = None
        try:
            row = json.loads(line)
            stamp = _parse_iso(row.get("ended_at")) or _parse_iso(row.get("started_at"))
        except (ValueError, AttributeError):
            stamp = None  # malformed row: keep — GC never invents data loss
        if stamp is not None and stamp < cutoff:
            drop.append(line)
            dropped_bytes += len(line.encode("utf-8")) + 1
        else:
            keep.append(line)
    if drop:
        actions.append(Action(
            store="ledger", kind=LEDGER,
            path=path, bytes=dropped_bytes, items=len(drop),
            keep_lines=tuple(keep), drop_lines=tuple(drop),
        ))


def _plan_messages(
    ctx: account.HomeContext, window: float, now: float,
    live: frozenset[str], actions: list[Action],
) -> None:
    """Terminal messages past the window; pending mail is never touched."""
    from . import message_store

    runs_root = ctx.runs_dir
    if not runs_root.is_dir():
        return
    cutoff = now - window
    terminal = {
        message_store.DELIVERED,
        message_store.COLLECTED,
        message_store.UNDELIVERABLE,
    }
    for messages_dir in sorted(runs_root.glob(f"*/*/{message_store.MESSAGES_PATH}")):
        run_id = messages_dir.parent.name
        if run_id in live:
            continue
        for path in sorted(messages_dir.glob("*.md")):
            mtime = _mtime(path)
            if mtime is None or mtime >= cutoff:
                continue
            message = message_store.read(path)
            if message is None or message.get("status") not in terminal:
                continue
            actions.append(Action(
                store="messages", kind=FILE, path=path, bytes=_size(path),
            ))


def _plan_inbox(
    ctx: account.HomeContext, window: float, now: float, actions: list[Action],
) -> None:
    """Done events + their attachments, responses, and partials; plus
    orphaned response artifacts whose event file is already gone."""
    inbox_dir = ctx.dispatch_inbox
    responses_dir = ctx.responses_dir
    cutoff = now - window
    handled_ids: set[str] = set()

    if inbox_dir.is_dir():
        for path in sorted(inbox_dir.glob("*.md")):
            mtime = _mtime(path)
            if mtime is None or mtime >= cutoff:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            meta = protocol.parse_frontmatter(text)
            if meta.get("status") != "done":
                continue  # pending/processing stay; unparseable stays
            event_id = path.stem
            handled_ids.add(event_id)
            actions.append(Action(
                store="inbox", kind=FILE, path=path, bytes=_size(path),
            ))
            attachments = inbox_dir / f"{event_id}.attachments"
            if attachments.is_dir():
                files, size, _ = _tree_stats(attachments)
                actions.append(Action(
                    store="inbox", kind=TREE, path=attachments,
                    bytes=size, items=max(files, 1),
                ))
            response = protocol.response_path(responses_dir, event_id)
            if response.is_file():
                actions.append(Action(
                    store="inbox", kind=FILE, path=response, bytes=_size(response),
                ))
            partials = protocol.partials_dir(responses_dir, event_id)
            if partials.is_dir():
                files, size, _ = _tree_stats(partials)
                actions.append(Action(
                    store="inbox", kind=TREE, path=partials,
                    bytes=size, items=max(files, 1),
                ))

    # Orphaned responses: the event file is gone, nothing will ever read
    # the response again. Age-gated by the same window.
    if responses_dir.is_dir():
        for path in sorted(responses_dir.glob("*.md")):
            event_id = path.stem
            if event_id in handled_ids:
                continue
            if (inbox_dir / f"{event_id}.md").exists():
                continue
            mtime = _mtime(path)
            if mtime is not None and mtime < cutoff:
                actions.append(Action(
                    store="inbox", kind=FILE, path=path, bytes=_size(path),
                ))


def build_plan(
    repo_root: Path,
    ctx: account.HomeContext | None,
    windows: Windows,
    *,
    now: float | None = None,
) -> Plan:
    """Walk every in-scope store and plan what is past its window."""
    now = _now() if now is None else now
    brr_dir = gitops.shared_brr_dir(repo_root)
    live = live_run_ids(repo_root)
    actions: list[Action] = []

    if windows.conversations is not None:
        _plan_conversations(brr_dir, windows.conversations, now, actions)
    if windows.run_history is not None:
        _plan_run_history(brr_dir, windows.run_history, now, live, actions)
    if windows.worktrees is not None:
        _plan_worktrees(brr_dir, windows.worktrees, now, live, actions)
    if windows.ledger is not None:
        _plan_ledger(brr_dir, windows.ledger, now, actions)
    if ctx is not None:
        if windows.messages is not None:
            _plan_messages(ctx, windows.messages, now, live, actions)
        if windows.inbox is not None:
            _plan_inbox(ctx, windows.inbox, now, actions)

    return Plan(actions=actions, live_run_ids=live)


# ── Executor ─────────────────────────────────────────────────────────

def _remove_worktree(repo_root: Path, action: Action) -> None:
    """Git-aware removal with a plain-rmtree fallback, then prune."""
    removed = False
    try:
        from . import worktree as worktree_mod

        worktree_mod.remove(repo_root, action.run_id, force=True)
        removed = True
    except Exception:
        removed = False
    if not removed and action.path.exists():
        shutil.rmtree(action.path, ignore_errors=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "prune"],
        capture_output=True, text=True, check=False,
    )


def _rewrite_ledger(action: Action) -> None:
    """Re-derive the cut from a fresh read; never replay a stale snapshot.

    The appender (``run_ledger.append_closed_run``) is lock-free — a plain
    ``open("a")`` write, no lock file, no rename — so a row can land between
    plan and execute. Writing the plan-time ``keep_lines`` snapshot would
    silently erase it. Instead: re-read now, keep every line that is not in
    the *planned drop set* (so appended rows survive by construction), and
    only replace if the file is still byte-identical to what we just read —
    otherwise retry once with the newer content. The remaining read→replace
    window is microseconds, down from the daily plan→execute gap.
    """
    dropped = set(action.drop_lines)
    tmp = action.path.with_suffix(action.path.suffix + ".tmp")
    for _attempt in range(2):
        raw = action.path.read_text(encoding="utf-8")
        keep = [l for l in raw.splitlines() if l.strip() and l not in dropped]
        body = "\n".join(keep)
        if body:
            body += "\n"
        tmp.write_text(body, encoding="utf-8")
        if action.path.read_text(encoding="utf-8") == raw:
            break  # no append raced this pass
    tmp.replace(action.path)


def execute_plan(repo_root: Path, plan: Plan) -> dict[str, StoreReport]:
    """Delete exactly what the plan names; report what actually went.

    Numbers mirror :meth:`Plan.report` unless a deletion fails, in which
    case the failure is counted per store instead of silently absorbed.
    """
    reports: dict[str, StoreReport] = {}
    empties: set[Path] = set()
    for action in plan.actions:
        rep = reports.setdefault(action.store, StoreReport(store=action.store))
        try:
            if action.kind == FILE:
                action.path.unlink(missing_ok=True)
                empties.add(action.path.parent)
            elif action.kind == TREE:
                shutil.rmtree(action.path, ignore_errors=False)
            elif action.kind == WORKTREE:
                _remove_worktree(repo_root, action)
            elif action.kind == LEDGER:
                _rewrite_ledger(action)
            rep.items += action.items
            rep.bytes += action.bytes
        except OSError:
            rep.errors += 1
    # Conversation dirs (and similar) that emptied out: drop the husk.
    for parent in sorted(empties, reverse=True):
        try:
            parent.rmdir()  # only succeeds when actually empty
        except OSError:
            pass
    return reports


def gc(
    repo_root: Path,
    ctx: account.HomeContext | None,
    windows: Windows,
    *,
    dry_run: bool = False,
    now: float | None = None,
) -> tuple[Plan, dict[str, StoreReport]]:
    """One entry point for CLI and daemon: plan, then (unless dry) execute."""
    plan = build_plan(repo_root, ctx, windows, now=now)
    if dry_run:
        return plan, plan.report()
    return plan, execute_plan(repo_root, plan)


# ── Rendering ────────────────────────────────────────────────────────

def format_bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(size)} B"


def render_report(
    reports: dict[str, StoreReport],
    windows: Windows,
    *,
    dry_run: bool,
) -> str:
    lines: list[str] = []
    header = "would delete" if dry_run else "deleted"
    order = (
        ("conversations", windows.conversations),
        ("messages", windows.messages),
        ("inbox", windows.inbox),
        ("run-history", windows.run_history),
        ("worktrees", windows.worktrees),
        ("ledger", windows.ledger),
    )
    total_items = 0
    total_bytes = 0
    for store, window in order:
        if window is None:
            lines.append(f"  {store:<14} window off (keep forever)")
            continue
        rep = reports.get(store)
        items = rep.items if rep else 0
        size = rep.bytes if rep else 0
        total_items += items
        total_bytes += size
        suffix = ""
        if rep and rep.errors:
            suffix = f"  ({rep.errors} failed)"
        lines.append(
            f"  {store:<14} {items:>6} item(s)  {format_bytes(size):>10}{suffix}"
        )
    lines.append(f"  {'total':<14} {total_items:>6} item(s)  {format_bytes(total_bytes):>10}")
    return f"[brnrd] gc: {header}\n" + "\n".join(lines)
