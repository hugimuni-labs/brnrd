"""The self CLI, also callable by Claude Code's JSON-on-stdin hooks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .home import init_home, resolve_home, require_home
from .memory import checkpoint, encode, wake


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="self", description="Give an agent a portable Markdown home; no daemon required.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "wake", "encode", "checkpoint", "mcp"):
        sub = commands.add_parser(command)
        sub.add_argument("--home", type=Path, help="Home directory (project setting, BRNRD_SELF_HOME or ~/.local/share/brnrd/self)")
        if command == "init":
            sub.add_argument("--project", type=Path, default=Path.cwd(), help="Claude Code project to wire (default: cwd)")
        if command in ("wake", "encode", "checkpoint"):
            sub.add_argument("--hook", action="store_true", help="Read Claude Code hook JSON from stdin")
        if command == "wake":
            sub.add_argument("--situation", default="")
            sub.add_argument("--budget", type=int, default=16384, metavar="BYTES")
    args = parser.parse_args(argv)
    try:
        home = resolve_home(args.home, getattr(args, "project", None))
        if args.command == "init":
            created = init_home(home, args.project)
            wake(home)
            print(f"Self home: {home}\nSeeded: {', '.join(created) or 'existing home preserved'}\n"
                  f"Claude Code wired in {args.project.resolve()}; restart a session to load the hooks.")
            return 0
        require_home(home)
        payload = {}
        if getattr(args, "hook", False) or args.command == "encode":
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                raise ValueError("Hook payload must be a JSON object")
        if args.command == "wake":
            situation = args.situation or str(payload.get("prompt") or payload.get("situation") or "")
            text = wake(home, situation, budget_bytes=args.budget)
            if args.hook:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
            else:
                print(text, end="")
        elif args.command == "encode":
            row = encode(home, payload)
            if not args.hook:
                print(json.dumps(row))
        elif args.command == "checkpoint":
            path = checkpoint(home, payload)
            if not args.hook:
                print(path)
        elif args.command == "mcp":
            from .mcp import serve
            serve(home)
        return 0
    except (OSError, ValueError) as exc:
        print(f"self: {exc}", file=sys.stderr)
        return 1
