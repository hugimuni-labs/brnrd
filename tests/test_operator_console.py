from __future__ import annotations

import asyncio
import hashlib
import json
import os

from brr import conversations, presence
from brr.operator_console.model import collect_snapshot, console_conversation_key
from brr.operator_console.tui import _boot, _portals


def _write_run(
    brr,
    run_id: str,
    event_id: str,
    *,
    prompt: str = "wake",
    session_start: bool = False,
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
    assert "pending" in text
    assert "await" in text
    # Raw JSON NOT present — no big dump
    assert "RAW PORTAL STATE" not in text
    assert "RAW INBOX" not in text
    # Hint that the toggle exists
    assert "[j]" in text


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


def test_set_log_scroll_preservation():
    """
    Drive scroll-preservation using Textual's pilot harness (asyncio.run so no
    pytest-asyncio plugin is needed).  Three scenarios:
      (a) unchanged content → scroll position completely preserved
      (b) changed content, user scrolled up → not forced to end
      (c) changed content, user at tail → stays at tail
    """
    try:
        from textual.app import App, ComposeResult
        from textual.widgets import RichLog
    except ImportError:
        import pytest

        pytest.skip("textual not installed")

    MANY_LINES = "\n".join(f"line {i:03d}" for i in range(200))

    class ScrollTestApp(App):
        """Minimal app exercising _set_log scroll logic in isolation."""

        def compose(self) -> ComposeResult:
            yield RichLog(id="log", auto_scroll=False, wrap=False, markup=False)

        def on_mount(self) -> None:
            self._last_content: dict[str, str] = {}

        def set_log(self, selector: str, text: str) -> None:
            """Mirror of OperatorConsole._set_log."""
            if self._last_content.get(selector) == text:
                return
            log = self.query_one(selector, RichLog)
            was_at_end = log.is_vertical_scroll_end
            log.clear()
            log.write(text or "—")
            if was_at_end:
                log.scroll_end(animate=False)
            self._last_content[selector] = text

    async def _run() -> None:
        app = ScrollTestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            log = app.query_one("#log", RichLog)

            # Seed with content (write line-by-line so RichLog definitely has
            # more lines than the 24-row visible area — a single multiline
            # string may not settle before the next layout pass).
            for line in MANY_LINES.splitlines():
                log.write(line)
            app._last_content["#log"] = MANY_LINES  # prime cache
            await pilot.pause(0.1)

            # --- Prime: scroll to a known mid-position for scenario (a) ---
            log.scroll_end(animate=False)
            await pilot.pause(0.05)
            assert log.is_vertical_scroll_end, "should be at tail after explicit scroll_end"
            log.scroll_to(y=0, animate=False)
            await pilot.pause(0.05)
            assert not log.is_vertical_scroll_end, "should not be at tail after scroll to top"

            # --- (a) unchanged content → scroll position completely preserved ---
            scroll_before = log.scroll_y
            app.set_log("#log", MANY_LINES)  # same content — cache hit, no widget touch
            await pilot.pause(0.05)
            assert log.scroll_y == scroll_before, "scroll moved on no-op update"

            # --- (c) changed content, user at tail → stays at tail ---
            log.scroll_end(animate=False)
            await pilot.pause(0.05)
            assert log.is_vertical_scroll_end, "pre-condition: at tail"
            app.set_log("#log", MANY_LINES + "\nextra line appended")
            await pilot.pause(0.05)
            assert log.is_vertical_scroll_end, "tail user should stay at tail after content change"

            # --- (b) changed content, user scrolled up → not forced to end ---
            log.scroll_to(y=0, animate=False)
            await pilot.pause(0.05)
            assert not log.is_vertical_scroll_end, "pre-condition: not at tail"
            app.set_log("#log", MANY_LINES + "\nanother update")
            await pilot.pause(0.05)
            assert not log.is_vertical_scroll_end, (
                "user who scrolled up should not be yanked to end on content change"
            )

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
