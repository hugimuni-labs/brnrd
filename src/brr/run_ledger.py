"""Append-only closed-run cost ledger.

The ledger is deliberately local-first: every closed daemon run appends one
JSON object to ``.brr/run-ledger.jsonl``.  Server mirroring and rollup queries
can project this later; the first invariant is that closeout never loses the
raw per-run row.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import claude_status
from . import claude_usage
from . import codex_status
from . import gitops
from .run import Run

LEDGER_NAME = "run-ledger.jsonl"
ESTIMATE_ACTUAL = "actual"
TASK_CLASSIFICATION_CONTROL_NAME = ".task-classification"
_TASK_CLASSIFICATION_MAX_BYTES = 200

_BEFORE_WEEKLY_KEY = "run_ledger_weekly_used_before"
_BEFORE_FIVE_HOUR_KEY = "run_ledger_five_hour_used_before"
_BASELINE_RUNNER_KEY = "run_ledger_baseline_runner"

_ROW_FIELDS = (
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
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def mark_run_started(
    task: Run,
    runner_name: str | None,
    outbox_dir: Path | None,
    work_dir: Path | None,
) -> None:
    """Record start time and the pre-run quota snapshot on *task*."""
    task.meta.setdefault("started_at", now_iso())
    if task.meta.get(_BASELINE_RUNNER_KEY) == runner_name:
        return
    task.meta[_BASELINE_RUNNER_KEY] = runner_name or ""
    levels = load_quota_levels(
        runner_name,
        outbox_dir,
        work_dir,
        force_claude_refresh=False,
    )
    weekly, five_hour = quota_used_percentages(levels)
    if weekly is not None:
        task.meta[_BEFORE_WEEKLY_KEY] = weekly
    else:
        task.meta.pop(_BEFORE_WEEKLY_KEY, None)
    if five_hour is not None:
        task.meta[_BEFORE_FIVE_HOUR_KEY] = five_hour
    else:
        task.meta.pop(_BEFORE_FIVE_HOUR_KEY, None)


def append_closed_run(
    repo_root: Path,
    task: Run,
    cfg: Mapping[str, Any] | None = None,
    *,
    outbox_dir: Path | None = None,
    work_dir: Path | None = None,
) -> Path:
    """Append one closed-run row and return the ledger path.

    This function is intentionally best-effort about source data: unavailable
    quota or token sources become ``null`` fields, not closeout failures.
    """
    row = build_closed_run_row(
        task,
        cfg or {},
        outbox_dir=outbox_dir,
        work_dir=work_dir,
    )
    path = ledger_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def build_closed_run_row(
    task: Run,
    cfg: Mapping[str, Any] | None = None,
    *,
    outbox_dir: Path | None = None,
    work_dir: Path | None = None,
    after_levels: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or {}
    runner_name = _str_or_none(task.meta.get("runner_name"))
    runner_shell = _str_or_none(task.meta.get("runner_shell")) or _runner_shell(
        runner_name
    )
    ended_at = _str_or_none(task.meta.get("ended_at")) or now_iso()
    task.meta["ended_at"] = ended_at

    if after_levels is None:
        after_levels = load_quota_levels(
            runner_name,
            outbox_dir,
            work_dir,
            force_claude_refresh=True,
        )

    after_weekly, after_five_hour = quota_used_percentages(after_levels)
    before_weekly = _num(task.meta.get(_BEFORE_WEEKLY_KEY))
    before_five_hour = _num(task.meta.get(_BEFORE_FIVE_HOUR_KEY))
    weekly_delta = _delta(after_weekly, before_weekly)
    five_hour_delta = _delta(after_five_hour, before_five_hour)

    subscription_price = subscription_price_for_shell(
        cfg,
        runner_shell=runner_shell,
        runner_name=runner_name,
    )
    usd_subscription = (
        round((subscription_price / 100.0) * weekly_delta, 6)
        if subscription_price is not None and weekly_delta is not None
        else None
    )

    tokens = token_fields(after_levels)
    started_at = _str_or_none(task.meta.get("started_at"))
    row = {
        "run_id": task.id,
        "event_id": task.event_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "wall_clock_seconds": wall_clock_seconds(started_at, ended_at),
        "runner_shell": runner_shell,
        "runner_core": _str_or_none(task.meta.get("runner_core")),
        "repo_label": _str_or_none(task.meta.get("repo_label")),
        "source_system": _source_system(task),
        "external_refs": external_refs(task.meta.get("external_refs")),
        "task_classification": task_classification(task),
        "parent_run_id": _str_or_none(task.meta.get("spawn_parent_run_id")),
        "is_subspawn": bool(task.meta.get("spawn_immediate")),
        "tokens_input": tokens["tokens_input"],
        "tokens_output": tokens["tokens_output"],
        "tokens_cache_read": tokens["tokens_cache_read"],
        "tokens_cache_creation": tokens["tokens_cache_creation"],
        "context_window_used": tokens["context_window_used"],
        "weekly_pct_delta": weekly_delta,
        "five_hour_pct_delta": five_hour_delta,
        "usd_subscription_attributed": usd_subscription,
        "usd_credits_equivalent": usd_credits_equivalent(after_levels),
        "estimate_vs_actual": ESTIMATE_ACTUAL,
    }
    return {field: row.get(field) for field in _ROW_FIELDS}


def ledger_path(repo_root: Path) -> Path:
    return gitops.shared_brr_dir(repo_root) / LEDGER_NAME


def load_quota_levels(
    runner_name: str | None,
    outbox_dir: Path | None,
    work_dir: Path | None,
    *,
    force_claude_refresh: bool,
) -> dict[str, Any] | None:
    """Read the current Shell quota levels without raising."""
    try:
        if codex_status.supported(runner_name):
            return codex_status.load_levels()
        if claude_status.supported(runner_name):
            usage = claude_usage.load_or_refresh_snapshot(
                outbox_dir,
                cwd=work_dir,
                max_age_seconds=0.0 if force_claude_refresh else None,
            )
            result = claude_status.load_snapshot(outbox_dir)
            return _merge_levels(usage, result)
    except Exception:
        return None
    return None


def quota_used_percentages(
    levels: Mapping[str, Any] | None,
) -> tuple[float | None, float | None]:
    """Return ``(weekly_used, five_hour_used)`` from a levels snapshot."""
    if not isinstance(levels, Mapping):
        return None, None
    weekly = _num(levels.get("week_used_percentage"))
    five_hour = _num(levels.get("session_used_percentage"))
    quota = levels.get("quota")
    if isinstance(quota, Mapping):
        weekly = weekly if weekly is not None else _num(quota.get("secondary_used_percent"))
        five_hour = five_hour if five_hour is not None else _num(
            quota.get("primary_used_percent")
        )
    return weekly, five_hour


def token_fields(levels: Mapping[str, Any] | None) -> dict[str, int | float | None]:
    tokens = levels.get("tokens") if isinstance(levels, Mapping) else None
    if not isinstance(tokens, Mapping):
        tokens = {}
    return {
        "tokens_input": _int_or_none(tokens.get("input_tokens")),
        "tokens_output": _int_or_none(tokens.get("output_tokens")),
        "tokens_cache_read": _int_or_none(tokens.get("cache_read_input_tokens")),
        "tokens_cache_creation": _int_or_none(
            tokens.get("cache_creation_input_tokens")
        ),
        "context_window_used": _num(tokens.get("context_window_used_percent")),
    }


def subscription_price_for_shell(
    cfg: Mapping[str, Any],
    *,
    runner_shell: str | None,
    runner_name: str | None,
) -> float | None:
    """Configured monthly subscription price for a runner Shell/profile.

    Flat config keys are accepted in decreasing specificity:
    ``run_ledger.subscription_price.<runner_name>``,
    ``run_ledger.subscription_price.<runner_shell>``,
    ``runner.subscription_price.<runner_name>``,
    ``runner.subscription_price.<runner_shell>``,
    then the generic ``run_ledger.subscription_price``.
    """
    keys: list[str] = []
    for key in (runner_name, runner_shell):
        if key:
            keys.extend([
                f"run_ledger.subscription_price.{key}",
                f"runner.subscription_price.{key}",
            ])
    keys.append("run_ledger.subscription_price")
    for key in keys:
        value = _num(cfg.get(key))
        if value is not None:
            return value
    return None


def wall_clock_seconds(started_at: str | None, ended_at: str | None) -> float | None:
    start = _parse_iso(started_at)
    end = _parse_iso(ended_at)
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start).total_seconds()), 3)


def external_refs(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def task_classification(task: Run) -> str | None:
    for key in (
        "task_classification",
        "classification",
        "task.classification",
        "task_class",
    ):
        value = _str_or_none(task.meta.get(key))
        if value:
            return value
    return None


def read_task_classification_control(outbox_dir: Path | None) -> str | None:
    """Read the resident-authored ``.task-classification`` control file.

    A run tags its own cost-ledger shape by writing a one-line slug to this
    dotfile anytime before closeout (``kb/design-quota-scheduling-loom.md``
    §"Tracking-table schema" calls this "the only field that makes
    rollup-by-shape possible"). Best-effort: an unreadable or missing file
    is silently ``None``, never a closeout failure.
    """
    if outbox_dir is None:
        return None
    path = outbox_dir / TASK_CLASSIFICATION_CONTROL_NAME
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    text = raw[:_TASK_CLASSIFICATION_MAX_BYTES].decode("utf-8", errors="replace")
    return _str_or_none(text.splitlines()[0]) if text.splitlines() else None


def usd_credits_equivalent(levels: Mapping[str, Any] | None) -> float | None:
    """Return a proven credit-equivalent USD value, or null.

    The first local ledger pass has no stable token-to-managed-credit mapping.
    Keep the field present but empty until the managed-compute pricing source is
    wired, instead of smuggling five-hour quota or subscription dollars into it.
    """
    return None


def _merge_levels(
    *snapshots: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    sources: list[str] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            continue
        source = snapshot.get("source")
        if isinstance(source, str) and source.strip():
            sources.append(source.strip())
        for key in (
            "quota",
            "spend",
            "context_window",
            "plan_type",
            "tokens",
            "session_used_percentage",
            "week_used_percentage",
        ):
            if key in snapshot:
                merged[key] = snapshot[key]
    if sources:
        merged["source"] = " + ".join(dict.fromkeys(sources))
    return merged or None


def _source_system(task: Run) -> str | None:
    return _str_or_none(task.meta.get("source_system")) or _str_or_none(task.source)


def _runner_shell(runner_name: str | None) -> str | None:
    if not runner_name:
        return None
    if runner_name.startswith("claude"):
        return "claude"
    if runner_name.startswith("codex"):
        return "codex"
    if runner_name.startswith("gemini"):
        return "gemini"
    return runner_name


def _delta(after: Any, before: Any) -> float | None:
    after_num = _num(after)
    before_num = _num(before)
    if after_num is None or before_num is None:
        return None
    return round(after_num - before_num, 6)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _str_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None
