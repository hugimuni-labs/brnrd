"""Reincarnating is the body's call; the loom tells it what it would lose.

The design is the self's threads/boot-review/design.md (v2, on the person's
5557 and 5565) and threads/reincarnate (10048). Nothing here says one is due. The value of what sits in a
context can't be measured from outside it, so the jack only states facts: what
the README gained and lost since this gen booted, the ids it cites that have
since closed, and, where a judgement is due (the reincarnation verb, a long park), the
stakes and the context's size as plain cost.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .home import home_of_room
from .ledger import renamed

#: Facts that settle a turn rather than act in it.
SETTLING = {"shown", "note", "wish.read", "left.asked", "reincarnate", "hold"}

STAKES = ("What isn't in the README is lost on any end, a reincarnation or a crash, and the body that "
          "picks up after a careless handover pays for it.")

#: Past this context a park asks what would die: when to ask, never what to do.
MARK = 150_000
#: A hold, or an ask left unanswered, covers the context until it grows this much more.
REARM = 50_000

DIE = "What in it would die if you reincarnated now?"
ANSWER = ('Answer with `brnrd runtime reincarnate --why "<what the successor picks up>"` '
          'or `brnrd runtime hold --why "<what would die, by name>"`.')


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


def _mine(room: Path, since: float) -> list[dict]:
    """The facts this strand wrote since ``since``, oldest first, from the tail of its own ledger files."""
    strand = Path(room).name
    found: list[tuple[float, dict]] = []
    try:
        files = list((home_of_room(room).root / "ledger" / "facts").glob(f"*/{strand}.jsonl"))
    except (OSError, ValueError):
        return []
    for path in files:
        with path.open("rb") as handle:
            handle.seek(max(path.stat().st_size - 200_000, 0))
            lines = handle.read().decode(errors="replace").splitlines()
        for line in reversed(lines):
            try:
                fact = renamed(json.loads(line))
                at = datetime.fromisoformat(fact["at"]).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if at < since:
                break
            if fact.get("by") == f"strand:{strand}":
                found.append((at, fact))
    return [fact for _, fact in sorted(found, key=lambda pair: pair[0])]


def _acted(fact: dict) -> bool:
    data = fact.get("data") or {}
    return fact.get("kind") not in SETTLING and not (fact.get("kind") == "wish.left" and data.get("same"))


def acts(room: Path, since: float) -> list[str]:
    """The kinds of the facts this strand wrote since ``since`` that acted: a
    letter, a wish, a left that moved, a PR. Distinct, in the order last written, for the README ask."""
    newest = dict.fromkeys(str(fact.get("kind")) for fact in reversed(_mine(room, since)) if _acted(fact))
    return list(reversed(newest))


def _readme(room: Path, thread: str) -> Path:
    from .selfrepo import resolve_clone
    return resolve_clone(room) / "threads" / thread / "README.md"


def readme_hash(room: Path, thread: str) -> str:
    try:
        return hashlib.sha256(_readme(room, thread).read_bytes()).hexdigest()
    except (OSError, ValueError):
        return ""


def readme_mtime(room: Path, thread: str) -> float:
    try:
        return _readme(room, thread).stat().st_mtime
    except (OSError, ValueError):
        return time.time()


_WISH = re.compile(r"\bw-[0-9a-z]{12}\b")
_PR = re.compile(r"(?:#|/pull/)(\d+)\b")
_CLOSED_WISH = {"delivered", "accepted", "dropped"}


def base(room: Path) -> str:
    """The self clone's HEAD, recorded when a gen boots: what "since boot" counts from."""
    from .selfrepo import resolve_clone
    proc = subprocess.run(["git", "-C", str(resolve_clone(room)), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    return proc.stdout.strip()


def closed(facts) -> tuple[dict[str, str], dict[str, str]]:
    """``(wishes, prs)``: each closed id and the word for how, from the ledger."""
    from .wishes import wishes
    shut = {ident: wish.state for ident, wish in wishes(facts).items() if wish.state in _CLOSED_WISH}
    prs = {str(f.data.get("number")): "merged" if f.kind == "pr.merged" else "closed"
           for f in facts if f.kind in ("pr.merged", "pr.closed") and f.data.get("number") is not None}
    return shut, prs


def readme_facts(room: Path, thread: str, facts, since: str = "") -> str:
    """One line of facts about the thread's README, or "" when there is none. Never a task."""
    path = _readme(room, thread)
    try:
        text = path.read_text()
    except (OSError, ValueError):
        return ""
    parts = [f"README: {len(text.encode()):,} B"]
    if since:
        from .selfrepo import resolve_clone
        clone, rel = resolve_clone(room), f"threads/{thread}/README.md"
        stat = subprocess.run(["git", "-C", str(clone), "diff", "--numstat", since, "--", rel],
                              capture_output=True, text=True).stdout.split()
        count = subprocess.run(["git", "-C", str(clone), "rev-list", "--count", f"{since}..HEAD", "--", rel],
                               capture_output=True, text=True).stdout.strip() or "0"
        if len(stat) >= 2 and stat[0].isdigit():
            parts.append(f"+{stat[0]} −{stat[1]} lines in {count} landing{'s' * (count != '1')} since this gen booted")
    shut, prs = closed(facts)
    gone = [f"{ident} {shut[ident]}" for ident in dict.fromkeys(_WISH.findall(text)) if ident in shut]
    gone += [f"#{n} {prs[n]}" for n in dict.fromkeys(_PR.findall(text)) if n in prs]
    if gone:
        more = f", +{len(gone) - 6}" if len(gone) > 6 else ""
        parts.append(f"cites {len(gone)} closed: " + ", ".join(gone[:6]) + more)
    return " · ".join(parts)


def _where(lines: list[str], ident: str) -> str:
    """The README place that cites ``ident``: its nearest heading, else the line's start."""
    for number, line in enumerate(lines, 1):
        if re.search(rf"(?<![\w-]){re.escape(ident)}(?!\w)", line):
            heading = next((x.lstrip("#").strip() for x in reversed(lines[:number])
                            if x.startswith("#")), "")
            return f'README "{heading or line.strip()[:40]}" (l.{number})'
    return ""


def cited_line(room: Path, thread: str, facts, began: str) -> str:
    """``cited:``: closures since ``began`` that the README cites and has not changed since."""
    path = _readme(room, thread)
    try:
        text, touched = path.read_text(), path.stat().st_mtime
    except (OSError, ValueError):
        return ""
    lines = text.splitlines()
    events: list[tuple[str, str, str]] = []
    for fact in facts:
        if fact.at < began:
            continue
        data = fact.data or {}
        if fact.kind in ("pr.merged", "pr.closed") and data.get("number") is not None:
            ident = f"#{data['number']}"
            word = "merged" if fact.kind == "pr.merged" else "closed"
        elif fact.kind == "wish.result":
            ident, word = str(data.get("wish")), "delivered" if data.get("delivered") else "got a result"
        elif fact.kind == "wish.verdict":
            ident, word = str(data.get("wish")), str(data.get("as") or "closed")
        else:
            continue
        try:
            at = datetime.fromisoformat(fact.at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        # Edited after the event: the body has acted on it, so the line clears.
        if at > touched:
            events.append((ident, word, _where(lines, ident)))
    said = {}
    for ident, word, where in events:
        if where:
            said[ident] = f"{ident} {word} · {where}"
    return "cited: " + " · ".join(said.values()) if said else ""


def long_wait(condition: str, now: datetime | None = None) -> bool:
    """A wait on a person or on a time hours out: where a reincarnation is a judgement worth making."""
    if condition.startswith(("letter:", "ask:")):
        return True
    if condition.startswith("at:"):
        try:
            when = datetime.fromisoformat(condition[3:].replace("Z", "+00:00"))
        except ValueError:
            return False
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return (when - (now or datetime.now(timezone.utc))).total_seconds() >= 3600
    return False


def rest_line(what: str, context: int, census: str = "") -> str:
    """The one sentence a long park gets: the cost, plain, and a question, never a verdict.
    Past ``MARK`` the question is what would die, with the census above it."""
    size = f" Context {context // 1000}k." if context else ""
    if context < MARK:
        return f"resting until {what}.{size} {STAKES} What does the next hour need that isn't written yet?"
    return (f"{census}\n" if census else "") + f"resting until {what}.{size} {STAKES} {DIE} {ANSWER}"


def _size(value) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode())
    if isinstance(value, list):
        return sum(_size(item.get("text", item) if isinstance(item, dict) else item) for item in value)
    return len(json.dumps(value, ensure_ascii=False).encode())


def _classes(line: str) -> dict[str, int]:
    """One transcript line's bytes by class: ``tool`` output, ``said`` (letters and
    boundaries: user turns and hook context) and ``own`` (text, thinking, tool calls)."""
    try:
        entry = json.loads(line)
    except ValueError:
        return {}
    if not isinstance(entry, dict):
        return {}
    kind = entry.get("type")
    if kind == "attachment":
        attached = entry.get("attachment") or {}
        if isinstance(attached, dict) and attached.get("type") == "hook_additional_context":
            return {"said": _size(attached.get("content"))}
        return {}
    if kind not in ("user", "assistant"):
        return {}
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return {"own" if kind == "assistant" else "said": _size(content)}
    out: dict[str, int] = {}
    for block in content or ():
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            cls, value = "tool", block.get("content")
        elif kind == "assistant":
            cls, value = "own", block.get("text") or block.get("thinking") or block.get("input")
        else:
            cls, value = "said", block.get("text")
        out[cls] = out.get(cls, 0) + _size(value)
    return out


def scroll(path: str, kept: dict | None = None) -> dict:
    """Bytes per class in the transcript at ``path``, read on from ``kept``'s offset
    (boot-review §3c), so a census reads only the tail that is new since the last."""
    kept = dict(kept or {})
    try:
        size = Path(path).stat().st_size
    except (OSError, ValueError, TypeError):
        return kept
    if kept.get("path") != path or int(kept.get("offset") or 0) > size:
        kept = {"path": path, "offset": 0, "tool": 0, "said": 0, "own": 0}
    with open(path, "rb") as handle:
        handle.seek(kept["offset"])
        chunk = handle.read()
    # A line still being written waits for the next read.
    end = chunk.rfind(b"\n") + 1
    for line in chunk[:end].decode(errors="replace").splitlines():
        for cls, count in _classes(line).items():
            kept[cls] += count
    kept["offset"] += end
    return kept


def landed_at(room: Path, thread: str, since: str) -> float | None:
    """When the README last landed after ``since`` (the gen's boot base), or None."""
    from .selfrepo import resolve_clone
    if not since:
        return None
    out = subprocess.run(["git", "-C", str(resolve_clone(room)), "log", "-1", "--format=%ct",
                          f"{since}..HEAD", "--", f"threads/{thread}/README.md"],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out.isdigit() else None


def _ago(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    return f"{minutes // 60}h{minutes % 60:02d}" if minutes >= 60 else f"{minutes}m"


def census(room: Path, thread: str, state: dict, facts, now: float | None = None) -> tuple[str, dict]:
    """``(lines, scroll)``: what this gen did since it booted, split at the README's last
    landing, and what its transcript is made of. Facts, never a verdict; the caller keeps
    ``scroll`` in jack-state so the next census reads on from it."""
    now = time.time() if now is None else now
    booted = float(state.get("booted_at") or 0)
    base = state.get("readme_base") or ""
    landed = landed_at(room, thread, base) if thread else None
    lines = []
    readme = readme_facts(room, thread, facts, base) if thread else ""
    head = f"since this gen booted{f' ({_ago(now - booted)} ago)' if booted else ''}"
    if landed is not None:
        lines.append(f"{head}, landed in the README (the successor gets it for free): {readme}")
    else:
        lines.append(f"{head}: nothing landed in the README" + (f" ({readme})" if readme else ""))
    mine = _mine(room, landed if landed is not None else booted)
    counts: dict[str, int] = {}
    for fact in mine:
        if _acted(fact):
            counts[str(fact.get("kind"))] = counts.get(str(fact.get("kind")), 0) + 1
    after = ", ".join(f"{count} {kind}" for kind, count in counts.items()) or "nothing"
    where = f"after the last landing ({_ago(now - landed)} ago)" if landed is not None else "unlanded"
    lines.append(f"{where}, not yet in the README: {after}")
    every = _mine(room, booted) if booted else mine
    sent = sum(1 for fact in every if fact.get("kind") == "letter")
    shown = {str(i) for fact in every if fact.get("kind") == "shown" for i in (fact.get("data") or {}).get("ids") or ()}
    kept = scroll(state.get("transcript") or "", state.get("scroll"))
    total = sum(int(kept.get(cls) or 0) for cls in ("tool", "said", "own"))
    parts = [f"letters {len(shown)} in, {sent} out", f"stops {int(state.get('stops') or 0)}"]
    if total:
        parts.insert(0, "scroll: " + " · ".join(
            f"{name} {round(100 * int(kept.get(cls) or 0) / total)}%"
            for cls, name in (("tool", "tool output"), ("said", "letters and boundaries"), ("own", "own turns"))))
    return "\n".join(lines + [" · ".join(parts)]), kept
