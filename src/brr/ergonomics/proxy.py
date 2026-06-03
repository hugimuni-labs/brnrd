"""Ergo proxies — where records cross from producer to reader.

The ``ErgoProxy`` Protocol is the producer-side seam: probe / telemetry
/ reflection layers write ``Record`` instances without caring how (or
whether) they reach an operator. Which proxy a run gets is decided by
**ownership** plus one user-facing knob, not by the data (see
``kb/design-agent-ergonomics.md`` → "Ownership decides routing").

Three proxies ship today:

- ``NullErgoProxy`` — drops the record. Resolved for ``ergonomics=off``
  and (for now) for operator-owned runs.
- ``LogErgoProxy`` — emits ``warn``+ records to the daemon log, deduped
  by issue-signature within a window. No disk, no tokens — the
  zero-config default for user-owned runs.
- ``LocalErgoProxy`` — opt-in (``ergonomics=local``). Appends JSONL to
  ``.brr/ergonomics/<YYYY-MM-DD>.jsonl`` for ``brr ergonomics`` to read.

``BrnrdErgoProxy`` (batched HTTPS to brnrd, the operator-owned sink) is
a later slice gated on managed compute + the brnrd ergonomics endpoint.
``response`` is not a proxy — it's a reflection-visibility choice
(``prompts.reflection_enabled``); its probe/telemetry records still flow
through ``LogErgoProxy``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .record import Record, SEVERITY_RANK
from .store import ergonomics_dir


@runtime_checkable
class ErgoProxy(Protocol):
    def emit(self, record: Record) -> None: ...


class NullErgoProxy:
    """Drop every record. Resolved for ``off`` and operator-owned runs."""

    def emit(self, record: Record) -> None:  # noqa: D401 - trivial
        return None


# Process-global dedup window for LogErgoProxy. The proxy is resolved
# per task, so the "have I already logged this?" state can't live on the
# instance; it lives here, keyed by signature → last-logged epoch. A
# long-running daemon that sees the same stale image every task logs it
# once per window, not once per task. Cleared by tests via reset_log_dedup.
_LOG_DEDUP: dict[str, float] = {}
_LOG_DEDUP_LOCK = threading.Lock()


def reset_log_dedup() -> None:
    """Clear the process-global log-dedup window (test seam)."""
    with _LOG_DEDUP_LOCK:
        _LOG_DEDUP.clear()


class LogErgoProxy:
    """Surface ``warn``+ records on the daemon log, deduped.

    The user-owned default. Costs no disk and no tokens: a self-hoster
    gets a quiet heads-up when their agents are running against stale
    images / missing auth / a filling disk, without anything reaching the
    task reply. Records below ``min_severity`` (``info``) are dropped, and
    a given issue-signature re-logs at most once per ``dedup_s`` so a
    persistent condition doesn't spam every task.
    """

    def __init__(self, *, min_severity: str = "warn", dedup_s: float = 21600.0) -> None:
        self._min = SEVERITY_RANK.get(min_severity, 1)
        self._dedup_s = dedup_s

    def emit(self, record: Record) -> None:
        if SEVERITY_RANK.get(record.severity, 0) < self._min:
            return
        now = record.timestamp or time.time()
        sig = f"{record.issue}|{record.env}|{record.image or ''}"
        with _LOG_DEDUP_LOCK:
            last = _LOG_DEDUP.get(sig)
            if last is not None and (now - last) < self._dedup_s:
                return
            _LOG_DEDUP[sig] = now
        print(_format_log_line(record))


def _format_log_line(record: Record) -> str:
    where = f" [{record.env}]" if record.env else ""
    hint = ""
    if isinstance(record.detail, dict):
        raw = record.detail.get("hint")
        if raw:
            hint = f" — {raw}"
    return f"[brr:ergo] {record.severity} {record.issue}{where}{hint}"


class LocalErgoProxy:
    """Append records as JSONL under the shared ``.brr/ergonomics`` dir.

    One file per UTC day so the store rotates without a sweeper and
    ``brr ergonomics clear --before`` can drop whole days by filename.
    Appends are guarded by a process-local lock; cross-process writers
    (daemon + a concurrent CLI) rely on append-mode atomicity for
    single short lines, which is sufficient for this low-rate stream.
    """

    def __init__(self, store_dir: Path) -> None:
        self._dir = store_dir
        self._lock = threading.Lock()

    def emit(self, record: Record) -> None:
        day = _utc_day(record.timestamp)
        path = self._dir / f"{day}.jsonl"
        line = record.to_json_line()
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def _utc_day(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(timestamp))


# Old/loose spellings folded onto the four-value surface, so a config
# someone wrote against an earlier cut (or a bare bool) still resolves.
_MODE_ALIASES = {
    "null": "off",
    "none": "off",
    "false": "off",
    "0": "off",
    "true": "log",
    "1": "log",
    # operator sinks aren't user-selectable; on a user-owned run they
    # degrade to the quiet log rather than silently doing nothing.
    "brnrd": "log",
    "cloud": "log",
}


def ergonomics_mode(cfg: dict[str, Any] | None) -> str:
    """Normalise the user-facing ``ergonomics`` knob to off|log|local|response.

    Default is ``log`` (quiet daemon log) — the user-owned default that
    gives a self-hoster free efficiency signal with no token cost. Reads
    the bare ``ergonomics`` key, accepting the older ``ergonomics.proxy``
    spelling and a few loose aliases. ``runner.self_review=true`` (the
    deprecated standalone knob) is treated as ``response``.
    """
    cfg = cfg or {}
    raw = cfg.get("ergonomics")
    if raw is None:
        raw = cfg.get("ergonomics.proxy", cfg.get("ergonomics_proxy"))
    if raw is None:
        if cfg.get("runner.self_review", cfg.get("runner_self_review")):
            return "response"
        return "log"
    mode = str(raw).strip().lower()
    mode = _MODE_ALIASES.get(mode, mode)
    if mode not in ("off", "log", "local", "response"):
        return "log"
    return mode


def resolve_proxy(
    cfg: dict[str, Any], brr_dir: Path, owner: str = "user"
) -> ErgoProxy:
    """Resolve the proxy for a run, owner-first.

    Operator-owned runs ignore the ``ergonomics`` knob entirely — routing
    on managed compute is the operator's, not the dispatched user's (the
    sink becomes ``BrnrdErgoProxy`` when managed compute lands; until then
    nothing is captured). User-owned runs honour the knob:

    - ``off`` → ``NullErgoProxy`` (short-circuited by the orchestrator)
    - ``local`` → ``LocalErgoProxy`` (on-disk JSONL store)
    - ``log`` (default) / ``response`` → ``LogErgoProxy``; ``response``
      additionally shows the agent's reflection in the reply, wired in
      the prompt path (``prompts.reflection_enabled``), not here.
    """
    if owner != "user":
        return NullErgoProxy()
    mode = ergonomics_mode(cfg)
    if mode == "off":
        return NullErgoProxy()
    if mode == "local":
        return LocalErgoProxy(ergonomics_dir(brr_dir))
    return LogErgoProxy()
