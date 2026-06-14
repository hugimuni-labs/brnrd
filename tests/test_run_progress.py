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
        seed_ref="main", target_branch="main",
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
    """Heartbeats stay out of memory and only re-trigger gate renders."""
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

    records = conversations.read_records(brr_dir, key)
    assert [r.get("type") for r in records if r.get("kind") == "update"] == [
        "task_created",
        "attempt_started",
    ]

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
        seed_ref="main", target_branch="main",
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


def test_push_done_carries_forge_view_url_into_view(tmp_path):
    """A ``push_done`` packet that includes a forge URL stores it on
    the projection so renderers can surface a clickable link."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:9c:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-fv", event_id="evt-fv",
        env="docker", status="running",
        seed_ref="main", target_branch=None,
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
        seed_ref="main", target_branch=None,
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
        seed_ref="main", target_branch=None,
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


def test_render_text_compact_omits_arrow_without_target_branch(tmp_path):
    """When there is no explicit expected publish target, the header shows
    just the branch name. The seed_ref (where the branch was cut from) is a
    setup detail and should NOT be rendered as a landing target.

    Previously ``display_base`` fell back to ``seed_ref``, which made every
    task card claim it was landing on `main` even when the agent picked its
    own branch with no expected publish intent. That was misleading enough
    to surface a real merge surprise in chat.
    """
    brr_dir = tmp_path / ".brr"
    key = "telegram:8b:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-na", event_id="evt-na",
        env="docker", status="running",
        seed_ref="main", target_branch=None,
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


def test_render_text_compact_reports_push_failure(tmp_path):
    """A failed push is post-response housekeeping: the response can be
    delivered, but the progress card must not claim the commits were
    pushed."""
    brr_dir = tmp_path / ".brr"
    key = "github:17:"
    _emit(brr_dir, key, "task_created", task_id="task-pf", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-pf", attempt=1)
    _emit(brr_dir, key, "finalizing", task_id="task-pf", stage="done")
    _emit(
        brr_dir, key, "push_done",
        task_id="task-pf", commits=5, ok=False,
        error="Permission denied (publickey)",
    )
    _emit(brr_dir, key, "done", task_id="task-pf", event_id="evt-pf")

    view = run_progress.project_task(brr_dir, key, "task-pf")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    last_line = text.rstrip().splitlines()[-1]
    assert "push failed" in last_line
    assert "pushed 5 commits" not in last_line


def test_render_text_compact_failed_keeps_error_below_struck_log(tmp_path):
    """On hard failure the strike-through log ends with the operational
    failure category (``timed out``/``runner failed``/``no reply``) and
    the actual error sits on the next line so the chat reader sees the
    problem without having to parse markup. §8 re-alignment: ``failed``
    on its own is too generic — the card names the runner-side category
    so the operator owning the runner sees it unambiguously."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8f:"
    _emit(brr_dir, key, "task_created", task_id="task-fail", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-fail", attempt=1)
    _emit(brr_dir, key, "finalizing", task_id="task-fail", stage="failed")
    _emit(brr_dir, key, "failed", task_id="task-fail", event_id="evt-fail",
          stage="run", attempts=1, exit_code=124, timed_out=True,
          failure_kind="timed_out",
          error="runner timed out after 3600s")

    view = run_progress.project_task(brr_dir, key, "task-fail")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    lines = text.rstrip().splitlines()
    assert any(line.startswith("timed out · ") for line in lines)
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


# ── Agent-composed card narration (issue #114) ──────────────────────


def test_card_composed_packet_lands_on_view_and_renders_as_note(tmp_path):
    """``card_composed`` updates flow into ``RunProgressView.agent_card_text``
    and the compact card surfaces them as a ``note:`` tail line under the
    live phase. The agent's narration is additive — the daemon's
    lifecycle scaffolding (header, phase log) is unchanged."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:agent-card:"
    _emit(brr_dir, key, "task_created", task_id="task-ac", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-ac", attempt=1)
    _emit(brr_dir, key, "card_composed", task_id="task-ac",
          text="scanning packet types and wiring the new seam")

    view = run_progress.project_task(brr_dir, key, "task-ac")
    assert view is not None
    assert view.agent_card_text == "scanning packet types and wiring the new seam"

    text = run_progress.render_text(view, compact=True)
    lines = text.rstrip().splitlines()
    # Live "running" line still present (daemon-owned lifecycle), the
    # agent narration follows as the card's tail.
    assert any(line.startswith("running") for line in lines)
    assert lines[-1] == "note: scanning packet types and wiring the new seam"


def test_card_composed_latest_replaces_earlier_text(tmp_path):
    """The agent's narration is single-source: a fresh ``card_composed``
    replaces the previous text (the resident rewrites the file as its
    context shifts; the projection keeps only the latest)."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:agent-card-rewrite:"
    _emit(brr_dir, key, "task_created", task_id="task-ar", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-ar", attempt=1)
    _emit(brr_dir, key, "card_composed", task_id="task-ar", text="first pass")
    _emit(brr_dir, key, "card_composed", task_id="task-ar", text="second pass")

    view = run_progress.project_task(brr_dir, key, "task-ar")
    assert view is not None
    assert view.agent_card_text == "second pass"

    text = run_progress.render_text(view, compact=True)
    assert "second pass" in text
    assert "first pass" not in text


def test_card_composed_empty_text_withdraws_note(tmp_path):
    """An empty/whitespace-only ``card_composed`` text clears the note so
    the agent can pull its narration back. The card falls cleanly back
    to the daemon-rendered phase log."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:agent-card-clear:"
    _emit(brr_dir, key, "task_created", task_id="task-cl", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-cl", attempt=1)
    _emit(brr_dir, key, "card_composed", task_id="task-cl", text="narration")
    _emit(brr_dir, key, "card_composed", task_id="task-cl", text="")

    view = run_progress.project_task(brr_dir, key, "task-cl")
    assert view is not None
    assert view.agent_card_text is None
    text = run_progress.render_text(view, compact=True)
    assert "note:" not in text


def test_card_composed_truncates_overlong_text(tmp_path):
    """The renderer caps the note at ``_AGENT_CARD_MAX_CHARS`` so a
    runaway narration can't flood a single chat card. The daemon side
    applies a similar byte cap on read."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:agent-card-long:"
    _emit(brr_dir, key, "task_created", task_id="task-lg", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-lg", attempt=1)
    long_text = "x" * (run_progress._AGENT_CARD_MAX_CHARS + 200)
    _emit(brr_dir, key, "card_composed", task_id="task-lg", text=long_text)

    view = run_progress.project_task(brr_dir, key, "task-lg")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    last_line = text.rstrip().splitlines()[-1]
    assert last_line.startswith("note: ")
    # +1 for the ellipsis appended on truncation, minus the "note: " prefix.
    payload = last_line[len("note: "):]
    assert len(payload) <= run_progress._AGENT_CARD_MAX_CHARS + 1
    assert payload.endswith("…")


def test_card_composed_survives_terminal_state(tmp_path):
    """The agent's last narration is preserved on the terminal card so
    a reader sees what the resident said it was doing when it finished."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:agent-card-terminal:"
    _emit(brr_dir, key, "task_created", task_id="task-tm", env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-tm", attempt=1)
    _emit(brr_dir, key, "card_composed", task_id="task-tm",
          text="wrote the agent card seam and tests")
    _emit(brr_dir, key, "finalizing", task_id="task-tm", stage="done")
    _emit(brr_dir, key, "done", task_id="task-tm", event_id="evt-tm")

    view = run_progress.project_task(brr_dir, key, "task-tm")
    assert view is not None
    assert view.state == "succeeded"
    assert view.agent_card_text == "wrote the agent card seam and tests"

    text = run_progress.render_text(view, compact=True)
    lines = text.rstrip().splitlines()
    assert any(line.startswith("delivered") for line in lines)
    assert lines[-1] == "note: wrote the agent card seam and tests"


def test_card_composed_is_in_card_packets_so_gates_rerender():
    """The packet must be in ``CARD_PACKETS`` so every gate that drives
    the live card re-renders when the resident rewrites its narration."""
    assert "card_composed" in run_progress.CARD_PACKETS


# ── §8 re-alignment: success-signal axis & multi-thread delivery ────


def test_done_packet_carries_success_signal_and_delivery_counts(tmp_path):
    """The §8 re-alignment lands the success-signal axis on the ``done``
    packet, so the projection knows *what kind* of success closed the run
    (current_reply / other_reply / outbound / commit / internal) and the
    multi-thread delivery shape (replies_current / replies_other /
    outbound_messages / committed)."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:11:"
    _emit(brr_dir, key, "task_created", task_id="task-multi")
    _emit(brr_dir, key, "attempt_started", task_id="task-multi", attempt=1)
    _emit(brr_dir, key, "done", task_id="task-multi", event_id="evt-x",
          success_signal="current_reply",
          replies_current=1, replies_other=2, outbound_messages=1,
          committed=True)

    view = run_progress.project_task(brr_dir, key, "task-multi")
    assert view is not None
    assert view.success_signal == "current_reply"
    assert view.replies_current == 1
    assert view.replies_other == 2
    assert view.outbound_messages == 1
    assert view.committed is True


def test_render_multi_thread_delivery_on_terminal_line(tmp_path):
    """A run that answered the current thread plus one folded-in event
    plus an out-of-bound gate send reads as ``delivered to 2 threads ·
    sent 1 out-of-bound message`` on the terminal line — not collapsed
    to a single-thread ``delivered``. §8 want #3."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:12:"
    _emit(brr_dir, key, "task_created", task_id="task-T",
          env="docker")
    _emit(brr_dir, key, "attempt_started", task_id="task-T", attempt=1)
    _emit(brr_dir, key, "done", task_id="task-T", event_id="evt-T",
          success_signal="current_reply",
          replies_current=1, replies_other=1, outbound_messages=1)

    view = run_progress.project_task(brr_dir, key, "task-T")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    assert "delivered to 2 threads" in text
    assert "sent 1 out-of-bound message" in text


def test_render_single_thread_delivery_stays_uncluttered(tmp_path):
    """The common single-thread case (current_reply only) doesn't get a
    multi-thread suffix — the card stays as terse as before for the
    overwhelming majority of runs."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:13:"
    _emit(brr_dir, key, "task_created", task_id="task-S")
    _emit(brr_dir, key, "attempt_started", task_id="task-S", attempt=1)
    _emit(brr_dir, key, "done", task_id="task-S", event_id="evt-S",
          success_signal="current_reply",
          replies_current=1, replies_other=0, outbound_messages=0)

    view = run_progress.project_task(brr_dir, key, "task-S")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    assert "delivered to" not in text
    assert "out-of-bound" not in text


def test_render_commit_only_success_says_committed_no_reply(tmp_path):
    """A run that closed via the ``commit`` success signal (the agent
    pushed work without replying on the addressed thread) renders as
    ``delivered · committed; no reply`` so the user understands the
    silence is intentional, not a drop. §8 want #1 (commit signal)."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:14:"
    _emit(brr_dir, key, "task_created", task_id="task-C")
    _emit(brr_dir, key, "attempt_started", task_id="task-C", attempt=1)
    _emit(brr_dir, key, "done", task_id="task-C", event_id="evt-C",
          success_signal="commit",
          replies_current=0, replies_other=0, outbound_messages=0,
          committed=True)

    view = run_progress.project_task(brr_dir, key, "task-C")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    assert "committed; no reply" in text


def test_render_internal_event_success_stays_terse(tmp_path):
    """Internal-event success (e.g. schedule fire that didn't deliver to
    a user thread) doesn't decorate the terminal line — there is no
    'where' to surface. §8 want #1 (internal signal)."""
    brr_dir = tmp_path / ".brr"
    key = "schedule:reconcile:"
    _emit(brr_dir, key, "task_created", task_id="task-I")
    _emit(brr_dir, key, "attempt_started", task_id="task-I", attempt=1)
    _emit(brr_dir, key, "done", task_id="task-I", event_id="evt-I",
          success_signal="internal",
          replies_current=0, replies_other=0, outbound_messages=0)

    view = run_progress.project_task(brr_dir, key, "task-I")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    assert "delivered to" not in text
    assert "committed; no reply" not in text


# ── §8 re-alignment: operational-failure distinction ───────────────


def test_render_timed_out_renames_the_failed_terminal_line(tmp_path):
    """A timed-out runner renders as ``timed out · 4m 02s`` on the
    terminal line — operationally distinct from a generic ``failed``
    so the operator (who owns the runner) sees the category at a
    glance. §8 want #2."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:21:"
    _emit(brr_dir, key, "task_created", task_id="task-To")
    _emit(brr_dir, key, "attempt_started", task_id="task-To", attempt=1)
    _emit(brr_dir, key, "failed", task_id="task-To", event_id="evt-To",
          stage="run", attempts=1, exit_code=124,
          timed_out=True, failure_kind="timed_out",
          error="runner timed out after 3600s")

    view = run_progress.project_task(brr_dir, key, "task-To")
    assert view is not None
    assert view.failure_kind == "timed_out"
    text = run_progress.render_text(view, compact=True)
    lines = text.rstrip().splitlines()
    assert any(line.startswith("timed out · ") for line in lines)
    assert "runner timed out after 3600s" in text


def test_render_runner_error_renames_the_failed_terminal_line(tmp_path):
    """A non-zero exit renders as ``runner failed · …`` — calling out
    that the runner process (the operator's owned infra) died, not the
    daemon or the agent's reasoning. §8 want #2."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:22:"
    _emit(brr_dir, key, "task_created", task_id="task-R")
    _emit(brr_dir, key, "attempt_started", task_id="task-R", attempt=1)
    _emit(brr_dir, key, "failed", task_id="task-R", event_id="evt-R",
          stage="run", attempts=1, exit_code=1,
          failure_kind="runner_error",
          error="connection dropped")

    view = run_progress.project_task(brr_dir, key, "task-R")
    assert view is not None
    assert view.failure_kind == "runner_error"
    text = run_progress.render_text(view, compact=True)
    lines = text.rstrip().splitlines()
    assert any(line.startswith("runner failed · ") for line in lines)


def test_render_no_output_failure_renames_the_failed_terminal_line(tmp_path):
    """A clean exit with no signal renders as ``no reply · …`` — distinct
    from an operational failure so the user can tell apart 'the runner
    died' from 'the runner ran but produced nothing'. §8 want #2."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:23:"
    _emit(brr_dir, key, "task_created", task_id="task-N")
    _emit(brr_dir, key, "attempt_started", task_id="task-N", attempt=1)
    _emit(brr_dir, key, "failed", task_id="task-N", event_id="evt-N",
          stage="run", attempts=1, failure_kind="no_output")

    view = run_progress.project_task(brr_dir, key, "task-N")
    assert view is not None
    assert view.failure_kind == "no_output"
    text = run_progress.render_text(view, compact=True)
    lines = text.rstrip().splitlines()
    assert any(line.startswith("no reply · ") for line in lines)


def test_failure_kind_inferred_from_legacy_payloads(tmp_path):
    """Older logs may carry ``timed_out=True`` without the explicit
    ``failure_kind`` field — the projection infers it so existing
    conversations still render the new category labels."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:24:"
    _emit(brr_dir, key, "task_created", task_id="task-L")
    _emit(brr_dir, key, "attempt_started", task_id="task-L", attempt=1)
    # Legacy: no failure_kind, but timed_out=True
    _emit(brr_dir, key, "failed", task_id="task-L", event_id="evt-L",
          stage="run", attempts=1, exit_code=124, timed_out=True,
          error="…")
    view = run_progress.project_task(brr_dir, key, "task-L")
    assert view is not None
    assert view.failure_kind == "timed_out"


def test_failed_packet_is_in_card_packets():
    """Re-aligned ``failed`` rendering must trigger a card re-render —
    the operational-failure category change is exactly what the gate
    needs to redraw to surface."""
    assert "failed" in run_progress.CARD_PACKETS
    assert "done" in run_progress.CARD_PACKETS
