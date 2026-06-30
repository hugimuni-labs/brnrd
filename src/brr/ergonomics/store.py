"""Read side of the local ergonomics store.

``LocalErgoProxy`` writes ``.brr/ergonomics/<YYYY-MM-DD>.jsonl``; this
module reads it back for the ``brr ergonomics`` CLI. Kept separate from
the proxy so the read path has no write-side imports (proxy imports
``ergonomics_dir`` from here, not the other way around).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .record import Record, SEVERITY_RANK


def ergonomics_dir(brr_dir: Path) -> Path:
    """Return the local ergonomics store directory for a repo's ``.brr``."""
    return brr_dir / "ergonomics"


def read_records(
    brr_dir: Path,
    *,
    days: int | None = None,
    issue: str | None = None,
) -> list[Record]:
    """Load records from the local store, newest last.

    ``days`` filters to records with a timestamp within the last N days
    (by wall clock, not by filename). ``issue`` filters to one issue
    identifier. Malformed lines are skipped rather than raising — the
    store is forensic, not transactional.
    """
    store = ergonomics_dir(brr_dir)
    if not store.exists():
        return []

    cutoff = time.time() - days * 86400 if days is not None else None
    records: list[Record] = []
    for path in sorted(store.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = Record.from_dict(json.loads(line))
            except (ValueError, KeyError, TypeError):
                continue
            if cutoff is not None and record.timestamp < cutoff:
                continue
            if issue is not None and record.issue != issue:
                continue
            records.append(record)
    records.sort(key=lambda r: r.timestamp)
    return records


@dataclass
class IssueSummary:
    issue: str
    count: int = 0
    severity: str = "info"
    last_seen: float = 0.0
    envs: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "count": self.count,
            "severity": self.severity,
            "last_seen": self.last_seen,
            "envs": sorted(self.envs),
        }


def summarize(records: list[Record]) -> list[IssueSummary]:
    """Roll records up by issue: count, worst severity, last seen, envs.

    Sorted by worst severity then count, both descending, so the most
    actionable issues sort to the top.
    """
    by_issue: dict[str, IssueSummary] = {}
    for record in records:
        summary = by_issue.get(record.issue)
        if summary is None:
            summary = IssueSummary(issue=record.issue)
            by_issue[record.issue] = summary
        summary.count += 1
        if SEVERITY_RANK.get(record.severity, 0) >= SEVERITY_RANK.get(
            summary.severity, 0
        ):
            summary.severity = record.severity
        summary.last_seen = max(summary.last_seen, record.timestamp)
        if record.env:
            summary.envs.add(record.env)
    return sorted(
        by_issue.values(),
        key=lambda s: (SEVERITY_RANK.get(s.severity, 0), s.count),
        reverse=True,
    )


def clear(brr_dir: Path, *, before: str | None = None) -> list[str]:
    """Delete stored day-files. With ``before`` (``YYYY-MM-DD``), only
    delete files for days strictly before that date; otherwise delete
    the whole store. Returns the filenames removed.
    """
    store = ergonomics_dir(brr_dir)
    if not store.exists():
        return []
    removed: list[str] = []
    for path in sorted(store.glob("*.jsonl")):
        if before is not None and path.stem >= before:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path.name)
    return removed


def clear_records(brr_dir: Path, *, before_ts: float | None = None) -> int:
    """Delete records from the local store and return the number removed.

    Without ``before_ts`` this removes whole day files. With ``before_ts`` it
    rewrites partially matching files so newer records survive.
    """
    store = ergonomics_dir(brr_dir)
    if not store.exists():
        return 0
    if before_ts is None:
        removed = 0
        for path in sorted(store.glob("*.jsonl")):
            try:
                removed += sum(
                    1 for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            except OSError:
                continue
            try:
                path.unlink()
            except OSError:
                continue
        return removed

    removed = 0
    for path in sorted(store.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        keep: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = Record.from_dict(json.loads(stripped))
            except (ValueError, KeyError, TypeError):
                keep.append(line)
                continue
            if record.timestamp < before_ts:
                removed += 1
            else:
                keep.append(line)
        if keep:
            path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    return removed
