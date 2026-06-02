"""Agent-ergonomics observability — the back-channel for friction data.

See ``kb/design-agent-ergonomics.md``. This package ships the first
slice: the deterministic **probe** layer plus the ``Null`` / ``Local``
proxies and the local store the ``brr ergonomics`` CLI reads. Telemetry,
sampled reflection, and the brnrd proxy are later slices that reuse the
same ``Record`` shape and ``ErgoProxy`` seam.

Daemon entry point is ``probe_task_prep`` (runs the per-task probe set,
no-ops on the default null proxy). The CLI reads via ``store``.
"""

from __future__ import annotations

from .probes import probe_task_prep, run_probes
from .proxy import ErgoProxy, LocalErgoProxy, NullErgoProxy, get_proxy
from .record import Record
from .store import IssueSummary, clear, ergonomics_dir, read_records, summarize

__all__ = [
    "Record",
    "ErgoProxy",
    "NullErgoProxy",
    "LocalErgoProxy",
    "get_proxy",
    "probe_task_prep",
    "run_probes",
    "ergonomics_dir",
    "read_records",
    "summarize",
    "clear",
    "IssueSummary",
]
