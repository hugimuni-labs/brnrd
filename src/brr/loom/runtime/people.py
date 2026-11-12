"""A person's page, as the jack sees it: one fact, never a demand (people-pages).

``people/<person>/page.md`` is the body's to write, on the trigger "something
happened between us". The loom cannot know whether a letter changed anything,
so it shows what it can count: how many of the person's letters this body has
seen this gen, when their page last changed on the self's main line, and when
its ``## between us`` section is over its budget. The body decides the rest.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .port import Boundary

#: Bytes ``## between us`` may hold before the stop fact says it is over (design §1).
BETWEEN_US_BUDGET = 1500


def person_of(sender: str) -> str:
    """The ``people/`` folder a letter's ``from`` names, or ``""`` for a thread or strand."""
    for prefix in ("person:", "p-"):
        if sender.startswith(prefix):
            name = sender[len(prefix):]
            return name if name and "/" not in name and name not in {".", ".."} else ""
    return ""


def between_us_bytes(text: str) -> int:
    """The byte size of the ``## between us`` section's body, 0 without one."""
    size, inside = 0, False
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            inside = line[3:].strip().lower() == "between us"
        elif inside:
            size += len(line.encode("utf-8"))
    return size


def page_date(self_dir: Path, person: str) -> str:
    """The page's last commit date as MM-DD, or ``never`` when it was never committed."""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    proc = subprocess.run(["git", "log", "-1", "--format=%cs", "--", f"people/{person}/page.md"],
                          cwd=self_dir, env=env, capture_output=True, text=True, timeout=10)
    day = proc.stdout.strip() if proc.returncode == 0 else ""
    return day[5:] if len(day) == 10 else "never"


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def tally(self_dir: Path, parsed: Boundary, kept: dict, *, names_repo: Path | None = None) -> tuple[list[str], list[str], dict]:
    """The people lines for this gen, what makes them news, and the state to keep.

    The news key is each person with their page date, and the over lines: never
    the count, which moves on every letter.

    ``kept`` is the last return's state: ``gen``, ``seen`` (person to letter ids)
    and ``dates`` (person to ``[moved, date]``, so git runs only when the
    page or HEAD moved). A person counts once their folder exists in ``people/``.
    """
    if kept.get("gen") != parsed.gen:
        kept = {"gen": parsed.gen, "seen": {}, "dates": kept.get("dates") or {}}
    seen = {p: list(ids) for p, ids in (kept.get("seen") or {}).items()}
    dates = dict(kept.get("dates") or {})
    for letter in parsed.letters:
        person = person_of(letter.sender)
        if person and (self_dir / "people" / person).is_dir() and letter.id not in seen.get(person, ()):
            seen.setdefault(person, []).append(letter.id)
    from .person_names import names, label
    resolved = names(names_repo or self_dir)
    lines, key = [], []
    for person in sorted(seen):
        page = self_dir / "people" / person / "page.md"
        mtime = _mtime(page)
        # A commit moves the date without touching the page: HEAD's reflog says it moved.
        moved = f"{mtime} {_mtime(self_dir / '.git' / 'logs' / 'HEAD')}"
        if (dates.get(person) or [None])[0] != moved:
            dates[person] = [moved, page_date(self_dir, person) if mtime else "never"]
        count = len(seen[person])
        lines.append(f"people: {count} letter{'s' * (count != 1)} from {label('person:' + person, resolved)} this gen; "
                     f"people/{person}/page.md last changed {dates[person][1]}")
        key.append(f"{person} {dates[person][1]}" + (f" {resolved[person]}" if person in resolved else ""))
        size = between_us_bytes(page.read_text(encoding="utf-8")) if mtime else 0
        if size > BETWEEN_US_BUDGET:
            lines.append(f"people: people/{person}/page.md between us is {size} bytes, "
                         f"over its {BETWEEN_US_BUDGET}")
            key.append(lines[-1])
    return lines, key, {"gen": parsed.gen, "seen": seen, "dates": dates}

