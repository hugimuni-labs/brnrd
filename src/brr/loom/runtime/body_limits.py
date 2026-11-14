"""Failed-start quota evidence is scoped to a core, independent of shell gauges."""

import re
import time
from datetime import datetime, timedelta

from brr.runner_failures import QUOTA_EXHAUSTED, classify_failure
from .ledger import LedgerView, kind_facts


def _clock_reset(tail, now):
    match = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", tail, re.I)
    if not match or int(match[1]) not in range(1, 13) or int(match[2] or 0) > 59:
        return None
    local = datetime.fromtimestamp(now).astimezone()
    hour = int(match[1]) % 12 + (12 if match[3].lower() == "pm" else 0)
    reset = local.replace(hour=hour, minute=int(match[2] or 0), second=0, microsecond=0)
    if reset.timestamp() <= now:
        reset += timedelta(days=1)
    return reset.timestamp()


def _latest_limit(facts, shell, core):
    leases = {(f.data.get("strand"), f.data.get("gen")): (f.data.get("shell"), f.data.get("core"))
              for f in kind_facts(facts, "lease")}
    for fact in reversed(kind_facts(facts, "attempt.ended")):
        evidence = fact.data.get("evidence", {})
        limit = evidence.get("core_limit")
        if limit and (limit["shell"], limit["core"]) == (shell, core):
            return limit
        if (leases.get((fact.data.get("strand"), fact.data.get("gen"))) == (shell, core)
                and any(value is not None for value in evidence.get("first_word_s", {}).values())):
            # A completed run proves the core recovered, including a manual probe.
            return None
    return None


def _weekly_reset(home, shell, now):
    if home is None:
        return None
    from .usage import reading, _reset
    gauge = reading(home, shell, cached_only=True)
    resets = [_reset(w) for w in (gauge or {}).get("windows", [])
              if re.search(r"week|7d", str(w.get("name", "")), re.I)]
    return min((at for at in resets if at > now), default=None)


def limit_evidence(facts, strand, gen, tail, *, home=None):
    if classify_failure(detail=tail) != QUOTA_EXHAUSTED:
        return {}
    lease = next((f for f in reversed(kind_facts(facts, "lease"))
                  if f.data.get("strand") == strand and f.data.get("gen") == gen), None)
    if not lease:
        return {}
    shell, core = lease.data["shell"], lease.data["core"]
    now = time.time()
    previous = _latest_limit(facts, shell, core)
    if previous and ((previous.get("known_reset") and previous["until"] <= now)
                     or previous.get("reset_cap", float("inf")) <= now):
        previous = None
    count = int(previous.get("count", 1)) + 1 if previous else 1
    spell = previous.get("spell") if previous else None
    stated = _clock_reset(tail, now)
    cap = _weekly_reset(home, shell, now)
    if cap is None and previous and previous.get("reset_cap", 0) > now:
        cap = previous["reset_cap"]
    delay = min(900 * 2 ** min(count - 1, 10), 7 * 86400)
    until = stated or now + delay
    if cap is not None:
        until = min(until, cap)
    return {"core_limit": {"shell": shell, "core": core, "until": until,
                           "count": count, "spell": spell or f"{strand}:{gen}",
                           "known_reset": stated is not None,
                           **({"reset_cap": cap} if cap else {}),
                           "source": lease.data.get("body_source", "lease")}}


def dry_core(facts, shell, core):
    """Latest core refusal; quiet ticks use an indexed, revision-bound cache."""
    if isinstance(facts, LedgerView):
        revision = len(facts.by_kind["attempt.ended"])
        key = ("core-limit", shell, core)
        prior = facts.cache.get(key)
        if prior is None or prior[0] != revision:
            prior = (revision, _latest_limit(facts, shell, core))
            facts.cache[key] = prior
        limit = prior[1]
    else:
        limit = _latest_limit(facts, shell, core)
    return limit if limit and limit["until"] > time.time() else None


def next_core(facts, shell, core):
    """Strongest remaining bundled core on the same subscription, below this core."""
    from brr.runner_cores import all_cores
    entries = [e for e in all_cores().values() if e["shell"] == shell and e.get("model")]
    rank = next((e.get("cost_rank") for e in entries if e["model"] == core), None)
    if rank is None:
        return None
    for entry in sorted(entries, key=lambda e: e.get("cost_rank") or 0, reverse=True):
        model = entry["model"]
        if (entry.get("cost_rank") or 0) < rank and not dry_core(facts, shell, model):
            return shell, model
    return None
