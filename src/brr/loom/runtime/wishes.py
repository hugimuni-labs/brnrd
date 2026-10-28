"""Wishes are a projection of four facts, never a second store."""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from brr.daemon2.facts import Fact, union

from .home import HOME_THREAD, LEGACY_HOME_THREAD


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
    # An ask's recommendation: the asker's proposal, never a reading of the person's answer.
    rec: str = ""
    reads: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    verdict: dict | None = None
    at: str = ""
    origin: str = "derived"
    born: str = ""
    lefts: list[dict] = field(default_factory=list)
    stamps: list[dict] = field(default_factory=list)
    # An ask's answer: the first letter of whoever it waits on that replies to it,
    # cites it, replies to a letter that raised it, or is stamped on it.
    answer: str = ""
    # The last time a person's letter named the wish (a stamp, or a verdict letter):
    # one after the delivery means they looked, so it stays delivered and never expires.
    heard_at: str = ""
    # Rank is the person's alone: the letter a wish was said in, and each later
    # letter of theirs that moved it to the top. Pick is the resident's.
    said_at: str = ""
    tops: list[dict] = field(default_factory=list)
    pick: dict | None = None

    @property
    def ranked_at(self) -> str:
        """When the person last put this wish on top; empty: never, so it is idle."""
        return max([self.said_at, *(t["at"] for t in self.tops)])

    @property
    def left(self) -> dict | None:
        """What is left: the latest restated list, never merged with older ones."""
        return self.lefts[-1] if self.lefts else None

    @property
    def state(self) -> str:
        if self.verdict:
            return self.verdict["as"]
        if self.answer:
            return "answered"
        delivered = max((r["at"] for r in self.results if r["delivered"]), default="")
        if delivered:
            # Delivered, then expired once nobody looked for EXPIRES: never an
            # acceptance. A correct or a steer after it keeps it delivered.
            if self.heard_at <= delivered and (
                    datetime.now(timezone.utc) - datetime.fromisoformat(delivered) > EXPIRES):
                return "expired"
            return "delivered"
        if self.results:
            return "granting"
        if any(read["by"] == "person" for read in self.reads):
            return "scoped"
        return "read" if self.reads else "heard"

    @property
    def open(self) -> bool:
        return self.state not in SHUT

    @property
    def derived_unconfirmed(self) -> bool:
        return self.origin == "derived" and self.state in {"heard", "read"}

    def unconfirmed(self, now: datetime | None = None) -> bool:
        """Age is assessed by the reader; silence never becomes acceptance."""
        return (self.state == "delivered" or self.derived_unconfirmed) and (
            (now or datetime.now(timezone.utc)) - datetime.fromisoformat(self.at)
            > timedelta(days=60))


EXPIRES = timedelta(days=3)
# A request nobody owes anything on any more: in no queue, done for its thread.
SHUT = frozenset({"accepted", "dropped", "expired", "answered"})


def _person(letter) -> bool:
    # The sender is data.from: the connector writes a person's
    # channel letters, so letter.by is loom:<install> for them.
    return str(letter.data.get("from", "")).startswith(("p-", "person:"))


def wishes(facts: Iterable[Fact]) -> dict[str, Wish]:
    ordered = union(facts)
    letters = {str(f.data.get("id", f.id)): f for f in ordered if f.kind == "letter"}
    found: dict[str, Wish] = {}
    for fact in ordered:
        if fact.kind not in {"wish", "wish.read", "wish.result", "wish.verdict", "wish.left",
                             "wish.top", "wish.pick"}:
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
            wish.rec = d.get("rec") or wish.rec
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
            elif fact.kind == "wish.top":
                wish.tops.append({"at": fact.at, "ref": d.get("ref"), "by": fact.by})
                continue
            elif fact.kind == "wish.pick":
                wish.pick = {"at": fact.at, "why": d.get("why", ""), "by": fact.by}
                continue
            elif fact.kind == "wish.left":
                steps = wish.left["steps"] if d.get("same") and wish.left else list(d.get("steps") or ())
                wish.lefts.append({"steps": steps, "at": fact.at, "by": fact.by, "gen": d.get("gen")})
                # A left is the plan, not news on the wish: its age stays the last stamp's.
                continue
            else:
                wish.verdict = dict(d, at=fact.at)
        wish.at = fact.at
    asked = [w for w in found.values() if w.sender == "resident"]
    if asked:
        _answer(asked, letters, ordered)
    named: dict[str, str] = {}
    for letter in letters.values():
        if letter.data.get("wish") and _person(letter):
            named[str(letter.data["wish"])] = max(named.get(str(letter.data["wish"]), ""), letter.at)
    for wish in found.values():
        wish.heard_at = max([named.get(wish.id, ""), *(s["at"] for s in wish.stamps
                             if any(i in letters and _person(letters[i]) for i in s["letters"]))])
        if wish.sender == "person":
            wish.said_at = min((letters[s.letter].at for s in wish.says
                                if s.letter in letters and _person(letters[s.letter])), default="")

        def own_say(say):
            letter = letters.get(say.letter)
            if letter is None:
                return False
            return _person(letter) if wish.sender == "person" else letter.by.startswith("strand:")
        # An ask cites no letter: the resident who writes it is the one saying it.
        asked = wish.sender == "resident" and not wish.says
        wish.origin = "said" if asked or (wish.says and all(map(own_say, wish.says))) else "derived"
    return found


# The home thread (and its legacy name) routes letters; it holds no answer of its own.
RELAYS = frozenset({HOME_THREAD, LEGACY_HOME_THREAD})


def _answer(asked: list[Wish], letters: dict, ordered: list[Fact]) -> None:
    """Each ask's answer, if it has one. The asking thread's own letters never answer
    it: its mail links the ask, and its raises do too."""
    # The latest lease names the thread a strand writes for.
    writers = {str(f.data.get("strand")): str(f.data.get("thread")) for f in ordered if f.kind == "lease"}
    raised: dict[str, set[str]] = {}
    for ident, letter in letters.items():
        for link in letter.data.get("links") or ():
            raised.setdefault(ident, set()).add(str(link))
    for ask in asked:
        def answers(letter) -> bool:
            if letter.at < ask.born:
                return False
            re = str(letter.data.get("re") or "")
            replies = re == ask.id or ask.id in raised.get(re, ())
            if _person(letter):
                return replies or ask.id in (letter.data.get("links") or ())
            # A thread asked onward answers only by replying: a mention is not an answer.
            # The home thread relays to the person and never answers for them.
            writer = writers.get(letter.by.removeprefix("strand:"))
            return (letter.by.startswith("strand:") and replies
                    and writer != ask.thread and writer not in RELAYS)
        stamped = [i for s in ask.stamps for i in s["letters"] if i in letters and _person(letters[i])]
        ask.answer = next(iter(stamped), None) or next((i for i, f in letters.items() if answers(f)), "")


WISH_KINDS = ("wish", "wish.read", "wish.result", "wish.verdict", "wish.left", "wish.top", "wish.pick",
              "letter", "lease")


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
            if nxt is None and opener in RELAYS:
                # The home thread opened it for the person and relays the ask: it waits on them.
                head = "operator"
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
    plan = {"done": sum(w.state in {"delivered", "accepted", "expired"} for w in rows), "total": len(rows)}
    waiting = sorted((w for w in rows if w.open and w.state != "delivered"), key=lambda w: w.at)
    if waiting:
        plan["next"] = waiting[0].line
    return plan


# A step's receipt: a pull request URL, 'PR #n' or 'brnrd#n', or a commit sha; a markdown link
# to one of those counts too. A link to a file of the self is a mention, a receipt only under [x].
# A bare #n is context (an issue, a label row), never a receipt.
RECEIPT = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)|(https://github\.com/\S+/pull/\d+)|\b(?:PR |brnrd)#(\d{3,})\b"
                     r"|\b((?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40})\b")


def receipt(thread: str, row: str, box: bool | None = None) -> tuple[str, str] | None:
    """``(kind, ref)`` that makes a Plan row's step done, read before its ` · re:` tail.

    An open ``[ ]`` box is never done. A PR, a pull URL or a sha is a receipt. A link
    to a file of the self (named from its root) is a mention: it counts only when the
    box is ``[x]``, and an ``[x]`` with nothing to cite is done as the README's own row."""
    if box is False:
        return None
    step = row.rpartition(" · re: ")[0] if " · re: " in row else row
    mention = None
    for hit in RECEIPT.finditer(step):
        link, url, number, sha = hit.groups()
        if link is not None:
            if re.search(r"/pull/\d+$", link):
                return "pr", link
            if mention is None and not re.match(r"[a-z]+:", link):
                mention = "file", posixpath.normpath(posixpath.join("threads", thread, link.split("#")[0]))
            continue
        if url is not None:
            return "pr", url
        return ("pr", "#" + number) if number is not None else ("commit", sha)
    if box:
        return mention or ("box", f"threads/{thread}/README.md")
    return None


def landed_results(found: dict[str, Wish], main: dict, by: str) -> list[Fact]:
    """A landed Plan row that ``receipt`` reads as done grants each open wish it cites, no
    verb to forget. A row that only links a doc, or says it waits, grants nothing.

    One result per wish and ref, never delivered: delivered stays declared. The id is
    the pair's, so the same receipt on two rows or two landings is one fact."""
    from .owners import plan_citations
    out, seen = [], {(w.id, r["ref"]) for w in found.values() for r in w.results}
    for thread, data in sorted(main.items()):
        for box, row in data.get("plan_rows") or ():
            got = receipt(thread, row, box)
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
    section = "## Wishes\n"
    # The last reopen, while the wish that reopened it is still open here.
    held = {w.id for w in rows}
    reopen = max((f for f in facts if f.kind == "thread.reopened" and f.data.get("thread") == thread
                  and f.data.get("wish") in held), key=lambda f: f.at, default=None)
    if reopen is not None:
        d = reopen.data
        section += (f"reopened {reopen.at[5:16].replace('T', ' ')}: {d['wish']} placed by "
                    f"{reopen.by.removeprefix('strand:')} on a thread {d['was']}"
                    + (f" as: {_short(d['why'], 120)}" if d.get("why") else "") + "\n")
    section += "".join(line + "\n" for w in rows[:20] for line in wish_tree(w, bodies, plan_rows))
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
