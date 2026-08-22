from __future__ import annotations

import json
import os

from brr import conversations, presence
from brr.operator_console.model import collect_snapshot, console_conversation_key


def _write_run(brr, run_id: str, event_id: str, *, prompt: str = "wake") -> None:
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
    (run_dir / "boundaries.jsonl").write_text(
        json.dumps(
            {
                "phase": "PostToolUse",
                "at": "2026-08-22T23:40:18Z",
                "act": "Bash",
                "inject": "pending event evt-followup",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_snapshot_projects_existing_runtime_surfaces(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    brr = repo / ".brr"
    brr.mkdir()

    _write_run(brr, "run-parent", "evt-parent", prompt="assembled wake")
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
    assert snapshot.selected.boundaries[0].act == "Bash"
    assert snapshot.selected.boundaries[0].inject == "pending event evt-followup"
    assert snapshot.selected.portal_state["await"]["outcome"] == "event"
    assert snapshot.selected.inbox_state[0]["id"] == "evt-followup"
    assert "Tracing the steer" in snapshot.selected.card
    assert [row["kind"] for row in snapshot.selected.thread] == ["event", "artifact"]
    assert snapshot.console_key == "console:hugimuni-labs_brnrd"


def test_console_key_has_no_gate_identity(tmp_path):
    repo = tmp_path / "my repo"
    repo.mkdir()
    assert console_conversation_key(repo) == "console:my_repo"
