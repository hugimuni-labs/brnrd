"""``python -m brr.loom.runtime face <word>|--off``: the body sets its thread's face."""

from __future__ import annotations

import argparse
import sys

from .home import mint, strand_of_room
from .port import _front, write_port


def _face(argv: list[str]) -> int:
    from .faces import word_of
    from .verbs_loom import _room
    parser = argparse.ArgumentParser(
        prog="python -m brr.loom.runtime face",
        description="show how this work feels on your thread; set none if none is true")
    parser.add_argument("word", nargs="?")
    parser.add_argument("--off", action="store_true", help="take the face off")
    parser.add_argument("--room")
    args = parser.parse_args(argv)
    if args.off == (args.word is not None):
        sys.stderr.write("face: give one word, or --off\n")
        return 2
    try:
        word = "" if args.off else word_of(args.word)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    room = _room(args.room)
    stem = mint(5)
    write_port(room, stem, _front({"kind": "face", "id": f"{strand_of_room(room)}/{stem}", "word": word}))
    sys.stdout.write(f"face {word or 'off'}\n")
    return 0
