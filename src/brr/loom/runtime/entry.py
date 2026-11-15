"""One stable launcher: ``~/.brnrd/bin/brnrd`` runs ``~/.brnrd/current``.

Every file the loom writes that starts brnrd later (the service unit, a
room's hooks, its merge driver, the wake's verb lines) names the shim and
never this interpreter, so an interpreter or checkout that moves breaks
nothing written before it moved (product-shape design §2.1). ``current``
is the one swap point: a link to a Python prefix whose ``bin/python``
imports brr. ``BRNRD_ROOT`` moves the whole layout (tests, a second user).
"""

from __future__ import annotations

import os
import json
import shlex
import sys
from pathlib import Path

from .home import atomic_write

# What a body types: the wake puts the shim's directory first on its PATH.
VERB = "brnrd runtime"


def root() -> Path:
    return Path(os.environ.get("BRNRD_ROOT") or Path.home() / ".brnrd")


def shim() -> Path:
    return root() / "bin" / "brnrd"


def current() -> Path:
    return root() / "current"


def script() -> str:
    python = shlex.quote(str(current() / "bin" / "python"))
    guard = shlex.quote(str(root() / "launcher.py"))
    source = shlex.quote(str(root() / "launcher-source"))
    return (
        "#!/bin/sh\n"
        f"python={python}\n"
        '[ -x "$python" ] || { echo "brnrd: $python is missing; run brnrd loom install" >&2; exit 127; }\n'
        'export BRNRD_CLI_ENTRY="$0"\n'
        f'[ "$1 $2" = "runtime loom" ] && [ -f {source} ] && exec "$python" -I {guard} "$@"\n'
        'exec "$python" -m brr "$@"\n'
    )


def command(*args: str) -> str:
    """A shell line that runs ``brnrd runtime <args>`` through the shim."""
    return shlex.join([str(shim()), "runtime", *args])


def write_shim() -> bool:
    """Write the shim when it is absent or differs; True when it wrote."""
    from .receipts import source_root
    source = source_root()
    root().mkdir(parents=True, exist_ok=True)
    guard = root() / "launcher.py"
    guard_text = Path(__file__).with_name("launcher.py").read_text()
    wrote_guard = not guard.exists() or guard.read_text() != guard_text
    if wrote_guard:
        atomic_write(guard, guard_text)
    if (source / ".git").exists():
        atomic_write(root() / "launcher-source", json.dumps({"source": str(source.resolve()),
                                                            "prefix": str(prefix().resolve())}))
    else:
        (root() / "launcher-source").unlink(missing_ok=True)
    path, text = shim(), script()
    if path.is_file() and path.read_text() == text and os.access(path, os.X_OK):
        return wrote_guard
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, text)
    path.chmod(0o755)
    return True


def sound() -> bool:
    """The shim is the one we write and ``current`` has a python to run."""
    path = shim()
    guard = root() / "launcher.py"
    return (path.is_file() and path.read_text() == script() and os.access(path, os.X_OK)
            and guard.is_file() and guard.read_text() == Path(__file__).with_name("launcher.py").read_text()
            and os.access(current() / "bin" / "python", os.X_OK))


def prefix() -> Path:
    """The environment this interpreter runs in: a venv, a uv tool, a checkout's .venv.

    ``sys.executable`` unresolved, as ``bin/python`` inside it: resolving a
    venv's link would land on the base Python without brr installed.
    """
    return Path(os.path.abspath(sys.executable)).parent.parent


def point(target: Path) -> str | None:
    """Make ``current`` a link to ``target``; the old target when it changed, else None."""
    link = current()
    old = os.readlink(link) if link.is_symlink() else None
    if old is not None and Path(old).resolve() == target.resolve():
        return None
    if link.exists() and not link.is_symlink():
        raise OSError(f"{link} is not a link; move it aside and run brnrd loom install")
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(f".current-{os.getpid()}")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target)
    os.replace(tmp, link)
    return old or "nothing"


def ensure() -> dict | None:
    """The shim written and ``current`` runnable; what was done, or None when both held.

    A ``current`` that is missing or has no ``bin/python`` is linked to this
    interpreter's prefix, and the returned data says why, so the caller can
    write it down; a ``current`` that runs is someone's choice and is kept.
    """
    wrote = write_shim()
    python = current() / "bin" / "python"
    if os.access(python, os.X_OK):
        return {"shim": str(shim())} if wrote else None
    link = current()
    why = (f"current pointed at {os.readlink(link)}, which has no bin/python"
           if link.is_symlink() else f"{link} was missing")
    if not os.access(prefix() / "bin" / "python", os.X_OK):
        raise OSError(f"{why}, and this interpreter's prefix {prefix()} has no bin/python to link")
    point(prefix())
    return {"current": str(link), "to": str(prefix()), "why": why}


def reexec_argv() -> list[str] | None:
    """The argv that re-runs this process through the shim, when the shim started it.

    Python resolves a linked ``current`` into ``sys.executable``, so a re-exec
    of ``sys.executable`` would keep the old target after ``current`` moved.
    """
    argv = list(getattr(sys, "orig_argv", []))
    if argv[1:2] == ["-I"]:
        argv.pop(1)
    if argv[1:3] != ["-m", "brr"] or not os.access(shim(), os.X_OK):
        return None
    return [str(shim()), *argv[3:]]
