"""boundary.md, wake.md, and the files a body writes into port/out."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from brr.daemon2.facts import Fact

from .home import (
    atomic_write, home_of_room, is_channel, mint, strand_of_room, thread_of,
)


class PortError(RuntimeError):
    """A port file or an address the loom will not guess its way past."""


_HEADER = re.compile(r"<!-- port (\S+) gen (\d+) · owed([^>]*)-->")
_ITEM = re.compile(
    r"^- (?P<id>\S+) · from (?P<sender>\S+) · reply (?P<reply>\S+)"
    r"(?P<mark> · compact)? — (?P<first>.*)$"
)


@dataclass
class LetterView:
    id: str
    sender: str
    reply: str
    body: str
    compact: bool = False


@dataclass
class Boundary:
    strand: str = ""
    gen: int = 0
    ids: list[str] = field(default_factory=list)
    letters: list[LetterView] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "Boundary":
        return cls()


def parse_boundary(text: str) -> Boundary:
    """Parse a boundary or the owed section of a wake. Empty text is nothing owed.

    A non-empty text with no port header, or a header that disagrees with the
    letter list, is corrupt. The jack fails open on that; it does not guess.
    """
    if not text or not text.strip():
        return Boundary.empty()
    header = _HEADER.search(text)
    if header is None:
        raise PortError("boundary has no port header")
    ids = header.group(3).split()
    letters: list[LetterView] = []
    current: LetterView | None = None
    for line in text[header.end():].splitlines():
        # Continuation first: a blank line inside a body renders as two
        # spaces, and must not end the letter (#2223 review).
        if line.startswith("  ") and current is not None and not current.compact:
            current.body += "\n" + line[2:]
            continue
        if not line.strip() or line.startswith("status:") or line.startswith("owed:"):
            current = None
            continue
        match = _ITEM.match(line)
        if match:
            current = LetterView(
                id=match.group("id"), sender=match.group("sender"),
                reply=match.group("reply"), body=match.group("first"),
                compact=bool(match.group("mark")),
            )
            letters.append(current)
            continue
        raise PortError(f"boundary line is not a letter: {line!r}")
    if [letter.id for letter in letters] != ids:
        raise PortError("owed ids do not match the letter list")
    return Boundary(strand=header.group(1), gen=int(header.group(2)),
                    ids=ids, letters=letters)


def _reply(sender: str, sender_threads: dict[str, str]) -> str:
    thread = sender_threads.get(sender)
    return f"thread:{thread}" if thread else "-"


def _letter_lines(letter: Fact, reply: str, compact: bool) -> list[str]:
    body = str(letter.data.get("body") or "")
    first, *rest = body.split("\n") if body else [""]
    mark = " · compact" if compact else ""
    lines = [
        f"- {letter.data['id']} · from {letter.data.get('from', '')} · "
        f"reply {reply}{mark} — {first}"
    ]
    if not compact:
        lines.extend(f"  {extra}" for extra in rest)
    return lines


def render_boundary(strand: str, gen: int, thread: str, letters: list[Fact],
                    shown: set[str], sender_threads: dict[str, str]) -> str:
    """Letters this strand was already shown are compacted to id plus first line."""
    lines = [
        f"<!-- port {strand} gen {gen} · owed "
        + " ".join(str(letter.data['id']) for letter in letters) + " -->",
        f"status: strand {strand} · gen {gen} · thread {thread}",
        "owed:",
    ]
    for letter in letters:
        compact = str(letter.data["id"]) in shown
        lines.extend(_letter_lines(letter, _reply(str(letter.data.get("from") or ""),
                                                   sender_threads), compact))
    lines.append("")
    return "\n".join(lines)


def render_wake(readme: str, strand: str, gen: int, thread: str,
                letters: list[Fact], sender_threads: dict[str, str]) -> str:
    """The birth prompt lists every owed letter in full. Compaction is the boundary's job."""
    owed = render_boundary(strand, gen, thread, letters, set(), sender_threads)
    sentence = (
        f"you are strand {strand} on thread {thread}; letters arrive at your "
        "tool boundaries; answer with `python -m brr.loom.runtime send --re <id> "
        "--to thread:<from-thread> \"…\"`; when you're done, stop, and the jack "
        "holds you while letters may come"
    )
    return readme.rstrip() + "\n\n" + sentence + "\n\n" + owed


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise PortError("port file missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise PortError("port file unclosed frontmatter")
    found: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise PortError(f"port frontmatter not key: value: {line!r}")
        key, value = line.split(":", 1)
        found[key.strip()] = value.strip()
    body = text[end + 5:]
    if body.startswith("\n"):
        body = body[1:]
    found["body"] = body.rstrip("\n")
    return found


def _front(fields: dict[str, str], body: str = "") -> str:
    lines = ["---", *[f"{key}: {value}" for key, value in fields.items()], "---"]
    text = "\n".join(lines) + "\n"
    if body:
        text += body if body.endswith("\n") else body + "\n"
    return text


def write_port(room: Path, stem: str, text: str) -> None:
    atomic_write(Path(room) / "port" / "out" / f"{stem}.md", text)


def _full_re(room: Path, re: str) -> str:
    """A bare stem that names exactly one owed letter becomes its full id.

    The router demo's haiku answered ``--re imf8b`` for ``p-test/imf8b``: the
    answer handled nothing, the letter stayed owed, and the body answered it
    twice. Anything else passes through untouched; a reply may name a letter
    that is no longer owed.
    """
    if "/" in re:
        return re
    path = room / "port" / "in" / "boundary.md"
    try:
        ids = parse_boundary(path.read_text()).ids if path.is_file() else []
    except PortError:
        return re
    matches = [item for item in ids if item.endswith("/" + re)]
    return matches[0] if len(matches) == 1 else re


def write_send(room: Path, *, to: str, sender: str, body: str = "",
               re: str | None = None, note: str | None = None) -> str:
    home = home_of_room(Path(room))
    if is_channel(to):
        if to != "channel:fake":
            raise PortError(f"send: no channel adapter for {to!r}")
    else:
        thread = thread_of(to)
        if not home.thread_exists(thread):
            raise PortError(
                f"send: no thread {thread} "
                f"({home.thread_dir(thread) / 'README.md'} does not exist)")
    if note is not None and not re:
        raise PortError("send: --note requires --re")
    if re:
        re = _full_re(Path(room), re)
    if note is not None and body:
        raise PortError("send: --note is the whole no-answer; drop the body")
    stem = mint(5)
    letter_id = f"{sender}/{stem}"
    fields = {
        "kind": "note" if note is not None else "letter",
        "id": letter_id, "to": to, "from": sender,
    }
    if re:
        fields["re"] = re
    if note is not None:
        fields["note"] = note
    write_port(room, stem, _front(fields, "" if note is not None else body))
    return letter_id


def write_molt(room: Path, why: str) -> str:
    if not why:
        raise PortError("molt: --why is empty")
    strand = strand_of_room(Path(room))
    stem = mint(5)
    fact_id = f"{strand}/{stem}"
    write_port(room, stem, _front({"kind": "molt", "id": fact_id, "why": why}))
    # The jack allows the next stop from this flag, including after the loom
    # has already ingested and deleted the port/out file.
    atomic_write(Path(room) / "port" / "molt-pending", why + "\n")
    return fact_id


def fact_from_port(fm: dict[str, str], strand: str, gen: int | None) -> Fact:
    """Turn one ingested port file into a fact. ``gen`` is the strand's live lease."""
    kind = fm.get("kind")
    fact_id = fm.get("id")
    if not fact_id:
        raise PortError("port file has no id")
    if kind in {"letter", "note", "molt"} and gen is None:
        raise PortError(f"strand {strand} has no lease; refusing {kind} {fact_id}")
    if kind == "letter":
        data: dict = {
            "id": fact_id, "to": fm["to"], "body": fm.get("body", ""),
            "from": fm.get("from") or strand, "gen": gen,
        }
        if not is_channel(data["to"]):
            thread_of(data["to"])
        if fm.get("re"):
            data["re"] = fm["re"]
        return Fact(kind="letter", by=f"strand:{strand}", data=data, id=fact_id)
    if kind == "note":
        if not fm.get("re") or not fm.get("note"):
            raise PortError(f"note {fact_id} requires re and note")
        return Fact(
            kind="note", by=f"strand:{strand}", id=fact_id,
            data={"re": fm["re"], "why": fm["note"], "gen": gen},
        )
    if kind == "shown":
        owner = fm.get("strand") or strand
        if owner != strand:
            raise PortError(f"shown strand {owner} is not room {strand}")
        if "gen" not in fm:
            raise PortError(f"shown {fact_id} has no gen")
        return Fact(
            kind="shown", by=f"strand:{strand}", id=fact_id,
            data={"strand": owner, "gen": int(fm["gen"]), "ids": fm.get("ids", "").split()},
        )
    if kind == "molt":
        if not fm.get("why"):
            raise PortError(f"molt {fact_id} has no why")
        return Fact(
            kind="molt", by=f"strand:{strand}", id=fact_id,
            data={"strand": strand, "gen": gen, "why": fm["why"]},
        )
    raise PortError(f"unknown port kind {kind!r}")
