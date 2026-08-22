"""Textual frontend for :mod:`brr.operator_console.model`.

Kept separate from the snapshot layer on purpose: Textual is an optional
developer dependency, and the data model should remain reusable by a future
standalone console, webview, or plain terminal renderer.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .model import ConsoleSnapshot, RunView, collect_snapshot, enqueue_console_message


def _clock(value: str) -> str:
    if not value or value == "?":
        return "??:??:??"
    match = re.search(r"T(\d\d:\d\d:\d\d)", value)
    return match.group(1) if match else value[-8:]


def _age(started_at: float) -> str:
    if not started_at:
        return "—"
    seconds = max(0, int(time.time() - started_at))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}h{minutes:02d}m"
    return f"{minutes:02d}m{seconds:02d}s"


def _short(value: str, width: int = 30) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= width:
        return text
    return text[: max(1, width - 1)].rstrip() + "…"


def _run_title(run: RunView) -> str:
    return run.name or run.label or run.run_id


def _runner(run: RunView) -> str:
    parts = [run.runner_shell, run.runner_core]
    text = " · ".join(part for part in parts if part)
    return text or run.runner_name or "runner ?"


def _pending_rows(run: RunView) -> list[dict[str, Any]]:
    data = run.inbox_state
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("pending", "events", "pending_events"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    value = run.portal_state.get("pending_events")
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _notices(run: RunView) -> list[Any]:
    value = run.portal_state.get("notices")
    return value if isinstance(value, list) else []


def _course(card: str) -> tuple[int, int]:
    rows = re.findall(r"(?m)^\s*-\s*\[([ xX])\]\s+", card or "")
    if not rows:
        return 0, 0
    return sum(1 for mark in rows if mark.lower() == "x"), len(rows)


def _format_edges(run: RunView | None) -> str:
    if run is None:
        return "Nothing awake. Send a console message below to create a daemon event."
    if not run.boundaries:
        return (
            f"{run.run_id}\n\n"
            "No boundary transcript yet. This can mean the run has not crossed a "
            "recorded hook boundary, or predates the transcript path."
        )

    chunks: list[str] = []
    for edge in run.boundaries[-80:]:
        flags: list[str] = []
        if edge.act:
            flags.append(edge.act)
        if edge.block:
            flags.append("BLOCKED")
        suffix = f"  [{' · '.join(flags)}]" if flags else ""
        chunks.append(f"{_clock(edge.at)}  EDGE #{edge.seq:<3}  {edge.phase}{suffix}")
        if edge.inject:
            payload = edge.inject.rstrip()
            if len(payload) > 1600:
                payload = payload[:1600].rstrip() + "\n… injection elided in console view"
            for line in payload.splitlines():
                chunks.append(f"             + {line}")
        else:
            chunks.append("               · silent — nothing injected")
        if edge.block_reason:
            chunks.append(f"               ! {_short(edge.block_reason, 120)}")
        chunks.append("")
    return "\n".join(chunks).rstrip()


def _format_wake(run: RunView | None) -> str:
    if run is None:
        return "No selected run."
    if not run.prompt:
        return f"{run.run_id}\n\nNo prompt.md captured for this run."
    size = len(run.prompt.encode("utf-8"))
    return (
        f"{run.run_id} · brnrd-supplied boot payload · {size:,} B\n"
        "Provenance: exact prompt.md. Runner/model-owned instructions may exist outside this file.\n"
        "────────────────────────────────────────────────────────────────────\n\n"
        + run.prompt
    )


def _format_portals(run: RunView | None) -> str:
    if run is None:
        return "No selected run."
    pending = _pending_rows(run)
    notices = _notices(run)
    state = run.portal_state

    lines = [
        f"run        {run.run_id}",
        f"event      {run.event_id or '—'}",
        f"pending    {len(pending)}",
        f"notices    {len(notices)}",
    ]
    await_state = state.get("await")
    if isinstance(await_state, dict):
        armed = await_state.get("armed")
        resolved = await_state.get("resolved")
        outcome = await_state.get("outcome") or "—"
        lines.append(f"await      armed={armed!s:<5} resolved={resolved!s:<5} outcome={outcome}")
    resources = state.get("resources")
    if isinstance(resources, dict):
        coexist = resources.get("coexisting_runs")
        if isinstance(coexist, dict):
            pool = coexist.get("spawn_pool")
            if isinstance(pool, dict):
                lines.append(
                    "spawn      "
                    f"{pool.get('active', '?')} active / {pool.get('max_concurrent', '?')} max"
                    f" · {pool.get('available', '?')} available"
                )
    produce = state.get("produce")
    if produce:
        lines.append(f"produce    {_short(json.dumps(produce, sort_keys=True), 120)}")

    if pending:
        lines.extend(["", "PENDING"])
        for row in pending[-20:]:
            eid = row.get("id") or row.get("event_id") or "?"
            source = row.get("source") or "?"
            body = row.get("body") or row.get("summary") or ""
            lines.append(f"  {str(eid)[-12:]:>12}  {source:<12}  {_short(str(body), 90)}")

    if notices:
        lines.extend(["", "NOTICES"])
        for notice in notices[-20:]:
            if isinstance(notice, dict):
                text = notice.get("message") or notice.get("text") or json.dumps(notice, sort_keys=True)
            else:
                text = str(notice)
            lines.append(f"  ! {_short(text, 120)}")

    lines.extend(
        [
            "",
            "RAW PORTAL STATE",
            json.dumps(state, indent=2, sort_keys=True, default=str) if state else "{}",
            "",
            "RAW INBOX",
            json.dumps(run.inbox_state, indent=2, sort_keys=True, default=str),
        ]
    )
    return "\n".join(lines)


def _format_thread(run: RunView | None, snapshot: ConsoleSnapshot) -> str:
    if run is None:
        records = snapshot.console_thread
        key = snapshot.console_key
    else:
        records = run.thread
        key = run.stream or "(no conversation key)"
    lines = [f"thread  {key}", ""]
    if not records:
        lines.append("No dialogue records.")
        if run is not None and snapshot.console_thread:
            lines.append(
                f"\nLocal console thread has {len(snapshot.console_thread)} turn(s); "
                "select the console-spawned run when it wakes to read them in context."
            )
        return "\n".join(lines)

    for record in records:
        ts = _clock(str(record.get("ts") or ""))
        kind = record.get("kind")
        if kind == "event":
            who = str(record.get("source") or "user")
            body = str(record.get("body") or record.get("summary") or "")
        else:
            who = str(record.get("artifact_kind") or "resident")
            body = str(record.get("body") or "")
        lines.append(f"{ts}  {who}")
        lines.extend(f"          {line}" for line in body.strip().splitlines() or [""])
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_body(run: RunView | None) -> str:
    if run is None:
        return "No selected run."
    if not run.card:
        return (
            f"{run.run_id}\n\n"
            "No live .card. The resident may not have authored it yet, or the run "
            "predates the current body contract."
        )
    done, total = _course(run.card)
    return f"{run.run_id} · course {done}/{total if total else '—'}\n\n{run.card}"


def _format_attention(run: RunView | None) -> str:
    if run is None:
        return "ATTENTION\n\nnothing awake"
    pending = _pending_rows(run)
    notices = _notices(run)
    done, total = _course(run.card)

    lines = [
        "ATTENTION",
        "",
        _short(_run_title(run), 28),
        _runner(run),
        f"awake      {_age(run.started_at)}",
        f"event      {run.event_id[-12:] if run.event_id else '—'}",
        f"pending    {len(pending)}",
        f"notices    {len(notices)}",
        f"course     {done}/{total}" if total else "course     —",
        f"boundaries {len(run.boundaries)}",
    ]
    await_state = run.portal_state.get("await")
    if isinstance(await_state, dict):
        outcome = await_state.get("outcome")
        if outcome:
            lines.append(f"await      {outcome}")
        elif await_state.get("armed"):
            lines.append("await      armed")

    if pending:
        lines.extend(["", "WAITING"])
        for row in pending[-6:]:
            eid = str(row.get("id") or row.get("event_id") or "?")
            source = str(row.get("source") or "?")
            body = str(row.get("summary") or row.get("body") or "")
            lines.append(f"◌ {eid[-8:]} {source}")
            if body:
                lines.append(f"  {_short(body, 28)}")

    if notices:
        lines.extend(["", "REFUSED"])
        for notice in notices[-4:]:
            text = (
                str(notice.get("message") or notice.get("text") or notice)
                if isinstance(notice, dict)
                else str(notice)
            )
            lines.append(f"! {_short(text, 28)}")
    return "\n".join(lines)


def run_tui(repo_root: Path, *, selected_run_id: str | None = None) -> None:
    """Launch the optional Textual frontend."""
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Horizontal, Vertical
        from textual.widgets import DataTable, Footer, Input, RichLog, Static, TabbedContent, TabPane
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise RuntimeError(
            "operator console needs the optional TUI dependency; "
            "install with `pip install 'brnrd[console]'` "
            "(or `pip install -e '.[console]'` in a checkout)"
        ) from exc

    class OperatorConsole(App):
        TITLE = "brnrd · resident console"
        CSS = """
        Screen {
            background: #0d0c0a;
            color: #d9cfbd;
        }
        #top {
            height: 3;
            padding: 0 1;
            background: #15120e;
            border-bottom: solid #51422c;
        }
        #workspace {
            height: 1fr;
        }
        #left {
            width: 31;
            min-width: 24;
            border-right: solid #3a3125;
        }
        #left-title, #right-title {
            height: 2;
            padding: 0 1;
            color: #d3a75e;
            text-style: bold;
        }
        #runs {
            height: 1fr;
        }
        #center {
            width: 1fr;
        }
        #right {
            width: 34;
            min-width: 26;
            padding: 0 1;
            border-left: solid #3a3125;
        }
        #attention {
            height: 1fr;
        }
        TabbedContent {
            height: 1fr;
        }
        TabPane {
            padding: 0;
        }
        RichLog {
            background: #0d0c0a;
            color: #d9cfbd;
            border: none;
            padding: 0 1;
        }
        #message {
            dock: bottom;
            height: 3;
            border-top: solid #51422c;
            background: #15120e;
        }
        DataTable {
            background: #0d0c0a;
        }
        Footer {
            background: #15120e;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "quit"),
            Binding("1", "tab_edge", "edge", show=False),
            Binding("2", "tab_wake", "wake", show=False),
            Binding("3", "tab_portals", "portals", show=False),
            Binding("4", "tab_thread", "thread", show=False),
            Binding("5", "tab_body", "body", show=False),
            Binding("/", "focus_message", "message"),
            Binding("r", "poll_now", "refresh"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.repo_root = repo_root
            self.selected_run_id = selected_run_id
            self.snapshot: ConsoleSnapshot | None = None
            self._table_ready = False

        def compose(self) -> ComposeResult:
            yield Static("", id="top")
            with Horizontal(id="workspace"):
                with Vertical(id="left"):
                    yield Static("AWAKE / QUEUE", id="left-title")
                    yield DataTable(id="runs", cursor_type="row", zebra_stripes=True)
                with Vertical(id="center"):
                    with TabbedContent(initial="edge"):
                        with TabPane("EDGE", id="edge"):
                            yield RichLog(id="edge-log", wrap=True, markup=False)
                        with TabPane("WAKE", id="wake"):
                            yield RichLog(id="wake-log", wrap=False, markup=False)
                        with TabPane("PORTALS", id="portals"):
                            yield RichLog(id="portals-log", wrap=True, markup=False)
                        with TabPane("THREAD", id="thread"):
                            yield RichLog(id="thread-log", wrap=True, markup=False)
                        with TabPane("BODY", id="body"):
                            yield RichLog(id="body-log", wrap=True, markup=False)
                with Vertical(id="right"):
                    yield Static("ATTENTION", id="right-title")
                    yield Static("", id="attention")
            yield Input(placeholder="message> local daemon event · / focuses here", id="message")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#runs", DataTable)
            table.add_columns("run", "runner", "age")
            self._table_ready = True
            self._poll()
            self.set_interval(1.0, self._poll)

        def _replace_log(self, selector: str, content: str) -> None:
            log = self.query_one(selector, RichLog)
            log.clear()
            log.write(content or "—")

        def _poll(self) -> None:
            try:
                snapshot = collect_snapshot(
                    self.repo_root,
                    selected_run_id=self.selected_run_id,
                )
            except Exception as exc:
                self.query_one("#top", Static).update(f"brnrd · console read failed: {exc}")
                return

            self.snapshot = snapshot
            self.selected_run_id = snapshot.selected_run_id
            selected = snapshot.selected

            daemon_label = f"daemon ● pid {snapshot.daemon_pid}" if snapshot.daemon_pid else "daemon ○"
            runner = _runner(selected) if selected else "no active resident"
            self.query_one("#top", Static).update(
                f"brnrd  /  RESIDENT CONSOLE    {snapshot.repo_root.name}    "
                f"{daemon_label}    {runner}"
            )

            if self._table_ready:
                table = self.query_one("#runs", DataTable)
                table.clear()
                for run in snapshot.runs:
                    marker = "↳" if run.is_subspawn else "◆"
                    title = f"{marker} {_short(_run_title(run), 18)}"
                    table.add_row(title, _short(_runner(run), 22), _age(run.started_at), key=run.run_id)

            self.query_one("#attention", Static).update(_format_attention(selected))
            self._replace_log("#edge-log", _format_edges(selected))
            self._replace_log("#wake-log", _format_wake(selected))
            self._replace_log("#portals-log", _format_portals(selected))
            self._replace_log("#thread-log", _format_thread(selected, snapshot))
            self._replace_log("#body-log", _format_body(selected))

        def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
            value = getattr(event.row_key, "value", event.row_key)
            self.selected_run_id = str(value)
            self._poll()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id != "message":
                return
            body = event.value.strip()
            if not body:
                return
            try:
                repo_label = self.snapshot.selected.repo_label if self.snapshot and self.snapshot.selected else ""
                path = enqueue_console_message(
                    self.repo_root,
                    body,
                    repo_label=repo_label,
                )
            except Exception as exc:
                self.notify(f"message not queued: {exc}", severity="error")
                return
            event.input.value = ""
            self.notify(f"queued {path.stem}", timeout=2)
            self._poll()

        def _tabs(self) -> Any:
            return self.query_one(TabbedContent)

        def action_tab_edge(self) -> None:
            self._tabs().active = "edge"

        def action_tab_wake(self) -> None:
            self._tabs().active = "wake"

        def action_tab_portals(self) -> None:
            self._tabs().active = "portals"

        def action_tab_thread(self) -> None:
            self._tabs().active = "thread"

        def action_tab_body(self) -> None:
            self._tabs().active = "body"

        def action_focus_message(self) -> None:
            self.query_one("#message", Input).focus()

        def action_poll_now(self) -> None:
            self._poll()

    OperatorConsole().run()
