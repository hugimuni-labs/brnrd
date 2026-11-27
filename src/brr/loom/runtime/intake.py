"""Open a letter, or move the one letter a rule already delivered.

``letter`` prints the body and records ``letter.read``. ``send --forward`` and
``send --off-topic`` write one ``letter.routed`` and leave the body where it
was. A refusal writes nothing, so the previous owner still has it.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from brr.daemon2.facts import Fact

from .home import home_of_room, mint, thread_of
from .ledger import append, read_facts
from .port import PortError
from .project import fold
from .refusals import Parser, refuse


def main(argv: list[str]) -> int:
    parser = Parser(verb="letter", prog="brnrd runtime letter")
    parser.add_argument("letter_id")
    parser.add_argument("--room")
    args = parser.parse_args(argv)
    try:
        from .verbs_loom import _room
        from .home import strand_of_room
        room = _room(args.room)
        body = open_letter(room, strand_of_room(room), args.letter_id)
    except (PortError, ValueError, OSError) as exc:
        return refuse("letter", exc)
    sys.stdout.write(body if body.endswith("\n") else body + "\n")
    return 0


def open_letter(room: Path, sender: str, ident: str) -> str:
    """Print the body by recording that this strand opened it. ``to`` stays."""
    home = home_of_room(Path(room))
    facts = read_facts(home)
    letter = _find(facts, ident)
    if letter is None:
        raise PortError(f"letter: no letter {ident}")
    key = _key(letter)
    append(home, Fact(
        kind="letter.read", by=f"strand:{sender}", id="letter.read:" + mint(12),
        data={"letter": key, "strand": sender},
    ))
    return str(letter.data.get("body") or "")


def forward(room: Path, sender: str, ident: str, to: str) -> str:
    try:
        target = thread_of(to)
    except ValueError as exc:
        raise PortError("send: --forward needs --to thread:<id>") from exc
    return _move(room, sender, ident, how="forward", target=target)


def off_topic(room: Path, sender: str, ident: str) -> str:
    home = home_of_room(Path(room))
    return _move(room, sender, ident, how="off-topic", target=home.main_thread())


def _move(room: Path, sender: str, ident: str, *, how: str, target: str) -> str:
    home = home_of_room(Path(room))
    facts = list(read_facts(home))
    letter = _find(facts, ident)
    if letter is None:
        raise PortError(f"send: no letter {ident}")
    key = _key(letter)
    state = fold(facts)
    owner = _owner(state, letter)
    if owner is None:
        raise PortError(f"send: {key} is not on a thread")
    inbox = home.main_thread()
    owner_hold = state.holder.get(owner)
    inbox_hold = state.holder.get(inbox)
    holds_owner = owner_hold is not None and owner_hold[0] == sender
    holds_inbox = inbox_hold is not None and inbox_hold[0] == sender
    if how == "off-topic":
        if owner == inbox:
            raise PortError(f"send: {key} is already the inbox's")
        if not holds_owner:
            raise PortError(f"send: only the strand that holds {owner} can send {key} back off topic")
        gen = int(owner_hold[1])
        by_thread = owner
    else:
        if not holds_owner and not holds_inbox:
            raise PortError(f"send: {sender} holds neither {owner} nor the inbox, so it cannot move {key}")
        if not _open(home, target):
            raise PortError(f"send: {target} is not an open thread on main")
        chain = _chain(state, letter, owner)
        if target in chain:
            raise PortError(
                f"send: {target} already had {key}. The letter stays with its owner. "
                f"When it belongs on {target} after all, write a new letter to that thread "
                f"citing {key} and the reason.")
        returned = _latest_return(state, key)
        if returned is not None and not _read_since(facts, sender, key, returned.at):
            raise PortError(
                f"send: {key} came back off topic. "
                f"Open it with `brnrd runtime letter {key}` before sending it on.")
        gen = int((owner_hold if holds_owner else inbox_hold)[1])
        by_thread = owner if holds_owner else inbox
    read = _was_read(facts, sender, key)
    taken = how == "forward" and holds_inbox and not holds_owner
    shown = _shown_on(facts, owner, key)
    route = Fact(
        kind="letter.routed", by=f"strand:{sender}", id="letter.routed:" + mint(12),
        data={"letter": key, "to": f"thread:{target}", "strand": sender, "how": how,
              "read": read, "shown": shown, "gen": gen},
    )
    facts.append(append(home, route))
    if how == "off-topic":
        _tell(home, facts, "returned", key, f"returned {key} off topic",
              "the inbox owes the letter", owner)
    else:
        unread = "" if read else ", unread"
        _tell(home, facts, "forwarded", f"{key}:{target}",
              f"forwarded {key} by {by_thread}{unread}",
              "the letter is on this thread", target)
        if taken:
            _tell(home, facts, "taken", f"{key}:{target}",
                  f"taken {key} by inbox to {target}",
                  "the letter left this thread", owner)
    return route.id


def _tell(home, facts, kind: str, subject: str, what: str, can: str, thread: str) -> None:
    from .loom import notice
    notice(home, facts, kind, subject, what, can, thread=thread, passive=True)


def _find(facts, ident: str) -> Fact | None:
    found = None
    for fact in facts:
        if fact.kind != "letter":
            continue
        if fact.id == ident or str(fact.data.get("id") or "") == ident:
            found = fact
            if fact.id == ident:
                return fact
    return found


def _key(letter: Fact) -> str:
    return str(letter.data.get("id") or letter.id)


def _owner(state, letter: Fact) -> str | None:
    from .owners import _current_thread
    return _current_thread(state, letter)


def _chain(state, letter: Fact, owner: str) -> list[str]:
    from .project import _destination
    start = _destination(letter.data)
    chain = [start] if start else []
    for fact in state.accepted:
        if fact.kind == "letter.routed" and str(fact.data.get("letter")) == _key(letter):
            thread = _destination(fact.data)
            if thread:
                chain.append(thread)
    if owner not in chain:
        chain.append(owner)
    return chain


def _latest_return(state, key: str) -> Fact | None:
    found = None
    for fact in state.accepted:
        if (fact.kind == "letter.routed" and str(fact.data.get("letter")) == key
                and fact.data.get("how") == "off-topic"):
            found = fact
    return found


def _was_read(facts, sender: str, key: str) -> bool:
    return any(fact.kind == "letter.read" and str(fact.data.get("letter")) == key
               and str(fact.data.get("strand")) == sender for fact in facts)


def _read_since(facts, sender: str, key: str, at: str) -> bool:
    mark = datetime.fromisoformat(at)
    for fact in facts:
        if (fact.kind == "letter.read" and str(fact.data.get("letter")) == key
                and str(fact.data.get("strand")) == sender
                and datetime.fromisoformat(fact.at) > mark):
            return True
    return False


def _shown_on(facts, thread: str, key: str) -> bool:
    leases = {}
    for fact in facts:
        if fact.kind != "lease":
            continue
        try:
            leases[(str(fact.data["strand"]), int(fact.data["gen"]))] = str(fact.data["thread"])
        except (KeyError, TypeError, ValueError):
            continue
    for fact in facts:
        if fact.kind != "shown" or key not in {str(item) for item in fact.data.get("ids") or ()}:
            continue
        try:
            held = leases.get((str(fact.data["strand"]), int(fact.data["gen"])))
        except (KeyError, TypeError, ValueError):
            continue
        if held == thread:
            return True
    return False


def _open(home, thread: str) -> bool:
    from .lifecycle import CLOSED, read_thread
    if not home.routable(thread):
        return False
    return read_thread(home, thread).get("status") not in CLOSED
