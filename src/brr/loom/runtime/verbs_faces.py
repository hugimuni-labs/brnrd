"""``python -m brr.loom.runtime face take|<face>|--off``: the body decides its thread's face."""

from __future__ import annotations

import argparse
import sys

from .home import mint, strand_of_room
from .port import _front, write_port


def _proposed(room) -> str | None:
    """The face the last boundary proposed, if one stands."""
    path = room / "port" / "in" / "boundary.md"
    text = path.read_text() if path.is_file() else ""
    line = next((line for line in text.splitlines() if line.startswith("face? ")), "")
    return line.split()[1] if line else None


def _face(argv: list[str]) -> int:
    from .faces import face_of
    from .verbs_loom import _room
    parser = argparse.ArgumentParser(
        prog="python -m brr.loom.runtime face",
        description="take the face the boundary proposed, wear another, or take yours off; "
                    "doing nothing lets a proposal lapse")
    parser.add_argument("face", nargs="?", help="take, or a face like b·w·d")
    parser.add_argument("--off", action="store_true", help="take the face off")
    parser.add_argument("--room")
    args = parser.parse_args(argv)
    if args.off == (args.face is not None):
        sys.stderr.write("face: give take, a face, or --off\n")
        return 2
    room = _room(args.room)
    raw = _proposed(room) if args.face == "take" else args.face
    if args.face == "take" and raw is None:
        sys.stderr.write("face: nothing is proposed; a proposal stands from the fact that "
                         "made it until your next letter\n")
        return 1
    try:
        face = "" if args.off else face_of(raw)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    stem = mint(5)
    write_port(room, stem, _front({"kind": "face", "id": f"{strand_of_room(room)}/{stem}", "face": face}))
    sys.stdout.write(f"face {face or 'off'}\n")
    return 0
