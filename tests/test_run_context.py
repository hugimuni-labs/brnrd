"""Tests for run_context.render_context — the persisted context.md render.

Focused on the "Original Event Body" section, which mirrors the live
Run Context Bundle in prompts.py: both must surface an event's downloaded
image attachments (see protocol.event_attachment_paths) so a resident
reading either surface knows to ``Read`` the local file.
"""

from __future__ import annotations

from pathlib import Path

from brr import protocol, run_context
from brr.envs import RunContext
from brr.run import Run


def _make_ctx(tmp_path: Path) -> RunContext:
    return RunContext(
        name="worktree",
        cwd=tmp_path,
        repo_root=tmp_path,
        runtime_dir=tmp_path / ".brr",
        response_path_host=tmp_path / ".brr" / "responses" / "evt-1.md",
        response_path_env=tmp_path / ".brr" / "responses" / "evt-1.md",
    )


def _make_task() -> Run:
    return Run(id="task-1", event_id="evt-1", body="look at this")


def test_render_context_lists_attachment_paths(tmp_path):
    inbox = tmp_path / ".brr" / "inbox"
    src = tmp_path / "src.png"
    src.write_bytes(b"data")
    protocol.create_event(
        inbox, source="telegram", body="check this out", attachment_files=[src],
    )
    event = protocol.list_pending(inbox)[0]

    rendered = run_context.render_context(_make_task(), event, _make_ctx(tmp_path))

    assert "Original Event Body" in rendered
    assert "check this out" in rendered
    assert "Attachments" in rendered
    attachment_path = protocol.event_attachment_paths(event)[0]
    assert str(attachment_path) in rendered


def test_render_context_shows_attachments_section_with_empty_body(tmp_path):
    inbox = tmp_path / ".brr" / "inbox"
    src = tmp_path / "src.png"
    src.write_bytes(b"data")
    protocol.create_event(inbox, source="telegram", body="", attachment_files=[src])
    event = protocol.list_pending(inbox)[0]

    rendered = run_context.render_context(_make_task(), event, _make_ctx(tmp_path))

    assert "Original Event Body" in rendered
    assert "Attachments" in rendered


def test_render_context_omits_section_with_no_body_and_no_attachments(tmp_path):
    inbox = tmp_path / ".brr" / "inbox"
    protocol.create_event(inbox, source="telegram", body="")
    event = protocol.list_pending(inbox)[0]

    rendered = run_context.render_context(_make_task(), event, _make_ctx(tmp_path))

    assert "Original Event Body" not in rendered


def test_render_context_names_host_publication_ownership(tmp_path):
    ctx = _make_ctx(tmp_path)
    ctx.name = "host"

    rendered = run_context.render_context(_make_task(), {}, ctx)

    assert "Environment: host — shared checkout" in rendered
    assert "host finalization does not publish commits" in rendered
    assert "own the push / PR handoff" in rendered
