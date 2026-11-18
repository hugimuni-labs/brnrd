"""How a person reads, kept in front of the body under every letter of theirs (brnrd#2776).

A person's page (``people/<person>/page.md``) may carry one line in its first
ten lines, ``reads: <free words>``, written by a body in the person's words,
e.g. ``reads: Arseni has slight dyslexia and ADHD``. The loom shows that line
under each of their letters wherever a body is shown one: the wake's occasion
and the boundary's owed list, a stop's included (``loom._reply_context`` feeds
both). It informs; the body judges. Nothing is refused on its account.

The page is read from the self's ``main`` tip, never from ``home/self``'s work
tree: that tree follows a landing but also holds files main does not, so the
line counts from the first render after it lands. No page or no line: nothing.
Over ``CAP`` bytes the first ``CAP`` are shown and the line says it was cut.
"""

from __future__ import annotations

from pathlib import Path

#: Bytes of a reads line shown; a person said about fifty is the size of it.
CAP = 100
#: The page's head: a reads line further down is page prose, not this line.
HEAD_LINES = 10
PREFIX = "reads:"

_CACHE: dict[tuple[Path, str, str], str] = {}


def line_of(page: str) -> str:
    """The page's reads line as shown, ``""`` without one; cut at ``CAP`` bytes with a receipt."""
    for raw in page.splitlines()[:HEAD_LINES]:
        text = raw.strip()
        if text.lower().startswith(PREFIX) and text[len(PREFIX):].strip():
            data = text.encode("utf-8")
            if len(data) <= CAP:
                return text
            return data[:CAP].decode("utf-8", "ignore").rstrip() + f" [cut at {CAP} bytes; the page holds the rest]"
    return ""


def reads_line(self_dir: Path, person: str) -> str:
    """``person``'s reads line from the page on main, ``""`` when there is none."""
    from .lifecycle import main_tip
    from .selfrepo import git
    repo = Path(self_dir)
    if not person or not (repo / ".git").exists():
        return ""
    try:
        tip = main_tip(repo)
    except Exception:  # noqa: BLE001 - a line that informs never costs a render
        return ""
    key = (repo, tip, person)
    if key not in _CACHE:
        shown = git(repo, "show", f"{tip}:people/{person}/page.md", check=False)
        _CACHE[key] = line_of(shown.stdout) if shown.returncode == 0 else ""
    return _CACHE[key]
