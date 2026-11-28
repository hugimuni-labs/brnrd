"""A body's friction goes where it belongs: a scar, a fit in the self, or upstream.

``brnrd runtime friction "<line>" --sink scar|fit|upstream --at <path>`` writes a
``friction`` fact. The loom never asks for one: it is a field on an act the body
already chose (a scar it landed, a molt it gives). The artifact is the receipt, so
``--at`` must name a file already on self main; an upstream friction's ``--at`` is
its scar, the workaround until the fix loads. Two frictions become work, as an own
wish on the home thread (sender ``self``, origin ``own``): one with no sink
(``sort: <line>``) and, on an install that can open a pull request on brnrd, an
upstream one (``fix upstream: <line>``).

A scar whose head carries ``upstream: brnrd#N`` is watched: once N's merge is in the
code this loom runs, the thread that wrote the scar gets one notice to probe the act
and delete the scar. Merged alone never fires it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from brr.daemon2.facts import Fact

from .home import Home, mint

SINKS = ("scar", "fit", "upstream")
SCARS = "memory/scars/"
UPSTREAM = "hugimuni-labs/brnrd"
# A scar's head: the frontmatter plus its first lines, as the scar check reads it.
HEAD = 10
# ``upstream: brnrd#123``, ``upstream: #123`` or a pull request URL on brnrd.
_LINE = re.compile(r"^\s*upstream:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_REF = re.compile(r"^(?:(?:[\w.-]+/)?brnrd)?#(\d+)$|^https://github\.com/[\w.-]+/brnrd/pull/(\d+)/?$")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)


def install_kind(home: Home, root: Path | None = None) -> str:
    """``upstream``, ``dev`` or ``plain``, derived, never declared.

    A loom that follows main (``update = dev``, or ``auto`` on a checkout that says dev)
    from a checkout of brnrd itself is upstream; following a checkout of anything else
    (a fork) is dev; a release or a checkout that does not follow main is plain."""
    from .config import load_config
    from .receipts import source_root
    from .update import checkout_mode, slug
    root = source_root() if root is None else root
    if not (root / ".git").exists():
        return "plain"
    mode = load_config(home.root).update
    if mode == "auto":
        mode = checkout_mode(root)
    if mode != "dev":
        return "plain"
    url = _git(root, "remote", "get-url", "origin").stdout.strip()
    return "upstream" if (slug(url) or "").lower() == UPSTREAM else "dev"


def _version(root: Path | None = None) -> str:
    from .receipts import source_root
    root = source_root() if root is None else root
    if (root / ".git").exists():
        sha = _git(root, "rev-parse", "--short=12", "HEAD").stdout.strip()
        if sha:
            return sha
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("brnrd")
    except PackageNotFoundError:
        return ""


def on_main(home: Home, path: str) -> bool:
    return _git(home.root / "self", "cat-file", "-e", f"main:{path}").returncode == 0


def check(home: Home, line: str, sink: str | None, at: str | None) -> tuple[str, str | None]:
    """The friction's line and ``--at`` as recorded, or a ValueError naming the fix."""
    line = " ".join((line or "").split())
    if not line:
        raise ValueError("friction: the line is empty: say what cost you, in one line")
    if sink is not None and sink not in SINKS:
        raise ValueError(f"friction: --sink is one of {', '.join(SINKS)}")
    at = at.strip().removeprefix("./") if at else None
    if sink is not None and not at:
        what = "its scar" if sink == "upstream" else f"the {sink} you landed"
        raise ValueError(f"friction: --sink {sink} needs --at <path in the self>: {what}")
    if sink == "upstream" and not at.startswith(SCARS):
        raise ValueError(f"friction: an upstream friction keeps a scar until its fix loads: "
                         f"--at names it under {SCARS}")
    if at and not on_main(home, at):
        raise ValueError(f"friction: {at} is not on self main: land it first (send-self), then record")
    return line, at


def facts_for(home: Home, facts, *, by: str, thread: str, strand: str, gen: int | None,
              line: str, sink: str | None, at: str | None) -> list[Fact]:
    """The ``friction`` fact, and the own wish it makes when it makes one."""
    from .verbs_wishes import wish_fact
    kind = install_kind(home)
    data = {"line": line, "sink": sink or "", "thread": thread, "strand": strand,
            "version": _version(), "install": kind}
    if at:
        data["at"] = at
    if gen is not None:
        data["gen"] = gen
    friction = Fact(kind="friction", by=by, id="friction:" + mint(12), data=data)
    out = [friction]
    words = ("sort" if sink is None else
             "fix upstream" if sink == "upstream" and kind in ("upstream", "dev") else None)
    held = _open_fix(facts, at) if words == "fix upstream" else None
    if held is not None:
        # The count is the signal: a scar paid again points at its open fix, never a second row.
        friction.data["wish"] = held
    elif words is not None:
        wish = wish_fact(facts, by=by, ident="new", line=f"{words}: {line}", thread=home.main_thread(),
                         sender="self")
        wish.data["friction"] = friction.id
        friction.data["wish"] = wish.id
        out.append(wish)
    return out


def _open_fix(facts, at: str | None) -> str | None:
    """The open own wish an earlier upstream friction on this scar opened, if any."""
    from .wishes import wishes
    rows = None
    for fact in reversed(facts):
        if (fact.kind == "friction" and fact.data.get("sink") == "upstream" and fact.data.get("at") == at
                and fact.data.get("wish")):
            rows = wishes(facts) if rows is None else rows
            row = rows.get(fact.data["wish"])
            if row is not None and row.open:
                return row.id
    return None


def paid(facts, at: str) -> int:
    """How many frictions named this artifact: the times its lesson was paid for."""
    return sum(1 for f in facts if f.kind == "friction" and f.data.get("at") == at)


def said(fact: Fact, facts) -> str:
    """What the verb prints: what was recorded, copied from the facts."""
    d = fact.data
    where = d.get("sink") or "unsorted"
    text = f"{fact.id} · {where}" + (f" · {d['at']} (paid {paid(facts, d['at'])})" if d.get("at") else "")
    if d.get("wish") and not any(f.kind == "wish" and f.id == d["wish"] and f.data.get("friction") == fact.id
                                 for f in facts):
        text += f" · own wish {d['wish']} already open"
    elif d.get("wish"):
        text += f" · own wish {d['wish']} on thread {d.get('home') or 'home'}"
    elif d.get("sink") == "upstream":
        text += f" · no fix wish: this {d['install']} install opens no pull request on brnrd; the scar stays"
    return text


def record(home: Home, *, by: str, thread: str, strand: str, gen: int | None,
           line: str, sink: str | None, at: str | None) -> list[Fact]:
    from .ledger import append, read_facts
    facts = read_facts(home)
    line, at = check(home, line, sink, at)
    out = facts_for(home, facts, by=by, thread=thread, strand=strand, gen=gen, line=line, sink=sink, at=at)
    if out[0].data.get("wish"):
        out[0].data["home"] = home.main_thread()
    for fact in reversed(out):  # the wish first: the friction fact names it
        append(home, fact)
    return out


def main(argv: list[str]) -> int:
    from .port import PortError
    from .refusals import Parser, refuse
    parser = Parser(verb="friction", prog="brnrd runtime friction")
    parser.add_argument("line", help="one line: what cost you")
    parser.add_argument("--sink", choices=SINKS,
                        help="scar: a lesson for this resident; fit: a setting or rule in this self; "
                             "upstream: a change to brnrd any install would want. None: the home thread sorts it")
    parser.add_argument("--at", help="the path in the self that carries it (upstream: its scar)")
    parser.add_argument("--room")
    args = parser.parse_args(argv)
    try:
        from .ledger import read_facts
        home, by, thread, strand, gen = _who(args.room)
        out = record(home, by=by, thread=thread, strand=strand, gen=gen,
                     line=args.line, sink=args.sink, at=args.at)
    except (PortError, ValueError, OSError, subprocess.SubprocessError) as exc:
        return refuse("friction", exc)
    print(said(out[0], read_facts(home)))
    return 0


def _who(room_arg):
    from .home import home_of_room, strand_of_room
    from .lifecycle import own_thread
    from .verbs_loom import _room
    room = _room(room_arg)
    strand = strand_of_room(room)
    thread = own_thread(room)
    if not thread:
        raise ValueError("friction: this room has no thread")
    return home_of_room(room), "strand:" + strand, thread, strand, room_gen(room)


def room_gen(room: Path) -> int | None:
    from .jack import _boundary_text
    from .port import parse_boundary
    try:
        return parse_boundary(_boundary_text(room)).gen or None
    except Exception:
        return None


# --- P2: a scar's ``upstream:`` line and the fix-loaded notice -------------------------

def upstream_ref(text: str) -> int | None:
    """The brnrd pull request a scar's head names on its ``upstream:`` line."""
    lines = text.replace("\r", "").split("\n")
    if lines and lines[0] == "---" and "---" in lines[1:]:
        end = lines.index("---", 1)
        lines = lines[1:end] + [line for line in lines[end + 1:] if line.strip()][:HEAD]
    else:
        lines = [line for line in lines if line.strip()][:HEAD]
    match = _LINE.search("\n".join(lines))
    if not match:
        return None
    ref = _REF.match(match.group(1).rstrip(".,;"))
    return int(ref.group(1) or ref.group(2)) if ref else None


def scars_upstream(self_repo: Path, tip: str) -> dict[str, int]:
    """Each scar on main whose head names an upstream pull request. One grep, then a read per hit."""
    found = _git(self_repo, "grep", "-l", "-i", "-E", r"^\s*upstream:", tip, "--", SCARS)
    out = {}
    for row in found.stdout.splitlines():
        path = row.split(":", 1)[1] if row.startswith(tip + ":") else row
        if not path.endswith(".md") or path.endswith("/index.md"):
            continue
        number = upstream_ref(_git(self_repo, "show", f"{tip}:{path}").stdout)
        if number is not None:
            out[path] = number
    return out


def _owner(facts, path: str, home: Home) -> str:
    """The open thread that last recorded a friction on this scar, else the home thread.

    A settled thread boots no body (the router passes over it), so a notice there
    would sit unread: it goes home instead."""
    from .lifecycle import CLOSED, read_thread
    for fact in reversed([f for f in facts if f.kind == "friction"]):
        thread = fact.data.get("thread")
        if fact.data.get("at") == path and thread and read_thread(home, str(thread)).get("status") not in CLOSED:
            return str(thread)
    return home.main_thread()


def loaded_notices(home: Home, facts, *, root: Path | None = None,
                   scars: dict[str, int] | None = None) -> list[str]:
    """One notice per scar whose upstream fix is merged and in the code this loom runs."""
    from .home import notice_id
    from .lifecycle import main_tip
    from .loom import notice
    from .receipts import held_merge, source_root
    if scars is None:
        scars = scars_upstream(home.root / "self", main_tip(home.root / "self"))
    if not scars:
        return []
    root = source_root() if root is None else root
    install = home.install_id()
    start = next((f.data for f in reversed([f for f in facts if f.kind == "loom.started"])
                  if f.data.get("install") == install), None)
    if not start or not start.get("commit"):
        return []
    ids = {f.id for f in facts}
    sent = []
    for path, number in sorted(scars.items()):
        if notice_id("scar-loaded", f"{path}:{number}") in ids:
            continue  # told once already: no merge to look up
        merge = held_merge({"kind": "pr", "ref": str(number)}, start, root=root)
        if not merge:
            continue
        what = (f"scar {path}: its fix brnrd#{number} loaded at {str(start['commit'])[:8]} "
                f"(merge {merge[:8]}).")
        can = "probe the act it names, then delete the scar; or keep it and say why"
        if notice(home, facts, "scar-loaded", f"{path}:{number}", what, can,
                  thread=_owner(facts, path, home)):
            sent.append(path)
    return sent


class ScarWatch:
    """Reads self main's scars again only when main or the running code moved."""

    EVERY = 60.0

    def __init__(self, home: Home, log):
        self.home, self.log = home, log
        self.seen: str | None = None
        self.next = 0.0

    def step(self) -> None:
        import time
        from .ledger import read_facts
        from .lifecycle import main_tip
        if time.monotonic() < self.next or not (self.home.root / "self" / ".git").exists():
            return
        self.next = time.monotonic() + self.EVERY
        # The code this process runs is fixed at its start; only self main moves under it.
        tip = main_tip(self.home.root / "self")
        if tip == self.seen:
            return
        # Self main moves at every landing: the grep comes first, and the ledger is read
        # only when some scar names an upstream fix.
        scars = scars_upstream(self.home.root / "self", tip)
        if scars:
            loaded_notices(self.home, read_facts(self.home), scars=scars)
        self.seen = tip
