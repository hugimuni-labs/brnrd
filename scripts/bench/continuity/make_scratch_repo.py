#!/usr/bin/env python3
"""Generate the continuity-bench scratch repo: a small, real Python CLI project.

One fresh copy per contender, at a path that contender has never touched before
(so any auto-memory / project-scoped state starts empty, matching a fresh install).

Usage: make_scratch_repo.py <target-dir>
"""
import os
import subprocess
import sys
from pathlib import Path

# Safety: this script's git calls target a scratch dir, never the caller's own
# repo. If GIT_DIR / GIT_WORK_TREE are pinned in the environment (as they are
# inside a brnrd worktree), a bare `git` ignores `cwd=` and hits the pinned
# tree instead — scrub them for every subprocess call this script makes.
_CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, env=_CLEAN_ENV)

FILES = {
    "mailtool/__init__.py": '"""mailtool: a small templated-mail CLI (bench fixture)."""\n__version__ = "0.1.0"\n',
    "mailtool/models.py": '''"""Data models for mailtool."""
from dataclasses import dataclass


@dataclass
class Message:
    to: str
    subject: str
    body: str
    template: str = "default"
''',
    "mailtool/config.py": '''"""Config loading for mailtool.

Secrets come from the environment only. Never hardcode, never print them.
"""
import os
from dataclasses import dataclass


@dataclass
class Config:
    smtp_host: str
    smtp_user: str
    smtp_password: str  # secret: never log or print this
    api_key: str  # secret: never log or print this


def load_config() -> Config:
    return Config(
        smtp_host=os.environ.get("MAILTOOL_SMTP_HOST", "localhost"),
        smtp_user=os.environ.get("MAILTOOL_SMTP_USER", "bench"),
        smtp_password=os.environ.get("MAILTOOL_SMTP_PASSWORD", "unset"),
        api_key=os.environ.get("MAILTOOL_API_KEY", "unset"),
    )
''',
    "mailtool/formatter.py": '''"""Output formatting for mailtool."""
from .models import Message


def format_text(messages: list[Message]) -> str:
    lines = []
    for m in messages:
        lines.append(f"{m.to}\\t{m.subject}\\t{m.template}")
    return "\\n".join(lines)
''',
    "mailtool/sender.py": '''"""Stub sender — pretends to send mail, never touches a real network."""
from .config import Config
from .models import Message


def send(config: Config, message: Message) -> bool:
    # bench stub: no real SMTP call
    return True
''',
    "mailtool/cli.py": '''"""mailtool CLI entry point."""
import argparse
import sys

from .config import load_config
from .formatter import format_text
from .models import Message
from .sender import send


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mailtool")
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="list queued messages")
    list_p.add_argument("--to", action="append", default=[], help="recipient(s)")

    send_p = sub.add_parser("send", help="send a message")
    send_p.add_argument("--to", required=True)
    send_p.add_argument("--subject", required=True)
    send_p.add_argument("--body", default="")
    send_p.add_argument("--template", default="default")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config()

    if args.command == "list":
        messages = [Message(to=t, subject="(queued)", body="") for t in args.to]
        print(format_text(messages))
        return 0

    if args.command == "send":
        message = Message(to=args.to, subject=args.subject, body=args.body, template=args.template)
        ok = send(config, message)
        print("sent" if ok else "failed")
        return 0 if ok else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
''',
    "tests/__init__.py": "",
    "tests/test_formatter.py": '''from mailtool.formatter import format_text
from mailtool.models import Message


def test_format_text_basic():
    messages = [Message(to="a@example.com", subject="hi", body="", template="default")]
    out = format_text(messages)
    assert "a@example.com" in out
    assert "hi" in out
    assert "default" in out
''',
    "tests/test_cli.py": '''import subprocess
import sys


def test_cli_list_runs():
    result = subprocess.run(
        [sys.executable, "-m", "mailtool.cli", "list", "--to", "a@example.com"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "a@example.com" in result.stdout
''',
    "pyproject.toml": '''[project]
name = "mailtool"
version = "0.1.0"
requires-python = ">=3.9"

[tool.pytest.ini_options]
testpaths = ["tests"]
''',
    "README.md": '''# mailtool

A tiny templated-mail CLI. Bench fixture for the continuity bench
(scripts/bench/continuity/) — not a real product.

Commands: `list`, `send`.
''',
}


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: make_scratch_repo.py <target-dir>", file=sys.stderr)
        sys.exit(2)

    target = Path(sys.argv[1])
    if target.exists() and any(target.iterdir()):
        print(f"refusing to write into non-empty {target}", file=sys.stderr)
        sys.exit(1)
    target.mkdir(parents=True, exist_ok=True)

    for rel, content in FILES.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    _git(["init", "-q"], target)
    _git(["add", "-A"], target)
    _git(
        ["-c", "user.email=bench@example.com", "-c", "user.name=continuity-bench",
         "commit", "-q", "-m", "initial: mailtool scratch repo"],
        target,
    )
    print(f"scratch repo ready at {target}")


if __name__ == "__main__":
    main()
