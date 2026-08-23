"""Textual frontend for :mod:`brr.operator_console.model`.

Textual stays optional and is imported only when the console starts. The
frontend is deliberately read-only in this first slice: a ``source=cli`` event
can fall through the daemon's ``notify.gate`` resolver to a real chat gate, so
local resident speech needs an explicit local terminal route before it is safe
to put behind an input box.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .model import ConsoleSnapshot, RunView, collect_snapshot


def _clock(value: str) -> str:
    match = re.search(r"T(\d\d:\d\d:\d\d)", value or "")
    return match.group(1) if match else (value[-8:] if value else "??:??:??")


def _age(started_at: float) -> str:
    if not started_at:
        return "—"
    seconds = max(0, int(time.time() - started_at))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}h{minutes:02d}m" if hours else f"{minutes:02d}m{seconds:02d}s"


def _short(value: object, width: int = 30) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _title(run: RunView) -> str:
    return run.name or run.label or run.run_id


def _runner(run: RunView | None) -> str:
    if run is None:
        return "no active resident"
    text = " · ".join(part for part in (run.runner_shell, run.runner_core) if part)
    return text or run.runner_name or "runner ?"


def _pending(run: RunView) -> list[dict[str, Any]]:
    data = run.inbox_state
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("pending", "events", "pending_events"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    rows = run.portal_state.get("pending_events")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _notices(run: RunView) -> list[Any]:
    rows = run.portal_state.get("notices")
    return rows if isinstance(rows, list) else []


def _course(card: str) -> tuple[int, int]:
    rows = re.findall(r"(?m)^\s*-\s*\[([ xX])\]\s+", card or "")
    return sum(1 for mark in rows if mark.lower() == "x"), len(rows)


def _edges(run: RunView | None) -> str:
    if run is None:
        return "Nothing awake."
    if not run.boundaries:
        return f"{run.run_id}\n\nNo recorded boundary fires yet."
    lines: list[str] = []
    for edge in run.boundaries[-100:]:
        flags = [part for part in (edge.act, "BLOCKED" if edge.block else "") if part]
        tail = f"  [{' · '.join(flags)}]" if flags else ""
        lines.append(f"{_clock(edge.at)}  EDGE #{edge.seq:<3}  {edge.phase}{tail}")
        if edge.inject:
            inject = edge.inject.rstrip()
            if len(inject) > 1800:
                inject = inject[:1800].rstrip() + "\n… elided in console projection"
            lines.extend(f"             + {row}" for row in inject.splitlines())
        else:
            lines.append("               · silent — nothing injected")
        if edge.block_reason:
            lines.append(f"               ! {_short(edge.block_reason, 120)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _wake_topology_table(blocks: list[dict[str, Any]]) -> str:
    """Render wake-manifest.json blocks as a compact topology table."""
    header = (
        "TOPOLOGY  (from wake-manifest.json)\n"
        "────────────────────────────────────────────────────────────────\n"
        f"{'store':<18}  {'name':<32}  {'kept':>8}  {'cut':>8}  trim\n"
        f"{'─'*18}  {'─'*32}  {'─'*8}  {'─'*8}  {'─'*4}"
    )
    rows: list[str] = []
    for block in blocks:
        if not block.get("present"):
            continue
        sources = block.get("sources") or []
        if sources and sources[0].get("synthesized"):
            store = "synthesized"
        else:
            store = (sources[0].get("store") or "?") if sources else "?"
        name = str(block.get("name") or "?")
        kept = block.get("bytes_kept")
        cut = block.get("bytes_cut")
        trim = str(block.get("trim_kind") or "")
        kept_str = f"{kept:,}" if isinstance(kept, int) else "—"
        cut_str = f"{cut:,}" if isinstance(cut, int) else "—"
        rows.append(
            f"{store[:18]:<18}  {name[:32]:<32}  {kept_str:>8}  {cut_str:>8}  {trim}"
        )
    if not rows:
        return header + "\n(no present blocks)"
    return header + "\n" + "\n".join(rows)


def _wake(run: RunView | None) -> str:
    if run is None:
        return "No selected run."
    prompt_bytes = len(run.prompt.encode("utf-8")) if run.prompt else 0
    header = (
        f"{run.run_id} · exact daemon → runner payload · "
        f"{prompt_bytes:,} B\n"
        "This is what brnrd supplied, not a claim about the Shell's final model context.\n"
        "────────────────────────────────────────────────────────────────"
    )
    if not run.prompt:
        return f"{run.run_id}\n\nNo prompt.md captured."
    if run.wake_manifest:
        topology = _wake_topology_table(run.wake_manifest)
        return f"{header}\n\n{topology}\n\n{run.prompt}"
    # Pre-manifest run: show bytes+hash header as fallback, then the prompt.
    no_manifest_note = "no manifest (pre-manifest run)"
    return f"{header}\n{no_manifest_note}\n\n{run.prompt}"


def _boot(run: RunView | None) -> str:
    """Render the runner side of wake-up without claiming unknowable context."""
    if run is None:
        return "No selected run."

    boot = run.boot if isinstance(run.boot, dict) else {}
    wake = boot.get("wake") if isinstance(boot.get("wake"), dict) else {}
    runner = boot.get("runner") if isinstance(boot.get("runner"), dict) else {}
    native = (
        boot.get("session_start")
        if isinstance(boot.get("session_start"), dict)
        else None
    )
    envelope = (
        boot.get("model_envelope")
        if isinstance(boot.get("model_envelope"), dict)
        else {}
    )

    shell = str(runner.get("shell") or run.runner_shell or "?")
    core = str(runner.get("core") or run.runner_core or "?")
    sha = str(wake.get("sha256") or "")
    lines = [
        f"{run.run_id} · BOOT · {shell} / {core}",
        "",
        "PROVENANCE",
        "  [exact]   bytes captured by brnrd for this run",
        "  [native]  event emitted by the runner's own lifecycle hook",
        "  [daemon]  runner selection recorded by brnrd presence",
        "  [opaque]  Shell/model-owned context not durably attested",
        "",
        "HANDOFF",
        f"  [daemon] runner     {runner.get('name') or run.runner_name or '—'}",
        f"  [daemon] shell      {shell}",
        f"  [daemon] core       {core}",
        f"  [daemon] class      {runner.get('class') or run.runner_class or '—'}",
        f"  [exact]  wake       {int(wake.get('bytes') or 0):,} B",
        f"  [exact]  wake sha   {sha[:16] + '…' if sha else '—'}",
        f"  [exact]  source     {wake.get('path') or '—'}",
        "",
        "SESSION",
    ]

    if native is not None:
        lines.extend(
            [
                f"  [native] session-start boundary #{native.get('boundary', '?')}",
                f"  [native] at         {native.get('at') or '—'}",
                f"  [native] phase      {native.get('phase') or 'session-start'}",
            ]
        )
        if native.get("act"):
            lines.append(f"  [native] act        {native['act']}")
        if native.get("inject"):
            lines.append(f"  [native] injected   {_short(native['inject'], 120)}")
        raw = native.get("raw")
        if isinstance(raw, dict) and raw:
            lines.extend(
                [
                    "",
                    "NATIVE SESSION-START RECORD",
                    json.dumps(raw, indent=2, sort_keys=True, default=str),
                ]
            )
    else:
        lines.extend(
            [
                "  [opaque] session-start was not observed in the durable boundary transcript",
                "           (Tier 0/1, hooks disabled/not fired, or a pre-transcript run are all possible)",
            ]
        )

    lines.extend(
        [
            "",
            "MODEL ENVELOPE",
            f"  [opaque] {envelope.get('note') or 'runner/model-owned context is not attested'}",
            "",
            "The important split: WAKE answers ‘what did brnrd send?’;",
            "BOOT answers ‘what can we prove about how the receiving Shell woke?’.",
            "A future runner receipt can promote argv/session/transcript rows from opaque to exact/native.",
        ]
    )
    return "\n".join(lines)


def _portals(run: RunView | None, *, show_raw: bool = False) -> str:
    if run is None:
        return "No selected run."
    pending = _pending(run)
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
        lines.append(
            "await      "
            f"armed={await_state.get('armed')} "
            f"resolved={await_state.get('resolved')} "
            f"outcome={await_state.get('outcome') or '—'}"
        )

    resources = state.get("resources")
    if isinstance(resources, dict):
        coexist = resources.get("coexisting_runs")
        pool = coexist.get("spawn_pool") if isinstance(coexist, dict) else None
        if isinstance(pool, dict):
            lines.append(
                f"spawn      {pool.get('active', '?')} active / "
                f"{pool.get('max_concurrent', '?')} max · "
                f"{pool.get('available', '?')} available"
            )

    if pending:
        lines.extend(["", "PENDING"])
        for row in pending[-20:]:
            eid = str(row.get("id") or row.get("event_id") or "?")
            lines.append(
                f"  {eid[-12:]:>12}  {str(row.get('source') or '?'):<12}  "
                f"{_short(row.get('body') or row.get('summary') or '', 90)}"
            )

    if notices:
        lines.extend(["", "NOTICES"])
        for notice in notices[-20:]:
            if isinstance(notice, dict):
                notice = notice.get("message") or notice.get("text") or notice
            lines.append(f"  ! {_short(notice, 120)}")

    if show_raw:
        lines += [
            "",
            "RAW PORTAL STATE",
            json.dumps(state, indent=2, sort_keys=True, default=str) if state else "{}",
            "",
            "RAW INBOX",
            json.dumps(run.inbox_state, indent=2, sort_keys=True, default=str),
        ]
    else:
        lines.append("")
        lines.append("  [j] show raw JSON")

    return "\n".join(lines)


def _thread(run: RunView | None, snapshot: ConsoleSnapshot) -> str:
    records = run.thread if run else snapshot.console_thread
    key = (run.stream if run else snapshot.console_key) or "(no conversation key)"
    lines = [f"thread  {key}", ""]
    if not records:
        return "\n".join(lines + ["No dialogue records."])
    for record in records:
        ts = _clock(str(record.get("ts") or ""))
        if record.get("kind") == "event":
            who = record.get("source") or "user"
            body = record.get("body") or record.get("summary") or ""
        else:
            who = record.get("artifact_kind") or "resident"
            body = record.get("body") or ""
        lines.append(f"{ts}  {who}")
        lines.extend(f"          {row}" for row in str(body).strip().splitlines() or [""])
        lines.append("")
    return "\n".join(lines).rstrip()


def _body(run: RunView | None) -> str:
    if run is None:
        return "No selected run."
    if not run.card:
        return f"{run.run_id}\n\nNo live .card."
    done, total = _course(run.card)
    return f"{run.run_id} · course {done}/{total or '—'}\n\n{run.card}"


def _attention(run: RunView | None) -> str:
    if run is None:
        return "nothing awake"
    pending = _pending(run)
    notices = _notices(run)
    done, total = _course(run.card)
    native_boot = isinstance(run.boot.get("session_start"), dict)
    lines = [
        _short(_title(run), 28),
        _runner(run),
        "",
        f"awake      {_age(run.started_at)}",
        f"event      {run.event_id[-12:] if run.event_id else '—'}",
        f"boot       {'native' if native_boot else 'partial'}",
        f"pending    {len(pending)}",
        f"notices    {len(notices)}",
        f"course     {done}/{total}" if total else "course     —",
        f"boundaries {len(run.boundaries)}",
    ]
    await_state = run.portal_state.get("await")
    if isinstance(await_state, dict):
        if await_state.get("outcome"):
            lines.append(f"await      {await_state['outcome']}")
        elif await_state.get("armed"):
            lines.append("await      armed")
    if pending:
        lines.extend(["", "WAITING"])
        for row in pending[-6:]:
            eid = str(row.get("id") or row.get("event_id") or "?")
            lines.append(f"◌ {eid[-8:]} {row.get('source') or '?'}")
            preview = row.get("summary") or row.get("body")
            if preview:
                lines.append(f"  {_short(preview, 28)}")
    if notices:
        lines.extend(["", "REFUSED"])
        for notice in notices[-4:]:
            if isinstance(notice, dict):
                notice = notice.get("message") or notice.get("text") or notice
            lines.append(f"! {_short(notice, 28)}")
    return "\n".join(lines)


def build_console_app() -> type:
    """Import Textual and return the real ``OperatorConsole`` class.

    Textual is imported here rather than at module top so the console stays an
    optional dependency. Tests reach the actual class through this factory —
    never a mirrored copy of its logic, which would go green while the real
    thing drifted.
    """
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Horizontal, Vertical
        from textual.widgets import DataTable, Footer, RichLog, Static, TabbedContent, TabPane
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "operator console needs the optional TUI dependency; install with "
            "`pip install 'brnrd[console]'` (or `pip install -e '.[console]'`)"
        ) from exc

    class OperatorConsole(App):
        TITLE = "brnrd · resident console"
        CSS = """
        Screen { background: #0d0c0a; color: #d9cfbd; }
        #top {
            height: 3; padding: 0 1; background: #15120e;
            border-bottom: solid #51422c;
        }
        #workspace { height: 1fr; }
        #left { width: 31; min-width: 24; border-right: solid #3a3125; }
        #left-title, #right-title {
            height: 2; padding: 0 1; color: #d3a75e; text-style: bold;
        }
        #runs { height: 1fr; }
        #center { width: 1fr; }
        #right {
            width: 34; min-width: 26; padding: 0 1;
            border-left: solid #3a3125;
        }
        #attention { height: 1fr; }
        #local-speech {
            height: 2; padding: 0 1; color: #8e806c; background: #15120e;
            border-top: solid #51422c;
        }
        TabbedContent { height: 1fr; }
        TabPane { padding: 0; }
        RichLog {
            background: #0d0c0a; color: #d9cfbd; border: none; padding: 0 1;
        }
        DataTable { background: #0d0c0a; }
        Footer { background: #15120e; }
        """
        BINDINGS = [
            Binding("q", "quit", "quit"),
            Binding("1", "tab_edge", "edge", show=False),
            Binding("2", "tab_wake", "wake", show=False),
            Binding("3", "tab_boot", "boot", show=False),
            Binding("4", "tab_portals", "portals", show=False),
            Binding("5", "tab_thread", "thread", show=False),
            Binding("6", "tab_body", "body", show=False),
            Binding("r", "poll_now", "refresh"),
            Binding("j", "toggle_portals_raw", "portals raw JSON", show=False),
        ]

        def __init__(self, repo_root: Path, *, selected_run_id: str | None = None) -> None:
            super().__init__()
            self.repo_root = repo_root
            self.selected_run_id = selected_run_id
            self.snapshot: ConsoleSnapshot | None = None
            # Tracks the last rendered text per log selector so unchanged ticks
            # skip the clear/write and leave the scroll position untouched.
            self._last_content: dict[str, str] = {}
            # Whether the PORTALS pane shows raw JSON (toggled with `j`).
            self._portals_show_raw: bool = False
            # Run ids in current table row order — lets the cursor follow the
            # row it was on across rebuilds (browsing), not just the selection.
            self._row_ids: list[str] = []
            self._poll_timer: Any = None

        def compose(self) -> ComposeResult:
            yield Static("", id="top")
            with Horizontal(id="workspace"):
                with Vertical(id="left"):
                    yield Static("AWAKE / QUEUE", id="left-title")
                    yield DataTable(id="runs", cursor_type="row", zebra_stripes=True)
                with Vertical(id="center"):
                    with TabbedContent(initial="edge"):
                        with TabPane("EDGE", id="edge"):
                            yield RichLog(id="edge-log", wrap=True, markup=False, auto_scroll=False)
                        with TabPane("WAKE", id="wake"):
                            yield RichLog(id="wake-log", wrap=False, markup=False, auto_scroll=False)
                        with TabPane("BOOT", id="boot"):
                            yield RichLog(id="boot-log", wrap=True, markup=False, auto_scroll=False)
                        with TabPane("PORTALS", id="portals"):
                            yield RichLog(id="portals-log", wrap=True, markup=False, auto_scroll=False)
                        with TabPane("THREAD", id="thread"):
                            yield RichLog(id="thread-log", wrap=True, markup=False, auto_scroll=False)
                        with TabPane("BODY", id="body"):
                            yield RichLog(id="body-log", wrap=True, markup=False, auto_scroll=False)
                with Vertical(id="right"):
                    yield Static("ATTENTION", id="right-title")
                    yield Static("", id="attention")
            yield Static(
                "resident input withheld in this slice · source=cli may fall through notify.gate",
                id="local-speech",
            )
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#runs", DataTable).add_columns("run", "runner", "age")
            self._poll()
            self._poll_timer = self.set_interval(1.0, self._poll)

        def _set_log(self, selector: str, text: str) -> None:
            # Skip entirely when the content hasn't changed — the widget is
            # not touched, so scroll position is naturally preserved.
            if self._last_content.get(selector) == text:
                return
            log = self.query_one(selector, RichLog)
            # Remember whether the operator was reading the tail before we
            # clobber the content.  Terminal convention: stay glued to tail
            # only if already there; never yank someone who scrolled up.
            was_at_end = log.is_vertical_scroll_end
            log.clear()
            log.write(text or "—")
            if was_at_end:
                log.scroll_end(animate=False)
            self._last_content[selector] = text

        def _poll(self) -> None:
            try:
                snap = collect_snapshot(self.repo_root, selected_run_id=self.selected_run_id)
            except Exception as exc:
                self.query_one("#top", Static).update(f"brnrd · console read failed: {exc}")
                return
            self.snapshot = snap
            self.selected_run_id = snap.selected_run_id
            run = snap.selected

            daemon_label = f"daemon ● pid {snap.daemon_pid}" if snap.daemon_pid else "daemon ○"
            self.query_one("#top", Static).update(
                f"brnrd  /  RESIDENT CONSOLE    {snap.repo_root.name}    "
                f"{daemon_label}    {_runner(run)}"
            )

            table = self.query_one("#runs", DataTable)
            # Cursor ≠ selection: arrows move the cursor, Enter selects.
            # Remember which run the browsing cursor sat on before the rebuild
            # so a tick never drags an operator mid-browse back to the
            # selected row.
            prev_cursor_id: str | None = None
            if self._row_ids and 0 <= table.cursor_row < len(self._row_ids):
                prev_cursor_id = self._row_ids[table.cursor_row]
            table.clear()
            chosen_row = 0
            self._row_ids = []
            for i, item in enumerate(snap.runs):
                marker = "↳" if item.is_subspawn else "◆"
                table.add_row(
                    f"{marker} {_short(_title(item), 18)}",
                    _short(_runner(item), 22),
                    _age(item.started_at),
                    key=item.run_id,
                )
                self._row_ids.append(item.run_id)
                if item.run_id == snap.selected_run_id:
                    chosen_row = i
            # Restore the cursor: follow the row it was on when it still
            # exists (browsing survives the tick); otherwise fall back to the
            # selected run so focus is never yanked to row 0.
            if snap.runs:
                if prev_cursor_id in self._row_ids:
                    table.move_cursor(row=self._row_ids.index(prev_cursor_id))
                else:
                    table.move_cursor(row=chosen_row)

            self.query_one("#attention", Static).update(_attention(run))
            self._set_log("#edge-log", _edges(run))
            self._set_log("#wake-log", _wake(run))
            self._set_log("#boot-log", _boot(run))
            self._set_log(
                "#portals-log",
                _portals(run, show_raw=self._portals_show_raw),
            )
            self._set_log("#thread-log", _thread(run, snap))
            self._set_log("#body-log", _body(run))

        def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
            self.selected_run_id = str(getattr(event.row_key, "value", event.row_key))
            # Clear the content cache so every pane refreshes to the new run's
            # state; the next _set_log call will scroll each pane to its end.
            self._last_content.clear()
            self._poll()

        def _tabs(self) -> Any:
            return self.query_one(TabbedContent)

        def action_tab_edge(self) -> None:
            self._tabs().active = "edge"

        def action_tab_wake(self) -> None:
            self._tabs().active = "wake"

        def action_tab_boot(self) -> None:
            self._tabs().active = "boot"

        def action_tab_portals(self) -> None:
            self._tabs().active = "portals"

        def action_tab_thread(self) -> None:
            self._tabs().active = "thread"

        def action_tab_body(self) -> None:
            self._tabs().active = "body"

        def action_poll_now(self) -> None:
            self._poll()

        def action_toggle_portals_raw(self) -> None:
            """Toggle raw JSON visibility on the PORTALS pane (bound to `j`)."""
            self._portals_show_raw = not self._portals_show_raw
            # Bust the content cache for the portals pane so the next _set_log
            # call writes the new render even when portal state hasn't changed.
            self._last_content.pop("#portals-log", None)
            self._poll()

    return OperatorConsole


def run_tui(repo_root: Path, *, selected_run_id: str | None = None) -> None:
    build_console_app()(repo_root, selected_run_id=selected_run_id).run()
