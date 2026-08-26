from __future__ import annotations

import asyncio
import hashlib
import json
import os

from brr import conversations, presence
from brr.operator_console.model import collect_snapshot, console_conversation_key
from brr.operator_console.tui import _boot, _edges, _fmt_bytes, _portals, _wake


_SAMPLE_WAKE_MANIFEST = {
    "schema_version": "1",
    "run_id": "run-parent",
    "blocks": [
        {
            "name": "boot-kernel",
            "label": "Boot kernel (action-first score)",
            "present": True,
            "sources": [{"synthesized": True}],
            "bytes_kept": 512,
            "bytes_cut": None,
            "budget_bytes": None,
            "trim_kind": None,
        },
        {
            "name": "identity-core",
            "label": "Resident Identity Core",
            "present": True,
            "sources": [{"path": "/repo/src/brr/prompts/identity-core.md", "store": "product-prompt"}],
            "bytes_kept": 8192,
            "bytes_cut": 2048,
            "budget_bytes": None,
            "trim_kind": None,
        },
        {
            "name": "dominion-digest",
            "label": "Dominion digest",
            "present": False,
            "sources": [{"path": "/home/.brr/dominion/playbook.md", "store": "dominion"}],
            "bytes_kept": None,
            "bytes_cut": None,
            "budget_bytes": None,
            "trim_kind": None,
        },
    ],
}


def _write_run(
    brr,
    run_id: str,
    event_id: str,
    *,
    prompt: str = "wake",
    session_start: bool = False,
    wake_manifest: dict | None = None,
) -> None:
    run_dir = brr / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.md").write_text(
        "---\n"
        f"event_id: {event_id}\n"
        "status: running\n"
        "---\n",
        encoding="utf-8",
    )
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    boundaries = []
    if session_start:
        boundaries.append(
            {
                "phase": "session-start",
                "at": "2026-08-22T23:40:01Z",
                "inject": "initial portal state",
                "session_id": "native-session-123",
            }
        )
    boundaries.append(
        {
            "phase": "PostToolUse",
            "at": "2026-08-22T23:40:18Z",
            "act": "Bash",
            "inject": "pending event evt-followup",
        }
    )
    (run_dir / "boundaries.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in boundaries),
        encoding="utf-8",
    )
    if wake_manifest is not None:
        (run_dir / "wake-manifest.json").write_text(
            json.dumps(wake_manifest), encoding="utf-8"
        )


def test_snapshot_projects_existing_runtime_surfaces(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()

    _write_run(
        brr,
        "run-parent",
        "evt-parent",
        prompt="assembled wake",
        session_start=True,
        wake_manifest=_SAMPLE_WAKE_MANIFEST,
    )
    outbox = brr / "outbox" / "evt-parent"
    outbox.mkdir(parents=True)
    (outbox / "portal-state.json").write_text(
        json.dumps({"notices": [], "await": {"resolved": True, "outcome": "event"}}),
        encoding="utf-8",
    )
    (outbox / "inbox.json").write_text(
        json.dumps([{"id": "evt-followup", "source": "telegram", "body": "one more thing"}]),
        encoding="utf-8",
    )
    (outbox / ".card").write_text(
        "## Now\nTracing the steer.\n\n## Course\n- [x] reproduce\n- [ ] isolate\n",
        encoding="utf-8",
    )

    presence.register(
        brr,
        kind="daemon",
        label="trace steering",
        name="edge trace",
        run_id="run-parent",
        repo_label="hugimuni-labs/brnrd",
        stream="telegram:123:",
        pid=os.getpid(),
        runner_name="codex",
        runner_shell="codex",
        runner_core="gpt-test",
    )

    # A newer child should not steal the default selection from the resident.
    _write_run(brr, "run-child", "evt-child")
    presence.register(
        brr,
        kind="strand",
        label="child",
        run_id="run-child",
        repo_label="hugimuni-labs/brnrd",
        stream="spawn:default",
        pid=os.getpid(),
        parent_run_id="run-parent",
        is_subspawn=True,
        runner_name="claude",
        runner_shell="claude",
        runner_core="sonnet",
        now=2_000_000_000.0,
    )

    conversations.append_record(
        brr,
        "telegram:123:",
        {
            "kind": "event",
            "event_id": "evt-parent",
            "run_id": "run-parent",
            "source": "telegram",
            "body": "please trace it",
        },
        event_id="evt-parent",
    )
    conversations.append_record(
        brr,
        "telegram:123:",
        {
            "kind": "update",
            "event_id": "evt-parent",
            "run_id": "run-parent",
            "type": "run_started",
        },
        event_id="evt-parent",
    )
    conversations.append_record(
        brr,
        "telegram:123:",
        {
            "kind": "artifact",
            "artifact_kind": "interim_response",
            "event_id": "evt-parent",
            "run_id": "run-parent",
            "body": "I found the edge.",
        },
        event_id="evt-parent",
    )

    snapshot = collect_snapshot(repo, brr_dir=brr)

    assert snapshot.selected_run_id == "run-parent"
    assert snapshot.selected is not None
    assert snapshot.selected.prompt == "assembled wake"
    assert snapshot.selected.event_id == "evt-parent"
    assert snapshot.selected.boundaries[0].phase == "session-start"
    assert snapshot.selected.boundaries[1].act == "Bash"
    assert snapshot.selected.boundaries[1].inject == "pending event evt-followup"
    assert snapshot.selected.portal_state["await"]["outcome"] == "event"
    assert snapshot.selected.inbox_state[0]["id"] == "evt-followup"
    assert "Tracing the steer" in snapshot.selected.card
    assert [row["kind"] for row in snapshot.selected.thread] == ["event", "artifact"]
    assert snapshot.console_key == "console:hugimuni-labs_brnrd"

    boot = snapshot.selected.boot
    assert boot["wake"]["provenance"] == "exact"
    assert boot["wake"]["bytes"] == len(b"assembled wake")
    assert boot["wake"]["sha256"] == hashlib.sha256(b"assembled wake").hexdigest()
    assert boot["runner"] == {
        "provenance": "daemon",
        "name": "codex",
        "shell": "codex",
        "core": "gpt-test",
        "class": "",
    }
    assert boot["session_start"]["provenance"] == "native"
    assert boot["session_start"]["boundary"] == 1
    assert boot["session_start"]["raw"]["session_id"] == "native-session-123"
    assert boot["model_envelope"]["provenance"] == "opaque"

    rendered_boot = _boot(snapshot.selected)
    assert "BOOT · codex / gpt-test" in rendered_boot
    assert "[native] session-start boundary #1" in rendered_boot
    assert "native-session-123" in rendered_boot
    assert "[opaque]" in rendered_boot

    # wake_manifest should be populated from wake-manifest.json.
    assert len(snapshot.selected.wake_manifest) == 3
    assert snapshot.selected.wake_manifest[0]["name"] == "boot-kernel"
    assert snapshot.selected.wake_manifest[0]["sources"] == [{"synthesized": True}]

    # _wake shows topology table when manifest is present.
    rendered_wake = _wake(snapshot.selected)
    assert "TOPOLOGY" in rendered_wake
    assert "boot-kernel" in rendered_wake
    assert "identity-core" in rendered_wake
    # Absent block (present=False) should not appear in the topology table.
    assert "dominion-digest" not in rendered_wake


def test_wake_falls_back_gracefully_without_manifest(tmp_path):
    """_wake shows a 'no manifest' note for runs that predate the manifest file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()
    _write_run(brr, "run-old", "evt-old", prompt="old prompt without manifest")
    presence.register(
        brr,
        kind="daemon",
        label="old run",
        run_id="run-old",
        repo_label="hugimuni-labs/brnrd",
        stream="cli:old",
        pid=os.getpid(),
        runner_name="claude",
        runner_shell="claude",
    )
    snapshot = collect_snapshot(repo, brr_dir=brr)
    assert snapshot.selected is not None
    assert snapshot.selected.wake_manifest == []

    rendered = _wake(snapshot.selected)
    assert "no manifest (pre-manifest run)" in rendered
    # Raw prompt still present as fallback content.
    assert "old prompt without manifest" in rendered


def test_boot_does_not_invent_native_session_when_hook_evidence_is_absent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()
    _write_run(brr, "run-plain", "evt-plain", prompt="plain wake")
    presence.register(
        brr,
        kind="daemon",
        label="plain",
        run_id="run-plain",
        repo_label="hugimuni-labs/brnrd",
        stream="cli:plain",
        pid=os.getpid(),
        runner_name="custom",
        runner_shell="custom",
    )

    snapshot = collect_snapshot(repo, brr_dir=brr)
    assert snapshot.selected is not None
    assert snapshot.selected.boot["session_start"] is None
    rendered = _boot(snapshot.selected)
    assert "session-start was not observed" in rendered
    assert "Tier 0/1" in rendered


def test_console_key_has_no_gate_identity(tmp_path):
    repo = tmp_path / "my repo"
    repo.mkdir()
    assert console_conversation_key(repo) == "console:my_repo"


# ---------------------------------------------------------------------------
# PORTALS panel: interpreted-primary, raw JSON behind show_raw flag
# ---------------------------------------------------------------------------


def _make_run_view(tmp_path, portal_state=None, inbox=None):
    """Minimal RunView for _portals() tests (no daemon/Textual needed)."""
    from brr.operator_console.model import RunView

    return RunView(
        run_id="run-test-1234",
        presence_id="p1",
        kind="daemon",
        label="test label",
        name="",
        stream="telegram:99:",
        repo_label="org/repo",
        parent_run_id="",
        is_subspawn=False,
        runner_name="claude",
        runner_shell="claude",
        runner_core="sonnet",
        runner_class="balanced",
        started_at=1_700_000_000.0,
        last_seen=1_700_000_001.0,
        event_id="evt-abc",
        portal_state=portal_state or {},
        inbox_state=inbox or [],
    )


def test_portals_default_shows_interpreted_not_raw_json(tmp_path):
    """Default render must NOT include the raw JSON blob."""
    run = _make_run_view(
        tmp_path,
        portal_state={"notices": ["bad write"], "await": {"armed": True, "resolved": False}},
        inbox=[{"id": "evt-x", "source": "telegram", "body": "hi"}],
    )
    text = _portals(run)
    # Interpreted fields present
    assert "run-test-1234" in text
    assert "attention  1 message · 0 strands finished" in text
    assert "await" in text
    # Raw JSON NOT present — no big dump
    assert "RAW PORTAL STATE" not in text
    assert "RAW INBOX" not in text
    # Hint that the toggle exists
    assert "[j]" in text


def test_portals_separates_correspondence_from_finished_strands_and_folds_notices(tmp_path):
    run = _make_run_view(
        tmp_path,
        portal_state={
            "notices": [
                {"text": "note: body text ignored — a note closes event evt-a without speaking; use event: to reply"},
                {"text": "note: body text ignored — a note closes event evt-b without speaking; use event: to reply"},
            ],
            "await": {"armed": True, "resolved": True, "outcome": "event"},
        },
        inbox=[
            {"id": "evt-person", "source": "cloud", "body": "does it read well?"},
            {
                "id": "evt-child",
                "source": "spawn_completed",
                "spawn_status": "done",
                "spawn_published_branch": "brr/the-finished-limb",
                "body": "concurrent spawn finished --- message_path: /private/path",
            },
        ],
    )

    text = _portals(run)
    assert "attention  1 message · 1 strand finished" in text
    assert "ATTENTION" in text and "does it read well?" in text
    assert "FINISHED STRANDS  (1)" in text
    assert "brr/the-finished-limb" in text
    assert "message_path" not in text
    assert "NOTICES  (2 advisories)" in text
    assert "note body ignored; use event: to reply ×2" in text
    assert "await      resolved by event" in text
    assert "armed=True" not in text


def test_portals_show_raw_includes_json_dumps(tmp_path):
    """show_raw=True must include both JSON sections."""
    run = _make_run_view(
        tmp_path,
        portal_state={"notices": [], "some_key": "some_value"},
        inbox=[{"id": "evt-y"}],
    )
    text = _portals(run, show_raw=True)
    assert "RAW PORTAL STATE" in text
    assert "RAW INBOX" in text
    assert "some_value" in text  # portal state JSON is present
    # The [j] hint should not appear when raw is visible
    assert "[j]" not in text


def test_portals_none_run_returns_placeholder():
    assert _portals(None) == "No selected run."
    assert _portals(None, show_raw=True) == "No selected run."


# ---------------------------------------------------------------------------
# Scroll-preservation: verified via Textual's async pilot harness
#
# Strategy: build a minimal app with a RichLog and the same _set_log logic
# used by OperatorConsole.  Run it under run_test() and assert:
#   1. Unchanged content → widget untouched, user's scroll position kept.
#   2. Changed content, user scrolled up → content updated, NOT yanked to end.
#   3. Changed content, user at tail → content updated, stays at tail.
# ---------------------------------------------------------------------------


def test_set_log_scroll_preservation(tmp_path):
    """
    Drive scroll-preservation through the REAL OperatorConsole._set_log (via
    build_console_app — never a mirrored copy, which would stay green while
    the real logic drifted).  Three scenarios:
      (a) unchanged content -> scroll position completely preserved
      (b) changed content, user scrolled up -> not forced to end
      (c) changed content, user at tail -> stays at tail
    """
    try:
        from brr.operator_console.tui import build_console_app

        OperatorConsole = build_console_app()
        from textual.widgets import RichLog, TabbedContent
    except RuntimeError:
        import pytest

        pytest.skip("textual not installed")

    MANY_LINES = "\n".join(f"line {i:03d}" for i in range(200))
    # Exercises the generic RichLog _set_log path; EDGE/WAKE moved to
    # Collapsible trees (see test_edge_renders_colored_collapsible_rows /
    # test_wake_renders_grouped_collapsible_blocks below) so a plain-text
    # RichLog pane — BOOT — stands in for this scroll-preservation check.
    SELECTOR = "#boot-log"

    async def _run() -> None:
        app = OperatorConsole(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            # Freeze the 1s poll so the pilot owns the pane for the test.
            if app._poll_timer is not None:
                app._poll_timer.pause()
            # BOOT isn't the initial tab (EDGE is) — an inactive TabPane isn't
            # laid out, so its scroll geometry lies. Activate it first.
            app.query_one(TabbedContent).active = "boot"
            await pilot.pause(0.05)
            log = app.query_one(SELECTOR, RichLog)

            # Seed with content (write line-by-line so RichLog definitely has
            # more lines than the 24-row visible area).
            log.clear()
            for line in MANY_LINES.splitlines():
                log.write(line)
            app._last_content[SELECTOR] = MANY_LINES  # prime cache
            await pilot.pause(0.1)

            # --- Prime: known positions ---
            log.scroll_end(animate=False)
            await pilot.pause(0.05)
            assert log.is_vertical_scroll_end, "should be at tail after explicit scroll_end"
            log.scroll_to(y=0, animate=False)
            await pilot.pause(0.05)
            assert not log.is_vertical_scroll_end, "should not be at tail after scroll to top"

            # --- (a) unchanged content -> scroll position completely preserved ---
            scroll_before = log.scroll_y
            app._set_log(SELECTOR, MANY_LINES)  # same content — cache hit, no widget touch
            await pilot.pause(0.05)
            assert log.scroll_y == scroll_before, "scroll moved on no-op update"

            # --- (c) changed content, user at tail -> stays at tail ---
            log.scroll_end(animate=False)
            await pilot.pause(0.05)
            assert log.is_vertical_scroll_end, "pre-condition: at tail"
            app._set_log(SELECTOR, MANY_LINES + "\nextra line appended")
            await pilot.pause(0.05)
            assert log.is_vertical_scroll_end, "tail user should stay at tail after content change"

            # --- (b) changed content, user scrolled up -> not forced to end ---
            log.scroll_to(y=0, animate=False)
            await pilot.pause(0.05)
            assert not log.is_vertical_scroll_end, "pre-condition: not at tail"
            app._set_log(SELECTOR, MANY_LINES + "\nanother update")
            await pilot.pause(0.05)
            assert not log.is_vertical_scroll_end, (
                "user who scrolled up should not be yanked to end on content change"
            )

    asyncio.run(_run())


def test_cursor_follows_browsing_across_polls(tmp_path):
    """
    Cursor != selection: arrows browse, Enter selects.  A 1s poll rebuild must
    not drag a browsing cursor back to the selected row (the defect the first
    cut of the cursor-restore shipped).
    """
    try:
        from brr.operator_console.tui import build_console_app

        OperatorConsole = build_console_app()
        from textual.widgets import DataTable
    except RuntimeError:
        import pytest

        pytest.skip("textual not installed")

    import dataclasses
    from unittest.mock import patch

    from brr.operator_console.model import ConsoleSnapshot

    run_a = _make_run_view(tmp_path)  # run_id="run-test-1234"
    run_b = dataclasses.replace(run_a, run_id="run-strand-5678", is_subspawn=True)
    snap = ConsoleSnapshot(
        repo_root=tmp_path,
        brr_dir=tmp_path / ".brr",
        daemon_pid=None,
        runs=(run_a, run_b),
        selected_run_id=run_a.run_id,
        selected=run_a,
        console_key="console:test",
        console_thread=(),
    )

    async def _run() -> None:
        with patch("brr.operator_console.tui.collect_snapshot", return_value=snap):
            app = OperatorConsole(tmp_path)
            async with app.run_test(size=(80, 24)) as pilot:
                if app._poll_timer is not None:
                    app._poll_timer.pause()
                table = app.query_one("#runs", DataTable)
                await pilot.pause(0.05)
                assert app._row_ids == [run_a.run_id, run_b.run_id]
                assert table.cursor_row == 0, "initial cursor on the selected run"

                # Browse to the strand row without selecting it...
                table.move_cursor(row=1)
                await pilot.pause(0.05)
                # ...then a poll tick rebuilds the table.
                app._poll()
                await pilot.pause(0.05)

                assert table.cursor_row == 1, "poll rebuild must not yank a browsing cursor"
                assert app.selected_run_id == run_a.run_id, "browsing must not change selection"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Run-selection: strand selection rebinds views and cursor is preserved
# ---------------------------------------------------------------------------


def test_strand_selection_preserved_across_polls(tmp_path):
    """
    collect_snapshot respects an explicit selected_run_id even when the chosen
    run is a strand (is_subspawn=True).  This is the model-level assertion that
    'prefer resident as initial selection' is not a lock.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()

    _write_run(brr, "run-resident", "evt-res", prompt="resident wake")
    _write_run(brr, "run-strand", "evt-strand", prompt="strand wake")

    presence.register(
        brr,
        kind="daemon",
        label="resident",
        run_id="run-resident",
        repo_label="org/repo",
        stream="telegram:1:",
        pid=os.getpid(),
        runner_name="claude",
        runner_shell="claude",
        runner_core="sonnet",
    )
    presence.register(
        brr,
        kind="strand",
        label="strand",
        run_id="run-strand",
        repo_label="org/repo",
        stream="spawn:default",
        pid=os.getpid(),
        parent_run_id="run-resident",
        is_subspawn=True,
        runner_name="claude",
        runner_shell="claude",
        runner_core="haiku",
    )

    # Default selection prefers the resident
    snap = collect_snapshot(repo, brr_dir=brr)
    assert snap.selected_run_id == "run-resident"

    # Explicit selection of the strand is honoured
    snap2 = collect_snapshot(repo, brr_dir=brr, selected_run_id="run-strand")
    assert snap2.selected_run_id == "run-strand"
    assert snap2.selected is not None
    assert snap2.selected.prompt == "strand wake"


def test_wake_topology_table_tokens_and_total():
    from brr.operator_console.tui import _wake_topology_table

    table = _wake_topology_table(_SAMPLE_WAKE_MANIFEST["blocks"])
    assert "≈tok" in table
    assert "2,048" in table  # identity-core: 8192 bytes // 4
    assert "Σ kept" in table
    assert "8,704" in table  # 512 + 8192 present bytes
    assert "heuristic" in table  # the tok column names its own basis


def test_attention_is_loud_about_await(tmp_path):
    from brr.operator_console.tui import _attention

    holding = _make_run_view(
        tmp_path,
        portal_state={
            "await": {"armed": True, "resolved": False, "deadline": "2026-08-23T18:00:00Z"}
        },
    )
    out = _attention(holding)
    assert out.splitlines()[0].startswith("▓▓ AWAIT"), "await must lead, loudly"
    assert "18:00:00" in out

    resolved = _make_run_view(
        tmp_path,
        portal_state={"await": {"armed": True, "resolved": True, "outcome": "event"}},
    )
    assert "▓▓ AWAIT" not in _attention(resolved)


def test_edges_renders_tool_detail_and_out_bytes(tmp_path):
    """EDGE shows tool name · command/path · out N KB for instrumented records.

    Old records (no ``detail`` / ``out_bytes``) render exactly as before;
    new records append the detail suffix to the same line.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()
    run_dir = brr / "runs" / "run-edge"
    run_dir.mkdir(parents=True)
    (run_dir / "run.md").write_text(
        "---\nevent_id: evt-edge\nstatus: running\n---\n", encoding="utf-8"
    )
    (run_dir / "prompt.md").write_text("wake", encoding="utf-8")
    # Mix: one record with detail+out_bytes (new), one without (old back-compat).
    boundaries = [
        {
            "phase": "post-tool",
            "at": "2026-08-23T10:00:00Z",
            "act": "mutate",
            "tools": ["Bash"],
            "detail": "git status --short",
            "out_bytes": 1228,
            "inject": None,
            "block": False,
            "block_reason": None,
        },
        {
            "phase": "post-tool",
            "at": "2026-08-23T10:00:05Z",
            "act": "orient",
            "tools": ["Read"],
            "inject": None,
            "block": False,
            "block_reason": None,
            # no detail, no out_bytes — old record shape
        },
    ]
    (run_dir / "boundaries.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in boundaries), encoding="utf-8"
    )
    presence.register(
        brr,
        kind="daemon",
        label="edge test",
        run_id="run-edge",
        repo_label="hugimuni-labs/brnrd",
        stream="cli:edge",
        pid=os.getpid(),
        runner_name="claude",
        runner_shell="claude",
        runner_core="default",
    )

    snapshot = collect_snapshot(repo, brr_dir=brr)
    assert snapshot.selected is not None
    rendered = _edges(snapshot.selected)

    # New-style record: shows tool · detail · out N KB.
    assert "Bash" in rendered
    assert "git status --short" in rendered
    assert "out 1.2 KB" in rendered

    # Old-style record: renders without crashing, no spurious detail suffix.
    # Only "Read" appears from the tools list — no out N KB on that line.
    lines = rendered.splitlines()
    old_line = next(ln for ln in lines if "Read" in ln)
    assert "out" not in old_line


def test_edge_renders_colored_collapsible_rows(tmp_path):
    """EDGE tab: one Collapsible per boundary, collapsed by default, color-
    classed by act; the compact title never carries the full inject text."""
    try:
        from brr.operator_console.tui import build_console_app

        OperatorConsole = build_console_app()
        from textual.widgets import Collapsible
    except RuntimeError:
        import pytest

        pytest.skip("textual not installed")

    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()
    run_dir = brr / "runs" / "run-edge2"
    run_dir.mkdir(parents=True)
    (run_dir / "run.md").write_text(
        "---\nevent_id: evt-edge2\nstatus: running\n---\n", encoding="utf-8"
    )
    (run_dir / "prompt.md").write_text("wake", encoding="utf-8")
    boundaries = [
        {
            "phase": "PostToolUse",
            "at": "2026-08-24T10:00:00Z",
            "act": "mutate",
            "tools": ["Write"],
            "detail": "src/brr/foo.py",
            "out_bytes": 12,
            "inject": "pending event evt-x",
        },
        {
            "phase": "Stop",
            "at": "2026-08-24T10:00:05Z",
            "act": "wait",
            "block": True,
            "block_reason": "unresolved closeout obligation",
            "inject": None,
        },
    ]
    (run_dir / "boundaries.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in boundaries), encoding="utf-8"
    )
    presence.register(
        brr,
        kind="daemon",
        label="edge2",
        run_id="run-edge2",
        repo_label="hugimuni-labs/brnrd",
        stream="cli:edge2",
        pid=os.getpid(),
        runner_name="claude",
        runner_shell="claude",
        runner_core="default",
    )

    async def _run() -> None:
        app = OperatorConsole(repo)
        async with app.run_test(size=(100, 30)) as pilot:
            if app._poll_timer is not None:
                app._poll_timer.pause()
            app._poll()
            await pilot.pause(0.1)
            edge_nodes = [n for n in app.query(Collapsible) if hasattr(n, "edge_seq")]
            assert len(edge_nodes) == 2
            assert all(n.collapsed for n in edge_nodes), "collapsed by default"

            mutate_node = next(n for n in edge_nodes if n.edge_seq == 1)
            assert "act-mutate" in mutate_node.classes
            assert "src/brr/foo.py" in mutate_node.title
            assert "pending event evt-x" not in mutate_node.title, (
                "full inject text belongs in the expanded body, not the compact title"
            )

            blocked_node = next(n for n in edge_nodes if n.edge_seq == 2)
            assert "act-wait" in blocked_node.classes
            assert "act-blocked" in blocked_node.classes
            assert "BLOCKED" in blocked_node.title

    asyncio.run(_run())


def test_edge_collapsible_title_treats_boundary_detail_as_literal_text(tmp_path):
    """Rich-style tokens in recorded commands must not become title markup."""
    try:
        from brr.operator_console.tui import build_console_app

        OperatorConsole = build_console_app()
        from textual.widgets import Collapsible
    except RuntimeError:
        import pytest

        pytest.skip("textual not installed")

    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()
    run_dir = brr / "runs" / "run-edge-markup"
    run_dir.mkdir(parents=True)
    (run_dir / "run.md").write_text(
        "---\nevent_id: evt-edge-markup\nstatus: running\n---\n", encoding="utf-8"
    )
    (run_dir / "prompt.md").write_text("wake", encoding="utf-8")
    detail = "ls [bold]x[/bold] [a-z]"
    (run_dir / "boundaries.jsonl").write_text(
        json.dumps(
            {
                "phase": "PostToolUse",
                "at": "2026-08-24T10:00:00Z",
                "act": "orient",
                "tools": ["Bash"],
                "detail": detail,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    presence.register(
        brr,
        kind="daemon",
        label="edge-markup",
        run_id="run-edge-markup",
        repo_label="hugimuni-labs/brnrd",
        stream="cli:edge-markup",
        pid=os.getpid(),
        runner_name="codex",
        runner_shell="codex",
        runner_core="default",
    )

    async def _run() -> None:
        app = OperatorConsole(repo)
        async with app.run_test(size=(100, 30)) as pilot:
            if app._poll_timer is not None:
                app._poll_timer.pause()
            app._poll()
            await pilot.pause(0.1)
            edge_node = next(n for n in app.query(Collapsible) if hasattr(n, "edge_seq"))
            assert r"ls \[bold]x\[/bold] \[a-z]" in edge_node.title
            assert detail in edge_node.query_one("CollapsibleTitle").render().plain

    asyncio.run(_run())


def test_wake_renders_grouped_collapsible_blocks(tmp_path):
    """WAKE tab: one Collapsible per manifest block plus the raw prompt,
    collapsed by default, header = block name + byte size."""
    try:
        from brr.operator_console.tui import build_console_app

        OperatorConsole = build_console_app()
        from textual.widgets import Collapsible
    except RuntimeError:
        import pytest

        pytest.skip("textual not installed")

    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()
    _write_run(
        brr,
        "run-wake2",
        "evt-wake2",
        prompt="assembled wake bytes",
        wake_manifest=_SAMPLE_WAKE_MANIFEST,
    )
    presence.register(
        brr,
        kind="daemon",
        label="wake2",
        run_id="run-wake2",
        repo_label="hugimuni-labs/brnrd",
        stream="cli:wake2",
        pid=os.getpid(),
        runner_name="claude",
        runner_shell="claude",
        runner_core="default",
    )

    async def _run() -> None:
        app = OperatorConsole(repo)
        async with app.run_test(size=(100, 30)) as pilot:
            if app._poll_timer is not None:
                app._poll_timer.pause()
            app._poll()
            await pilot.pause(0.1)
            wake_nodes = [n for n in app.query(Collapsible) if hasattr(n, "wake_block_name")]
            # 3 manifest blocks (present + absent) + the raw-prompt section.
            assert len(wake_nodes) == 4
            assert all(n.collapsed for n in wake_nodes), "collapsed by default"

            titles = {n.wake_block_name: n.title for n in wake_nodes}
            assert titles["boot-kernel"] == "boot-kernel  ·  512 B"
            assert titles["identity-core"] == "identity-core  ·  8,192 B"
            assert titles["dominion-digest"] == "dominion-digest  ·  absent"

            absent_node = next(n for n in wake_nodes if n.wake_block_name == "dominion-digest")
            assert "wake-absent" in absent_node.classes

            raw_node = next(n for n in wake_nodes if n.wake_block_name == "__raw__")
            assert "RAW PROMPT.MD" in raw_node.title

    asyncio.run(_run())


def test_fmt_bytes_human_readable():
    assert _fmt_bytes(0) == "0 B"
    assert _fmt_bytes(512) == "512 B"
    assert _fmt_bytes(1024) == "1.0 KB"
    assert _fmt_bytes(1228) == "1.2 KB"
    assert _fmt_bytes(1024 * 1024) == "1.0 MB"


# ── EDGE: the boundary blocks (2026-08-26) ────────────────────────────────
#
# The pane used to put the command on the collapsed title line, uncapped at
# `hooks._DETAIL_BASH_MAX` (500 B) — measured across 3,944 recorded details,
# 69% ran past 80 chars, so the wall was a column of clipped pipelines. The
# expanded body held only the inject, unlabelled. `cwd` was recorded on every
# boundary and rendered nowhere.


def _b(**kw):
    """A Boundary with the fields these tests care about; rest defaulted."""
    from brr.operator_console.model import Boundary

    base = dict(seq=1, phase="post-tool", at="2026-08-26T17:31:07Z", act="mutate", inject="")
    base.update(kw)
    return Boundary(**base)


def test_edge_title_is_compact_and_body_carries_the_whole_command():
    from brr.operator_console.tui import (
        _TITLE_DETAIL_WIDTH,
        _edge_body,
        _edge_title,
    )

    command = " && ".join(f"npm run step-{i} 2>&1 | tail -4" for i in range(12))
    edge = _b(detail=command, tools=("Bash",), out_bytes=40042)

    title = _edge_title(edge, markup=False)
    assert len(title) < 120, f"title is not compact: {len(title)} chars"
    assert command not in title
    assert "…" in title
    assert "out 39.1 KB" in title

    body = _edge_body(edge)
    # Wrapped for width, so compare on the joined-and-squashed text.
    flat = " ".join(body.split())
    for fragment in ("npm run step-0", "npm run step-11", "&& npm run step-6"):
        assert fragment in flat, fragment
    assert "not retained" in body, "a counted-but-discarded response must say so"


def test_edge_title_names_the_batch_size():
    """A boundary can carry several tool calls; only the first has a detail.

    Rendering `tools[0]` alone made the rest invisible rather than merely
    unelaborated — the count is the honest summary.
    """
    from brr.operator_console.tui import _edge_title

    one = _edge_title(_b(tools=("Bash",), detail="ls"), markup=False)
    many = _edge_title(_b(tools=("Bash", "Read", "Read"), detail="ls"), markup=False)
    assert "Bash" in one and "×" not in one
    assert "Bash ×3" in many


def test_edge_body_labels_every_block_in_order():
    """where · ran · out · said — four labelled blocks, fixed order.

    The collapsed wall does not repeat `where` (that is the group header's
    job); an expanded row does, because opening a row is asking for the act
    in full and where it ran is part of full.
    """
    from brr.operator_console.tui import _edge_body

    body = _edge_body(
        _b(detail="git status", tools=("Bash",), out_bytes=64, inject="q S17·W55",
           cwd="/somewhere/else")
    )
    labels = [ln.split()[0] for ln in body.splitlines() if not ln.startswith(" ")]
    assert labels == ["where", "ran", "out", "said"]
    assert "/somewhere/else" in body
    assert "q S17·W55" in body


def test_edge_body_says_silent_when_nothing_was_injected():
    from brr.operator_console.tui import _edge_body

    assert "silent — nothing injected" in _edge_body(_b(detail="ls", tools=("Bash",)))


def test_edge_groups_collapse_consecutive_shared_directories():
    from brr.operator_console.tui import _edge_groups

    rows = (
        _b(seq=1, cwd="/repo"),
        _b(seq=2, cwd="/repo"),
        _b(seq=3, cwd="/repo/.brr/worktrees/child"),
        _b(seq=4, cwd="/repo"),
    )
    groups = _edge_groups(rows)
    assert [len(g) for _, g in groups] == [2, 1, 1]
    # A directory change is the interesting event and must be visible as one,
    # rather than requiring a reader to diff two adjacent rows.
    assert [w for w, _ in groups] == ["/repo", "/repo/.brr/worktrees/child", "/repo"]


def test_silent_pre_tool_rows_are_folded_but_a_blocked_one_never_is():
    """2,130 recorded pre-tool rows, 100% of them with zero inject.

    Each rendered as a header carrying only its own sequence number and
    expanded to "silent — nothing injected". A pre-tool row that *blocked* a
    call is the opposite of empty and stays.
    """
    from brr.operator_console.tui import _edge_is_mute

    assert _edge_is_mute(_b(seq=1, phase="pre-tool", act="", detail="", inject=""))
    assert not _edge_is_mute(
        _b(seq=2, phase="pre-tool", act="", detail="", inject="",
           block=True, block_reason="refused: writes outside the worktree")
    )
    # Only pre-tool folds. A post-tool fire that injected nothing still
    # happened, and its command is the reason to look at it.
    assert not _edge_is_mute(_b(seq=3, phase="post-tool", detail="ls", tools=("Bash",)))
    assert not _edge_is_mute(_b(seq=4, phase="stop", act="", detail="", inject=""))


def test_wrap_command_breaks_on_shell_operators_and_path_separators():
    from brr.operator_console.tui import _wrap_command

    lines = _wrap_command("a=1 && b=2 | c=3")
    assert lines == ["a=1", "&& b=2", "| c=3"]

    # One long unbroken token is almost always a path; a break mid-segment
    # renders a path that is not the path.
    long_path = "/very/long/root/" + "segment/" * 20 + "leaf.txt"
    for line in _wrap_command(long_path, width=40):
        assert line.endswith("/") or line.endswith("leaf.txt")
