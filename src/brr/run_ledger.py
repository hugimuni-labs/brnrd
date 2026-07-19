"""Append-only closed-run cost ledger.

The ledger is deliberately local-first: every closed daemon run appends one
JSON object to ``.brr/run-ledger.jsonl``.  Server mirroring and rollup queries
can project this later; the first invariant is that closeout never loses the
raw per-run row.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import claude_status
from . import claude_usage
from . import codex_status
from . import gitops
from . import relics
from . import runner_select
from .run import Run

LEDGER_NAME = "run-ledger.jsonl"
ESTIMATE_ACTUAL = "actual"
RUN_NAME_CONTROL_NAME = ".name"
_RUN_NAME_MAX_BYTES = 240
_RUN_NAME_MAX_CHARS = 60

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
    "core_expected",
    "core_mismatch",
    "substitution_reason",
    "repo_label",
    "source_system",
    "external_refs",
    "reply_archive",
    "name",
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

    # Prefer the model id(s) actually observed in this run's own result JSON
    # (`modelUsage.keys()`) over the static runner-catalog placeholder
    # ("default") an unpinned Claude profile resolves to at dispatch time —
    # the real id is only knowable once the run has produced output (#255).
    # Written back onto the task manifest too, so every later consumer of
    # ``runner_core`` (the run-state doc, a future wake's Mode block) sees
    # the same resolved value rather than diverging from the ledger row.
    #
    # Core attestation (follow-up to the shell=/core= shadowing bug fixed
    # 2026-07-09, runner.py::_warn_if_shell_shadows_core): before observed
    # overwrites expected, compare the two. `core_expected` is what the
    # config/catalog *claimed* at dispatch; `runner_core` becomes what the
    # Shell *actually ran*; `core_mismatch` is the alarm bit when the claim
    # and the observation disagree — the reliable "did this run respect the
    # pinned core" signal, so a shadowed/misrouted config can never again go
    # silent for days.
    expected_core = (
        _str_or_none(task.meta.get("core_requested"))
        or _str_or_none(task.meta.get("runner_core"))
    )
    resolved_core = (
        _str_or_none(task.meta.get("core_observed"))
        or claude_status.resolved_model_id(after_levels)
    )
    if resolved_core:
        task.meta["core_observed"] = resolved_core
        task.meta["runner_core"] = resolved_core
    mismatch = core_mismatch(expected_core, resolved_core)
    if mismatch:
        print(
            f"[brnrd:run-ledger] WARNING: run {task.id} was dispatched with "
            f"core={expected_core!r} but the Shell observed "
            f"{resolved_core!r} — the configured core pin was not respected.",
            file=sys.stderr,
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
    # Run relics (#200/#317, kb/design-run-relics.md): commits/branch/PR are
    # auto-derived from git + the ``.pr`` control file, captured kb pages and
    # the terminal reply are appended during knowledge closeout, and only
    # issue/comment/message/summary context depends on resident reporting.
    # Falls back to the pre-existing (always-empty in practice, since
    # nothing ever wrote it) ``task.meta["external_refs"]`` path so a task
    # that somehow pre-populated it directly doesn't regress.
    collected_relics = relics.collect(
        work_dir,
        branch=_str_or_none(task.meta.get("branch_name")),
        seed_ref=_str_or_none(task.meta.get("seed_ref")),
        outbox_dir=outbox_dir,
    )
    row = {
        "run_id": task.id,
        "event_id": task.event_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "wall_clock_seconds": wall_clock_seconds(started_at, ended_at),
        "runner_shell": runner_shell,
        "runner_core": _str_or_none(task.meta.get("runner_core")),
        "core_expected": expected_core,
        "core_mismatch": mismatch,
        # *Why* a substitution happened, when the envelope carried a signal
        # (fallback/refusal/iterations). ``None`` when clean or unobservable —
        # the reason rides next to the ``core_mismatch`` alarm bit (#substitution).
        "substitution_reason": claude_status.substitution_reason(after_levels),
        "repo_label": _str_or_none(task.meta.get("repo_label")),
        "source_system": _source_system(task),
        "external_refs": collected_relics or external_refs(task.meta.get("external_refs")),
        "reply_archive": _str_or_none(task.meta.get("reply_archive")),
        "name": read_run_name_control(outbox_dir) or "",
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


def core_mismatch(expected: str | None, observed: str | None) -> bool | None:
    """Did the Shell actually run the core the config pinned?

    Returns ``None`` (unverifiable) when there is nothing to compare:
    no observation (non-Claude Shells produce no ``modelUsage``, or the run
    died before result JSON), or an unpinned dispatch (``"default"`` means
    "whatever the Shell chooses" — anything observed is by definition
    respected). Returns ``False`` when at least one observed id matches the
    pin — subagents legitimately resolve to other tiers (Explore on haiku
    under a fable parent), so the joined ``a+b`` observation only alarms
    when *none* of its ids match. Matching is prefix-tolerant in both
    directions so a date-suffixed concrete id (``claude-haiku-4-5-20251001``)
    matches its shorter catalog pin and vice versa.
    """
    return runner_select.core_mismatch(expected, observed)


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


def read_run_name_control(outbox_dir: Path | None) -> str | None:
    """Read the resident-authored one-line run name, capped for dashboards."""
    if outbox_dir is None:
        return None
    try:
        raw = (outbox_dir / RUN_NAME_CONTROL_NAME).read_bytes()
    except OSError:
        return None
    lines = raw[:_RUN_NAME_MAX_BYTES].decode("utf-8", errors="replace").splitlines()
    value = lines[0].strip()[:_RUN_NAME_MAX_CHARS] if lines else ""
    return value or None


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
            "model_ids",
            "fallback_signals",
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
    """A run's quota draw, or ``None`` when the window reset underneath it.

    These columns are *used*-percentages, so the draw is ``after - before``.
    That subtraction is only meaningful while both readings belong to the same
    window: when a 5h or weekly window rolls over (or a reset credit is spent)
    mid-run, ``used`` drops back toward zero and the run books a large negative
    cost — it gets *credited* for spending. Live evidence at the time this
    guard was written: 5 of 181 weekly rows and 20 of 253 five-hour rows on
    this account were negative, reaching -77 and -83 respectively, and
    ``usd_subscription_attributed`` (derived from the weekly delta) inherited
    the sign.

    ``usage_samples.recent_burn`` already refuses to measure across a reset for
    exactly this reason; the ledger simply never learned the same lesson.
    A reset is unrecoverable here — the pre-reset portion of the run's spend
    went with the old window — so the honest row is a null, which every
    consumer already handles, rather than a negative that reads as real.
    """
    after_num = _num(after)
    before_num = _num(before)
    if after_num is None or before_num is None:
        return None
    if after_num < before_num:
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
