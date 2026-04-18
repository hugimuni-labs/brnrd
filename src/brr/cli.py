"""brr CLI — thin dispatch layer over the library modules."""

from __future__ import annotations

import argparse
import shutil
import sys
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
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("run", help="run a task through the runner")
    p.add_argument("instruction", help="what to do")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="show project state")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("auth", help="authenticate a gate")
    p.add_argument("gate", help="gate name (telegram, slack, git)")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("connect", help="bind repo to a gate")
    p.add_argument("gate", help="gate name (telegram, slack, git)")
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("up", help="start the daemon")
    p.add_argument("--debug", action="store_true", default=None,
                    help="keep worktrees and write traces for troubleshooting")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("down", help="stop the daemon")
    p.set_defaults(func=cmd_down)

    p = sub.add_parser("inspect", help="show details for a task or event")
    p.add_argument("task_id", help="task ID (or partial match)")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("docs", help="show bundled brr documentation")
    p.add_argument("topic", nargs="?", default=None,
                    help="doc topic (omit to list available topics)")
    p.set_defaults(func=cmd_docs)

    p = sub.add_parser("eject", help="copy bundled prompts for customization")
    p.set_defaults(func=cmd_eject)

    args = parser.parse_args(argv)
    return args.func(args)


def _repo_root() -> Path:
    from . import gitops
    return gitops.ensure_git_repo()


def _brr_dir() -> Path:
    return _repo_root() / ".brr"


def cmd_init(args):
    from . import adopt
    adopt.init_repo(args.url)


def cmd_run(args):
    from . import daemon as daemon_mod
    brr = _brr_dir()
    pid = daemon_mod.read_pid(brr)
    if pid:
        print(f"[brr] warning: daemon running (pid {pid}) — concurrent writes possible")

    from . import runner
    runner.run_task(args.instruction)


def cmd_status(args):
    from . import status as status_mod
    sys.stdout.write(status_mod.get_status() + "\n")


def cmd_auth(args):
    gate_mod = _load_gate(args.gate)
    gate_mod.auth(_brr_dir())


def cmd_connect(args):
    gate_mod = _load_gate(args.gate)
    gate_mod.connect(_brr_dir())


def cmd_up(args):
    from . import daemon as daemon_mod
    root = _repo_root()
    daemon_mod.start(root, debug=args.debug)


def cmd_down(args):
    from . import daemon as daemon_mod
    brr = _brr_dir()
    if daemon_mod.stop(brr):
        print("[brr] daemon stopped")
    else:
        print("[brr] daemon not running")


def cmd_inspect(args):
    from . import status as status_mod
    sys.stdout.write(status_mod.inspect_task(args.task_id, _repo_root()) + "\n")


def cmd_docs(args):
    from . import docs as docs_mod
    from . import gitops
    try:
        repo_root = gitops.ensure_git_repo()
    except (RuntimeError, SystemExit):
        repo_root = None

    if not args.topic:
        sys.stdout.write(docs_mod.format_listing(repo_root) + "\n")
        return

    content = docs_mod.read_topic(args.topic, repo_root)
    if content is None:
        topics = docs_mod.list_topics(repo_root)
        available = ", ".join(topics) if topics else "(none)"
        raise SystemExit(
            f"[brr] unknown doc topic: {args.topic} (available: {available})"
        )
    sys.stdout.write(content)
    if not content.endswith("\n"):
        sys.stdout.write("\n")


def cmd_eject(args):
    from . import runner as runner_mod
    dest = _brr_dir() / "prompts"
    dest.mkdir(parents=True, exist_ok=True)
    src = runner_mod._PROMPTS_DIR
    count = 0
    for f in src.iterdir():
        if f.suffix == ".md":
            target = dest / f.name
            if target.exists():
                print(f"[brr] skip (exists): {target.relative_to(_repo_root())}")
            else:
                shutil.copy2(f, target)
                print(f"[brr] copied: {target.relative_to(_repo_root())}")
                count += 1
    print(f"[brr] ejected {count} prompt(s) to .brr/prompts/")


def _load_gate(name: str):
    gate_map = {"telegram": "telegram", "slack": "slack", "git": "git_gate"}
    mod_name = gate_map.get(name)
    if not mod_name:
        raise SystemExit(f"[brr] unknown gate: {name} (available: {', '.join(gate_map)})")
    from .gates import import_gate
    return import_gate(mod_name)
