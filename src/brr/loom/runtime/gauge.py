"""The wake gauge: what the boots of a window cost before they produced, in one line.

Three facts feed it, all measured: ``wake.sizes`` (one per boot, bytes per
part and the total), ``attempt.ended``'s ``evidence.reads`` (calls and bytes
read before the body's first line) and ``wish.result`` (delivered wishes).
An unknown is said, never counted as zero.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from .ledger import kind_facts

ORDER = ("core", "briefing", "readme", "generated", "notes", "tree", "people", "topics",
         "loom", "occasion", "recipe")


def _since(facts, kind, cut):
    return [f for f in kind_facts(facts, kind) if f.at >= cut]


def gauge(facts, days=7, now=None, spent=None):
    cut = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    sizes = _since(facts, "wake.sizes", cut)
    ends = _since(facts, "attempt.ended", cut)
    parts = {}
    for fact in sizes:
        for name, size in (fact.data.get("parts") or {}).items():
            parts[name] = parts.get(name, 0) + int(size)
    reads = [f.data["evidence"]["reads"] for f in ends
             if isinstance((f.data.get("evidence") or {}).get("reads"), dict)]
    sized = [r for r in reads if r.get("bytes") is not None]
    delivered = {f.data.get("wish") for f in _since(facts, "wish.result", cut) if f.data.get("delivered")}
    out = {"days": days, "boots": len(sizes) or len(ends), "sized_boots": len(sizes),
           "wake_total": sum(int(f.data.get("total") or 0) for f in sizes), "parts": parts,
           "delivered": len(delivered), "traced": len(reads), "sized_reads": len(sized),
           "before_bytes": sum(r["before_bytes"] or 0 for r in sized),
           "read_bytes": sum(r["bytes"] or 0 for r in sized),
           "before_calls": sum(int(r.get("before") or 0) for r in reads),
           "spent": None, "unknown_spend": 0}
    if spent is not None:
        values = {s: spent(s) for s in {str(f.data.get("strand")) for f in (sizes or ends)}}
        known = [v for v in values.values() if v is not None]
        out["spent"] = sum(known) if known else None
        out["unknown_spend"] = len(values) - len(known)
    return out


def line(g):
    from .spend import format_tokens
    bits = [f"{g['days']:g}d: {g['boots']} boots"]
    if g["spent"] is not None:
        bits.append(f"spent {format_tokens(g['spent'])}"
                    + (f" ({g['unknown_spend']} strands unknown)" if g["unknown_spend"] else ""))
    elif g["unknown_spend"]:
        bits.append("spent unknown")
    if g["sized_boots"]:
        total = g["wake_total"]
        shares = sorted(g["parts"].items(), key=lambda kv: (ORDER.index(kv[0]) if kv[0] in ORDER else len(ORDER), kv[0]))
        mix = ", ".join(f"{n} {s / total:.0%}" for n, s in shares if total and s)
        bits.append(f"wake {total / g['sized_boots'] / 1000:.1f} KB avg ({mix})")
    else:
        bits.append("wake size unknown (no wake.sizes yet)")
    bits.append(f"{g['boots'] / g['delivered']:.1f} boots per delivered wish" if g["delivered"]
                else f"{g['boots']} boots, 0 delivered")
    if g["sized_reads"] and g["read_bytes"]:
        bits.append(f"{g['before_bytes'] / g['read_bytes']:.0%} of read bytes before a first line "
                    f"({g['sized_reads']} bodies traced)")
    elif g["traced"]:
        bits.append(f"{g['before_calls'] / g['traced']:.1f} calls before a first line, bytes unknown")
    else:
        bits.append("reads before a first line unknown (no trace yet)")
    return " · ".join(bits)


def _spent(home, facts):
    from .spend import READERS
    shells = {str(f.data.get("strand")): str(f.data.get("shell")) for f in kind_facts(facts, "lease")}

    def spent(strand):
        reader = READERS.get(shells.get(strand, ""))
        return int(reader(home.room(strand))) if reader else None
    return spent


def main(argv):
    from .home import Home, resolve_home
    from .ledger import read_facts
    from .refusals import Parser
    parser = Parser(verb="gauge")
    parser.add_argument("--home")
    parser.add_argument("--days", type=float, default=7)
    args = parser.parse_args(argv)
    home = Home(resolve_home(args.home)[0])
    facts = read_facts(home)
    print(line(gauge(facts, args.days, spent=_spent(home, facts))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
