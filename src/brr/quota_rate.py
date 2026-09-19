"""The exchange rate between weighted tokens and quota percent — measured, never guessed.

**What was missing, precisely.** An allowance is declared in cost-weighted
tokens (:mod:`brr.allowance`); a quota window is reported in percent
(:mod:`brr.runner_quota`). Both ends were already recorded. Nothing related
them, so ``allowance: 20m`` and ``week 19% left`` were two numbers in two
units, and no caller could tell whether the first fits inside the second.
:func:`brr.allowance` says so in its own docstring: *"the per-provider quota
exchange rate (percent per weighted token) is still slice 3's learned rate."*
This module is that rate.

**It is a projection, not a collector.** Every operand already exists on the
closed-run ledger (``.brr/run-ledger.jsonl``, :mod:`brr.run_ledger`): each row
carries ``runner_shell``, ``runner_core``, the four token classes, and
``weekly_pct_delta`` / ``five_hour_pct_delta`` — the account-window movement
measured across that run's own life. Weighted tokens on one side, percent on
the other, on the same row, since 2026-08-17. No new meter is added here and
none is needed; what was missing was a reader.

**Why the row-level rates are not averaged.** ``*_pct_delta`` is quantized to
whole percent on Claude (its ``/usage`` screen reports integers), so a run that
truly drew 0.4 % records ``0`` and a run that truly drew 0.6 % records ``1`` —
a 1.7x overstatement. Averaging per-row ratios therefore inherits a strong
upward bias from small runs. The pooled estimator used here (``sum(delta) /
sum(weighted)``) is dominated by the large runs, where quantization is noise
rather than signal. Measured on the live ledger 2026-09-19: median-of-ratios
read 0.543 %/Mtok for claude/week against a pooled 0.546 — but for codex/5h,
70.2 against 129.1, a 1.8x disagreement. The pooled number is the one reported.

**The known bias, stated rather than corrected.** A window delta is
*account-wide*: it includes every sibling run that overlapped. A row from a run
that had three strands beside it attributes all four runs' draw to one run's
tokens, so the pooled rate over-reads whenever the fleet is busy. Measured on
the same ledger by restricting to runs with no same-Shell overlap at all:
claude/week 0.546 -> 0.405, codex/week 39.3 -> 35.1. The over-read is real and
it is in the **safe** direction for this module's one consumer — a pool priced
too small warns too early, which is the failure a warning is allowed to have.
The solitary figure rides along as :data:`SOLITARY_KEY` for a caller that wants
the unbiased one; the headline stays pooled-over-everything.

**Absence is a value here.** Every function returns ``None``, or a reading
whose ``status`` is ``"unmeasured"`` with a ``reason``, rather than a plausible
number. A rate resting on three samples across two hours is not a rate, and a
pool priced from one would be a confident lie in the one place confidence is
expensive. :data:`MIN_SAMPLES` and :data:`MIN_SPAN_HOURS` are where that line
is drawn, and both travel on the reading so a renderer can show the evidence
beside the estimate.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .allowance import TOKEN_WEIGHTS

#: Only rows ended inside this trailing horizon are evidence. The rate is not
#: a constant — it moves with model, cache hit rate and plan — so old rows are
#: not merely weak evidence, they are evidence about a different regime.
#: Measured on the live ledger 2026-09-19, claude/week reads 0.679 %/Mtok at
#: 14 d, 0.633 at 21 d, 0.589 at 30 d (stable), against 0.811 at 7 d and 0.656
#: at 3 d (n=5 and n=3 — noise). 14 d is where sample count stops being the
#: binding constraint on this account's traffic.
DEFAULT_HORIZON_HOURS = 14 * 24.0

#: Below this many joinable rows the reading is ``unmeasured``. Five is not a
#: statistical threshold; it is the count at which this account's own 3-day and
#: 7-day windows stopped disagreeing with the 30-day one by more than the
#: estimator's own quantization error.
MIN_SAMPLES = 5

#: And below this span, even enough rows prove nothing: five runs inside one
#: hour share one cache state and one window position.
MIN_SPAN_HOURS = 24.0

#: Ledger tail read, in bytes. The ledger is append-only and rows are ~1.7 KB;
#: 2 MB covers well over the horizon on the busiest account measured, and caps
#: the read so a year-old ledger never turns a heartbeat into a file scan.
TAIL_BYTES = 2_000_000

#: Window identities, by duration in minutes — the same key
#: :func:`brr.runner_quota.binding_quota_window` reports, so a caller never has
#: to translate a name. Maps to the ledger column that measures that window.
WINDOW_DELTA_FIELDS: dict[float, str] = {
    10080.0: "weekly_pct_delta",
    300.0: "five_hour_pct_delta",
}

#: Diagnostic key carrying the same rate computed over non-overlapping runs
#: only (see the module docstring's bias note). ``None`` when too few solitary
#: rows exist to compute one.
SOLITARY_KEY = "solitary_pct_per_mtok"

_TOKEN_COLUMNS = {
    "tokens_input": TOKEN_WEIGHTS["input"],
    "tokens_output": TOKEN_WEIGHTS["output"],
    "tokens_cache_read": TOKEN_WEIGHTS["cache_read"],
    "tokens_cache_creation": TOKEN_WEIGHTS["cache_creation"],
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(value: Any) -> float | None:
    """An ISO-8601 ``...Z`` stamp as epoch seconds; ``None`` on anything else."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def shell_family(name: Any) -> str | None:
    """A Shell name, runner name or catalog slug -> the Shell its quota belongs to.

    ``claude-opus`` and ``claude`` are the same account window; so are
    ``codex-gpt-5.6-sol`` and ``codex``. Anything else is ``None`` rather than
    a guessed family — a third Shell must show up in the catalog before its
    quota can be priced, and mis-keying it onto an existing window would
    corrupt both.
    """
    slug = str(name or "").strip().lower()
    if slug.startswith("claude"):
        return "claude"
    if slug.startswith("codex"):
        return "codex"
    return None


def weighted_tokens_of(row: Mapping[str, Any]) -> float | None:
    """The cost-weighted token total a ledger row proves, or ``None``.

    ``None`` — not zero — when the row carries no token column at all: a run
    whose Shell never returned a usage envelope is *unmeasured*, and folding it
    in as a zero-token run would drag every rate it touches toward infinity.
    A row with some columns present and others absent counts the ones present,
    which under-reads rather than invents.
    """
    if all(row.get(column) is None for column in _TOKEN_COLUMNS):
        return None
    total = 0.0
    for column, weight in _TOKEN_COLUMNS.items():
        value = _num(row.get(column))
        if value is not None:
            total += value * weight
    return total


def read_rows(ledger: Path | str | None, *, tail_bytes: int = TAIL_BYTES) -> list[dict]:
    """Parseable ledger rows from the tail of *ledger*, oldest first. Never raises.

    The first line of a mid-file seek is almost always a fragment; it is
    dropped rather than repaired. Losing one row out of a two-megabyte tail
    costs an estimator nothing, and a repaired fragment would be a fabricated
    sample.
    """
    if ledger is None:
        return []
    path = Path(ledger)
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > tail_bytes:
                handle.seek(size - tail_bytes)
                handle.readline()
            raw = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    rows: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _samples(
    rows: Iterable[Mapping[str, Any]],
    shell: str,
    delta_field: str,
    cutoff: float,
) -> list[dict[str, Any]]:
    """Rows that carry both operands for *shell* and *delta_field*, inside *cutoff*.

    A zero delta is dropped, not counted: on a provider reporting whole
    percent, ``0`` means "below the reporting resolution", which is a *bound*
    on the draw and not a measurement of it. Keeping zeros would understate the
    rate by exactly the amount the provider declined to report.
    """
    kept: list[dict[str, Any]] = []
    for row in rows:
        if shell_family(row.get("runner_shell")) != shell:
            continue
        ended = _epoch(row.get("ended_at"))
        if ended is None or ended < cutoff:
            continue
        weighted = weighted_tokens_of(row)
        delta = _num(row.get(delta_field))
        if not weighted or weighted <= 0 or delta is None or delta <= 0:
            continue
        started = _epoch(row.get("started_at"))
        kept.append({
            "run_id": row.get("run_id"),
            "core": row.get("runner_core"),
            "weighted": weighted,
            "delta": delta,
            "started": started,
            "ended": ended,
        })
    kept.sort(key=lambda s: s["ended"])
    return kept


def _solitary_rate(samples: list[dict[str, Any]], rows: Iterable[Mapping[str, Any]],
                   shell: str) -> float | None:
    """Pooled rate over samples whose run overlapped no other run on *shell*.

    The overlap test runs against every ledger row in the tail, not only the
    ones that became samples — a sibling that produced no usable row still ate
    the window while it ran, and excluding it from the test would call a busy
    run solitary.
    """
    spans = []
    for row in rows:
        if shell_family(row.get("runner_shell")) != shell:
            continue
        start, end = _epoch(row.get("started_at")), _epoch(row.get("ended_at"))
        if start is None or end is None:
            continue
        spans.append((start, end, row.get("run_id")))
    solitary = []
    for sample in samples:
        start, end = sample["started"], sample["ended"]
        if start is None:
            continue
        if any(
            other_id != sample["run_id"] and other_start < end and other_end > start
            for other_start, other_end, other_id in spans
        ):
            continue
        solitary.append(sample)
    if len(solitary) < MIN_SAMPLES:
        return None
    total_weighted = sum(s["weighted"] for s in solitary)
    if total_weighted <= 0:
        return None
    return sum(s["delta"] for s in solitary) / total_weighted * 1_000_000.0


def rate(
    ledger: Path | str | None,
    shell: str | None,
    window_minutes: float | None,
    *,
    now: float | None = None,
    horizon_hours: float = DEFAULT_HORIZON_HOURS,
    rows: list[dict] | None = None,
) -> dict[str, Any]:
    """Percent of *window_minutes* per million weighted tokens, on *shell*.

    Always returns a reading. ``status`` is ``"measured"`` with a
    ``pct_per_mtok``, or ``"unmeasured"`` with a ``reason`` naming which
    threshold failed and how far short the evidence fell. There is no third
    state and no default number — a caller that wants to render something must
    render the absence.
    """
    family = shell_family(shell)
    field = WINDOW_DELTA_FIELDS.get(float(window_minutes)) if window_minutes else None
    base = {
        "status": "unmeasured",
        "shell": family,
        "window_minutes": window_minutes,
        "horizon_hours": horizon_hours,
        "samples": 0,
        "min_samples": MIN_SAMPLES,
        "min_span_hours": MIN_SPAN_HOURS,
        "source": "brr run ledger",
    }
    if family is None:
        return {**base, "reason": f"no quota window is keyed to shell {shell!r}"}
    if field is None:
        return {
            **base,
            "reason": (
                f"no ledger column measures a {window_minutes}-minute window "
                f"(known: {sorted(WINDOW_DELTA_FIELDS)})"
            ),
        }

    wall = time.time() if now is None else float(now)
    ledger_rows = read_rows(ledger) if rows is None else list(rows)
    samples = _samples(ledger_rows, family, field, wall - horizon_hours * 3600.0)
    base["samples"] = len(samples)
    if len(samples) < MIN_SAMPLES:
        return {
            **base,
            "reason": (
                f"{len(samples)} joinable run(s) in the last "
                f"{horizon_hours / 24:.0f}d; {MIN_SAMPLES} needed"
            ),
        }
    span_hours = (samples[-1]["ended"] - samples[0]["ended"]) / 3600.0
    base["span_hours"] = round(span_hours, 1)
    if span_hours < MIN_SPAN_HOURS:
        return {
            **base,
            "reason": (
                f"{len(samples)} run(s) but only {span_hours:.1f}h apart; "
                f"{MIN_SPAN_HOURS:.0f}h needed"
            ),
        }
    total_weighted = sum(s["weighted"] for s in samples)
    if total_weighted <= 0:
        return {**base, "reason": "every joinable run reports zero weighted tokens"}
    pooled = sum(s["delta"] for s in samples) / total_weighted * 1_000_000.0
    per_row = sorted(s["delta"] / s["weighted"] * 1_000_000.0 for s in samples)
    return {
        **base,
        "status": "measured",
        "pct_per_mtok": round(pooled, 4),
        "method": "pooled: sum(window delta %) / sum(weighted tokens)",
        "row_rate_p10": round(per_row[len(per_row) // 10], 4),
        "row_rate_median": round(per_row[len(per_row) // 2], 4),
        "row_rate_p90": round(per_row[-1 - len(per_row) // 10], 4),
        SOLITARY_KEY: (
            round(solitary, 4)
            if (solitary := _solitary_rate(samples, ledger_rows, family)) is not None
            else None
        ),
        "weighted_tokens_observed": int(total_weighted),
    }


def tokens_for_percent(reading: Mapping[str, Any] | None, percent: float | None) -> int | None:
    """*percent* of the priced window, in weighted tokens. ``None`` if unmeasured."""
    if not isinstance(reading, Mapping) or reading.get("status") != "measured":
        return None
    pct_per_mtok = _num(reading.get("pct_per_mtok"))
    value = _num(percent)
    if not pct_per_mtok or pct_per_mtok <= 0 or value is None or value < 0:
        return None
    return int(value / pct_per_mtok * 1_000_000.0)


def commitments(live_runs: Iterable[Mapping[str, Any]], shell: str | None) -> dict[str, Any]:
    """What runs already in flight on *shell* still have the right to draw.

    *live_runs* are control records: ``{"shell", "allowance_tokens",
    "allowance_spent", "run_id", "title"}``. A run's commitment is the
    **undrawn remainder of its declared allowance** — what it may still spend
    without asking, which is the only bound the daemon actually holds over it.
    A run whose spend has not been read yet counts its whole allowance
    (``spent`` unknown is not ``spent`` zero, but for a *commitment* the
    conservative reading is the correct one: the run has drawn at most all of
    it and at least none of it, and a pool must plan for the ceiling).

    Runs with no declared allowance are counted in ``unbounded`` and contribute
    nothing to the total — the resident seat is the usual member of that set,
    and its standing ceiling is a default, not a commitment. This is the
    deliberate under-count named in the report's §5.
    """
    family = shell_family(shell)
    total = 0
    counted: list[dict[str, Any]] = []
    unbounded = 0
    for run in live_runs or []:
        if not isinstance(run, Mapping):
            continue
        if family is not None and shell_family(run.get("shell")) != family:
            continue
        allowance_tokens = _num(run.get("allowance_tokens"))
        if allowance_tokens is None or allowance_tokens <= 0:
            unbounded += 1
            continue
        spent = _num(run.get("allowance_spent"))
        undrawn = allowance_tokens if spent is None else max(0.0, allowance_tokens - spent)
        total += int(undrawn)
        counted.append({
            "run_id": run.get("run_id") or run.get("event_id"),
            "title": run.get("title") or None,
            "undrawn": int(undrawn),
            "spend_read": spent is not None,
        })
    return {
        "tokens": total,
        "runs": len(counted),
        "unbounded_runs": unbounded,
        "detail": counted,
    }


def pool(
    ledger: Path | str | None,
    shell: str | None,
    *,
    window: Mapping[str, Any] | None,
    live_runs: Iterable[Mapping[str, Any]] = (),
    now: float | None = None,
    horizon_hours: float = DEFAULT_HORIZON_HOURS,
    rows: list[dict] | None = None,
) -> dict[str, Any]:
    """The dispatchable token pool on *shell*, priced and net of work in flight.

    *window* is :func:`brr.runner_quota.binding_quota_window`'s reading — the
    binding bucket with ``remaining_pct`` **and** ``window_minutes``. Nothing
    less will do: a remaining percentage whose window is unidentified cannot be
    priced, because the two windows' rates differ by ~6x on Claude and ~4x on
    Codex, and picking one would be a coin flip rendered as a measurement. A
    per-model Claude bucket binds without a clock and lands here as
    ``unmeasured``; that is the honest outcome, not a gap to paper over.

    ``free_tokens`` may be **negative**. That is the reading's most useful
    state: it means the live fleet's undrawn allowances already exceed what the
    window can pay for, and it is exactly the condition point 3 warns on.
    """
    commitment = commitments(live_runs, shell)
    remaining_pct = _num((window or {}).get("remaining_pct"))
    window_minutes = _num((window or {}).get("window_minutes"))
    reading = rate(
        ledger, shell, window_minutes,
        now=now, horizon_hours=horizon_hours, rows=rows,
    )
    base = {
        "shell": shell_family(shell),
        "remaining_percent": remaining_pct,
        "window_minutes": window_minutes,
        "committed_tokens": commitment["tokens"],
        "committed_runs": commitment["runs"],
        "unbounded_runs": commitment["unbounded_runs"],
        "commitments": commitment["detail"],
        "rate": reading,
    }
    if window is None or remaining_pct is None or window_minutes is None:
        return {
            **base,
            "status": "unmeasured",
            "reason": "no binding quota window with both a remaining percent and a duration",
        }
    priced = tokens_for_percent(reading, remaining_pct)
    if priced is None:
        return {**base, "status": "unmeasured", "reason": reading.get("reason") or "rate unmeasured"}
    return {
        **base,
        "status": "measured",
        "pool_tokens": priced,
        "free_tokens": priced - commitment["tokens"],
    }
