"""How a person reads, checked at the one place every message to them passes (brnrd#2776).

``people/<person>/reading.toml`` in the self names the limits, all optional::

    max_chars = 600          # the whole body
    max_line_chars = 120     # any one line
    max_questions = 1        # lines ending in a question mark
    no_repeat_minutes = 10   # the channel's last body again within N minutes: the same,
                             # or one holding the other when the shorter is over 200 chars

A body writes the file at the person's word; the person never manages it. It
is read from the self's ``main`` tip, never from ``home/self``'s work tree: that
tree follows a landing (``updateInstead``) but also holds files main does not
(an untracked ``channels.toml`` on 2026-11-18), so a limit holds from the next
send after it lands, and only once it has landed. No file on main, no check. Only a letter to that person's own direct channel is checked
(``connector.person_dm`` says whose it is); a ``--note`` and a thread letter
never are. A refusal is one line naming every limit broken and how to split;
there is no override: a body that needs more sends more messages.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

#: The keys a reading.toml may set. Anything else is a typo that would switch a limit off unseen.
KEYS = ("max_chars", "max_line_chars", "max_questions", "no_repeat_minutes")


#: A body that holds the last one, or sits in it, is a repeat when the shorter is longer than this.
REPEAT_SPAN = 200


def limits(self_dir: Path, person: str) -> dict[str, int]:
    """The person's limits on the self's main, ``{}`` without a file; a malformed file raises ValueError."""
    from .config import ConfigError, parse_values
    from .selfrepo import git
    where = f"people/{person}/reading.toml"
    if not (Path(self_dir) / ".git").exists():
        return {}
    shown = git(Path(self_dir), "show", f"main:{where}", check=False)
    if shown.returncode != 0:
        return {}
    try:
        values = parse_values(shown.stdout, where)
    except ConfigError as exc:
        raise ValueError(f"reading: {where}: {exc}") from exc
    unknown = sorted(set(values) - set(KEYS))
    if unknown:
        raise ValueError(f"reading: {where} sets {', '.join(unknown)}; the keys are {', '.join(KEYS)}")
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"reading: {where}: {key} must be a positive whole number, not {value!r}")
    return dict(values)


def _ordinal(n: int) -> str:
    words = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}
    return words.get(n, f"{n}th")


def _seconds(at: object) -> float | None:
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def last_sent(room: Path, facts, channel: str) -> tuple[float, str] | None:
    """``(when, body)`` of the latest letter to ``channel``: in the ledger, or still in this room's port."""
    from .port import PortError, parse_frontmatter
    found: list[tuple[float, str]] = []
    for fact in facts:
        if fact.kind == "letter" and fact.data.get("to") == channel:
            when = _seconds(fact.at)
            if when is not None:
                found.append((when, str(fact.data.get("body") or "")))
    out = Path(room) / "port" / "out"
    for path in sorted(out.glob("*.md")) if out.is_dir() else ():
        try:
            sent = parse_frontmatter(path.read_text(encoding="utf-8"))
            when = path.stat().st_mtime
        except (OSError, PortError, UnicodeDecodeError):
            continue
        if sent.get("kind") == "letter" and sent.get("to") == channel:
            found.append((when, sent.get("body", "")))
    return max(found, key=lambda row: row[0]) if found else None


def broken(body: str, rules: dict[str, int], name: str, *, last: tuple[float, str] | None = None,
           now: float | None = None) -> list[str]:
    """Each limit ``body`` breaks, as the words that say how to mend it."""
    text = body.strip()
    said: list[str] = []
    limit = rules.get("max_chars")
    if limit is not None and len(text) > limit:
        said.append(f"{len(text)} chars, {name}'s limit is {limit}; split it into messages of {limit} or fewer")
    limit = rules.get("max_line_chars")
    if limit is not None:
        long = [(i, len(line)) for i, line in enumerate(text.splitlines(), 1) if len(line) > limit]
        if long:
            first, size = long[0]
            more = f" (and {len(long) - 1} more)" if len(long) > 1 else ""
            said.append(f"line {first} is {size} chars{more}, {name}'s limit is {limit}; break it into shorter lines")
    limit = rules.get("max_questions")
    if limit is not None:
        asked = sum(1 for line in text.splitlines() if line.rstrip().endswith("?"))
        if asked > limit:
            rest = (f"send the {_ordinal(limit + 1)} as its own message" if asked == limit + 1
                    else f"send the ones past the {_ordinal(limit)} as their own messages")
            said.append(f"{asked} questions, {name}'s limit is {limit}; {rest}")
    limit = rules.get("no_repeat_minutes")
    if limit is not None and last is not None and repeats(text, last[1]):
        ago = (time.time() if now is None else now) - last[0]
        if ago < limit * 60:
            minutes = max(0, int(ago // 60))
            said.append(f"this repeats what the channel got {minutes} min ago, {name}'s limit is no repeat "
                        f"within {limit} min; send only what is new, or nothing")
    return said


def repeats(body: str, last: str) -> bool:
    """The same body, or one holding the other once both are long enough to be a message and not an 'ok'."""
    body, last = body.strip(), last.strip()
    if body == last:
        return True
    short, long = sorted((body, last), key=len)
    return len(short) > REPEAT_SPAN and short in long


def check(room: Path, home, channel: str, body: str) -> None:
    """Refuse a letter to a person's own channel that breaks how they read; silent where no limits apply."""
    from .connector import person_dm
    sender = person_dm(home, channel)
    if not sender:
        return
    name = sender.removeprefix("person:")
    rules = limits(home.root / "self", name)
    if not rules:
        return
    last = None
    if "no_repeat_minutes" in rules:
        from .ledger import read_facts
        last = last_sent(room, read_facts(home), channel)
    said = broken(body, rules, name, last=last)
    if said:
        raise ValueError("reading: " + " · ".join(said))
