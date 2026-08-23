"""Historical projection for the operator console.

Live console state comes from :mod:`brr.operator_console.model`; this module
adds the post-mortem half without creating another store. It indexes the
persisted local run bundles and the resolved brnrd-home run nodes, merges them
with the live snapshot by run id, and hydrates heavy forensic surfaces only for
the selected historical row.

The invariant is deliberately asymmetric: presence is evidence of *liveness*;
its disappearance is not evidence that the run ceased to exist. A completed
run remains inspectable for as long as its existing run/home artifacts are
retained.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import account, conversations, message_store, protocol
from .. import config as conf
from ..run import Run
from . import model as live

Boundary = live.Boundary
RunView = live.RunView
ConsoleSnapshot = live.ConsoleSnapshot
console_conversation_key = live.console_conversation_key
resolve_repo_root = live.resolve_repo_root

# Enough room for months of ordinary use without turning a 1-second refresh
# into an unbounded filesystem walk. Explicit --run selection is always added
# even when it sits outside this recent window.
_HISTORY_RAIL_LIMIT = 200
_TERMINAL = {"done", "error", "conflict"}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _frontmatter(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    return protocol.parse_frontmatter(text) if text else {}


def _epoch(value: object) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _stat_time(path: Path, attr: str = "st_mtime") -> float:
    try:
        return float(getattr(path.stat(), attr))
    except OSError:
        return 0.0


def _runner_fields(source: dict[str, Any]) -> tuple[str, str, str, str]:
    name = str(source.get("runner_name") or source.get("runner") or "")
    shell = str(source.get("runner_shell") or source.get("shell") or name or "")
    core = str(source.get("runner_core") or source.get("core") or "")
    klass = str(source.get("runner_class") or "")
    return name, shell, core, klass


def _history_title(status: str, title: str, run_id: str) -> str:
    base = " ".join((title or "").split()) or run_id
    return f"{status or 'history'} · {base}"


def _resolve_existing_home(repo_root: Path) -> tuple[account.HomeContext, str] | None:
    """Resolve the selected home without ever scaffolding one."""
    try:
        cfg = conf.load_config(repo_root)
        ctx = account.resolve_context(repo_root, cfg, create=False)
        label = account.repo_label(repo_root, cfg)
        if not account.context_home_root(ctx).exists():
            return None
        return ctx, label
    except Exception:
        return None


def _recent_dirs(root: Path, selected_run_id: str | None) -> list[Path]:
    try:
        dirs = sorted(
            (p for p in root.iterdir() if p.is_dir() and p.name.startswith("run-")),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError:
        return []
    picked = dirs[:_HISTORY_RAIL_LIMIT]
    if selected_run_id:
        explicit = root / selected_run_id
        if explicit.is_dir() and explicit not in picked:
            picked.append(explicit)
    return picked


def _summary_from_task(task: Run, run_dir: Path, repo_label: str) -> RunView:
    meta = dict(task.meta)
    parent = str(meta.get("parent_run_id") or meta.get("spawn_parent_run_id") or "")
    is_subspawn = bool(meta.get("is_subspawn")) or bool(parent) or task.source == "spawn"
    started = _epoch(meta.get("started_at")) or _stat_time(run_dir / "run.md", "st_ctime")
    ended = _epoch(meta.get("ended_at"))
    if not ended and task.status in _TERMINAL:
        ended = _stat_time(run_dir / "run.md")
    runner_name, runner_shell, runner_core, runner_class = _runner_fields(meta)
    title = str(meta.get("name") or meta.get("label") or meta.get("title") or "")
    return RunView(
        run_id=task.id,
        presence_id="",
        kind="history-strand" if is_subspawn else "history",
        label=_history_title(task.status, title, task.id),
        name="",
        stream=task.conversation_key,
        repo_label=repo_label,
        parent_run_id=parent,
        is_subspawn=is_subspawn,
        runner_name=runner_name,
        runner_shell=runner_shell,
        runner_core=runner_core,
        runner_class=runner_class,
        started_at=started,
        last_seen=ended or _stat_time(run_dir / "run.md") or started,
        event_id=task.event_id,
        manifest={"status": task.status, **meta},
    )


def _summary_from_home(state: dict[str, Any], run_dir: Path, repo_label: str) -> RunView:
    run_id = str(state.get("run_id") or run_dir.name)
    status = str(state.get("status") or "history")
    parent = str(state.get("parent_run_id") or "")
    is_subspawn = bool(parent) or str(state.get("source") or "") == "spawn"
    runner_name, runner_shell, runner_core, runner_class = _runner_fields(state)
    started = _epoch(state.get("started_at")) or _stat_time(run_dir, "st_ctime")
    ended = _epoch(state.get("ended_at"))
    title = str(state.get("name") or state.get("label") or state.get("title") or "")
    return RunView(
        run_id=run_id,
        presence_id="",
        kind="history-strand" if is_subspawn else "history",
        label=_history_title(status, title, run_id),
        name="",
        stream=str(state.get("conversation_key") or ""),
        repo_label=str(state.get("repo_label") or repo_label),
        parent_run_id=parent,
        is_subspawn=is_subspawn,
        runner_name=runner_name,
        runner_shell=runner_shell,
        runner_core=runner_core,
        runner_class=runner_class,
        started_at=started,
        last_seen=ended or _stat_time(run_dir) or started,
        event_id=str(state.get("event_id") or ""),
        manifest=dict(state),
    )


def _merge_summary(primary: RunView, fallback: RunView) -> RunView:
    """Keep local invocation identity, filling durable-home facts it lacks."""
    manifest = dict(fallback.manifest)
    manifest.update(primary.manifest)
    status = str(primary.manifest.get("status") or fallback.manifest.get("status") or "history")
    title = primary.label.split(" · ", 1)[-1] if " · " in primary.label else primary.label
    return replace(
        primary,
        label=_history_title(status, title, primary.run_id),
        stream=primary.stream or fallback.stream,
        repo_label=primary.repo_label or fallback.repo_label,
        parent_run_id=primary.parent_run_id or fallback.parent_run_id,
        is_subspawn=primary.is_subspawn or fallback.is_subspawn,
        runner_name=primary.runner_name or fallback.runner_name,
        runner_shell=primary.runner_shell or fallback.runner_shell,
        runner_core=primary.runner_core or fallback.runner_core,
        runner_class=primary.runner_class or fallback.runner_class,
        started_at=primary.started_at or fallback.started_at,
        last_seen=max(primary.last_seen, fallback.last_seen),
        event_id=primary.event_id or fallback.event_id,
        manifest=manifest,
    )


def _historical_index(
    repo_root: Path,
    runtime: Path,
    *,
    selected_run_id: str | None,
) -> tuple[dict[str, RunView], tuple[account.HomeContext, str] | None]:
    home = _resolve_existing_home(repo_root)
    if home is not None:
        _ctx, repo_label = home
    else:
        try:
            repo_label = account.repo_label(repo_root, conf.load_config(repo_root))
        except Exception:
            repo_label = repo_root.name

    rows: dict[str, RunView] = {}
    for run_dir in _recent_dirs(runtime / "runs", selected_run_id):
        task = Run.from_file(run_dir / "run.md")
        if task is None:
            continue
        rows[task.id] = _summary_from_task(task, run_dir, repo_label)

    if home is None:
        return rows, None
    ctx, repo_label = home
    home_root = ctx.runs_dir / account.slug_repo_label(repo_label)
    for run_dir in _recent_dirs(home_root, selected_run_id):
        state = _frontmatter(run_dir / "state.md")
        if not state and not (run_dir / "body.md").is_file() and not (run_dir / "messages").is_dir():
            continue
        home_row = _summary_from_home(state, run_dir, repo_label)
        if home_row.run_id in rows:
            rows[home_row.run_id] = _merge_summary(rows[home_row.run_id], home_row)
        else:
            rows[home_row.run_id] = home_row
    return rows, home


def _message_thread(messages: list[dict[str, Any]], run_id: str) -> tuple[dict[str, Any], ...]:
    projected: list[dict[str, Any]] = []
    for message in messages:
        body = str(message.get("body") or "").strip()
        if not body:
            continue
        projected.append({
            "kind": "artifact",
            "artifact_kind": f"{message.get('kind') or 'message'}/{message.get('status') or 'unknown'}",
            "body": body,
            "ts": str(message.get("delivered_at") or message.get("created_at") or ""),
            "run_id": run_id,
        })
    return tuple(projected)


def _historical_thread(runtime: Path, stream: str, run_id: str, limit: int) -> tuple[dict[str, Any], ...]:
    if not stream:
        return ()
    records = conversations.read_recent(runtime, stream, limit=max(limit * 8, 200))
    owned = [row for row in records if str(row.get("run_id") or "") == run_id]
    return tuple(owned[-limit:])


def _hydrate_history(
    summary: RunView,
    runtime: Path,
    home: tuple[account.HomeContext, str] | None,
    *,
    thread_limit: int,
) -> RunView:
    """Open the selected run's heavy surfaces; never hydrate the whole rail."""
    run_dir = runtime / "runs" / summary.run_id
    synthetic = {
        "run_id": summary.run_id,
        "id": "",
        "kind": summary.kind,
        "label": summary.label,
        "name": "",
        "stream": summary.stream,
        "repo_label": summary.repo_label,
        "parent_run_id": summary.parent_run_id,
        "is_subspawn": summary.is_subspawn,
        "runner_name": summary.runner_name,
        "runner_shell": summary.runner_shell,
        "runner_core": summary.runner_core,
        "runner_class": summary.runner_class,
        "started_at": summary.started_at,
        "last_seen": summary.last_seen,
    }
    hydrated = live._run_from_presence(runtime, synthetic, thread_limit=thread_limit) if run_dir.is_dir() else None
    if hydrated is None:
        hydrated = summary
    else:
        hydrated = replace(
            hydrated,
            presence_id="",
            kind=summary.kind,
            label=summary.label,
            name="",
            stream=summary.stream or hydrated.stream,
            repo_label=summary.repo_label or hydrated.repo_label,
            parent_run_id=summary.parent_run_id or hydrated.parent_run_id,
            is_subspawn=summary.is_subspawn,
            runner_name=summary.runner_name or hydrated.runner_name,
            runner_shell=summary.runner_shell or hydrated.runner_shell,
            runner_core=summary.runner_core or hydrated.runner_core,
            runner_class=summary.runner_class or hydrated.runner_class,
            started_at=summary.started_at or hydrated.started_at,
            last_seen=summary.last_seen or hydrated.last_seen,
            event_id=summary.event_id or hydrated.event_id,
            manifest={**summary.manifest, **hydrated.manifest},
        )

    thread = _historical_thread(runtime, hydrated.stream, hydrated.run_id, thread_limit)
    body = ""
    messages: list[dict[str, Any]] = []
    home_state: dict[str, Any] = {}
    if home is not None:
        ctx, repo_label = home
        node = account.run_dir(ctx, repo_label, hydrated.run_id)
        home_state = _frontmatter(node / "state.md")
        body = _read_text(node / "body.md")
        messages = message_store.list_messages(node / message_store.MESSAGES_PATH)
    if not thread and messages:
        thread = _message_thread(messages, hydrated.run_id)[-thread_limit:]

    # body.md is the resident-owned closeout body. Retained .card remains a
    # useful fallback for a pre-body run, but never outranks the durable body.
    card = body or hydrated.card
    manifest = dict(home_state)
    manifest.update(hydrated.manifest)
    manifest["_history"] = True
    manifest["_body_source"] = "home body.md" if body else ("retained .card" if hydrated.card else "")
    manifest["_storage"] = ",".join(
        part for part, present in (
            ("run-bundle", run_dir.is_dir()),
            ("home-node", bool(home_state or body or messages)),
        ) if present
    )
    return replace(hydrated, card=card, thread=thread, manifest=manifest)


def _history_sort_key(run: RunView) -> tuple[float, str]:
    ended = _epoch(run.manifest.get("ended_at"))
    return (ended or run.last_seen or run.started_at, run.run_id)


def collect_snapshot(
    repo_root: Path,
    *,
    selected_run_id: str | None = None,
    brr_dir: Path | None = None,
    thread_limit: int = 40,
) -> ConsoleSnapshot:
    """Live snapshot plus persisted history, merged by run id."""
    repo_root = Path(repo_root).resolve()
    live_snapshot = live.collect_snapshot(
        repo_root,
        selected_run_id=selected_run_id,
        brr_dir=brr_dir,
        thread_limit=thread_limit,
    )
    runtime = live_snapshot.brr_dir
    history, home = _historical_index(
        repo_root, runtime, selected_run_id=selected_run_id
    )
    live_ids = {run.run_id for run in live_snapshot.runs}
    for run_id in live_ids:
        history.pop(run_id, None)

    history_rows = sorted(history.values(), key=_history_sort_key, reverse=True)
    all_ids = live_ids | set(history)
    if selected_run_id in all_ids:
        chosen = selected_run_id
    elif live_snapshot.selected_run_id:
        chosen = live_snapshot.selected_run_id
    elif history_rows:
        chosen = history_rows[0].run_id
    else:
        chosen = None

    if chosen in history:
        history[chosen] = _hydrate_history(
            history[chosen], runtime, home, thread_limit=thread_limit
        )
        history_rows = sorted(history.values(), key=_history_sort_key, reverse=True)

    runs = tuple([*live_snapshot.runs, *history_rows])
    selected = next((run for run in runs if run.run_id == chosen), None)
    repo_label = selected.repo_label if selected else ""
    key = live.console_conversation_key(repo_root, repo_label)
    console_thread = tuple(conversations.read_recent(runtime, key, limit=thread_limit))
    return ConsoleSnapshot(
        repo_root=repo_root,
        brr_dir=runtime,
        daemon_pid=live_snapshot.daemon_pid,
        runs=runs,
        selected_run_id=chosen,
        selected=selected,
        console_key=key,
        console_thread=console_thread,
    )
