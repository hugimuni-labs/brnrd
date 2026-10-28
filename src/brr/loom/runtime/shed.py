"""Shedding: the stop asks a body to shed when its scroll is mostly ceremony,
and ``shed`` refuses a README its successor can't boot into.

The design is the self's threads/handover/design.md §4. The jack keeps a tally
in jack-state.json: one ``[acted, context]`` per turn since the last self
landing. A turn acted when this strand wrote a fact beyond its own settlement
(not a note, a park or a shown mark) or moved a clone's HEAD. Every turn
re-reads the whole scroll, so a body whose turns mostly act on nothing pays
for its size on ceremony; that, not its size, is what makes a shed due.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .home import home_of_room

#: Share of turns since the last landing that acted on nothing.
NOISE = 0.6
#: Turns a share is taken over; fewer say nothing.
TURNS = 3
#: The context window the backstop is a share of. Auto-compaction near it
#: loses more than a shed does, so the backstop is due at any noise share.
WINDOW = 1_000_000
BACKSTOP = 0.6
#: Facts that settle a turn rather than act in it.
SETTLING = {"shown", "note", "wish.read", "left.asked", "molt"}
#: Quota windows a pace is read from, by the gauge's names.
SPANS = {"session": 5 * 3600, "week": 7 * 86400}

ASK = """shed due: {bars}
pre-shed: read threads/{thread}/README.md as the body that will boot into it.
  Rewrite it to the current state: a `## Now` block naming work in flight (open PRs), done rows cut
  or moved to a topic, decisions with their why. Then:
- brnrd runtime shed --why "<one line pointing at the README>"   (then end the turn; the process ends itself)
- or once, with a reason: brnrd runtime shed --decline "<why not now>\""""

AGAIN = """shed due again: {bars}
declined once at {at}Z ({why}); this one can't be declined.
pre-shed: read threads/{thread}/README.md as its successor, rewrite it to the current state, then
- brnrd runtime shed --why "<one line pointing at the README>"   (then end the turn; the process ends itself)"""


def contexts(stdin_text: str, boot: int | None = None) -> tuple[int, int]:
    """``(boot, now)``: the context the transcript began and ends with, 0 when unread."""
    try:
        path = Path(json.loads(stdin_text).get("transcript_path") or "")
        size = path.stat().st_size
        with path.open("rb") as handle:
            if boot is None:
                boot = _context(handle.read(400_000).decode(errors="replace").splitlines())
            handle.seek(max(size - 200_000, 0))
            tail = handle.read().decode(errors="replace").splitlines()
    except (OSError, ValueError, AttributeError, TypeError):
        return boot or 0, 0
    return boot or 0, _context(reversed(tail))


def _context(lines) -> int:
    for line in lines:
        try:
            usage = json.loads(line)["message"]["usage"]
        except (ValueError, KeyError, TypeError):
            continue
        return sum(int(usage.get(key) or 0) for key in
                   ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return 0


def heads(room: Path) -> dict[str, str]:
    """Each clone in the room and its HEAD."""
    found = {}
    for clone in sorted(Path(room).iterdir()):
        if clone.name != "port" and (clone / ".git").exists():
            proc = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"],
                                  capture_output=True, text=True)
            found[clone.name] = proc.stdout.strip()
    return found


def wrote(room: Path, since: float) -> bool:
    """This strand wrote a fact since ``since`` that acted: a letter, a wish, a
    left that moved, a PR. Read from the tail of its own ledger files."""
    strand = Path(room).name
    try:
        files = list((home_of_room(room).root / "ledger" / "facts").glob(f"*/{strand}.jsonl"))
    except (OSError, ValueError):
        return False
    for path in files:
        with path.open("rb") as handle:
            handle.seek(max(path.stat().st_size - 200_000, 0))
            lines = handle.read().decode(errors="replace").splitlines()
        for line in reversed(lines):
            try:
                fact = json.loads(line)
                at = datetime.fromisoformat(fact["at"]).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if at < since:
                break
            data = fact.get("data") or {}
            if (fact.get("by") == f"strand:{strand}" and fact.get("kind") not in SETTLING
                    and not (fact.get("kind") == "wish.left" and data.get("same"))):
                return True
    return False


def pace(room: Path) -> float:
    """The worst quota window's used share over its elapsed share; 1 when unknown."""
    from .usage import reading
    shell = os.environ.get("BRNRD_BODY_SHELL")
    try:
        value = reading(home_of_room(room), shell) if shell else None
    except (OSError, ValueError):
        value = None
    worst = None
    now = time.time()
    for window in (value or {}).get("windows") or ():
        span = SPANS.get(window.get("name"))
        try:
            ends = datetime.fromisoformat(window["resets_at"]).timestamp()
            used = (100.0 - float(window["left_pct"])) / 100.0
        except (KeyError, TypeError, ValueError):
            continue
        if not span or ends <= now:
            continue
        elapsed = max(1.0 - (ends - now) / span, 0.05)
        worst = max(worst or 0.0, used / elapsed)
    return 1.0 if worst is None else worst


def bars(state: dict, excess: int, p: float) -> tuple[float | None, int, float, int]:
    """``(q, x, p, need)``: the noise share over the turns that count (None under
    ``TURNS``), the context grown since boot, the pace and the excess it needs."""
    turns = (state.get("turns") or [])[int(state.get("declined_turns") or 0):]
    q = sum(not acted for acted, _ in turns) / len(turns) if len(turns) >= TURNS else None
    now = (state.get("turns") or [[None, 0]])[-1][1]
    x = max(now - int(state.get("boot") or 0), 0) if now else 0
    return q, x, p, int(excess / max(p, 0.5))


def due(state: dict, excess: int, p: float) -> bool:
    q, x, _, need = bars(state, excess, p)
    return x >= BACKSTOP * WINDOW or (q is not None and q >= NOISE and x >= need)


def line(state: dict, excess: int, p: float) -> str:
    q, x, p, need = bars(state, excess, p)
    return (f"q {'-' if q is None else f'{q:.1f}'} · x {x // 1000}k/{need // 1000}k · p {p:.1f}")


def news(state: dict, excess: int, p: float) -> str:
    """The numbers, once two of the three bars are crossed."""
    q, x, p, need = bars(state, excess, p)
    crossed = (q is not None and q >= NOISE) + (x >= need) + (p >= 1.0)
    return f"shed: {line(state, excess, p)}" if crossed >= 2 else ""


def held(room: Path, thread: str) -> bool:
    """A thread the person holds is never asked to shed; ``home`` names the home thread."""
    from .config import load_config
    home = home_of_room(room)
    names = set(load_config(home.root).shed_hold or ())
    if "home" in names:
        names.add(home.main_thread())
    return thread in names


def ask(room: Path, state: dict, thread: str) -> tuple[str, dict]:
    """The stop's shed section when one is due, and the state to record."""
    from .config import load_config
    if not thread or held(room, thread):
        return "", {}
    excess = int(load_config(home_of_room(room).root).shed_excess)
    p = pace(room)
    if not due(state, excess, p):
        return "", {}
    record = {} if state.get("shed_due_at") else {"shed_due_at": time.time(), "shed_readme": readme_hash(room, thread)}
    if state.get("declined_at"):
        at = time.strftime("%H:%M", time.gmtime(float(state["declined_at"])))
        return AGAIN.format(bars=line(state, excess, p), at=at, why=state.get("declined_why", ""),
                            thread=thread), record
    return ASK.format(bars=line(state, excess, p), thread=thread), record


def _readme(room: Path, thread: str) -> Path:
    from .selfrepo import resolve_clone
    return resolve_clone(room) / "threads" / thread / "README.md"


def readme_hash(room: Path, thread: str) -> str:
    try:
        return hashlib.sha256(_readme(room, thread).read_bytes()).hexdigest()
    except (OSError, ValueError):
        return ""


_WISH = re.compile(r"\bw-[0-9a-z]{12}\b")
_PR = re.compile(r"(?:#|/pull/)(\d+)\b")


def refusals(room: Path, thread: str, facts, state: dict) -> list[str]:
    """What keeps the README from being one a successor boots into (design §4)."""
    from .wishes import wishes
    text = _readme(room, thread).read_text()
    out = []
    if state.get("shed_due_at") and readme_hash(room, thread) == state.get("shed_readme"):
        at = time.strftime("%H:%M", time.gmtime(float(state["shed_due_at"])))
        out.append(f"the README is unchanged since the shed came due at {at}Z: read it as its successor and rewrite it")
    now, below = _split(text)
    if now is None:
        out.append("the README has no `## Now` block: say what is in flight, what waits on whom, what is next")
    opened, merged = {}, set()
    for fact in facts:
        data = fact.data or {}
        if fact.kind in ("pr.opened", "pr.merged") and data.get("thread") == thread:
            key = str(data.get("number"))
            if fact.kind == "pr.opened":
                opened[key] = data.get("url") or f"#{key}"
            else:
                merged.add(key)
    named = set(_PR.findall(now or ""))
    for number, url in opened.items():
        if number not in merged and number not in named:
            out.append(f"`## Now` doesn't name the open PR {url}")
    found = wishes(facts)
    for row in below:
        cited = [("w", ident) for ident in _WISH.findall(row)] + [("pr", n) for n in _PR.findall(row)]
        if cited and all(found[ident].state in {"delivered", "accepted", "dropped"} if kind == "w" and ident in found
                         else kind == "pr" and ident in merged for kind, ident in cited):
            out.append(f"dead row (every id it cites is closed): prune it or move it to a topic: {row.strip()[:120]}")
    return out


def _split(text: str) -> tuple[str | None, list[str]]:
    """The ``## Now`` block, and the table rows below it."""
    lines = text.splitlines()
    start = next((i for i, x in enumerate(lines) if x.strip().lower() == "## now"), None)
    if start is None:
        return None, []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    rule = re.compile(r"\|[\s:|-]*")
    rows = [x for i, x in enumerate(lines[end:], end) if x.startswith("|") and not rule.fullmatch(x.strip())
            # A header row is the one a rule follows.
            and not (i + 1 < len(lines) and rule.fullmatch(lines[i + 1].strip()))]
    return "\n".join(lines[start + 1:end]), rows


def decline(room: Path, why: str) -> None:
    """Once per body, with a reason: the trigger waits for ``TURNS`` new turns."""
    from .jack import _set_state, _state
    from .port import PortError
    state = _state(room)
    if not why.strip():
        raise PortError("shed: --decline needs a reason")
    if state.get("declined_at"):
        at = time.strftime("%H:%M", time.gmtime(float(state["declined_at"])))
        raise PortError(f"shed: declined once already at {at}Z ({state.get('declined_why')}); this one can't be declined")
    if not state.get("shed_due_at"):
        raise PortError("shed: no shed is due, so there is nothing to decline")
    _set_state(room, declined_at=time.time(), declined_why=why.strip(),
               declined_turns=len(state.get("turns") or []), shed_due_at=None, shed_readme=None)


def landed(room: Path) -> None:
    """A self landing starts a new stretch: the tally counts turns since it."""
    from .jack import _set_state
    _set_state(room, turns=[], declined_turns=0, landed_at=time.time())
