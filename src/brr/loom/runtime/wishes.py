"""Wishes are a projection of four facts, never a second store."""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from brr.daemon2.facts import Fact, union


@dataclass(frozen=True)
class Say:
    """A span of a letter a wish cites. ``size`` None cites from ``offset`` to the end;
    ``quote`` is kept when the quote could not be placed, and then it cites the whole letter."""
    letter: str
    offset: int = 0
    size: int | None = None
    quote: str | None = None

    @classmethod
    def read(cls, raw) -> "Say":
        """A ``{"letter": …}`` row, or an old ``[id, off, size]`` / ``[id, 0, None, quote]``."""
        if isinstance(raw, Say):
            return raw
        if isinstance(raw, dict):
            raw = [raw.get("letter"), raw.get("offset", 0), raw.get("size"), raw.get("quote")]
        if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 4:
            raise ValueError(f"say {raw!r}: not a say")
        letter, offset, size, quote = (*raw, *(0, None, None)[len(raw) - 1:])
        if not isinstance(letter, str) or not letter:
            raise ValueError(f"say {raw!r}: no letter id")
        if type(offset) is not int or offset < 0 or (size is not None and (type(size) is not int or size < 0)):
            raise ValueError(f"say {raw!r}: offset and size are counts")
        if quote is not None and not isinstance(quote, str):
            raise ValueError(f"say {raw!r}: a quote is text")
        return cls(letter, offset, size, quote)

    def row(self) -> dict:
        return {"letter": self.letter, "offset": self.offset,
                **({"size": self.size} if self.size is not None else {}),
                **({"quote": self.quote} if self.quote is not None else {})}

    def text(self, body: str) -> str:
        """The words cited; an unplaced quote (at 0, no size) cites the whole letter."""
        return body[self.offset:] if self.size is None else body[self.offset:self.offset + self.size]


def read_says(rows) -> list[Say]:
    """Every say a row holds that can be read; a bad one is left out, never raised."""
    found = []
    for raw in rows or ():
        try:
            found.append(Say.read(raw))
        except ValueError:
            continue
    return found


@dataclass
class Wish:
    id: str
    says: list[Say] = field(default_factory=list)
    sender: str = "person"
    line: str = ""
    thread: str = ""
    parent: str = ""
    reading: str = ""
    reads: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    verdict: dict | None = None
    at: str = ""
    origin: str = "derived"
    born: str = ""
    lefts: list[dict] = field(default_factory=list)
    stamps: list[dict] = field(default_factory=list)

    @property
    def left(self) -> dict | None:
        """What is left: the latest restated list, never merged with older ones."""
        return self.lefts[-1] if self.lefts else None

    @property
    def state(self) -> str:
        if self.verdict:
            return self.verdict["as"]
        if any(result["delivered"] for result in self.results):
            return "delivered"
        if self.results:
            return "granting"
        if any(read["by"] == "person" for read in self.reads):
            return "scoped"
        return "read" if self.reads else "heard"

    @property
    def open(self) -> bool:
        return self.state not in {"accepted", "dropped"}

    @property
    def derived_unconfirmed(self) -> bool:
        return self.origin == "derived" and self.state in {"heard", "read"}

    def unconfirmed(self, now: datetime | None = None) -> bool:
        """Age is assessed by the reader; silence never becomes acceptance."""
        return (self.state == "delivered" or self.derived_unconfirmed) and (
            (now or datetime.now(timezone.utc)) - datetime.fromisoformat(self.at)
            > timedelta(days=60))


def wishes(facts: Iterable[Fact]) -> dict[str, Wish]:
    ordered = union(facts)
    letters = {str(f.data.get("id", f.id)): f for f in ordered if f.kind == "letter"}
    found: dict[str, Wish] = {}
    for fact in ordered:
        if fact.kind not in {"wish", "wish.read", "wish.result", "wish.verdict", "wish.left"}:
            continue
        d = fact.data
        ident = d["wish"]
        if fact.kind == "wish":
            wish = found.setdefault(ident, Wish(ident, born=fact.at))
            if fact.id != ident:
                wish.stamps.append({"at": fact.at, "by": fact.by,
                                    "letters": [say.letter for say in read_says(d.get("says"))]})
            for say in read_says(d.get("says")):
                if say not in wish.says:
                    wish.says.append(say)
            wish.sender, wish.line, wish.thread = d["from"], d["line"], d["thread"]
            wish.parent = d.get("parent") or wish.parent
            wish.verdict = None
        else:
            if ident not in found:
                continue
            wish = found[ident]
            if fact.kind == "wish.read":
                wish.reading = d["reading"]
                wish.reads.append(dict(d, at=fact.at, writer=fact.by))
            elif fact.kind == "wish.result":
                wish.results.append(dict(d, at=fact.at, writer=fact.by))
            elif fact.kind == "wish.left":
                steps = wish.left["steps"] if d.get("same") and wish.left else list(d.get("steps") or ())
                wish.lefts.append({"steps": steps, "at": fact.at, "by": fact.by, "gen": d.get("gen")})
                # A left is the plan, not news on the wish: its age stays the last stamp's.
                continue
            else:
                wish.verdict = dict(d, at=fact.at)
        wish.at = fact.at
    for wish in found.values():
        def own_say(say):
            letter = letters.get(say.letter)
            if letter is None:
                return False
            # The sender is data.from: the connector writes a person's
            # channel letters, so letter.by is loom:<install> for them.
            return (str(letter.data.get("from", "")).startswith(("p-", "person:"))
                    if wish.sender == "person"
                    else letter.by.startswith("strand:"))
        # An ask cites no letter: the resident who writes it is the one saying it.
        asked = wish.sender == "resident" and not wish.says
        wish.origin = "said" if asked or (wish.says and all(map(own_say, wish.says))) else "derived"
    return found


WISH_KINDS = ("wish", "wish.read", "wish.result", "wish.verdict", "wish.left", "letter")


def told(facts) -> dict[str, Wish]:
    """``wishes`` once per change to its kinds: a held tick never walks the ledger."""
    from .ledger import kind_facts
    from .project import cached_projection
    return cached_projection(facts, "wishes", (), lambda rows: wishes(kind_facts(rows, *WISH_KINDS)))


def waits_on(found: dict[str, Wish], main: dict) -> dict[str, str]:
    """Who each open ask waits on, derived and never stored.

    An ask is an open wish a resident made, on the thread that asked. It waits
    on that thread's opener. When the opener is a thread that asked onward
    for it (an open ask whose parent is this ask), the wait passes along to
    that thread's opener, until it reaches a person or a thread that has not
    asked. A thread with no opener on record, or one opened by a thread that
    is settled or gone, waits on the operator, as the owners table reads it:
    nobody else will wake for it.
    """
    from .lifecycle import CLOSED
    asks = {w.id: w for w in found.values() if w.open and w.sender == "resident"}
    onward: dict[tuple[str, str], Wish] = {}
    for w in asks.values():
        onward.setdefault((w.thread, w.parent), w)
    heads: dict[str, str] = {}
    for ask in asks.values():
        seen, current = set(), ask
        while True:
            seen.add(current.id)
            head = str(main.get(current.thread, {}).get("opened-by") or "operator")
            opener = head.removeprefix("thread:")
            if head.startswith("thread:") and (opener not in main or main[opener].get("status") in CLOSED):
                head = "operator"
            nxt = onward.get((opener, current.id)) if head.startswith("thread:") else None
            if nxt is None or nxt.id in seen:
                break
            current = nxt
        heads[ask.id] = head
    return heads


def progress(found: dict[str, Wish], thread: str) -> dict | None:
    """A thread's plan is its person wishes: delivered or accepted over all not dropped."""
    rows = [w for w in found.values() if w.thread == thread and w.sender == "person"
            and w.state != "dropped"]
    if not rows:
        return None
    plan = {"done": sum(w.state in {"delivered", "accepted"} for w in rows), "total": len(rows)}
    waiting = sorted((w for w in rows if w.open and w.state != "delivered"), key=lambda w: w.at)
    if waiting:
        plan["next"] = waiting[0].line
    return plan


# A step's receipt: a markdown link, a pull request URL, 'PR #n' or 'brnrd#n', or a commit sha.
# A bare #n is context (an issue, a label row), never a receipt.
RECEIPT = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)|(https://github\.com/\S+/pull/\d+)|\b(?:PR |brnrd)#(\d{3,})\b"
                     r"|\b((?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40})\b")


def receipt(thread: str, row: str) -> tuple[str, str] | None:
    """``(kind, ref)`` of the first receipt in a Plan row's step, before its ` · re:` tail.
    A relative link is a file of the self, named from its root."""
    step = row.rpartition(" · re: ")[0] if " · re: " in row else row
    hit = RECEIPT.search(step)
    if hit is None:
        return None
    link, url, number, sha = hit.groups()
    if link is not None:
        if re.match(r"[a-z]+:", link):
            return ("pr" if re.search(r"/pull/\d+$", link) else "link"), link
        path = posixpath.normpath(posixpath.join("threads", thread, link.split("#")[0]))
        return "file", path
    if url is not None:
        return "pr", url
    return ("pr", "#" + number) if number is not None else ("commit", sha)


def landed_results(found: dict[str, Wish], main: dict, by: str) -> list[Fact]:
    """A landed Plan row with a receipt grants each open wish it cites, no verb to forget.
    A row with an open ``[ ]`` box says it is not done, whatever it mentions.

    One result per wish and ref, never delivered: delivered stays declared. The id is
    the pair's, so the same receipt on two rows or two landings is one fact."""
    from .owners import plan_citations
    out, seen = [], {(w.id, r["ref"]) for w in found.values() for r in w.results}
    for thread, data in sorted(main.items()):
        for box, row in data.get("plan_rows") or ():
            got = None if box is False else receipt(thread, row)
            if got is None:
                continue
            kind, ref = got
            line = _short(row.rpartition(" · re: ")[0].strip(), 160)
            for ident in plan_citations(row):
                wish = found.get(ident)
                if wish is None or not wish.open or (ident, ref) in seen:
                    continue
                seen.add((ident, ref))
                key = hashlib.sha256(f"{ident} {ref}".encode()).hexdigest()[:12]
                out.append(Fact(kind="wish.result", by=by, id="wish.result:landed-" + key, data={
                    "wish": ident, "kind": kind, "ref": ref, "line": line, "delivered": False}))
    return out


def _moves(wish: Wish, strand: str, since: str, alone: bool) -> list[tuple[str, str]]:
    """``(at, why)`` for each thing that changed what is left on ``wish``."""
    me = "strand:" + strand
    moves = [(wish.born, "new")]
    moves += [(s["at"], "steered: " + ", ".join(_letter_word(x) for x in s["letters"]))
              for s in wish.stamps if s["letters"]]
    moves += [(r["at"], f"result {_short(r['ref'], 40)}") for r in wish.results if not r["delivered"]]
    moves += [(s["at"], "named by this body") for s in wish.stamps
              if s["by"] == me and not s["letters"] and s["at"] >= since]
    moves += [(r["at"], "named by this body") for r in wish.reads
              if r.get("writer") == me and r["at"] >= since and not r["reading"].startswith("waiting:")]
    if alone and since:
        # The thread's only open wish: all of this body's work is its work.
        moves.append((since, "this body's work"))
    return moves


def _answered(wish: Wish, found: dict[str, Wish]) -> str:
    """When ``wish`` last said what is left: a left, a park, an ask, a delivery, a verdict."""
    times = [left["at"] for left in wish.lefts]
    times += [r["at"] for r in wish.reads if r["reading"].startswith("waiting:")]
    times += [r["at"] for r in wish.results if r["delivered"]]
    times += [w.born for w in found.values() if w.sender == "resident" and w.parent == wish.id]
    if wish.verdict:
        times.append(wish.verdict["at"])
    return max(times, default="")


def owed_lefts(found: dict[str, Wish], facts: Iterable[Fact], thread: str, strand: str) -> list[tuple[str, str]]:
    """``(wish, why)`` for each open person wish on ``thread`` moved since it last said
    what is left, and not yet asked of this body at a stop. Derived at every read."""
    from .ledger import kind_facts
    rows = [w for w in found.values() if w.thread == thread and w.open and w.sender == "person"]
    if not rows:
        return []
    # The lease is this body's start on every install; body.started stays on one machine.
    since = max((f.at for f in kind_facts(facts, "lease") if f.data.get("strand") == strand), default="")
    asked: dict[str, str] = {}
    for f in kind_facts(facts, "left.asked"):
        if f.data.get("strand") == strand and f.at >= since:
            for ident in f.data.get("wishes") or ():
                asked[ident] = max(asked.get(ident, ""), f.at)
    out = []
    for wish in sorted(rows, key=lambda w: w.born):
        answered = max(_answered(wish, found), asked.get(wish.id, ""))
        moves = _moves(wish, strand, since, len(rows) == 1)
        if wish.state == "delivered":
            # Handed over and waiting on a verdict: only a steer reopens what is left.
            moves = [m for m in moves if m[1].startswith("steered")]
        late = [m for m in moves if m[0] > answered]
        if late:
            out.append((wish.id, "no left yet" if not wish.lefts else max(late)[1]))
    return out


def left_line(owed: list[tuple[str, str]]) -> str:
    """The boundary line the jack refuses a stop on."""
    if not owed:
        return ""
    named = " · ".join(f"{ident} ({why})" for ident, why in owed[:STEERS * 2])
    more = f" · +{len(owed) - STEERS * 2} more" if len(owed) > STEERS * 2 else ""
    return f"left? {named}{more}"


def _letter_word(ident: str) -> str:
    """A person letter by its number, others by their id."""
    return ident.rpartition(":")[2] if ident.startswith("letter:") else ident


def opened_results(found: dict[str, Wish], opened: Iterable[Fact], by: str) -> list[Fact]:
    """A ``pr.opened`` is a result on its thread's wish when the thread has exactly one
    open person wish: no other wish could own the work. Same id as a landed row's."""
    out, seen = [], {(w.id, r["ref"]) for w in found.values() for r in w.results}
    for fact in opened:
        thread, url = fact.data.get("thread"), fact.data.get("url")
        rows = [w for w in found.values() if w.thread == thread and w.open and w.sender == "person"]
        # A wish born after the pull request was not what it was opened for.
        if len(rows) != 1 or not url or (rows[0].id, url) in seen or rows[0].born > fact.at:
            continue
        ident = rows[0].id
        seen.add((ident, url))
        key = hashlib.sha256(f"{ident} {url}".encode()).hexdigest()[:12]
        out.append(Fact(kind="wish.result", by=by, id="wish.result:landed-" + key, data={
            "wish": ident, "kind": "pr", "ref": url, "line": f"PR #{fact.data.get('number')} opened",
            "delivered": False}))
    return out


def plan_word(found: dict[str, Wish], thread: str) -> str:
    plan = progress(found, thread)
    return f"{plan['done']}/{plan['total']}" if plan else "—"


STEERS = 3


def steer_candidates(found: dict[str, Wish], thread: str) -> list[Wish]:
    """Open person wishes a follow-up on ``thread`` may steer, newest first."""
    return sorted((w for w in found.values() if w.thread == thread and w.open
                   and w.sender == "person"), key=lambda w: w.at, reverse=True)


def steers_line(found: dict[str, Wish], thread: str, letter: str) -> str:
    """The prompt under a person letter that has no disposition yet."""
    rows = steer_candidates(found, thread)
    if not rows:
        return ""
    named = " · ".join(f'{w.id} "{_short(w.line, 40)}"' for w in rows[:STEERS])
    more = f" · +{len(rows) - STEERS} more: wishes --thread {thread}" if len(rows) > STEERS else ""
    return (f"steers? {named}{more} · or new — send --re {letter} --wish <w-id> "
            '[--says "…"] | --wish new:"<line>" | --chatter')


def steers_refusal(found: dict[str, Wish], thread: str | None) -> str:
    rows = steer_candidates(found, thread) if thread else []
    if not rows:
        return ""
    named = " | ".join(f'--wish {w.id} ("{_short(w.line, 40)}")' for w in rows[:STEERS])
    more = f" | wishes --thread {thread}" if len(rows) > STEERS else ""
    return f"if it steers a wish on {thread}: {named}{more}; else "


def _short(value: str, size: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= size else text[:size - 1] + "…"


def waits_word(head: str) -> str:
    return f" · waits on {head}"


def wish_line(wish: Wish) -> str:
    state = wish.state
    if wish.origin == "derived":
        state += ", derived"
    if wish.unconfirmed():
        state += ", unconfirmed"
    ref = wish.results[-1]["ref"] if wish.results else "-"
    return f"{_short(wish.id, 48)} · {state} · {_short(wish.line, 160)} · {_short(ref, 120)}"


def wish_tree(wish: Wish, bodies: dict[str, str] | None = None, rows=()) -> list[str]:
    """One open wish as the wake shows it: done from its results, the latest left,
    and the steers that came after it. ``rows`` are Plan rows, read only for a wish
    that has no left yet."""
    lines = [wish_line(wish)]
    if wish.sender == "resident":
        return lines
    refs = list(dict.fromkeys(r["ref"] for r in wish.results))
    if refs:
        more = f" · +{len(refs) - 5}" if len(refs) > 5 else ""
        lines.append("  done: " + " · ".join(_short(ref, 60) for ref in refs[-5:]) + more)
    left = wish.left
    if left is not None:
        steps = " · ".join(_short(step, 100) for step in left["steps"][:8]) or "(no step listed)"
        lines.append(f"  left (gen {left['gen']}, {left['at'][5:16].replace('T', ' ')}): {steps}")
    elif wish.state != "delivered":
        from .owners import plan_citations
        cited = [_short(row.rpartition(" · re: ")[0] or row, 80) for box, row in rows
                 if box is not False and wish.id in plan_citations(row) and receipt(wish.thread, row) is None]
        lines.append("  left: none said yet" + ("; README rows citing it: " + " · ".join(cited[:3]) if cited else ""))
    since = [x for x in wish.stamps if x["letters"] and (left is None or x["at"] > left["at"])
             and x["at"] > wish.born]
    if since:
        said = [f'{_letter_word(i)} "{_short((bodies or {}).get(i, ""), 60)}"'
                for x in since[-3:] for i in x["letters"][:1]]
        lines.append("  steered since: " + " · ".join(said))
    return lines


def wake_wishes(facts: Iterable[Fact], thread: str, plan_rows=()) -> str:
    facts = list(facts)
    rows = [w for w in wishes(facts).values() if w.thread == thread and w.open]
    if not rows:
        return ""
    rows.sort(key=lambda w: (w.sender != "resident", w.id))
    bodies = {str(f.data.get("id", f.id)): str(f.data.get("body", "")) for f in facts if f.kind == "letter"}
    section = "## Wishes\n" + "".join(line + "\n" for w in rows[:20] for line in wish_tree(w, bodies, plan_rows))
    if len(rows) > 20:
        section += f"+{len(rows) - 20} more · wishes --thread {thread}\n"
    return section


def insert_wishes(prompt: str, section: str) -> str:
    """Follow the Plan, or the thread README when it has no Plan yet."""
    start = prompt.find("## Plan\n")
    if start < 0:
        start = prompt.find("## threads/")
    end = prompt.find("\n## ", start + 3) if start >= 0 else -1
    if end < 0:
        return prompt + "\n" + section
    return prompt[:end + 1] + section + prompt[end + 1:]
