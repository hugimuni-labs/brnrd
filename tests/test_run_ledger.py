import json

from brr import run_ledger
from brr.run import Run


_ROW_FIELDS = {
    "run_id",
    "event_id",
    "started_at",
    "ended_at",
    "wall_clock_seconds",
    "runner_shell",
    "runner_core",
    "repo_label",
    "source_system",
    "external_refs",
    "task_classification",
    "parent_run_id",
    "is_subspawn",
    "tokens_input",
    "tokens_output",
    "tokens_cache_read",
    "tokens_cache_creation",
    "context_window_used",
    "weekly_pct_delta",
    "five_hour_pct_delta",
    "usd_subscription_attributed",
    "usd_credits_equivalent",
    "estimate_vs_actual",
}


def _levels(
    *,
    weekly: float | None = None,
    five_hour: float | None = None,
    tokens: dict | None = None,
):
    quota = {}
    if weekly is not None:
        quota["secondary_used_percent"] = weekly
    if five_hour is not None:
        quota["primary_used_percent"] = five_hour
    levels = {"quota": quota} if quota else {}
    if tokens is not None:
        levels["tokens"] = tokens
    return levels


def _task(run_id: str = "run-ledger") -> Run:
    return Run(
        id=run_id,
        event_id=f"evt-{run_id}",
        body="",
        source="telegram",
        meta={
            "runner_name": "codex",
            "runner_shell": "codex",
            "runner_core": "gpt-5-codex",
            "repo_label": "Gurio/brr",
        },
    )


def test_closed_run_appends_one_well_formed_jsonl_row(tmp_path, monkeypatch):
    (tmp_path / ".brr").mkdir()
    snapshots = iter([
        _levels(weekly=20.0, five_hour=40.0),
        _levels(
            weekly=23.0,
            five_hour=48.0,
            tokens={
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
                "context_window_used_percent": 12.5,
            },
        ),
    ])
    monkeypatch.setattr(run_ledger.codex_status, "load_levels", lambda: next(snapshots))

    task = _task()
    run_ledger.mark_run_started(task, "codex", None, None)
    task.meta["ended_at"] = "2026-07-06T10:00:05Z"
    task.meta["started_at"] = "2026-07-06T10:00:00Z"

    path = run_ledger.append_closed_run(
        tmp_path,
        task,
        {"run_ledger.subscription_price.codex": 20},
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert set(row) == _ROW_FIELDS
    assert row["run_id"] == "run-ledger"
    assert row["event_id"] == "evt-run-ledger"
    assert row["wall_clock_seconds"] == 5.0
    assert row["weekly_pct_delta"] == 3.0
    assert row["five_hour_pct_delta"] == 8.0
    assert row["usd_subscription_attributed"] == 0.6
    assert row["tokens_input"] == 100
    assert row["tokens_output"] == 50
    assert row["tokens_cache_read"] == 10
    assert row["tokens_cache_creation"] == 5
    assert row["context_window_used"] == 12.5
    assert row["external_refs"] == []
    assert row["estimate_vs_actual"] == "actual"


def test_claude_snapshot_absence_writes_nulls(tmp_path, monkeypatch):
    (tmp_path / ".brr").mkdir()
    monkeypatch.setattr(
        run_ledger.claude_usage,
        "load_or_refresh_snapshot",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(run_ledger.claude_status, "load_snapshot", lambda _outbox: None)

    task = Run(
        id="run-claude",
        event_id="evt-claude",
        body="",
        source="telegram",
        meta={
            "runner_name": "claude",
            "runner_shell": "claude",
            "runner_core": "claude-sonnet",
            "repo_label": "Gurio/brr",
            "started_at": "2026-07-06T10:00:00Z",
            "ended_at": "2026-07-06T10:00:10Z",
        },
    )

    run_ledger.mark_run_started(task, "claude", tmp_path / "outbox", tmp_path)
    path = run_ledger.append_closed_run(
        tmp_path,
        task,
        {"run_ledger.subscription_price.claude": 20},
        outbox_dir=tmp_path / "outbox",
    )

    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["weekly_pct_delta"] is None
    assert row["five_hour_pct_delta"] is None
    assert row["usd_subscription_attributed"] is None
    assert row["tokens_input"] is None


def test_closeout_forces_claude_usage_refresh_not_a_stale_cache(tmp_path, monkeypatch):
    """Design rule (kb/design-quota-scheduling-loom.md): Claude usage must be
    force-refreshed at closeout, never trusted from a stale TUI-scrape cache.
    ``mark_run_started`` (pre-run baseline) may read the cache as-is, but
    ``append_closed_run`` (post-run) must request ``max_age_seconds=0.0``."""
    (tmp_path / ".brr").mkdir()
    calls: list[float | None] = []

    def _fake_refresh(*args, **kwargs):
        calls.append(kwargs.get("max_age_seconds"))
        return None

    monkeypatch.setattr(
        run_ledger.claude_usage, "load_or_refresh_snapshot", _fake_refresh
    )
    monkeypatch.setattr(run_ledger.claude_status, "load_snapshot", lambda _outbox: None)

    task = Run(
        id="run-claude-refresh",
        event_id="evt-claude-refresh",
        body="",
        source="telegram",
        meta={
            "runner_name": "claude",
            "runner_shell": "claude",
            "runner_core": "claude-sonnet",
            "repo_label": "Gurio/brr",
        },
    )

    run_ledger.mark_run_started(task, "claude", tmp_path / "outbox", tmp_path)
    task.meta["ended_at"] = "2026-07-06T10:00:10Z"
    run_ledger.append_closed_run(
        tmp_path, task, {}, outbox_dir=tmp_path / "outbox", work_dir=tmp_path
    )

    assert len(calls) == 2
    assert calls[0] is None  # pre-run baseline: cache is fine
    assert calls[1] == 0.0  # closeout: must force a fresh scrape


def test_five_hour_delta_never_drives_subscription_usd():
    task = _task()
    task.meta["started_at"] = "2026-07-06T10:00:00Z"
    task.meta["ended_at"] = "2026-07-06T10:00:05Z"
    task.meta["run_ledger_five_hour_used_before"] = 10.0

    row = run_ledger.build_closed_run_row(
        task,
        {"run_ledger.subscription_price.codex": 20},
        after_levels=_levels(five_hour=15.0),
    )

    assert row["weekly_pct_delta"] is None
    assert row["five_hour_pct_delta"] == 5.0
    assert row["usd_subscription_attributed"] is None


def test_append_preserves_prior_rows(tmp_path, monkeypatch):
    (tmp_path / ".brr").mkdir()
    monkeypatch.setattr(
        run_ledger,
        "load_quota_levels",
        lambda *args, **kwargs: _levels(weekly=2.0, five_hour=3.0),
    )

    first = _task("run-one")
    first.meta["started_at"] = "2026-07-06T10:00:00Z"
    first.meta["ended_at"] = "2026-07-06T10:00:01Z"
    first.meta["run_ledger_weekly_used_before"] = 1.0
    second = _task("run-two")
    second.meta["started_at"] = "2026-07-06T10:00:02Z"
    second.meta["ended_at"] = "2026-07-06T10:00:03Z"
    second.meta["run_ledger_weekly_used_before"] = 1.0

    run_ledger.append_closed_run(tmp_path, first, {})
    path = run_ledger.append_closed_run(tmp_path, second, {})

    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["run_id"] for row in rows] == ["run-one", "run-two"]


def test_read_task_classification_control_reads_first_line(tmp_path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".task-classification").write_text(
        "dashboard-slice\nignored second line\n", encoding="utf-8"
    )

    assert (
        run_ledger.read_task_classification_control(outbox) == "dashboard-slice"
    )


def test_read_task_classification_control_missing_file_and_dir(tmp_path):
    assert run_ledger.read_task_classification_control(tmp_path / "no-outbox") is None
    assert run_ledger.read_task_classification_control(None) is None


def test_subspawn_row_carries_parent_run_id(tmp_path, monkeypatch):
    """A concurrent worker-stack child's row rolls up to its parent.

    kb/design-director-loop.md §"Concurrent sub-spawns": a sub-spawn's true
    cost is parent row + Σ(child rows), via this additive field — not a
    rewrite of the parent's own row.
    """
    (tmp_path / ".brr").mkdir()
    monkeypatch.setattr(run_ledger, "load_quota_levels", lambda *a, **kw: None)
    child = _task("run-child")
    child.meta["spawn_parent_run_id"] = "run-parent"
    child.meta["spawn_immediate"] = True

    row = run_ledger.build_closed_run_row(child, {})

    assert row["parent_run_id"] == "run-parent"
    assert row["is_subspawn"] is True


def test_non_subspawn_row_has_no_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(run_ledger, "load_quota_levels", lambda *a, **kw: None)
    row = run_ledger.build_closed_run_row(_task("run-solo"), {})

    assert row["parent_run_id"] is None
    assert row["is_subspawn"] is False
