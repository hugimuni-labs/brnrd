"""Two queues, one per direction, derived at every read and never stored as a list.

Mine holds the open wishes to the resident, in the person's order: a wish's
place is the last time they put it on top (the letter it was said in, or a
``wish.top``). Nothing moves a wish down, and one nobody ever put on top is
idle. Yours holds the open asks to the person and the wishes delivered to
them, ranked where the wish they hang from ranks, so the person never sorts a
second list. Next build is the resident's pick when one is live, else the top
of mine that no ask to the person blocks (threads/requests/design.md §3).
"""

from __future__ import annotations

from dataclasses import dataclass

from .wishes import Wish, _short

# Below this place mine folds into a count: idle is a place, not a state.
SHOWN = 12


@dataclass
class Queues:
    mine: list[Wish]
    idle: list[Wish]
    yours: list[Wish]
    blocked: dict[str, list[Wish]]
    next: Wish | None
    why: str


def _to_person(heads: dict[str, str], ident: str) -> bool:
    head = heads.get(ident, "")
    return bool(head) and not head.startswith("thread:")


def queues(found: dict[str, Wish], heads: dict[str, str]) -> Queues:
    """``heads`` is ``waits_on``: who each open ask waits on."""
    wanted = [w for w in found.values() if w.sender == "person" and w.open and w.state != "delivered"]
    ranked = sorted((w for w in wanted if w.ranked_at), key=lambda w: (w.ranked_at, w.id), reverse=True)
    unranked = sorted((w for w in wanted if not w.ranked_at), key=lambda w: (w.born, w.id), reverse=True)
    order = [*ranked, *unranked]
    mine, idle = ranked[:SHOWN], [*ranked[SHOWN:], *unranked]
    place = {w.id: n for n, w in enumerate(order)}
    asks = [w for w in found.values() if w.sender == "resident" and w.open and _to_person(heads, w.id)]
    blocked: dict[str, list[Wish]] = {}
    for ask in asks:
        blocked.setdefault(ask.parent, []).append(ask)
    delivered = [w for w in found.values() if w.sender == "person" and w.state == "delivered"]
    far = len(order)
    yours = sorted([*asks, *delivered], key=lambda w: (
        place.get(w.parent if w.sender == "resident" else w.id, far), -_epoch(w.born), w.id))
    picks = sorted((w for w in wanted if w.pick and w.id not in blocked), key=lambda w: w.pick["at"], reverse=True)
    if picks:
        return Queues(mine, idle, yours, blocked, picks[0], "mine: " + picks[0].pick["why"])
    top = next((w for w in ranked if w.id not in blocked), None)
    return Queues(mine, idle, yours, blocked, top, "top" if top is not None else "")


def _epoch(at: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(at).timestamp() if at else 0.0


def _row(w: Wish) -> str:
    return f'{w.id} · {w.thread} · "{_short(w.line, 70)}"'


def held_move(wish: Wish, asks: list[Wish]) -> str:
    steps = (wish.left or {}).get("steps") or ()
    if steps:
        return _short(steps[0], 100)
    if asks:
        return "record a step that can move, or propose a default and a date"
    return _short(wish.line, 100)


def held_row(wish: Wish, asks: list[Wish]) -> str:
    words = "".join(f" · their word moves: {_short(ask.line, 70)}" for ask in asks)
    move = f" · next: {held_move(wish, asks)}" if asks or wish.left else ""
    return f"held {_row(wish)}{words}{move}"


def standing_ask_rows(found: dict[str, Wish], heads: dict[str, str], thread: str,
                      names: dict[str, str]) -> list[str]:
    """The thread owns these questions across bodies, including after delivery."""
    from .person_names import label

    rows = []
    asks = sorted((found[ident] for ident in heads if found[ident].thread == thread),
                  key=lambda ask: (ask.born, ask.id), reverse=True)
    for ask in asks:
        ident, head = ask.id, heads[ask.id]
        row = f"Your open ask {ident} · waiting on {label(head, names)} · {_short(ask.line, 120)}"
        if ask.parent:
            parent = found.get(ask.parent)
            state = f" ({parent.state})" if parent else ""
            row += f" · for {ask.parent}{state}"
        rows.append(row)
    return rows


def held_rows_for(found: dict[str, Wish], heads: dict[str, str], thread: str, home_thread: str) -> list[str]:
    q = queues(found, heads)
    return [held_row(wish, q.blocked.get(wish.id, []))
            for wish in (*q.mine, *q.idle)
            if thread == home_thread or wish.thread == thread]


def movable(q: Queues, thread: str, parked: set[str]) -> list[Wish]:
    """Queue order, including rows below the cap and unranked work with a stated step."""
    free = []
    for wish in (*q.mine, *q.idle):
        if wish.thread != thread or wish.id in parked:
            continue
        steps = (wish.left or {}).get("steps")
        if steps or wish.ranked_at and wish.id not in q.blocked:
            free.append(wish)
    return free


def render(q: Queues) -> str:
    """The section the main thread wakes with, and ``wishes --queues`` prints."""
    lines = ["## Queues"]
    if q.next is not None:
        lines.append(f"Next build: {_row(q.next)} · {q.why}")
    else:
        lines.append("Next build: none (nothing ranked is free of an ask to the person)")
    lines.append("held, in their order (move to top only):")
    for n, w in enumerate(q.mine, 1):
        lines.append(f"{n}. {held_row(w, q.blocked.get(w.id, []))}")
    if q.idle:
        lines.append(f"+{len(q.idle)} idle · wishes --queues --all")
    if q.yours:
        lines.append("theirs:")
        for w in q.yours[:SHOWN]:
            if w.sender == "resident":
                lines.append(f'- ask {w.id} · "{_short(w.line, 70)}" · for {w.parent}')
            else:
                ref = next((r["ref"] for r in reversed(w.results) if r["delivered"]), "-")
                lines.append(f'- delivered {w.id} · "{_short(w.line, 60)}" · {_short(ref, 50)} · verdict?')
        if len(q.yours) > SHOWN:
            lines.append(f"+{len(q.yours) - SHOWN} more")
    return "\n".join(lines) + "\n"


def render_idle(q: Queues) -> str:
    return "".join(f"- {_row(w)}\n" for w in q.idle)
