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
    "core_expected",
    "core_mismatch",
    "substitution_reason",
    "repo_label",
    "source_system",
    "external_refs",
    "reply_archive",
    "name",
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
    assert row["reply_archive"] is None
    assert row["name"] == ""
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


def test_task_classification_normalizes_case_and_underscores(tmp_path):
    task = _task("run-classification")
    task.meta["task_classification"] = "Director_Tick"
    assert run_ledger.task_classification(task) == "director-tick"

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".task-classification").write_text("Director_Tick\n", encoding="utf-8")
    assert run_ledger.read_task_classification_control(outbox) == "director-tick"


def test_read_task_classification_control_missing_file_and_dir(tmp_path):
    assert run_ledger.read_task_classification_control(tmp_path / "no-outbox") is None
    assert run_ledger.read_task_classification_control(None) is None


def test_read_run_name_control_uses_first_line_and_caps_length(tmp_path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".name").write_text("x" * 70 + "\nignored\n", encoding="utf-8")
    assert run_ledger.read_run_name_control(outbox) == "x" * 60
    assert run_ledger.read_run_name_control(tmp_path / "missing") is None


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


def test_row_prefers_real_observed_model_id_over_catalog_placeholder():
    """Regression #255: an unpinned Claude run's ``runner_core`` must carry
    the actually-resolved model id (from the completed run's own
    ``modelUsage`` keys) instead of the static catalog placeholder
    ``"default"`` once that id has been observed."""
    task = Run(
        id="run-claude-resolved",
        event_id="evt-claude-resolved",
        body="",
        source="telegram",
        meta={
            "runner_name": "claude",
            "runner_shell": "claude",
            "runner_core": "default",
            "repo_label": "Gurio/brr",
        },
    )

    row = run_ledger.build_closed_run_row(
        task,
        {},
        after_levels={"model_ids": ["claude-opus-4-8"]},
    )

    assert row["runner_core"] == "claude-opus-4-8"
    # The manifest itself is updated too, so later consumers (run-state doc,
    # a future wake's Mode block) converge on the same resolved value.
    assert task.meta["runner_core"] == "claude-opus-4-8"


def test_row_falls_back_to_catalog_placeholder_when_model_usage_absent():
    """An aborted stream (or any run with no captured ``modelUsage``) keeps
    the pre-existing placeholder rather than silently clearing the field."""
    task = _task("run-no-model-usage")
    task.meta["runner_core"] = "default"

    row = run_ledger.build_closed_run_row(task, {}, after_levels=_levels())

    assert row["runner_core"] == "default"
    assert task.meta["runner_core"] == "default"


def test_core_attestation_alarms_when_pin_not_respected(capsys):
    """The shell=/core= shadowing failure mode (2026-07-09): config pinned
    fable-5 but the Shell actually ran another model. The row must carry
    the expected pin, the observed core, and a true ``core_mismatch`` —
    plus a loud stderr warning so it can never again go silent for days."""
    task = Run(
        id="run-shadowed",
        event_id="evt-shadowed",
        body="",
        source="telegram",
        meta={
            "runner_name": "claude-fable",
            "runner_shell": "claude",
            "runner_core": "claude-fable-5",
            "repo_label": "Gurio/brr",
        },
    )

    row = run_ledger.build_closed_run_row(
        task,
        {},
        after_levels={"model_ids": ["claude-sonnet-5"]},
    )

    assert row["core_expected"] == "claude-fable-5"
    assert row["runner_core"] == "claude-sonnet-5"
    assert row["core_mismatch"] is True
    assert "was dispatched with core='claude-fable-5'" in capsys.readouterr().err


def test_core_attestation_verifies_respected_pin(capsys):
    task = Run(
        id="run-attested",
        event_id="evt-attested",
        body="",
        source="telegram",
        meta={
            "runner_name": "claude-fable",
            "runner_shell": "claude",
            "runner_core": "claude-fable-5",
            "repo_label": "Gurio/brr",
        },
    )

    row = run_ledger.build_closed_run_row(
        task,
        {},
        after_levels={"model_ids": ["claude-fable-5"]},
    )

    assert row["core_expected"] == "claude-fable-5"
    assert row["core_mismatch"] is False
    assert capsys.readouterr().err == ""


def test_core_attestation_unpinned_or_unobserved_is_unverifiable():
    # Unpinned dispatch ("default"): anything observed is by definition
    # respected — no claim to verify.
    task = _task("run-unpinned")
    task.meta["runner_core"] = "default"
    row = run_ledger.build_closed_run_row(
        task, {}, after_levels={"model_ids": ["claude-opus-4-8"]}
    )
    assert row["core_mismatch"] is None

    # No observation (non-Claude Shell / aborted stream): unverifiable.
    task = _task("run-unobserved")
    row = run_ledger.build_closed_run_row(task, {}, after_levels=_levels())
    assert row["core_expected"] == "gpt-5-codex"
    assert row["core_mismatch"] is None


def test_core_mismatch_matching_rules():
    # Prefix-tolerant both directions (date-suffixed concrete ids).
    assert run_ledger.core_mismatch(
        "claude-haiku-4-5", "claude-haiku-4-5-20251001"
    ) is False
    assert run_ledger.core_mismatch(
        "claude-haiku-4-5-20251001", "claude-haiku-4-5"
    ) is False
    # Joined multi-id observation: fine when any id matches the pin
    # (subagents legitimately resolve to other tiers)...
    assert run_ledger.core_mismatch(
        "claude-fable-5", "claude-haiku-4-5+claude-fable-5"
    ) is False
    # ...alarms only when none do.
    assert run_ledger.core_mismatch(
        "claude-fable-5", "claude-haiku-4-5+claude-sonnet-5"
    ) is True


def test_core_mismatch_fable_billed_as_opus_alarms():
    """A fable pin observed as the opus billing id IS a mismatch.

    #394 taught core_mismatch to accept ``claude-opus-4-8`` as a billing
    alias for ``claude-fable-5``, on the theory that modelUsage reports the
    underlying billing id for a correctly-served fable run. Observed history
    disproved that: modelUsage used to report fable ids directly, and the
    switch to opus coincided with fable-pinned runs being served opus. The
    alias suppressed a true alarm. Reverted — an opus observation under a
    fable pin must fire until the substitution is explained upstream.
    """
    assert run_ledger.core_mismatch("claude-fable-5", "claude-opus-4-8") is True
    assert run_ledger.core_mismatch(
        "claude-fable-5", "claude-opus-4-8+claude-haiku-4-5"
    ) is True
    # A genuinely served fable run still verifies clean.
    assert run_ledger.core_mismatch("claude-fable-5", "claude-fable-5") is False


def test_merge_levels_preserves_fallback_signals():
    # Regression: ``fallback_signals`` must be on the merge allowlist, or the
    # substitution reason is dropped before the ledger ever reads it.
    merged = run_ledger._merge_levels(
        {"source": "usage"},
        {"source": "claude result JSON", "fallback_signals": {"stop_reason": "refusal"}},
    )
    assert merged["fallback_signals"] == {"stop_reason": "refusal"}


def test_row_surfaces_substitution_reason(tmp_path, monkeypatch):
    (tmp_path / ".brr").mkdir()
    after = {
        "quota": {"secondary_used_percent": 23.0, "primary_used_percent": 48.0},
        "model_ids": ["claude-opus-4-8"],
        "fallback_signals": {
            "fallback_blocks": [
                {"type": "fallback", "to": {"model": "claude-opus-4-8"}}
            ],
        },
    }
    before = {"quota": {"secondary_used_percent": 20.0, "primary_used_percent": 40.0}}
    calls = iter([before, after])
    monkeypatch.setattr(
        run_ledger, "load_quota_levels", lambda *a, **k: next(calls)
    )

    task = Run(
        id="run-sub",
        event_id="evt-run-sub",
        body="",
        source="telegram",
        meta={
            "runner_name": "claude",
            "runner_shell": "claude",
            "core_requested": "claude-fable-5",
            "repo_label": "Gurio/brr",
        },
    )
    run_ledger.mark_run_started(task, "claude", None, None)
    task.meta["started_at"] = "2026-07-16T10:00:00Z"
    task.meta["ended_at"] = "2026-07-16T10:00:05Z"

    path = run_ledger.append_closed_run(tmp_path, task, {})
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["substitution_reason"] == "fallback->claude-opus-4-8"
    assert row["core_mismatch"] is True
