"""Historical presentation adapter for the operator console TUI.

The live TUI remains the renderer. This module swaps in the history-aware
snapshot projection and changes only language that would otherwise lie about a
completed run ("awake", "live .card", live AWAIT). No second widget tree.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import history
from . import tui


def _is_history(run: history.RunView | None) -> bool:
    return bool(run is not None and (run.kind.startswith("history") or run.manifest.get("_history")))


def _stamp(value: float) -> str:
    if not value:
        return "—"
    return datetime.fromtimestamp(value).astimezone().strftime("%d/%m %H:%M:%S")


def _duration(start: float, end: float) -> str:
    if not start or not end or end < start:
        return "—"
    seconds = int(end - start)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {seconds:02d}s"


_live_attention = tui._attention
_live_body = tui._body
_live_awaiting = tui._awaiting
_live_boot = tui._boot


def _attention(run: history.RunView | None) -> str:
    if not _is_history(run):
        return _live_attention(run)
    assert run is not None
    meta = run.manifest
    status = str(meta.get("status") or "history")
    ended = history._epoch(meta.get("ended_at")) or run.last_seen
    native_boot = isinstance(run.boot.get("session_start"), dict)
    source = str(meta.get("_body_source") or "")
    storage = str(meta.get("_storage") or "run-bundle")
    lines = [
        "◇ HISTORY · " + status,
        tui._short(tui._title(run), 28),
        tui._runner(run),
        "",
        f"started    {_stamp(run.started_at)}",
        f"ended      {_stamp(ended)}",
        f"wall       {_duration(run.started_at, ended)}",
        f"event      {run.event_id[-12:] if run.event_id else '—'}",
        f"boot       {'native' if native_boot else 'partial'}",
        f"boundaries {len(run.boundaries)}",
        f"body       {source or 'not retained'}",
        f"storage    {storage or 'partial'}",
    ]
    return "\n".join(lines)


def _body(run: history.RunView | None) -> str:
    if not _is_history(run):
        return _live_body(run)
    assert run is not None
    if not run.card:
        return (
            f"{run.run_id} · HISTORY\n\n"
            "No retained run body. This run may predate body.md, or the relevant "
            "artifact may have aged out."
        )
    done, total = tui._course(run.card)
    source = str(run.manifest.get("_body_source") or "retained body")
    return (
        f"{run.run_id} · HISTORY · {source} · course {done}/{total or '—'}\n\n"
        f"{run.card}"
    )


def _awaiting(run: history.RunView | None):
    if _is_history(run):
        return None
    return _live_awaiting(run)


def _boot(run: history.RunView | None) -> str:
    text = _live_boot(run)
    if _is_history(run):
        text = text.replace(
            "runner selection recorded by brnrd presence",
            "runner selection recorded by brnrd durable state",
        )
    return text


def run_tui(repo_root: Path, *, selected_run_id: str | None = None) -> None:
    # The renderer resolves these module globals at poll/render time. Swap only
    # the projection and history-sensitive wording before constructing it.
    tui.collect_snapshot = history.collect_snapshot
    tui._attention = _attention
    tui._body = _body
    tui._awaiting = _awaiting
    tui._boot = _boot

    base = tui.build_console_app()

    class HistoricalOperatorConsole(base):
        def on_mount(self) -> None:
            super().on_mount()
            self.query_one("#left-title").update("RUNS · LIVE / HISTORY")

    HistoricalOperatorConsole(repo_root, selected_run_id=selected_run_id).run()
