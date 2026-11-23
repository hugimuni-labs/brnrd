"""What a molt cost, measured on the wake it opened, at that wake's first act.

``molt.cost`` is written once, when the new context's first act moves something.
The wake after that opens with the last reading's line. Tokens, the carry's
bytes and the bytes read before the act are null where they are not known:
an unknown is said, never counted as zero.

The bytes read before the first act are ``tooltrace.reads``'s ``before_bytes``,
the same number ``loom._reads`` stores on ``attempt.ended`` and the gauge
already sums. This module copies that number. It does not count the trace again.

The carry is the text the molt recorded (``reincarnate`` data ``carry``).
#2816 stores the file's text there, so a string is that text, even when it
names a file in the room. Its bytes are the utf-8 length. The file is not
opened. A non-negative int is a size already counted. Absent is unknown.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon2.facts import Fact

from .bodies import fact_for
from .home import home_of_room
from .ledger import LedgerConflict, append, kind_facts, read_facts


def count(n: int, singular: str) -> str:
    return f"{n} {singular if n == 1 else singular + 's'}"


def bytes_of(n: int) -> str:
    if abs(n) >= 1000:
        return f"{n / 1000:.1f} KB"
    return f"{n} B"


def _wait_key(read: dict) -> tuple:
    on = (read.get("wait") or {}).get("on")
    return ("on", on) if on else ("prose", (read.get("reading") or "").strip())


def set_again(
    facts, thread: str, old_strand: str, new_strand: str, started_at: str, ended_at: str | None = None
) -> tuple[list[str], list[str]]:
    """Wish ids the new context parked or asked with the same wait the old one had live.

    A park is a ``waiting:`` read. An ask is one whose reading starts ``waiting: ask``.
    The same wish with a different ``wait.on`` is a new wait, not one set again.
    """
    from .wishes import wishes

    old_by, new_by = f"strand:{old_strand}", f"strand:{new_strand}"
    parks, asks = [], []
    for wish in wishes(facts).values():
        if wish.thread != thread:
            continue
        before = [
            r
            for r in wish.reads
            if r.get("at", "") < started_at and r.get("writer") == old_by
        ]
        if not before:
            continue
        last = before[-1]
        if not str(last.get("reading") or "").startswith("waiting:") or not last.get(
            "wait"
        ):
            continue
        if any(
            r.get("at", "") > last["at"] and r.get("at", "") < started_at
            for r in wish.results
        ):
            continue
        after = [
            r
            for r in wish.reads
            if r.get("at", "") >= started_at
            and (ended_at is None or r.get("at", "") <= ended_at)
            and r.get("writer") == new_by
            and str(r.get("reading") or "").startswith("waiting:")
            and r.get("wait")
        ]
        if not after or _wait_key(after[0]) != _wait_key(last):
            continue
        (
            asks if str(after[0]["reading"]).startswith("waiting: ask ") else parks
        ).append(wish.id)
    return parks, asks


def repeated(room: Path, boot: float) -> list[str]:
    """Scars shown in this context whose program was blocked again, since ``boot``.

    ``last blocked`` from before this context is a warning, not a repeated mistake.
    A block counts when it happened here and the scar that fired names it.
    """
    from . import notes

    blocked = dict(notes.blocked_since(room, boot))
    if not blocked:
        return []
    try:
        seen = json.loads((Path(room) / "port" / "notes-shown.json").read_text())
        shown = set(seen.get("shown") or [])
    except (OSError, ValueError, TypeError):
        return []
    found = []
    for note in notes.load(room):
        if note.path not in shown or not str(note.path).startswith("memory/scars/"):
            continue
        names = {p for p in note.phrases} | {
            p.split()[0] for p in note.phrases if p.split()
        }
        keys = sorted(names & set(blocked))
        if not keys:
            continue
        try:
            text = "\n".join(notes.scar_text(room, note.path))
        except Exception:  # noqa: BLE001 - the head and the trigger still name it
            text = ""
        named = (note.head + "\n" + text).casefold()
        if any(blocked[key] and blocked[key].casefold() in named for key in keys):
            found.append(note.path)
    return found


def carry_bytes(room: Path, molt: Fact) -> int | None:
    """Bytes of the text recorded in ``carry``. The file is not opened.

    A string is that text, including one that names a file under ``room``.
    A non-negative int is a size already counted. Absent is unknown.
    """
    # ``room`` stays in the signature so a path-shaped carry can be tested against
    # a real file there. Resolving it would count the wrong bytes.
    if molt.kind != "reincarnate":
        return None
    raw = molt.data.get("carry")
    if isinstance(raw, bool) or raw is None or raw == "":
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, str):
        return len(raw.encode("utf-8"))
    return None


def line(data: dict) -> str:
    """One line for the last molt. A null reading is said."""
    from .spend import format_tokens

    tokens = data.get("tokens")
    token = (
        f"{format_tokens(tokens)} to first act"
        if isinstance(tokens, int)
        else "tokens unknown"
    )
    reset = data.get("reset") or {}
    carry = data.get("carry_bytes")
    read = data.get("read_bytes")
    return (
        f"last molt: {token} · {count(len(reset.get('parks') or []), 'park')} set again · "
        f"{count(len(reset.get('asks') or []), 'ask')} set again · "
        f"{count(len(data.get('repeated') or []), 'scar')} again · "
        f"carry {bytes_of(carry) if isinstance(carry, int) else 'unknown'} against "
        f"{bytes_of(read) if isinstance(read, int) else 'unknown'} read before the first act"
    )


def wake_line(facts, thread: str) -> str:
    """The latest ``molt.cost`` for ``thread``, or ``""`` when none has been written."""
    rows = [f for f in kind_facts(facts, "molt.cost") if f.data.get("thread") == thread]
    if not rows:
        return ""
    latest = max(rows, key=lambda f: (int(f.data.get("gen") or 0), f.at))
    return line(latest.data)


def clause(
    molts: int,
    tokens: list[int],
    parks: int,
    asks: int,
    scars: int,
    carry: list[int],
    read: list[int],
) -> str:
    """The gauge's molt clause. No reading yet is unknown, not a zero."""
    from .spend import format_tokens

    if not molts:
        return "molt cost unknown (no molt.cost yet)"
    if tokens and len(tokens) == molts:
        token = f"{format_tokens(sum(tokens))} to first act"
    elif tokens:
        token = (
            f"{format_tokens(sum(tokens))} to first act ({molts - len(tokens)} unknown)"
        )
    else:
        token = "tokens unknown"
    if carry and read:
        against = f"carry {bytes_of(sum(carry))} against {bytes_of(sum(read))} read"
        missing = []
        if len(carry) != molts:
            missing.append(f"{molts - len(carry)} carry unknown")
        if len(read) != molts:
            missing.append(f"{molts - len(read)} read unknown")
        if missing:
            against += f" ({', '.join(missing)})"
    elif read:
        against = f"carry unknown against {bytes_of(sum(read))} read"
    else:
        against = "carry unknown against read unknown"
    return (
        f"{count(molts, 'molt')} · {token} · {count(parks, 'park')} set again · "
        f"{count(asks, 'ask')} set again · {count(scars, 'scar')} again · {against}"
    )


def _before(room: Path, started: Fact) -> dict | None:
    """``tooltrace.reads`` from this gen's boot: the same call ``loom._reads`` makes."""
    from . import tooltrace

    try:
        return tooltrace.reads(room, datetime.fromisoformat(started.at).timestamp())
    except (TypeError, ValueError, OSError):
        return None


def _place(room: Path, facts) -> tuple[str, int, str] | None:
    strand, gen = room.name, None
    path = room / "port" / "in" / "boundary.md"
    try:
        text = path.read_text()
    except OSError:
        text = ""
    if text.strip():
        try:
            from .port import parse_boundary

            parsed = parse_boundary(text)
        except Exception:  # noqa: BLE001 - a corrupt boundary is not a place to guess
            parsed = None
        if parsed is not None and parsed.strand:
            strand, gen = parsed.strand, parsed.gen
    if gen is None:
        leases = [
            f for f in kind_facts(facts, "lease") if f.data.get("strand") == room.name
        ]
        if not leases:
            return None
        lease = max(leases, key=lambda f: int(f.data.get("gen") or 0))
        thread = str(lease.data.get("thread") or "")
        return (room.name, int(lease.data["gen"]), thread) if thread else None
    lease = fact_for(facts, "lease", strand, gen)
    thread = str(lease.data.get("thread") or "") if lease else ""
    return (strand, gen, thread) if thread else None


def opened_by_molt(facts, thread: str, gen: int) -> tuple[Fact, str] | None:
    """The molt that opened this gen: the previous end was a reincarnation, not a crash."""
    ends = [
        f
        for f in kind_facts(facts, "attempt.ended")
        if f.data.get("thread") == thread and int(f.data.get("gen") or -1) < gen
    ]
    if not ends:
        return None
    prev = max(ends, key=lambda f: int(f.data["gen"]))
    if prev.data.get("cause") != "reincarnated":
        return None
    old = str(prev.data.get("strand") or "")
    molts = [
        f
        for f in kind_facts(facts, "reincarnate")
        if f.data.get("strand") == old
        and int(f.data.get("gen") or -1) == int(prev.data["gen"])
    ]
    return (molts[-1] if molts else prev), old


def _observe(room: Path, facts=None, *, place=None, reads=None) -> None:
    from .jack import _set_state, _state
    from .spend import body_spent
    from .tooltrace import tokens_through

    if _state(room).get("molt_cost"):
        return
    home = home_of_room(room)
    facts = read_facts(home) if facts is None else facts
    place = place or _place(room, facts)
    if place is None:
        return
    strand, gen, thread = place
    started = fact_for(facts, "body.started", strand, gen)
    if started is None:
        return
    reads = _before(room, started) if reads is None else reads
    if not reads or reads.get("first_s") is None:
        return
    opened = opened_by_molt(facts, thread, gen)
    if opened is None:
        _set_state(room, molt_cost="skip")
        return
    molt, old = opened
    lease = fact_for(facts, "lease", strand, gen)
    shell = str((lease.data.get("shell") if lease else "") or "")
    boot = datetime.fromisoformat(started.at).timestamp()
    first, end = reads["first_at"], reads["first_end"]
    at = datetime.fromtimestamp(end, timezone.utc).isoformat()
    parks, asks = set_again(facts, thread, old, strand, started.at, at)
    if shell in {"codex", "claude"}:
        spent = tokens_through(room, shell, boot, first, end)
    else:
        spent, _ = body_spent(room, shell) if reads["before"] + 1 == reads["calls"] else (None, None)
    read = reads.get("before_bytes")
    try:
        append(
            home,
            Fact(
                kind="molt.cost",
                by=f"loom:{home.install_id()}",
                id=f"molt.cost:{strand}:{gen}",
                data={
                    # ``opened``, not ``molt``: ledger.renamed turns a data key named molt into reincarnate.
                    "thread": thread,
                    "strand": strand,
                    "gen": gen,
                    "opened": molt.id,
                    "tokens": spent if isinstance(spent, int) else None,
                    "reset": {"parks": parks, "asks": asks},
                    "repeated": repeated(room, boot),
                    "carry_bytes": carry_bytes(room, molt),
                    "read_bytes": read if isinstance(read, int) else None,
                },
            ),
        )
    except LedgerConflict:
        pass
    _set_state(room, molt_cost="done")


def observe_hand(room: Path, facts, strand: str, gen: int, thread: str, reads=None) -> None:
    """Codex has a rollout trace in place of tool hooks."""
    try:
        if opened_by_molt(facts, thread, gen) is not None:
            _observe(room, facts, place=(strand, gen, thread), reads=reads)
    except Exception:  # noqa: BLE001 - a reading never costs the body
        return


def observe(room: Path, stdin_text: str) -> None:
    """At a producing call: write ``molt.cost`` once, if this wake followed a molt.

    A failure costs the reading, never the call.
    """
    from . import tooltrace

    try:
        payload = json.loads(stdin_text) if stdin_text and stdin_text.strip() else {}
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    if not tooltrace.produces(
        str(payload.get("tool_name") or ""), payload.get("tool_input")
    ):
        return
    try:
        _observe(Path(room))
    except Exception:  # noqa: BLE001 - the hook fails open
        return
