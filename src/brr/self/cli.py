"""The self CLI, also callable by Claude Code's JSON-on-stdin hooks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .home import init_home, resolve_home, require_home, write_text, wake_budget
from .memory import checkpoint, encode, wake
from .consolidation import consolidate, curate, proposals


# Claude converts hook context above 10,000 characters into a file preview.
# Leave headroom for the adapter's receipt; reuse the selector at smaller
# budgets instead of silently cutting its rendered text a second time.
CLAUDE_CONTEXT_UNITS = 9000


def _hook_wake(home: Path, situation: str, requested_budget: int) -> str:
    for budget in dict.fromkeys((requested_budget, min(requested_budget, 8192),
                                min(requested_budget, 4096), 0)):
        text = wake(home, situation, budget_bytes=budget)
        # Match JavaScript string length, including astral surrogate pairs.
        if len(text.encode("utf-16-le")) // 2 <= CLAUDE_CONTEXT_UNITS:
            if budget != requested_budget:
                text = (f"> Claude hook context cap: memory budget reduced from "
                        f"{requested_budget} to {budget} bytes; omissions named below.\n\n" + text)
                write_text(home / "wake.md", text)
            return text
    # Identity and accounting are not disposable memory. If they alone exceed
    # the body ceiling, preserve the full file and make the need to pull it
    # explicit; a prefix preview must never claim it delivered a whole wake.
    return (f"Fresh self wake is at {home / 'wake.md'}. Its identity and omission "
            "receipts exceed Claude's direct hook context cap. Read that file "
            "before acting on this session's task; it contains the full identity "
            "and names what is outside the memory slice. This pointer is not "
            "the wake's full content.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="self", description="Give an agent a portable Markdown home; no daemon required.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "wake", "encode", "checkpoint", "mcp", "consolidate", "curate", "proposals"):
        sub = commands.add_parser(command)
        sub.add_argument("--home", type=Path, help="Home directory (project setting, BRNRD_SELF_HOME or ~/.local/share/brnrd/self)")
        if command == "init":
            sub.add_argument("--project", type=Path, default=Path.cwd(), help="Claude Code project to wire (default: cwd)")
        if command in ("wake", "encode", "checkpoint"):
            sub.add_argument("--hook", action="store_true", help="Read Claude Code hook JSON from stdin")
        if command == "wake":
            sub.add_argument("--situation", default="")
            sub.add_argument("--budget", type=int, default=None, metavar="BYTES")
        if command == "consolidate":
            sub.add_argument("--since", default="last", help="ISO timestamp with timezone, or last accepted curation")
        if command == "curate":
            sub.add_argument("proposal", help="Proposal stamp or absolute proposal directory")
            decision = sub.add_mutually_exclusive_group(required=True)
            decision.add_argument("--accept", nargs="*", metavar="FILE", help="Accept selected files (default: all proposed)")
            decision.add_argument("--reject", action="store_true")
            sub.add_argument("--why", default="")
            sub.add_argument("--identity", action="store_true", help="Explicitly authorize identity.md acceptance")
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
            budget = args.budget if args.budget is not None else wake_budget(home)
            situation = args.situation or str(payload.get("prompt") or payload.get("situation") or "")
            text = (_hook_wake(home, situation, budget) if args.hook else
                    wake(home, situation, budget_bytes=budget))
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
        elif args.command == "consolidate":
            print(consolidate(home, args.since))
        elif args.command == "proposals":
            print(json.dumps(proposals(home), ensure_ascii=False))
        elif args.command == "curate":
            print(json.dumps(curate(home, args.proposal, accept=args.accept is not None,
                                    files=args.accept, why=args.why, identity=args.identity), ensure_ascii=False))
        elif args.command == "mcp":
            from .mcp import serve
            serve(home)
        return 0
    except (OSError, ValueError) as exc:
        print(f"self: {exc}", file=sys.stderr)
        return 1
