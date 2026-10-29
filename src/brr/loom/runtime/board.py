"""The board: one view model, computed in the loom, shipped in parts (self threads/web-main design §D1, §D2, §D7).

``compute`` derives every part from what the loom already holds: ``index`` (threads with their board
state, calls and steps, wishes, presence, fuel, every other part's hash), one ``thread/<id>`` per
thread (its newest letters and its strands' tool tape) and ``beads`` (each item's parent and a label,
so a client walks chains itself). ``Parts`` recomputes them on its own thread whenever the ledger or
a room's trace moves; ``serve`` is the loopback shell over it on 127.0.0.1 (config ``board_port``), which also
serves the SvelteKit build, so the board needs no cloud hop and no dev server.
The cloud PUT of parts stays off until brnrd.dev stores them.
"""

from __future__ import annotations

import gzip
import hashlib
from collections import Counter
import json
import mimetypes
import os
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .dispositions import letter_index
from .config import read_array
from .connector import instances
from .home import Home, atomic_write, channel_parts, mint, thread_of
from .port import _front
from .lifecycle import CLOSED, main_snapshot
from .project import fold, is_notice, projection_cache, sender_threads
from .snapshot import _iso, assemble
from .wishes import Say, receipt, told as told_of, waits_on, wishes
from . import tooltrace
from brr import emotes

TICK = 0.25  # seconds between stats of the ledger and the rooms; a still home costs a few stats
TOOLS = 120  # tool lines kept per strand
LETTERS = 200  # letters per thread part; older ones by ?before=
LABEL = 80  # characters of a bead's label
PLAN_CAP = 2048  # the inbox README's own rule for its Plan; the loom enforces no cap


def _person_side(sender: str) -> bool:
    return sender.startswith(("person:", "p-", "stranger:"))


def _say(raw) -> tuple:
    """``(letter, offset, size, quote)``, whichever shape the loom holds a say in."""
    say = Say.read(raw)
    return say.letter, say.offset, say.size, say.quote



def plan_bytes(self_root: Path, thread: str) -> int | None:
    """Bytes of a thread's ``## Plan`` section, the part of its README every wake carries."""
    try:
        text = (self_root / "threads" / thread / "README.md").read_text()
    except OSError:
        return None
    found = re.search(r"^## Plan\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return len(found.group(1).encode()) if found else None


STEP_RE = re.compile(r"\s·\s*re:\s*(.*)$")


def steps_of(self_root: Path, thread: str) -> list[dict]:
    """A thread's steps (6lozd): the top-level rows of its ``## Plan``, each with the wishes its ``· re:`` tail cites.
    A row is done as the loom's ``receipt`` reads it: a PR or a sha, or an ``[x]`` box; a file link alone is a mention."""
    try:
        text = (self_root / "threads" / thread / "README.md").read_text()
    except OSError:
        return []
    found = re.search(r"^## Plan\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    out = []
    for row in re.findall(r"^- (.+)$", found.group(1) if found else "", re.M):
        open_box = row.startswith("[ ]")  # one-progress: an open box is never a receipt, whatever it mentions
        box = False if open_box else (True if re.match(r"^\[[xX]\]", row) else None)
        row = re.sub(r"^\[[ xX]\]\s*", "", row)
        tail = STEP_RE.search(row)
        cites = re.split(r",\s*", tail.group(1).strip()) if tail else []
        body = row[:tail.start()] if tail else row
        got = None if open_box else receipt(thread, body, box)
        rc = got[1] if got else None
        out.append({"text": body.strip(), "wishes": [c for c in cites if c.startswith("w-")], "receipt": rc})
    return out


def verdict_letters(facts) -> tuple[dict, dict]:
    """``(raised, said)``: when each wish was last raised (its last ``wish`` fact), and his latest verdict
    letter per wish (a person letter carrying ``wish`` and ``verdict`` agree|correct), both read from the ledger."""
    raised, said = {}, {}
    for f in facts:
        d = f.data or {}
        if f.kind == "wish":
            raised[d.get("wish")] = f.at
        elif (f.kind == "letter" and d.get("wish") and d.get("verdict") in {"agree", "correct"}
              and _person_side(str(d.get("from", "")))):
            said[str(d["wish"])] = {"verdict": d["verdict"], "letter": str(d.get("id") or f.id), "at": f.at}
    return raised, said


def calls_of(threads: list[dict], told: dict, facts=()) -> dict[str, list[dict]]:
    """Each thread's calls: the one action each wish waiting on him names (your-call.md, 4uwjt agreed in 4886).

    ask (red): an open ask whose wait reaches a person (waits_on, the loom's own rule); its line is
    the question. verdict (yellow): a delivered wish with no verdict, on the open thread opened for it,
    else the open thread it was said on, else the inbox; its last delivered result is the link.
    Either is ``closed`` once his verdict letter for the wish lands after it was raised (your-call.md, 4907):
    the card collapses, and the loom's own verdict stays the resident's to stamp.
    """
    raised, said = verdict_letters(facts)
    main = {t["id"]: {"opened-by": t.get("opened_by"), "status": t.get("status")} for t in threads}
    live = {t["id"] for t in threads if t.get("status") not in CLOSED}
    opened = {}
    for t in threads:
        for o in t.get("opened_for") or []:
            opened.setdefault(o["wish"], t["id"])
    calls: dict[str, list[dict]] = {}
    for ident, head in waits_on(told, main).items():
        w = told[ident]
        if not head.startswith("thread:") and w.state != "delivered":
            calls.setdefault(w.thread, []).append({"kind": "ask", "wish": ident, "line": w.line, "waits": head,
                                                   "at": _iso(raised.get(ident, w.at)), "raised": raised.get(ident, w.at)})
    for w in told.values():
        if w.state != "delivered":
            continue
        home = next((t for t in (opened.get(w.id), w.thread) if t in live), "inbox")
        result = [r for r in w.results if r.get("delivered")][-1]
        calls.setdefault(home, []).append({"kind": "verdict", "wish": w.id, "line": w.line, "ref": result.get("ref"),
                                           "result": result.get("line"), "at": _iso(result["at"]), "raised": result["at"]})
    for c in (c for cs in calls.values() for c in cs):
        v, at = said.get(c["wish"]), c.pop("raised")
        if v and v["at"] > at:
            c["closed"] = {"verdict": v["verdict"], "letter": v["letter"], "at": _iso(v["at"])}
    return calls


def board_state(t: dict, calls: list[dict], left: int = 0, end: tuple | None = None,
                parked: str | None = None, person: str | None = None, parked_at: str | None = None,
                parked_wish: str | None = None) -> dict:
    """A column's state from one rule table (your-call.md), first match wins; the page only renders it.

    settled: status is closed. seated: a held strand works or holds, or the turn is mine (a body or the
    loom is on owed letters). your call: a call names his action (red when one is an ask). mine: the
    resident owes a move the loom will not make (a parked or failed strand, a letter owed by the
    operator, or an open thread with work left and no body that declared its end: wake or settle).
    waiting: work left, and the last body declared why it stopped (a `waiting:` read, or a paced/stopped end).
    quiet: nothing left (4987; named apart from the strand end cause `idle`, icjy2: a thread greys only when no wish on it is open).
    ``since`` orders your call by when the thread entered it; ``why`` names the move a mine column waits on.
    ``left`` counts its open wishes not yet delivered; ``end`` is the last body's (cause, at); ``parked`` is the
    newest resident `waiting:` read on one of them, ``parked_at`` when it was written: a read that names
    ``person`` waits on him, so the column is his call and reads so (9mswfh0474 4): an ask call on
    ``parked_wish`` with the read as its line, the one block every call renders as (5092).
    """
    if t.get("status") in CLOSED:
        return {"state": "settled"}
    held = next((s for s in reversed(t.get("strands") or []) if s.get("state") in {"working", "holding"}), None)
    shown = {"calls": calls} if calls else {}  # closed calls still render, collapsed, in any column
    calls = [c for c in calls if not c.get("closed")]
    # Whose move is apart from motion (4909): open calls badge the column, seated or not.
    calling = {"open": len(calls), "red": any(c["kind"] == "ask" for c in calls)} if calls else {}
    if held or t.get("turn") == "mine":
        word = held["state"] if held else (t.get("strands") or [{}])[-1].get("state") or "starting"
        return {"state": "seated", "strand": word, **shown, **calling}
    if calls:
        return {"state": "your-call", **calling, **shown, "since": min(c["at"] for c in calls)}
    last = (t.get("strands") or [{}])[-1].get("state")
    if t.get("turn") == "blocked" or last in {"parked", "failed"}:
        return {"state": "mine", "why": f"{last or 'strand'}: {t.get('blocked_on') or 'restart'}", **shown}
    answered = [c["wish"] for c in shown.get("calls", []) if c["kind"] == "ask"]
    if answered:  # his done is on record and the ask is still open: accepting it is the thread's move
        return {"state": "mine", "why": f"his done is in: wish {answered[0]} --accept", **shown}
    if t.get("turn") == "yours":  # no ask reaches him, so the operator owns a letter here
        return {"state": "mine", "why": f"{t.get('owed') or 'a'} letter(s) owed by the operator: retry or attention --note", **shown}
    if not left:
        return {"state": "quiet", **shown}
    if parked and person and re.search(rf"\b{re.escape(person)}\b", parked, re.I):
        ask = {"kind": "ask", "wish": parked_wish, "line": parked, "waits": person, "at": parked_at or ""}
        return {"state": "your-call", "waits": True, "open": 1, "red": True,
                "calls": [*shown.get("calls", []), ask], "since": parked_at or ""}
    if parked:
        return {"state": "waiting", "why": parked, **shown}
    cause, at = end or (None, None)
    if cause in {"paced", "stopped"}:
        return {"state": "waiting", "why": f"{cause} {at[:16].replace('T', ' ')}", **shown}
    ended = f"ended {cause} {at[:16].replace('T', ' ')}" if cause else "no body yet"
    return {"state": "mine", "stall": True,
            "why": f"no body, {left} open wish{'es' if left > 1 else ''} ({ended}): wake or settle", **shown}


DONE = {"delivered", "accepted", "dropped", "declined", "expired", "answered"}


def thread_marks(threads: list[dict], told: dict, unanswered: dict[str, tuple[str, ...]]) -> dict[str, dict]:
    """Two marks on a column, from stamps already on record; neither is a state (9mswfh0474 3, 6).

    ready: every wish on the thread or opened for it is delivered or past it (the settle candidate).
    unanswered: the loom's own list (owners.unanswered_by_thread, the one the wake and status line read):
    his letters with no delivered reply on his channel, placed on each thread whose Plan cites them (8u0ru).
    """
    marks: dict[str, dict] = {}
    for t in threads:
        mine = {o["wish"] for o in t.get("opened_for") or []}
        states = [w.state for w in told.values() if w.thread == t["id"] or w.id in mine]
        if states and all(s in DONE for s in states):
            marks.setdefault(t["id"], {})["ready"] = True
    for thread, ids in unanswered.items():
        if ids:
            marks.setdefault(thread, {})["unanswered"] = list(ids)
    return marks


def letters_of(facts, typed: dict | None = None, told: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Every accepted letter, full text, with the stamps it has gathered; and every wish with its stamps."""
    state = fold(facts)
    writers = sender_threads(facts)
    shown: dict[str, list] = {}
    sent: dict[str, dict] = {}
    for f in facts:
        if f.kind == "shown":
            for ident in f.data.get("ids") or []:
                seen = shown.setdefault(str(ident), [])
                if all(s["strand"] != f.data.get("strand") for s in seen):  # first sight per strand
                    seen.append({"strand": f.data.get("strand"), "at": _iso(f.at)})
        elif f.kind == "speech" and f.by.startswith("loom:"):
            sent[str(f.data.get("key"))] = {"state": f.data.get("state"), "at": _iso(f.at)}
    told = told if told is not None else wishes([f for f in facts if f.kind.startswith("wish") or f.kind == "letter"])
    index = letter_index(facts)
    heard = {}
    for f in facts:
        if f.kind == "wish":
            heard.setdefault(f.data["wish"], f.at)
    answered: dict[str, list] = {}
    every = []
    for w in told.values():
        stamps = [{"stamp": "heard", "at": _iso(heard.get(w.id, w.at))}]
        if w.origin == "derived":
            stamps.append({"stamp": "derived", "at": _iso(heard.get(w.id, w.at))})
        stamps += [{"stamp": "read", "at": _iso(r["at"]), "by": r.get("by")} for r in w.reads]
        stamps += [{"stamp": "delivered" if r.get("delivered") else "result", "at": _iso(r["at"]),
                    "ref": r.get("ref"), "line": r.get("line")} for r in w.results]
        if w.verdict:
            stamps.append({"stamp": w.verdict["as"], "at": _iso(w.verdict["at"])})
        entry = {"wish": w.id, "line": w.line, "state": w.state, "origin": w.origin,
                 "thread": w.thread, "stamps": stamps}
        # Each say as the loom placed it: a span, the whole letter, or a quote it could not find in the letter.
        says = []
        for ident, offset, size, quote in map(_say, w.says):
            body = str(index[ident].data.get("body") or "") if ident in index else ""
            if quote is not None:
                says.append({"id": ident, "quote": quote, "cites": "unplaced"})
            elif size is None:
                says.append({"id": ident, "quote": body[offset:], "cites": "whole"})
            else:
                says.append({"id": ident, "quote": body[offset:offset + size], "cites": "span"})
        every.append(dict(entry, at=_iso(heard.get(w.id, w.at)), says=says, sender=w.sender,
                          results=[{k: _iso(v) if k == "at" else v for k, v in r.items()
                                    if k in {"kind", "ref", "line", "delivered", "at"}} for r in w.results],
                          verdict={k: _iso(v) if k == "at" else v for k, v in w.verdict.items()
                                   if k in {"as", "evidence", "ref", "at"}} if w.verdict else None))
        for ident, *_ in map(_say, w.says):
            answered.setdefault(ident, []).append(entry)
    # Chatter: letters a reply or note cites as wanting nothing (send --chatter).
    chattered: dict[str, list] = {}
    for f in facts:
        if f.kind in {"letter", "note"}:
            for ident in f.data.get("chatter") or ():
                chattered.setdefault(str(ident), []).append(str(f.data.get("id") or f.id))
    rows = []
    for f in state.accepted:
        if f.kind != "letter":
            continue
        d = f.data
        ident = str(d.get("id") or f.id)
        strand = f.by.removeprefix("strand:") if f.by.startswith("strand:") else None
        # A letter sits in the column it was written on and the one it went to.
        threads = [writers.get(strand)] if strand else []
        try:
            threads.append(thread_of(d.get("to")))
        except (ValueError, TypeError):
            pass
        threads = list(dict.fromkeys(t for t in threads if t))
        sender = str(d.get("from") or strand or f.by)
        row = {"id": ident, "at": _iso(f.at), "from": sender, "to": d.get("to"), "threads": threads,
               "person": _person_side(sender), "notice": is_notice(f), "body": str(d.get("body") or ""),
               "re": d.get("re"), "wish": d.get("wish"), "shown": shown.get(ident),
               "sent": sent.get(ident), "wishes": answered.get(ident), "chatter": chattered.get(ident),
               "open": _person_side(sender) and not (answered.get(ident) or chattered.get(ident)),
               "typed_by": (typed or {}).get(ident)}
        rows.append({k: v for k, v in row.items() if v not in (None, "", [], False)})
    # What answers a letter: the letters whose re names it (replies, routes, notes).
    answers: dict[str, list] = {}
    for row in rows:
        if row.get("re"):
            answers.setdefault(str(row["re"]), []).append(row["id"])
    for row in rows:
        if row["id"] in answers:
            row["answers"] = answers[row["id"]]
    # A wish's letters: the ones its says cite, then every letter whose re reaches one of them, in time order.
    # Walked over a reply map, not rows × wishes: a reply joins when it comes after what it answers,
    # and a cited letter takes its replies whenever they came.
    place = {row["id"]: n for n, row in enumerate(rows)}
    replies: dict[str, list] = {}
    for row in rows:
        if re_ := str(row.get("re") or ""):
            replies.setdefault(re_ if re_ in place else f"letter:{re_}", []).append(row["id"])
            if re_ not in place:
                replies.setdefault(re_, []).append(row["id"])
    for w in every:
        tied = {s["id"]: -1 for s in w["says"]}
        todo = list(tied)
        while todo:
            ident = todo.pop()
            for child in replies.get(ident, ()):
                if child not in tied and place[child] > tied[ident]:
                    tied[child] = place[child]
                    todo.append(child)
        w["letters"] = sorted((i for i in tied if i in place), key=place.__getitem__)
    return rows, every


def board_threads(home: Home, facts, config, person: str | None = None, typed: dict | None = None) -> tuple[dict, dict, list, dict]:
    """``(snapshot, told, letters, wish rows)``: snapshot v1 with each thread's board state, steps and opening."""
    raw = told_of(facts)  # the projection the beads read too: one union of the wish facts per change
    snap = assemble(home, facts, config, told=raw)
    root = home.root / "self"
    main_name = home.main_thread()
    for t in snap.get("threads") or []:
        t["steps"] = steps_of(root, t["id"])
        if t.get("plan") and (size := plan_bytes(root, t["id"])) is not None:
            t["plan"] = dict(t["plan"], bytes=size, **({"cap": PLAN_CAP} if t["id"] == main_name else {}))
    letters, told = letters_of(facts, typed, raw)
    calls = calls_of(snap.get("threads") or [], raw, facts)
    opened = {}  # a thread opens with its first letter or lease, whichever came first: idle keeps that place (4921)
    ends = {}  # strand → its last body's (cause, at)
    for f in facts:
        name = f.data.get("thread") if f.kind == "lease" else None
        if f.kind == "letter":
            try:
                name = thread_of(f.data.get("to"))
            except (ValueError, TypeError):
                name = None
        if name and (name not in opened or f.at < opened[name]):
            opened[name] = f.at
        if f.kind == "attempt.ended":
            ends[f.data.get("strand")] = (f.data.get("cause"), _iso(f.at))
    left, parked = {}, {}
    for w in raw.values():
        if not w.open or w.state == "delivered":
            continue
        left[w.thread] = left.get(w.thread, 0) + 1
        read = next((r for r in reversed(w.reads) if r.get("by") != "person"), None)
        if read and str(read.get("reading", "")).startswith("waiting:") and _iso(read["at"]) > parked.get(w.thread, ("",))[0]:
            parked[w.thread] = (_iso(read["at"]), read["reading"], w.id)
    from .owners import unanswered_by_thread
    marks = thread_marks(snap.get("threads") or [], raw, unanswered_by_thread(facts, main_snapshot(home)))
    for t in snap.get("threads") or []:
        last = ((t.get("strands") or [{}])[-1]).get("id")
        at, why, wid = parked.get(t["id"], (None, None, None))
        t["board"] = board_state(t, calls.get(t["id"], []), left.get(t["id"], 0), ends.get(last), why, person, at, wid)
        if t["board"]["state"] != "settled":
            t["board"].update(marks.get(t["id"], {}))
        if t["id"] in opened:
            t["opened_at"] = _iso(opened[t["id"]])
    return snap, raw, letters, told


def beads_of(facts, main: dict) -> dict[str, list]:
    """Every letter, wish, strand and thread as ``id → [parent, edge, from, at, label]`` (design §D3)."""
    from .asks import Beads
    beads = Beads(facts, main)
    out: dict[str, list] = {}

    def line(text) -> str:
        return " ".join(str(text or "").split())[:LABEL]
    for ident, f in beads.letters.items():
        sender = "loom" if is_notice(f) else str(f.data.get("from") or f.by)
        out[ident] = [*beads.parent(ident), sender, _iso(f.at), line(f.data.get("body"))]
    for ident, w in beads.wishes.items():
        out[ident] = [*beads.parent(ident), w.sender, _iso(w.at), line(w.line)]
    for strand, thread in beads.writers.items():
        out.setdefault(strand, [*beads.parent(strand), None, None, thread])
    for name, data in main.items():
        out.setdefault(f"thread:{name}", [*beads.parent(f"thread:{name}"), None, None, line(data.get("title"))])
    return out


def digest(part) -> str:
    """A part's hash: sha256 of its canonical JSON, cut to 16 (design §D1)."""
    return hashlib.sha256(json.dumps(part, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


# The rail's face per strand state, the frames a working or resting strand plays (board.html read these off board.py).
FACES = {s: list(emotes.for_telemetry(s).frames) for s in ("idle", "running")}


def compute(home: Home, facts, config, tools: dict, live: dict, person: str | None = None,
            typed: dict | None = None) -> dict[str, dict]:
    """Every part by name: ``index``, ``thread/<id>`` per thread, ``beads``. The index carries the others' hashes."""
    snap, raw, letters, told = board_threads(home, facts, config, person, typed)
    threads = snap.get("threads") or []
    parts: dict[str, dict] = {}
    by_thread: dict[str, list] = {}
    for row in letters:
        for name in row.get("threads") or ():
            by_thread.setdefault(name, []).append(row)
    # A wish row ships in its thread's part; the index keeps counts by state, so a moved wish resends one thread.
    wished: dict[str, list] = {}
    for row in told:
        wished.setdefault(row.get("thread"), []).append(row)
    known = {t["id"] for t in threads}
    counts = {name: dict(Counter(row["state"] for row in rows)) for name, rows in wished.items() if name in known}
    for t in threads:
        mine = by_thread.get(t["id"], [])
        strands = [s["id"] for s in t.get("strands") or ()]
        parts[f"thread/{t['id']}"] = {
            "id": t["id"], "count": len(mine), "letters": mine, "wishes": wished.get(t["id"], []),
            "tools": {s: tools[s] for s in strands if s in tools},
            "live": {s: live[s] for s in strands if s in live}}
    parts["beads"] = beads_of(facts, main_snapshot(home))
    hashes = {name: digest(part) for name, part in parts.items()}
    index = {"v": 1, **{k: v for k, v in snap.items() if k not in {"at", "log"}}, "wishes": counts,
             "unthreaded": [row for name, rows in wished.items() if name not in known for row in rows],
             **_as(home, person), "parts": hashes, "faces": FACES}
    parts["index"] = index
    return parts


def _as(home: Home, person: str | None) -> dict:
    """The index's ``person`` and ``via``: who a letter typed on the board goes out as (the header's "as ... via ...")."""
    try:
        name, instance, user = person_of(home, person)
    except LookupError:
        return {"person": person, "via": None}
    return {"person": name, "via": f"channel:{instance}/{user}"}


def window(part: dict, before: str | None = None, until: str | None = None, most: int = LETTERS) -> dict:
    """A thread part cut to its newest ``most`` letters older than ``before``; ``until`` reaches back to a target."""
    rows = part.get("letters") or []
    ids = [row["id"] for row in rows]
    end = ids.index(before) if before in ids else len(rows)
    start = max(0, end - most)
    if until in ids:
        start = min(start, ids.index(until))
    return dict(part, letters=rows[start:end], more=start > 0)


class Tools:
    """Each room's tape, re-read only when its trace moved: a few stats per room per tick."""

    def __init__(self, home: Home):
        self.home = home
        self.tools, self.live, self.sig = {}, {}, {}

    def read(self) -> bool:
        """Re-read the rooms whose trace moved; True when any did. A codex room's calls come from its body.log."""
        moved, sig = False, {}
        for port in (self.home.root / "rooms").glob("*/port"):
            strand, stamp = port.parent.name, []
            for name in ("tools.jsonl", "body.log", ".paused.json"):
                if name == "body.log" and stamp[0]:
                    stamp.append(None)
                    continue
                try:
                    st = (port / name).stat()
                    stamp.append((st.st_mtime_ns, st.st_size))
                except OSError:
                    stamp.append(None)
            if not (stamp[0] or stamp[1]):
                continue
            sig[strand] = tuple(stamp)
            if self.sig.get(strand) == sig[strand]:
                continue
            rows = tooltrace.read(port.parent, limit=TOOLS)
            self.tools[strand] = [{"at": _iso(r["at"]), "end": r["end"] and _iso(r["end"]), "tool": r["tool"],
                                   "arg": r["arg"], "cmd": r.get("cmd", ""), "say": r.get("say", ""),
                                   "by": r.get("by", ""), "failed": bool(r.get("failed"))} for r in rows]
            now = tooltrace.live(port.parent)
            self.live[strand] = {"open": [{"at": _iso(c["at"]), "tool": c["tool"], "arg": c["arg"],
                                           "cmd": c.get("cmd", ""), "by": c.get("by", "")} for c in now["open"]],
                                 "say": now["say"], "last_end": now["last_end"] and _iso(now["last_end"]),
                                 "paused": bool(now["paused"])}
            moved = True
        for gone in set(self.tools) - set(sig):
            self.tools.pop(gone, None)
            self.live.pop(gone, None)
            moved = True
        self.sig = sig
        return moved


class Parts:
    """The parts, recomputed whole on their own thread when the ledger or a tape moved (design §D7).

    Its own ledger reader and projection cache keep it apart from the loop: the loop never waits on a
    board, and a board never sees a tick's half-written state. ``changed`` wakes the event streams.
    """

    def __init__(self, home: Home, config_of, log=None, person: str | None = None):
        from .ledger import LedgerReader
        self.home, self.config_of, self.log, self.person = home, config_of, log, person
        self.reader, self.cache, self.tape = LedgerReader(), {}, Tools(home)
        self.parts: dict[str, dict] = {}
        self.hashes: dict[str, str] = {}
        self.revision = None
        # Which strand typed a letter through the board: the board's own note, outside the self repo.
        self.typed_path, self.typed, self.typed_mtime = home.root / "loom/board/typed.json", {}, None
        self.changed = threading.Condition()
        self.stop = threading.Event()

    def step(self) -> bool:
        """One recompute when anything moved; True when a part's hash did."""
        facts = self.reader.refresh(self.home)
        moved = self.tape.read() | self._typed()
        if self.reader.revision == self.revision and not moved:
            return False
        self.revision = self.reader.revision
        with projection_cache(self.cache):
            parts = compute(self.home, facts, self.config_of(), self.tape.tools, self.tape.live, self.person,
                            self.typed)
        hashes = {name: digest(part) for name, part in parts.items()}
        if hashes == self.hashes:
            return False
        with self.changed:
            self.parts, self.hashes = parts, hashes
            self.changed.notify_all()
        return True

    def _typed(self) -> bool:
        mtime = self.typed_path.stat().st_mtime if self.typed_path.exists() else None
        if mtime == self.typed_mtime:
            return False
        self.typed, self.typed_mtime = json.loads(self.typed_path.read_text()) if mtime else {}, mtime
        return True

    def send(self, body: str, typed_by: str | None = None, wish: str | None = None,
             verdict: str | None = None) -> dict:
        """One letter as the person into the main thread, dropped where the channel's own letters land.

        ``wish`` ties it to the wish a call names; ``verdict`` is agree or correct, as the relay writes
        them: an agree on a delivered wish is the intake's own acceptance (connector.ingest), a correct
        is the letter itself. A done on an ask goes as agree too; the intake stamps nothing on an ask.
        """
        if not body.strip():
            raise ValueError("empty letter")
        if wish and not re.fullmatch(r"w-[a-z0-9]{6,16}", wish):
            raise ValueError("wish is a wish id")
        if verdict not in (None, "agree", "correct"):
            raise ValueError("verdict is agree or correct")
        if typed_by and not re.fullmatch(r"s-[a-z0-9]{2,8}-[a-z0-9]{4,12}", typed_by):
            raise ValueError("typed_by is a strand id")
        instance, user = person_channel(self.home, self.person)
        lands = self.home.main_thread()
        ident = f"{user}:board-{mint(10)}"  # a chat-scoped native id, as the connector writes
        drop = self.home.root / "loom/connectors" / instance / "state/in"
        drop.mkdir(parents=True, exist_ok=True)
        fields = {"kind": "letter", "id": ident, "to": user, "from": user, "thread": lands,
                  **({"wish": wish} if wish else {}), **({"verdict": verdict} if wish and verdict else {})}
        if typed_by:
            with self.changed:
                typed = json.loads(self.typed_path.read_text()) if self.typed_path.exists() else {}
                typed[f"letter:{instance}:{ident}"] = typed_by
                self.typed_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(self.typed_path, json.dumps(typed))
        atomic_write(drop / f"{ident}.md", _front(fields, body))
        return {"id": f"letter:{instance}:{ident}", "thread": lands}

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.step()
            except Exception as exc:  # a torn read is retried next tick
                if self.log:
                    self.log(self.home, f"board: {type(exc).__name__}: {exc}")
            self.stop.wait(TICK)


class Pusher:
    """The parts whose hash moved, PUT to the cloud (``/v1/daemons/loom/parts``), at most once per ``EVERY`` s.

    The PUT runs on its own thread, one at a time; the cloud answers the hashes it holds, so a restart or a lost
    push resends exactly what it lacks. Batches stay under ``BATCH`` gzip bytes, the index in the last one, so a
    reader the new index wakes finds the threads it names. Until a push succeeds again, each step raises the
    last failure for the loop's guard.
    """

    EVERY, RETRY, BATCH = 3.0, 60.0, 3_000_000

    def __init__(self, parts: Parts):
        self.parts = parts
        self.sent: dict[str, str] = {}
        self.attempted: float | None = None
        self.worker: threading.Thread | None = None
        self.failing: tuple[Exception, object] | None = None

    def step(self, config) -> None:
        if not config.relay_state:
            return
        with self.parts.changed:
            held, hashes = self.parts.parts, self.parts.hashes
        now = time.monotonic()
        busy = self.worker is not None and self.worker.is_alive()
        wait = self.RETRY if self.failing else self.EVERY
        if hashes and hashes != self.sent and not busy and (self.attempted is None or now - self.attempted >= wait):
            self.attempted = now
            self.worker = threading.Thread(target=self._put, args=(Path(config.relay_state), held, hashes),
                                           name="loom-parts", daemon=True)
            self.worker.start()
        if self.failing is not None:
            raise self.failing[0].with_traceback(self.failing[1])

    def batches(self, held: dict, hashes: dict) -> list[bytes]:
        """The gzip bodies of one push: every changed part once, the index last."""
        keys = sorted(hashes)
        moved = sorted((k for k in keys if self.sent.get(k) != hashes[k]), key=lambda k: k == "index")
        bodies, batch, size = [], {}, 0
        for key in moved:
            one = len(gzip.compress(json.dumps(held[key], separators=(",", ":")).encode(), 1))
            if batch and size + one > self.BATCH:
                bodies.append(batch)
                batch, size = {}, 0
            batch[key], size = {"hash": hashes[key], "part": held[key]}, size + one
        bodies.append(batch)
        return [gzip.compress(json.dumps({"keys": keys, "parts": b}, separators=(",", ":")).encode(), 6)
                for b in bodies]

    def _put(self, state_dir: Path, held: dict, hashes: dict) -> None:
        from brr.gates import cloud
        try:
            state = cloud._load_state_from_dir(state_dir)
            if not state.get("brnrd_url") or not state.get("token"):
                raise RuntimeError("cloud state has no URL or token")
            url = state["brnrd_url"].rstrip("/") + "/v1/daemons/loom/parts"
            headers = {"Authorization": f"Bearer {state['token']}", "Content-Type": "application/json",
                       "Content-Encoding": "gzip"}
            for body in self.batches(held, hashes):
                resp = cloud._SESSION.request("PUT", url, data=body, headers=headers, timeout=60)
                if not 200 <= resp.status_code < 300:
                    raise RuntimeError(f"PUT /v1/daemons/loom/parts -> {resp.status_code}: {resp.text[:200]}")
                self.sent = dict(resp.json().get("hashes") or {})
            self.failing = None
        except Exception as exc:
            self.failing = (exc, exc.__traceback__)


def person_channel(home: Home, person: str | None) -> tuple[str, str]:
    """``(instance, user)``: the person's first direct channel on an enabled connector, from people/*/channels.toml."""
    return person_of(home, person)[1:]


def person_of(home: Home, person: str | None) -> tuple[str, str, str]:
    """``(name, instance, user)``: who the board writes as, and the channel it writes through."""
    enabled = instances(home)
    for path in sorted((home.root / "self/people").glob("*/channels.toml")):
        if person and path.parent.name != person:
            continue
        for entry in read_array(path, "channels"):
            instance, _, user = entry.partition(":")
            if instance in enabled and user and ":" not in user:
                channel_parts(f"channel:{instance}/{user}", home)
                return path.parent.name, instance, user
    raise LookupError("no person with a direct channel on an enabled connector")


def spa_root() -> Path | None:
    """The SvelteKit build the loopback serves: ``BRNRD_FRONTEND_DIR`` (as the cloud's ``brnrd.spa``), else the
    checkout's ``src/frontend/build``; ``None`` until ``npm run build`` has written one. Read per request, so a
    rebuild lands without a re-exec."""
    configured = os.environ.get("BRNRD_FRONTEND_DIR")
    root = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[3] / "frontend" / "build"
    return root.resolve() if root.is_dir() else None


def spa_file(root: Path, url_path: str) -> Path | None:
    """What a GET of ``url_path`` reads from the build: the file, its prerendered ``<path>.html`` or ``<path>/index.html``,
    else the ``200.html`` shell (the client router owns the path), but never the shell under ``_app/``, where every
    URL is the build's own and a missing chunk must 404 for SvelteKit's stale-build recovery. Nothing outside ``root``."""
    rel = url_path.lstrip("/")
    candidate = (root / rel).resolve()
    if candidate.is_relative_to(root):
        if candidate.is_file():
            return candidate
        if candidate != root and (sibling := candidate.with_name(candidate.name + ".html")).is_file():
            return sibling
        if (child := candidate / "index.html").is_file():
            return child
    if rel == "_app" or rel.startswith("_app/"):
        return None
    shell = root / "200.html"
    return shell if shell.is_file() else None


def handler(parts: Parts, port: int, spa=spa_root):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{host}" for host in hosts}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, payload, etag: str | None = None):
            if etag and self.headers.get("If-None-Match") == f'"{etag}"':
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header("ETag", f'"{etag}"')
                self.end_headers()
                return
            raw = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(HTTPStatus.OK if etag or "error" not in payload else HTTPStatus.NOT_FOUND)
            self.send_header("Content-Type", "application/json")
            if len(raw) > 4096 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                raw = gzip.compress(raw, 5)
                self.send_header("Content-Encoding", "gzip")
            if etag:
                self.send_header("ETag", f'"{etag}"')
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.headers.get("Host") not in hosts:  # DNS rebinding: only our own loopback names
                self.send_error(HTTPStatus.FORBIDDEN, "host")
                return
            url = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            path = url.path.removeprefix("/v1/loom/")
            with parts.changed:
                held, hashes = parts.parts, parts.hashes
            if url.path == "/v1/loom/events":
                return self._events()
            if path in {"index", "beads"} or path.startswith("thread/"):
                part = held.get(path)
                if part is None:
                    return self._json({"error": f"no part {path}"})
                if path.startswith("thread/") and ("before" in query or "until" in query):
                    return self._json(window(part, query.get("before"), query.get("until")))
                if path.startswith("thread/"):
                    cut = window(part)
                    return self._json(cut, digest(cut))
                return self._json(part, hashes[path])
            if not url.path.startswith("/v1/"):
                return self._file(url.path)
            self.send_error(HTTPStatus.NOT_FOUND)

        def _file(self, url_path: str):
            root = spa()
            found = spa_file(root, url_path) if root else None
            if found is None:
                self.send_error(HTTPStatus.NOT_FOUND, "no SPA build" if root is None else None)
                return
            raw = found.read_bytes()
            kind = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
                    ".json": "application/json", ".svg": "image/svg+xml", ".woff2": "font/woff2"}.get(found.suffix)
            kind = kind or mimetypes.guess_type(found.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", kind + ("; charset=utf-8" if kind.startswith("text/") else ""))
            # hashed chunks never change under one name; every page points at one build's chunks, so it revalidates
            immutable = url_path.startswith("/_app/immutable/")
            self.send_header("Cache-Control", "public, max-age=31536000, immutable" if immutable else "no-cache")
            if len(raw) > 4096 and kind != "font/woff2" and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                raw = gzip.compress(raw, 5)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            # A write needs our own Host (DNS rebinding), our own Origin when a browser sends one, and a
            # JSON body: a foreign page cannot send application/json without a preflight we never answer.
            if self.headers.get("Host") not in hosts or self.headers.get("Origin", next(iter(origins))) not in origins:
                self.send_error(HTTPStatus.FORBIDDEN, "origin")
                return
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                self.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                return
            if self.path != "/v1/loom/letters":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                data = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1 << 20)))
                sent = parts.send(str(data["body"]), data.get("typed_by") or None,
                                  data.get("wish") or None, data.get("verdict") or None)
            except (ValueError, KeyError, TypeError, LookupError) as exc:
                raw = json.dumps({"error": str(exc)}).encode()
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self._json(sent)

        def _events(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b": " + b" " * 2048 + b"\n\n")  # past a proxy's first buffer
            seen = None
            try:
                while not parts.stop.is_set():
                    with parts.changed:
                        if parts.hashes.get("index") == seen:
                            parts.changed.wait(15)
                        current = parts.hashes.get("index")
                    if current and current != seen:
                        seen = current
                        self.wfile.write(f"event: index\ndata: {current}\n\n".encode())
                    else:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def serve(parts: Parts, port: int, spa=spa_root) -> ThreadingHTTPServer:
    """The loopback shell: GET ``/v1/loom/{index,thread/<id>,beads,events}``, POST ``/v1/loom/letters``, and every
    other GET outside ``/v1/`` from the SPA build ``spa()`` names (its ``/`` is the board), on 127.0.0.1."""
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(parts, port, spa))
    server.daemon_threads = True
    if port == 0:  # a test's free port: the Host check needs the bound one
        server.RequestHandlerClass = handler(parts, server.server_address[1], spa)
    threading.Thread(target=server.serve_forever, name="board-http", daemon=True).start()
    return server
