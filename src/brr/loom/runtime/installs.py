"""One install id runs on one machine, and a second machine joins with its own.

An install id is minted once per home (``loom/install-id``) and names that
home's ledger folder and its ``refs/ledger/<install>`` on the lease remote. A
``loom/`` copied to a second machine carries the id with it: both would write
one folder and push one ref, and neither would notice. So the id is bound to
where it was minted: ``loom/install-host`` holds this machine's id and the
home's path, and every shuttle commit carries the same as ``Loom-Host``. A
loom refuses to start when either names somewhere else (``refusal``).

``join`` is how a second machine gets the resident: it clones the self from
the lease remote, mints an id nobody's ledger holds, and sets ``lease_remote``,
never seeding a self of its own. The design: brnrd-self
threads/machines/design.md, section 3.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path

from .gitlease import LEASES, LEDGERS
from .home import Home

HOST_FILE = "install-host"
TRAILER = "Loom-Host: "
#: Where ``refusal`` fetches the remote's copy of our ledger ref, apart from
#: the shuttle's own ref so its parent never moves under it.
CLAIMS = "refs/claims/"

_MACHINE: str | None = None


def machine_id() -> str:
    """A stable id for this computer: the platform UUID on macOS, the
    machine-id on Linux, the host name where neither exists.
    ``BRNRD_MACHINE_ID`` overrides it (tests and rehearsals)."""
    global _MACHINE
    if override := os.environ.get("BRNRD_MACHINE_ID"):
        return override
    if _MACHINE is None:
        found = ""
        if platform.system() == "Darwin":
            try:
                out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                     capture_output=True, text=True, timeout=10).stdout
                match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
                found = match.group(1) if match else ""
            except (OSError, subprocess.SubprocessError):
                pass
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            if found:
                break
            try:
                found = Path(path).read_text().strip()
            except OSError:
                pass
        _MACHINE = found or "host:" + socket.gethostname()
    return _MACHINE


def here(home: Home) -> str:
    """This machine and this home, as ``install-host`` and ``Loom-Host`` spell it."""
    return f"{machine_id()} {Path(home.root).resolve()}"


def claim(home: Home) -> str:
    """The ``install-host`` line, written here when absent (an id minted before
    the file existed is adopted where it runs)."""
    path = home.root / "loom" / HOST_FILE
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(here(home) + "\n")
        return here(home)


def remote_claim(gate, install: str) -> str:
    """The ``Loom-Host`` on the tip of ``refs/ledger/<install>`` on the gate's
    remote; "" when the ref is absent, unread or carries none."""
    ref = LEDGERS + install
    fetched = gate.git(["fetch", "--no-tags", "--quiet", gate.remote, f"+{ref}:{CLAIMS}{install}"])
    if fetched.returncode != 0:
        return ""
    body = subprocess.run(["git", "-C", str(gate.repo), "log", "-1", "--format=%B", CLAIMS + install],
                          capture_output=True, text=True).stdout
    return next((line[len(TRAILER):].strip() for line in body.splitlines()
                 if line.startswith(TRAILER)), "")


def refusal(home: Home, gate=None) -> str:
    """Why this loom must not start, or "" when it may: the install id was
    minted for another machine or home, or the lease remote's ledger for it
    is pushed by one."""
    install = home.install_id()
    mine, ours = here(home), claim(home)
    fix = ("Each machine needs its own id: on a new machine, start from an empty home "
           "and run `brnrd runtime machines join <self-url>`. If this is the same machine "
           f"and only the home moved, delete {home.root / 'loom' / HOST_FILE}.")
    if ours != mine:
        return f"install {install} was minted at {ours!r}, not here ({mine!r}). {fix}"
    if gate is not None:
        theirs = remote_claim(gate, install)
        if theirs and theirs != mine:
            return f"{gate.remote} has {LEDGERS}{install} pushed from {theirs!r}, not here ({mine!r}). {fix}"
    return ""


class JoinError(Exception):
    pass


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", *args],
        capture_output=True, text=True, timeout=60, cwd=cwd,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def join(root: Path, url: str) -> str:
    """Make ``root`` a second machine of the resident whose self is at ``url``;
    the one-line result. Refuses a home that has a self, a remote that holds
    none, and one whose machine is not leasing on it yet: that machine routes
    on its own local lease, so a second one would lead beside it."""
    from .config import set_value
    from .selfrepo import restore_self

    root = Path(root).resolve()
    if (root / "self").exists() and any((root / "self").iterdir()):
        raise JoinError(f"{root / 'self'} already holds a self; join is for a home with none")
    listed = _git("ls-remote", url)
    if listed.returncode != 0:
        why = (listed.stderr.strip().splitlines() or [f"git ls-remote exited {listed.returncode}"])[-1]
        raise JoinError(f"could not read {url}: {why}")
    refs = {line.split("\t")[1] for line in listed.stdout.splitlines() if "\t" in line}
    if "refs/heads/main" not in refs:
        raise JoinError(f"{url} holds no self (no main branch)")
    if LEASES + "inbox" not in refs:
        raise JoinError(
            f"no loom leases on {url} yet ({LEASES}inbox is absent). On the machine brnrd runs on "
            f'now, add lease_remote = "{url}" to loom/config.toml (`brnrd runtime config edit`) '
            "and wait for its loom to take the lease; then join again.")
    claimed = {ref[len(LEDGERS):] for ref in refs if ref.startswith(LEDGERS)}
    for _ in range(16):
        home = Home(root)
        install = home.install_id()
        if install not in claimed:
            break
        if any((root / "ledger" / "facts" / install).glob("*.jsonl")):
            raise JoinError(f"install {install} is another machine's ({LEDGERS}{install} on {url}) "
                            "and this home has written facts as it; start from an empty home")
        (root / "loom" / "install-id").unlink()
        (root / "loom" / HOST_FILE).unlink(missing_ok=True)
        shutil.rmtree(root / "ledger" / "facts" / install, ignore_errors=True)
    else:
        raise JoinError("could not mint an install id no ledger on the remote holds")
    claim(home)
    sha = restore_self(root, url)
    set_value(root, "lease_remote", url)
    return (f"joined {url} as install {install} at self {sha}; lease_remote is set. "
            f"Next: `brnrd setup` for this machine's harness logins, then start the loom: it waits "
            f"for the inbox lease and runs what it is granted.")
