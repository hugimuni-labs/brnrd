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

#: How long to wait for `bootout` to actually remove the job from the user
#: domain before bootstrapping over it. The old budget was a blind
#: 4 x 0.25s retry *after* bootout returned; a daemon with live run
#: children outlasts it, and every attempt inside that window comes back
#: error 5 / EIO (measured 2026-08-29: `brnrd connect` in a second repo on
#: this machine, which then died on launchd's advice to try again as root).
BOOTOUT_SETTLE_TIMEOUT = 10.0

#: Backstop retries for a bootstrap that still hits EIO after the settle
#: wait, backing off exponentially from 0.25s.
BOOTSTRAP_ATTEMPTS = 5


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
    #: launchd's own words when the job could not be brought up, carried
    #: instead of raised. `brnrd connect` renders a "paired, but the
    #: background service did not come up" branch and then finishes repo
    #: setup; a `SystemExit` out of `install` vaulted over both, so a
    #: machine that lost the bootstrap race ended up paired, serviceless,
    #: *and* uninitialised (2026-08-29).
    error: str | None = None


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
    #: ``WorkingDirectory`` out of the installed plist — the repo this
    #: machine's single LaunchAgent calls home, which is *not* necessarily
    #: the repo the reader is standing in. The daemon writes its pidfile
    #: under that repo's ``.brr/``, so a status read from anywhere else
    #: finds no pidfile and used to conclude "not running".
    workdir: Path | None = None


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
    error: str | None = None
    if not no_start:
        _bootout(run=run, check=False)
        # `bootout` is a request, not a receipt. Wait for the job to leave
        # the domain before bootstrapping over it, rather than bootstrapping
        # into the teardown window and calling the resulting EIO transient.
        _await_job_gone(run=run, sleep=sleep)
        error = _bootstrap_after_bootout(path, run=run, sleep=sleep)
        if error is None:
            _run_launchctl(["kickstart", _gui_service()], run=run)
            started = True
            pid = _poll_for_pid(workdir_value, timeout=poll_timeout, sleep=sleep)
            alive = pid is not None
        else:
            alive = False

    return InstallResult(
        plist_path=path,
        log_dir=logs,
        started=started,
        alive=alive,
        pid=pid,
        error=error,
    )


def _await_job_gone(
    *,
    run: RunFn,
    sleep: Callable[[float], None],
    timeout: float = BOOTOUT_SETTLE_TIMEOUT,
    poll_interval: float = 0.2,
) -> bool:
    """Block until launchd no longer knows the job, or *timeout* passes.

    For a daemon with live run children the job stays in the user domain
    for seconds after `bootout` returns, and the whole EIO class below is
    a bootstrap landing inside that window. Read the state back —
    `launchctl print` starts failing once the job is gone — instead of
    sleeping a guessed interval and hoping.

    Returns whether the job actually left. ``False`` is not fatal: the
    bootstrap retry below still gets its turn.
    """
    elapsed = 0.0
    while True:
        result = run(
            ["launchctl", "print", _gui_service()],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return True
        if elapsed >= timeout:
            return False
        sleep(poll_interval)
        elapsed += poll_interval


def _bootstrap_after_bootout(
    path: Path,
    *,
    run: RunFn,
    sleep: Callable[[float], None],
    attempts: int = BOOTSTRAP_ATTEMPTS,
) -> str | None:
    """Bootstrap the job; ``None`` on success, else the failure detail.

    Returns rather than raises, so `install` can report a service that did
    not come up without taking its caller down with it.
    """
    args = ["bootstrap", _gui_domain(), str(path)]
    delay = 0.25
    for attempt in range(attempts):
        result = _run_launchctl(args, run=run, check=False)
        if result.returncode == 0:
            return None
        detail = (result.stderr or result.stdout or "").strip()
        transient_eio = "Bootstrap failed: 5" in detail or "Input/output error" in detail
        if not transient_eio:
            return detail or f"[brnrd] launchctl {' '.join(args)} failed"
        if attempt == attempts - 1:
            # Never hand launchd's last line back unqualified: it ends in
            # "Try re-running the command as root", and root is the one
            # thing that cannot help a `gui/<uid>` domain. Name the real
            # cause and the act that clears it.
            return (
                "launchd would not take the job (error 5 / EIO): the "
                "previous daemon was still shutting down. This is a "
                "user-domain service, so running as root will not help. "
                "Wait for the old daemon to exit, then re-run "
                "`brnrd daemon install` from the checkout the service "
                "should run from.\n"
                f"  launchd said: {detail}"
            )
        sleep(delay)
        delay *= 2
    return None


def installed_workdir(*, home: Path | None = None) -> Path | None:
    """``WorkingDirectory`` out of the installed plist, or ``None``.

    One machine has one ``dev.brnrd.brr``. Which repo it runs from is an
    install-time snapshot (:func:`resolve_workdir`), so a reader that wants
    to know where the daemon's pidfile landed has to ask the plist instead
    of assuming its own checkout.
    """
    try:
        payload = plistlib.loads(plist_path(home=home).read_bytes())
    except (OSError, ValueError):
        return None
    raw = payload.get("WorkingDirectory")
    return Path(raw) if isinstance(raw, str) and raw else None


def daemon_pid_for_workdir(workdir: Path | None) -> int | None:
    """The live pid of the daemon that calls *workdir* home, if any.

    Reads the same pidfile `brnrd daemon status` reads, only under the repo
    the plist names rather than the repo the caller happens to stand in.
    """
    if workdir is None:
        return None
    from brr import daemon as daemon_mod
    from brr import gitops

    try:
        brr_dir = gitops.shared_brr_dir(workdir)
        return daemon_mod.read_pid(brr_dir)
    except (OSError, RuntimeError):
        return None


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
        workdir=installed_workdir(home=home),
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
