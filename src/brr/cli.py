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

    p = sub.add_parser("review", help="work with diffense review packs")
    p.add_argument("pack", help="path to a review pack JSON file")
    p.add_argument("--check", action="store_true",
                   help="validate the pack's schema, card graph, and locators")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.set_defaults(func=cmd_review)

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

    daemon_p = sub.add_parser("daemon", help="daemon lifecycle")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_command", required=True)

    p = daemon_sub.add_parser("up", help="start the daemon")
    p.add_argument("--foreground", action="store_true",
                   help="run the foreground daemon instead of the installed service")
    p.add_argument("--dev-reload", action="store_true", default=None,
                   help="developer: re-exec daemon when brr package files change")
    p.set_defaults(func=cmd_daemon_up)

    p = daemon_sub.add_parser("down", help="stop the daemon")
    p.set_defaults(func=cmd_daemon_down)

    p = daemon_sub.add_parser("status", help="show daemon status")
    p.set_defaults(func=cmd_daemon_status)

    p = daemon_sub.add_parser("install", help="install the native user service")
    p.add_argument("--no-start", action="store_true",
                   help="write the service file without starting it now")
    linger = p.add_mutually_exclusive_group()
    linger.add_argument("--yes-linger", action="store_true",
                        help="linux: enable systemd linger without prompting")
    linger.add_argument("--no-linger", action="store_true",
                        help="linux: skip the linger prompt")
    p.set_defaults(func=cmd_daemon_install)

    p = daemon_sub.add_parser("uninstall", help="remove the native user service")
    disable_linger = p.add_mutually_exclusive_group()
    disable_linger.add_argument("--yes-disable-linger", action="store_true",
                                help="linux: disable linger if brr enabled it earlier")
    disable_linger.add_argument("--no-disable-linger", action="store_true",
                                help="linux: leave linger enabled without prompting")
    p.set_defaults(func=cmd_daemon_uninstall)

    p = daemon_sub.add_parser("logs", help="tail daemon service logs")
    p.add_argument("-n", "--lines", type=int, default=80,
                   help="number of existing log lines to show first")
    p.add_argument("--no-follow", action="store_true",
                   help="print existing log lines and exit")
    p.set_defaults(func=cmd_daemon_logs)

    brnrd_p = sub.add_parser("brnrd", help="brnrd managed backend")
    brnrd_sub = brnrd_p.add_subparsers(dest="brnrd_command", required=True)

    p = brnrd_sub.add_parser(
        "connect", help="link this daemon to a brnrd project (device-flow)")
    p.add_argument("--url", default=None,
                   help="brnrd base URL (default: $BRNRD_URL or https://brnrd.dev)")
    p.add_argument("--daemon-name", default=None,
                   help="name to register this daemon under (default: hostname)")
    p.set_defaults(func=cmd_brnrd_connect)

    args = parser.parse_args(argv)
    return args.func(args)


def _repo_root() -> Path:
    from . import gitops
    return gitops.ensure_git_repo()


def _brr_dir() -> Path:
    from . import gitops

    return gitops.shared_brr_dir(_repo_root())


def _maybe_brr_dir() -> Path | None:
    try:
        return _brr_dir()
    except (RuntimeError, SystemExit):
        return None


def _maybe_repo_root() -> Path | None:
    try:
        return _repo_root()
    except (RuntimeError, SystemExit):
        return None


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


def cmd_review(args):
    import json as _json

    from .diffense import pack as pack_mod

    path = Path(args.pack)
    try:
        loaded = pack_mod.load_pack(path)
    except pack_mod.PackError as e:
        if args.json:
            print(_json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"[brr review] {e}")
        return 2

    if not args.check:
        print("[brr review] only `--check` is implemented today; pass --check "
              "(the local render/serve surface is a follow-up)")
        return 0

    repo_root = _maybe_repo_root()
    issues = pack_mod.check_pack(loaded, repo_root=repo_root)
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    if args.json:
        print(_json.dumps(
            {
                "ok": not errors,
                "errors": len(errors),
                "warnings": len(warnings),
                "issues": [i.__dict__ for i in issues],
            },
            indent=2,
        ))
    else:
        for issue in issues:
            print(f"  {issue.format()}")
        n_cards = len(loaded.get("cards") or [])
        scope = "against repo" if repo_root else "structure-only (no repo)"
        print(f"[brr review] {path.name}: {n_cards} cards, "
              f"{len(errors)} error(s), {len(warnings)} warning(s) — {scope}")
    return 1 if errors else 0


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
    if not args.foreground:
        from . import daemon_install
        code = daemon_install.start_service()
        if code is not None:
            return code
    return cmd_up(args)


def cmd_daemon_down(args):
    from . import daemon_install
    code = daemon_install.stop_service()
    if code is not None:
        return code
    return cmd_down(args)


def cmd_daemon_status(args):
    from . import daemon_install
    return daemon_install.status(direct_brr_dir=_maybe_brr_dir())


def cmd_daemon_install(args):
    from . import daemon_install
    return daemon_install.install(
        no_start=args.no_start,
        prompt_linger=not args.no_linger,
        assume_yes_linger=args.yes_linger,
    )


def cmd_daemon_uninstall(args):
    from . import daemon_install
    return daemon_install.uninstall(
        prompt_linger=not args.no_disable_linger,
        assume_yes_disable_linger=args.yes_disable_linger,
    )


def cmd_daemon_logs(args):
    from . import daemon_install
    return daemon_install.logs(follow=not args.no_follow, lines=args.lines)


def cmd_brnrd_connect(args):
    import os
    import socket

    from .gates import cloud

    brr_dir = _brr_dir()
    url = args.url or os.environ.get("BRNRD_URL", "https://brnrd.dev")
    daemon_name = args.daemon_name or socket.gethostname()
    cloud.connect(brr_dir, brnrd_url=url, daemon_name=daemon_name)
    print("[brr] Start the daemon with `brr up` to begin draining the brnrd inbox.")


def _load_gate(name: str):
    gate_map = {
        "telegram": "telegram",
        "slack": "slack",
        "github": "github",
        "cloud": "cloud",
    }
    mod_name = gate_map.get(name)
    if not mod_name:
        raise SystemExit(f"[brr] unknown gate: {name} (available: {', '.join(gate_map)})")
    from .gates import import_gate
    return import_gate(mod_name)
