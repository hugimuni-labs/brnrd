"""Agent-ergonomics observability — the back-channel for friction data.

See ``kb/design-agent-ergonomics.md``. This package ships the
deterministic **probe** layer plus the ``Null`` / ``Log`` / ``Local``
proxies, the owner-aware resolver, and the local store the ``brr
ergonomics`` CLI reads. Telemetry, hidden (sampled) reflection, and the
brnrd proxy are later slices that reuse the same ``Record`` shape and
``ErgoProxy`` seam.

Routing keys off ``RunContext.owner`` plus the ``ergonomics`` knob
(``off|log|local``, default ``log``): the user-owned default is
a quiet daemon log, operator-owned runs ignore the knob. Daemon entry
point is ``probe_task_prep`` (per-task probe set; no-ops when the
resolved proxy is null). The CLI reads via ``store``.
"""

from __future__ import annotations

from .probes import probe_task_prep, run_probes
from .proxy import (
    ErgoProxy,
    LocalErgoProxy,
    LogErgoProxy,
    NullErgoProxy,
    ergonomics_mode,
    reset_log_dedup,
    resolve_proxy,
)
from .record import Record
from .store import IssueSummary, clear, ergonomics_dir, read_records, summarize

__all__ = [
    "Record",
    "ErgoProxy",
    "NullErgoProxy",
    "LogErgoProxy",
    "LocalErgoProxy",
    "resolve_proxy",
    "ergonomics_mode",
    "reset_log_dedup",
    "probe_task_prep",
    "run_probes",
    "ergonomics_dir",
    "read_records",
    "summarize",
    "clear",
    "IssueSummary",
]
