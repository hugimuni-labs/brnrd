"""Since you were gone: what happened between a thread's last body and this one.

The cut is the predecessor's last ``shown`` fact, or the thread's last
``attempt.ended`` when that is later. The section opens the wake's loom part:
restarts and the silence before them, how the predecessor ended, what the loom
repaired or fell back on for this strand, notes by outside hands, letters of
this thread that bounced, and the loop's degradations. Every line names its
fact. Newest last, capped at ``CAP`` bytes by dropping the oldest lines.
"""

from __future__ import annotations

import json

from .health import _seconds, open_streaks

CAP = 1500
SILENT = 600
ROOM_KINDS = ("room.repaired", "room.unseeded", "room.seeded", "room.fallback", "wake.fallback")
HEADER = "since your predecessor's last boundary (each line names its fact):"


def _brief(value, size: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    text = " ".join(text.split())
    return text if len(text) <= size else text[:size - 1] + "…"


def _span(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _cut(rows, strand: str, gen: int, thread: str) -> tuple[int, tuple[str, int] | None]:
    """The cut's position and the predecessor; -1 when the thread had no body before."""
    leases = [f for f in rows if f.kind == "lease" and f.data.get("thread") == thread]
    mine = next((i for i, f in enumerate(leases)
                 if f.data.get("strand") == strand and f.data.get("gen") == gen), len(leases))
    before = leases[:mine]
    pred = (str(before[-1].data.get("strand")), before[-1].data.get("gen")) if before else None
    cut = -1
    for i, f in enumerate(rows):
        if f.kind == "shown" and pred and (f.data.get("strand"), f.data.get("gen")) == pred:
            cut = i
        elif f.kind == "attempt.ended" and f.data.get("thread") == thread and (
                f.data.get("strand"), f.data.get("gen")) != (strand, gen):
            cut = i
    return cut, pred


def _ended(rows, pred) -> tuple[int, str] | None:
    """How the predecessor ended, naming a loom restart inside its life."""
    began = None
    for i, f in enumerate(rows):
        if (f.data.get("strand"), f.data.get("gen")) != pred:
            continue
        if f.kind == "body.started" and began is None:
            began = i
        if f.kind == "attempt.ended":
            cause, code = f.data.get("cause"), f.data.get("code")
            restart = began is not None and any(r.kind == "loom.started" for r in rows[began:i])
            how = (f"was lost to a loom restart (recorded {cause}, code {code})"
                   if restart and cause == "failed" else f"ended {cause}, code {code}")
            return i, f"your predecessor {pred[0]} gen {pred[1]} {how} ({f.id})"
    return None


def since_gone(facts, strand: str, gen: int, thread: str, threads: dict[str, str],
               install: str) -> str:
    rows = list(facts)
    cut, pred = _cut(rows, strand, gen, thread)
    if cut < 0:
        return ""
    lines: list[tuple[int, str]] = []
    if pred is not None and (ended := _ended(rows, pred)) is not None:
        lines.append(ended)
    after = list(enumerate(rows))[cut + 1:]
    starts = [(i, f) for i, f in after if f.kind == "loom.started"]
    if starts:
        i, first = starts[0]
        commits = list(dict.fromkeys(str(f.data.get("commit") or "?")[:8] for _, f in starts))
        text = f"the loom started {len(starts)} time{'s' * (len(starts) > 1)} (at {', '.join(commits)})"
        gap = _seconds(first.at) - _seconds(rows[i - 1].at) if i > 0 else 0
        if gap > SILENT:
            text += f"; the ledger was silent {_span(gap)} before the first (a power-off or a stopped loop)"
        lines.append((i, f"{text} ({first.id})"))
    letters = {str(f.data.get("id") or f.id): f for f in rows if f.kind == "letter"}
    recovered = {f.id.replace("loom.recovered:", "loom.degraded:", 1): f
                 for _, f in after if f.kind == "loom.recovered"}
    for i, f in after:
        data = f.data or {}
        if f.kind in ROOM_KINDS and data.get("strand") == strand:
            if f.kind == "room.seeded" and data.get("source") == f"strand/{data.get('parent')}":
                continue
            rest = {k: v for k, v in data.items() if k not in ("strand", "gen")}
            lines.append((i, f"{f.kind}: {_brief(rest)} ({f.id})"))
        elif f.kind == "note" and (f.by.startswith("person:") or not (
                f.by == f"loom:{install}" or f.by.startswith("strand:"))):
            lines.append((i, f"note by {f.by}: {_brief(data.get('why') or data.get('body') or data)} ({f.id})"))
        elif f.kind in ("attempt.ended", "letter.disposed"):
            for d in data.get("dispositions") or ():
                letter = letters.get(str(d.get("id")))
                if ("bounced" in (d.get("outcome"), d.get("reason")) and letter is not None
                        and threads.get(str(letter.data.get("from"))) == thread):
                    lines.append((i, f"bounced: your letter {d.get('id')} ({f.id})"))
        elif f.kind == "loom.degraded" and f.id in recovered:
            back = recovered[f.id]
            lines.append((i, f"{data.get('step')} degraded at {data.get('since')} and recovered after "
                             f"{back.data.get('count')} failures, the last {_brief(back.data.get('last'), 80)} ({back.id})"))
    for f in open_streaks(rows, install):
        data = f.data
        lines.append((len(rows), f"degraded now: {data.get('step')} since {data.get('since')}: "
                                 f"{_brief(data.get('error'), 120)} at {data.get('where')} ({f.id})"))
    if not lines:
        return ""
    body = [text for _, text in sorted(lines, key=lambda row: row[0])]
    dropped = 0
    while body and len("\n".join([HEADER, f"({dropped} earlier lines dropped)", *body]).encode()) > CAP:
        body.pop(0)
        dropped += 1
    head = [HEADER] + ([f"({dropped} earlier lines dropped)"] if dropped else [])
    return "\n".join(head + [f"- {text}" for text in body]) + "\n"
