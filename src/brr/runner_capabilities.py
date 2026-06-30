"""Cached benchmark hints for capability-aware runner selection.

The selector's hard floor is still deterministic and cost-aware. Capability
scores are hints for class assignment, never a live network dependency and
never a promise that one benchmark captures task quality. The packaged JSON
cache is intentionally small and source/freshness tagged so a future refresh
can update data without changing dispatch code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

ECONOMY = "economy"
BALANCED = "balanced"
STRONG = "strong"

_DATA_PACKAGE = "brr.data"
_DATA_FILE = "runner-capabilities.json"


@dataclass(frozen=True)
class CapabilityHint:
    """One model's cached benchmark hint."""

    model: str
    swe_bench_verified: float | None = None
    terminal_bench: float | None = None
    source: str | None = None
    freshness_date: str | None = None

    @property
    def score(self) -> float | None:
        values = [
            score
            for score in (
                _normalise_score(self.swe_bench_verified),
                _normalise_score(self.terminal_bench),
            )
            if score is not None
        ]
        if not values:
            return None
        return sum(values) / len(values)


@lru_cache(maxsize=1)
def load_capabilities() -> dict[str, CapabilityHint]:
    """Load the packaged benchmark cache, keyed by model id."""
    try:
        text = resources.files(_DATA_PACKAGE).joinpath(_DATA_FILE).read_text(
            encoding="utf-8"
        )
        raw = json.loads(text)
    except (FileNotFoundError, json.JSONDecodeError, ModuleNotFoundError):
        return {}
    models = raw.get("models") if isinstance(raw, dict) else {}
    if not isinstance(models, dict):
        return {}
    out: dict[str, CapabilityHint] = {}
    for model, entry in models.items():
        if not isinstance(entry, dict):
            continue
        out[str(model)] = CapabilityHint(
            model=str(model),
            swe_bench_verified=_float(entry.get("swe_bench_verified")),
            terminal_bench=_float(entry.get("terminal_bench")),
            source=_str(entry.get("source")) or _str(raw.get("source")),
            freshness_date=_str(entry.get("freshness_date"))
            or _str(raw.get("freshness_date")),
        )
    return out


def capability_for_model(
    model: str | None,
    *,
    table: dict[str, CapabilityHint] | None = None,
) -> CapabilityHint | None:
    """Return a cached hint for *model*, matching exact id or prefix."""
    needle = _str(model)
    if not needle:
        return None
    rows = table if table is not None else load_capabilities()
    exact = rows.get(needle)
    if exact:
        return exact
    lower = needle.lower()
    for model_id, hint in rows.items():
        if model_id.lower().startswith(lower):
            return hint
    return None


def class_from_score(score: float | None) -> str | None:
    """Map a normalized benchmark score to brr's coarse cost class."""
    if score is None:
        return None
    if score >= 0.75:
        return STRONG
    if score >= 0.45:
        return BALANCED
    return ECONOMY


def derived_cost_class(
    model: str | None,
    *,
    table: dict[str, CapabilityHint] | None = None,
) -> str | None:
    """Derive a cost class from cached capability scores, when present."""
    hint = capability_for_model(model, table=table)
    return class_from_score(hint.score if hint else None)


def metadata_for_model(model: str | None) -> dict[str, object]:
    """Capability metadata suitable for a runner profile entry."""
    hint = capability_for_model(model)
    if not hint:
        return {}
    out: dict[str, object] = {}
    score = hint.score
    if score is not None:
        out["capability_score"] = round(score, 4)
    if hint.source:
        out["capability_source"] = hint.source
    if hint.freshness_date:
        out["capability_freshness"] = hint.freshness_date
    return out


def _normalise_score(value: float | None) -> float | None:
    if value is None:
        return None
    score = float(value)
    if score > 1.0:
        score = score / 100.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
