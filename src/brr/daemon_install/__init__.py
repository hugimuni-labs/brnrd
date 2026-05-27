"""Native service-manager integration for ``brr daemon``."""

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
        print(f"[brr] wrote LaunchAgent: {result.plist_path}")
        print(f"[brr] logs: {result.log_dir}")
        if result.started:
            print("[brr] launchd service loaded and kickstarted")
        else:
            print("[brr] launchd service written; it will load at next login")
        _print_projects(result.enabled_projects)
        print(
            "[brr] next: `brr daemon status`, `brr daemon logs`, "
            "`brr daemon uninstall`",
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
            print("[brr] launchd service stopped if it was loaded")
        if result.removed:
            print(f"[brr] removed LaunchAgent: {result.plist_path}")
        else:
            print(f"[brr] LaunchAgent already absent: {result.plist_path}")
        print("[brr] project registry and account binding were left in place")
        return None
    _unsupported("uninstall")


def status(*, direct_brr_dir: Path | None = None) -> int:
    if linux.supported():
        if linux.service_installed():
            return linux.status()
        print("[brr] daemon service not installed")
        return _print_direct_status(direct_brr_dir)

    if _is_macos():
        service = macos.status()
        installed = "installed" if service.installed else "not installed"
        print(f"[brr] macOS LaunchAgent: {installed}")
        print(f"[brr] plist: {service.plist_path}")
        if service.loaded is True:
            print("[brr] launchd: loaded")
        elif service.loaded is False:
            print("[brr] launchd: not loaded")
            if service.detail:
                print(f"[brr] launchd detail: {service.detail}")
        else:
            print("[brr] launchd: unknown")
            if service.detail:
                print(f"[brr] launchd detail: {service.detail}")
        print(f"[brr] logs: {service.log_dir}")
        _print_projects(service.enabled_projects)
        direct_code = _print_direct_status(direct_brr_dir)
        if service.loaded is True:
            return 0
        if service.installed:
            return 3
        return direct_code

    system = platform.system() or "this platform"
    print(f"[brr] native service: unsupported on {system} in this build")
    return _print_direct_status(direct_brr_dir)


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
            print("[brr] daemon service started")
        return code

    if _is_macos() and macos.plist_path().exists():
        macos.start_loaded_service()
        print("[brr] launchd service started")
        return 0

    return None


def stop_service() -> int | None:
    if linux.supported() and linux.service_installed():
        code = linux.stop_service()
        if code == 0:
            print("[brr] daemon service stopped")
        return code

    if _is_macos() and macos.plist_path().exists():
        macos.stop_loaded_service()
        print("[brr] launchd service stopped")
        return 0

    return None


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _unsupported(action: str) -> None:
    system = platform.system() or "this platform"
    raise SystemExit(f"[brr] daemon {action} on {system} is not implemented yet")


def _print_projects(projects: list[Path]) -> None:
    if projects:
        print("[brr] registered projects:")
        for project in projects:
            print(f"  - {project}")
    else:
        print("[brr] no projects registered yet - run `brr init` in a repo to add one")


def _print_direct_status(direct_brr_dir: Path | None) -> int:
    if direct_brr_dir is None:
        print("[brr] foreground daemon: unavailable outside a repo")
        return 1

    from brr import daemon as daemon_mod

    pid = daemon_mod.read_pid(direct_brr_dir)
    if pid is None:
        print("[brr] foreground daemon: not running")
        return 3
    print(f"[brr] foreground daemon: running (pid {pid})")
    return 0
