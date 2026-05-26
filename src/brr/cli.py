"""brr CLI — thin dispatch layer over the library modules."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    from . import __version__
    parser = argparse.ArgumentParser(
        prog="brr",
        description="Playbook + knowledge base for AI agents, with remote execution",
    )
    parser.add_argument("--version", action="version", version=f"brr {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="set up a repo for brr")
    p.add_argument("url", nargs="?", default=None, help="clone URL (optional)")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="ask setup questions (runner, config) with timed defaults")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("run", help="run a task through the runner")
    p.add_argument("instruction", help="what to do")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("auth", help="authenticate a gate")
    p.add_argument("gate", help="gate name (telegram, slack, git)")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("bind", help="bind repo to a gate channel or watch")
    p.add_argument("gate", help="gate name (telegram, slack, git)")
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("setup", help="configure a gate in one step")
    p.add_argument("gate", help="gate name (telegram, slack, git)")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("up", help="start the daemon")
    p.add_argument("--dev-reload", action="store_true", default=None,
                   help="developer: re-exec daemon when brr package files change")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("down", help="stop the daemon")
    p.set_defaults(func=cmd_down)

    p = sub.add_parser("daemon", help="manage the daemon")
    daemon_sub = p.add_subparsers(dest="daemon_command", required=True)

    p_up = daemon_sub.add_parser("up", help="start the daemon")
    p_up.add_argument("--foreground", action="store_true",
                      help="run the daemon in this process")
    p_up.add_argument("--dev-reload", action="store_true", default=None,
                      help="developer: re-exec daemon when brr package files change")
    p_up.set_defaults(func=cmd_daemon_up)

    p_down = daemon_sub.add_parser("down", help="stop the daemon")
    p_down.set_defaults(func=cmd_daemon_down)

    p_install = daemon_sub.add_parser("install", help="install the user service")
    p_install.add_argument("--no-start", action="store_true",
                           help="install and enable the service without starting it")
    linger = p_install.add_mutually_exclusive_group()
    linger.add_argument("--yes-linger", action="store_true",
                        help="enable systemd linger without prompting")
    linger.add_argument("--no-linger", action="store_true",
                        help="skip the linger prompt")
    p_install.set_defaults(func=cmd_daemon_install)

    p_uninstall = daemon_sub.add_parser("uninstall", help="uninstall the user service")
    disable_linger = p_uninstall.add_mutually_exclusive_group()
    disable_linger.add_argument("--yes-disable-linger", action="store_true",
                                help="disable linger if brr enabled it earlier")
    disable_linger.add_argument("--no-disable-linger", action="store_true",
                                help="leave linger enabled without prompting")
    p_uninstall.set_defaults(func=cmd_daemon_uninstall)

    p_status = daemon_sub.add_parser("status", help="show daemon status")
    p_status.set_defaults(func=cmd_daemon_status)

    p_logs = daemon_sub.add_parser("logs", help="tail daemon logs")
    p_logs.set_defaults(func=cmd_daemon_logs)

    args = parser.parse_args(argv)
    return args.func(args)


def _repo_root() -> Path:
    from . import gitops
    return gitops.ensure_git_repo()


def _brr_dir() -> Path:
    from . import gitops

    return gitops.shared_brr_dir(_repo_root())


def cmd_init(args):
    from . import adopt
    adopt.init_repo(args.url, interactive=args.interactive)


def cmd_run(args):
    from . import daemon as daemon_mod
    brr = _brr_dir()
    pid = daemon_mod.read_pid(brr)
    if pid:
        print(f"[brr] warning: daemon running (pid {pid}) — concurrent writes possible")

    from . import runner
    runner.run_task(args.instruction)


def cmd_auth(args):
    gate_mod = _load_gate(args.gate)
    gate_mod.auth(_brr_dir())


def cmd_bind(args):
    gate_mod = _load_gate(args.gate)
    gate_mod.bind(_brr_dir())


def cmd_setup(args):
    gate_mod = _load_gate(args.gate)
    brr_dir = _brr_dir()
    setup = getattr(gate_mod, "setup", None)
    if setup is not None:
        setup(brr_dir)
        return
    gate_mod.auth(brr_dir)
    gate_mod.bind(brr_dir)


def cmd_up(args):
    from . import daemon as daemon_mod
    root = _repo_root()
    daemon_mod.start(root, dev_reload=args.dev_reload)


def cmd_down(args):
    from . import daemon as daemon_mod
    brr = _brr_dir()
    if daemon_mod.stop(brr):
        print("[brr] daemon stopped")
    else:
        print("[brr] daemon not running")


def cmd_daemon_up(args):
    if args.foreground:
        return cmd_up(args)

    from .daemon_install import linux as systemd_linux

    if systemd_linux.supported() and systemd_linux.service_installed():
        code = systemd_linux.start_service()
        if code == 0:
            print("[brr] daemon service started")
        return code

    return cmd_up(args)


def cmd_daemon_down(args):
    from .daemon_install import linux as systemd_linux

    if systemd_linux.supported() and systemd_linux.service_installed():
        code = systemd_linux.stop_service()
        if code == 0:
            print("[brr] daemon service stopped")
        return code

    return cmd_down(args)


def cmd_daemon_install(args):
    from .daemon_install import linux as systemd_linux

    return systemd_linux.install(
        no_start=args.no_start,
        prompt_linger=not args.no_linger,
        assume_yes_linger=args.yes_linger,
    )


def cmd_daemon_uninstall(args):
    from .daemon_install import linux as systemd_linux

    return systemd_linux.uninstall(
        prompt_linger=not args.no_disable_linger,
        assume_yes_disable_linger=args.yes_disable_linger,
    )


def cmd_daemon_status(args):
    from .daemon_install import linux as systemd_linux

    if systemd_linux.supported() and systemd_linux.service_installed():
        return systemd_linux.status()

    try:
        brr = _brr_dir()
    except SystemExit:
        print("[brr] daemon service not installed")
        return 1

    from . import daemon as daemon_mod
    pid = daemon_mod.read_pid(brr)
    if pid:
        print(f"[brr] daemon running directly under PID {pid}")
        return 0
    print("[brr] daemon not running")
    return 3


def cmd_daemon_logs(args):
    from .daemon_install import linux as systemd_linux

    if not systemd_linux.supported():
        raise SystemExit("[brr] daemon logs on this platform is not implemented yet")
    return systemd_linux.logs()


def _load_gate(name: str):
    gate_map = {"telegram": "telegram", "slack": "slack", "github": "github"}
    mod_name = gate_map.get(name)
    if not mod_name:
        raise SystemExit(f"[brr] unknown gate: {name} (available: {', '.join(gate_map)})")
    from .gates import import_gate
    return import_gate(mod_name)
