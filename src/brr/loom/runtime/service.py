"""``brnrd loom install|start|stop|status|uninstall|logs``.

The daemon's service with another label and another command. The home's
absolute path is frozen into the unit at install. ``body`` in
``loom/config.toml`` is the only place the login service learns which
shell and core to run: the unit's argv passes no ``--adapter``.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import site
import subprocess
import sys
import sysconfig
import time
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon_install import linux, macos

from . import entry
from .config import config_path, load_config, seed_config
from .home import found_homes, is_home, resolve_home
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
    """The unit's arguments after the shim (entry.py), which it runs instead of this Python."""
    return ["runtime", "loom", "--home", str(home)]


def _path_env() -> str:
    """The installing shell's PATH, the shim's directory first and this venv's ``bin`` read
    through ``current``: a unit that names the venv would outlive it."""
    venv = {os.path.abspath(entry.prefix() / "bin"), os.path.dirname(_interpreter())}
    kept = [str(entry.current() / "bin") if os.path.abspath(part) in venv else part
            for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    return os.pathsep.join(dict.fromkeys([str(entry.shim().parent), *kept]))


def _refuse_prefix() -> None:
    """Refuse an interpreter whose prefix has no ``bin/python`` for the shim to run,
    before anything is stopped or written."""
    target = entry.prefix()
    if not os.access(target / "bin" / "python", os.X_OK):
        raise SystemExit(f"loom install: {target} has no bin/python for {entry.shim()} to run; "
                         "install brnrd into a virtualenv (uv tool, pipx) and run it from there")


def _point_entry() -> str:
    """Write the shim and point ``current`` at this interpreter's prefix: installing is choosing this brnrd."""
    target = entry.prefix()
    entry.write_shim()
    was = entry.point(target)
    return f"{entry.current()} -> {entry.prefix()}" + (f" (was {was})" if was else "")


def _brr_source() -> Path | None:
    """The checkout ``brr`` is imported from, or None when it is installed.

    uv tool, pipx and pip all put brr under one of this interpreter's
    site-packages. Anywhere else (an editable install, ``PYTHONPATH=src``)
    is a source tree the service would go on running after the shell that
    installed it has moved on.
    """
    import brr

    package = Path(brr.__file__).resolve().parent
    roots = {sysconfig.get_paths()[k] for k in ("purelib", "platlib")}
    roots.update(site.getsitepackages() if hasattr(site, "getsitepackages") else [])
    roots.add(site.getusersitepackages())
    if any(package.is_relative_to(Path(root).resolve()) for root in roots if root):
        return None
    return package


def _refuse_source(dev: bool) -> None:
    source = _brr_source()
    if source is None or dev:
        return
    raise SystemExit(
        f"loom install: {_interpreter()} imports brr from {source}, a source checkout, "
        "and the service would keep running it. Run `deactivate; hash -r` and "
        "`brnrd loom install` again for the installed brnrd, or pass --dev to mean it."
    )


def _installed_home(agents_home: Path | None, system: str | None) -> Path | None:
    try:
        system = _platform(system)
    except SystemExit:
        return None
    if system == "Linux":
        return _linux_home(UNIT)
    return macos.installed_workdir(home=_agents(agents_home), label=LABEL)


def _service_home(flag, *, agents_home=None, system=None) -> tuple[Path, str]:
    """``--home``, BRNRD_HOME, the installed service's home, the one home found, or ~/brnrd."""
    if flag is not None or os.environ.get("BRNRD_HOME"):
        return resolve_home(flag)
    installed = _installed_home(agents_home, system)
    if installed is not None and is_home(installed):
        return installed.resolve(), "the installed service's"
    found = found_homes()
    default = (Path.home() / "brnrd").resolve()
    if len(found) == 1:
        return found[0], "default" if found[0] == default else "the only one found"
    if found:
        raise SystemExit(
            "several loom homes; pass --home with one of:\n"
            + "\n".join(f"  {path}" for path in found)
        )
    return default, "new"


def _body_choice(home: Path) -> str | None:
    """``body`` from the home's config: None when unset, refused when not shell/core."""
    choice = load_config(home).body
    if not isinstance(choice, str) or not choice.strip():
        return None
    shell, slash, core = choice.partition("/")
    if not slash or not shell.strip() or not core.strip():
        raise SystemExit(
            f"loom install: body must be shell/core, got {choice!r} in {config_path(home)}"
        )
    return choice.strip()


def _codex_model() -> str | None:
    """The person's own codex default: top-level ``model`` in codex's config."""
    path = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "config.toml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            return None
        match = re.fullmatch(r'model\s*=\s*"([^"]+)"\s*(#.*)?', line)
        if match:
            return match.group(1)
    return None


def _defaults() -> list[tuple[str, str | None]]:
    """The bodies install may pick, in order."""
    return [("claude", "sonnet"), ("codex", _codex_model())]


def _fuel(home: Path, shell: str) -> str | None:
    """``session 91% week 76%`` when the shell's CLI answers with fuel above the floor."""
    if shutil.which(shell) is None:
        return None
    from . import usage
    from .adapters import resolve
    from .home import Home

    reading = home / "loom" / "usage" / f"{shell}.json"
    try:
        usage._refresh(reading.resolve(), resolve(Home(home), shell) / "usage")
        windows = json.loads(reading.read_text())["windows"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    floor = load_config(home).hold_floor_pct
    if not windows or any(w["left_pct"] <= floor for w in windows):
        return None
    return " ".join(f"{w['name']} {w['left_pct']:g}%" for w in windows)


def _write_body(home: Path, choice: str) -> None:
    """Set ``body`` in the config, keeping every other line as written."""
    seed_config(home)
    path = config_path(home)
    lines = path.read_text(encoding="utf-8").splitlines()
    setting = f'body = "{choice}"'
    for index, line in enumerate(lines):
        if re.match(r"\s*body\s*=", line):
            lines[index] = setting
            break
    else:
        lines.append(setting)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _require_body(home: Path) -> str:
    """Refuse a login service whose home has no ``shell/core`` body."""
    choice = _body_choice(home)
    if choice is None:
        raise SystemExit(
            f"loom install: body is unset in {config_path(home)}; set body to shell/core"
        )
    return choice


def _settle_body(home: Path, *, write: bool = True) -> tuple[str, str]:
    """The body install's service will run, and where it came from.

    Unset: the first default body whose CLI answers with fuel, written to
    the config so ``loom config`` and the service agree on it.
    """
    choice = _body_choice(home)
    if choice is not None:
        return choice, "from config"
    tried = []
    for shell, core in _defaults():
        fuel = _fuel(home, shell) if core else None
        if fuel is None:
            tried.append(shell if core else f"{shell} (no default model)")
            continue
        choice = f"{shell}/{core}"
        if write:
            _write_body(home, choice)
        where = f"written to {config_path(home)}" if write else "not written: dry run"
        return choice, f"picked: {shell} has fuel, {fuel}; {where}"
    raise SystemExit(
        f"loom install: body is unset in {config_path(home)} and no CLI answered with fuel "
        f"({', '.join(tried)}); set body to shell/core with `brnrd loom config edit`"
    )


def _lock_held(home: Path) -> bool:
    path = home / "loom" / "loop.lock"
    if not path.is_file():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


def _holders(run, home: Path) -> list[int] | None:
    """Pids with the home's loop lock open, or None when lsof cannot say."""
    try:
        result = _run(run)(
            ["lsof", "-t", str(home / "loom" / "loop.lock")],
            check=False, capture_output=True, text=True,
        )
    except OSError:
        return None
    pids = [int(raw) for raw in (result.stdout or "").split() if raw.isdigit()]
    return [pid for pid in pids if pid != os.getpid()] or None


def _service_pid(run, system: str, alive) -> int | None:
    if system == "Linux":
        if _unit_text(UNIT) is None:
            return None
        state, pid = _linux_show(run, UNIT)
        return pid if state == "active" and pid is not None and alive(pid) else None
    result = _run(run)(
        ["launchctl", "print", macos._gui_service(LABEL)],
        check=False, capture_output=True, text=True,
    )
    pid = _parse_pid(_fields(result.stdout or "").get("pid"))
    return pid if result.returncode == 0 and pid is not None and alive(pid) else None


def _replace(home: Path, *, run, system: str, alive, dry_run: bool) -> str:
    """Make way for the new service; the receipt's ``replaced:`` line.

    The service's own loom is left to the install, which boots it out
    before it loads the new one. A loom running outside the service (a
    shell, an old install) holds the home's loop lock and would keep the
    new one waiting: it gets SIGTERM, which ends only its loop. Bodies
    are never signalled: they keep their sessions, and the new loom finds
    them in the ledger.
    """
    service = _service_pid(run, system, alive)
    others = []
    if _lock_held(home):
        holders = _holders(run, home)
        if holders is None:
            raise SystemExit(
                f"loom install: a loom outside the service holds {home / 'loom' / 'loop.lock'} "
                "and lsof could not name it; stop it, then run install again"
            )
        others = [pid for pid in holders if pid != service]
    parts = [f"pid {service}, the service's loom"] if service else []
    parts += [f"pid {pid}, a loom outside the service" for pid in others]
    if not parts:
        return "none"
    if dry_run:
        return "would stop " + "; ".join(parts) + " · bodies keep running"
    for pid in others:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    while others and _lock_held(home):
        if time.monotonic() > deadline:
            raise SystemExit(
                f"loom install: sent SIGTERM to {', '.join(map(str, others))}; "
                f"{home / 'loom' / 'loop.lock'} is still held after 10s"
            )
        time.sleep(0.1)
    return "; ".join(parts) + " · bodies keep running"


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


def _receipt(rows: list[tuple[str, object]]) -> None:
    for key, value in rows:
        print(f"{key}: {value}")


def _install(argv: list[str], *, run, agents_home, sleep, pid_alive, poll_timeout, system) -> int:
    parser = argparse.ArgumentParser(prog="brnrd loom install")
    parser.add_argument("--home")
    parser.add_argument("--no-start", action="store_true")
    parser.add_argument("--dev", action="store_true",
                        help="install even though brr is imported from a source checkout")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the receipt install would print; write and start nothing")
    args = parser.parse_args(argv)
    from brr import __version__

    _refuse_source(args.dev)
    _refuse_prefix()
    system = _platform(system)
    home, source = _service_home(args.home, agents_home=agents_home, system=system)
    body, picked = _settle_body(home, write=not args.dry_run)
    alive = _alive(pid_alive)
    if system not in {"Linux", "Darwin"}:
        raise SystemExit("[brnrd] loom install on this platform is not implemented yet")
    unit_file = (linux.unit_path(UNIT) if system == "Linux"
                 else macos.plist_path(home=_agents(agents_home), label=LABEL))
    receipt = [
        ("interpreter", _interpreter()),
        ("brnrd", __version__ + (" (source checkout, --dev)" if _brr_source() else "")),
        ("home", f"{home} ({source})"),
        ("body", f"{body} ({picked})"),
        ("service", unit_file),
        ("entry", entry.shim()),
    ]
    if args.dry_run:
        receipt.append(("current", f"{entry.current()} -> {entry.prefix()} (dry run: not linked)"))
        replaced = "not checked (--no-start)" if args.no_start else _replace(
            home, run=run, system=system, alive=alive, dry_run=True)
        _receipt(receipt + [("replaced", replaced), ("pid", "- (dry run: nothing written or started)")])
        return 0
    replaced = "nothing (--no-start)" if args.no_start else _replace(
        home, run=run, system=system, alive=alive, dry_run=False)
    receipt.append(("replaced", replaced))
    receipt.append(("current", _point_entry()))
    poll = _poll(poll_timeout, system)
    if system == "Linux":
        code = linux.install(
            no_start=args.no_start,
            prompt_linger=False,
            unit=UNIT,
            program_args=_program(home),
            log_directory=_linux_log_dir(),
            kill_mode="process",
            executable=str(entry.shim()),
            successor="loom",
            run=run,
            sleep=_sleep(sleep),
            workdir=home,
            delay=poll,
            restart=True,
            path_env=_path_env(),
        )
        if args.no_start:
            _receipt(receipt + [("pid", "- (not started: --no-start)")])
            return 0
        if code != 0:
            _receipt(receipt + [("pid", "-")])
            return code
        state, pid = _linux_show(run, UNIT)
        if state == "active" and pid is not None and alive(pid):
            _receipt(receipt + [("pid", pid)])
            return 0
        print(_NOT_RUNNING)
        _receipt(receipt + [("pid", "-")])
        return 1

    def confirm(*, run, sleep, timeout, label, alive=alive):
        return _confirm_macos(run=run, sleep=sleep, timeout=timeout, label=label, alive=alive)

    result = macos.install(
        no_start=args.no_start,
        brr_path=entry.shim(),
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
        path_env=_path_env(),
    )
    if args.no_start:
        _receipt(receipt + [("pid", "- (not started: --no-start; it loads at next login)")])
        return 0
    if result.alive and result.pid:
        _receipt(receipt + [("pid", result.pid)])
        return 0
    print(result.error or _NOT_RUNNING)
    _receipt(receipt + [("pid", "-")])
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
    parser = argparse.ArgumentParser(prog="brnrd loom where")
    parser.add_argument("--home")
    args = parser.parse_args(argv)
    home, source = _service_home(args.home)
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
    from .config import ConfigError, _render_value, describe_config

    parser = argparse.ArgumentParser(prog="brnrd loom config")
    parser.add_argument("--home", default=None)
    parser.add_argument("action", nargs="?", choices=("edit",), help="edit and validate the config")
    args = parser.parse_args(argv)
    home, _ = _service_home(args.home)
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
        "usage: brnrd loom install [--home PATH] [--no-start] [--dry-run] [--dev] | uninstall | start | stop | status | logs | where | config [edit]"
    )
