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


def test_project_run_returns_none_when_conversation_missing(tmp_path):
    view = run_progress.project_run(tmp_path / ".brr", "no-such", "run-x")
    assert view is None


def test_project_run_succeeds_through_full_lifecycle(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:1:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-1", event_id="evt-1",
        env="docker", status="running",
        seed_ref="main", target_branch="main",
        branch_name="brr/run-1",
    )
    _emit(brr_dir, key, "run_created", run_id="run-1", event_id="evt-1",
          env="docker")
    _emit(brr_dir, key, "env_prepared", run_id="run-1", env="docker",
          branch_name="brr/run-1")
    _emit(brr_dir, key, "container_started", run_id="run-1",
          env="docker", container="brr-run-1-evt-1-attempt-1")
    _emit(brr_dir, key, "attempt_started", run_id="run-1", attempt=1)
    _emit(brr_dir, key, "run_started", run_id="run-1", branch="brr/run-1",
          env="docker")
    _emit(brr_dir, key, "artifact_created", run_id="run-1", kind="response",
          path="/tmp/r.md", label="response:evt-1")
    conversations.append_artifact(
        brr_dir, key,
        kind="response", path="/tmp/r.md",
        run_id="run-1", label="response:evt-1",
    )
    _emit(brr_dir, key, "finalizing", run_id="run-1", stage="done")
    _emit(brr_dir, key, "done", run_id="run-1", event_id="evt-1")

    view = run_progress.project_run(brr_dir, key, "run-1")
    assert view is not None
    assert view.state == "succeeded"
    assert view.phase == "delivered"
    assert view.is_terminal is True
    assert view.branch_name == "brr/run-1"
    assert view.env == "docker"
    assert view.attempt == 1
    assert view.response_path == "/tmp/r.md"
    assert "brr-run-1-evt-1-attempt-1" in view.container_ids


def test_project_run_ignores_anonymous_task_era_records(tmp_path):
    """Old task-era conversation records did not carry ``run_id``.

    After run manifests landed, treating those anonymous records as
    "maybe current" made every new run card inherit stale phase history
    from the whole thread. Per-run projection must require an explicit
    run_id match.
    """
    brr_dir = tmp_path / ".brr"
    key = "telegram:legacy-thread:"

    _emit(brr_dir, key, "run_created", event_id="evt-old", env="docker")
    _emit(brr_dir, key, "attempt_started", event_id="evt-old", attempt=1)
    _emit(brr_dir, key, "done", event_id="evt-old")

    _emit(brr_dir, key, "run_created", run_id="run-new",
          event_id="evt-new", env="host")
    _emit(brr_dir, key, "attempt_started", run_id="run-new",
          event_id="evt-new", attempt=1)

    view = run_progress.project_run(brr_dir, key, "run-new")
    assert view is not None
    assert view.phase == "running"
    assert [entry.name for entry in view.phase_history] == [
        "preparing",
        "running",
    ]
    text = run_progress.render_text(view, compact=True)
    assert "delivered" not in text


def test_project_run_failed_with_retry(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:2:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-2", event_id="evt-2",
        env="worktree", status="running",
    )
    _emit(brr_dir, key, "run_created", run_id="run-2", event_id="evt-2",
          env="worktree")
    _emit(brr_dir, key, "attempt_started", run_id="run-2", attempt=1)
    _emit(brr_dir, key, "attempt_failed", run_id="run-2", attempt=1,
          reason="missing required output(s): response:evt-2", will_retry=True)
    _emit(brr_dir, key, "retrying", run_id="run-2", attempt=2,
          reason="missing required output(s): response:evt-2")
    _emit(brr_dir, key, "attempt_started", run_id="run-2", attempt=2)
    _emit(brr_dir, key, "attempt_failed", run_id="run-2", attempt=2,
          reason="missing required output(s)", will_retry=False)
    _emit(brr_dir, key, "failed", run_id="run-2", event_id="evt-2", stage="run")

    view = run_progress.project_run(brr_dir, key, "run-2")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "failed"
    assert view.attempt == 2


def test_retrying_packet_can_record_runner_fallback(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:fallback:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-fallback", event_id="evt-fallback",
        env="worktree", status="running",
    )
    _emit(brr_dir, key, "run_created", run_id="run-fallback", event_id="evt-fallback",
          env="worktree")
    _emit(brr_dir, key, "run_started", run_id="run-fallback", runner="codex")
    _emit(brr_dir, key, "attempt_started", run_id="run-fallback", attempt=1)
    _emit(brr_dir, key, "attempt_failed", run_id="run-fallback", attempt=1,
          reason="session limit", failure_kind="quota_exhausted",
          will_retry=False, will_fallback=True, fallback_runner="claude")
    _emit(brr_dir, key, "retrying", run_id="run-fallback", attempt=2,
          reason="fallback after quota_exhausted", from_runner="codex",
          runner="claude")

    view = run_progress.project_run(brr_dir, key, "run-fallback")

    assert view is not None
    assert view.runner_name == "claude"
    assert view.detail == "fallback codex -> claude (attempt 2)"
    assert view.attempt_history[0].reason == "session limit"
    assert view.attempt_history[0].failure_kind == "quota_exhausted"
    assert view.attempt_history[0].fallback_runner == "claude"


def test_render_text_compact_surfaces_attempt_failure_ledger(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:attempt-ledger:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-ledger", event_id="evt-ledger",
        env="host", status="running",
        branch_name="brr/run-ledger",
    )
    _emit(brr_dir, key, "run_created", run_id="run-ledger", env="host")
    _emit(brr_dir, key, "run_started", run_id="run-ledger",
          runner="codex", branch="brr/run-ledger")
    _emit(brr_dir, key, "attempt_started", run_id="run-ledger", attempt=1)
    _emit(brr_dir, key, "attempt_failed", run_id="run-ledger", attempt=1,
          reason="You've hit your session limit",
          failure_kind="quota_exhausted", will_retry=False,
          will_fallback=True, fallback_runner="claude")
    _emit(brr_dir, key, "retrying", run_id="run-ledger", attempt=2,
          reason="fallback after quota_exhausted", from_runner="codex",
          runner="claude")
    _emit(brr_dir, key, "attempt_started", run_id="run-ledger", attempt=2)

    view = run_progress.project_run(brr_dir, key, "run-ledger")
    assert view is not None
    text = run_progress.render_text(view, compact=True)

    assert "attempts:" in text
    assert (
        "- attempt 1 (codex): quota exhausted - "
        "You've hit your session limit -> claude"
    ) in text


def test_render_text_compact_surfaces_relay_offer(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:relay-ledger:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-relay", event_id="evt-relay",
        env="host", status="running",
        branch_name="brr/run-relay",
    )
    _emit(brr_dir, key, "run_created", run_id="run-relay", env="host")
    _emit(brr_dir, key, "run_started", run_id="run-relay",
          runner="codex", branch="brr/run-relay")
    _emit(brr_dir, key, "attempt_started", run_id="run-relay", attempt=1)
    _emit(
        brr_dir,
        key,
        "attempt_failed",
        run_id="run-relay",
        attempt=1,
        reason="You've hit your session limit",
        failure_kind="quota_exhausted",
        will_retry=False,
        needs_relay_consent=True,
        relay_candidate="brnrd-codex-relay · gpt-5-codex-relay (relay, brnrd)",
    )

    view = run_progress.project_run(brr_dir, key, "run-relay")
    assert view is not None
    assert "relay available: brnrd-codex-relay" in view.detail
    text = run_progress.render_text(view, compact=True)
    assert (
        "relay available: brnrd-codex-relay · gpt-5-codex-relay "
        "(relay, brnrd)"
    ) in text


def test_project_run_conflict(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:4:"
    _emit(brr_dir, key, "run_created", run_id="run-4", env="worktree")
    _emit(brr_dir, key, "done", run_id="run-4")
    _emit(brr_dir, key, "conflict", run_id="run-4", branch="brr/run-4")

    view = run_progress.project_run(brr_dir, key, "run-4")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "conflict"
    assert view.status_label() == "conflict"
    assert "brr/run-4" in view.detail


def test_project_run_failure_detail_survives_finalizing(tmp_path):
    """The daemon emits ``finalizing(stage=failed)`` before ``failed`` so
    the operator's view ends on the real error rather than the generic
    "finalizing (failed)" placeholder. The projection should fold them
    in that order and end with the failed packet's detail."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:f:"
    _emit(brr_dir, key, "run_created", run_id="run-f", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-f", attempt=1)
    _emit(brr_dir, key, "attempt_failed", run_id="run-f", attempt=1,
          reason="timed out", will_retry=False, exit_code=124, timed_out=True)
    _emit(brr_dir, key, "finalizing", run_id="run-f", stage="failed")
    _emit(brr_dir, key, "failed", run_id="run-f", stage="run", attempts=1,
          exit_code=124, timed_out=True,
          error="runner timed out after 3600s")

    view = run_progress.project_run(brr_dir, key, "run-f")
    assert view is not None
    assert view.state == "failed"
    assert view.phase == "failed"
    assert "timed out" in view.detail
    assert "runner timed out after 3600s" in view.detail
    assert "finalizing" not in view.detail
    assert view.error == "runner timed out after 3600s"


def test_project_run_container_preserved(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:5:"
    _emit(brr_dir, key, "run_created", run_id="run-5",
          env="docker")
    _emit(brr_dir, key, "container_preserved", run_id="run-5",
          containers=["brr-run-5-attempt-1", "brr-run-5-attempt-2"])
    _emit(brr_dir, key, "failed", run_id="run-5", stage="run")

    view = run_progress.project_run(brr_dir, key, "run-5")
    assert view is not None
    assert view.state == "failed"
    assert view.container_ids == [
        "brr-run-5-attempt-1",
        "brr-run-5-attempt-2",
    ]


def test_project_conversation_latest_picks_most_recent_task(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:6:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-old", event_id="evt-old",
        env="host", status="done",
    )
    conversations.append_run(
        brr_dir, key,
        run_id="run-new", event_id="evt-new",
        env="docker", status="running",
    )
    _emit(brr_dir, key, "run_created", run_id="run-new",
          env="docker")
    _emit(brr_dir, key, "run_started", run_id="run-new")

    view = run_progress.project_conversation_latest(brr_dir, key)
    assert view is not None
    assert view.run_id == "run-new"
    assert view.state == "active"


def test_project_conversation_latest_returns_none_when_no_runs(tmp_path):
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
    _emit(brr_dir, key, "run_created", run_id="run-rn", env="docker")
    _emit(brr_dir, key, "run_started", run_id="run-rn",
          runner="codex", branch="brr/run-rn", env="docker")

    view = run_progress.project_run(brr_dir, key, "run-rn")
    assert view is not None
    assert view.runner_name == "codex"
    assert view.branch_name == "brr/run-rn"


def test_projection_treats_heartbeat_as_no_op(tmp_path):
    """Heartbeats stay out of memory and only re-trigger gate renders."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:hb:"
    _emit(brr_dir, key, "run_created", run_id="run-hb", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-hb", attempt=1)
    before = run_progress.project_run(brr_dir, key, "run-hb")
    assert before is not None
    assert len(before.phase_history) == 2

    _emit(brr_dir, key, "heartbeat", run_id="run-hb",
          attempt=1, elapsed_seconds=30)
    _emit(brr_dir, key, "heartbeat", run_id="run-hb",
          attempt=1, elapsed_seconds=60)

    records = conversations.read_records(brr_dir, key)
    assert [r.get("type") for r in records if r.get("kind") == "update"] == [
        "run_created",
        "attempt_started",
    ]

    after = run_progress.project_run(brr_dir, key, "run-hb")
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
    _emit(brr_dir, key, "run_created", run_id="run-r", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-r", attempt=1)
    _emit(brr_dir, key, "attempt_failed", run_id="run-r", attempt=1,
          reason="missing required output(s)", will_retry=True)
    _emit(brr_dir, key, "retrying", run_id="run-r", attempt=2)
    _emit(brr_dir, key, "attempt_started", run_id="run-r", attempt=2)

    view = run_progress.project_run(brr_dir, key, "run-r")
    assert view is not None
    running = [e for e in view.phase_history if e.name == "running"]
    assert [e.attempt for e in running] == [1, 2]
    # First running entry is closed (next attempt opened a fresh one),
    # second is the live one.
    assert running[0].ended_at is not None
    assert running[1].ended_at is None


def test_render_text_compact_has_runner_env_branch_header(tmp_path):
    """Compact card opens with a sticky ``run-id · runner · env ·
    branch ← base`` header. The run ID leads it (2026-07-08, direct ask):
    a user following a fast-moving thread across several runs needs a
    stable handle to point back at a specific one — the earlier "dev-side
    noise, leave it out" call didn't weigh that against a chat reader
    who has no other way to say "regarding run X"."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8:"
    _emit(brr_dir, key, "run_created", run_id="run-r", env="docker")
    _emit(brr_dir, key, "env_prepared", run_id="run-r", env="docker",
          branch_name="brr/run-r")
    _emit(brr_dir, key, "attempt_started", run_id="run-r", attempt=1)
    _emit(brr_dir, key, "run_started", run_id="run-r",
          runner="codex", branch="brr/run-r", env="docker")
    # Backfill the display base the same way daemon._run_worker does
    # via the run record (env_prepared doesn't carry seed_ref by name).
    conversations.append_run(
        brr_dir, key,
        run_id="run-r", event_id="evt-r",
        env="docker", status="running",
        seed_ref="main", target_branch="main",
        branch_name="brr/run-r",
    )

    view = run_progress.project_run(brr_dir, key, "run-r")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    header = text.splitlines()[0]
    assert header == "run-r · codex · docker · brr/run-r ← main"
    # Phase log: no "phase:" labels (those belong to the verbose form).
    assert "phase:" not in text
    # run-r now appears twice: once as the leading run-id, once inside
    # the branch name in the header.
    assert text.count("run-r") == 2
    assert "running" in text


def test_render_text_compact_prefixes_repo_when_known(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:repo-header:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-repo", event_id="evt-repo",
        env="host", status="running",
        branch_name="brr/run-repo",
        repo_label="Gurio/brr",
    )
    _emit(brr_dir, key, "run_created", run_id="run-repo",
          env="host", repo_label="Gurio/brr")
    _emit(brr_dir, key, "run_started", run_id="run-repo",
          runner="codex", branch="brr/run-repo", env="host")

    view = run_progress.project_run(brr_dir, key, "run-repo")
    assert view is not None
    text = run_progress.render_text(view, compact=True)

    assert (
        text.splitlines()[0]
        == "run-repo · Gurio/brr · codex · host · brr/run-repo"
    )


def test_push_done_carries_forge_view_url_into_view(tmp_path):
    """A ``push_done`` packet that includes a forge URL stores it on
    the projection so renderers can surface a clickable link."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:9c:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-fv", event_id="evt-fv",
        env="docker", status="running",
        seed_ref="main", target_branch=None,
        branch_name="brr/run-fv",
    )
    _emit(brr_dir, key, "run_created", run_id="run-fv", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-fv", attempt=1)
    _emit(brr_dir, key, "finalizing", run_id="run-fv", stage="done")
    _emit(
        brr_dir, key, "push_done", run_id="run-fv",
        branch="brr/run-fv", commits=2, ok=True,
        view_url="https://github.com/Gurio/brr/tree/brr/run-fv",
    )
    _emit(brr_dir, key, "done", run_id="run-fv", event_id="evt-fv")

    view = run_progress.project_run(brr_dir, key, "run-fv")
    assert view is not None
    assert view.view_url == "https://github.com/Gurio/brr/tree/brr/run-fv"


def test_render_text_compact_emits_view_url_under_delivered(tmp_path):
    """The forge link gets its own line below the delivered header so
    long URLs don't wrap the duration / push summary. Bare URLs
    auto-link on every gate we render to today, so no markdown
    wrapping is needed."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:9d:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-fl", event_id="evt-fl",
        env="docker", status="running",
        seed_ref="main", target_branch=None,
        branch_name="brr/run-fl",
    )
    _emit(brr_dir, key, "run_created", run_id="run-fl", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-fl", attempt=1)
    _emit(brr_dir, key, "finalizing", run_id="run-fl", stage="done")
    _emit(
        brr_dir, key, "push_done", run_id="run-fl",
        branch="brr/run-fl", commits=1, ok=True,
        view_url="https://github.com/Gurio/brr/tree/brr/run-fl",
    )
    _emit(brr_dir, key, "done", run_id="run-fl", event_id="evt-fl")

    view = run_progress.project_run(brr_dir, key, "run-fl")
    text = run_progress.render_text(view, compact=True)

    lines = text.splitlines()
    delivered_idx = next(
        i for i, line in enumerate(lines) if line.startswith("delivered")
    )
    assert lines[delivered_idx + 1] == (
        "view: https://github.com/Gurio/brr/tree/brr/run-fl"
    )


def test_render_text_compact_omits_view_line_without_url(tmp_path):
    """When push_done has no view_url, the renderer stays quiet — no
    trailing empty line, no placeholder."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:9e:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-fn", event_id="evt-fn",
        env="docker", status="running",
        seed_ref="main", target_branch=None,
        branch_name="brr/run-fn",
    )
    _emit(brr_dir, key, "run_created", run_id="run-fn", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-fn", attempt=1)
    _emit(brr_dir, key, "finalizing", run_id="run-fn", stage="done")
    _emit(
        brr_dir, key, "push_done", run_id="run-fn",
        branch="brr/run-fn", commits=1, ok=True,
    )
    _emit(brr_dir, key, "done", run_id="run-fn", event_id="evt-fn")

    view = run_progress.project_run(brr_dir, key, "run-fn")
    text = run_progress.render_text(view, compact=True)

    assert "view:" not in text


def test_terminal_card_hides_branch_when_run_committed_nothing(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:no-commit:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-no-commit", event_id="evt-no-commit",
        env="worktree", status="running",
        seed_ref="main", target_branch=None,
        branch_name="brr/run-no-commit",
    )
    _emit(brr_dir, key, "run_created", run_id="run-no-commit", env="worktree")
    _emit(
        brr_dir, key, "env_prepared", run_id="run-no-commit",
        env="worktree", branch_name="brr/run-no-commit",
    )
    _emit(brr_dir, key, "attempt_started", run_id="run-no-commit", attempt=1)
    _emit(brr_dir, key, "finalizing", run_id="run-no-commit", stage="done")
    _emit(
        brr_dir, key, "done", run_id="run-no-commit",
        event_id="evt-no-commit", committed=False,
    )

    view = run_progress.project_run(brr_dir, key, "run-no-commit")
    assert view is not None
    assert view.branch_name is None
    assert "brr/run-no-commit" not in run_progress.render_text(view, compact=True)


def test_terminal_card_keeps_branch_when_run_has_a_commit(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:committed:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-committed", event_id="evt-committed",
        env="worktree", status="running",
        seed_ref="main", target_branch=None,
        branch_name="brr/run-committed",
    )
    _emit(brr_dir, key, "run_created", run_id="run-committed", env="worktree")
    _emit(
        brr_dir, key, "env_prepared", run_id="run-committed",
        env="worktree", branch_name="brr/run-committed",
    )
    _emit(brr_dir, key, "attempt_started", run_id="run-committed", attempt=1)
    _emit(brr_dir, key, "finalizing", run_id="run-committed", stage="done")
    _emit(
        brr_dir, key, "done", run_id="run-committed",
        event_id="evt-committed", committed=True,
    )

    view = run_progress.project_run(brr_dir, key, "run-committed")
    assert view is not None
    assert view.branch_name == "brr/run-committed"
    assert "brr/run-committed" in run_progress.render_text(view, compact=True)


def test_render_text_compact_omits_arrow_without_target_branch(tmp_path):
    """When there is no explicit expected publish target, the header shows
    just the branch name. The seed_ref (where the branch was cut from) is a
    setup detail and should NOT be rendered as a landing target.

    Previously ``display_base`` fell back to ``seed_ref``, which made every
    run card claim it was landing on `main` even when the agent picked its
    own branch with no expected publish intent. That was misleading enough
    to surface a real merge surprise in chat.
    """
    brr_dir = tmp_path / ".brr"
    key = "telegram:8b:"
    conversations.append_run(
        brr_dir, key,
        run_id="run-na", event_id="evt-na",
        env="docker", status="running",
        seed_ref="main", target_branch=None,
        branch_name="brr/run-na",
    )
    _emit(brr_dir, key, "run_created", run_id="run-na", env="docker")
    _emit(brr_dir, key, "env_prepared", run_id="run-na", env="docker",
          branch_name="brr/run-na", seed_ref="main")
    _emit(brr_dir, key, "attempt_started", run_id="run-na", attempt=1)
    _emit(brr_dir, key, "run_started", run_id="run-na",
          runner="codex", branch="brr/run-na", env="docker",
          seed_ref="main")

    view = run_progress.project_run(brr_dir, key, "run-na")
    assert view is not None
    assert view.display_base is None
    text = run_progress.render_text(view, compact=True)
    header = text.splitlines()[0]
    assert header == "run-na · codex · docker · brr/run-na"
    assert "←" not in header


def test_render_text_compact_strikes_through_finished_phases(tmp_path):
    """Closed phases get wrapped in the style's done-open/done-close
    markers; the live current line stays unmarked. Plain text gets no
    decoration so the log reads positionally."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8a:"
    _emit(brr_dir, key, "run_created", run_id="run-s", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-s", attempt=1)
    _emit(brr_dir, key, "finalizing", run_id="run-s", stage="done")

    view = run_progress.project_run(brr_dir, key, "run-s")
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
    _emit(brr_dir, key, "run_created", run_id="run-e", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-e", attempt=1)

    view = run_progress.project_run(brr_dir, key, "run-e")
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
    _emit(brr_dir, key, "run_created", run_id="run-rt", env="worktree")
    _emit(brr_dir, key, "attempt_started", run_id="run-rt", attempt=1)
    view_one = run_progress.project_run(brr_dir, key, "run-rt")
    assert view_one is not None
    assert "attempt" not in run_progress.render_text(view_one, compact=True)

    _emit(brr_dir, key, "attempt_failed", run_id="run-rt", attempt=1,
          reason="missing required output(s)", will_retry=True)
    _emit(brr_dir, key, "retrying", run_id="run-rt", attempt=2)
    _emit(brr_dir, key, "attempt_started", run_id="run-rt", attempt=2)
    view_retry = run_progress.project_run(brr_dir, key, "run-rt")
    assert view_retry is not None
    text = run_progress.render_text(view_retry, compact=True)
    assert "running (attempt 1)" in text
    assert "running (attempt 2)" in text


def test_render_text_compact_terminal_reports_total_elapsed(tmp_path):
    """The terminal line names the total wall-clock time from event
    arrival to delivery — that's the meaningful "how long did it take"."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8e:"
    _emit(brr_dir, key, "run_created", run_id="run-d", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-d", attempt=1)
    _emit(brr_dir, key, "finalizing", run_id="run-d", stage="done")
    _emit(brr_dir, key, "push_done", run_id="run-d", commits=2, ok=True)
    _emit(brr_dir, key, "done", run_id="run-d", event_id="evt-d")

    view = run_progress.project_run(brr_dir, key, "run-d")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    last_line = text.rstrip().splitlines()[-1]
    assert last_line.startswith("delivered · ")
    assert "pushed 2 commits" in last_line


def test_render_text_compact_attending_is_nonterminal_delivery_phase(tmp_path):
    """Daemon-owned post-delivery dwell renders as an active
    ``delivered · attending`` phase; terminal ``delivered`` remains the
    final packet when the dwell ends."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8a:"
    _emit(brr_dir, key, "run_created", run_id="run-a", env="host")
    _emit(brr_dir, key, "attempt_started", run_id="run-a", attempt=1)
    _emit(brr_dir, key, "finalizing", run_id="run-a", stage="done")
    _emit(brr_dir, key, "attending", run_id="run-a", event_id="evt-a",
          reason="watching for follow-up after delivery")

    view = run_progress.project_run(brr_dir, key, "run-a")
    assert view is not None
    assert view.phase == "attending"
    assert view.state == "active"
    text = run_progress.render_text(view, compact=True)
    assert "delivered · attending" in text
    assert text.rstrip().splitlines()[-1].startswith("delivered · attending")

    _emit(brr_dir, key, "done", run_id="run-a", event_id="evt-a")
    done = run_progress.project_run(brr_dir, key, "run-a")
    assert done is not None
    assert done.phase == "delivered"
    assert done.state == "succeeded"


def test_render_text_compact_reports_push_failure(tmp_path):
    """A failed push is post-response housekeeping: the response can be
    delivered, but the progress card must not claim the commits were
    pushed."""
    brr_dir = tmp_path / ".brr"
    key = "github:17:"
    _emit(brr_dir, key, "run_created", run_id="run-pf", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-pf", attempt=1)
    _emit(brr_dir, key, "finalizing", run_id="run-pf", stage="done")
    _emit(
        brr_dir, key, "push_done",
        run_id="run-pf", commits=5, ok=False,
        error="Permission denied (publickey)",
    )
    _emit(brr_dir, key, "done", run_id="run-pf", event_id="evt-pf")

    view = run_progress.project_run(brr_dir, key, "run-pf")
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
    _emit(brr_dir, key, "run_created", run_id="run-fail", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-fail", attempt=1)
    _emit(brr_dir, key, "finalizing", run_id="run-fail", stage="failed")
    _emit(brr_dir, key, "failed", run_id="run-fail", event_id="evt-fail",
          stage="run", attempts=1, exit_code=124, timed_out=True,
          failure_kind="timed_out",
          error="runner timed out after 3600s")

    view = run_progress.project_run(brr_dir, key, "run-fail")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    lines = text.rstrip().splitlines()
    assert any(line.startswith("timed out · ") for line in lines)
    assert lines[-1] == "runner timed out after 3600s"


def test_render_text_verbose_keeps_dev_fields(tmp_path):
    """Verbose mode (compact=False) keeps the operator-facing detail."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:8z:"
    _emit(brr_dir, key, "run_created", run_id="run-v", env="docker")
    _emit(brr_dir, key, "env_prepared", run_id="run-v", env="docker",
          branch_name="brr/run-v")
    _emit(brr_dir, key, "run_started", run_id="run-v",
          runner="codex", branch="brr/run-v", env="docker")
    _emit(brr_dir, key, "done", run_id="run-v")

    view = run_progress.project_run(brr_dir, key, "run-v")
    assert view is not None
    text = run_progress.render_text(view, compact=False)
    assert "branch: brr/run-v" in text
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
    _emit(brr_dir, key, "run_created", run_id="run-c",
          env="docker")
    _emit(brr_dir, key, "failed", run_id="run-c", stage="env",
          error="docker env requires docker.image in .brr/config")

    view = run_progress.project_run(brr_dir, key, "run-c")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    assert "USER CONTEXTUAL OVERRIDE" not in text


def test_run_id_from_packet():
    packet = updates.UpdatePacket(
        type="run_created", conversation_key="k", payload={"run_id": "run-x"},
    )
    assert run_progress.run_id_from_packet(packet) == "run-x"

    empty = updates.UpdatePacket(type="event_received", conversation_key="k")
    assert run_progress.run_id_from_packet(empty) is None


# ── Agent-composed card narration (issue #114) ──────────────────────


def test_card_composed_packet_lands_on_view_and_renders_as_note(tmp_path):
    """``card_composed`` updates flow into ``RunProgressView.agent_card_text``
    and the compact card surfaces them as a ``note:`` tail line under the
    live phase. The agent's narration is additive — the daemon's
    lifecycle scaffolding (header, phase log) is unchanged."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:agent-card:"
    _emit(brr_dir, key, "run_created", run_id="run-ac", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-ac", attempt=1)
    _emit(brr_dir, key, "card_composed", run_id="run-ac",
          text="scanning packet types and wiring the new seam")

    view = run_progress.project_run(brr_dir, key, "run-ac")
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
    _emit(brr_dir, key, "run_created", run_id="run-ar", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-ar", attempt=1)
    _emit(brr_dir, key, "card_composed", run_id="run-ar", text="first pass")
    _emit(brr_dir, key, "card_composed", run_id="run-ar", text="second pass")

    view = run_progress.project_run(brr_dir, key, "run-ar")
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
    _emit(brr_dir, key, "run_created", run_id="run-cl", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-cl", attempt=1)
    _emit(brr_dir, key, "card_composed", run_id="run-cl", text="narration")
    _emit(brr_dir, key, "card_composed", run_id="run-cl", text="")

    view = run_progress.project_run(brr_dir, key, "run-cl")
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
    _emit(brr_dir, key, "run_created", run_id="run-lg", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-lg", attempt=1)
    long_text = "x" * (run_progress._AGENT_CARD_MAX_CHARS + 200)
    _emit(brr_dir, key, "card_composed", run_id="run-lg", text=long_text)

    view = run_progress.project_run(brr_dir, key, "run-lg")
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
    _emit(brr_dir, key, "run_created", run_id="run-tm", env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-tm", attempt=1)
    _emit(brr_dir, key, "card_composed", run_id="run-tm",
          text="wrote the agent card seam and tests")
    _emit(brr_dir, key, "finalizing", run_id="run-tm", stage="done")
    _emit(brr_dir, key, "done", run_id="run-tm", event_id="evt-tm")

    view = run_progress.project_run(brr_dir, key, "run-tm")
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
    _emit(brr_dir, key, "run_created", run_id="run-multi")
    _emit(brr_dir, key, "attempt_started", run_id="run-multi", attempt=1)
    _emit(brr_dir, key, "done", run_id="run-multi", event_id="evt-x",
          success_signal="current_reply",
          replies_current=1, replies_other=2, outbound_messages=1,
          committed=True)

    view = run_progress.project_run(brr_dir, key, "run-multi")
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
    _emit(brr_dir, key, "run_created", run_id="run-T",
          env="docker")
    _emit(brr_dir, key, "attempt_started", run_id="run-T", attempt=1)
    _emit(brr_dir, key, "done", run_id="run-T", event_id="evt-T",
          success_signal="current_reply",
          replies_current=1, replies_other=1, outbound_messages=1)

    view = run_progress.project_run(brr_dir, key, "run-T")
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
    _emit(brr_dir, key, "run_created", run_id="run-S")
    _emit(brr_dir, key, "attempt_started", run_id="run-S", attempt=1)
    _emit(brr_dir, key, "done", run_id="run-S", event_id="evt-S",
          success_signal="current_reply",
          replies_current=1, replies_other=0, outbound_messages=0)

    view = run_progress.project_run(brr_dir, key, "run-S")
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
    _emit(brr_dir, key, "run_created", run_id="run-C")
    _emit(brr_dir, key, "attempt_started", run_id="run-C", attempt=1)
    _emit(brr_dir, key, "done", run_id="run-C", event_id="evt-C",
          success_signal="commit",
          replies_current=0, replies_other=0, outbound_messages=0,
          committed=True)

    view = run_progress.project_run(brr_dir, key, "run-C")
    assert view is not None
    text = run_progress.render_text(view, compact=True)
    assert "committed; no reply" in text


def test_render_internal_event_success_stays_terse(tmp_path):
    """Internal-event success (e.g. schedule fire that didn't deliver to
    a user thread) doesn't decorate the terminal line — there is no
    'where' to surface. §8 want #1 (internal signal)."""
    brr_dir = tmp_path / ".brr"
    key = "schedule:reconcile:"
    _emit(brr_dir, key, "run_created", run_id="run-I")
    _emit(brr_dir, key, "attempt_started", run_id="run-I", attempt=1)
    _emit(brr_dir, key, "done", run_id="run-I", event_id="evt-I",
          success_signal="internal",
          replies_current=0, replies_other=0, outbound_messages=0)

    view = run_progress.project_run(brr_dir, key, "run-I")
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
    _emit(brr_dir, key, "run_created", run_id="run-To")
    _emit(brr_dir, key, "attempt_started", run_id="run-To", attempt=1)
    _emit(brr_dir, key, "failed", run_id="run-To", event_id="evt-To",
          stage="run", attempts=1, exit_code=124,
          timed_out=True, failure_kind="timed_out",
          error="runner timed out after 3600s")

    view = run_progress.project_run(brr_dir, key, "run-To")
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
    _emit(brr_dir, key, "run_created", run_id="run-R")
    _emit(brr_dir, key, "attempt_started", run_id="run-R", attempt=1)
    _emit(brr_dir, key, "failed", run_id="run-R", event_id="evt-R",
          stage="run", attempts=1, exit_code=1,
          failure_kind="runner_error",
          error="connection dropped")

    view = run_progress.project_run(brr_dir, key, "run-R")
    assert view is not None
    assert view.failure_kind == "runner_error"
    text = run_progress.render_text(view, compact=True)
    lines = text.rstrip().splitlines()
    assert any(line.startswith("runner failed · ") for line in lines)


def test_render_quota_failure_renames_the_failed_terminal_line(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:22q:"
    _emit(brr_dir, key, "run_created", run_id="run-Q")
    _emit(brr_dir, key, "attempt_started", run_id="run-Q", attempt=1)
    _emit(brr_dir, key, "failed", run_id="run-Q", event_id="evt-Q",
          stage="run", attempts=1, exit_code=1,
          failure_kind="quota_exhausted",
          error="You've hit your session limit")

    view = run_progress.project_run(brr_dir, key, "run-Q")
    assert view is not None
    assert view.failure_kind == "quota_exhausted"
    text = run_progress.render_text(view, compact=True)
    lines = text.rstrip().splitlines()
    assert any(line.startswith("quota exhausted · ") for line in lines)


def test_render_no_output_failure_renames_the_failed_terminal_line(tmp_path):
    """A clean exit with no signal renders as ``no reply · …`` — distinct
    from an operational failure so the user can tell apart 'the runner
    died' from 'the runner ran but produced nothing'. §8 want #2."""
    brr_dir = tmp_path / ".brr"
    key = "telegram:23:"
    _emit(brr_dir, key, "run_created", run_id="run-N")
    _emit(brr_dir, key, "attempt_started", run_id="run-N", attempt=1)
    _emit(brr_dir, key, "failed", run_id="run-N", event_id="evt-N",
          stage="run", attempts=1, failure_kind="no_output")

    view = run_progress.project_run(brr_dir, key, "run-N")
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
    _emit(brr_dir, key, "run_created", run_id="run-L")
    _emit(brr_dir, key, "attempt_started", run_id="run-L", attempt=1)
    # Legacy: no failure_kind, but timed_out=True
    _emit(brr_dir, key, "failed", run_id="run-L", event_id="evt-L",
          stage="run", attempts=1, exit_code=124, timed_out=True,
          error="…")
    view = run_progress.project_run(brr_dir, key, "run-L")
    assert view is not None
    assert view.failure_kind == "timed_out"


def test_failed_packet_is_in_card_packets():
    """Re-aligned ``failed`` rendering must trigger a card re-render —
    the operational-failure category change is exactly what the gate
    needs to redraw to surface."""
    assert "failed" in run_progress.CARD_PACKETS
    assert "done" in run_progress.CARD_PACKETS


# ── relics-so-far on the card (#342) ────────────────────────────────


def _write_produce_capsule(brr_dir: Path, event_id: str, counts) -> None:
    import json

    outbox = brr_dir / "outbox" / event_id
    outbox.mkdir(parents=True)
    (outbox / "portal-state.json").write_text(
        json.dumps({"produce": {"known": True, "counts": counts}}),
        encoding="utf-8",
    )


def _start_run(brr_dir: Path, key: str, run_id: str, event_id: str) -> None:
    _emit(brr_dir, key, "run_created", run_id=run_id, event_id=event_id,
          env="worktree")
    _emit(brr_dir, key, "attempt_started", run_id=run_id, attempt=1)


def test_projection_joins_relics_counts_from_portal_capsule(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:9:"
    _start_run(brr_dir, key, "run-9", "evt-9")
    _write_produce_capsule(brr_dir, "evt-9", {"commit": 2, "kb": 1})

    view = run_progress.project_run(brr_dir, key, "run-9")
    assert view is not None
    assert view.relics_counts == {"commit": 2, "kb": 1}

    latest = run_progress.project_conversation_latest(brr_dir, key)
    assert latest is not None
    assert latest.relics_counts == {"commit": 2, "kb": 1}


def test_projection_relics_counts_none_without_capsule(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:10:"
    _start_run(brr_dir, key, "run-10", "evt-10")

    view = run_progress.project_run(brr_dir, key, "run-10")
    assert view is not None
    assert view.relics_counts is None
    # And the rendered card carries no relics line at all.
    assert "relics:" not in run_progress.render_text(view, compact=True)


def test_compact_card_appends_relics_tail(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:11:"
    _start_run(brr_dir, key, "run-11", "evt-11")
    _write_produce_capsule(brr_dir, "evt-11", {"commit": 2, "kb": 1})

    view = run_progress.project_run(brr_dir, key, "run-11")
    text = run_progress.render_text(view, compact=True)
    # The issue's own example shape, as the card's tail line.
    assert text.rstrip().splitlines()[-1] == "relics: 2 commits · 1 page"


def test_compact_card_zero_relics_renders_no_line(tmp_path):
    brr_dir = tmp_path / ".brr"
    key = "telegram:12:"
    _start_run(brr_dir, key, "run-12", "evt-12")
    _write_produce_capsule(brr_dir, "evt-12", {})

    view = run_progress.project_run(brr_dir, key, "run-12")
    assert view is not None
    assert view.relics_counts == {}
    assert "relics:" not in run_progress.render_text(view, compact=True)
