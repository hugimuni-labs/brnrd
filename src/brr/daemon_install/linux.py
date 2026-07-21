"""Linux systemd user-service integration for ``brnrd daemon``."""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable


SERVICE_UNIT = "brr.service"
SYSTEMD_UNIT = """[Unit]
Description=brnrd daemon (machine-scoped multi-project multiplexer)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start} daemon up --foreground
Restart=on-failure
RestartSec=5s
Environment=BRR_INSTALL_MANAGED=1
Environment="PATH={path_env}"

[Install]
WantedBy=default.target
"""


def supported() -> bool:
    return sys.platform.startswith("linux")


def xdg_config_home() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".config"


def xdg_state_home() -> Path:
    raw = os.environ.get("XDG_STATE_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".local" / "state"


def unit_path() -> Path:
    return xdg_config_home() / "systemd" / "user" / SERVICE_UNIT


def projects_registry_path() -> Path:
    return xdg_config_home() / "brr" / "projects.toml"


def linger_marker_path() -> Path:
    return xdg_state_home() / "brr" / "systemd-linger-enabled-by-brr"


def resolve_brr_bin() -> str:
    """The absolute path of the ``brnrd`` entrypoint the service should run.

    The systemd user manager's PATH is minimal (often not even
    ``~/.local/bin``, never a venv or nvm), so a template that says
    ``/usr/bin/env brnrd`` installs a service that cannot start on the very
    host where ``brnrd daemon install`` just succeeded.  Pin the binary that
    is running the install instead — the same contract the macOS installer
    has always used.
    """
    found = shutil.which("brnrd")
    if found:
        return str(Path(found).resolve())
    raise SystemExit(
        "[brnrd] cannot find `brnrd` on PATH; install the CLI before "
        "registering the systemd service"
    )


def _systemd_escape(value: str) -> str:
    """Escape a value for a quoted systemd ``Environment=`` assignment.

    ``%`` is a unit-file specifier and doubles; backslash and double quote
    follow systemd's quoted-string rules.
    """
    return (
        value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    )


def render_systemd_unit(
    brr_path: str | Path | None = None,
    *,
    path_env: str | None = None,
) -> str:
    """Render the unit with the resolved entrypoint and the installing
    shell's PATH frozen in.

    The daemon dispatches runner Shells (``claude``, ``codex``, …) by PATH
    lookup, and its environment snapshot is what every run inherits — under
    the user manager's thin default PATH those CLIs vanish even when the
    daemon itself starts.  Freezing the install-time PATH hands the service
    exactly the environment the install was verified in; re-running
    ``brnrd daemon install`` refreshes it.
    """
    exec_start = str(brr_path) if brr_path else resolve_brr_bin()
    path_value = path_env if path_env is not None else os.environ.get("PATH", "")
    return SYSTEMD_UNIT.format(
        exec_start=_systemd_escape(exec_start),
        path_env=_systemd_escape(path_value),
    )


def service_installed() -> bool:
    return unit_path().exists()


def write_unit_file() -> Path:
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_systemd_unit(), encoding="utf-8")
    return path


def ensure_projects_registry() -> Path:
    path = projects_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, check=False)
    except FileNotFoundError:
        raise SystemExit(f"[brnrd] required command not found: {command[0]}")
    if check and result.returncode != 0:
        rendered = " ".join(command)
        raise SystemExit(f"[brnrd] command failed ({result.returncode}): {rendered}")
    return result


def _capture(command: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def linger_enabled(user: str) -> bool:
    result = _capture(
        ["loginctl", "show-user", user, "--property=Linger", "--value"],
    )
    return result.returncode == 0 and result.stdout.strip().lower() in {
        "yes",
        "true",
        "1",
    }


def _confirm(
    prompt: str,
    *,
    default: bool,
    input_fn: Callable[[str], str] = input,
) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        choice = input_fn(f"{prompt} [{hint}]: ").strip().lower()
    except EOFError:
        return default
    if not choice:
        return default
    return choice in {"y", "yes"}


def maybe_enable_linger(
    *,
    user: str | None = None,
    prompt: bool = True,
    assume_yes: bool = False,
) -> bool:
    user = user or os.environ.get("USER") or getpass.getuser()
    if linger_enabled(user):
        return False

    if assume_yes:
        enable = True
    elif not prompt or not sys.stdin.isatty():
        print(
            "[brnrd] linger is not enabled; the service may wait for first login "
            "before starting. Run `sudo loginctl enable-linger $USER` to "
            "change that."
        )
        return False
    else:
        enable = _confirm(
            "Enable linger? lets brr start at boot before you log in; "
            "one-time setting per user; uses sudo",
            default=True,
        )

    if not enable:
        print("[brnrd] skipping linger; brnrd will start after user login")
        return False

    _run(["sudo", "loginctl", "enable-linger", user])
    marker = linger_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(user + "\n", encoding="utf-8")
    print(f"[brnrd] enabled linger for {user}")
    return True


def maybe_disable_linger(
    *,
    prompt: bool = True,
    assume_yes: bool = False,
) -> bool:
    marker = linger_marker_path()
    if not marker.exists():
        return False

    user = marker.read_text(encoding="utf-8").strip() or (
        os.environ.get("USER") or getpass.getuser()
    )
    if assume_yes:
        disable = True
    elif not prompt or not sys.stdin.isatty():
        print(
            "[brnrd] leaving linger enabled; brr enabled it earlier, but other "
            "user services may rely on it."
        )
        marker.unlink(missing_ok=True)
        return False
    else:
        disable = _confirm(
            "Disable linger? brr enabled it earlier, but other user services "
            "may rely on it",
            default=False,
        )

    if disable:
        _run(["sudo", "loginctl", "disable-linger", user], check=False)
        print(f"[brnrd] disabled linger for {user}")
    else:
        print("[brnrd] leaving linger enabled")
    marker.unlink(missing_ok=True)
    return disable


def install(
    *,
    no_start: bool = False,
    prompt_linger: bool = True,
    assume_yes_linger: bool = False,
) -> None:
    if not supported():
        raise SystemExit("[brnrd] daemon install on this platform is not implemented yet")

    service_path = write_unit_file()
    registry_path = ensure_projects_registry()
    print(f"[brnrd] wrote {service_path}")

    maybe_enable_linger(prompt=prompt_linger, assume_yes=assume_yes_linger)

    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", SERVICE_UNIT])
    if not no_start:
        _run(["systemctl", "--user", "start", SERVICE_UNIT])

    if "[[projects]]" in registry_path.read_text(encoding="utf-8"):
        print(f"[brnrd] project registry: {registry_path}")
    else:
        print("[brnrd] no projects registered yet — run `brnrd init` in a repo to add one")
    print("[brnrd] next: `brnrd daemon status`, `brnrd daemon logs`, `brnrd daemon uninstall`")


def uninstall(
    *,
    prompt_linger: bool = True,
    assume_yes_disable_linger: bool = False,
) -> None:
    if not supported():
        raise SystemExit("[brnrd] daemon uninstall on this platform is not implemented yet")

    _run(["systemctl", "--user", "stop", SERVICE_UNIT], check=False)
    _run(["systemctl", "--user", "disable", SERVICE_UNIT], check=False)
    unit_path().unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"], check=False)
    maybe_disable_linger(
        prompt=prompt_linger,
        assume_yes=assume_yes_disable_linger,
    )
    print("[brnrd] daemon service uninstalled")


def start_service() -> int:
    result = _run(["systemctl", "--user", "start", SERVICE_UNIT], check=False)
    return result.returncode


def stop_service() -> int:
    result = _run(["systemctl", "--user", "stop", SERVICE_UNIT], check=False)
    return result.returncode


def status() -> int:
    result = _run(
        ["systemctl", "--user", "status", SERVICE_UNIT, "--no-pager"],
        check=False,
    )
    return result.returncode


def logs(*, follow: bool = True, lines: int = 80) -> int:
    command = ["journalctl", "--user", "-u", "brr", "-n", str(lines)]
    if follow:
        command.append("-f")
    result = _run(command, check=False)
    return result.returncode
