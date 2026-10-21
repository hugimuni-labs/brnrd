"""The room's self clone keeps up with main without a body committing by hand.

The jack calls two doors. ``land_at_stop`` commits the clone's tracked
changes when the body stops and lands them through ``send_to_self``, the
same door and push lock as ``send-self``. ``pull_at_boundary`` rebases a
clean clone onto main at the body's tool boundaries. Untracked files are
never added: a file the body did not track is not the body's to publish.

A conflict the merge drivers cannot resolve aborts the rebase, so the room
stands on its own commit again, and leaves ``port/out/sync-*.json``. The
loom's ingest turns that into a notice to the strand's thread. Nothing here
raises into the jack: ``jack-errors.log`` taints a strand, so trouble goes
to ``port/sync-errors.log`` and the ``sync`` line.

Config ``self_sync = off`` turns both doors off.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from .home import atomic_write, mint
from .selfrepo import SelfError, git

LAND_MESSAGE = "self-sync: land at stop"


def _enabled(room: Path) -> bool:
    from .config import load_config
    from .home import home_of_room
    return load_config(home_of_room(room).root).self_sync != "off"


def _clone(room: Path) -> Path | None:
    clone = room / "self"
    return clone if (clone / ".git").exists() else None


def _rebasing(clone: Path) -> bool:
    from .merge import _rebasing
    return _rebasing(clone)


def _dirty(clone: Path) -> list[str]:
    """Tracked paths that differ from ``HEAD``, staged or not. Untracked ones do not count."""
    out = git(clone, "status", "--porcelain", "-z", "--untracked-files=no").stdout
    return [entry[3:] for entry in out.split("\0") if entry[3:]]


def _short(clone: Path, ref: str = "HEAD") -> str:
    return git(clone, "rev-parse", "--short", ref).stdout.strip()


def _say(room: Path, line: str) -> None:
    from .jack import _set_state
    _set_state(room, sync=line, sync_at=time.time())


def _log(room: Path) -> str:
    path = room / "port" / "sync-errors.log"
    try:
        with path.open("a") as handle:
            handle.write(traceback.format_exc())
    except OSError:
        pass
    return f"held: error, see {path}"


def _conflict(room: Path, clone: Path, what: str, files: tuple[str, ...], detail: str = "") -> str:
    """Put the room back on its own commit and leave the loom a port file."""
    if _rebasing(clone):
        git(clone, "rebase", "--abort", check=False)
    from .merge import _write_stops
    _write_stops(clone, 0)
    base = git(clone, "rev-parse", "origin/main", check=False).stdout.strip()
    stem = mint(5)
    atomic_write(room / "port" / "out" / f"sync-{stem}.json", json.dumps({
        "what": what, "files": list(files), "base": base, "detail": detail[:2000],
    }, separators=(",", ":")) + "\n")
    named = ", ".join(files) or next(iter(detail.strip().splitlines()), "")
    return f"{what}: {named}" if named else what


def _tainted(room: Path) -> bool:
    from .home import strand_of_room
    from .port import _current_label
    return bool(_current_label(room / "self", strand_of_room(room)).taint)


def land_at_stop(room: Path) -> str:
    """Commit tracked changes and land everything ``origin/main`` lacks. Returns the ``sync`` line, or ""."""
    room = Path(room)
    try:
        clone = _clone(room)
        if clone is None or not _enabled(room):
            return ""
        if _rebasing(clone):
            # The body is mid-resolution; its own send-self finishes the job.
            line = "held: rebase in progress"
            _say(room, line)
            return line
        if _delegates_live(room):
            # A delegate's half-made edit is not the body's to land.
            line = "held: a delegate is at work"
            _say(room, line)
            return line
        changed = _dirty(clone)
        if changed:
            git(clone, "add", "-u")
            git(clone, "commit", "-m", LAND_MESSAGE)
        ahead = git(clone, "rev-list", "--count", "origin/main..HEAD").stdout.strip()
        if ahead == "0":
            return ""
        if _tainted(room):
            line = "held: tainted strand, not landed"
            _say(room, line)
            return line
        from .merge import send_to_self
        try:
            outcome = send_to_self(clone)
        except SelfError as exc:
            # A rebase git would not start, a guard, a push that failed: the body must hear it.
            line = _conflict(room, clone, "land refused", (), str(exc))
            _say(room, line)
            return line
        if outcome.status == "merged":
            line = f"landed {outcome.sha[:7]}"
        elif outcome.status == "refused":
            line = _conflict(room, clone, "refused", (), outcome.stderr)
        else:
            line = _conflict(room, clone, "conflict", outcome.files)
        _say(room, line)
        return line
    except Exception:
        line = _log(room)
        _say(room, line)
        return line


def _main_moved(clone: Path) -> bool:
    """Whether the self's ``main`` is ahead of what this clone last fetched.

    Read from the self's own ref, one ``rev-parse``. A remote that is not a
    local directory is not polled at every tool call.
    """
    url = git(clone, "remote", "get-url", "origin", check=False).stdout.strip()
    source = Path(url[7:] if url.startswith("file://") else url)
    if not url or not source.is_absolute() or not source.is_dir():
        return False
    theirs = git(source, "rev-parse", "--verify", "--quiet", "refs/heads/main", check=False).stdout.strip()
    ours = git(clone, "rev-parse", "--verify", "--quiet", "origin/main", check=False).stdout.strip()
    return bool(theirs) and theirs != ours


def _delegates_live(room: Path) -> bool:
    live = room / "port" / "live-delegates"
    return live.is_dir() and any(live.iterdir())


def pull_at_boundary(room: Path) -> str:
    """Rebase a clean clone onto a main that moved. Returns what to tell the body, or ""."""
    room = Path(room)
    try:
        clone = _clone(room)
        if clone is None or not _enabled(room) or not _main_moved(clone):
            return ""
        # Mid-edit, mid-resolution, or a delegate at work: leave the tree alone.
        if (_rebasing(clone) or _delegates_live(room)
                or Path(git(clone, "rev-parse", "--absolute-git-dir").stdout.strip(), "index.lock").exists()
                or _dirty(clone)):
            return ""
        before = git(clone, "rev-parse", "HEAD").stdout.strip()
        git(clone, "fetch", "origin", "main")
        proc = git(clone, "rebase", "origin/main", check=False)
        if proc.returncode != 0:
            from .merge import _text, _unmerged
            files = _unmerged(clone) if _rebasing(clone) else ()
            line = _conflict(room, clone, "conflict" if files else "pull refused", files,
                             "" if files else _text(proc))
            _say(room, line)
            return f"self-sync: {line}; the clone is back on {_short(clone)}. A notice follows."
        names = git(clone, "diff", "--name-only", before, "HEAD").stdout.split()
        if not names:
            return ""
        line = f"pulled {_short(clone, 'origin/main')}"
        _say(room, line)
        shown = ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
        return f"self-sync: {line} from main into this clone; changed: {shown}"
    except Exception:
        line = _log(room)
        _say(room, line)
        return f"self-sync: {line}"


def sync_line(room: Path) -> str:
    """The status line's ``sync`` part: what self-sync last did in this room."""
    try:
        return str(json.loads((Path(room) / "port" / "jack-state.json").read_text()).get("sync") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def notice_from_port(home, facts, strand: str, text: str) -> None:
    """The loom's side: a ``sync-*.json`` becomes one notice per condition."""
    from .loom import notice
    from .project import sender_threads
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("sync file is not an object")
    files = [str(name) for name in payload.get("files") or ()]
    what = str(payload.get("what") or "conflict")
    base = str(payload.get("base") or "")
    detail = str(payload.get("detail") or "").strip()
    thread = sender_threads(facts).get(strand)
    if what == "conflict":
        said = (f"self-sync could not land or pull {strand}'s self clone: main {base[:7]} conflicts "
                f"in {', '.join(files)}. The rebase was aborted; the room is on its own commit.")
    else:
        said = f"self-sync: {what} for {strand}'s self clone at main {base[:7]}:\n{detail}"
    if what == "conflict":
        can = ("resolve in the room: `python -m brr.loom.runtime send-self --room .` rebases and stops on the "
               "files above; fix them, `git add`, and send-self again")
    else:
        can = "fix what the lines above name, then land with `python -m brr.loom.runtime send-self --room .`"
    notice(home, facts, "self-sync", f"{strand}:{what}:{base}:{' '.join(sorted(files))}", said, can,
           thread=thread)
