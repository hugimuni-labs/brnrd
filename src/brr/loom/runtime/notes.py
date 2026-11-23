"""Notes that open themselves: any note with a ``trigger:`` line is shown at the call that touches it.

One mechanism for all memory (threads/judged-acts, his 11339): a scar, a
library page, a person's page and a service note are all notes. A note is a
markdown file on the self's main under ``memory/``, ``people/`` or
``library/``, or a product note in brnrd's ``limbs/``, with a ``trigger:``
line in its first ten lines: comma-separated phrases (a command, a host, a
path, a name). When a tool call's own text (its command, path, URL, query or
address) holds a phrase as a word, the pre-tool hook shows the note: its
path, its first line, and what the loom saw happen. Once per body per note:
a body remembering to open its scars is the reflex that fails, a scar shown
at every call is noise.

Two kinds of line, kept apart. The body writes what it thinks, in the note.
The loom writes what happened, in ``hands.json`` beside the journal: for each
program or host a marked call reached (mark.py), when it last worked and
when it last blocked, and why. A note with no success behind it for 30 days
is shown as unverified. When something blocked, a stop that already holds for
another reason adds one line asking for its note; the line never holds a stop
alone (a hook shows a fact or refuses, never demands). No upkeep pass:
everything here runs at a call or a stop.

A scar (``memory/scars/``) is shown with its text, up to :data:`SCAR_LINES`
lines after its title, trigger and date, read from the self's tip at show
time: a path one read away is the reflex that failed (the empty sends of
2026-11-18). Other notes show their path and head.

A phrase that ends in ``:`` or ``/`` is a prefix: ``telegram:`` opens at
``telegram:155783668``.

The same record keeps seven days of scar displays and their tool outcomes.
Writers in different rooms share a lock; readers see an atomic snapshot.
"""

from __future__ import annotations

import fcntl
import json
import re
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOTS = ("memory/", "people/", "library/")
PRODUCT = Path(__file__).resolve().parent / "hand_notes"
CACHE = "notes.json"
HANDS = "hands.json"
SCAR_PREFIX = "memory/scars/"
SCAR_WINDOW = 7 * 86400
_SCARS = "/scar-displays"  # Reserved key, outside the program and host namespaces.
STALE = 30 * 86400
# At most this many notes at one call: the rest open at a later call that names them.
AT_ONCE = 5
MIN = 2
SCAR_LINES = 15


@dataclass(frozen=True)
class Note:
    path: str
    head: str
    phrases: tuple[str, ...]


def _trigger(text: str) -> tuple[str, tuple[str, ...]]:
    """``(head, phrases)`` from a note's first ten lines; ``("", ())`` with no trigger line."""
    lines = text.splitlines()[:10]
    head = next((ln.lstrip("# ").strip() for ln in lines if ln.strip()), "")
    for ln in lines:
        if ln.lower().startswith("trigger:"):
            raw = ln.split(":", 1)[1]
            phrases = tuple(p.strip().strip("`").strip() for p in raw.split(","))
            return head, tuple(p for p in phrases if len(p) >= MIN)
    return head, ()


def _git(cwd: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True, text=True, timeout=60)


def _from_self(self_repo: Path, tip: str) -> list[Note]:
    listing = _git(self_repo, "ls-tree", "-r", "--name-only", tip, *ROOTS)
    paths = [p for p in listing.stdout.splitlines() if p.endswith(".md") and not p.endswith("/index.md")]
    if not paths:
        return []
    batch = subprocess.run(["git", "cat-file", "--batch"], cwd=self_repo, capture_output=True, timeout=60,
                           input="".join(f"{tip}:{p}\n" for p in paths).encode())
    notes, out, i = [], batch.stdout, 0
    for path in paths:
        end = out.find(b"\n", i)
        if end < 0:
            break
        header = out[i:end].split()
        if len(header) < 3 or header[1] != b"blob":
            i = end + 1
            continue
        size = int(header[2])
        body = out[end + 1:end + 1 + size].decode("utf-8", "replace")
        i = end + 1 + size + 1
        head, phrases = _trigger(body)
        if phrases:
            notes.append(Note(path, head, phrases))
    return notes


def _from_product() -> list[Note]:
    notes = []
    for page in sorted(PRODUCT.glob("*.md")) if PRODUCT.is_dir() else []:
        head, phrases = _trigger(page.read_text(encoding="utf-8"))
        if phrases:
            notes.append(Note(f"brnrd:limbs/{page.name}", head, phrases))
    return notes


def _product_stamp() -> float:
    """The product notes' newest change: a loom update that edits one is seen at once."""
    try:
        return max((p.stat().st_mtime for p in PRODUCT.glob("*.md")), default=0.0)
    except OSError:
        return 0.0


def load(room: Path) -> list[Note]:
    """Every note, cached in the room's port by the self's main tip."""
    from .home import home_of_room
    from .lifecycle import main_tip
    self_repo = home_of_room(Path(room)).root / "self"
    try:
        tip = main_tip(self_repo)
    except Exception:  # noqa: BLE001 - no self, no self notes; the product's still show
        tip = ""
    cache = Path(room) / "port" / CACHE
    try:
        kept = json.loads(cache.read_text())
        if kept.get("tip") == tip and kept.get("product") == _product_stamp():
            return [Note(n["path"], n["head"], tuple(n["phrases"])) for n in kept["notes"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    notes = (_from_self(self_repo, tip) if tip else []) + _from_product()
    try:
        cache.write_text(json.dumps({"tip": tip, "product": _product_stamp(), "notes": [n.__dict__ | {"phrases": list(n.phrases)}
                                                           for n in notes]}) + "\n")
    except OSError:
        pass
    return notes


def call_text(payload: dict) -> str:
    """What a call names: its command, path, URL, query, pattern or address; never its result."""
    args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    keys = ("command", "cmd", "file_path", "notebook_path", "path", "url", "query", "pattern", "to")
    return "\n".join(str(args[k]) for k in keys if isinstance(args.get(k), str))


def _phrase(phrase: str) -> re.Pattern:
    # A phrase ending in ":" or "/" is a prefix: "telegram:" names "telegram:155783668".
    tail = "" if phrase[-1:] in (":", "/") else r"(?![\w-])"
    return re.compile(rf"(?<![\w-]){re.escape(phrase)}{tail}", re.I)


def touched(notes: list[Note], text: str) -> list[tuple[Note, str]]:
    """Each note a phrase of which stands in ``text`` as a word, with that phrase; the most
    specific (longest phrase) first. A substring test first keeps a call's cost in the
    milliseconds with hundreds of notes."""
    if not text:
        return []
    low = text.lower()
    out = []
    for note in notes:
        hits = [p for p in note.phrases if p.lower() in low and _phrase(p).search(text)]
        if hits:
            out.append((note, max(hits, key=len)))
    return sorted(out, key=lambda np: -len(np[1]))


# --- what happened: the loom's line, from marked calls ----------------------

def _hands_path(room: Path) -> Path:
    from .home import home_of_room
    return home_of_room(Path(room)).root / "loom" / HANDS


def _read_hands(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def hands(room: Path) -> dict:
    return _read_hands(_hands_path(room))


@contextmanager
def _edit_hands(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = _read_hands(path)
        yield record
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, sort_keys=True) + "\n")
        tmp.replace(path)


def _call_id(room: Path, payload: dict) -> str | None:
    if not payload.get("tool_use_id"):
        return None
    return json.dumps([room.name, payload.get("session_id"), payload["tool_use_id"]])


def record_displays(room: Path, payload: dict, paths: list[str]) -> None:
    scars = [path for path in paths if path.startswith(SCAR_PREFIX)]
    if not scars:
        return
    ident = _call_id(room, payload)
    if ident is None:
        return
    with _edit_hands(_hands_path(room)) as record:
        now = time.time()
        history = record.setdefault(_SCARS, {"since": now, "calls": {}})
        calls = {key: call for key, call in history["calls"].items()
                 if call["at"] >= now - SCAR_WINDOW}
        calls.setdefault(ident, {"at": now, "paths": scars})
        history["calls"] = calls


def _hand_key(payload: dict) -> str | None:
    from . import mark
    if payload.get("tool_name") == "Bash":
        command = str((payload.get("tool_input") or {}).get("command") or "")
        if mark.shell_mark(command) is None:
            return None
        found = mark._ASSIGN.search(command.split("<<", 1)[0])
        return mark.program(command[found.end():] if found else command)
    if payload.get("tool_name") == "WebFetch":
        return mark.host(str((payload.get("tool_input") or {}).get("url") or ""))
    return None


def saw(room: Path, payload: dict, failed: bool, why: str = "") -> str | None:
    """Record a marked hand's stamp and the outcome of any scars shown for this call."""
    from brr.redact import redact_detail

    key = _hand_key(payload)
    ident = _call_id(room, payload)
    path = _hands_path(room)
    if not key and (ident is None or ident not in _read_hands(path).get(_SCARS, {}).get("calls", {})):
        return None
    try:
        with _edit_hands(path) as record:
            now = time.time()
            if key:
                entry = record.setdefault(key, {})
                if failed:
                    entry.update(blocked=now, why=why[:200], by=room.name)
                else:
                    entry["worked"] = now
            call = record.get(_SCARS, {}).get("calls", {}).get(ident)
            if call is not None and "ended" not in call and call["at"] >= now - SCAR_WINDOW:
                call["ended"] = now
                if failed:
                    call["failure"] = redact_detail(why)[:200]
    except OSError:
        return None
    return key


def _when(seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(seconds))


def stamp(note: Note, record: dict, now: float | None = None) -> str:
    """What the loom saw for the programs and hosts a note's phrases name; unverified past 30 days."""
    now = time.time() if now is None else now
    # A program or host stands for itself and for the phrases it opens ("gh" for "gh pr checks").
    keys = {p for p in note.phrases} | {p.split()[0] for p in note.phrases if p.split()}
    seen = [(k, record[k]) for k in sorted(keys) if isinstance(record.get(k), dict)]
    worked = max((e.get("worked", 0) for _, e in seen), default=0)
    parts = []
    if worked:
        parts.append(f"last worked {_when(worked)}")
    blocked = max(seen, key=lambda pe: pe[1].get("blocked", 0), default=None)
    if blocked and blocked[1].get("blocked"):
        why = blocked[1].get("why") or ""
        parts.append(f"last blocked {_when(blocked[1]['blocked'])}" + (f" ({why})" if why else ""))
    if seen and now - worked > STALE:
        parts.insert(0, "unverified: no act behind it worked in 30 days")
    return "; ".join(parts)


def shown_at(room: Path, payload: dict, already: set[str]) -> tuple[str, list[str]]:
    """The notes this call touches that this body has not seen: ``(context, paths shown)``."""
    found = [(n, p) for n, p in touched(load(room), call_text(payload)) if n.path not in already][:AT_ONCE]
    if not found:
        return "", []
    record = hands(room)
    lines = []
    for note, phrase in found:
        where = note.path if note.path.startswith("brnrd:") else f"self/{note.path}"
        said = stamp(note, record)
        lines.append(f"note ({phrase}): {where}: {note.head}" + (f" [{said}]" if said else ""))
        if note.path.startswith(SCAR_PREFIX):
            lines.extend("  " + ln for ln in scar_text(room, note.path))
    return "\n".join(lines), [n.path for n, _ in found]


def scar_text(room: Path, path: str) -> list[str]:
    """A scar's rule, read from the self's tip now: the lines after its title, trigger and date."""
    from .home import home_of_room
    from .lifecycle import main_tip
    self_repo = home_of_room(Path(room)).root / "self"
    try:
        shown = _git(self_repo, "show", f"{main_tip(self_repo)}:{path}")
    except Exception:  # noqa: BLE001 - the head was shown; the text is a bonus
        return []
    if shown.returncode != 0:
        return []
    lines = shown.stdout.splitlines()
    start = 0
    for i, ln in enumerate(lines[:10]):
        if ln.startswith("#") or re.match(r"(?i)(trigger|date):", ln):
            start = i + 1
    body = [ln for ln in lines[start:] if ln.strip()][:SCAR_LINES]
    return body


def blocked_since(room: Path, since: float) -> list[tuple[str, str]]:
    """Programs or hosts this room's marked calls blocked at since ``since``: ``(key, why)``."""
    name = Path(room).name
    return sorted((k, e.get("why", "")) for k, e in hands(room).items()
                  if isinstance(e, dict) and e.get("by") == name and e.get("blocked", 0) >= since)
