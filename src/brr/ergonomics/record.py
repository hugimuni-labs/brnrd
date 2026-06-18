"""The canonical ergonomics record.

All three producer layers (probe, telemetry, reflection) emit the same
shape so the proxy, the store, and any downstream renderer don't care
which layer produced it. See ``kb/design-agent-ergonomics.md`` →
"Canonical record shape". This slice ships only the ``probe`` producer;
the ``kind`` field already distinguishes the others so the store and
CLI need no changes when they land.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any


# Stable enums the daemon owns. Kept as plain str at runtime (Python
# doesn't enforce Literal) but documented here as the allowed values.
KINDS = ("probe", "telemetry", "reflection")
SEVERITIES = ("info", "warn", "error")

# Ordering for "which severity is worse" — used by the store rollup and
# by the log proxy's warn+ threshold. Canonical here so producers and
# readers agree on one scale.
SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2}


@dataclass
class Record:
    kind: str
    issue: str
    severity: str
    detail: dict[str, Any] = field(default_factory=dict)
    project_id: str = ""
    run_id: str | None = None
    env: str = ""
    image: str | None = None
    source: str | None = None
    timestamp: float = field(default_factory=time.time)
    daemon_version: str = ""

    def to_json_line(self) -> str:
        """Serialize to a single JSON line (no embedded newlines)."""
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Record":
        """Build a Record from a parsed dict, ignoring unknown keys.

        Tolerant by design: an older or newer on-disk record with extra
        fields still loads, and missing optional fields fall back to the
        dataclass defaults. A record missing the required ``kind`` /
        ``issue`` / ``severity`` keys raises ``KeyError`` and the caller
        skips the line.
        """
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(
            kind=kwargs.pop("kind"),
            issue=kwargs.pop("issue"),
            severity=kwargs.pop("severity"),
            **kwargs,
        )
