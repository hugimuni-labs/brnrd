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

from rich.markup import escape

from .model import Boundary, ConsoleSnapshot, RunView, collect_snapshot


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


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count for EDGE output sizes."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


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


def _notice_text(notice: Any) -> str:
    if isinstance(notice, dict):
        notice = notice.get("message") or notice.get("text") or notice
    return str(notice)


def _notice_class(notice: Any) -> str:
    text = _notice_text(notice)
    if "note: body text ignored" in text:
        return "note body ignored; use event: to reply"
    return _short(text, 120)


def _course(card: str) -> tuple[int, int]:
    rows = re.findall(r"(?m)^\s*-\s*\[([ xX])\]\s+", card or "")
    return sum(1 for mark in rows if mark.lower() == "x"), len(rows)


#: How much of the command echo survives on the collapsed title line. The
#: recorded detail is capped at 500 B by ``hooks._DETAIL_BASH_MAX`` — measured
#: across 3,944 records the median is 110 chars and the p90 is 494, so pasting
#: it whole into a one-line header made 69% of rows run off the terminal edge
#: and read as a wall. The title carries an identifying *prefix*; the full
#: command lives in the expanded body, where it can wrap.
_TITLE_DETAIL_WIDTH = 56

#: Label column for the expanded body's blocks. Four fixed keys so the eye
#: lands in the same place on every row: where it ran, what it ran, how much
#: came back, what the daemon said into the run at that boundary.
_BODY_LABEL_WIDTH = 7

#: Where a command line may be broken for display. Shell operators only —
#: splitting anywhere else would render a command that is not the command.
_CMD_BREAKS = (" && ", " || ", " | ", " ; ")


def _home_short(path: str) -> str:
    """``/Users/gurio/Source/…`` → ``~/Source/…``; anything else untouched."""
    if not path:
        return ""
    home = str(Path.home())
    if home and path.startswith(home):
        return "~" + path[len(home):]
    return path


def _tool_label(edge: Boundary) -> str:
    """``Bash`` for one tool, ``Bash ×3`` for a batch.

    A Claude boundary can carry several tool calls (669 of them in this
    machine's recorded history carry 2-5). Only the first tool's act and
    detail are recorded, so the count is the honest way to say the row is
    standing for more than it shows — silently rendering ``tools[0]`` made
    the other calls invisible rather than merely unelaborated.
    """
    if not edge.tools:
        return ""
    first = str(edge.tools[0])
    return f"{first} ×{len(edge.tools)}" if len(edge.tools) > 1 else first


def _wrap_command(cmd: str, width: int = 70) -> list[str]:
    """Break a long command at shell operators, never mid-token.

    The width is deliberately narrow. These lines land in a Static inside the
    center pane, and anything that overflows gets re-wrapped by Textual —
    which wraps to the *widget* edge, not to the label column, so one long
    line silently un-indents itself and the block stops reading as a block.
    Wrapping short enough to survive the pane is what keeps the column true.
    """
    text = " ".join((cmd or "").split())
    if not text:
        return []
    parts: list[str] = [text]
    for sep in _CMD_BREAKS:
        nxt: list[str] = []
        for chunk in parts:
            pieces = chunk.split(sep)
            for i, piece in enumerate(pieces):
                nxt.append(piece if i == 0 else sep.lstrip() + piece)
        parts = nxt
    lines: list[str] = []
    for part in parts:
        part = part.strip()
        while len(part) > width:
            lines.append(part[:width])
            part = part[width:]
        if part:
            lines.append(part)
    return lines


def _edge_is_mute(edge: Boundary) -> bool:
    """True for a row that carries nothing a reader could act on.

    ``pre-tool`` fires before the tool runs, so it has no act, no detail and
    no response — and in this machine's history all 2,130 of them injected
    nothing. Each one rendered as a header saying only its own sequence
    number, expanding to "silent — nothing injected": two thirds of the wall,
    guaranteed empty. A pre-tool row that *blocked* something is the opposite
    of empty and is never folded.
    """
    return (
        edge.phase == "pre-tool"
        and not edge.block
        and not edge.inject.strip()
        and not edge.detail
        and not edge.tools
    )


def _edge_rows(run: RunView | None, limit: int = 100) -> tuple[Boundary, ...]:
    """The boundaries the EDGE pane actually shows, mute rows folded out."""
    if run is None:
        return ()
    return tuple(e for e in run.boundaries if not _edge_is_mute(e))[-limit:]


def _edge_groups(
    rows: tuple[Boundary, ...],
) -> list[tuple[str, list[Boundary]]]:
    """Split boundaries into consecutive runs that share a working directory.

    The directory is where the act happened, and it changes rarely — a
    resident stays in its checkout, a strand stays in its worktree. Rendering
    it once per stretch says the same thing as rendering it per row, minus
    the wall of repetition, and it turns a directory change (the interesting
    event) into something visible rather than something you would have to
    diff two adjacent rows to notice.
    """
    out: list[tuple[str, list[Boundary]]] = []
    for edge in rows:
        where = _home_short(edge.cwd)
        if out and out[-1][0] == where:
            out[-1][1].append(edge)
        else:
            out.append((where, [edge]))
    return out


def _edge_cwd_now(run: RunView | None) -> str:
    """The directory the pane's standing header shows — the latest recorded."""
    for edge in reversed(_edge_rows(run)):
        if edge.cwd:
            return _home_short(edge.cwd)
    return ""


def _edge_blocks(edge: Boundary, *, command_width: int = 70) -> list[tuple[str, list[str]]]:
    """The expanded body as labelled blocks: where · ran · out · said.

    Each is a distinct fact and they used to be three different shapes: the
    command was jammed into the title, ``cwd`` was recorded and rendered
    nowhere at all, and the body held only the inject — unlabelled, which is
    why expanding a row read as if it had failed rather than as if it were
    answering a different question than the one asked.

    ``cwd`` appears here *and* as a group header (:func:`_edge_groups`) and
    the pane's standing header — deliberately, at the maintainer's call
    (2026-08-26). The collapsed wall must not repeat one directory down every
    row, but an expanded row is a reader asking for this act in full, and
    "where did it run" is part of full. Cheap where it is asked for, absent
    where it would be noise.
    """
    blocks: list[tuple[str, list[str]]] = []
    if edge.cwd:
        blocks.append(("where", [_home_short(edge.cwd)]))
    if edge.detail:
        blocks.append(("ran", _wrap_command(edge.detail, width=command_width)))
    if edge.out_bytes >= 0:
        # The bytes are counted, never kept (hooks.py: responses are excluded
        # from the transcript on purpose). Say so, or the number reads like a
        # link to something openable.
        blocks.append(("out", [f"{_fmt_bytes(edge.out_bytes)} — not retained"]))
    if edge.inject.strip():
        inject = edge.inject.rstrip()
        if len(inject) > 1800:
            inject = inject[:1800].rstrip() + "\n… elided in console projection"
        blocks.append(("said", inject.splitlines()))
    else:
        blocks.append(("said", ["silent — nothing injected"]))
    if edge.block_reason:
        blocks.append(("block", _wrap_command(edge.block_reason, width=command_width)))
    return blocks


def _render_blocks(blocks: list[tuple[str, list[str]]], indent: str = "    ") -> list[str]:
    """Labelled blocks → lines, label column fixed so the eye tracks it."""
    lines: list[str] = []
    pad = " " * (_BODY_LABEL_WIDTH + len(indent))
    for label, rows in blocks:
        if not rows:
            continue
        lines.append(f"{indent}{label:<{_BODY_LABEL_WIDTH}}{rows[0]}")
        lines.extend(f"{pad}{row}" for row in rows[1:])
    return lines


def _edges(run: RunView | None) -> str:
    """Plain-text mirror of the EDGE pane — one compact line + its blocks.

    The Textual pane builds Collapsibles rather than reading this string, but
    it must render the same facts: this is both the pane's change signature
    and the shape the tests assert against, so a drift here is a drift the
    suite can see.
    """
    if run is None:
        return "Nothing awake."
    if not run.boundaries:
        return f"{run.run_id}\n\nNo recorded boundary fires yet."
    rows = _edge_rows(run)
    if not rows:
        return f"{run.run_id}\n\nAll recorded boundaries were silent pre-tool fires."
    lines: list[str] = []
    standing = _edge_cwd_now(run)
    if standing:
        lines.append(f"in  {standing}")
        lines.append("")
    for where, group in _edge_groups(rows):
        lines.append(f"▸ {where or '—'}")
        for edge in group:
            lines.append(_edge_title(edge, markup=False))
            lines.extend(_render_blocks(_edge_blocks(edge)))
            lines.append("")
    folded = len(run.boundaries) - len(tuple(e for e in run.boundaries if not _edge_is_mute(e)))
    if folded:
        lines.append(f"({folded} silent pre-tool boundaries folded)")
    return "\n".join(lines).rstrip()


#: Stable act → color map, keyed by `hooks.ACT_LABELS` (orient/probe/mutate/
#: publish/dispatch/wait) — one CSS class per act so a wall of boundaries
#: reads as a colored rhythm instead of identical lines. An act outside this
#: set (blank, or a future label this console hasn't caught up to) gets the
#: neutral "act-unknown" class rather than silently inheriting a color that
#: means something else.
_ACT_CSS_CLASS = {act: f"act-{act}" for act in ("orient", "probe", "mutate", "publish", "dispatch", "wait")}
_ACT_UNKNOWN_CLASS = "act-unknown"


def _edge_css_class(edge: Boundary) -> str:
    """CSS class(es) for one boundary's Collapsible: act color + blocked flag."""
    base = _ACT_CSS_CLASS.get(edge.act, _ACT_UNKNOWN_CLASS)
    return f"{base} act-blocked" if edge.block else base


def _edge_title(edge: Boundary, *, markup: bool = True) -> str:
    """One compact line — the Collapsible header shown while collapsed.

    Compact is the contract: time, sequence, act, phase, which tools, an
    identifying *prefix* of the command, and the response size. The command
    itself moved to the body — a 500-byte pipeline on a one-line header is
    not a summary of the act, it is the act with its right-hand side hidden
    by the terminal.
    """
    esc = escape if markup else (lambda value: value)
    tools = _tool_label(edge)
    parts = [p for p in (tools, _short(edge.detail, _TITLE_DETAIL_WIDTH)) if p]
    if edge.out_bytes >= 0:
        parts.append(f"out {_fmt_bytes(edge.out_bytes)}")
    suffix = f"  {esc(' · '.join(parts))}" if parts else ""
    flag = "  ⛔ BLOCKED" if edge.block else ""
    act = esc(edge.act) if edge.act else "·"
    return f"{esc(_clock(edge.at))}  #{edge.seq:<3} {act:<8} {esc(edge.phase)}{suffix}{flag}"


def _edge_body(edge: Boundary, *, command_width: int = 70) -> str:
    """The expanded content — where · ran · out · said, as separate blocks."""
    return "\n".join(_render_blocks(_edge_blocks(edge, command_width=command_width), indent=""))


#: Rough bytes-per-token heuristic for English/Markdown prose. A heuristic,
#: not a measurement — the table says so where it uses it.
_BYTES_PER_TOKEN = 4


def _wake_topology_table(blocks: list[dict[str, Any]]) -> str:
    """Render wake-manifest.json blocks as a compact topology table.

    The ``≈tok`` column is ``bytes // 4`` — a stated heuristic so the
    operator can price a block (tokens × their core's input rate) without
    the console inventing a dollar figure it has no rate to back.
    """
    header = (
        "TOPOLOGY  (from wake-manifest.json)\n"
        "────────────────────────────────────────────────────────────────\n"
        f"{'store':<18}  {'name':<32}  {'kept':>8}  {'≈tok':>7}  {'cut':>8}  trim\n"
        f"{'─'*18}  {'─'*32}  {'─'*8}  {'─'*7}  {'─'*8}  {'─'*4}"
    )
    rows: list[str] = []
    total_kept = 0
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
        tok_str = f"{kept // _BYTES_PER_TOKEN:,}" if isinstance(kept, int) else "—"
        cut_str = f"{cut:,}" if isinstance(cut, int) else "—"
        if isinstance(kept, int):
            total_kept += kept
        rows.append(
            f"{store[:18]:<18}  {name[:32]:<32}  {kept_str:>8}  {tok_str:>7}  {cut_str:>8}  {trim}"
        )
    if not rows:
        return header + "\n(no present blocks)"
    footer = (
        f"{'─'*18}  {'─'*32}  {'─'*8}  {'─'*7}  {'─'*8}  {'─'*4}\n"
        f"{'Σ kept':<18}  {'':<32}  {total_kept:>8,}  {total_kept // _BYTES_PER_TOKEN:>7,}\n"
        f"≈tok = bytes/{_BYTES_PER_TOKEN} (heuristic) · price ≈ tok × the core's input rate · "
        "cached prefixes re-bill far cheaper"
    )
    return header + "\n" + "\n".join(rows) + "\n" + footer


def _wake_header(run: RunView) -> str:
    """The always-visible preamble above WAKE's grouped block sections."""
    prompt_bytes = len(run.prompt.encode("utf-8")) if run.prompt else 0
    return (
        f"{run.run_id} · exact daemon → runner payload · {prompt_bytes:,} B\n"
        "This is what brnrd supplied, not a claim about the Shell's final model context."
    )


def _wake_block_title(block: dict[str, Any]) -> str:
    """Collapsed-view header for one manifest block: name + byte size."""
    name = escape(str(block.get("name") or "?"))
    if not block.get("present"):
        return f"{name}  ·  absent"
    kept = block.get("bytes_kept")
    kept_str = f"{kept:,} B" if isinstance(kept, int) else "— B"
    return f"{name}  ·  {kept_str}"


def _wake_block_detail(block: dict[str, Any]) -> str:
    """Expanded content for one manifest block: everything the manifest knows about it.

    No per-block raw prompt text exists to show — `wake-manifest.json` records
    accounting (bytes/source/trim), not a byte range into `prompt.md` — so the
    expanded view is that accounting, not archaeology the manifest can't back.
    """
    sources = block.get("sources") or []
    if sources and sources[0].get("synthesized"):
        store = "synthesized"
        source_line = "synthesized — computed at wake time, no source file"
    else:
        src = sources[0] if sources else {}
        store = str(src.get("store") or "?")
        source_line = str(src.get("path") or "—")
    kept = block.get("bytes_kept")
    cut = block.get("bytes_cut")
    budget = block.get("budget_bytes")
    trim = block.get("trim_kind")
    lines = [str(block.get("label") or "")] if block.get("label") else []
    lines += [
        f"store   {store}",
        f"source  {source_line}",
        f"kept    {kept:,} B" if isinstance(kept, int) else "kept    —",
        f"≈tok    {kept // _BYTES_PER_TOKEN:,}" if isinstance(kept, int) else "≈tok    —",
        f"cut     {cut:,} B" if isinstance(cut, int) else "cut     —",
        f"budget  {budget:,} B" if isinstance(budget, int) else "budget  —",
        f"trim    {trim or '—'}",
    ]
    return "\n".join(lines)


def _wake_footer(blocks: list[dict[str, Any]]) -> str:
    """Σ kept + the same ≈tok heuristic disclosure `_wake_topology_table` states."""
    total_kept = sum(
        block["bytes_kept"]
        for block in blocks
        if block.get("present") and isinstance(block.get("bytes_kept"), int)
    )
    return (
        f"Σ kept  {total_kept:,} B  ·  ≈{total_kept // _BYTES_PER_TOKEN:,} tok\n"
        f"≈tok = bytes/{_BYTES_PER_TOKEN} (heuristic) · price ≈ tok × the core's input rate · "
        "cached prefixes re-bill far cheaper"
    )


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
    finished = [row for row in pending if row.get("source") == "spawn_completed"]
    attention = [row for row in pending if row.get("source") != "spawn_completed"]
    notices = _notices(run)
    state = run.portal_state
    lines = [
        f"run        {run.run_id}",
        f"event      {run.event_id or '—'}",
        f"attention  {len(attention)} message{'s' if len(attention) != 1 else ''}"
        f" · {len(finished)} strand{'s' if len(finished) != 1 else ''} finished",
    ]

    await_state = state.get("await")
    if isinstance(await_state, dict):
        if await_state.get("resolved"):
            lines.append(f"await      resolved by {await_state.get('outcome') or 'event'}")
        elif await_state.get("armed"):
            lines.append("await      holding")

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

    if attention:
        lines.extend(["", "ATTENTION"])
        for row in attention[-20:]:
            eid = str(row.get("id") or row.get("event_id") or "?")
            lines.append(
                f"  {eid[-12:]:>12}  {str(row.get('source') or '?'):<12}  "
                f"{_short(row.get('body') or row.get('summary') or '', 90)}"
            )

    if finished:
        lines.extend(["", f"FINISHED STRANDS  ({len(finished)})"])
        for row in finished[-20:]:
            status = str(row.get("spawn_status") or "done")
            branch = str(row.get("spawn_published_branch") or "—")
            lines.append(f"  {_short(branch, 54):<54}  {status}")

    if notices:
        grouped: dict[str, int] = {}
        for notice in notices:
            label = _notice_class(notice)
            grouped[label] = grouped.get(label, 0) + 1
        lines.extend(["", f"NOTICES  ({len(notices)} advisories)"])
        for label, count in grouped.items():
            suffix = f" ×{count}" if count > 1 else ""
            lines.append(f"  ! {label}{suffix}")

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


def _awaiting(run: RunView | None) -> dict[str, Any] | None:
    """The live await state when the run is holding, else None.

    Loud on purpose: an operator watching a silent console must be able to
    tell "the resident is deliberately waiting" from "nothing is happening"
    at a glance — the two are byte-identical otherwise.
    """
    if run is None:
        return None
    state = run.portal_state.get("await")
    if isinstance(state, dict) and state.get("armed") and not state.get("resolved"):
        return state
    return None


def _attention(run: RunView | None) -> str:
    if run is None:
        return "nothing awake"
    pending = _pending(run)
    notices = _notices(run)
    done, total = _course(run.card)
    native_boot = isinstance(run.boot.get("session_start"), dict)
    lines = []
    holding = _awaiting(run)
    if holding is not None:
        deadline = str(holding.get("deadline") or "")
        lines.extend(
            [
                "▓▓ AWAIT — deliberately holding ▓▓",
                f"   until {_clock(deadline)}" if deadline else "   no deadline recorded",
                "   any event resolves it",
                "",
            ]
        )
    lines += [
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
        from textual.containers import Horizontal, Vertical, VerticalScroll
        from textual.widgets import (
            Collapsible,
            DataTable,
            Footer,
            RichLog,
            Static,
            TabbedContent,
            TabPane,
        )
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "operator console needs the optional TUI dependency; install with "
            "`pip install 'brnrd[console]'` (or `pip install -e '.[console]'`)"
        ) from exc

    class _MeasuredEdgeBody(Static):
        """Keep command wrapping inside the mounted EDGE widget's column."""

        def __init__(self, edge: Boundary) -> None:
            self.edge = edge
            super().__init__(_edge_body(edge), markup=False)

        def on_mount(self) -> None:
            # content_region excludes this widget's padding; the fixed label
            # column still occupies part of each rendered line.  Headless
            # paths can report zero before layout, so retain the safe width.
            content_width = self.content_region.width or self.size.width
            if content_width <= 0:
                return
            command_width = max(1, content_width - _BODY_LABEL_WIDTH)
            self.update(_edge_body(self.edge, command_width=command_width))

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
        #edge-log, #wake-log { background: #0d0c0a; padding: 0 1; }
        #edge-cwd {
            background: #15120e; color: #8fd6c4; padding: 0 1; height: auto;
        }
        /* Group header: one directory, one line, above the stretch it owns. */
        Static.edge-group {
            color: #8fd6c4; text-style: bold; padding: 1 0 0 0; background: #0d0c0a;
        }
        Collapsible { padding-bottom: 0; background: #0d0c0a; border-top: none; }
        CollapsibleTitle { color: #a99f8c; }
        /* act → color, keyed by hooks.ACT_LABELS — a wall of boundaries reads
           as a colored rhythm instead of a log dump. */
        Collapsible.act-orient CollapsibleTitle { color: #6ec6ff; }
        Collapsible.act-probe CollapsibleTitle { color: #8fd6c4; }
        Collapsible.act-mutate CollapsibleTitle { color: #d3a75e; }
        Collapsible.act-publish CollapsibleTitle { color: #7fbf7f; }
        Collapsible.act-dispatch CollapsibleTitle { color: #c792ea; }
        Collapsible.act-wait CollapsibleTitle { color: #8e806c; }
        Collapsible.act-unknown CollapsibleTitle { color: #a99f8c; }
        /* Blocked overrides act color — always the alarm, whatever the act. */
        Collapsible.act-blocked CollapsibleTitle { color: #e06c75; text-style: bold; }
        Collapsible.wake-absent CollapsibleTitle { color: #6b6153; text-style: italic; }
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
            # EDGE/WAKE rebuild their Collapsible trees only when content
            # changes (see _render_edges/_render_wake); expand state is
            # tracked here, keyed by run id, so a rebuild never re-collapses
            # a section the operator opened. EDGE keys by boundary seq
            # (stable — boundaries.jsonl only appends); WAKE keys by block
            # name plus the sentinel "__raw__" for the raw-prompt section.
            self._edge_expanded: dict[str, set[int]] = {}
            self._wake_expanded: dict[str, set[str]] = {}
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
                            # Standing directory header: the EDGE wall can be
                            # hundreds of rows, and "which tree is this
                            # happening in" is the one fact you want still on
                            # screen after scrolling past its group header.
                            yield Static("", id="edge-cwd")
                            yield VerticalScroll(id="edge-log")
                        with TabPane("WAKE", id="wake"):
                            yield VerticalScroll(id="wake-log")
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

        def _render_edges(self, run: RunView | None) -> None:
            # _edges(run) is not rendered here — reused only as a cheap
            # change signature so an unchanged boundary list skips the
            # rebuild entirely, the same no-op-skip _set_log gives RichLog
            # panes. The actual Collapsible tree is built from run.boundaries
            # directly, one compact colored line each, since that's the only
            # shape that can carry both a title and hidden detail content.
            sig = _edges(run)
            standing = _edge_cwd_now(run)
            self.query_one("#edge-cwd", Static).update(
                f"in  {escape(standing)}" if standing else ""
            )
            if self._last_content.get("#edge-log") == sig:
                return
            container = self.query_one("#edge-log", VerticalScroll)
            was_at_end = container.is_vertical_scroll_end
            container.remove_children()
            if run is None:
                container.mount(Static("Nothing awake."))
            elif not run.boundaries:
                container.mount(Static(f"{run.run_id}\n\nNo recorded boundary fires yet."))
            else:
                # _edge_rows folds out the silent pre-tool fires; the pane and
                # the plain-text projection must show the same set, or the
                # change signature above stops describing what is on screen.
                rows = _edge_rows(run)
                expanded = self._edge_expanded.setdefault(run.run_id, set())
                nodes: list[Any] = []
                for where, group in _edge_groups(rows):
                    nodes.append(Static(f"▸ {where or '—'}", classes="edge-group"))
                    for edge in group:
                        node = Collapsible(
                            _MeasuredEdgeBody(edge),
                            title=_edge_title(edge),
                            collapsed=edge.seq not in expanded,
                            classes=_edge_css_class(edge),
                        )
                        node.edge_seq = edge.seq  # recovered in on_collapsible_toggled
                        nodes.append(node)
                container.mount_all(nodes)
            if was_at_end:
                container.scroll_end(animate=False)
            self._last_content["#edge-log"] = sig

        def _render_wake(self, run: RunView | None) -> None:
            # Same reuse-as-signature move as _render_edges: _wake(run) drives
            # only the skip-if-unchanged check.
            sig = _wake(run)
            if self._last_content.get("#wake-log") == sig:
                return
            container = self.query_one("#wake-log", VerticalScroll)
            was_at_end = container.is_vertical_scroll_end
            container.remove_children()
            if run is None:
                container.mount(Static("No selected run."))
            elif not run.prompt:
                container.mount(Static(f"{run.run_id}\n\nNo prompt.md captured."))
            else:
                container.mount(Static(_wake_header(run)))
                expanded = self._wake_expanded.setdefault(run.run_id, set())
                if run.wake_manifest:
                    nodes = []
                    for block in run.wake_manifest:
                        name = str(block.get("name") or "?")
                        node = Collapsible(
                            Static(_wake_block_detail(block), markup=False),
                            title=_wake_block_title(block),
                            collapsed=name not in expanded,
                            classes="" if block.get("present") else "wake-absent",
                        )
                        node.wake_block_name = name
                        nodes.append(node)
                    container.mount_all(nodes)
                    container.mount(Static(_wake_footer(run.wake_manifest)))
                else:
                    container.mount(Static("no manifest (pre-manifest run)"))
                raw_bytes = len(run.prompt.encode("utf-8"))
                raw_node = Collapsible(
                    Static(run.prompt, markup=False),
                    title=f"RAW PROMPT.MD  ·  {raw_bytes:,} B",
                    collapsed="__raw__" not in expanded,
                )
                raw_node.wake_block_name = "__raw__"
                container.mount(raw_node)
            if was_at_end:
                container.scroll_end(animate=False)
            self._last_content["#wake-log"] = sig

        def on_collapsible_toggled(self, event: Collapsible.Toggled) -> None:
            """Track expand state so the next rebuild doesn't re-collapse it."""
            node = event.collapsible
            run = self.snapshot.selected if self.snapshot else None
            run_id = run.run_id if run else ""
            seq = getattr(node, "edge_seq", None)
            if seq is not None:
                expanded = self._edge_expanded.setdefault(run_id, set())
                expanded.discard(seq) if node.collapsed else expanded.add(seq)
                return
            name = getattr(node, "wake_block_name", None)
            if name is not None:
                expanded = self._wake_expanded.setdefault(run_id, set())
                expanded.discard(name) if node.collapsed else expanded.add(name)

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
            await_label = "    ▓ AWAIT — holding ▓" if _awaiting(run) else ""
            self.query_one("#top", Static).update(
                f"brnrd  /  RESIDENT CONSOLE    {snap.repo_root.name}    "
                f"{daemon_label}    {_runner(run)}{await_label}"
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
            self._render_edges(run)
            self._render_wake(run)
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
