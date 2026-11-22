"""A foreground wait on the room's boundary and the ledger's wish changes."""

from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

from .home import home_of_room
from .jack import attend_letters
from .ledger import LedgerReader, strand_of
from .port import parse_boundary
from .refusals import Parser

INTERVAL = 0.2
BOARD_KINDS = ("wish", "wish.result", "wish.verdict", "letter")
_BOARD_ROW = re.compile(rb'"kind"\s*:\s*"wish(?:\.result|\.verdict)?"|"id"\s*:\s*"loom/wait-')


def _board_row(line: bytes) -> bool:
    # Most rows are unrelated. The cheap substring checks put a bound on the
    # regex work when opening a large ledger.
    return (b'"wish' in line or b'"loom/wait-' in line) and _BOARD_ROW.search(line) is not None


def _gist(text: str) -> str:
    first = text.split("\n", 1)[0].strip()
    return first if len(first) <= 160 else first[:159] + "…"


def _duration(raw: str) -> float:
    match = re.fullmatch(r"(?:(\d{1,3})m)?(?:(\d{1,3}(?:\.\d+)?)s)?", raw)
    if match is None or not 0 < (seconds := 60 * int(match[1] or 0) + float(match[2] or 0)) <= 570:
        raise argparse.ArgumentTypeError("use a duration above 0 and up to 9m30s (e.g. 30s or 9m)")
    return seconds


def _stamp(path: Path):
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return st.st_ino, st.st_mtime_ns, st.st_ctime_ns


def _letters(room: Path, thread: str):
    boundary = room / "port" / "in" / "boundary.md"
    stamp = object()

    def read():
        nonlocal stamp
        current = _stamp(boundary)
        if current == stamp:
            return [], ""
        stamp = current
        owed = attend_letters(room)
        return [(letter.id, _gist(letter.body)) for letter in parse_boundary(owed).letters], owed

    return read


def _board_item(fact):
    data = fact.data
    if fact.kind == "letter":
        if fact.by.startswith("loom:") and data.get("from") == "loom" and fact.id.startswith("loom/wait-"):
            head = _gist(str(data.get("body", "")))
            match = re.match(r"wait on (\S+) resolved:", head)
            if match:
                return match[1], head
        return None
    if fact.kind == "wish.verdict":
        return data["wish"], f"{data['wish']} · {data['as']}"
    return data["wish"], _gist(data.get("line") or data.get("ref") or data["wish"])


def _board(room: Path, thread: str):
    home = home_of_room(room)
    reader = LedgerReader(select=_board_row)
    previous = reader.refresh(home)
    offsets = {kind: len(previous.by_kind[kind]) for kind in BOARD_KINDS}
    owners = {fact.data["wish"]: fact.data["thread"] for fact in previous.by_kind["wish"]}

    def read():
        nonlocal previous, owners
        facts = reader.refresh(home)
        if facts is not previous:
            # A repair or a fact from another install can reorder the ledger.
            # Compare identities on replay so an old event does not fire again.
            additions = [fact for kind in BOARD_KINDS for fact in facts.by_kind[kind]
                         if fact.id not in previous.by_id]
            owners = {fact.data["wish"]: fact.data["thread"] for fact in facts.by_kind["wish"]}
        else:
            additions = [fact for kind in BOARD_KINDS for fact in facts.by_kind[kind][offsets[kind]:]]
            for fact in facts.by_kind["wish"][offsets["wish"]:]:
                owners[fact.data["wish"]] = fact.data["thread"]
        previous = facts
        offsets.update((kind, len(facts.by_kind[kind])) for kind in BOARD_KINDS)
        items = []
        for fact in sorted(additions, key=lambda fact: facts.positions[fact.id]):
            if strand_of(fact) != room.name and (item := _board_item(fact)) and owners.get(item[0]) == thread:
                items.append((fact.id, item[1]))
        return items, ""

    return read


STREAMS = {"letters": _letters, "board": _board}


def main(argv: list[str]) -> int:
    parser = Parser(verb="attend", prog="brnrd runtime attend")
    parser.add_argument("--room", default=os.environ.get("BRNRD_ROOM"), help=argparse.SUPPRESS)
    parser.add_argument("--for", dest="duration", default="9m", metavar="DURATION")
    parser.add_argument("--streams", default="letters,board")
    args = parser.parse_args(argv)
    try:
        seconds = _duration(args.duration)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    streams = dict.fromkeys(args.streams.split(","))
    if streams.keys() - STREAMS.keys():
        parser.error("--streams must be a comma-separated selection of letters,board")
    if not args.room:
        parser.error("BRNRD_ROOM is unset")
    thread = os.environ.get("BRNRD_THREAD", "")
    if "board" in streams and not thread:
        parser.error("BRNRD_THREAD is unset")

    deadline = time.monotonic() + seconds
    readers = {name: STREAMS[name](Path(args.room), thread) for name in streams}
    while True:
        digest, details = [], []
        for name, read in readers.items():
            items, owed = read()
            digest.extend(f"{name} · {ident} · {gist}" for ident, gist in items)
            if owed:
                details.append(owed.rstrip("\n"))
        if digest:
            print("\n".join([*digest, *details]))
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(f"attend: nothing in {args.duration}")
            return 0
        time.sleep(min(INTERVAL, remaining))
