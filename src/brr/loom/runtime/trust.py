"""The two trust numbers (threads/two-numbers): took-rec per ask kind, and irreversible acts without a grant.

An ask carries a kind (``ASK_KINDS``) and, usually, a rec. How it was answered
is one of ``ANSWERS``, written as ``ask.answer`` where the answer binds: the
loom writes ``lapsed`` when a ``--default-at`` park runs out unanswered and the
body is told to carry out its rec (waits.py); the body woken by a person's
reply writes ``took``, ``changed`` or ``never`` with ``wish <ask> --answer``,
since only a reader can say whether the reply matches the rec. A person's
verdict on the ask counts without a fact: accepted is took, dropped is changed.
A reply nobody classified yet is ``unread``, said beside the rate, never folded
into it.

Irreversible acts without a grant are the misses: lines under ``## Misses`` in
the thread READMEs on the self's main, which the stop's receipt check (cites.py)
makes a body write for an act that cited nothing. ``guard.granted`` and
``guard.refused`` (grants.py) stand beside it: the acts that ran on a grant and
the ones the never guards stopped. The number should read zero.
"""

from __future__ import annotations

import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ledger import kind_facts

# The verb and act an ask is for: a short list the loom keeps, so the table has rows.
ASK_KINDS = ("merge", "settle", "publish", "delete", "spend", "send-outside", "reincarnate",
             "design-pick", "scope", "other")
# What a body records; ``lapsed`` is the loom's, at the default-at park.
ANSWERS = ("took", "changed", "never")


def answer_fact(by: str, ask: str, how: str, ref: str):
    from brr.daemon2.facts import Fact
    from .home import mint
    return Fact(kind="ask.answer", by=by, id="ask.answer:" + mint(12), data={"wish": ask, "as": how, "ref": ref})


def _instant(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


@dataclass
class Row:
    asks: int = 0
    counts: dict = field(default_factory=dict)
    ages: list = field(default_factory=list)

    def n(self, how: str) -> int:
        return self.counts.get(how, 0)

    @property
    def classified(self) -> int:
        return sum(self.n(h) for h in ANSWERS)

    @property
    def answered(self) -> int:
        return self.classified + self.n("unread")


def outcomes(facts) -> dict[str, tuple[str, str]]:
    """Each resident ask with a rec, as ``(how, when)``: its latest ``ask.answer``, else a
    person's verdict, else ``unread`` once a reply bound to it, else nothing."""
    from .wishes import wishes
    facts = list(facts)
    letters = {str(f.data.get("id", f.id)): f for f in facts if f.kind == "letter"}
    recorded = {}
    for fact in kind_facts(facts, "ask.answer"):
        recorded[fact.data["wish"]] = fact
    out = {}
    for ask in wishes(facts).values():
        if ask.sender != "resident" or not ask.rec:
            continue
        fact = recorded.get(ask.id)
        verdict = ask.verdict or {}
        if fact is not None:
            ref = letters.get(str(fact.data.get("ref") or ""))
            out[ask.id] = (fact.data["as"], ref.at if ref is not None else fact.at)
        elif verdict.get("as") in ("accepted", "dropped") and str(verdict.get("writer", "")).startswith("person:"):
            out[ask.id] = ("took" if verdict["as"] == "accepted" else "changed", verdict["at"])
        elif ask.answer and ask.answer in letters:
            out[ask.id] = ("unread", letters[ask.answer].at)
    return out


def calibrate(facts, days: float = 30, now: datetime | None = None) -> dict[str, Row]:
    """Per kind, the asks born in the window and how each was answered."""
    from .wishes import wishes
    facts = list(facts)
    cut = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    found = outcomes(facts)
    rows: dict[str, Row] = {}
    for ask in wishes(facts).values():
        if ask.sender != "resident" or not ask.born or ask.born < cut:
            continue
        row = rows.setdefault(ask.kind or "(no kind)", Row())
        row.asks += 1
        how, when = found.get(ask.id, ("", ""))
        if not how:
            continue
        row.counts[how] = row.counts.get(how, 0) + 1
        born, at = _instant(ask.born), _instant(when)
        if how != "lapsed" and born and at:
            row.ages.append((at - born).total_seconds())
    return rows


def _age(seconds: float) -> str:
    return f"{seconds / 86400:.1f}d" if seconds >= 86400 else f"{seconds / 3600:.1f}h"


def _rate(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "-"


def table(rows: dict[str, Row], days: float) -> str:
    head = ("kind", "asks", "answered", "took-rec", "lapsed", "age at answer")
    body = []
    for kind, row in sorted(rows.items(), key=lambda kv: (-kv[1].asks, kv[0])):
        took = (f"{_rate(row.n('took'), row.classified)} of {row.classified}"
                + (f" ({row.n('unread')} unread)" if row.n("unread") else ""))
        body.append((kind, str(row.asks), str(row.answered), took, _rate(row.n("lapsed"), row.asks),
                     _age(statistics.median(row.ages)) if row.ages else "-"))
    if not body:
        return f"{days:g}d: no asks\n"
    widths = [max(len(r[i]) for r in (head, *body)) for i in range(len(head))]
    fmt = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()  # noqa: E731
    return f"{days:g}d, asks with a rec\n" + "\n".join(map(fmt, (head, *body))) + "\n"


MISS_LINE = re.compile(r"^\s*[-*]\s+\S", re.M)
KEY = re.compile(r"@([\w./:-]+)")


def misses(self_repo: Path) -> list[tuple[str, str]]:
    """``(thread, line)`` for each list line under ``## Misses`` in a thread README on the self's main."""
    from .cites import missed_text
    from .guards import _git
    from .lifecycle import main_tip
    self_repo = Path(self_repo)
    if not (self_repo / ".git").exists():
        return []
    tip = main_tip(self_repo)
    hits = _git(self_repo, "grep", "-l", "^## Misses", tip, "--", "threads/*/README.md")
    out = []
    for name in hits.stdout.splitlines():
        path = name.partition(":")[2] or name
        text = _git(self_repo, "show", f"{tip}:{path}").stdout
        thread = path.split("/")[1]
        out += [(thread, line.strip()) for line in missed_text(text).splitlines() if MISS_LINE.match(line)]
    return out


def ungranted(facts, self_repo: Path, days: float = 30, now: datetime | None = None) -> dict:
    """Misses in the window (a miss whose key names a letter is dated by it; one that names
    none is always counted, so the number errs high), beside the window's grants and refusals."""
    facts = list(facts)
    cut = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    letters = {str(f.data.get("id", f.id)): f.at for f in facts if f.kind == "letter"}
    counted = 0
    for _thread, line in misses(self_repo):
        dated = [letters[k] for k in KEY.findall(line) if k in letters]
        counted += not dated or max(dated) >= cut
    return {"misses": counted,
            "granted": sum(f.at >= cut for f in kind_facts(facts, "guard.granted")),
            "refused": sum(f.at >= cut for f in kind_facts(facts, "guard.refused"))}


def line(facts, self_repo: Path, days: float = 14, now: datetime | None = None) -> str:
    """The weave-up's line: both numbers, one row."""
    u = ungranted(facts, self_repo, days, now)
    rows = calibrate(facts, days, now)
    total = Row()
    for row in rows.values():
        total.asks += row.asks
        for how, n in row.counts.items():
            total.counts[how] = total.counts.get(how, 0) + n
    acts = (f"{days:g}d: {u['misses']} irreversible acts without a grant "
            f"({u['granted']} granted, {u['refused']} refused by the never guards)")
    if not total.asks:
        return acts + " · no asks"
    kinds = ", ".join(f"{k} {_rate(r.n('took'), r.classified)} of {r.classified}"
                      for k, r in sorted(rows.items(), key=lambda kv: (-kv[1].classified, kv[0])) if r.classified)
    took = f"took-rec {_rate(total.n('took'), total.classified)} of {total.classified} answered"
    tail = [f"{total.n('lapsed')} lapsed", f"{total.n('unread')} unread", f"{total.asks} asks"]
    return f"{acts} · {took}" + (f" ({kinds})" if kinds else "") + " · " + ", ".join(tail)


def main(argv):
    from .home import home_for
    from .ledger import read_facts
    from .refusals import Parser
    parser = Parser(verb="trust")
    parser.add_argument("--home")
    parser.add_argument("--room")
    parser.add_argument("--days", type=float, default=14)
    args = parser.parse_args(argv)
    home = home_for(args.home, args.room)
    print(line(read_facts(home), home.root / "self", args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
