"""Failed-start quota evidence is scoped to a core, independent of shell gauges."""

import re
import time
from datetime import datetime, timedelta

from brr.runner_failures import QUOTA_EXHAUSTED, classify_failure


def reset_at(tail, now=None):
    """Use the provider's stated clock reset; unknown resets get a bounded hold."""
    now = time.time() if now is None else now
    match = re.search(r"resets?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", tail, re.I)
    if match:
        local = datetime.fromtimestamp(now).astimezone()
        hour = int(match[1]) % 12 + (12 if match[3].lower() == "pm" else 0)
        if int(match[1]) not in range(1, 13) or int(match[2] or 0) > 59:
            return now + 900
        reset = local.replace(hour=hour, minute=int(match[2] or 0), second=0, microsecond=0)
        if reset.timestamp() <= now:
            reset += timedelta(days=1)
        return reset.timestamp()
    return now + 900


def limit_evidence(facts, strand, gen, tail):
    if classify_failure(detail=tail) != QUOTA_EXHAUSTED:
        return {}
    lease = next((f for f in reversed(facts) if f.kind == "lease"
                  and f.data.get("strand") == strand and f.data.get("gen") == gen), None)
    if not lease:
        return {}
    return {"core_limit": {"shell": lease.data["shell"], "core": lease.data["core"],
                           "until": reset_at(tail), "source": lease.data.get("body_source", "lease")}}


def dry_core(facts, shell, core):
    """Latest unexpired provider refusal for exactly this shell/core, across threads."""
    from .ledger import kind_facts
    now = time.time()
    return next((limit for f in reversed(kind_facts(facts, "attempt.ended")) if f.kind == "attempt.ended"
                 and (limit := f.data.get("evidence", {}).get("core_limit"))
                 and (limit["shell"], limit["core"]) == (shell, core)
                 and limit["until"] > now), None)


def next_core(facts, shell, core):
    """Strongest remaining bundled core on the same subscription, below this core."""
    from brr.runner_cores import all_cores
    entries = [e for e in all_cores().values() if e["shell"] == shell and e.get("model")]
    rank = next((e.get("cost_rank") for e in entries if e["model"] == core), None)
    # Unknown/custom models have no justified fall-down order.
    if rank is None:
        return None
    for entry in sorted(entries, key=lambda e: e.get("cost_rank") or 0, reverse=True):
        model = entry["model"]
        if (entry.get("cost_rank") or 0) < rank and not dry_core(facts, shell, model):
            return shell, model
    return None
