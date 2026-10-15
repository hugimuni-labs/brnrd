"""``brnrd loom install|start|stop|status|uninstall|logs``.

The daemon's service with another label and another command. The home's
absolute path is frozen into the unit at install. ``body`` in
``loom/config.toml`` is the only place the login service learns which
shell and core to run: the unit's argv passes no ``--adapter``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon_install import linux, macos

from .config import load_config
from .home import resolve_home
from .threads import _age


LABEL = "dev.brnrd.loom"
UNIT = "dev.brnrd.loom.service"
LOG_NAME = "brnrd-loom"
_NOT_RUNNING = "the service was not running after the poll interval"


def _platform(system: str | None) -> str:
    if system:
        return system
    if sys.platform == "darwin":
        return "Darwin"
    if sys.platform.startswith("linux"):
        return "Linux"
    raise SystemExit("[brnrd] loom install on this platform is not implemented yet")


def _interpreter() -> str:
    # Resolving a virtualenv symlink would select the base interpreter and
    # discard the environment that has brnrd installed. Freeze this Python.
    return os.path.abspath(sys.executable)


def _program(home: Path) -> list[str]:
    return ["-m", "brr.loom.runtime", "loom", "--home", str(home)]


def _require_body(home: Path) -> str:
    """Refuse a login service whose home has no ``shell/core`` body."""
    choice = load_config(home).body
    path = home / "loom" / "config.toml"
    if not isinstance(choice, str) or not choice.strip():
        raise SystemExit(
            f"loom install: body is unset in {path}; set body to shell/core"
        )
    shell, slash, core = choice.partition("/")
    if not slash or not shell.strip() or not core.strip():
        raise SystemExit(
            f"loom install: body must be shell/core, got {choice!r} in {path}"
        )
    return choice.strip()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _fields(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.strip().partition("=")
        found.setdefault(key.strip(), value.strip())
    return found


def _parse_pid(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        number = int(raw)
    except ValueError:
        return None
    return number if number > 0 else None


def _confirm_macos(*, run, sleep, timeout, label, alive) -> int | None:
    """Sleep the installer's poll interval, then trust a live pid only."""
    sleep(timeout)
    result = run(
        ["launchctl", "print", macos._gui_service(label)],
        check=False,
        capture_output=True,
        text=True,
    )
    fields = _fields(result.stdout or "")
    pid = _parse_pid(fields.get("pid"))
    if result.returncode != 0 or fields.get("state") != "running" or pid is None or not alive(pid):
        return None
    return pid


def _linux_show(run, unit: str) -> tuple[str, int | None]:
    result = _run(run)(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "query-failed", None
    fields = _fields(result.stdout or "")
    return fields.get("ActiveState") or "unknown", _parse_pid(fields.get("MainPID"))


def _unescape(value: str) -> str:
    return value.replace("\\\\", "\\").replace('\\"', '"').replace("%%", "%")


def _unit_text(unit: str) -> str | None:
    path = linux.unit_path(unit)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _linux_home(unit: str) -> Path | None:
    text = _unit_text(unit)
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("WorkingDirectory="):
            raw = _unescape(line.split("=", 1)[1].strip())
            return Path(raw) if raw else None
    return None


def _linux_log_dir() -> Path:
    return linux.xdg_state_home() / LOG_NAME


def _log_line(home: Path | None) -> str:
    if home is None:
        return "log: -"
    path = home / "loom" / "loom.log"
    if not path.is_file():
        return "log: -"
    try:
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return "log: -"
    if not lines:
        return "log: -"
    last = lines[-1]
    stamp, _, _rest = last.partition(" ")
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return f"log: {last}"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age = _age((datetime.now(timezone.utc) - when).total_seconds())
    return f"log: {last} ({age})"


def _body_line(home: Path | None) -> str:
    if home is None:
        return "body: -"
    choice = load_config(home).body
    if not isinstance(choice, str) or not choice.strip():
        return "body: unset"
    return f"body: {choice.strip()}"


def _print_status(*, state: str, pid: int | None, home: Path | None) -> None:
    print(f"state: {state}")
    print(f"pid: {pid if pid else '-'}")
    print(f"home: {home if home else '-'}")
    print(_body_line(home))
    print(_log_line(home))


def _run(run):
    return run or subprocess.run


def _sleep(sleep):
    return sleep or time.sleep


def _alive(pid_alive):
    return pid_alive or _pid_alive


def _poll(poll_timeout: float | None, system: str) -> float:
    if poll_timeout is not None:
        return poll_timeout
    return 2.0 if system == "Linux" else macos.DEFAULT_POLL_TIMEOUT


def _agents(agents_home: Path | None) -> Path:
    return agents_home if agents_home is not None else Path.home()


def _install(argv: list[str], *, run, agents_home, sleep, pid_alive, poll_timeout, system) -> int:
    parser = argparse.ArgumentParser(prog="brnrd loom install")
    parser.add_argument("--home")
    parser.add_argument("--no-start", action="store_true")
    args = parser.parse_args(argv)
    home, _ = resolve_home(args.home)
    _require_body(home)
    system = _platform(system)
    poll = _poll(poll_timeout, system)
    if system == "Linux":
        code = linux.install(
            no_start=args.no_start,
            prompt_linger=False,
            unit=UNIT,
            program_args=_program(home),
            log_directory=_linux_log_dir(),
            kill_mode="process",
            executable=_interpreter(),
            successor="loom",
            run=run,
            sleep=_sleep(sleep),
            workdir=home,
            delay=poll,
            restart=True,
        )
        if args.no_start:
            return 0
        if code != 0:
            return code
        state, pid = _linux_show(run, UNIT)
        if state == "active" and pid is not None and _alive(pid_alive)(pid):
            print(f"pid: {pid}")
            return 0
        print(_NOT_RUNNING)
        return 1
    if system != "Darwin":
        raise SystemExit("[brnrd] loom install on this platform is not implemented yet")
    alive = _alive(pid_alive)

    def confirm(*, run, sleep, timeout, label, alive=alive):
        return _confirm_macos(run=run, sleep=sleep, timeout=timeout, label=label, alive=alive)

    result = macos.install(
        no_start=args.no_start,
        brr_path=_interpreter(),
        home=_agents(agents_home),
        workdir=home,
        run=_run(run),
        poll_timeout=poll,
        sleep=_sleep(sleep),
        label=LABEL,
        program_args=_program(home),
        log_directory=_agents(agents_home) / "Library" / "Logs" / LOG_NAME,
        abandon_process_group=True,
        confirm=confirm,
        retry_command="`brnrd loom install`",
    )
    print(f"[brnrd] wrote LaunchAgent: {result.plist_path}")
    if args.no_start:
        print("[brnrd] launchd service written; it will load at next login")
        return 0
    if result.alive and result.pid:
        print(f"pid: {result.pid}")
        return 0
    print(result.error or _NOT_RUNNING)
    return 1


def _start(*, run, agents_home, sleep, pid_alive, poll_timeout, system) -> int:
    system = _platform(system)
    poll = _poll(poll_timeout, system)
    if system == "Linux":
        code = linux.start_service(
            unit=UNIT, successor="loom", run=run, sleep=_sleep(sleep), delay=poll,
        )
        if code != 0:
            return code
        state, pid = _linux_show(run, UNIT)
        if state == "active" and pid is not None and _alive(pid_alive)(pid):
            print(f"pid: {pid}")
            return 0
        print(_NOT_RUNNING)
        return 1
    alive = _alive(pid_alive)

    def confirm(*, run, sleep, timeout, label, alive=alive):
        return _confirm_macos(run=run, sleep=sleep, timeout=timeout, label=label, alive=alive)

    result = macos.start_loaded_service(
        run=_run(run),
        sleep=_sleep(sleep),
        poll_timeout=poll,
        home=_agents(agents_home),
        label=LABEL,
        confirm=confirm,
    )
    if result.started and result.pid:
        print(f"pid: {result.pid}")
        return 0
    print(result.error or _NOT_RUNNING)
    return 1


def _stop(*, run, agents_home, system) -> int:
    system = _platform(system)
    if system == "Linux":
        return linux.stop_service(unit=UNIT, run=run)
    result = macos.stop_loaded_service(run=_run(run), label=LABEL)
    return result.returncode


def _status(*, run, agents_home, system, pid_alive) -> int:
    system = _platform(system)
    alive = _alive(pid_alive)
    if system == "Linux":
        if _unit_text(UNIT) is None:
            _print_status(state="not-installed", pid=None, home=None)
            return 1
        state, pid = _linux_show(run, UNIT)
        home = _linux_home(UNIT)
        if pid is not None and not alive(pid):
            pid = None
        _print_status(state=state, pid=pid, home=home)
        return 0
    path = macos.plist_path(home=_agents(agents_home), label=LABEL)
    if not path.is_file():
        _print_status(state="not-installed", pid=None, home=None)
        return 1
    result = _run(run)(
        ["launchctl", "print", macos._gui_service(LABEL)],
        check=False,
        capture_output=True,
        text=True,
    )
    fields = _fields(result.stdout or "")
    pid = _parse_pid(fields.get("pid"))
    state = fields.get("state") or "not-running"
    if result.returncode != 0:
        state = "not-running"
        pid = None
    elif pid is not None and not alive(pid):
        pid = None
    home = macos.installed_workdir(home=_agents(agents_home), label=LABEL)
    _print_status(state=state, pid=pid, home=home)
    return 0


def cmd_where(argv: list[str]) -> int:
    """Print today's home layout without creating paths or an install id."""
    from .config import config_path

    parser = argparse.ArgumentParser(prog="brnrd loom where")
    parser.add_argument("--home")
    args = parser.parse_args(argv)
    home, source = resolve_home(args.home)
    print(f"home: {home}")
    print(f"source: {source}")
    for name, path in (
        ("config", config_path(home)), ("self", home / "self"),
        ("ledger", home / "ledger"), ("rooms", home / "rooms"),
        ("logs", home / "loom" / "loom.log"),
        ("body logs", home / "rooms" / "<strand>" / "port" / "body.log"),
    ):
        print(f"{name}: {path}")
    return 0


def cmd_config(argv: list[str]) -> int:
    return _config(argv)


def _config(argv: list[str]) -> int:
    """``brnrd loom config [--home H]``: every key, its effective value, source, help."""
    from .config import ConfigError, _render_value, config_path, describe_config

    parser = argparse.ArgumentParser(prog="brnrd loom config")
    parser.add_argument("--home", default=None)
    parser.add_argument("action", nargs="?", choices=("edit",), help="edit and validate the config")
    args = parser.parse_args(argv)
    home, _ = resolve_home(args.home)
    if args.action == "edit":
        from .config_edit import edit_config
        return edit_config(home)
    try:
        rows = describe_config(home)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"file: {config_path(home)}" + ("" if config_path(home).is_file() else " (absent: all defaults)"))
    width = max(len(name) for name, *_ in rows)
    for name, value, source, help_ in rows:
        shown = "-" if value is None else (_render_value(value) if isinstance(value, (str, tuple)) else f"{value:g}")
        print(f"{name:<{width}} = {shown} · {source} · {help_}")
    return 0


def _logs(argv: list[str], *, run, agents_home, system) -> int:
    parser = argparse.ArgumentParser(prog="brnrd loom logs")
    parser.add_argument("-n", "--lines", type=int, default=80)
    parser.add_argument("--no-follow", action="store_true")
    args = parser.parse_args(argv)
    follow = not args.no_follow
    system = _platform(system)
    if system == "Linux":
        directory = _linux_log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        paths = [directory / "out.log", directory / "err.log"]
        for path in paths:
            path.touch(exist_ok=True)
        command = ["tail", "-n", str(args.lines)]
        if follow:
            command.append("-F")
        command.extend(str(path) for path in paths)
        _run(run)(command, check=False)
        return 0
    macos.logs(
        follow=follow,
        lines=args.lines,
        home=_agents(agents_home),
        run=_run(run),
        log_directory=_agents(agents_home) / "Library" / "Logs" / LOG_NAME,
    )
    return 0


def _uninstall(*, run, agents_home, system) -> int:
    system = _platform(system)
    if system == "Linux":
        linux.uninstall(
            prompt_linger=False,
            unit=UNIT,
            successor="loom",
            run=run,
        )
        return 0
    macos.uninstall(home=_agents(agents_home), run=_run(run), label=LABEL)
    return 0


def main(
    argv: list[str] | None = None,
    *,
    run=None,
    agents_home: Path | None = None,
    sleep=None,
    pid_alive=None,
    poll_timeout: float | None = None,
    system: str | None = None,
) -> int:
    """The ``brnrd loom <verb>`` argv, with ``loom`` already stripped."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "loom":
        argv = argv[1:]
    verb = argv[0] if argv else ""
    rest = argv[1:]
    shared = dict(
        run=run,
        agents_home=agents_home,
        sleep=sleep,
        pid_alive=pid_alive,
        poll_timeout=poll_timeout,
        system=system,
    )
    if verb in {"start", "stop", "status", "uninstall"}:
        argparse.ArgumentParser(prog=f"brnrd loom {verb}").parse_args(rest)
    if verb == "config":
        return _config(rest)
    if verb == "where":
        return cmd_where(rest)
    if verb == "install":
        return _install(rest, **shared)
    if verb == "start":
        return _start(**shared)
    if verb == "stop":
        return _stop(run=run, agents_home=agents_home, system=system)
    if verb == "status":
        return _status(run=run, agents_home=agents_home, system=system, pid_alive=pid_alive)
    if verb == "logs":
        return _logs(rest, run=run, agents_home=agents_home, system=system)
    if verb == "uninstall":
        return _uninstall(run=run, agents_home=agents_home, system=system)
    raise SystemExit(
        "usage: brnrd loom install [--home PATH] | uninstall | start | stop | status | logs | where | config [edit]"
    )
