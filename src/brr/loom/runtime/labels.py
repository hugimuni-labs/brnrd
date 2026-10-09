"""A strand's label is a fold over facts. The body never declares it.

Taint is one bit. Audience is a set. Join is ``taint OR taint'`` and
``audience ∩ audience'``. The self's audience is ``self``. 4b enforces the
taint bit. Audience is carried, joined, and checked only by ``20-labels``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SELF_AUDIENCE = frozenset({"self"})
_URL_SCHEMES = ("https://", "http://")


@dataclass(frozen=True)
class Label:
    taint: bool
    audience: frozenset[str]

    def trailer(self) -> str:
        names = ",".join(sorted(self.audience))
        return f"taint={1 if self.taint else 0}; audience={names}"

    def as_dict(self) -> dict:
        return {"taint": self.taint, "audience": sorted(self.audience)}


CLEAN = Label(False, SELF_AUDIENCE)


def join(left: Label, right: Label) -> Label:
    return Label(left.taint or right.taint, left.audience & right.audience)


def parse_trailer(value: str) -> Label | None:
    """``taint=<0|1>; audience=<a,b>``. Missing pieces do not default open."""
    if not value or not value.strip():
        return None
    taint: bool | None = None
    audience: frozenset[str] | None = None
    for part in value.split(";"):
        piece = part.strip()
        if piece.startswith("taint="):
            bit = piece.split("=", 1)[1].strip()
            if bit not in {"0", "1"}:
                return None
            taint = bit == "1"
        elif piece.startswith("audience="):
            raw = piece.split("=", 1)[1].strip()
            names = frozenset(item for item in raw.split(",") if item)
            audience = names
    if taint is None:
        return None
    return Label(taint, SELF_AUDIENCE if audience is None else audience)


def _segment(value: str) -> str | None:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        return None
    return value


def _strip_prefix(sender: str) -> str:
    for prefix in ("thread:", "person:"):
        if sender.startswith(prefix):
            return sender.split(":", 1)[1]
    return sender


def is_stranger(sender: str, self_root: Path | None) -> bool:
    """True unless ``sender`` is a thread of this self or a person in ``people/``.

    A strand id is not a thread and not a person. Callers that know the
    ledger treat a leased strand as this self and fold *its* label instead.
    """
    if not isinstance(sender, str) or self_root is None:
        return True
    name = _segment(_strip_prefix(sender.strip()))
    if name is None:
        return True
    root = Path(self_root)
    if (root / "threads" / name / "README.md").is_file():
        return False
    if (root / "people" / name).is_dir():
        return False
    if name.startswith("p-"):
        person = _segment(name[2:])
        if person is not None and (root / "people" / person).is_dir():
            return False
    return True


def _letter_map(facts) -> dict[str, object]:
    found: dict[str, object] = {}
    for fact in facts:
        if getattr(fact, "kind", None) != "letter":
            continue
        data = fact.data or {}
        ident = data.get("id") or fact.id
        if ident:
            found[str(ident)] = fact
    return found


def _explicit(fact) -> Label | None:
    raw = (fact.data or {}).get("label")
    if isinstance(raw, dict) and "taint" in raw:
        bit = raw.get("taint")
        if isinstance(bit, str):
            bit = bit == "1" or bit.lower() == "true"
        audience = raw.get("audience")
        if isinstance(audience, str):
            audience = [item for item in audience.split(",") if item]
        names = frozenset(str(item) for item in audience) if audience else SELF_AUDIENCE
        return Label(bool(bit), names)
    if isinstance(raw, str):
        return parse_trailer(raw)
    return None


def _about(fact, strand: str) -> bool:
    data = fact.data or {}
    if data.get("strand") == strand:
        return True
    return getattr(fact, "by", "") == f"strand:{strand}"


def _url(origin: object) -> bool:
    return isinstance(origin, str) and origin.startswith(_URL_SCHEMES)


def _known_strand(facts, sender: str) -> bool:
    for fact in facts:
        if getattr(fact, "kind", None) != "lease":
            continue
        if (fact.data or {}).get("strand") == sender:
            return True
    return False


def _sibling_log(self_root: Path | None, strand: str) -> str:
    if self_root is None or not _segment(strand):
        return ""
    path = Path(self_root).parent / "rooms" / strand / "port" / "jack-errors.log"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def jack_log_text(room: Path | None, self_root: Path | None, strand: str) -> str:
    """Every jack error log that belongs to this strand. Non-empty taints."""
    paths: list[Path] = []
    if room is not None:
        room = Path(room)
        paths.append(room / "port" / "jack-errors.log")
        if room.name == "self":
            paths.append(room.parent / "port" / "jack-errors.log")
    if self_root is not None and _segment(strand):
        paths.append(Path(self_root).parent / "rooms" / strand / "port" / "jack-errors.log")
    chunks: list[str] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            chunks.append(text)
    return "\n".join(chunks)


def strand_label(
    facts,
    strand: str,
    *,
    self_root: Path | None = None,
    jack_log: str | None = None,
    _seen: frozenset[str] = frozenset(),
) -> Label:
    """Join what this strand was shown, its parent at birth, and taint facts.

    ``jack_log`` is the room's jack error log. A non-empty log taints: the
    jack fails open on the work and closed on the label. Pass ``""`` to say
    the log was read and was empty. ``None`` reads the sibling room when
    ``self_root`` is set.
    """
    if not strand or strand in _seen:
        return CLEAN
    seen = _seen | {strand}
    label = CLEAN
    birth: tuple[int, object] | None = None
    for index, fact in enumerate(facts):
        if getattr(fact, "kind", None) != "lease":
            continue
        if (fact.data or {}).get("strand") == strand:
            birth = (index, fact)
            break
    if birth is not None:
        parent = (birth[1].data or {}).get("parent")
        if isinstance(parent, str) and parent and parent != strand:
            label = join(label, strand_label(
                list(facts)[: birth[0]], parent,
                self_root=self_root, jack_log=None, _seen=seen,
            ))
    letters = _letter_map(facts)
    for fact in facts:
        kind = getattr(fact, "kind", None)
        if kind == "shown" and (fact.data or {}).get("strand") == strand:
            for ident in (fact.data or {}).get("ids") or ():
                letter = letters.get(str(ident))
                if letter is not None:
                    label = join(label, _sender_label(letter, facts, self_root, seen))
        elif kind == "label.tainted" and _about(fact, strand):
            label = join(label, Label(True, SELF_AUDIENCE))
        elif kind == "source" and _about(fact, strand) and _url((fact.data or {}).get("origin")):
            label = join(label, Label(True, SELF_AUDIENCE))
    log = jack_log
    if log is None:
        log = _sibling_log(self_root, strand)
    if log and log.strip():
        label = Label(True, label.audience)
    return label


def _sender_label(letter, facts, self_root: Path | None, seen: frozenset[str]) -> Label:
    explicit = _explicit(letter)
    if explicit is not None:
        return explicit
    sender = str((letter.data or {}).get("from") or "")
    if not is_stranger(sender, self_root):
        return CLEAN
    if _known_strand(facts, sender):
        return strand_label(facts, sender, self_root=self_root, jack_log=None, _seen=seen)
    return Label(True, SELF_AUDIENCE)


def label_inputs(room: Path) -> tuple[str, list, Path | None, str]:
    """Strand id, facts, self root, and jack log for a room about to send.

    A loom home is a directory that holds ``ledger/facts`` and ``self`` or
    ``rooms``. The walk stops there. It does not keep climbing into a
    parent checkout that happens to contain some other ledger.
    """
    room = Path(room)
    strand = _strand_of(room)
    home = _home_of(room)
    facts: list = []
    self_root: Path | None = None
    if home is not None:
        from .home import Home
        from .ledger import read_facts
        facts = read_facts(Home(home))
        if (home / "self").is_dir():
            self_root = home / "self"
    if self_root is None and (room / "threads").is_dir():
        self_root = room
    return strand, facts, self_root, jack_log_text(room, self_root, strand)


def _strand_of(room: Path) -> str:
    import os
    import subprocess
    env = {
        key: value for key, value in os.environ.items()
        if key not in {
            "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX",
            "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE",
        }
    }
    proc = subprocess.run(
        ["git", "-C", os.fspath(room), "symbolic-ref", "--short", "HEAD"],
        env=env, capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0:
        branch = proc.stdout.strip()
        if branch.startswith("strand/"):
            return branch.split("/", 1)[1]
    if room.name == "self" and room.parent.name not in {"", ".", ".."}:
        return room.parent.name
    return room.name


def _home_of(room: Path) -> Path | None:
    candidate = room
    for _ in range(6):
        if (candidate / "ledger" / "facts").is_dir() and (
            (candidate / "self").is_dir() or (candidate / "rooms").is_dir()
        ):
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return None
