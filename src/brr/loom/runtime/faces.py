"""The face on a thread: the loom proposes one when a fact changes, the body decides.

A fact the body has just met (his letter, a merged PR, a delivered wish, 40
minutes without a letter out, a wake after a failed attempt) proposes a face
at the next boundary. The body takes it (``runtime face take``), wears
another (``runtime face <face>``), or lets it lapse by writing its next
letter without answering. A lapse writes nothing, so the last decided face
stays. A state (a high gen, low fuel) proposes nothing: only a change does.

A face is the face itself, ``b<eye><mouth><eye>d``, never a name: brr.emotes
is a reference, not the wire. The wordmark's letters never move inside it.

A face belongs to the body that set it (strand and gen). The thread shows
it until its next lease: a successor did not choose its predecessor's face,
so it starts with none. The voice is the loom's: the fact that proposed,
whatever the body decided, so a face can neither lie nor nag.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .ledger import LedgerView, kind_facts
from .project import fold

QUIET = timedelta(minutes=40)
# One codepoint per eye and mouth, eyes alike; none of b, r, n, d, space inside.
_FACE = re.compile(r"b([^\sbrnd])([^\sbrnd])\1d")
# Trigger kind → the face it proposes; emotes.py's names label them only here.
PROPOSED = {
    "letter": "b°O°d",     # curious
    "quiet": "b·w·d",      # focused
    "merged": "b^‿^d",     # proud
    "delivered": "b^ᵕ^d",  # pleased, peak
    "failed": "bx_xd",
}


def face_of(raw: str) -> str:
    """``raw`` when it is a face, or a refusal that shows what one looks like."""
    if _FACE.fullmatch(raw or ""):
        return raw
    from brr import emotes
    hint = emotes.glyph(raw or "")
    raise ValueError(f"face: {raw!r} is not a face; give the face itself, "
                     + (f"e.g. {hint} " if hint and _FACE.fullmatch(hint) else "")
                     + "b<eye><mouth><eye>d, eyes alike, none of b r n d inside "
                     "(" + " ".join(PROPOSED.values()) + ")")


def _at(fact) -> datetime:
    return datetime.fromisoformat(str(fact.at))


def _body(facts, strand: str, gen: int):
    return next((f for f in reversed(kind_facts(facts, "body.started"))
                 if f.data.get("strand") == strand and f.data.get("gen") == gen), None)


def _met(facts, strand: str):
    """The rows ``triggers`` can act on, in ledger order; on a view, without walking every letter."""
    if not isinstance(facts, LedgerView):
        return kind_facts(facts, "shown", "pr.merged", "wish.result", "letter")
    senders = facts.project("letters-by-sender", None, _letters_by_sender)
    rows = [f for f in facts.by_strand[strand] if f.kind == "shown"]
    rows += [*kind_facts(facts, "pr.merged"), *kind_facts(facts, "wish.result"), *senders.get(strand, ())]
    return sorted(rows, key=lambda f: facts.positions[f.id])


def _letters_by_sender(facts) -> dict[str, list]:
    found: dict[str, list] = {}
    for fact in kind_facts(facts, "letter"):
        found.setdefault(fact.data.get("from"), []).append(fact)
    return found


def triggers(facts, strand: str, gen: int, thread: str, now: datetime | None = None) -> list[dict]:
    """``{kind, fact, at, token}`` per fact this body met, oldest first, each a change."""
    from .dispositions import is_person, letter_index
    from .wishes import told
    start = _body(facts, strand, gen)
    if start is None:
        return []
    began, found = _at(start), []
    ended = next((f for f in reversed(kind_facts(facts, "attempt.ended"))
                  if f.data.get("strand") == strand and f.data.get("gen") == gen - 1), None)
    if ended is not None and ended.data.get("code") not in (0, None):
        found.append({"kind": "failed", "fact": ended.id, "at": began,
                      "token": f"exit {ended.data['code']}"})
    letters, seen, outs = letter_index(kind_facts(facts, "letter")), set(), [began]
    wished = {w.id: w.thread for w in told(facts).values()} if kind_facts(facts, "wish.result") else {}
    for fact in _met(facts, strand):
        if fact.kind == "shown" and fact.data.get("strand") == strand:
            new = [i for i in fact.data.get("ids", ()) if i not in seen
                   and i in letters and is_person(letters[i])]
            seen.update(fact.data.get("ids", ()))
            if new and int(fact.data.get("gen", 0)) == gen:
                found.append({"kind": "letter", "fact": new[-1], "at": _at(fact),
                              "token": f"your {new[-1].rpartition(':')[2].rpartition('/')[2]}"})
            continue
        if fact.kind != "shown" and _at(fact) < began:
            continue
        if fact.kind == "pr.merged" and fact.data.get("thread") == thread:
            found.append({"kind": "merged", "fact": fact.id, "at": _at(fact),
                          "token": f"#{fact.data['number']} merged"})
        elif (fact.kind == "wish.result" and fact.data.get("delivered")
              and wished.get(fact.data.get("wish")) == thread):
            found.append({"kind": "delivered", "fact": fact.id, "at": _at(fact),
                          "token": f"{fact.data['wish']} delivered"})
        elif fact.kind == "letter" and fact.data.get("from") == strand:
            outs.append(_at(fact))
    now = now or datetime.now(began.tzinfo)
    for left, right in zip(outs, [*outs[1:], now]):
        if right - left >= QUIET:
            found.append({"kind": "quiet", "fact": f"quiet:{strand}:{gen}:{left.isoformat()}",
                          "at": left + QUIET, "token": f"quiet since {left:%H:%M}"})
    return sorted(found, key=lambda t: t["at"])


def _standing(facts, strand, gen, thread, now=None):
    """The last trigger that proposes (never the same kind twice in a row), and its outs."""
    proposed = []
    for trigger in triggers(facts, strand, gen, thread, now):
        if not proposed or proposed[-1]["kind"] != trigger["kind"]:
            proposed.append(trigger)
    return proposed[-1] if proposed else None


def propose(facts, strand: str, gen: int, thread: str, now: datetime | None = None) -> dict | None:
    """``{face, token, fact}`` while a proposal stands: nobody decided and no letter went out since."""
    trigger = _standing(facts, strand, gen, thread, now)
    if trigger is None:
        return None
    for fact in kind_facts(facts, "face", "letter", "note"):
        if fact.kind == "face":
            mine = (fact.data.get("strand"), fact.data.get("gen")) == (strand, gen)
        else:
            # A note settles a letter: it is the body's next word as much as a letter is.
            mine = fact.data.get("from") == strand or fact.by == f"strand:{strand}"
        if mine and _at(fact) >= trigger["at"]:
            return None
    return {"face": PROPOSED[trigger["kind"]], "token": trigger["token"], "fact": trigger["fact"]}


def proposal_line(proposal: dict | None) -> str:
    if not proposal:
        return ""
    return (f"face? {proposal['face']} for {proposal['token']} ({proposal['fact']}): "
            "`brnrd runtime face take` wears it, `face <face>` wears another, "
            "your next letter lets it lapse")


# A letter's token is a raw id whose form differs by channel and is often not
# the letter being answered: the face? hint carries it, the header does not.
UNSPOKEN = {"letter"}


def voice(facts, strand: str, gen: int, thread: str) -> str:
    """The fact the header carries: the last that proposed, whatever the body decided."""
    trigger = _standing(facts, strand, gen, thread)
    return trigger["token"] if trigger and trigger["kind"] not in UNSPOKEN else ""


def faces(facts) -> dict[str, dict]:
    """Thread → ``{face, strand, gen, at}`` of its latest lease's body, if it decided one."""
    current = {}
    for fact in fold(facts).accepted:
        if fact.kind == "lease":
            current[str(fact.data["thread"])] = (str(fact.data["strand"]), int(fact.data["gen"]))
    body = {lease: thread for thread, lease in current.items()}
    found: dict[str, dict] = {}
    for fact in kind_facts(facts, "face"):
        thread = body.get((str(fact.data.get("strand")), fact.data.get("gen")))
        if thread is None:
            continue
        if fact.data.get("face"):
            found[thread] = {"face": fact.data["face"], "strand": fact.data["strand"],
                             "gen": fact.data["gen"], "at": fact.at}
        else:
            found.pop(thread, None)
    return found
