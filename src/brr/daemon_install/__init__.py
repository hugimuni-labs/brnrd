"""Native service-manager integration for ``brnrd daemon``."""

from __future__ import annotations

import platform
from pathlib import Path

from ..cli import brnrd_cmd
from . import linux, macos


def install(
    *,
    no_start: bool = False,
    prompt_linger: bool = True,
    assume_yes_linger: bool = False,
) -> int | None:
    if linux.supported():
        return linux.install(
            no_start=no_start,
            prompt_linger=prompt_linger,
            assume_yes_linger=assume_yes_linger,
        )
    if _is_macos():
        result = macos.install(no_start=no_start)
        print(f"[brnrd] wrote LaunchAgent: {result.plist_path}")
        print(f"[brnrd] logs: {result.log_dir}")
        if not result.started:
            print("[brnrd] launchd service written; it will load at next login")
            brnrd = brnrd_cmd()
            print(
                f"[brnrd] next: `{brnrd} daemon status`, `{brnrd} daemon logs`, "
                f"`{brnrd} daemon uninstall`",
            )
            return 0
        # `launchctl kickstart` returning is not the daemon surviving — it
        # forks the job and returns at once, so a hard exit before
        # `_write_pid` (no `AGENTS.md`) still looked like a clean install
        # here until the pidfile was actually read back (issue #1238).
        if result.alive:
            print(f"[brnrd] launchd service loaded and kickstarted — running (pid {result.pid})")
        else:
            print("[brnrd] launchd service kickstarted, but the daemon did not start.")
            _, err_log = macos.log_paths()
            print(f"[brnrd] last stderr ({err_log}):")
            print(_tail_lines(err_log))
        brnrd = brnrd_cmd()
        print(
            f"[brnrd] next: `{brnrd} daemon status`, `{brnrd} daemon logs`, "
            f"`{brnrd} daemon uninstall`",
        )
        return 0 if result.alive else 1
    _unsupported("install")


def uninstall(
    *,
    prompt_linger: bool = True,
    assume_yes_disable_linger: bool = False,
) -> int | None:
    if linux.supported():
        return linux.uninstall(
            prompt_linger=prompt_linger,
            assume_yes_disable_linger=assume_yes_disable_linger,
        )
    if _is_macos():
        result = macos.uninstall()
        if result.bootout_attempted:
            print("[brnrd] launchd service stopped if it was loaded")
        if result.removed:
            print(f"[brnrd] removed LaunchAgent: {result.plist_path}")
        else:
            print(f"[brnrd] LaunchAgent already absent: {result.plist_path}")
        return None
    _unsupported("uninstall")


def status(*, direct_brr_dir: Path | None = None) -> int:
    if linux.supported():
        if linux.service_installed():
            code = linux.status()
            _print_gate_health(direct_brr_dir)
            return code
        print("[brnrd] daemon service not installed")
        code = _print_direct_status(direct_brr_dir)
        _print_gate_health(direct_brr_dir)
        return code

    if _is_macos():
        service = macos.status()
        installed = "installed" if service.installed else "not installed"
        print(f"[brnrd] macOS LaunchAgent: {installed}")
        print(f"[brnrd] plist: {service.plist_path}")
        if service.loaded is True:
            print("[brnrd] launchd: loaded")
        elif service.loaded is False:
            print("[brnrd] launchd: not loaded")
            if service.detail:
                print(f"[brnrd] launchd detail: {service.detail}")
        else:
            print("[brnrd] launchd: unknown")
            if service.detail:
                print(f"[brnrd] launchd detail: {service.detail}")
        print(f"[brnrd] logs: {service.log_dir}")
        direct_code = _print_direct_status(direct_brr_dir)
        if service.loaded is True and direct_code != 0:
            # launchd's "loaded" only proves the job is registered, not that
            # it is alive: a `KeepAlive: {SuccessfulExit: False}` unit whose
            # program hard-exits before `_write_pid` (no `AGENTS.md`) reports
            # "loaded" forever through a throttled crash loop. `direct_code`
            # reads the pidfile the daemon process itself writes — the
            # launchd-managed process and a plain foreground one write the
            # same file (`daemon.py:330`) — so it, never `loaded`, decides
            # whether this command succeeds (issue #1238).
            print("[brnrd] launchd reports loaded, but the daemon process is not running")
            _, err_log = macos.log_paths()
            print(f"[brnrd] last stderr ({err_log}):")
            print(_tail_lines(err_log))
        _print_gate_health(direct_brr_dir)
        return direct_code

    system = platform.system() or "this platform"
    print(f"[brnrd] native service: unsupported on {system} in this build")
    code = _print_direct_status(direct_brr_dir)
    _print_gate_health(direct_brr_dir)
    return code


def logs(*, follow: bool = True, lines: int = 80) -> int | None:
    if linux.supported():
        return linux.logs(follow=follow, lines=lines)
    if _is_macos():
        macos.logs(follow=follow, lines=lines)
        return None
    _unsupported("logs")


def start_service() -> int | None:
    if linux.supported() and linux.service_installed():
        code = linux.start_service()
        if code == 0:
            print("[brnrd] daemon service started")
        return code

    if _is_macos() and macos.plist_path().exists():
        macos.start_loaded_service()
        print("[brnrd] launchd service started")
        return 0

    return None


def stop_service() -> int | None:
    if linux.supported() and linux.service_installed():
        code = linux.stop_service()
        if code == 0:
            print("[brnrd] daemon service stopped")
        return code

    if _is_macos() and macos.plist_path().exists():
        macos.stop_loaded_service()
        print("[brnrd] launchd service stopped")
        return 0

    return None


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _unsupported(action: str) -> None:
    system = platform.system() or "this platform"
    raise SystemExit(f"[brnrd] daemon {action} on {system} is not implemented yet")


def _print_direct_status(direct_brr_dir: Path | None) -> int:
    # Labelled "daemon process", not "foreground daemon": this reads the
    # same pidfile a launchd- or systemd-managed daemon writes
    # (`daemon.py:330`), so it is the one accurate liveness line whichever
    # way the process was started — the old "foreground" label read as
    # irrelevant background noise next to "launchd: loaded" when it was
    # actually the only line answering "is it alive" (issue #1238).
    if direct_brr_dir is None:
        print("[brnrd] daemon process: unavailable outside a repo")
        return 1

    from brr import daemon as daemon_mod

    pid = daemon_mod.read_pid(direct_brr_dir)
    if pid is None:
        print("[brnrd] daemon process: not running")
        return 3
    print(f"[brnrd] daemon process: running (pid {pid})")
    return 0


def _tail_lines(path: Path, *, lines: int = 20) -> str:
    """Best-effort tail of a log file for an honest failure report.

    Never raises — a missing or unreadable log is itself worth reporting
    plainly rather than crashing the status/install call that wanted it.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"  (could not read {path})"
    rows = text.splitlines()
    if not rows:
        return "  (empty)"
    return "\n".join(f"  {row}" for row in rows[-lines:])


def _print_gate_health(brr_dir: Path | None) -> None:
    if brr_dir is None:
        return

    from brr.gates import runtime

    rows = runtime.gate_health_rows(brr_dir)
    if not rows:
        print("[brnrd] gates: none configured")
        return
    print("[brnrd] gates:")
    for row in rows:
        age = "never" if row["age_seconds"] is None else f'{row["age_seconds"]}s ago'
        detail = (
            f'  - {row["gate"]}: {row["status"]}; last successful poll {age}'
        )
        if row["last_error"]:
            detail += f'; last error: {row["last_error"]}'
        print(detail)
