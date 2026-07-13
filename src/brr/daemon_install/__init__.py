"""Native service-manager integration for ``brnrd daemon``."""

from __future__ import annotations

import platform
from pathlib import Path

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
        if result.started:
            print("[brnrd] launchd service loaded and kickstarted")
        else:
            print("[brnrd] launchd service written; it will load at next login")
        _print_projects(result.enabled_projects)
        print(
            "[brnrd] next: `brnrd daemon status`, `brnrd daemon logs`, "
            "`brnrd daemon uninstall`",
        )
        return None
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
        print("[brnrd] project registry and account binding were left in place")
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
        _print_projects(service.enabled_projects)
        direct_code = _print_direct_status(direct_brr_dir)
        _print_gate_health(direct_brr_dir)
        if service.loaded is True:
            return 0
        if service.installed:
            return 3
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


def _print_projects(projects: list[Path]) -> None:
    if projects:
        print("[brnrd] registered projects:")
        for project in projects:
            print(f"  - {project}")
    else:
        print("[brnrd] no projects registered yet - run `brnrd init` in a repo to add one")


def _print_direct_status(direct_brr_dir: Path | None) -> int:
    if direct_brr_dir is None:
        print("[brnrd] foreground daemon: unavailable outside a repo")
        return 1

    from brr import daemon as daemon_mod

    pid = daemon_mod.read_pid(direct_brr_dir)
    if pid is None:
        print("[brnrd] foreground daemon: not running")
        return 3
    print(f"[brnrd] foreground daemon: running (pid {pid})")
    return 0


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
