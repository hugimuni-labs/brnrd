"""Passive Vibe plan metadata from its credential-keyed whoami cache.

The cache contains no monthly allowance. Multiple entries cannot identify the
active credential without reading its secret, so they render as ambiguous.
Plan facts retain the cache's own timestamp and six-hour TTL; no numeric
quota is inferred and no network request is made.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

# Shells whose plan facts live in the Vibe whoami cache.
_VIBE_FLAVOURS = {"vibe"}

# Level slots this collector can actually populate (per-slot honesty: the
# plan/allowance statement renders in the ``quota`` summary — there is no
# numeric gauge, no spend meter, and no context-window reading on this seam,
# so ``spend`` and ``context_window`` stay ``unimplemented``).
COLLECTED_SLOTS: frozenset[str] = frozenset({"quota"})

#: The whoami cache's own refresh window — the age past which Vibe itself
#: would re-fetch before trusting the entry. Older than this, the plan fact
#: is still real but must be marked stale rather than silently served fresh.
WHOAMI_TTL_SECONDS = 6 * 3600.0

#: Stamp identifying the snapshot's origin on the exit-quota path
#: (``spawn_quota_source``).
SOURCE = "vibe-whoami-cache"


def supported(runner_name: str | None) -> bool:
    """True when *runner_name*'s Shell is Vibe (whoami-cache plan source)."""
    if not runner_name:
        return False
    slug = str(runner_name).strip().lower()
    return any(slug == f or slug.startswith(f + "-") for f in _VIBE_FLAVOURS)


def cache_path(env: dict[str, str] | None = None) -> Path:
    """The whoami cache path, honouring ``VIBE_HOME`` (default ``~/.vibe``)."""
    env = env if env is not None else dict(os.environ)
    home = env.get("VIBE_HOME")
    base = Path(home) if home else Path(env.get("HOME", str(Path.home()))) / ".vibe"
    return base / "whoami_cache.json"


def _epoch(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        value = float(raw)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def _iso(epoch: float) -> str | None:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    except (OverflowError, OSError, ValueError):
        return None


def load_levels(
    env: dict[str, str] | None = None, *, now: float | None = None,
) -> dict[str, object] | None:
    """Passive plan-fact snapshot from Vibe's own whoami cache, or ``None``.

    Never touches the network and never raises: a missing, malformed, or
    plan-less cache is "no reading" (the collector is wired, so the facet
    reads ``absent``), never a crash and never a fabricated summary.
    Multiple keyed entries can exist (one per hashed API key); the active
    credential cannot be proven passively, so more than one entry renders
    an explicit ambiguous reading rather than claiming any entry's plan.
    """
    try:
        raw = json.loads(cache_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not raw:
        return None

    # The active credential cannot be proven passively (proving it would
    # mean reading the keychain secret just for telemetry), so more than one
    # keyed entry is an explicit ambiguous reading: no entry's plan facts
    # are claimed — including not "the freshest one".
    if len(raw) > 1:
        return {
            "source": SOURCE,
            "quota": {
                "summary": (
                    f"vibe plan ambiguous — {len(raw)} cached credentials, "
                    "active one unproven; remaining allowance unavailable "
                    "from this cache"
                ),
            },
        }

    entry = next(iter(raw.values()))
    if not isinstance(entry, dict):
        return None
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    plan_type = payload.get("plan_type")
    plan_name = payload.get("plan_name")
    plan_type = plan_type.strip().lower() if isinstance(plan_type, str) else ""
    plan_name = plan_name.strip() if isinstance(plan_name, str) else ""
    if not plan_type and not plan_name:
        return None

    plan = " / ".join(part for part in (plan_name, plan_type) if part)
    summary = (
        f"vibe cached plan {plan} "
        "(cached metadata, not active billing); "
        "remaining allowance unavailable from this cache"
    )
    levels: dict[str, object] = {"source": SOURCE}
    quota: dict[str, object] = {"summary": summary}
    measured_at = _epoch(entry.get("stored_at_timestamp"))
    stamp = _iso(measured_at) if measured_at is not None else None
    if stamp is not None:
        quota["updated_at"] = stamp
        levels["updated_at"] = stamp
        age = (time.time() if now is None else now) - measured_at
        stale = " (stale)" if age > WHOAMI_TTL_SECONDS else ""
        quota["summary"] = f"{summary}; cached {stamp}{stale}"
    levels["quota"] = quota
    return levels
