"""Tests for the gate-agnostic run progress projection over conversation logs."""

from __future__ import annotations

import datetime
from pathlib import Path

from brr import conversations, run_progress, updates


def _emit(brr_dir: Path, key: str, ptype: str, **payload):
    updates.emit(
        brr_dir,
        updates.UpdatePacket(type=ptype, conversation_key=key, payload=payload),
    )


def test_project_task_returns_none_when_conversation_missing(tmp_path):
    view = run_progress.project_task(tmp_path / ".brr", "no-such", "task-x")
    assert view is None


def test_project_task_succeeds_through_full_lifecycle(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:1:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-1", event_id="evt-1",
        env="docker", status="running",
        seed_ref="main", auto_land_branch="main",
        branch_name="brr/task-1",
    )
    _emit(brr_dir, key, "task_created", task_id="task-1", event_id="evt-1",
          env="docker")
    _emit(brr_dir, key, "env_prepared", task_id="task-1", env="docker",
          branch_name="brr/task-1")
    _emit(brr_dir, key, "container_started", task_id="task-1",
          env="docker", container="brr-task-1-evt-1-attempt-1")
    _emit(brr_dir, key, "attempt_started", task_id="task-1", attempt=1)
    _emit(brr_dir, key, "run_started", task_id="task-1", branch="brr/task-1",
          env="docker")
    _emit(brr_dir, key, "artifact_created", task_id="task-1", kind="response",
          path="/tmp/r.md", label="response:evt-1")
    conversations.append_artifact(
        brr_dir, key,
        kind="response", path="/tmp/r.md",
        task_id="task-1", label="response:evt-1",
    )
    _emit(brr_dir, key, "finalizing", task_id="task-1", stage="done")
    _emit(brr_dir, key, "done", task_id="task-1", event_id="evt-1")

    view = run_progress.project_task(brr_dir, key, "task-1")
    assert view is not None
    assert view.state == "succeeded"
    assert view.phase == "delivered"
    assert view.is_terminal is True
    assert view.branch_name == "brr/task-1"
    assert view.env == "docker"
    assert view.attempt == 1
    assert view.response_path == "/tmp/r.md"
    assert "brr-task-1-evt-1-attempt-1" in view.container_ids


def test_project_task_failed_with_retry(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:2:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-2", event_id="evt-2",
        env="worktree", status="running",
    )
    _emit(brr_dir, key, "task_created", task_id="task-2", event_id="evt-2",
          env="worktree")
    _emit(brr_dir, key, "attempt_started", task_id="task-2", attempt=1)
    _emit(brr_dir, key, "attempt_failed", task_id="task-2", attempt=1,
          reason="missing required output(s): response:evt-2", will_retry=True)
    _emit(brr_dir, key, "retrying", task_id="task-2", attempt=2,
          reason="missing required output(s): response:evt-2")
    _emit(brr_dir, key, "attempt_started", task_id="task-2", attempt=2)
    _emit(brr_dir, key, "attempt_failed", task_id="task-2", attempt=2,
          reason="missing required output(s)", will_retry=False)
    _emit(brr_dir, key, "failed", task_id="task-2", event_id="evt-2", stage="run")

    view = run_progress.project_task(brr_dir, key, "task-2")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "failed"
    assert view.attempt == 2


def test_project_task_conflict(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:4:"
    _emit(brr_dir, key, "task_created", task_id="task-4", env="worktree")
    _emit(brr_dir, key, "done", task_id="task-4")
    _emit(brr_dir, key, "conflict", task_id="task-4", branch="brr/task-4")

    view = run_progress.project_task(brr_dir, key, "task-4")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "conflict"
    assert view.status_label() == "conflict"
    assert "brr/task-4" in view.detail


def test_project_task_failure_detail_survives_finalizing(tmp_path):
    """The daemon emits ``finalizing(stage=failed)`` before ``failed`` so
    the operator's view ends on the real error rather than the generic
    "finalizing (failed)" placeholder. The projection should fold them
    in that order and end with the failed packet's detail."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:f:"
    _emit(brr_dir, key, "task_created", task_id="task-f", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-f", attempt=1)
    _emit(brr_dir, key, "attempt_failed", task_id="task-f", attempt=1,
          reason="timed out", will_retry=False, exit_code=124, timed_out=True)
    _emit(brr_dir, key, "finalizing", task_id="task-f", stage="failed")
    _emit(brr_dir, key, "failed", task_id="task-f", stage="run", attempts=1,
          exit_code=124, timed_out=True,
          error="runner timed out after 3600s")

    view = run_progress.project_task(brr_dir, key, "task-f")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "failed"
    assert "timed out" in view.detail
    assert "runner timed out after 3600s" in view.detail
    assert "finalizing" not in view.detail
    assert view.error == "runner timed out after 3600s"


def test_project_task_container_preserved(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:5:"
    _emit(brr_dir, key, "task_created", task_id="task-5",
          env="docker")
    _emit(brr_dir, key, "container_preserved", task_id="task-5",
          containers=["brr-task-5-attempt-1", "brr-task-5-attempt-2"])
    _emit(brr_dir, key, "failed", task_id="task-5", stage="run")

    view = run_progress.project_task(brr_dir, key, "task-5")
    assert view is not None
    assert view.state == "failed"
    assert view.container_ids == [
        "brr-task-5-attempt-1",
        "brr-task-5-attempt-2",
    ]


def test_project_conversation_latest_picks_most_recent_task(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:6:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-old", event_id="evt-old",
        env="host", status="done",
    )
    conversations.append_task(
        brr_dir, key,
        task_id="task-new", event_id="evt-new",
        env="docker", status="running",
    )
    _emit(brr_dir, key, "task_created", task_id="task-new",
          env="docker")
    _emit(brr_dir, key, "run_started", task_id="task-new")

    view = run_progress.project_conversation_latest(brr_dir, key)
    assert view is not None
    assert view.task_id == "task-new"
    assert view.state == "active"


def test_project_conversation_latest_returns_none_when_no_tasks(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:7:"
    conversations.append_event(brr_dir, key, {"id": "evt-1", "source": "telegram"})
    view = run_progress.project_conversation_latest(brr_dir, key)
    assert view is None


def test_projection_captures_runner_name_from_run_started(tmp_path):
    """``run_started`` carries the resolved runner name so the chat
    card header can show ``codex``/``claude`` etc. without the gate
    re-querying state."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:1:"
    _emit(brr_dir, key, "task_created", task_id="task-rn", env="docker")
    _emit(brr_dir, key, "run_started", task_id="task-rn",
          runner="codex", branch="brr/task-rn", env="docker")

    view = run_progress.project_task(brr_dir, key, "task-rn")
    assert view is not None
    assert view.runner_name == "codex"
    assert view.branch_name == "brr/task-rn"


def test_projection_treats_heartbeat_as_no_op(tmp_path):
    """Heartbeats must not push new phase entries — they only exist to
    re-trigger gate renders so the live elapsed counter ticks."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:hb:"
    _emit(brr_dir, key, "task_created", task_id="task-hb", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-hb", attempt=1)
    before = run_progress.project_task(brr_dir, key, "task-hb")
    assert before is not None
    assert len(before.phase_history) == 2

    _emit(brr_dir, key, "heartbeat", task_id="task-hb",
          attempt=1, elapsed_seconds=30)
    _emit(brr_dir, key, "heartbeat", task_id="task-hb",
          attempt=1, elapsed_seconds=60)

    after = run_progress.project_task(brr_dir, key, "task-hb")
    assert after is not None
    assert len(after.phase_history) == 2
    # Phase shape unchanged: still on the live "running" entry.
    assert after.phase_history[-1].name == "running"
    assert after.phase_history[-1].ended_at is None


def test_projection_records_separate_running_entries_per_attempt(tmp_path):
    """Each attempt opens its own ``running`` entry so the strike-
    through log ends up with one struck line per finished attempt."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:rt:"
    _emit(brr_dir, key, "task_created", task_id="task-r", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-r", attempt=1)
    _emit(brr_dir, key, "attempt_failed", task_id="task-r", attempt=1,
          reason="missing required output(s)", will_retry=True)
    _emit(brr_dir, key, "retrying", task_id="task-r", attempt=2)
    _emit(brr_dir, key, "attempt_started", task_id="task-r", attempt=2)

    view = run_progress.project_task(brr_dir, key, "task-r")
    assert view is not None
    running = [e for e in view.phase_history if e.name == "running"]
    assert [e.attempt for e in running] == [1, 2]
    # First running entry is closed (next attempt opened a fresh one),
    # second is the live one.
    assert running[0].ended_at is not None
    assert running[1].ended_at is None


def test_render_text_compact_has_runner_env_branch_header(tmp_path):
    """Compact card opens with a sticky ``runner · env · branch ← base``
    header naming the three things that don't change once a task starts.
    Task ID is dev-side noise in a chat reply and stays out."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8:"
    _emit(brr_dir, key, "task_created", task_id="task-r", env="docker")
    _emit(brr_dir, key, "env_prepared", task_id="task-r", env="docker",
          branch_name="brr/task-r")
    _emit(brr_dir, key, "attempt_started", task_id="task-r", attempt=1)
    _emit(brr_dir, key, "run_started", task_id="task-r",
          runner="codex", branch="brr/task-r", env="docker")
    # Backfill the display base the same way daemon._run_worker does
    # via the task record (env_prepared doesn't carry seed_ref by name).
    conversations.append_task(
        brr_dir, key,
        task_id="task-r", event_id="evt-r",
        env="docker", status="running",
        seed_ref="main", auto_land_branch="main",
        branch_name="brr/task-r",
    )

    view = run_progress.project_task(brr_dir, key, "task-r")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    header = text.splitlines()[0]
    assert header == "codex · docker · brr/task-r ← main"
    # Phase log: no "phase:" labels (those belong to the verbose form),
    # and the bare task ID never leaks in — task-r only appears as part
    # of the branch name in the header.
    assert "phase:" not in text
    assert text.count("task-r") == 1  # only inside the branch name
    assert "running" in text


def test_render_text_compact_surfaces_kb_maintenance_done(tmp_path):
    """When the inline maintenance pass committed kb edits, the
    response card surfaces it on the terminal line so the operator
    sees that cleanup landed on the task's branch. Without this,
    maintenance was historically a silent drop."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:9:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-m", event_id="evt-m",
        env="docker", status="running",
        seed_ref="main", auto_land_branch=None,
        branch_name="brr/task-m",
    )
    _emit(brr_dir, key, "task_created", task_id="task-m", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-m", attempt=1)
    _emit(brr_dir, key, "kb_maintenance_done", task_id="task-m",
          commits=2, files=3, ok=True)
    _emit(brr_dir, key, "finalizing", task_id="task-m", stage="done")
    _emit(brr_dir, key, "push_done", task_id="task-m", branch="brr/task-m",
          commits=1, ok=True)
    _emit(brr_dir, key, "done", task_id="task-m", event_id="evt-m")

    view = run_progress.project_task(brr_dir, key, "task-m")
    assert view is not None
    assert view.maintenance_ran is True
    assert view.maintenance_commits == 2
    text = run_progress.render_text(view, compact=True)
    # delivered line carries both push and maintenance summaries.
    delivered = [
        line for line in text.splitlines() if line.startswith("delivered")
    ]
    assert delivered, text
    assert "pushed 1 commit" in delivered[0]
    assert "maintenance: 2 kb commits" in delivered[0]


def test_render_text_compact_shows_maintenance_clean_when_no_commits(tmp_path):
    """A maintenance pass that ran with nothing to do still appears
    on the card as 'maintenance: clean'. Suppressing the line would
    hide the fact that brr did the cleanup check at all."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:9a:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-mc", event_id="evt-mc",
        env="docker", status="running",
        seed_ref="main", auto_land_branch=None,
        branch_name="brr/task-mc",
    )
    _emit(brr_dir, key, "task_created", task_id="task-mc", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-mc", attempt=1)
    _emit(brr_dir, key, "kb_maintenance_done", task_id="task-mc",
          commits=0, files=0, ok=True)
    _emit(brr_dir, key, "finalizing", task_id="task-mc", stage="done")
    _emit(brr_dir, key, "done", task_id="task-mc", event_id="evt-mc")

    view = run_progress.project_task(brr_dir, key, "task-mc")
    text = run_progress.render_text(view, compact=True)

    delivered = [
        line for line in text.splitlines() if line.startswith("delivered")
    ]
    assert delivered, text
    assert "maintenance: clean" in delivered[0]


def test_push_done_carries_forge_view_url_into_view(tmp_path):
    """A ``push_done`` packet that includes a forge URL stores it on
    the projection so renderers can surface a clickable link."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:9c:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-fv", event_id="evt-fv",
        env="docker", status="running",
        seed_ref="main", auto_land_branch=None,
        branch_name="brr/task-fv",
    )
    _emit(brr_dir, key, "task_created", task_id="task-fv", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-fv", attempt=1)
    _emit(brr_dir, key, "finalizing", task_id="task-fv", stage="done")
    _emit(
        brr_dir, key, "push_done", task_id="task-fv",
        branch="brr/task-fv", commits=2, ok=True,
        view_url="https://github.com/Gurio/brr/tree/brr/task-fv",
    )
    _emit(brr_dir, key, "done", task_id="task-fv", event_id="evt-fv")

    view = run_progress.project_task(brr_dir, key, "task-fv")
    assert view is not None
    assert view.view_url == "https://github.com/Gurio/brr/tree/brr/task-fv"


def test_render_text_compact_emits_view_url_under_delivered(tmp_path):
    """The forge link gets its own line below the delivered header so
    long URLs don't wrap the duration / push summary. Bare URLs
    auto-link on every gate we render to today, so no markdown
    wrapping is needed."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:9d:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-fl", event_id="evt-fl",
        env="docker", status="running",
        seed_ref="main", auto_land_branch=None,
        branch_name="brr/task-fl",
    )
    _emit(brr_dir, key, "task_created", task_id="task-fl", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-fl", attempt=1)
    _emit(brr_dir, key, "finalizing", task_id="task-fl", stage="done")
    _emit(
        brr_dir, key, "push_done", task_id="task-fl",
        branch="brr/task-fl", commits=1, ok=True,
        view_url="https://github.com/Gurio/brr/tree/brr/task-fl",
    )
    _emit(brr_dir, key, "done", task_id="task-fl", event_id="evt-fl")

    view = run_progress.project_task(brr_dir, key, "task-fl")
    text = run_progress.render_text(view, compact=True)

    lines = text.splitlines()
    delivered_idx = next(
        i for i, line in enumerate(lines) if line.startswith("delivered")
    )
    assert lines[delivered_idx + 1] == (
        "view: https://github.com/Gurio/brr/tree/brr/task-fl"
    )


def test_render_text_compact_omits_view_line_without_url(tmp_path):
    """When push_done has no view_url, the renderer stays quiet — no
    trailing empty line, no placeholder."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:9e:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-fn", event_id="evt-fn",
        env="docker", status="running",
        seed_ref="main", auto_land_branch=None,
        branch_name="brr/task-fn",
    )
    _emit(brr_dir, key, "task_created", task_id="task-fn", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-fn", attempt=1)
    _emit(brr_dir, key, "finalizing", task_id="task-fn", stage="done")
    _emit(
        brr_dir, key, "push_done", task_id="task-fn",
        branch="brr/task-fn", commits=1, ok=True,
    )
    _emit(brr_dir, key, "done", task_id="task-fn", event_id="evt-fn")

    view = run_progress.project_task(brr_dir, key, "task-fn")
    text = run_progress.render_text(view, compact=True)

    assert "view:" not in text


def test_render_text_compact_skips_maintenance_when_not_run(tmp_path):
    """If the maintenance pass was skipped (no findings, kb
    untouched), no packet was emitted; the card stays quiet."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:9b:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-mn", event_id="evt-mn",
        env="docker", status="running",
        seed_ref="main", auto_land_branch=None,
        branch_name="brr/task-mn",
    )
    _emit(brr_dir, key, "task_created", task_id="task-mn", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-mn", attempt=1)
    _emit(brr_dir, key, "finalizing", task_id="task-mn", stage="done")
    _emit(brr_dir, key, "done", task_id="task-mn", event_id="evt-mn")

    view = run_progress.project_task(brr_dir, key, "task-mn")
    text = run_progress.render_text(view, compact=True)

    assert view.maintenance_ran is False
    assert "maintenance" not in text


def test_render_text_compact_omits_arrow_without_auto_land_branch(tmp_path):
    """When there is no explicit auto-land target, the header shows just the
    branch name. The seed_ref (where the branch was cut from) is a setup
    detail and should NOT be rendered as a landing target.

    Previously ``display_base`` fell back to ``seed_ref``, which made every
    task card claim it was landing on `main` even when the agent picked its
    own branch with no auto-merge intent. That was misleading enough to
    surface a real merge surprise in chat.
    """
    brr_dir = tmp_path / ".brr"
    key = "telegram:8b:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-na", event_id="evt-na",
        env="docker", status="running",
        seed_ref="main", auto_land_branch=None,
        branch_name="brr/task-na",
    )
    _emit(brr_dir, key, "task_created", task_id="task-na", env="docker")
    _emit(brr_dir, key, "env_prepared", task_id="task-na", env="docker",
          branch_name="brr/task-na", seed_ref="main")
    _emit(brr_dir, key, "attempt_started", task_id="task-na", attempt=1)
    _emit(brr_dir, key, "run_started", task_id="task-na",
          runner="codex", branch="brr/task-na", env="docker",
          seed_ref="main")

    view = run_progress.project_task(brr_dir, key, "task-na")
    assert view is not None
    assert view.display_base is None
    text = run_progress.render_text(view, compact=True)
    header = text.splitlines()[0]
    assert header == "codex · docker · brr/task-na"
    assert "←" not in header


def test_render_text_compact_strikes_through_finished_phases(tmp_path):
    """Closed phases get wrapped in the style's done-open/done-close
    markers; the live current line stays unmarked. Plain text gets no
    decoration so the log reads positionally."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8a:"
    _emit(brr_dir, key, "task_created", task_id="task-s", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-s", attempt=1)
    _emit(brr_dir, key, "finalizing", task_id="task-s", stage="done")

    view = run_progress.project_task(brr_dir, key, "task-s")
    assert view is not None

    plain = run_progress.render_text(view, compact=True)
    assert "preparing" in plain
    assert "running" in plain
    assert plain.rstrip().endswith("finalizing")

    html = run_progress.render_text(
        view, compact=True, style=run_progress.TELEGRAM_HTML_STYLE,
    )
    assert "<s>preparing" in html
    assert "</s>" in html
    assert "<s>running" in html
    # Live phase ("finalizing") must NOT be wrapped in <s>.
    assert html.rstrip().endswith("finalizing")
    assert "<s>finalizing" not in html


def test_render_text_compact_running_shows_elapsed(tmp_path):
    """The live ``running`` line bumps elapsed time at render time so
    heartbeats produce visible motion in the chat card."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8c:"
    _emit(brr_dir, key, "task_created", task_id="task-e", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-e", attempt=1)

    view = run_progress.project_task(brr_dir, key, "task-e")
    assert view is not None
    started = run_progress._parse_iso(view.phase_history[-1].started_at)
    assert started is not None
    later = started + datetime.timedelta(seconds=130)
    text = run_progress.render_text(view, compact=True, now=later)
    last_line = text.rstrip().splitlines()[-1]
    assert last_line.startswith("running · ")
    assert "2m 10s" in last_line


def test_render_text_compact_attempt_label_only_when_multi(tmp_path):
    """Single-attempt runs read as plain ``running``; once a second
    attempt starts, every running entry gets an attempt suffix."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8d:"
    _emit(brr_dir, key, "task_created", task_id="task-rt", env="worktree")
    _emit(brr_dir, key, "attempt_started", task_id="task-rt", attempt=1)
    view_one = run_progress.project_task(brr_dir, key, "task-rt")
    assert view_one is not None
    assert "attempt" not in run_progress.render_text(view_one, compact=True)

    _emit(brr_dir, key, "attempt_failed", task_id="task-rt", attempt=1,
          reason="missing required output(s)", will_retry=True)
    _emit(brr_dir, key, "retrying", task_id="task-rt", attempt=2)
    _emit(brr_dir, key, "attempt_started", task_id="task-rt", attempt=2)
    view_retry = run_progress.project_task(brr_dir, key, "task-rt")
    assert view_retry is not None
    text = run_progress.render_text(view_retry, compact=True)
    assert "running (attempt 1)" in text
    assert "running (attempt 2)" in text


def test_render_text_compact_terminal_reports_total_elapsed(tmp_path):
    """The terminal line names the total wall-clock time from event
    arrival to delivery — that's the meaningful "how long did it take"."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8e:"
    _emit(brr_dir, key, "task_created", task_id="task-d", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-d", attempt=1)
    _emit(brr_dir, key, "finalizing", task_id="task-d", stage="done")
    _emit(brr_dir, key, "push_done", task_id="task-d", commits=2, ok=True)
    _emit(brr_dir, key, "done", task_id="task-d", event_id="evt-d")

    view = run_progress.project_task(brr_dir, key, "task-d")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    last_line = text.rstrip().splitlines()[-1]
    assert last_line.startswith("delivered · ")
    assert "pushed 2 commits" in last_line


def test_render_text_compact_failed_keeps_error_below_struck_log(tmp_path):
    """On hard failure the strike-through log ends with ``failed`` and
    the actual error sits on the next line so the chat reader sees the
    problem without having to parse markup."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8f:"
    _emit(brr_dir, key, "task_created", task_id="task-fail", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-fail", attempt=1)
    _emit(brr_dir, key, "finalizing", task_id="task-fail", stage="failed")
    _emit(brr_dir, key, "failed", task_id="task-fail", event_id="evt-fail",
          stage="run", attempts=1, exit_code=124, timed_out=True,
          error="runner timed out after 3600s")

    view = run_progress.project_task(brr_dir, key, "task-fail")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    lines = text.rstrip().splitlines()
    assert any(line.startswith("failed · ") for line in lines)
    assert lines[-1] == "runner timed out after 3600s"


def test_render_text_verbose_keeps_dev_fields(tmp_path):
    """Verbose mode (compact=False) keeps the operator-facing detail."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8z:"
    _emit(brr_dir, key, "task_created", task_id="task-v", env="docker")
    _emit(brr_dir, key, "env_prepared", task_id="task-v", env="docker",
          branch_name="brr/task-v")
    _emit(brr_dir, key, "run_started", task_id="task-v",
          runner="codex", branch="brr/task-v", env="docker")
    _emit(brr_dir, key, "done", task_id="task-v")

    view = run_progress.project_task(brr_dir, key, "task-v")
    assert view is not None
    text = run_progress.render_text(view, compact=False)
    assert "branch: brr/task-v" in text
    assert "env: docker" in text
    assert "runner: codex" in text


def test_render_text_compact_does_not_inject_conversation_identity(tmp_path):
    """Compact card must not surface arbitrary conversation strings.

    This is the key regression from the dropped streams design — old
    code rendered a frozen stream title/intent here. Now there is no
    such field, so any conversation identifier must not leak into the
    compact card unprompted.
    """
    brr_dir = tmp_path / ".brr"
    key = "telegram:99:USER CONTEXTUAL OVERRIDE"
    _emit(brr_dir, key, "task_created", task_id="task-c",
          env="docker")
    _emit(brr_dir, key, "failed", task_id="task-c", stage="env",
          error="docker env requires docker.image in .brr/config")

    view = run_progress.project_task(brr_dir, key, "task-c")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    assert "USER CONTEXTUAL OVERRIDE" not in text


def test_task_id_from_packet():
    packet = updates.UpdatePacket(
        type="task_created", conversation_key="k", payload={"task_id": "task-x"},
    )
    assert run_progress.task_id_from_packet(packet) == "task-x"

    empty = updates.UpdatePacket(type="event_received", conversation_key="k")
    assert run_progress.task_id_from_packet(empty) is None
