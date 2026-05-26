"""macOS LaunchAgent support for ``brr daemon``."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:  # Python 3.11+. brr still supports 3.10, so this stays optional.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    tomllib = None  # type: ignore[assignment]


LABEL = "dev.brnrd.brr"
PLIST_NAME = f"{LABEL}.plist"

RunFn = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class InstallResult:
    plist_path: Path
    log_dir: Path
    started: bool
    enabled_projects: list[Path]


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
    enabled_projects: list[Path]


def launch_agents_dir(*, home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents"


def plist_path(*, home: Path | None = None) -> Path:
    return launch_agents_dir(home=home) / PLIST_NAME


def log_dir(*, home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "Logs" / "brr"


def log_paths(*, home: Path | None = None) -> tuple[Path, Path]:
    logs = log_dir(home=home)
    return logs / "brr.out.log", logs / "brr.err.log"


def project_registry_path(*, config_home: Path | None = None) -> Path:
    base = config_home
    if base is None:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "brr" / "projects.toml"


def ensure_project_registry(*, config_home: Path | None = None) -> Path:
    path = project_registry_path(config_home=config_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def enabled_projects(*, config_home: Path | None = None) -> list[Path]:
    path = project_registry_path(config_home=config_home)
    if not path.exists() or tomllib is None:
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return []
    projects = data.get("projects", [])
    if not isinstance(projects, list):
        return []

    enabled: list[Path] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        if project.get("enabled", True) is not True:
            continue
        raw_path = project.get("path")
        if isinstance(raw_path, str) and raw_path:
            enabled.append(Path(raw_path))
    return enabled


def render_plist(
    brr_path: str | Path,
    *,
    home: Path | None = None,
) -> str:
    out_log, err_log = log_paths(home=home)
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
        "StandardOutPath": str(out_log),
        "StandardErrorPath": str(err_log),
        "EnvironmentVariables": {
            "BRR_INSTALL_MANAGED": "1",
        },
    }
    return plistlib.dumps(payload, sort_keys=False).decode("utf-8")


def install(
    *,
    no_start: bool = False,
    brr_path: str | Path | None = None,
    home: Path | None = None,
    config_home: Path | None = None,
    run: RunFn = subprocess.run,
) -> InstallResult:
    brr_bin = str(brr_path or shutil.which("brr") or "")
    if not brr_bin:
        raise SystemExit("[brr] cannot find `brr` on PATH; install the CLI before registering launchd")

    ensure_project_registry(config_home=config_home)
    launch_agents_dir(home=home).mkdir(parents=True, exist_ok=True)
    logs = log_dir(home=home)
    logs.mkdir(parents=True, exist_ok=True)

    path = plist_path(home=home)
    path.write_text(render_plist(brr_bin, home=home), encoding="utf-8")

    started = False
    if not no_start:
        _bootout(run=run, check=False)
        _run_launchctl(["bootstrap", _gui_domain(), str(path)], run=run)
        _run_launchctl(["kickstart", _gui_service()], run=run)
        started = True

    return InstallResult(
        plist_path=path,
        log_dir=logs,
        started=started,
        enabled_projects=enabled_projects(config_home=config_home),
    )


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
    config_home: Path | None = None,
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
        enabled_projects=enabled_projects(config_home=config_home),
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
        raise SystemExit(detail or f"[brr] launchctl {' '.join(args)} failed")
    return result


def _gui_domain() -> str:
    return f"gui/{os.getuid()}"


def _gui_service() -> str:
    return f"{_gui_domain()}/{LABEL}"
