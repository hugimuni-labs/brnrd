"""Linux systemd user-service integration for ``brnrd daemon``."""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ..cli import brnrd_cmd


SERVICE_UNIT = "brr.service"
SYSTEMD_UNIT = """[Unit]
Description=brnrd daemon (machine-scoped multi-project multiplexer)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={workdir}
ExecStart={exec_start} daemon up --foreground
Restart=on-failure
RestartSec=5s
# A runner subprocess OOM-killed by the kernel must not tear down the whole
# daemon: the default OOMPolicy=stop turns one bloated runner into a full
# unit stop, which SIGKILLs every *other* in-flight run on the way down
# (2026-08-04: a 10.7 GB runner drew the OOM killer, systemd stopped the
# unit, and an unrelated healthy run died with it). The daemon's give-up
# path already handles a dead runner gracefully — salvage, failure packet,
# retry accounting — so the right unit-level policy is to keep the daemon
# alive and let it grieve one runner at a time.
OOMPolicy=continue
# The fence below OOMPolicy: that policy only decides what happens *after*
# the kernel OOM-killer has already picked a victim process (no notion of
# "which one is disposable here" — it took an unrelated healthy run down
# with the runaway one in the same incident, see
# incident-oom-cascade-salvage-on-main.md / issue #1110). MemoryHigh
# throttles the cgroup's reclaimable memory before that point; MemoryMax
# has systemd kill the cgroup outright rather than leaving the choice to
# the kernel. Defaults below are sized to the incident host (30 GB box,
# ~10 GB desktop baseline, ~5 GB real headroom) — override per machine via
# `.brr/config`: `daemon.memory_high=<value>` / `daemon.memory_max=<value>`
# (any systemd memory value, including `infinity` to lift one half of the
# fence entirely). See `resolve_memory_limits` in daemon_install/linux.py.
MemoryHigh={memory_high}
MemoryMax={memory_max}
Environment=BRR_INSTALL_MANAGED=1
Environment="PATH={path_env}"

[Install]
WantedBy=default.target
"""

#: Sized to the reference incident host, not derived from it — see the
#: `MemoryHigh=` / `MemoryMax=` comment in ``SYSTEMD_UNIT`` above. These are
#: the values `resolve_memory_limits` falls back to when `.brr/config` sets
#: neither `daemon.memory_high` nor `daemon.memory_max`.
DEFAULT_MEMORY_HIGH = "8G"
DEFAULT_MEMORY_MAX = "12G"


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


def resolve_workdir() -> Path:
    """The repository root the service should run the daemon from.

    ``daemon up --foreground`` resolves its project from the current
    directory; the systemd user manager starts services from ``$HOME``, so a
    unit with no ``WorkingDirectory=`` installs a daemon that crash-loops on
    "Not a Git repository" — however correct its binary and PATH.  Freeze the
    repo the install ran from, the same install-time-snapshot contract as the
    binary and PATH pins; re-running ``brnrd daemon install`` refreshes it.
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


def resolve_memory_limits(cfg: dict[str, Any] | None) -> tuple[str, str]:
    """Read the runner memory ceiling from *cfg*, falling back to defaults.

    ``daemon.memory_high`` / ``daemon.memory_max`` are the operator's knob
    (see the ``MemoryHigh=`` / ``MemoryMax=`` comment in ``SYSTEMD_UNIT``);
    an unset or blank key keeps the incident-sized default, and any
    systemd-accepted memory value is passed through verbatim — including
    ``infinity``, which lifts that half of the fence entirely rather than
    requiring the operator to hand-edit the generated unit.
    """
    cfg = cfg or {}

    def _value(key: str, default: str) -> str:
        raw = cfg.get(key)
        if raw is None:
            return default
        text = str(raw).strip()
        return text or default

    return (
        _value("daemon.memory_high", DEFAULT_MEMORY_HIGH),
        _value("daemon.memory_max", DEFAULT_MEMORY_MAX),
    )


def _systemd_escape(value: str) -> str:
    """Escape a value for a quoted systemd ``Environment=`` assignment.

    ``%`` is a unit-file specifier and doubles; backslash and double quote
    follow systemd's quoted-string rules.
    """
    return (
        value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    )


def _percent_escape(value: str) -> str:
    """Double a literal ``%`` for an *unquoted* unit-file value.

    ``%`` starts a specifier expansion everywhere in a unit file, quoted or
    not, so ``MemoryHigh=``/``MemoryMax=`` need the same doubling
    ``_systemd_escape`` does for quoted ``Environment=`` values — but not
    its backslash/quote handling, which would corrupt an unquoted
    assignment instead of protecting it.
    """
    return value.replace("%", "%%")


def render_systemd_unit(
    brr_path: str | Path | None = None,
    *,
    path_env: str | None = None,
    workdir: str | Path | None = None,
    cfg: dict[str, Any] | None = None,
) -> str:
    """Render the unit with the resolved entrypoint, the installing shell's
    PATH, and the installing repo's root frozen in.

    The daemon dispatches runner Shells (``claude``, ``codex``, …) by PATH
    lookup, and its environment snapshot is what every run inherits — under
    the user manager's thin default PATH those CLIs vanish even when the
    daemon itself starts.  Freezing the install-time PATH hands the service
    exactly the environment the install was verified in; re-running
    ``brnrd daemon install`` refreshes it.

    *cfg* is the operator's ``.brr/config`` view (``config.load_config``'s
    return shape) and supplies ``daemon.memory_high`` / ``daemon.memory_max``
    (see ``resolve_memory_limits``). When omitted, it's loaded from
    *workdir* — the same repo the memory-ceiling knob lives beside — so a
    caller that already resolved ``workdir`` explicitly (tests; a future
    multi-repo installer) doesn't pay a second, possibly-different lookup.
    A *workdir* that isn't a usable git tree (a test double, a repo torn
    down mid-call) falls back to the memory-ceiling defaults rather than
    raising — this function's job is rendering a unit, not validating a
    checkout.
    """
    exec_start = str(brr_path) if brr_path else resolve_brr_bin()
    path_value = path_env if path_env is not None else os.environ.get("PATH", "")
    workdir_value = str(workdir) if workdir else str(resolve_workdir())
    if cfg is None:
        from .. import config as _config

        try:
            cfg = _config.load_config(Path(workdir_value))
        except Exception:  # noqa: BLE001 - unusable tree ⇒ ceiling defaults
            cfg = {}
    memory_high, memory_max = resolve_memory_limits(cfg)
    return SYSTEMD_UNIT.format(
        exec_start=_systemd_escape(exec_start),
        path_env=_systemd_escape(path_value),
        workdir=_systemd_escape(workdir_value),
        memory_high=_percent_escape(memory_high),
        memory_max=_percent_escape(memory_max),
    )


def service_installed() -> bool:
    return unit_path().exists()


def write_unit_file() -> Path:
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_systemd_unit(), encoding="utf-8")
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
    print(f"[brnrd] wrote {service_path}")

    maybe_enable_linger(prompt=prompt_linger, assume_yes=assume_yes_linger)

    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", SERVICE_UNIT])
    if not no_start:
        _run(["systemctl", "--user", "start", SERVICE_UNIT])
        verify_started()

    brnrd = brnrd_cmd()
    print(
        f"[brnrd] next: `{brnrd} daemon status`, `{brnrd} daemon logs`, "
        f"`{brnrd} daemon uninstall`"
    )


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


def verify_started(
    *,
    delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Confirm the just-started service is still alive a beat later.

    ``systemctl start`` on a ``Type=simple`` unit returns 0 the moment the
    process forks — a daemon that crashes 200ms in still reports a clean
    start, and the failure is only visible in the journal.  One short sleep
    and an ``is-active`` probe turns that silent crash-loop into an
    immediate, pointed message.
    """
    sleep(delay)
    result = _capture(
        ["systemctl", "--user", "is-active", SERVICE_UNIT],
    )
    state = (result.stdout or "").strip()
    if result.returncode == 0 and state == "active":
        return True
    print(
        f"[brnrd] warning: the service started but is not running "
        f"(state: {state or 'unknown'}) — check `brnrd daemon logs`"
    )
    return False


def start_service() -> int:
    result = _run(["systemctl", "--user", "start", SERVICE_UNIT], check=False)
    if result.returncode == 0 and not verify_started():
        return 1
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
