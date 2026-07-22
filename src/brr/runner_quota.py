"""Best-effort runner quota snapshots for wake prompts.

The daemon runs on the host, where runner credentials and local helper
state live. The sandboxed agent usually cannot see those facts, so a
small quota summary belongs in the wake bundle when the daemon can prove
one cheaply. This module is deliberately conservative: it reads explicit
snapshots from config, environment, or `.brr/runner-quota.json`, and
returns no summary when none exists.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class RunnerQuotaBucket:
    """One quota bucket such as `weekly` or `5h`."""

    label: str
    remaining_percent: float | None = None
    remaining: str | None = None
    reset_at: str | None = None
    reset_after_seconds: int | None = None
    status: str | None = None


@dataclass(frozen=True)
class RunnerQuotaSnapshot:
    """Quota posture for one runner/profile."""

    runner: str
    buckets: tuple[RunnerQuotaBucket, ...]
    source: str | None = None
    updated_at: str | None = None


def describe_runner_quota(
    runner_name: str,
    cfg: Mapping[str, Any] | None,
    brr_dir: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return a compact quota summary for *runner_name*, or ``None``.

    Inputs, highest precedence first:

    - ``BRR_RUNNER_QUOTA_<RUNNER>`` / ``runner.quota.<runner>`` raw
      summaries or JSON snippets;
    - ``BRR_RUNNER_QUOTA`` / ``runner.quota`` raw summaries or JSON;
    - a JSON snapshot file (``runner.quota.file`` or
      ``.brr/runner-quota.json``).

    Raw strings are allowed because an operator or wrapper may already
    have read a provider page/header and just needs to pass a trusted
    one-line fact into the wake. JSON uses the structured snapshot shape
    parsed by :func:`format_snapshot`.
    """
    env = environ if environ is not None else os.environ
    cfg = cfg or {}
    keys = _runner_keys(runner_name)

    for key in keys:
        env_value = env.get(f"BRR_RUNNER_QUOTA_{_env_key(key)}")
        summary = _summary_from_inline(env_value, runner_name)
        if summary:
            return summary

    for key in keys:
        summary = _summary_from_inline(_cfg_get(cfg, f"runner.quota.{key}"), runner_name)
        if summary:
            return summary
        summary = _summary_from_inline(_cfg_get(cfg, f"runner_quota_{key}"), runner_name)
        if summary:
            return summary

    summary = _summary_from_inline(env.get("BRR_RUNNER_QUOTA"), runner_name)
    if summary:
        return summary
    summary = _summary_from_inline(_cfg_get(cfg, "runner.quota"), runner_name)
    if summary:
        return summary
    summary = _summary_from_inline(_cfg_get(cfg, "runner_quota"), runner_name)
    if summary:
        return summary

    snapshot_path = _snapshot_path(cfg, brr_dir)
    if not snapshot_path:
        return None
    snapshot = load_snapshot(snapshot_path, runner_name)
    if snapshot is None:
        return None
    return format_snapshot(snapshot)


def load_snapshot(path: Path, runner_name: str) -> RunnerQuotaSnapshot | None:
    """Load *path* and extract the entry for *runner_name*.

    Accepted file shapes:

    ``{"runners": {"codex": {"buckets": [...]}}}``
    ``{"codex": {"buckets": [...]}}``
    ``{"runner": "codex", "buckets": [...]}``
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _snapshot_from_data(data, runner_name)


def summary_from_levels(levels: Mapping[str, Any] | None) -> str | None:
    """Extract a quota one-liner from a Shell level snapshot, if present.

    This is the policy seam between cached collector output
    (``.claude-usage-levels.json``, Codex rollout levels, etc.) and the
    wake prompt's ``runner_quota`` line. When both exist, the levels
    summary wins — matching :func:`brr.facets.build`.
    """
    if not isinstance(levels, dict):
        return None
    quota = levels.get("quota")
    if isinstance(quota, dict):
        text = str(quota.get("summary") or "").strip()
        return _sanitize(text) if text else None
    if isinstance(quota, str):
        text = quota.strip()
        return _sanitize(text) if text else None
    return None


def latest_claude_usage_outbox_dir(brr_dir: Path) -> Path | None:
    """The most recently written Claude usage-levels snapshot's directory.

    ``claude_usage``/``claude_status`` cache their PTY/result scrapes into a
    *run's* outbox dir (``.brr/outbox/<event-id>/``), never into ``brr_dir``
    itself — a shared-level reader that has no "current run" of its own (the
    schedule-pacing read in ``daemon._fire_due_schedules``, the dashboard
    quota publish in ``brr.gates.cloud``) has to go find the freshest one.
    Same "freshest mtime" shape as :func:`codex_status._latest_rollout_fallback`
    — the compatibility fallback Codex now falls back to only when it has no
    ``thread_id`` to correlate on exactly (issue #195); Claude's outbox scan
    here has no comparable per-run id to correlate on, so it keeps the
    mtime guess unconditionally. Returns ``None`` when no run has ever cached
    one (cold daemon, or Codex-only).
    """
    from . import claude_usage

    try:
        candidates = (brr_dir / "outbox").glob(f"*/{claude_usage.SNAPSHOT_NAME}")
    except OSError:
        return None
    best_path: Path | None = None
    best_mtime = -1.0
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best_path = candidate.parent
    return best_path


def latest_claude_spend_outbox_dir(brr_dir: Path) -> Path | None:
    """The most recently written Claude result-JSON spend snapshot's directory.

    Same freshest-mtime heuristic as :func:`latest_claude_usage_outbox_dir`,
    over :mod:`brr.claude_status`'s per-run ``.claude-result-levels.json``
    instead of the interactive ``/usage`` scrape — the two are written by
    different collectors (a run's own headless result JSON vs. the PTY
    probe) and are not always the same outbox dir, so this is a distinct
    glob rather than reusing the quota helper's answer.
    """
    from . import claude_status

    try:
        candidates = (brr_dir / "outbox").glob(f"*/{claude_status.SNAPSHOT_NAME}")
    except OSError:
        return None
    best_path: Path | None = None
    best_mtime = -1.0
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best_path = candidate.parent
    return best_path


def binding_quota_remaining_pct(levels: Mapping[str, Any] | None) -> float | None:
    """The lowest remaining-percent found in a level snapshot's ``quota`` dict.

    Reads either shape a collector produces (`kb/design-director-loop.md`
    §B1's "binding bucket"): Claude's ``buckets.session`` /
    ``buckets.week`` / ``buckets.week_models.<label>`` (each carrying
    ``remaining_percentage``), or Codex's ``primary_remaining_percent`` /
    ``secondary_remaining_percent``. Returns the minimum of whatever numeric
    fields are present — the binding (most constrained) bucket — or
    ``None`` when nothing numeric is present. Never falls back to parsing
    the ``summary`` string; a policy decision needs a proven number, not a
    guess.
    """
    if not isinstance(levels, Mapping):
        return None
    quota = levels.get("quota")
    if not isinstance(quota, Mapping):
        return None

    found: list[float] = []

    def _add(value: Any) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found.append(float(value))

    buckets = quota.get("buckets")
    if isinstance(buckets, Mapping):
        for key, bucket in buckets.items():
            if key == "week_models" and isinstance(bucket, Mapping):
                for model_bucket in bucket.values():
                    if isinstance(model_bucket, Mapping):
                        _add(model_bucket.get("remaining_percentage"))
                continue
            if isinstance(bucket, Mapping):
                _add(bucket.get("remaining_percentage"))

    _add(quota.get("primary_remaining_percent"))
    _add(quota.get("secondary_remaining_percent"))

    return min(found) if found else None


def format_snapshot(snapshot: RunnerQuotaSnapshot) -> str | None:
    """Render a snapshot as a prompt-sized one-liner."""
    parts: list[str] = []
    for bucket in snapshot.buckets[:3]:
        text = _format_bucket(bucket)
        if text:
            parts.append(text)
    return _sanitize("; ".join(parts)) if parts else None


def _summary_from_inline(value: Any, runner_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (dict, list)):
        snapshot = _snapshot_from_data(value, runner_name)
        return format_snapshot(snapshot) if snapshot else None
    text = str(value).strip()
    if not text:
        return None
    if text[:1] in "[{":
        try:
            snapshot = _snapshot_from_data(json.loads(text), runner_name)
        except json.JSONDecodeError:
            snapshot = None
        if snapshot:
            return format_snapshot(snapshot)
    return _sanitize(text)


def _snapshot_from_data(data: Any, runner_name: str) -> RunnerQuotaSnapshot | None:
    if not isinstance(data, dict):
        return None
    entry = _runner_entry(data, runner_name)
    if entry is None:
        return None
    if isinstance(entry, str):
        bucket = RunnerQuotaBucket(label="quota", status=entry)
        return RunnerQuotaSnapshot(runner=runner_name, buckets=(bucket,))
    if not isinstance(entry, dict):
        return None
    buckets_raw = entry.get("buckets")
    if buckets_raw is None:
        buckets_raw = entry.get("quota")
    buckets: list[RunnerQuotaBucket] = []
    if isinstance(buckets_raw, list):
        for item in buckets_raw:
            bucket = _bucket_from_data(item)
            if bucket:
                buckets.append(bucket)
    elif isinstance(buckets_raw, dict):
        for label, item in buckets_raw.items():
            bucket = _bucket_from_data(item, fallback_label=str(label))
            if bucket:
                buckets.append(bucket)
    elif isinstance(buckets_raw, str):
        buckets.append(RunnerQuotaBucket(label="quota", status=buckets_raw))
    else:
        bucket = _bucket_from_data(entry)
        if bucket:
            buckets.append(bucket)
    if not buckets:
        return None
    return RunnerQuotaSnapshot(
        runner=str(entry.get("runner") or runner_name),
        buckets=tuple(buckets),
        source=_str_or_none(entry.get("source")),
        updated_at=_str_or_none(entry.get("updated_at")),
    )


def _runner_entry(data: dict[str, Any], runner_name: str) -> Any:
    keys = _runner_keys(runner_name)
    runners = data.get("runners")
    if isinstance(runners, dict):
        for key in keys:
            if key in runners:
                return runners[key]
    for key in keys:
        if key in data:
            return data[key]
    named = data.get("runner")
    if isinstance(named, str) and named in keys:
        return data
    # A single unnamed snapshot is acceptable for the selected runner.
    if "buckets" in data or "quota" in data:
        return data
    return None


def _bucket_from_data(
    data: Any,
    *,
    fallback_label: str | None = None,
) -> RunnerQuotaBucket | None:
    if isinstance(data, str):
        return RunnerQuotaBucket(label=fallback_label or "quota", status=data)
    if not isinstance(data, dict):
        return None
    label = _str_or_none(data.get("label") or data.get("name") or fallback_label)
    if not label:
        return None
    return RunnerQuotaBucket(
        label=label,
        remaining_percent=_float_or_none(
            data.get("remaining_percent", data.get("percent"))
        ),
        remaining=_str_or_none(data.get("remaining")),
        reset_at=_str_or_none(data.get("reset_at") or data.get("resets_at")),
        reset_after_seconds=_int_or_none(
            data.get("reset_after_seconds", data.get("resets_in_seconds"))
        ),
        status=_str_or_none(data.get("status")),
    )


def _format_bucket(bucket: RunnerQuotaBucket) -> str | None:
    label = _sanitize(bucket.label, limit=40)
    if not label:
        return None
    parts = [label]
    if bucket.remaining_percent is not None:
        parts.append(f"{_format_percent(bucket.remaining_percent)}%")
    elif bucket.remaining:
        parts.append(_sanitize(bucket.remaining, limit=60))
    elif bucket.status:
        parts.append(_sanitize(bucket.status, limit=80))
    else:
        return None
    reset = _format_reset(bucket)
    if reset:
        parts.append(f"- {reset}")
    return " ".join(p for p in parts if p)


def _format_reset(bucket: RunnerQuotaBucket) -> str | None:
    if bucket.reset_after_seconds is not None:
        return f"resets in {_duration(bucket.reset_after_seconds)}"
    if bucket.reset_at:
        return f"resets {_format_time(bucket.reset_at)}"
    return None


def _format_percent(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _duration(seconds: int) -> str:
    seconds = max(0, seconds)
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    chunks: list[str] = []
    if days:
        chunks.append(f"{days}d")
    if hours:
        chunks.append(f"{hours}h")
    if minutes or not chunks:
        chunks.append(f"{minutes}m")
    return "".join(chunks)


def _format_time(value: str) -> str:
    text = value.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _sanitize(text, limit=40)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def _snapshot_path(cfg: Mapping[str, Any], brr_dir: Path) -> Path | None:
    raw = _cfg_get(cfg, "runner.quota.file")
    if raw is None:
        raw = _cfg_get(cfg, "runner_quota_file")
    if raw:
        path = Path(str(raw)).expanduser()
        return path if path.is_absolute() else brr_dir / path
    return brr_dir / "runner-quota.json"


def _runner_keys(runner_name: str) -> tuple[str, ...]:
    slug = _slug(runner_name)
    provider = _provider_key(slug)
    return (slug,) if provider == slug else (slug, provider)


def _provider_key(slug: str) -> str:
    for prefix in ("codex", "claude", "gemini"):
        if slug == prefix or slug.startswith(prefix + "-") or slug.startswith(prefix + "_"):
            return prefix
    return slug


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _env_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


def _cfg_get(cfg: Mapping[str, Any], key: str) -> Any:
    if key in cfg:
        return cfg[key]
    alt = key.replace(".", "_")
    return cfg.get(alt)


def _sanitize(value: str, *, limit: int = 240) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
