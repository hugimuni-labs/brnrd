"""Codex app-server quota probe — the *active* half of Codex level collection.

:mod:`brr.codex_status` reads quota **passively**, out of the newest session
rollout file's last ``token_count`` event. That is exact and free while a Codex
run is alive, and it has one structural hole: **between runs nothing writes a
rollout event**, so an idle Codex has no fresh numbers on disk at all. Post-#312
the dashboard reports that honestly (``stale`` / ``unknown``) rather than
stamping "now" on hours-old percentages — honest, and still a bad panel to look
at (#315).

The fix is not the "fire a trivial `codex exec` to nudge a rollout write"
proposal #315 opens with (that spends real subscription quota on telemetry).
Codex ships an actual head-less quota endpoint, one layer down from the CLI's
subcommands: ``codex app-server`` speaks JSON-RPC over stdio, and answers

    {"id": 2, "method": "account/rateLimits/read", "params": null}

with the same ``RateLimitSnapshot`` the interactive ``/status`` panel renders —
5h (``primary``) + weekly (``secondary``) windows, ``planType``, credit balance,
and ``rateLimitResetCredits`` (the free "Full reset" grants, which nothing in brr
could see before). It is an account-metadata call, not a completion: **no model
tokens, no subscription quota spent, no active run required.** Verified live
against codex-cli 0.144.1 on 2026-07-12.

So the shape mirrors Claude exactly, and the earlier asymmetry note can retire:

    claude_usage  (active PTY scrape of /usage)  ⟷  codex_usage   (active app-server probe)
    claude_status (passive result-JSON read)     ⟷  codex_status  (passive rollout read)

The probe is cached on disk with a TTL (:data:`DEFAULT_TTL_SECONDS`) and the
cache is what every reader actually reads — including *between* runs and across
daemon restarts, which is the "rough cached state when nothing is running, exact
when a run is live" contract #315 asked for. The passive rollout stays wired as
the fallback for when the probe can't run (logged out, older CLI, app-server
gone): :func:`merge_levels` keeps whichever quota snapshot is genuinely fresher.

Never raises: a probe failure returns ``None`` and the caller falls back to the
last cached value (labelled with its true age) or to the rollout.
"""

from __future__ import annotations

import calendar
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from . import codex_status

# Cache file, written beside the other level snapshots (see ``claude_usage``).
SNAPSHOT_NAME = ".codex-usage-levels.json"

# The probe is a process spawn plus one backend round-trip (~1s), and costs no
# subscription quota — but it is not free wall-clock, and the daemon heartbeat
# beats every ~30s. 60s keeps the dashboard's 300s staleness threshold from ever
# firing on an idle Codex while spawning at most one probe every other beat.
DEFAULT_TTL_SECONDS = 60.0
TTL_ENV_VAR = "BRR_CODEX_USAGE_TTL"

# Hard ceiling on the JSON-RPC exchange. The backend call dominates; a hung
# app-server must never stall a heartbeat.
DEFAULT_TIMEOUT_SECONDS = 15.0

COLLECTED_SLOTS: frozenset[str] = frozenset({"quota"})

_SOURCE = "codex app-server"


def supported(runner_name: str | None) -> bool:
    """True for Codex Shells — same predicate as the passive collector."""
    return codex_status.supported(runner_name)


def _updated_at() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _window_payload(window: Any) -> dict[str, Any] | None:
    """app-server ``RateLimitWindow`` → the rollout event's ``rate_limits`` shape.

    The two seams describe the same windows in different casings
    (``usedPercent``/``windowDurationMins``/``resetsAt`` vs
    ``used_percent``/``window_minutes``/``resets_at``). Normalizing here means
    :func:`codex_status.parse_token_count` — already the single place that knows
    how a Codex quota window renders — stays the only formatter, and the
    dashboard sees one shape regardless of which seam supplied it.
    """
    if not isinstance(window, dict):
        return None
    used = _num(window.get("usedPercent"))
    if used is None:
        return None
    return {
        "used_percent": used,
        "window_minutes": _num(window.get("windowDurationMins")),
        "resets_at": _num(window.get("resetsAt")),
    }


def parse_rate_limits(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize an ``account/rateLimits/read`` result into the levels shape.

    Defensive in the same way as the rollout parser: the app-server protocol is
    OpenAI's, undocumented as a stability contract and free to change, so every
    field is optional and an unrecognized shape yields a snapshot with no level
    slots rather than an exception.
    """
    result = result if isinstance(result, dict) else {}
    snapshot = result.get("rateLimits")
    if not isinstance(snapshot, dict):
        return {"source": _SOURCE, "updated_at": _updated_at()}

    payload: dict[str, Any] = {
        "rate_limits": {
            key: value
            for key, value in (
                ("primary", _window_payload(snapshot.get("primary"))),
                ("secondary", _window_payload(snapshot.get("secondary"))),
                ("plan_type", snapshot.get("planType")),
            )
            if value is not None
        }
    }
    levels = codex_status.parse_token_count(payload, _updated_at())
    levels["source"] = _SOURCE

    quota = levels.get("quota")
    if isinstance(quota, dict):
        # Fields the rollout event never carried. `reset_credits` is the free
        # "Full reset (Weekly + 5 hr)" grants OpenAI hands out — brr could not
        # see them before, and a paused-for-quota director loop that is holding
        # four unused resets is a fact worth surfacing.
        credits = snapshot.get("credits")
        if isinstance(credits, dict):
            balance = _num(credits.get("balance"))
            if balance is not None:
                quota["credit_balance"] = balance
            if isinstance(credits.get("unlimited"), bool):
                quota["credits_unlimited"] = credits["unlimited"]
        resets = result.get("rateLimitResetCredits")
        if isinstance(resets, dict):
            available = _num(resets.get("availableCount"))
            if available is not None:
                quota["reset_credits_available"] = int(available)
    return levels


def _probe_env(env: dict[str, str] | None = None) -> dict[str, str]:
    base = dict(env if env is not None else os.environ)
    # The app-server is a plain client of the user's Codex login; it needs
    # CODEX_HOME/HOME/PATH and nothing brr-specific.
    base.setdefault("RUST_LOG", "error")
    return base


def probe_rate_limits(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
    codex_bin: str = "codex",
) -> dict[str, Any] | None:
    """Ask a throwaway ``codex app-server`` for the account's rate limits.

    Returns the normalized levels snapshot, or ``None`` when the probe could
    not produce one (no ``codex`` binary, logged out, protocol drift, timeout).
    Never raises — a collector must not break a heartbeat.
    """
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            [codex_bin, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # Codex logs sandbox/config warnings here; draining them buys
            # nothing and an undrained pipe can deadlock the child, so drop them.
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=_probe_env(env),
        )
    except (OSError, ValueError):
        return None

    try:
        assert proc.stdin is not None and proc.stdout is not None
        for message in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "brr", "title": "brr", "version": "0"}},
            },
            {"jsonrpc": "2.0", "method": "initialized", "params": None},
            {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": None},
        ):
            proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

        # The server interleaves unsolicited notifications (config warnings,
        # remote-control status) with responses; read until our id lands.
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                return None
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("id") != 2:
                continue
            if "error" in message:
                return None
            result = message.get("result")
            if not isinstance(result, dict):
                return None
            levels = parse_rate_limits(result)
            return levels if "quota" in levels else None
        return None
    except (OSError, ValueError, AssertionError):
        return None
    finally:
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass


def write_snapshot(cache_dir: Path | None, levels: dict[str, Any]) -> Path | None:
    if cache_dir is None:
        return None
    try:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / SNAPSHOT_NAME
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(levels, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return path
    except OSError:
        return None


def load_snapshot(cache_dir: Path | None) -> dict[str, Any] | None:
    """The last cached probe, however old — age lives in its own ``updated_at``.

    This is the "rough cached state" an idle Codex reports: a real number the
    reader can age-label, rather than a blank ``unknown``.
    """
    if cache_dir is None:
        return None
    try:
        data = json.loads((Path(cache_dir) / SNAPSHOT_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _fresh(path: Path, max_age_seconds: float) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < max_age_seconds
    except OSError:
        return False


def _ttl_seconds(env: dict[str, str] | None = None) -> float:
    raw = (env or os.environ).get(TTL_ENV_VAR)
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_TTL_SECONDS


def load_or_refresh_snapshot(
    cache_dir: Path | None,
    *,
    max_age_seconds: float | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Cached probe if fresh, else re-probe; on probe failure, the stale cache.

    The failure fallback is the point: a probe that fails (offline, logged out)
    must degrade to *last known numbers, honestly aged*, never to nothing.
    """
    if cache_dir is None:
        return probe_rate_limits(timeout_seconds=timeout_seconds, env=env)
    if max_age_seconds is None:
        max_age_seconds = _ttl_seconds(env)
    path = Path(cache_dir) / SNAPSHOT_NAME
    if _fresh(path, max_age_seconds):
        cached = load_snapshot(cache_dir)
        if cached is not None:
            return cached
    levels = probe_rate_limits(timeout_seconds=timeout_seconds, env=env)
    if levels is None:
        return load_snapshot(cache_dir)
    write_snapshot(cache_dir, levels)
    return levels


def _updated_epoch(levels: dict[str, Any] | None) -> float:
    """Parse a snapshot's own ``updated_at`` to an epoch; ``-1`` when unknown."""
    if not isinstance(levels, dict):
        return -1.0
    raw = levels.get("updated_at")
    if not isinstance(raw, str) or not raw:
        return -1.0
    try:
        return calendar.timegm(time.strptime(raw, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return -1.0


def merge_levels(
    probe: dict[str, Any] | None,
    rollout: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Fold the active probe and the passive rollout read into one snapshot.

    Slot ownership follows what each seam can actually prove:

    - ``context_window`` / ``tokens`` — rollout only (they are *this thread's*
      occupancy; the account-wide app-server has no view of them).
    - ``quota`` / ``plan_type`` — whichever snapshot is genuinely fresher by its
      own ``updated_at``. During a live Codex run the rollout's per-turn write is
      as current as anything; once the run ends it freezes, and the probe (or the
      probe's cache) carries the panel. Freshest-wins keeps that automatic rather
      than hard-coding a winner, and keeps the merged ``updated_at`` honest — it
      is what the dashboard's staleness math reads.
    """
    if probe is None and rollout is None:
        return None
    merged: dict[str, Any] = {}
    sources: list[str] = []

    if isinstance(rollout, dict):
        for key in ("context_window", "tokens"):
            if key in rollout:
                merged[key] = rollout[key]

    # Only snapshots that actually carry a quota compete. A probe that returned
    # None (offline, logged out, no `codex` binary) or an unparseable one must
    # never win the comparison and blank out a rollout that *did* read — caught
    # by the cloud-gate publish test, which stubs the rollout and no probe.
    # `max` keeps the first maximal element, so probe-first breaks a tie toward
    # the account-authoritative seam.
    candidates = [
        snapshot for snapshot in (probe, rollout)
        if isinstance(snapshot, dict) and "quota" in snapshot
    ]
    quota_from: dict[str, Any] = (
        max(candidates, key=_updated_epoch) if candidates else {}
    )
    for key in ("quota", "plan_type"):
        if key in quota_from:
            merged[key] = quota_from[key]
    if not merged:
        return None

    # `updated_at` describes the freshest thing in the merged snapshot; the
    # dashboard ages the *quota* panel off it, so it tracks the quota source.
    merged["updated_at"] = (
        quota_from.get("updated_at")
        or (rollout or {}).get("updated_at")
        or _updated_at()
    )
    for snapshot in (quota_from, rollout, probe):
        if isinstance(snapshot, dict):
            source = snapshot.get("source")
            if isinstance(source, str) and source.strip():
                sources.append(source.strip())
    if sources:
        merged["source"] = " + ".join(dict.fromkeys(sources))
    return merged
