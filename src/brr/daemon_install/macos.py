"""macOS LaunchAgent support for ``brnrd daemon``."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

LABEL = "dev.brnrd.brr"
PLIST_NAME = f"{LABEL}.plist"

RunFn = Callable[..., subprocess.CompletedProcess]

#: Post-kickstart liveness poll ceiling, seconds. `launchctl kickstart`
#: returns the moment the job forks — a job that hard-exits before
#: `_write_pid` (no `AGENTS.md`, a crashed import) still reports a clean
#: kickstart, so `install()` must read the pidfile back before claiming
#: anything survived (issue #1238).
DEFAULT_POLL_TIMEOUT = 5.0


@dataclass(frozen=True)
class InstallResult:
    plist_path: Path
    log_dir: Path
    started: bool
    #: Confirmed alive by reading the daemon's own pidfile back within the
    #: poll window. ``None`` when ``no_start`` skipped the kickstart
    #: entirely — there was nothing to confirm.
    alive: bool | None = None
    pid: int | None = None


@dataclass(frozen=True)
class UninstallResult:
    plist_path: Path
    removed: bool
    bootout_attempted: bool


@dataclass(frozen=True)
class ServiceStatus:
    plist_path: Path
    log_dir: Path
    installed: bool
    loaded: bool | None
    detail: str


def launch_agents_dir(*, home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents"


def plist_path(*, home: Path | None = None) -> Path:
    return launch_agents_dir(home=home) / PLIST_NAME


def log_dir(*, home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "Logs" / "brr"


def log_paths(*, home: Path | None = None) -> tuple[Path, Path]:
    logs = log_dir(home=home)
    return logs / "brr.out.log", logs / "brr.err.log"


def resolve_workdir() -> Path:
    """The repository root the agent should run the daemon from.

    ``daemon up --foreground`` resolves its project from the current
    directory, and launchd starts agents from ``/`` — a plist with no
    ``WorkingDirectory`` installs a daemon that crash-loops on "Not a Git
    repository".  Freeze the repo the install ran from, the same
    install-time-snapshot contract as the binary and PATH pins.
    """
    from brr import gitops

    try:
        return gitops.ensure_git_repo()
    except RuntimeError:
        raise SystemExit(
            "[brnrd] `brnrd daemon install` must run from inside the project "
            "repository — the service is pinned to the repo it is installed "
            "from"
        )


def render_plist(
    brr_path: str | Path,
    *,
    home: Path | None = None,
    path_env: str | None = None,
    workdir: str | Path | None = None,
) -> str:
    """launchd's default PATH is ``/usr/bin:/bin:…`` — the daemon starts but
    cannot find the runner Shells (``claude``, ``codex``) its runs dispatch
    by PATH lookup. Freeze the installing shell's PATH into the agent, same
    contract as the Linux unit; re-running install refreshes it. The working
    directory is frozen the same way: launchd's default cwd is ``/``, which
    is not the project repo ``daemon up`` requires.

    No counterpart here to the Linux unit's ``MemoryHigh=``/``MemoryMax=``
    runner memory fence (``daemon_install/linux.py``, issue #1110): launchd
    has no per-job cgroup-style memory cap. ``SoftResourceLimits`` /
    ``HardResourceLimits`` exist but are a per-process ``setrlimit``, a
    cruder and differently-scoped mechanism than a job-wide memory ceiling,
    and are deliberately not used here as a substitute. macOS installs are
    unfenced against the failure mode
    ``incident-oom-cascade-salvage-on-main.md`` describes."""
    out_log, err_log = log_paths(home=home)
    path_value = path_env if path_env is not None else os.environ.get("PATH", "")
    workdir_value = str(workdir) if workdir else str(resolve_workdir())
    payload: dict[str, Any] = {
        "Label": LABEL,
        "ProgramArguments": [
            str(brr_path),
            "daemon",
            "up",
            "--foreground",
        ],
        "RunAtLoad": True,
        "KeepAlive": {
            "SuccessfulExit": False,
        },
        "WorkingDirectory": workdir_value,
        "StandardOutPath": str(out_log),
        "StandardErrorPath": str(err_log),
        "EnvironmentVariables": {
            "BRR_INSTALL_MANAGED": "1",
            **({"PATH": path_value} if path_value else {}),
        },
    }
    return plistlib.dumps(payload, sort_keys=False).decode("utf-8")


def install(
    *,
    no_start: bool = False,
    brr_path: str | Path | None = None,
    home: Path | None = None,
    workdir: str | Path | None = None,
    run: RunFn = subprocess.run,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
) -> InstallResult:
    brr_bin = str(brr_path or shutil.which("brnrd") or "")
    if not brr_bin:
        raise SystemExit("[brnrd] cannot find `brnrd` on PATH; install the CLI before registering launchd")

    launch_agents_dir(home=home).mkdir(parents=True, exist_ok=True)
    logs = log_dir(home=home)
    logs.mkdir(parents=True, exist_ok=True)

    workdir_value = Path(workdir) if workdir else resolve_workdir()

    path = plist_path(home=home)
    path.write_text(
        render_plist(brr_bin, home=home, workdir=workdir_value), encoding="utf-8"
    )

    started = False
    alive: bool | None = None
    pid: int | None = None
    if not no_start:
        _bootout(run=run, check=False)
        # `bootout` returns before launchd has always finished tearing the
        # old job down. A bootstrap in that window fails with error 5 / EIO
        # and suggests root, although this is a user-domain service. Retry
        # that one transient shape; preserve every real failure verbatim.
        _bootstrap_after_bootout(path, run=run, sleep=sleep)
        _run_launchctl(["kickstart", _gui_service()], run=run)
        started = True
        pid = _poll_for_pid(workdir_value, timeout=poll_timeout, sleep=sleep)
        alive = pid is not None

    return InstallResult(
        plist_path=path,
        log_dir=logs,
        started=started,
        alive=alive,
        pid=pid,
    )


def _bootstrap_after_bootout(
    path: Path,
    *,
    run: RunFn,
    sleep: Callable[[float], None],
    attempts: int = 4,
) -> None:
    args = ["bootstrap", _gui_domain(), str(path)]
    for attempt in range(attempts):
        result = _run_launchctl(args, run=run, check=False)
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout or "").strip()
        transient_eio = "Bootstrap failed: 5" in detail or "Input/output error" in detail
        if not transient_eio or attempt == attempts - 1:
            raise SystemExit(detail or f"[brnrd] launchctl {' '.join(args)} failed")
        sleep(0.25)


def _poll_for_pid(
    workdir: Path,
    *,
    timeout: float,
    sleep: Callable[[float], None],
    poll_interval: float = 0.25,
) -> int | None:
    """Poll the daemon's own pidfile for up to *timeout* seconds.

    Reads the exact file `daemon.read_pid` / `brnrd daemon status` read
    (``daemon.py:330`` — the launchd-managed process and a plain foreground
    one write the same path), so a ``True`` here means the same thing a
    later ``status`` call would report. A *workdir* that turns out not to
    be a usable git checkout (a test double, a repo torn down mid-call)
    fails the first read rather than raising — this is a liveness probe,
    not a checkout validator.
    """
    from brr import daemon as daemon_mod
    from brr import gitops

    try:
        brr_dir = gitops.shared_brr_dir(Path(workdir))
    except OSError:
        return None

    elapsed = 0.0
    while True:
        pid = daemon_mod.read_pid(brr_dir)
        if pid is not None:
            return pid
        if elapsed >= timeout:
            return None
        sleep(poll_interval)
        elapsed += poll_interval


def uninstall(
    *,
    home: Path | None = None,
    run: RunFn = subprocess.run,
) -> UninstallResult:
    _bootout(run=run, check=False)
    path = plist_path(home=home)
    removed = path.exists()
    path.unlink(missing_ok=True)
    return UninstallResult(path, removed, True)


def status(
    *,
    home: Path | None = None,
    run: RunFn = subprocess.run,
) -> ServiceStatus:
    path = plist_path(home=home)
    loaded: bool | None = None
    detail = ""

    if path.exists():
        result = run(
            ["launchctl", "print", _gui_service()],
            check=False,
            capture_output=True,
            text=True,
        )
        loaded = result.returncode == 0
        if not loaded:
            detail = (result.stderr or result.stdout or "").strip()

    return ServiceStatus(
        plist_path=path,
        log_dir=log_dir(home=home),
        installed=path.exists(),
        loaded=loaded,
        detail=detail,
    )


def logs(
    *,
    follow: bool = True,
    lines: int = 80,
    home: Path | None = None,
    run: RunFn = subprocess.run,
) -> None:
    log_dir(home=home).mkdir(parents=True, exist_ok=True)
    out_log, err_log = log_paths(home=home)
    for path in (out_log, err_log):
        path.touch(exist_ok=True)
    cmd = ["tail", "-n", str(lines)]
    if follow:
        cmd.append("-F")
    cmd.extend([str(out_log), str(err_log)])
    run(cmd, check=False)


def start_loaded_service(*, run: RunFn = subprocess.run) -> None:
    if not plist_path().exists():
        return
    _run_launchctl(
        ["bootstrap", _gui_domain(), str(plist_path())],
        run=run,
        check=False,
    )
    _run_launchctl(["kickstart", _gui_service()], run=run)


def stop_loaded_service(*, run: RunFn = subprocess.run) -> None:
    _bootout(run=run, check=False)


def _bootout(*, run: RunFn, check: bool) -> None:
    _run_launchctl(["bootout", _gui_service()], run=run, check=check)


def _run_launchctl(
    args: list[str],
    *,
    run: RunFn,
    check: bool = True,
) -> subprocess.CompletedProcess:
    result = run(
        ["launchctl", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit(detail or f"[brnrd] launchctl {' '.join(args)} failed")
    return result


def _gui_domain() -> str:
    return f"gui/{os.getuid()}"


def _gui_service() -> str:
    return f"{_gui_domain()}/{LABEL}"
