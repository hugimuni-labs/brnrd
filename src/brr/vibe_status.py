"""Vibe whoami-cache plan collector — the level-facet source for the Vibe Shell.

**The monthly allowance is not readable from any route probed.** Measured
2026-09-29 (``.brr/reports/vibe-quota-wire-1d0h.md``): the whoami route
answers the saved API key with plan facts only — no usage, allowance,
remaining, or reset field anywhere in the raw response — and every guessed
console sibling (``/api/vibe/{usage,quota,subscription,allowance,limits,plans}``)
returned 401 from auth middleware before routing, so no key-authenticated
endpoint was identified among the routes tried. What this collector states is
the honest, cache-scoped claim: **the remaining allowance is unavailable from
this cache** — never that no endpoint exists anywhere.

The only authenticated quota
fields found anywhere on the wire are per-minute RPM/TPM headers on a chat
completion, which (a) cost a real completion to read and (b) measure
headroom against a 500k tokens/minute transport limit, not the monthly
pool — collecting them here would spend the subscription to mislabel a
rate limit as quota. So this collector is deliberately **passive**: it reads
the cached plan facts and states the remaining allowance as unavailable from
this cache — never a percent, never a reset, never "unlimited".

The reading is Vibe's own whoami cache (``$VIBE_HOME/whoami_cache.json``,
default ``~/.vibe``), which the CLI refreshes on its own 6h TTL, keyed by a
hash of the API key (``sha256(api_key)`` — ``setup/auth/whoami.py``), so a
cache entry identifies a *credential*, and the collector cannot prove which
keyed entry is the active one without reading the keychain secret — which it
will never do just for telemetry. Consequence, held here: **a single cached
entry's plan facts are stated as cached metadata, not active billing**, and
**multiple keyed entries read as ambiguous** — the active credential is
unproven, so no entry's plan is claimed. The hashed keys and the payload's
``customer_id`` are read past and **never copied into the snapshot**.

Honesty rules, matching the shared levels contract:

- **measured and dated** — the cached entry's ``stored_at_timestamp`` is the
  reading's own clock, carried as ``quota.updated_at`` (the stamp
  ``daemon._levels_measured_at`` prefers), not the moment brnrd read it;
- **stale vs missing** — an entry older than Vibe's own TTL renders a
  stale-marked summary (a real fact, honestly aged); no cache at all
  renders no reading, so the facet reads ``absent``, never a guess;
- **unknown blocks nothing** — ``runner_quota.binding_quota_remaining_pct``
  binds only on numeric fields and this snapshot carries none, so pacing
  and dispatch floors fall through to "not resolvable", which by design
  does not refuse anything.
"""

from __future__ import annotations

import json
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
    return any(slug == f or slug.startswith(f) for f in _VIBE_FLAVOURS)


def cache_path(env: dict[str, str] | None = None) -> Path:
    """The whoami cache path, honouring ``VIBE_HOME`` (default ``~/.vibe``)."""
    env = env if env is not None else dict(os.environ)
    home = env.get("VIBE_HOME")
    base = Path(home) if home else Path(env.get("HOME", str(Path.home()))) / ".vibe"
    return base / "whoami_cache.json"


def _epoch(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if value > 0 else None


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


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
    plan_type = str(payload.get("plan_type") or "").strip().lower() or None
    plan_name = str(payload.get("plan_name") or "").strip() or None
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
    if measured_at is not None:
        stamp = _iso(measured_at)
        quota["updated_at"] = stamp
        levels["updated_at"] = stamp
        age = (time.time() if now is None else now) - measured_at
        stale = " (stale)" if age > WHOAMI_TTL_SECONDS else ""
        quota["summary"] = f"{summary}; cached {stamp}{stale}"
    levels["quota"] = quota
    return levels
